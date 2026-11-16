"""Treg: the token, the two calls Lead Studio makes, and the cost it reports."""
import base64
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error

if not os.environ.get("LS_DATA"):
    os.environ["LS_DATA"] = tempfile.mkdtemp(prefix="ls_treg_")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine import config, treg  # noqa: E402


class FakeResponse:
    def __init__(self, payload, cost=0):
        self.payload = json.dumps(payload).encode()
        self.headers = {"X-Treg-Cost-Micro": str(cost)}

    def read(self, _limit=None):
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TokenTests(unittest.TestCase):
    def setUp(self):
        self.saved = config.load_settings()

    def tearDown(self):
        config.save_settings(self.saved or {})

    def set_connectors(self, **values):
        settings = dict(config.load_settings() or {})
        connectors = dict(settings.get("connectors") or {})
        connectors.update(values)
        settings["connectors"] = connectors
        config.save_settings(settings)

    def test_no_token_is_reported_not_raised(self):
        self.set_connectors(treg_key="")
        self.assertFalse(treg.status()["has_token"])
        with self.assertRaises(ValueError):
            treg.verify_email("a@b.com")

    def test_the_team_token_is_decoded_for_the_status_line(self):
        payload = base64.urlsafe_b64encode(
            b'{"uid":1,"org":"test-team","scp":"team"}').decode().rstrip("=")
        self.set_connectors(treg_key=payload + ".signature")
        status = treg.status()
        self.assertTrue(status["has_token"])
        self.assertEqual(status["org"], "test-team")
        self.assertEqual(status["scope"], "team")


class CallTests(unittest.TestCase):
    def setUp(self):
        self.saved = config.load_settings()
        settings = dict(config.load_settings() or {})
        connectors = dict(settings.get("connectors") or {})
        connectors["treg_key"] = "test-token"
        settings["connectors"] = connectors
        config.save_settings(settings)
        self.saved_open = treg.urllib.request.urlopen
        self.calls = []

    def tearDown(self):
        treg.urllib.request.urlopen = self.saved_open
        config.save_settings(self.saved or {})

    def fake(self, payload, cost=0):
        def opener(request, timeout=None):
            self.calls.append({"url": request.full_url, "body": request.data})
            return FakeResponse(payload, cost)
        return opener

    def test_verification_uses_the_routed_tool_and_reports_the_charge(self):
        treg.urllib.request.urlopen = self.fake({
            "output": {"valid": True, "status": "valid"},
            "_treg": {"served_by": "contactout.people.email.verify", "charged_micro": 0}})
        answer = treg.verify_email("Someone@Example.com")
        self.assertTrue(answer["ok"])
        self.assertTrue(answer["valid"])
        self.assertTrue(answer["free"])
        self.assertEqual(answer["email"], "someone@example.com")
        self.assertIn("/call/treg.people.email.verify", self.calls[0]["url"])
        self.assertEqual(json.loads(self.calls[0]["body"].decode()),
                         {"email": "someone@example.com"})

    def test_maps_returns_places_and_the_charge(self):
        treg.urllib.request.urlopen = self.fake({
            "output": {"places": [
                {"name": "Urban House", "address": "Laurinska 14", "phone": "+421 900 000 000",
                 "rating": 4.7, "review_count": 14279, "place_id": "abc",
                 "website": "u.sk", "category": "Cafe"},
                {"name": "Second", "address": "Kosicka 21"}]},
            "_treg": {"charged_micro": 230}})
        answer = treg.maps_search("coffee Bratislava", limit=5)
        self.assertTrue(answer["ok"])
        self.assertEqual(answer["count"], 2)
        self.assertEqual(answer["charged_micro"], 230)
        self.assertFalse(answer["free"])
        self.assertEqual(answer["places"][0]["rating"], 4.7)

    def test_a_bad_request_is_reported_with_the_detail(self):
        def raiser(request, timeout=None):
            raise urllib.error.HTTPError(request.full_url, 422, "Unprocessable", {},
                                         io.BytesIO(b'{"detail":"bad body"}'))
        treg.urllib.request.urlopen = raiser
        answer = treg.call("treg.people.email.verify", {"email": "x"})
        self.assertFalse(answer["ok"])
        self.assertIn("bad body", answer["error"])

    def test_the_catalog_search_returns_ids(self):
        treg.urllib.request.urlopen = self.fake({"query": "email", "total": 129, "results": [
            {"id": "treg.people.email.verify", "provider": "treg", "name": "Verify email"}]})
        answer = treg.catalog_search("email", limit=5)
        self.assertTrue(answer["ok"])
        self.assertEqual(answer["total"], 129)
        self.assertEqual(answer["results"][0]["id"], "treg.people.email.verify")


if __name__ == "__main__":
    unittest.main(verbosity=2)
