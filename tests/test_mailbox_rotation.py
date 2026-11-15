"""Sending spreads over every active mailbox instead of burning the first one."""
import os
import sys
import tempfile
import time
import unittest

if not os.environ.get("LS_DATA"):
    os.environ["LS_DATA"] = tempfile.mkdtemp(prefix="ls_rotation_")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine import db, pipeline  # noqa: E402

TAG = "%rotation-test.com"


def day_start():
    return int(time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1)))


class RotationTests(unittest.TestCase):
    def setUp(self):
        db.init_db()
        self.clean()
        self.a = db.insert("mailboxes", {"name": "A", "from_email": "a@rotation-test.com",
                                         "smtp_host": "smtp.test", "imap_host": "imap.test",
                                         "daily_limit": 2, "last_send_ts": 0, "active": 1})
        self.b = db.insert("mailboxes", {"name": "B", "from_email": "b@rotation-test.com",
                                         "smtp_host": "smtp.test", "imap_host": "imap.test",
                                         "daily_limit": 2, "last_send_ts": 0, "active": 1})

    def tearDown(self):
        self.clean()

    def clean(self):
        db.execute("DELETE FROM sends WHERE to_email LIKE ?", (TAG,))
        db.execute("DELETE FROM mailboxes WHERE from_email LIKE ?", (TAG,))

    def sent(self, mailbox_id, count):
        for index in range(count):
            db.insert("sends", {"mailbox_id": mailbox_id, "lead_id": 0,
                                "to_email": "x" + str(index) + TAG,
                                "status": "sent", "sent_ts": day_start() + 60 + index})

    def test_the_quietest_mailbox_goes_first(self):
        db.update("mailboxes", self.a, {"last_send_ts": int(time.time()) - 10})
        order = pipeline.mailbox_candidates()
        self.assertEqual([box["name"] for box in order[:2]], ["B", "A"])

    def test_a_mailbox_at_its_cap_is_skipped(self):
        self.sent(self.a, 2)
        order = pipeline.mailbox_candidates()
        self.assertEqual(order[0]["id"], self.b)
        self.assertNotIn(self.a, [box["id"] for box in order])

    def test_mailbox_for_picks_the_one_with_room(self):
        self.sent(self.a, 2)
        self.assertEqual(pipeline.mailbox_for(0)["id"], self.b)

    def test_when_every_mailbox_is_capped_the_list_still_has_them(self):
        self.sent(self.a, 2)
        self.sent(self.b, 2)
        self.assertEqual(len(pipeline.mailbox_candidates()), 2)
        # run_tick then reports "every mailbox reached its daily limit" instead of sending.
        self.assertEqual(pipeline.sent_today(self.a), 2)

    def test_an_explicit_mailbox_is_never_swapped(self):
        self.sent(self.a, 2)
        self.assertEqual(pipeline.mailbox_for(self.a)["id"], self.a)


if __name__ == "__main__":
    unittest.main(verbosity=2)
