"""Downloaded senders become mailboxes and downloaded campaigns become sequences."""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

if not os.environ.get("LS_DATA"):
    os.environ["LS_DATA"] = tempfile.mkdtemp(prefix="ls_import_into_")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine import db, manyreach_api  # noqa: E402

DOM = "-import-test.com"


class ImportIntoAppTests(unittest.TestCase):
    def setUp(self):
        db.init_db()
        self.clean()
        db.insert("mcp_items", {
            "connection": "manyreach", "kind": "senders", "item_id": "s1",
            "name": "s1@import-test.com", "status": "warmup",
            "payload": json.dumps({
                "senderId": 1, "email": "s1" + DOM, "fromName": "Sender One",
                "replyTo": "reply" + DOM, "customSmtpServer": "smtp.test", "customSmtpPort": 587,
                "customSmtpUsername": "s1" + DOM, "customImapServer": "imap.test",
                "customImapPort": 993, "customImapUsername": "s1" + DOM,
                "dailyLimit": 25, "signature": "Best, Juraj"})})
        db.insert("mcp_items", {
            "connection": "manyreach", "kind": "senders", "item_id": "s2",
            "name": "s2@import-test.com", "status": "error",
            "payload": json.dumps({"senderId": 2, "email": "s2" + DOM,
                                   "fromName": "Sender Two", "disconnected": True,
                                   "customSmtpServer": "smtp.test", "customSmtpPort": 465})})
        db.insert("mcp_items", {
            "connection": "manyreach", "kind": "campaigns", "item_id": "c1",
            "name": "Roof washing", "status": "Draft",
            "payload": json.dumps({"campaignId": 1, "name": "Roof washing",
                                   "subject": "Quick question about roofs",
                                   "body": "<p>Hi there,</p><p>Worth a look?</p>"})})

    def tearDown(self):
        self.clean()

    def clean(self):
        db.execute("DELETE FROM mcp_items WHERE connection='manyreach'")
        db.execute("DELETE FROM mailboxes WHERE from_email LIKE ?", ("%" + DOM,))
        names = [row["id"] for row in db.rows("SELECT id FROM sequences WHERE name LIKE '[MR] %'")]
        for sequence_id in names:
            db.execute("DELETE FROM steps WHERE sequence_id=?", (sequence_id,))
            db.execute("DELETE FROM sequences WHERE id=?", (sequence_id,))

    def test_a_sender_becomes_a_mailbox_with_its_servers(self):
        answer = manyreach_api.import_into_app()
        self.assertTrue(answer["ok"], answer)
        box = db.one("SELECT * FROM mailboxes WHERE from_email=?", ("s1" + DOM,))
        self.assertIsNotNone(box)
        self.assertEqual(box["smtp_host"], "smtp.test")
        self.assertEqual(box["smtp_port"], 587)
        self.assertEqual(box["smtp_security"], "starttls")
        self.assertEqual(box["imap_host"], "imap.test")
        self.assertEqual(box["daily_limit"], 25)
        self.assertEqual(box["source"], "manyreach")
        self.assertEqual(answer["mailboxes_without_password"], 2)   # neither carries a password

    def test_a_mailbox_without_a_password_is_inactive(self):
        manyreach_api.import_into_app()
        box = db.one("SELECT * FROM mailboxes WHERE from_email=?", ("s1" + DOM,))
        self.assertEqual(box["active"], 0)
        self.assertEqual(box["smtp_pass"], "")

    def test_a_disconnected_sender_is_never_active(self):
        manyreach_api.import_into_app()
        box = db.one("SELECT * FROM mailboxes WHERE from_email=?", ("s2" + DOM,))
        self.assertEqual(box["active"], 0)

    def test_a_campaign_becomes_a_one_step_sequence_with_text(self):
        manyreach_api.import_into_app()
        sequence = db.one("SELECT * FROM sequences WHERE name=?", ("[MR] Roof washing",))
        self.assertIsNotNone(sequence)
        step = db.one("SELECT * FROM steps WHERE sequence_id=? AND step_no=1", (sequence["id"],))
        self.assertEqual(step["subject"], "Quick question about roofs")
        self.assertIn("Hi there,", step["body"])
        self.assertNotIn("<p>", step["body"])

    def test_a_second_import_does_not_duplicate_anything(self):
        first = manyreach_api.import_into_app()
        second = manyreach_api.import_into_app()
        self.assertEqual(first["mailboxes"], 2)
        self.assertEqual(second["mailboxes"], 0)
        self.assertEqual(first["sequences"], 1)
        self.assertEqual(second["sequences"], 0)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM mailboxes WHERE from_email LIKE ?",
                                   ("%" + DOM,)), 2)

    def test_an_existing_sender_email_is_not_touched(self):
        db.insert("mailboxes", {"name": "Mine", "from_email": "s1" + DOM, "smtp_host": "mine.test",
                                "created_ts": db.now(), "active": 1})
        manyreach_api.import_into_app()
        box = db.one("SELECT * FROM mailboxes WHERE from_email=?", ("s1" + DOM,))
        self.assertEqual(box["smtp_host"], "mine.test")


class CampaignStatsTests(unittest.TestCase):
    """The campaign's own numbers, as the sending platform reports them."""

    def setUp(self):
        db.init_db()
        self.clean()
        db.insert("mcp_items", {
            "connection": "manyreach", "kind": "campaigns", "item_id": "c9",
            "name": "With stats", "status": "Running",
            "payload": json.dumps({
                "campaignId": 127134, "name": "With stats", "subject": "s", "body": "<p>b</p>",
                "status": "Running", "createdAt": "2026-11-11T17:18:42.527Z",
                "prospectCount": 2595, "sentCount": 159, "replyCount": 2,
                "interestedCount": 1, "conversionCount": 1,
                "fromEmails": "a@x.com,b@x.com,c@x.com"})})

    def tearDown(self):
        self.clean()

    def clean(self):
        db.execute("DELETE FROM mcp_items WHERE connection='manyreach'")
        ids = [row["id"] for row in db.rows("SELECT id FROM sequences WHERE name LIKE '[MR] %'")]
        for sequence_id in ids:
            db.execute("DELETE FROM steps WHERE sequence_id=?", (sequence_id,))
            db.execute("DELETE FROM sequences WHERE id=?", (sequence_id,))

    def test_the_campaign_numbers_land_on_the_sequence(self):
        # No series lookup: sentCount is set and opens are zero, so it does try; patch it out.
        with mock.patch.object(manyreach_api, "campaign_totals",
                               return_value={"ok": True, "opened": 9, "clicked": 0, "replied": 2}):
            answer = manyreach_api.import_into_app()
        self.assertTrue(answer["ok"], answer)
        sequence = db.one("SELECT * FROM sequences WHERE name=?", ("[MR] With stats",))
        stats = json.loads(sequence["stats_json"])
        self.assertEqual(stats["prospects"], 2595)
        self.assertEqual(stats["reached"], 159)
        self.assertEqual(stats["replied"], 2)
        self.assertEqual(stats["opened"], 9)
        self.assertEqual(stats["senders"], 3)
        self.assertEqual(stats["source"], "manyreach")
        self.assertGreater(sequence["created_ts"], 1_700_000_000)

    def test_a_second_import_updates_the_numbers_only(self):
        with mock.patch.object(manyreach_api, "campaign_totals",
                               return_value={"ok": True, "opened": 9, "clicked": 0, "replied": 2}):
            manyreach_api.import_into_app()
            again = manyreach_api.import_into_app()
        self.assertEqual(again["sequences"], 0)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM sequences WHERE name='[MR] With stats'"), 1)

    def test_the_series_are_summed_from_their_count_arrays(self):
        payload = {"data": {"opensSeries": {"count": [1, 2, None]},
                            "clicksSeries": {"count": [0, 3]},
                            "replySeries": {"count": [2]},
                            "sentSeries": {"count": [39, 120]}}}
        with mock.patch.object(manyreach_api, "_request",
                               return_value={"ok": True, "data": payload["data"]}):
            totals = manyreach_api.campaign_totals(1)
        self.assertEqual(totals["opened"], 3)
        self.assertEqual(totals["clicked"], 3)
        self.assertEqual(totals["replied"], 2)
        self.assertEqual(totals["sent"], 159)


if __name__ == "__main__":
    unittest.main(verbosity=2)
