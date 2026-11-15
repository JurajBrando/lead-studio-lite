"""The inbox is one list across every mailbox, with a count beside each one."""
import os
import sys
import tempfile
import unittest

if not os.environ.get("LS_DATA"):
    os.environ["LS_DATA"] = tempfile.mkdtemp(prefix="ls_inbox_")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine import db, server  # noqa: E402


class UnifiedInboxTests(unittest.TestCase):
    def setUp(self):
        db.init_db()
        db.execute("DELETE FROM replies WHERE from_email LIKE '%inbox-test.com'")
        db.execute("DELETE FROM mailboxes WHERE from_email LIKE '%inbox-test.com'")
        self.one = db.insert("mailboxes", {"name": "Main", "from_email": "main@inbox-test.com",
                                           "imap_host": "imap.test", "smtp_host": "smtp.test",
                                           "created_ts": db.now()})
        self.two = db.insert("mailboxes", {"name": "Second", "from_email": "two@inbox-test.com",
                                           "imap_host": "imap.test", "smtp_host": "smtp.test",
                                           "created_ts": db.now()})
        now = db.now()
        db.insert("replies", {"mailbox_id": self.one, "from_email": "a@inbox-test.com",
                              "subject": "first", "received_ts": now, "seen": 0})
        db.insert("replies", {"mailbox_id": self.two, "from_email": "b@inbox-test.com",
                              "subject": "second", "received_ts": now + 1, "seen": 1})
        db.insert("replies", {"mailbox_id": self.two, "from_email": "c@inbox-test.com",
                              "subject": "third", "received_ts": now + 2, "seen": 0})

    def tearDown(self):
        db.execute("DELETE FROM replies WHERE from_email LIKE '%inbox-test.com'")
        db.execute("DELETE FROM mailboxes WHERE from_email LIKE '%inbox-test.com'")

    def rows(self, **query):
        answer = server.r_inbox(query)
        self.assertTrue(answer["ok"], answer)
        return [row for row in answer["replies"] if (row["from_email"] or "").endswith("inbox-test.com")]

    def test_every_mailbox_lands_in_the_one_list(self):
        self.assertEqual(len(self.rows(limit=300)), 3)

    def test_the_list_is_newest_first(self):
        subjects = [row["subject"] for row in self.rows(limit=300)]
        self.assertEqual(subjects, ["third", "second", "first"])

    def test_each_mailbox_has_its_own_count_and_unread(self):
        boxes = {box["name"]: box for box in server.r_inbox({"limit": 300})["mailboxes"]}
        self.assertEqual((boxes["Main"]["total"], boxes["Main"]["unread"]), (1, 1))
        self.assertEqual((boxes["Second"]["total"], boxes["Second"]["unread"]), (2, 1))

    def test_the_list_can_be_filtered_to_one_mailbox(self):
        rows = self.rows(limit=300, mailbox_id=self.two)
        self.assertEqual(sorted(row["subject"] for row in rows), ["second", "third"])

    def test_a_reply_carries_the_name_of_its_mailbox(self):
        rows = self.rows(limit=300, mailbox_id=self.two)
        self.assertEqual(rows[0]["mailbox_name"], "Second")

    def test_the_unread_count_is_account_wide(self):
        self.assertGreaterEqual(server.r_inbox({"limit": 300})["unread"], 2)


class MailboxPlanTests(unittest.TestCase):
    def test_the_inbox_uses_imap_mailboxes(self):
        from engine import pipeline
        db.init_db()
        self.assertTrue(hasattr(pipeline, "sync_inbox"))
        # sync_inbox(0) walks every active mailbox - that is what makes the inbox unified.
        source = open(os.path.join(ROOT, "engine", "pipeline.py"), encoding="utf-8").read()
        self.assertIn("boxes = [mailbox_for(box_id)] if box_id else active_mailboxes()", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)

