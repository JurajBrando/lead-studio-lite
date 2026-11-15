"""End-to-end tests for Lead Studio Lite.

Run:  python -m unittest discover -s tests -v

They use a throwaway data folder and a local fake SMTP server, so no real
email leaves the machine.
"""
import base64
import json
import os
import socket
import socketserver
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from unittest import mock

TMP = tempfile.mkdtemp(prefix="ls_e2e_")
os.environ["LS_DATA"] = TMP
os.environ["LS_PORT"] = "8798"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine import config, db, demo, importer, mail, pipeline, scheduler, verify  # noqa: E402
from engine import server as web  # noqa: E402

SINK = None


class _SinkHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.wfile.write(b"220 sink ESMTP ready\r\n")
        data = []
        in_data = False
        while True:
            line = self.rfile.readline()
            if not line:
                break
            if in_data:
                if line.strip() == b".":
                    in_data = False
                    self.server.messages.append(b"".join(data))
                    data = []
                    self.wfile.write(b"250 2.0.0 queued\r\n")
                else:
                    data.append(line)
                continue
            cmd = line[:4].upper()
            if cmd in (b"EHLO", b"HELO"):
                self.wfile.write(b"250-sink\r\n250-SIZE 10485760\r\n250 8BITMIME\r\n")
            elif cmd == b"DATA":
                self.wfile.write(b"354 go ahead\r\n")
                in_data = True
            elif cmd == b"QUIT":
                self.wfile.write(b"221 bye\r\n")
                break
            else:
                self.wfile.write(b"250 OK\r\n")


class SMTPSink(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), _SinkHandler)
        self.messages = []


def setUpModule():
    global SINK
    db.init_db()
    SINK = SMTPSink()
    threading.Thread(target=SINK.serve_forever, daemon=True).start()


def tearDownModule():
    if SINK:
        SINK.shutdown()
    db.close()


def wipe():
    for t in ("sends", "replies", "enrollments", "steps", "sequences", "leads",
              "suppressions", "events", "mailboxes"):
        db.execute("DELETE FROM %s" % t)


def add_mailbox(daily_limit=50):
    return db.insert("mailboxes", {
        "name": "Test sender", "from_name": "Test Sender", "from_email": "sender@test.local",
        "smtp_host": "127.0.0.1", "smtp_port": SINK.server_address[1], "smtp_security": "none",
        "smtp_user": "", "smtp_pass": "", "imap_host": "127.0.0.1", "imap_port": 993,
        "imap_user": "sender@test.local", "imap_pass": "", "daily_limit": daily_limit,
        "active": 1, "created_ts": db.now(),
    })


def add_sequence(steps):
    sid = db.insert("sequences", {"name": "Test cadence", "description": "test",
                                  "active": 1, "created_ts": db.now(), "updated_ts": db.now()})
    for i, st in enumerate(steps, start=1):
        db.insert("steps", {"sequence_id": sid, "step_no": i, "delay_days": st.get("delay", 0),
                            "subject": st["subject"], "body": st["body"], "stop_on_reply": 1})
    return sid


class ImportTests(unittest.TestCase):
    def setUp(self):
        wipe()

    def test_import_csv_with_duplicates_and_invalid(self):
        csv_text = ("Email,First Name,Company,Job Title,City\n"
                    "anna@acme-test.com,Anna,Acme GmbH,Head of Sales,Vienna\n"
                    "bob@beta-test.com,Bob,Beta Ltd,CEO,Prague\n"
                    "anna@acme-test.com,Anna,Acme GmbH,Head of Sales,Vienna\n"
                    "not-an-email,X,Y,Z,W\n")
        res = importer.import_data("leads.csv", csv_text.encode())
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["imported"], 2)
        self.assertEqual(res["duplicates"], 1)
        self.assertEqual(res["invalid"], 1)
        row = db.one("SELECT * FROM leads WHERE email='anna@acme-test.com'")
        self.assertEqual(row["first_name"], "Anna")
        self.assertEqual(row["company"], "Acme GmbH")
        self.assertEqual(row["title"], "Head of Sales")
        self.assertEqual(row["domain"], "acme-test.com")

    def test_import_semicolon_and_name_column(self):
        text = "Name;E-mail;Firma\nTest Person;person@example-test.sk;Example Co\n"
        res = importer.import_data("x.csv", text.encode("utf-8"))
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["imported"], 1)
        row = db.one("SELECT * FROM leads WHERE email='person@example-test.sk'")
        self.assertEqual(row["first_name"], "Test")
        self.assertEqual(row["last_name"], "Person")

    def test_import_without_an_email_column_is_allowed(self):
        text = (b"Company,Description,Domain\n"
                b"Acme GmbH,We build thingies,https://www.acme-test.com/about\n")
        res = importer.import_data("companies.csv", text)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["imported"], 1)
        self.assertEqual(res["no_email"], 1)
        row = db.one("SELECT * FROM leads WHERE company='Acme GmbH'")
        self.assertEqual(row["email"], "")
        self.assertEqual(row["verify_status"], "none")
        self.assertEqual(row["description"], "We build thingies")
        self.assertEqual(row["domain"], "acme-test.com")

    def test_a_row_still_needs_something_to_identify_it(self):
        res = importer.import_data("x.csv", b"phone,notes\n123,hello\n")
        self.assertFalse(res["ok"])
        self.assertIn("company", res["error"].lower())

    def test_company_rows_are_not_duplicated(self):
        text = b"company,description\nAcme,We build\nAcme,We build\n"
        res = importer.import_data("x.csv", text)
        self.assertEqual(res["imported"], 1)
        self.assertEqual(res["duplicates"], 1)
        again = importer.import_data("x.csv", text)
        self.assertEqual(again["imported"], 0)
        self.assertEqual(again["existing_email"], 1)

    def test_update_existing_fills_the_missing_email(self):
        importer.import_data("companies.csv", b"company,description\nAcme,We build\n")
        res = importer.import_data("people.csv", b"company,email\nAcme,hi@acme-test.com\n",
                                   update_existing=True)
        self.assertEqual(res["updated"], 1)
        row = db.one("SELECT * FROM leads WHERE company='Acme'")
        self.assertEqual(row["email"], "hi@acme-test.com")
        self.assertEqual(row["verify_status"], "unverified")

    def test_export_round_trip(self):
        importer.import_data("leads.csv", b"email\nx@export-test.com\n")
        text, n = importer.export_csv()
        self.assertEqual(n, 1)
        self.assertIn("x@export-test.com", text)


class TemplateTests(unittest.TestCase):
    def test_merge_with_fallback(self):
        out, missing = mail.merge("Hi {{first_name|there}} at {{company}}", {"company": "Acme"})
        self.assertEqual(out, "Hi there at Acme")
        self.assertEqual(missing, [])

    def test_merge_reports_missing(self):
        out, missing = mail.merge("Hi {{first_name}}", {})
        self.assertIn("first_name", missing)

    def test_lead_fields(self):
        f = mail.lead_fields({"email": "a@b.com", "first_name": "Ann", "last_name": "Lee",
                              "company": "Acme", "industry": "SaaS"})
        self.assertEqual(f["name"], "Ann Lee")
        self.assertEqual(f["company"], "Acme")

    def test_html_contains_pixel_and_escapes(self):
        html = mail.plain_to_html("Hello <script>\n\nhttp://x.test/page",
                                  track_url="http://t.test/o/abc.gif")
        self.assertIn("<p", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("o/abc.gif", html)


class VerifyTests(unittest.TestCase):
    def test_syntax(self):
        self.assertEqual(verify.verify("")["status"], "invalid")
        self.assertEqual(verify.verify("nope")["status"], "invalid")
        self.assertEqual(verify.verify("a b@c.com")["status"], "invalid")

    def test_placeholder_and_disposable(self):
        self.assertEqual(verify.verify("test@example.com", {"mx": False})["status"], "invalid")
        self.assertEqual(verify.verify("x@mailinator.com", {"mx": False})["status"], "risky")

    def test_role_address_is_risky(self):
        res = verify.verify("info@some-company-test.com", {"mx": False})
        self.assertIn(res["status"], ("risky", "valid"))
        self.assertIn("Role", res["detail"])


class SendingTests(unittest.TestCase):
    def setUp(self):
        wipe()
        self.box = add_mailbox()
        self.seq = add_sequence([
            {"delay": 0, "subject": "Hello {{first_name|there}}",
             "body": "Hi {{first_name|there}},\n\nQuestion about {{company}}.\n\n{{sender_name}}"},
            {"delay": 3, "subject": "Following up", "body": "Bump for {{company}}."},
        ])
        self.lead = db.insert("leads", {
            "email": "prospect@target-test.com", "first_name": "Petra", "company": "Target s.r.o.",
            "status": "new", "created_ts": db.now(), "updated_ts": db.now()})
        config.save_settings({**config.load_settings(),
                              "sending": {"enabled": True, "window_start": 0, "window_end": 24,
                                          "gap_seconds": 0, "jitter_seconds": 0, "weekdays_only": False},
                              "sender_name": "Test Person", "company_name": "Example Co"})
        SINK.messages.clear()

    def test_enroll_and_send_reaches_smtp(self):
        res = pipeline.enroll(self.seq, [self.lead], self.box)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["added"], 1)

        tick = pipeline.tick(force=True)
        self.assertTrue(tick.get("ok"), tick)
        self.assertEqual(tick.get("action"), "sent", tick)

        self.assertEqual(len(SINK.messages), 1)
        raw = SINK.messages[0].decode("utf-8", "replace")
        self.assertIn("Subject: Hello Petra", raw)
        self.assertIn("Question about Target", raw)
        self.assertIn("Test Person", raw)

        send = db.one("SELECT * FROM sends WHERE lead_id=?", (self.lead,))
        self.assertEqual(send["status"], "sent")
        self.assertTrue(send["message_id"].startswith("<"))
        lead = db.one("SELECT * FROM leads WHERE id=?", (self.lead,))
        self.assertEqual(lead["status"], "contacted")

        en = db.one("SELECT * FROM enrollments WHERE lead_id=?", (self.lead,))
        self.assertEqual(en["step_no"], 1)
        self.assertEqual(en["status"], "running")
        self.assertGreater(en["next_run_ts"], db.now() + 2 * 86400)

    def test_missing_merge_field_pauses_instead_of_sending_blank(self):
        lead = db.insert("leads", {"email": "nofields@target-test.com", "status": "new"})
        seq = add_sequence([{"delay": 0, "subject": "Hi {{company}}", "body": "About {{company}}"}])
        pipeline.enroll(seq, [lead], self.box)
        res = pipeline.tick(force=True)
        self.assertFalse(res.get("ok"), res)
        self.assertIn("Missing data", res["error"])
        en = db.one("SELECT * FROM enrollments WHERE lead_id=?", (lead,))
        self.assertEqual(en["status"], "paused")
        self.assertEqual(len(SINK.messages), 0)

    def test_daily_limit_stops_sending(self):
        db.update("mailboxes", self.box, {"daily_limit": 1})
        for i in range(3):
            lid = db.insert("leads", {"email": "bulk%d@target-test.com" % i, "status": "new",
                                      "first_name": "B", "company": "C"})
            pipeline.enroll(self.seq, [lid], self.box)
        first = pipeline.tick(force=True)
        self.assertEqual(first.get("action"), "sent")
        second = pipeline.tick(force=True)
        self.assertEqual(second.get("action"), "idle")
        self.assertIn("daily limit", second["reason"])
        self.assertEqual(len(SINK.messages), 1)

    def test_suppressed_lead_never_sends(self):
        db.add_suppression("prospect@target-test.com", "test")
        res = pipeline.enroll(self.seq, [self.lead], self.box)
        self.assertEqual(res["added"], 0)
        self.assertEqual(res["skipped"], 1)
        pipeline.tick(force=True)
        self.assertEqual(len(SINK.messages), 0)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM sends"), 0)


class ReplyTests(unittest.TestCase):
    def setUp(self):
        wipe()
        self.box = add_mailbox()
        self.seq = add_sequence([{"delay": 0, "subject": "Hello", "body": "Body"},
                                 {"delay": 3, "subject": "Second", "body": "Body two"}])
        self.lead = db.insert("leads", {"email": "reply@target-test.com", "status": "new",
                                        "first_name": "R", "company": "C"})
        config.save_settings({**config.load_settings(),
                              "sending": {"enabled": True, "window_start": 0, "window_end": 24,
                                          "gap_seconds": 0, "jitter_seconds": 0, "weekdays_only": False}})
        SINK.messages.clear()
        pipeline.enroll(self.seq, [self.lead], self.box)
        pipeline.tick(force=True)
        self.send = db.one("SELECT * FROM sends WHERE lead_id=?", (self.lead,))

    def test_reply_stops_sequence_and_marks_lead(self):
        fake = [{
            "from": "Reply Person <reply@target-test.com>", "address": "reply@target-test.com",
            "subject": "Re: Hello", "date": "today",
            "message_id": "<incoming-1@target-test.com>",
            "in_reply_to": [self.send["message_id"]],
        }]
        with mock.patch.object(mail, "fetch_recent", return_value=(fake, "")):
            res = pipeline.sync_inbox()
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["new_replies"], 1)
        lead = db.one("SELECT * FROM leads WHERE id=?", (self.lead,))
        self.assertEqual(lead["status"], "replied")
        en = db.one("SELECT * FROM enrollments WHERE lead_id=?", (self.lead,))
        self.assertEqual(en["status"], "replied")

    def test_bounce_suppresses_address(self):
        fake = [{
            "from": "Mail Delivery System <MAILER-DAEMON@target-test.com>",
            "address": "mailer-daemon@target-test.com",
            "subject": "Undeliverable: Hello", "date": "today",
            "message_id": "<bounce-1@target-test.com>",
            "in_reply_to": [self.send["message_id"]],
        }]
        with mock.patch.object(mail, "fetch_recent", return_value=(fake, "")):
            res = pipeline.sync_inbox()
        self.assertEqual(res["new_bounces"], 1)
        lead = db.one("SELECT * FROM leads WHERE id=?", (self.lead,))
        self.assertEqual(lead["status"], "bounced")

    def test_an_unsubscribe_answer_is_not_a_sales_reply(self):
        fake = [{
            "from": "Reply Person <reply@target-test.com>",
            "address": "reply@target-test.com",
            "subject": "unsubscribe", "date": "today",
            "message_id": "<unsub-1@target-test.com>",
            "in_reply_to": [self.send["message_id"]],
        }]
        with mock.patch.object(mail, "fetch_recent", return_value=(fake, "")):
            res = pipeline.sync_inbox()
        self.assertEqual(res["new_unsubscribes"], 1)
        self.assertEqual(res["new_replies"], 0)
        self.assertTrue(db.is_suppressed("reply@target-test.com"))
        self.assertEqual(db.one("SELECT * FROM leads WHERE id=?", (self.lead,))["status"],
                         "unsubscribed")
        self.assertTrue(db.is_suppressed("reply@target-test.com"))

    def test_sync_is_idempotent(self):
        fake = [{"from": "R <reply@target-test.com>", "address": "reply@target-test.com",
                 "subject": "Re: Hello", "date": "today",
                 "message_id": "<dup-1@target-test.com>", "in_reply_to": [self.send["message_id"]]}]
        with mock.patch.object(mail, "fetch_recent", return_value=(fake, "")):
            pipeline.sync_inbox()
            pipeline.sync_inbox()
        n = db.scalar("SELECT COUNT(*) FROM replies")
        self.assertEqual(n, 1)


class TrackingTests(unittest.TestCase):
    def setUp(self):
        wipe()
        lead = db.insert("leads", {"email": "track@target-test.com", "status": "contacted"})
        db.insert("sends", {"lead_id": lead, "to_email": "track@target-test.com",
                            "subject": "s", "token": "tok123", "status": "sent", "sent_ts": db.now()})
        self.lead = lead

    def test_open_and_click_are_counted_once(self):
        self.assertTrue(pipeline.record_open("tok123"))
        pipeline.record_open("tok123")
        pipeline.record_click("tok123")
        send = db.one("SELECT * FROM sends WHERE token='tok123'")
        self.assertEqual(send["opens"], 2)
        self.assertEqual(send["clicks"], 1)
        self.assertGreater(send["opened_ts"], 0)
        self.assertEqual(pipeline.record_open("nope"), False)

    def test_unsubscribe_link_suppresses(self):
        self.assertTrue(pipeline.unsubscribe("tok123"))
        lead = db.one("SELECT * FROM leads WHERE id=?", (self.lead,))
        self.assertEqual(lead["status"], "unsubscribed")
        self.assertTrue(db.is_suppressed("track@target-test.com"))


class DemoAndStatsTests(unittest.TestCase):
    def setUp(self):
        wipe()

    def test_demo_seed_and_remove(self):
        res = demo.seed()
        self.assertTrue(res["ok"])
        self.assertEqual(res["leads"], 24)
        stats = pipeline.stats()
        self.assertEqual(stats["leads_total"], 24)
        self.assertGreater(stats["sequences"], 0)
        # demo sequences are paused so nothing can send by accident
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM enrollments WHERE status='running'"), 0)
        out = demo.remove()
        self.assertTrue(out["ok"])
        self.assertEqual(pipeline.stats()["leads_total"], 0)


class LeadSaveTests(unittest.TestCase):
    """Saving a lead must never silently erase the fields the form does not show."""

    def setUp(self):
        wipe()

    def make_lead(self, email="keep@target-test.com"):
        return db.insert("leads", {
            "email": email, "phone": "+421900000000", "country": "Slovakia",
            "industry": "accounting", "linkedin": "https://www.linkedin.com/in/x",
            "website": "https://keep.sk", "tags": "warm, vip", "source": "demo",
            "status": "new", "verify_status": "valid", "created_ts": db.now()})

    def test_editing_one_field_keeps_the_others(self):
        lid = self.make_lead()
        res = web.r_lead_save({"id": lid, "email": "keep@target-test.com",
                               "first_name": "Anna", "last_name": "", "company": "",
                               "title": "", "city": "", "notes": "", "status": "new"})
        self.assertTrue(res["ok"])
        row = db.one("SELECT * FROM leads WHERE id=?", (lid,))
        self.assertEqual(row["first_name"], "Anna")
        self.assertEqual(row["phone"], "+421900000000")
        self.assertEqual(row["country"], "Slovakia")
        self.assertEqual(row["industry"], "accounting")
        self.assertEqual(row["linkedin"], "https://www.linkedin.com/in/x")
        self.assertEqual(row["website"], "https://keep.sk")
        self.assertEqual(row["tags"], "warm, vip")
        self.assertEqual(row["source"], "demo")
        self.assertEqual(row["verify_status"], "valid")

    def test_changing_the_email_asks_for_a_new_verification(self):
        lid = self.make_lead()
        res = web.r_lead_save({"id": lid, "email": "brand-new@target-test.com"})
        self.assertTrue(res["ok"])
        row = db.one("SELECT * FROM leads WHERE id=?", (lid,))
        self.assertEqual(row["verify_status"], "unverified")
        self.assertEqual(row["domain"], "target-test.com")

    def test_create_still_fills_every_column(self):
        res = web.r_lead_save({"email": "Fresh@Target-Test.com", "first_name": "Bo"})
        self.assertTrue(res["ok"])
        row = db.one("SELECT * FROM leads WHERE id=?", (res["id"],))
        self.assertEqual(row["email"], "fresh@target-test.com")
        self.assertEqual(row["status"], "new")
        self.assertEqual(row["domain"], "target-test.com")

    def test_saving_an_unknown_lead_is_an_error(self):
        res = web.r_lead_save({"id": 999999, "email": "ghost@target-test.com"})
        self.assertFalse(res["ok"])


class LeadStatusTests(unittest.TestCase):
    """A manual status change has to stop the sequence, or follow-ups keep going out."""

    def setUp(self):
        wipe()
        self.box = add_mailbox()
        self.seq = add_sequence([{"subject": "Step one", "body": "Hello there"}])

    def enroll(self, status="contacted"):
        lead = db.insert("leads", {"email": "flow@target-test.com", "status": status})
        en = db.insert("enrollments", {"sequence_id": self.seq, "lead_id": lead,
                                       "mailbox_id": self.box, "status": "running", "step_no": 0,
                                       "next_run_ts": 0, "started_ts": db.now()})
        return lead, en

    def test_marking_won_stops_the_sequence(self):
        lead, en = self.enroll()
        web.r_lead_status({"ids": [lead], "status": "won"})
        row = db.one("SELECT * FROM enrollments WHERE id=?", (en,))
        self.assertNotEqual(row["status"], "running")
        self.assertGreater(row["finished_ts"], 0)

    def test_marking_replied_sets_the_reply_time(self):
        lead, en = self.enroll()
        web.r_lead_status({"ids": [lead], "status": "replied"})
        row = db.one("SELECT * FROM leads WHERE id=?", (lead,))
        self.assertGreater(row["replied_ts"], 0)
        self.assertEqual(db.one("SELECT * FROM enrollments WHERE id=?", (en,))["status"], "replied")
        # ... and the queue will not touch that lead again
        self.assertEqual(pipeline.tick(force=True)["action"], "idle")

    def test_marking_unsubscribed_still_suppresses(self):
        lead, en = self.enroll()
        web.r_lead_status({"ids": [lead], "status": "unsubscribed"})
        self.assertTrue(db.is_suppressed("flow@target-test.com"))
        self.assertEqual(db.one("SELECT * FROM enrollments WHERE id=?", (en,))["status"],
                         "unsubscribed")


class PacingTests(unittest.TestCase):
    def test_jitter_actually_delays_the_next_send(self):
        box = {"last_send_ts": int(time.time()) - 2}
        ready, wait = pipeline.gap_ready(box, {"sending": {"gap_seconds": 10, "jitter_seconds": 7}})
        self.assertFalse(ready)
        self.assertGreaterEqual(wait, 1)
        self.assertLessEqual(wait, 10 + 7)

    def test_no_jitter_means_an_exact_gap(self):
        now = int(time.time())
        ready, wait = pipeline.gap_ready({"last_send_ts": now},
                                         {"sending": {"gap_seconds": 30, "jitter_seconds": 0}})
        self.assertFalse(ready)
        self.assertLessEqual(wait, 30)


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        wipe()
        demo.seed()
        cls.httpd = web.make_server(0)
        cls.port = cls.httpd.server_address[1]
        web.PORT = cls.port
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = "http://127.0.0.1:%d" % cls.port
        cls.token = config.load_settings()["session_token"]

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def get(self, path, headers=None):
        req = urllib.request.Request(self.base + path, headers=headers or {})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode("utf-8", "replace"), dict(r.headers)

    def test_index_page_serves_and_injects_token(self):
        status, body, headers = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("Lead Studio Lite", body)
        self.assertIn(self.token, body)
        self.assertIn("Content-Security-Policy", headers)

    def test_state_api(self):
        status, body, _ = self.get("/api/state")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertTrue(data["ok"])
        self.assertEqual(data["stats"]["leads_total"], 24)
        self.assertIn("settings", data)
        self.assertNotIn("session_token", data["settings"])

    def test_leads_api_search(self):
        status, body, _ = self.get("/api/leads?q=northline")
        data = json.loads(body)
        self.assertTrue(data["ok"])
        self.assertEqual(data["total"], 1)

    def test_export_csv(self):
        status, body, headers = self.get("/export.csv")
        self.assertEqual(status, 200)
        self.assertIn("email,", body.lower().replace(" ", ""))
        self.assertIn("attachment", headers.get("Content-Disposition", ""))

    def test_post_requires_token(self):
        body = json.dumps({"settings": {"onboarded": True}}).encode()
        req = urllib.request.Request(self.base + "/api/settings/save", data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(ctx.exception.code, 403)

    def test_post_with_token_saves_settings(self):
        body = json.dumps({"settings": {"sender_name": "Test Person"}}).encode()
        req = urllib.request.Request(
            self.base + "/api/settings/save", data=body,
            headers={"Content-Type": "application/json", "X-LS-Token": self.token}, method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode())
        self.assertTrue(data["ok"])
        self.assertEqual(config.load_settings()["sender_name"], "Test Person")

    def test_tracking_pixel_and_unsubscribe_page(self):
        lead = db.insert("leads", {"email": "pix@target-test.com", "status": "contacted"})
        db.insert("sends", {"lead_id": lead, "token": "pixtok", "status": "sent", "sent_ts": db.now()})
        status, body, headers = self.get("/t/o/pixtok.gif")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Type"), "image/gif")
        db.execute("UPDATE sends SET token='unsubtok' WHERE token='pixtok'")
        status, body, _ = self.get("/t/u/unsubtok")
        self.assertEqual(status, 200)
        self.assertIn("unsubscribed", body.lower())
        self.assertTrue(db.is_suppressed("pix@target-test.com"))

    def test_unknown_static_path_falls_back_to_index(self):
        status, body, _ = self.get("/something-else")
        self.assertEqual(status, 200)
        self.assertIn("Lead Studio Lite", body)

    def test_tracking_links_work_through_a_public_host(self):
        # Recipients open these through tracking.base_url (a tunnel or a domain), so the
        # request arrives with that Host - it must not be rejected as "not local".
        lead = db.insert("leads", {"email": "tunnel@target-test.com", "status": "contacted"})
        db.insert("sends", {"lead_id": lead, "token": "tunneltok", "status": "sent",
                            "sent_ts": db.now()})
        status, body, headers = self.get("/t/o/tunneltok.gif",
                                         headers={"Host": "mail.example.com"})
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Type"), "image/gif")
        db.execute("UPDATE sends SET token='tunnelunsub' WHERE token='tunneltok'")
        status, body, _ = self.get("/t/u/tunnelunsub", headers={"Host": "mail.example.com"})
        self.assertEqual(status, 200)
        self.assertIn("unsubscribed", body.lower())

    def test_api_is_still_refused_for_a_foreign_host(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/api/state", headers={"Host": "mail.example.com"})
        self.assertEqual(ctx.exception.code, 403)

    def test_oauth_error_page_escapes_the_message(self):
        evil = urllib.parse.quote('<img src=x onerror="alert(1)">')
        status, body, headers = self.get("/oauth/callback?error=" + evil)
        self.assertEqual(status, 200)
        self.assertNotIn("<img", body)
        self.assertIn("&lt;img", body)
        self.assertIn("Content-Security-Policy", headers)
        self.assertEqual(headers.get("X-Frame-Options"), "DENY")

    def test_settings_reject_an_impossible_sending_window(self):
        for bad in ({"window_start": 25, "window_end": 18},
                    {"window_start": 18, "window_end": 18},
                    {"gap_seconds": "abc"}):
            body = json.dumps({"settings": {"sending": dict(bad)}}).encode()
            req = urllib.request.Request(
                self.base + "/api/settings/save", data=body,
                headers={"Content-Type": "application/json", "X-LS-Token": self.token},
                method="POST")
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read().decode())
            self.assertFalse(data["ok"], "accepted %r" % (bad,))
        self.assertEqual(config.load_settings()["sending"]["window_start"], 8)

    def test_invalid_content_length_gets_a_json_error(self):
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as sock:
            sock.sendall(b"POST /api/wipe HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                         b"X-LS-Token: " + self.token.encode() + b"\r\n"
                         b"Content-Length: abc\r\n\r\n")
            raw = sock.recv(8192).decode("latin-1")
        self.assertIn("400", raw.split("\r\n")[0])
        self.assertIn("Content-Length", raw)

    def test_oversized_body_is_refused_before_it_is_read(self):
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as sock:
            sock.sendall(b"POST /api/wipe HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                         b"X-LS-Token: " + self.token.encode() + b"\r\n"
                         b"Content-Length: 999999999999\r\n\r\n")
            raw = sock.recv(8192).decode("latin-1")
        self.assertIn("413", raw.split("\r\n")[0])

    def test_export_csv_is_excel_safe(self):
        db.insert("leads", {"email": "excel@target-test.com", "first_name": "NovĂˇkovĂˇ",
                            "company": '=HYPERLINK("http://evil","x")', "status": "new"})
        try:
            status, body, _ = self.get("/export.csv")
            self.assertEqual(status, 200)
            self.assertTrue(body.startswith("\ufeff"), "the BOM is what makes Excel read UTF-8")
            self.assertIn("NovĂˇkovĂˇ", body)
            self.assertNotIn('"=HYPERLINK', body)
            self.assertIn("'=HYPERLINK", body)
        finally:
            db.execute("DELETE FROM leads WHERE email='excel@target-test.com'")


class SendDurabilityTests(unittest.TestCase):
    """A prospect must never get the same step twice, and a broken mailbox must not be
    hammered on every tick."""

    def setUp(self):
        wipe()
        self.box = add_mailbox()
        self.seq = add_sequence([{"delay": 0, "subject": "Hello", "body": "Body"},
                                 {"delay": 3, "subject": "Second", "body": "Body two"}])
        self.lead = db.insert("leads", {"email": "race@target-test.com", "first_name": "R",
                                        "company": "C", "status": "new"})
        config.save_settings({**config.load_settings(),
                              "sending": {"enabled": True, "window_start": 0, "window_end": 24,
                                          "gap_seconds": 0, "jitter_seconds": 0,
                                          "weekdays_only": False}})
        SINK.messages.clear()
        pipeline.enroll(self.seq, [self.lead], self.box)

    def enrollment(self):
        return db.one("SELECT * FROM enrollments WHERE lead_id=?", (self.lead,))

    def test_two_ticks_never_send_the_same_step_twice(self):
        real_send = mail.smtp_send

        def slow_send(*args, **kwargs):
            time.sleep(0.3)
            return real_send(*args, **kwargs)

        results = []
        with mock.patch.object(mail, "smtp_send", side_effect=slow_send):
            threads = [threading.Thread(target=lambda: results.append(pipeline.tick(force=True)))
                       for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self.assertEqual(len(SINK.messages), 1, results)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM sends WHERE status='sent'"), 1)

    def test_a_failed_send_is_not_retried_on_every_tick(self):
        with mock.patch.object(mail, "smtp_send",
                               return_value=(False, "The server refused the recipient: nope")):
            first = pipeline.tick(force=True)
            second = pipeline.tick(force=True)
        self.assertFalse(first["ok"])
        self.assertEqual(second.get("action"), "idle")
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM sends WHERE status='failed'"), 1)
        self.assertGreater(self.enrollment()["next_run_ts"], db.now() + 60)

    def test_it_gives_up_after_three_failures(self):
        for _ in range(3):
            db.execute("UPDATE enrollments SET next_run_ts=0 WHERE lead_id=?", (self.lead,))
            with mock.patch.object(mail, "smtp_send",
                                   return_value=(False, "Could not connect to host")):
                pipeline.tick(force=True)
        en = self.enrollment()
        self.assertEqual(en["status"], "paused")
        self.assertIn("Gave up", en["last_error"])
        # The retries reuse the same send row instead of piling up one per tick.
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM sends WHERE lead_id=?", (self.lead,)), 1)

    def test_a_dropped_connection_mid_send_is_never_resent(self):
        with mock.patch.object(mail, "smtp_send", return_value=(False, "Sending failed: timed out")):
            pipeline.tick(force=True)
        self.assertEqual(db.scalar("SELECT status FROM sends WHERE lead_id=?", (self.lead,)),
                         "unknown")
        self.assertEqual(self.enrollment()["status"], "paused")
        # Put it back in the queue: the claim still stops a second, duplicate delivery.
        db.execute("UPDATE enrollments SET next_run_ts=0, status='running' WHERE lead_id=?",
                   (self.lead,))
        with mock.patch.object(mail, "smtp_send", return_value=(True, "<x@target-test.com>")):
            res = pipeline.tick(force=True)
        self.assertFalse(res["ok"])
        self.assertEqual(len(SINK.messages), 0)


class DataSafetyTests(unittest.TestCase):
    def setUp(self):
        wipe()

    def test_deleting_a_lead_removes_its_email_content(self):
        lead = db.insert("leads", {"email": "bye@target-test.com", "status": "contacted"})
        db.insert("sends", {"lead_id": lead, "to_email": "bye@target-test.com",
                            "subject": "Hi", "body": "the whole message", "status": "sent"})
        db.insert("replies", {"lead_id": lead, "from_email": "bye@target-test.com",
                              "snippet": "their answer"})
        web.r_lead_delete({"ids": [lead]})
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM sends"), 0)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM replies"), 0)

    def test_broken_settings_are_kept_instead_of_overwritten(self):
        with open(config.SETTINGS_PATH, "w", encoding="utf-8") as fh:
            fh.write('{"company_name": "Example Co", "sending": {')
        settings = config.load_settings()
        self.assertTrue(settings.get("session_token"))
        backups = [n for n in os.listdir(config.DATA_DIR) if ".broken-" in n]
        self.assertEqual(len(backups), 1)
        with open(os.path.join(config.DATA_DIR, backups[0]), encoding="utf-8") as fh:
            self.assertIn("Example Co", fh.read())

    def test_valid_json_that_is_not_an_object_does_not_crash(self):
        with open(config.SETTINGS_PATH, "w", encoding="utf-8") as fh:
            fh.write('["not", "a", "settings", "object"]')
        self.assertTrue(config.load_settings().get("session_token"))

    def test_a_job_left_running_by_a_crash_is_picked_up_again(self):
        scheduler.sync_registry()
        db.execute("UPDATE jobs SET running=1")
        with mock.patch.object(scheduler.threading, "Thread"):
            scheduler.start()
        scheduler.stop()
        scheduler._thread = None
        scheduler._stop.clear()
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM jobs WHERE running<>0"), 0)


class UnsubscribeHeaderTests(unittest.TestCase):
    def box(self):
        return {"from_email": "sender@test.local", "from_name": "Sender",
                "reply_to": "", "smtp_user": "sender@test.local"}

    def test_one_click_is_only_advertised_when_it_can_be_honoured(self):
        plain = mail.build_message(self.box(), "to@test.local", "Hi", "Body",
                                   unsub_mailto="sender@test.local")
        self.assertEqual(plain["List-Unsubscribe"], "<mailto:sender@test.local?subject=unsubscribe>")
        self.assertIsNone(plain["List-Unsubscribe-Post"])

        full = mail.build_message(self.box(), "to@test.local", "Hi", "Body",
                                  unsub_mailto="sender@test.local",
                                  unsub_url="https://mail.example.com/t/u/tok")
        self.assertIn("https://mail.example.com/t/u/tok", full["List-Unsubscribe"])
        self.assertEqual(full["List-Unsubscribe-Post"], "List-Unsubscribe=One-Click")


if __name__ == "__main__":
    unittest.main(verbosity=2)
