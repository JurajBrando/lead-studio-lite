"""The Ads hub: per-platform credentials, a test call and a raw pull."""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

if not os.environ.get("LS_DATA"):
    os.environ["LS_DATA"] = tempfile.mkdtemp(prefix="ls_ads_")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine import ads, config, db  # noqa: E402


class FakeResponse:
    def __init__(self, body, status=200):
        self._body = body.encode("utf-8")
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def wipe():
    settings = config.load_settings()
    settings.pop("ads", None)
    config.save_settings(settings)


class PlatformTests(unittest.TestCase):
    def setUp(self):
        db.init_db()
        wipe()

    def tearDown(self):
        wipe()

    def test_the_four_platforms_are_defined(self):
        names = [row["name"] for row in ads.status_all()]
        self.assertEqual(names, ["metricool", "hyros", "plai", "datafast"])

    def test_the_key_is_stored_but_never_returned(self):
        answer = ads.save("hyros", {"api_key": "secret-key", "base_url": "https://api.hyros.com/v1"})
        self.assertTrue(answer["ok"], answer)
        status = ads.status("hyros")
        self.assertTrue(status["has_key"])
        self.assertNotIn("api_key", status)
        self.assertTrue(status["base_url"].startswith("https://api.hyros.com"))

    def test_a_blank_key_keeps_the_stored_one(self):
        ads.save("hyros", {"api_key": "first"})
        ads.save("hyros", {"api_key": "", "base_url": "https://api.hyros.com/v1"})
        self.assertTrue(ads.status("hyros")["has_key"])

    def test_a_base_url_has_to_look_like_one(self):
        self.assertFalse(ads.save("plai", {"api_key": "x", "base_url": "api.plai.io"})["ok"])

    def test_an_unknown_platform_is_refused(self):
        with self.assertRaises(ValueError):
            ads.status("nope")

    def test_metricool_is_read_through_mcp_not_a_key(self):
        answer = ads.test("metricool")
        self.assertFalse(answer["ok"])
        self.assertIn("MCP", answer["error"])


class RequestTests(unittest.TestCase):
    def setUp(self):
        db.init_db()
        wipe()
        ads.save("hyros", {"api_key": "k-123", "base_url": "https://api.hyros.com/v1"})

    def tearDown(self):
        wipe()

    def test_the_api_key_header_is_used_for_hyros(self):
        seen = {}

        def fake(request, timeout=0):
            seen["url"] = request.full_url
            seen["headers"] = {k.lower(): v for k, v in request.header_items()}
            return FakeResponse(json.dumps({"ok": True}))

        with mock.patch.object(ads.urllib.request, "urlopen", side_effect=fake):
            answer = ads.pull("hyros", "/api/v1.0/leads")
        self.assertTrue(answer["ok"], answer)
        self.assertEqual(seen["url"], "https://api.hyros.com/v1/api/v1.0/leads")
        self.assertEqual(seen["headers"].get("api-key"), "k-123")

    def test_datafast_uses_a_bearer_token(self):
        seen = {}

        def fake(request, timeout=0):
            seen["headers"] = {k.lower(): v for k, v in request.header_items()}
            return FakeResponse("{}")

        ads.save("datafast", {"api_key": "df_123", "base_url": "https://datafa.st/api"})
        with mock.patch.object(ads.urllib.request, "urlopen", side_effect=fake):
            ads.pull("datafast", "/v1/websites")
        self.assertEqual(seen["headers"].get("authorization"), "Bearer df_123")

    def test_an_http_error_is_reported_with_its_status(self):
        import urllib.error
        error = urllib.error.HTTPError("https://x", 401, "no", {}, None)
        error.read = lambda: b'{"error":"bad key"}'
        with mock.patch.object(ads.urllib.request, "urlopen", side_effect=error):
            answer = ads.pull("hyros", "/leads")
        self.assertFalse(answer["ok"])
        self.assertIn("401", answer["error"])

    def test_a_platform_without_a_base_url_says_so(self):
        answer = ads.pull("plai")
        self.assertFalse(answer["ok"])
        self.assertIn("base URL", answer["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
