"""A finding is only worth as much as what you can do with it: score, lead, email, campaign."""
import os
import sys
import tempfile
import unittest
from unittest import mock

if not os.environ.get("LS_DATA"):
    os.environ["LS_DATA"] = tempfile.mkdtemp(prefix="ls_daily_")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine import daily, db, offers, server  # noqa: E402

DOM = "-daily-test.com"
LIKE = "%" + DOM


class DailyTests(unittest.TestCase):
    def setUp(self):
        db.init_db()
        self.clean()
        self.offer = db.insert("offers", {
            "name": "AI booking for roofers", "active": 1,
            "keywords": "roofing, roof, roofing companies", "category": "roofing",
            "description": "An AI receptionist that books roof inspections.",
            "affiliate_url": "https://example.test/offer", "commission": "20%",
            "pitch_subject": "{{company}} - bookings while you are on a roof",
            "pitch_body": "Hi {{first_name|there}}, a thought for {{company}} about {{offer_name}}.",
            "created_ts": db.now()})
        self.roof = db.insert("signals", {
            "ts": db.now(), "kind": "new-domain", "domain": "roofers" + DOM,
            "company": "Bright Roofing Ltd", "title": "roofing contractor in Leeds",
            "source": "test", "source_url": "https://roofers" + DOM + "/1",
            "payload": "{}"})
        self.cafe = db.insert("signals", {
            "ts": db.now(), "kind": "new-domain", "domain": "cafe" + DOM,
            "company": "Corner Cafe", "title": "coffee shop", "source": "test",
            "source_url": "https://cafe" + DOM + "/1", "payload": "{}"})

    def tearDown(self):
        self.clean()

    def clean(self):
        db.execute("DELETE FROM signals WHERE domain LIKE ?", (LIKE,))
        db.execute("DELETE FROM leads WHERE notes LIKE ?", (LIKE,))
        db.execute("DELETE FROM offers WHERE name='AI booking for roofers'")
        db.execute("DELETE FROM sends WHERE to_email LIKE ?", (LIKE,))

    def test_a_signal_is_scored_against_what_you_sell(self):
        offer = db.one("SELECT * FROM offers WHERE id=?", (self.offer,))
        signal = db.one("SELECT * FROM signals WHERE id=?", (self.roof,))
        scored = daily.score(offer, signal)
        self.assertGreaterEqual(scored["fit"], 50)
        self.assertIn("roofing", scored["matched"])

    def test_an_unrelated_finding_does_not_match(self):
        offer = db.one("SELECT * FROM offers WHERE id=?", (self.offer,))
        signal = db.one("SELECT * FROM signals WHERE id=?", (self.cafe,))
        self.assertEqual(daily.score(offer, signal)["fit"], 0)

    def test_the_feed_puts_the_best_fit_first_and_can_filter(self):
        rows = daily.feed(limit=50, offer_id=self.offer)
        mine = [row for row in rows if (row["domain"] or "").endswith(DOM)]
        self.assertEqual(mine[0]["id"], self.roof)
        self.assertEqual(mine[0]["offer"], "AI booking for roofers")
        only = daily.feed(limit=50, offer_id=self.offer, min_fit=1)
        self.assertNotIn(self.cafe, [row["id"] for row in only])

    def test_promote_turns_a_finding_into_a_lead(self):
        answer = daily.promote(self.roof)
        self.assertTrue(answer["ok"], answer)
        self.assertEqual(answer["email"], "info@roofers" + DOM)
        lead = db.one("SELECT * FROM leads WHERE id=?", (answer["lead_id"],))
        self.assertEqual(lead["company"], "Bright Roofing Ltd")
        self.assertEqual(lead["source"], "new-domain")
        self.assertIn("Signal new-domain", lead["notes"])
        self.assertEqual(db.scalar("SELECT lead_id FROM signals WHERE id=?", (self.roof,)),
                         answer["lead_id"])

    def test_promote_without_a_domain_asks_for_an_address(self):
        signal = db.insert("signals", {"ts": db.now(), "kind": "ad-spend", "domain": "",
                                       "company": "No Domain Co", "title": "ads",
                                       "source": "test", "source_url": "https://nd" + DOM,
                                       "payload": "{}"})
        answer = daily.promote(signal)
        self.assertFalse(answer["ok"])
        self.assertIn("email address", answer["error"])
        db.execute("DELETE FROM signals WHERE id=?", (signal,))

    def test_the_draft_uses_the_offer_pitch(self):
        answer = daily.draft(self.roof, offer_id=self.offer)
        self.assertTrue(answer["ok"], answer)
        self.assertIn("Bright Roofing", answer["subject"])
        self.assertIn("AI booking for roofers", answer["body"])
        self.assertEqual(answer["to"], "info@roofers" + DOM)
        self.assertIn("example.test", answer["body"])

    def test_a_finding_can_be_put_straight_into_a_campaign(self):
        sequence = db.insert("sequences", {"name": "Roofers" + DOM, "created_ts": db.now(),
                                           "active": 1})
        db.insert("steps", {"sequence_id": sequence, "step_no": 1, "delay_days": 0,
                            "subject": "hi", "body": "hello"})
        answer = daily.to_campaign(self.roof, sequence)
        self.assertTrue(answer["ok"], answer)
        self.assertEqual(answer["sequence"], "Roofers" + DOM)
        self.assertTrue(db.one("SELECT * FROM enrollments WHERE sequence_id=? AND lead_id=?",
                               (sequence, answer["lead_id"])))
        db.execute("DELETE FROM enrollments WHERE sequence_id=?", (sequence,))
        db.execute("DELETE FROM steps WHERE sequence_id=?", (sequence,))
        db.execute("DELETE FROM sequences WHERE id=?", (sequence,))

    def test_contacting_sends_from_a_mailbox_and_records_it(self):
        box = db.insert("mailboxes", {"name": "Daily", "from_email": "me" + DOM,
                                      "smtp_host": "smtp.test", "imap_host": "imap.test",
                                      "daily_limit": 30, "active": 1})
        db.execute("DELETE FROM leads WHERE email LIKE ?", ("%roofers" + DOM,))
        with mock.patch.object(daily.mail, "smtp_send",
                               return_value=(True, "<msg-1@test>")):
            answer = daily.contact(self.roof, "Hello", "Body for {{company}}")
        self.assertTrue(answer["ok"], answer)
        self.assertEqual(answer["mailbox"], "Daily")
        send = db.one("SELECT * FROM sends WHERE message_id='<msg-1@test>'")
        self.assertEqual(send["status"], "sent")
        lead = db.one("SELECT * FROM leads WHERE id=?", (answer["lead_id"],))
        self.assertEqual(lead["status"], "contacted")
        db.execute("DELETE FROM sends WHERE message_id='<msg-1@test>'")
        db.execute("DELETE FROM mailboxes WHERE id=?", (box,))

    def test_a_mailbox_at_its_cap_refuses(self):
        box = db.insert("mailboxes", {"name": "Full", "from_email": "full" + DOM,
                                      "smtp_host": "smtp.test", "daily_limit": 1, "active": 1})
        db.insert("sends", {"mailbox_id": box, "lead_id": 0, "to_email": "x" + DOM,
                            "status": "sent", "sent_ts": db.now()})
        answer = daily.contact(self.roof, "Hello", "Body", mailbox_id=box)
        self.assertFalse(answer["ok"])
        self.assertIn("daily limit", answer["error"])
        db.execute("DELETE FROM sends WHERE mailbox_id=?", (box,))
        db.execute("DELETE FROM mailboxes WHERE id=?", (box,))


class RouteTests(unittest.TestCase):
    def test_the_feed_route_carries_the_offers_to_pick_from(self):
        db.init_db()
        answer = server.r_signals({"limit": 5})
        self.assertTrue(answer["ok"])
        self.assertIn("offers", answer)
        self.assertIn("signals", answer)

    def test_a_draft_for_a_missing_finding_is_a_clear_error(self):
        answer = server.r_signal_draft({"id": 999999})
        self.assertFalse(answer["ok"])
        self.assertIn("no longer exists", answer["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

