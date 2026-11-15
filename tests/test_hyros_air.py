"""HYROS AIR is a paid Hyros service: the app only reads it through the HYROS API key."""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

if not os.environ.get("LS_DATA"):
    os.environ["LS_DATA"] = tempfile.mkdtemp(prefix="ls_hyros_air_")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine import ads, config, db, hyros_air  # noqa: E402


def wipe():
    settings = config.load_settings()
    settings.pop("ads", None)
    settings.pop("hyros_air", None)
    config.save_settings(settings)


class StatusTests(unittest.TestCase):
    def setUp(self):
        db.init_db()
        wipe()

    def tearDown(self):
        wipe()

    def test_it_points_at_the_hyros_api_not_a_local_model(self):
        status = hyros_air.status()
        self.assertEqual(status["base_url"], "https://api.hyros.com/v1")
        self.assertIn("paid", status["note"])
        self.assertEqual(sorted(status["paths"]),
                         ["ad-accounts", "calls", "leads", "sales"])
        self.assertIn("api-docs.hyros.com", status["links"]["api"])
        self.assertIn("mcp.hyros.com", status["links"]["mcp"])

    def test_without_a_key_it_says_where_to_put_one(self):
        answer = hyros_air.pull("leads")
        self.assertFalse(answer["ok"])
        self.assertIn("Ad Tracking", answer["error"])

    def test_an_unknown_collection_is_refused(self):
        self.assertFalse(hyros_air.pull("campaigns")["ok"])


class PullTests(unittest.TestCase):
    def setUp(self):
        db.init_db()
        wipe()
        ads.save("hyros", {"api_key": "hk-1", "base_url": "https://api.hyros.com/v1"})

    def tearDown(self):
        wipe()

    def test_it_reads_the_leads_path_with_the_stored_key(self):
        seen = {}

        def fake(name, path=""):
            seen["name"] = name
            seen["path"] = path
            return {"ok": True, "status": 200, "url": "https://api.hyros.com/v1" + path,
                    "data": {"data": [{"email": "a@x.com"}, {"email": "b@x.com"}]}}

        with mock.patch.object(hyros_air.ads, "pull", side_effect=fake):
            answer = hyros_air.pull("leads")
        self.assertTrue(answer["ok"], answer)
        self.assertEqual(answer["count"], 2)
        self.assertTrue(seen["path"].startswith("/api/v1.0/leads"), seen["path"])
        self.assertIn("limit=100", seen["path"])

    def test_a_bare_list_is_understood(self):
        with mock.patch.object(hyros_air.ads, "pull",
                               return_value={"ok": True, "status": 200,
                                             "data": [{"id": 1}, {"id": 2}, {"id": 3}]}):
            answer = hyros_air.pull("sales")
        self.assertEqual(answer["count"], 3)

    def test_an_api_error_is_passed_through(self):
        with mock.patch.object(hyros_air.ads, "pull",
                               return_value={"ok": False, "error": "HYROS answered HTTP 401: bad key"}):
            answer = hyros_air.pull("calls")
        self.assertFalse(answer["ok"])
        self.assertIn("401", answer["error"])

    def test_paths_can_be_corrected(self):
        hyros_air.save_paths({"leads": "/api/v1.0/customers"})
        self.assertEqual(hyros_air.status()["paths"]["leads"], "/api/v1.0/customers")
        self.assertEqual(hyros_air.status()["paths"]["sales"], "/api/v1.0/sales")

    def test_a_path_that_is_not_one_is_ignored(self):
        hyros_air.save_paths({"leads": "no-slash", "nonsense": "/x"})
        self.assertEqual(hyros_air.status()["paths"]["leads"], "/api/v1.0/leads")


if __name__ == "__main__":
    unittest.main(verbosity=2)
