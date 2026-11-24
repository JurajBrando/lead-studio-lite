"""Radar finds and approves -> Army writes and sends -> replies are sorted -> XP comes back."""
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

if not os.environ.get("LS_DATA"):
    os.environ["LS_DATA"] = tempfile.mkdtemp(prefix="ls_radar_army_")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine import army, config, db, hunt, llm, mail, offers, radar, server, verify  # noqa: E402

assert not config.DB_PATH.startswith(os.path.join(ROOT, "data")), "tests must never touch the real database"

OFFER_TEXT = """HYROS - ad tracking and attribution for ecommerce brands spending on Meta and Google ads.
Problem: the ad platforms misreport ROAS so media buyers scale the wrong campaigns.
Must run paid ads.
Out: agencies and freelancers.
Competitors: Triple Whale, Northbeam.
Price $230 per month."""

SITE_HTML = """<html><head><title>Glow Skincare - clean skincare</title>
<meta name="description" content="Clean skincare shipped across the US. Free shipping over $50.">
<script src="https://cdn.shopify.com/s/x.js"></script><script>fbq('init','123456789012');</script>
<script src="https://www.googletagmanager.com/gtm.js?id=GTM-ABC1234"></script></head>
<body>Add to cart. Free shipping. Our ROAS on paid social doubled. <a href="/contact">Contact</a>
hello@glow-test.com</body></html>"""


def fake_fetch(url, timeout=15):
    if "agency" in url:
        return "<html><title>Growth agency</title><body>marketing agency for brands</body></html>"
    return SITE_HTML


class RadarArmyTests(unittest.TestCase):
    def setUp(self):
        db.init_db()
        for table in ("leads", "signals", "offers", "mailboxes", "sends", "replies", "radar_leads", "radar_cards",
                      "hunts", "soldiers", "army_events", "army_claims", "army_bandit", "army_drafts", "army_suppress",
                      "app_kv", "domains", "llm_providers", "suppressions"):
            db.execute("DELETE FROM %s" % table)
        s = config.load_settings()
        s["army"] = {}
        s["sending"] = dict(s.get("sending", {}), weekdays_only=False, window_start=0, window_end=24, gap_seconds=0,
                            jitter_seconds=0)
        config.save_settings(s)
        res = hunt.offer_from_text(OFFER_TEXT, name="HYROS", affiliate_url="https://hyros.example/aff")
        self.offer_id = res["offer_id"]
        now = db.now()
        for i, dom in enumerate(("glow-test.com", "agency-test.com", "shop2-test.com")):
            db.insert("signals", {"ts": now - 86400, "kind": "google-ads", "domain": dom,
                                  "company": dom.split("-")[0].title(), "title": "spends on google ads",
                                  "source": "test", "source_url": "https://x/%d" % i,
                                  "payload": json.dumps({"impressions": 300000})})
        db.insert("leads", {"email": "", "company": "Glow", "domain": "glow-test.com", "industry": "ecommerce skincare",
                            "country": "United States", "status": "new", "created_ts": now})
        self.boxes = [db.insert("mailboxes", {"name": "Box %d" % i, "from_email": "me%d@sender-test.com" % i,
                                              "from_name": "George", "smtp_host": "smtp.test", "daily_limit": 30,
                                              "active": 1, "created_ts": now}) for i in (1, 2)]

    # ---------------------------------------------------------------- radar
    def test_parse_offer_card(self):
        card = radar.parse_offer(OFFER_TEXT)
        self.assertEqual(card["category"], "attribution")
        self.assertIn("Triple Whale", card["competitors"])
        self.assertEqual(card["price"], 230)
        self.assertTrue(any("agencies" in d for d in card["disqualify"]))

    def run_hunt(self):
        with mock.patch.object(verify, "mx_lookup", return_value=(["mx.test"], "dnspython")), \
                mock.patch("engine.crawl.fetch_page", side_effect=fake_fetch):
            res = hunt.start(self.offer_id, 100, 50)
            self.assertTrue(res["ok"], res)
            for _ in range(200):
                if not hunt.RUN["running"]:
                    break
                time.sleep(0.05)
        return hunt.state(self.offer_id)["hunt"]

    def test_hunt_scores_reads_sites_and_approves(self):
        h = self.run_hunt()
        self.assertEqual(h["status"], "done", h.get("note"))
        domains = {r["domain"]: r for r in h["rows"]}
        self.assertIn("glow-test.com", domains)
        self.assertNotIn("agency-test.com", domains)              # "out: agencies" on the card
        glow = domains["glow-test.com"]
        self.assertEqual(glow["email"], "hello@glow-test.com")
        self.assertGreaterEqual(glow["email_confidence"], 70)
        self.assertIn("Shopify", glow["tools"])
        self.assertTrue(glow["why"])
        res = hunt.approve(h["id"], top=10)
        self.assertGreaterEqual(res["approved"], 1, res)
        rl = db.one("SELECT * FROM radar_leads WHERE domain='glow-test.com'")
        self.assertEqual(rl["status"], "approved")
        lead = db.one("SELECT * FROM leads WHERE id=?", (rl["lead_id"],))
        self.assertEqual(lead["email"], "hello@glow-test.com")
        self.assertIn("Glow Skincare", json.loads(rl["profile"])["title"])

    # ---------------------------------------------------------------- army
    def test_deploy_spreads_offers_over_mailboxes(self):
        second = offers.save({"name": "Other offer", "keywords": "dental"})["id"]
        res = army.deploy([self.offer_id, second], mode="spread")
        self.assertEqual(res["created"], 2)
        got = sorted(r["offer_id"] for r in db.rows("SELECT offer_id FROM soldiers"))
        self.assertEqual(got, sorted([self.offer_id, second]))
        res = army.deploy([self.offer_id], mode="same", reassign=True)
        self.assertEqual({r["offer_id"] for r in db.rows("SELECT offer_id FROM soldiers")}, {self.offer_id})
        self.assertFalse(army.save_soldier({"mailbox_id": self.boxes[0]})["ok"])   # one soldier per inbox

    def full_flow(self):
        h = self.run_hunt()
        hunt.approve(h["id"], top=10)
        army.deploy([self.offer_id], mode="same")
        sid = db.one("SELECT id FROM soldiers ORDER BY id LIMIT 1")["id"]
        llm.save({"name": "OpenRouter", "base_url": "https://openrouter.ai/api/v1", "model": "openai/gpt-4o-mini",
                  "api_key": "k"})
        answer = json.dumps({"subject": "glow and your roas", "body": "Hi, saw Glow runs Meta and Google pixels. Worth a look?"})
        with mock.patch.object(llm, "chat", return_value=answer) as chat:
            res = army.turn(sid)
            self.assertTrue(res["ok"], res)
            self.assertTrue(res["drafted"])
            prompt = chat.call_args[0][0][0]["content"]
            self.assertIn("Glow Skincare", prompt)                  # it read the company's profile
        draft = db.one("SELECT * FROM army_drafts WHERE id=?", (res["drafted"],))
        self.assertIn("https://hyros.example/aff", draft["body"])
        with mock.patch.object(mail, "smtp_send", return_value=(True, "<m1@test>")):
            sent = army.send_draft(draft["id"])
        self.assertTrue(sent["ok"], sent)
        return sid, draft

    def test_turn_writes_from_profile_then_send_pays_xp(self):
        sid, draft = self.full_flow()
        s = army.soldier(sid)
        self.assertEqual(s["xp"], army.XP_SEND)
        lead = db.one("SELECT * FROM leads WHERE id=?", (draft["lead_id"],))
        self.assertEqual(lead["status"], "contacted")
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM sends WHERE status='sent'"), 1)
        self.assertEqual(db.one("SELECT status FROM radar_leads WHERE lead_id=?", (lead["id"],))["status"], "sent")
        self.assertEqual(db.one("SELECT sends FROM army_bandit")["sends"], 1)
        # the company is claimed: the second soldier cannot take it
        other = db.one("SELECT id FROM soldiers WHERE id<>? LIMIT 1", (sid,))
        if other:
            self.assertFalse(army.claim(army.company_key("glow-test.com"), other["id"]))

    def test_positive_reply_pays_and_finishes_mission(self):
        sid, draft = self.full_flow()
        lead_id = draft["lead_id"]
        for i in range(3):
            rid = db.insert("replies", {"lead_id": lead_id, "mailbox_id": self.boxes[0], "from_email": "hello@glow-test.com",
                                        "subject": "Re: glow", "snippet": "Sounds good, send me pricing",
                                        "message_id": "<r%d>" % i, "received_ts": db.now()})
        out = army.sort_replies(fetch_snippets=False)
        self.assertEqual(out["counts"].get("positive"), 3)
        s = army.soldier(sid)
        self.assertEqual(s["xp"], army.XP_SEND + 3 * army.XP_POSITIVE + army.MISSION_XP)
        self.assertTrue(army.mission()["done"])
        self.assertEqual(db.one("SELECT status FROM leads WHERE id=?", (lead_id,))["status"], "interested")

    def test_reply_classes(self):
        self.assertEqual(army.classify("Automatická odpoveď - som na dovolenke"), "auto_reply")
        self.assertEqual(army.classify("Prosím odhlásiť"), "unsub")
        self.assertEqual(army.classify("Ďakujem, nemám záujem"), "not_now")
        self.assertEqual(army.classify("Mail delivery failed: user unknown"), "bounce")
        self.assertEqual(army.classify("Pošlite mi cenník"), "positive")
        self.assertEqual(army.classify("Who are you?"), "")

    def test_bounces_bench_the_soldier(self):
        sid, draft = self.full_flow()
        for i in range(2):
            db.insert("replies", {"lead_id": draft["lead_id"], "mailbox_id": self.boxes[0], "from_email": "mailer-daemon@x",
                                  "subject": "Undelivered", "is_bounce": 1, "message_id": "<b%d>" % i,
                                  "received_ts": db.now()})
        army.sort_replies(fetch_snippets=False)
        self.assertEqual(army.soldier(sid)["state"], "benched")
        self.assertEqual(army.energy(army.soldier(sid))["limit"], 0)

    def test_doctrine_rotates_until_every_angle_has_data(self):
        army.deploy([self.offer_id], mode="same")
        s = army.soldier(db.one("SELECT id FROM soldiers LIMIT 1")["id"])
        seen = set()
        for _ in range(4):
            angle = army.best_angle(s)
            seen.add(angle)
            army._bandit_bump(self.offer_id, angle, "send")
        self.assertEqual(seen, set(army.ANGLES))

    def test_routes_answer(self):
        self.assertTrue(server.r_radar_state({"offer_id": str(self.offer_id)})["ok"])
        self.assertTrue(server.r_army_state()["ok"])
        self.assertTrue(server.r_radar_parse({"text": OFFER_TEXT})["ok"])
        self.assertIn("/api/army/deploy", server.ROUTES_POST)
        self.assertIn("/api/radar/state", server.ROUTES_GET)


if __name__ == "__main__":
    unittest.main()
