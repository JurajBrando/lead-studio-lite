"""The Manyreach REST API path: key validation, pagination, and the stored snapshot."""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

if not os.environ.get("LS_DATA"):
    os.environ["LS_DATA"] = tempfile.mkdtemp(prefix="ls_manyreach_api_")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine import config, db, manyreach_api  # noqa: E402


def wipe():
    settings = config.load_settings()
    settings.pop("manyreach", None)
    config.save_settings(settings)


class KeyTests(unittest.TestCase):
    def setUp(self):
        db.init_db()
        wipe()

    def tearDown(self):
        wipe()

    def test_a_working_key_is_stored_with_the_account(self):
        with mock.patch.object(manyreach_api, "_request",
                               return_value={"ok": True, "data": {"id": 29309,
                                                                 "title": "Acme", "keyType": "organization"}}):
            answer = manyreach_api.set_key("abc-123")
        self.assertTrue(answer["ok"], answer)
        self.assertEqual(answer["account"]["title"], "Acme")
        self.assertTrue(manyreach_api.status()["has_key"])

    def test_a_refused_key_is_not_kept(self):
        with mock.patch.object(manyreach_api, "_request",
                               return_value={"ok": False, "error": "Manyreach rejected that API key."}):
            answer = manyreach_api.set_key("bad")
        self.assertFalse(answer["ok"])
        self.assertFalse(manyreach_api.status()["has_key"])

    def test_no_key_means_no_request(self):
        self.assertIn("API key", manyreach_api.account()["error"])


class PagingTests(unittest.TestCase):
    def setUp(self):
        wipe()
        manyreach_api._save({"api_key": "k"})

    def tearDown(self):
        wipe()

    def test_it_stops_on_the_reported_total(self):
        pages = {
            1: {"items": [{"senderId": 1}, {"senderId": 2}], "pagination": {"totalItems": 3}},
            2: {"items": [{"senderId": 3}], "pagination": {"totalItems": 3}},
        }

        def fake(path, params):
            return {"ok": True, "data": pages[params["page"]]}

        with mock.patch.object(manyreach_api, "_request", side_effect=fake):
            answer, rows = manyreach_api.senders()
        self.assertEqual(len(rows), 3)
        self.assertEqual(answer["total"], 3)

    def test_an_empty_page_ends_it(self):
        def fake(path, params):
            if params["page"] == 1:
                return {"ok": True, "data": {"items": [{"senderId": 1}]}}
            return {"ok": True, "data": {"items": [], "pagination": {"totalItems": 99}}}

        with mock.patch.object(manyreach_api, "_request", side_effect=fake):
            _, rows = manyreach_api.senders()
        self.assertEqual(len(rows), 1)

    def test_a_failed_page_is_reported(self):
        with mock.patch.object(manyreach_api, "_request",
                               return_value={"ok": False, "error": "HTTP 429"}):
            answer, rows = manyreach_api.senders()
        self.assertFalse(answer["ok"])
        self.assertEqual(rows, [])


class SyncTests(unittest.TestCase):
    def setUp(self):
        wipe()
        manyreach_api._save({"api_key": "k"})
        db.init_db()
        db.execute("DELETE FROM mcp_items WHERE connection='manyreach'")

    def tearDown(self):
        db.execute("DELETE FROM mcp_items WHERE connection='manyreach'")
        wipe()

    def test_sync_stores_campaigns_and_senders(self):
        def fake(path, params):
            if path == "/campaigns":
                return {"ok": True, "data": {"items": [
                    {"campaignId": 1, "name": "One", "status": "Running"}],
                    "pagination": {"totalItems": 1}}}
            return {"ok": True, "data": {"items": [
                {"senderId": 2, "email": "s@x.com", "warmup": True}],
                "pagination": {"totalItems": 1}}}

        with mock.patch.object(manyreach_api, "_request", side_effect=fake):
            answer = manyreach_api.sync()
        self.assertTrue(answer["ok"], answer)
        self.assertEqual(answer["counts"], {"campaigns": 1, "senders": 1})
        stored = db.rows("SELECT kind, name, status FROM mcp_items WHERE connection='manyreach' "
                         "ORDER BY kind")
        self.assertEqual([row["name"] for row in stored], ["One", "s@x.com"])
        self.assertEqual(stored[1]["status"], "warmup")

    def test_sync_keeps_the_old_snapshot_when_the_api_fails(self):
        db.insert("mcp_items", {"connection": "manyreach", "kind": "campaigns", "item_id": "9",
                                "name": "Old", "status": "Draft", "payload": "{}",
                                "updated_ts": db.now()})
        with mock.patch.object(manyreach_api, "_request",
                               return_value={"ok": False, "error": "HTTP 401"}):
            answer = manyreach_api.sync()
        self.assertFalse(answer["ok"])
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM mcp_items WHERE connection='manyreach'"), 1)
        self.assertEqual(manyreach_api.status()["counts"]["campaigns"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
