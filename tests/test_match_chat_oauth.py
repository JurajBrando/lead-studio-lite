"""Tests for business matching, the dashboard chat agent and OAuth plumbing."""
import json
import os
import tempfile
import unittest
from unittest import mock

TMP = tempfile.mkdtemp(prefix="ls_match_")
os.environ["LS_DATA"] = TMP
os.environ["LS_PORT"] = "8793"

import sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine import chat, config, db, llm, match, oauth  # noqa: E402


class MatchTests(unittest.TestCase):
    def setUp(self):
        db.execute("DELETE FROM leads")
        db.execute("DELETE FROM llm_providers")
        settings = config.load_settings()
        settings["connectors"] = {"ai_key": "", "ai_model": "", "ai_base": ""}
        config.save_settings(settings)
        db.insert("leads", {"email": "a@clinic-test.com", "industry": "dental",
                            "company": "Bright Dental", "title": "Practice Manager",
                            "created_ts": db.now(), "updated_ts": db.now()})
        db.insert("leads", {"email": "b@truck-test.com", "industry": "logistics",
                            "company": "Northline", "created_ts": db.now(), "updated_ts": db.now()})

    def test_keywords_drop_stopwords(self):
        words = match.keywords_from_text("We provide bookkeeping for the clinics and the dental offices")
        self.assertIn("bookkeeping", words)
        self.assertNotIn("the", words)

    def test_match_finds_the_right_lead(self):
        result = match.match(text="dental clinic marketing", use_ai=False)
        self.assertTrue(result["ok"])
        self.assertEqual(result["matches"][0]["lead"]["email"], "a@clinic-test.com")
        self.assertIn("dental", result["matches"][0]["matched"])

    def test_match_requires_input(self):
        self.assertFalse(match.match(text="", url="", use_ai=False)["ok"])

    def test_ai_profile_is_used_when_available(self):
        llm.save({"name": "T", "base_url": "https://ai.test/v1", "model": "m", "api_key": "k"})
        answer = json.dumps({"industry": "dental software", "segment": "clinics",
                             "keywords": ["dental", "clinic"], "summary": "s"})
        with mock.patch.object(llm, "chat", return_value=answer):
            profile = match.build_profile(text="we sell to dentists", use_ai=True)
        self.assertTrue(profile["used_ai"])
        self.assertIn("dental", profile["keywords"])

    def test_ai_failure_falls_back_to_keywords(self):
        llm.save({"name": "T", "base_url": "https://ai.test/v1", "model": "m", "api_key": "k"})
        with mock.patch.object(llm, "chat", side_effect=llm.LLMError("down")):
            profile = match.build_profile(text="we sell dental software", use_ai=True)
        self.assertFalse(profile["used_ai"])
        self.assertTrue(profile["keywords"])


class ChatTests(unittest.TestCase):
    def setUp(self):
        db.execute("DELETE FROM llm_providers")
        settings = config.load_settings()
        settings["connectors"] = {"ai_key": "", "ai_model": "", "ai_base": ""}
        config.save_settings(settings)

    def test_no_provider_is_reported(self):
        result = chat.run("hello")
        self.assertFalse(result["ok"])
        self.assertIn("No LLM provider", result["error"])

    def test_plain_answer(self):
        llm.save({"name": "T", "base_url": "https://ai.test/v1", "model": "m", "api_key": "k"})
        with mock.patch.object(llm, "chat", return_value='{"answer": "You have 2 leads."}'):
            result = chat.run("how many leads?")
        self.assertTrue(result["ok"])
        self.assertEqual(result["answer"], "You have 2 leads.")

    def test_non_json_reply_is_still_an_answer(self):
        llm.save({"name": "T", "base_url": "https://ai.test/v1", "model": "m", "api_key": "k"})
        with mock.patch.object(llm, "chat", return_value="Just some prose."):
            result = chat.run("hi")
        self.assertEqual(result["answer"], "Just some prose.")

    def test_tool_call_is_executed_then_answered(self):
        llm.save({"name": "T", "base_url": "https://ai.test/v1", "model": "m", "api_key": "k"})
        replies = [json.dumps({"tool": "stats", "arguments": {}}),
                   json.dumps({"answer": "Done."})]
        with mock.patch.object(llm, "chat", side_effect=replies), \
                mock.patch("engine.mcp._run_tool", return_value={"ok": True, "stats": {}}) as runner:
            result = chat.run("look at stats")
        self.assertEqual(result["answer"], "Done.")
        self.assertEqual(result["steps"][0]["tool"], "stats")
        runner.assert_called_once()

    def test_unknown_tool_does_not_crash(self):
        llm.save({"name": "T", "base_url": "https://ai.test/v1", "model": "m", "api_key": "k"})
        replies = [json.dumps({"tool": "rm_rf", "arguments": {}}),
                   json.dumps({"answer": "Sorry."})]
        with mock.patch.object(llm, "chat", side_effect=replies):
            result = chat.run("delete everything")
        self.assertTrue(result["ok"])
        self.assertEqual(result["answer"], "Sorry.")


class OAuthTests(unittest.TestCase):
    def setUp(self):
        settings = config.load_settings()
        settings["oauth"] = {}
        config.save_settings(settings)

    def test_missing_client_is_reported(self):
        with self.assertRaises(oauth.OAuthError):
            oauth.auth_url("google")

    def test_auth_url_contains_the_expected_parameters(self):
        settings = config.load_settings()
        settings["oauth"] = {"google_client_id": "cid", "google_client_secret": "sec"}
        config.save_settings(settings)
        url, state = oauth.auth_url("google", port=8793)
        self.assertIn("client_id=cid", url)
        self.assertIn("response_type=code", url)
        self.assertIn("127.0.0.1%3A8793%2Foauth%2Fcallback", url)
        self.assertIn("access_type=offline", url)
        self.assertTrue(state)

    def test_microsoft_scope_has_imap_and_smtp(self):
        settings = config.load_settings()
        settings["oauth"] = {"microsoft_client_id": "cid"}
        config.save_settings(settings)
        url, _ = oauth.auth_url("microsoft")
        self.assertIn("IMAP.AccessAsUser.All", url.replace("%2F", "/").replace("%3A", ":"))
        self.assertIn("SMTP.Send", url.replace("%2F", "/").replace("%3A", ":"))

    def test_xoauth2_string_shape(self):
        value = oauth.xoauth2("me@example.com", "TOKEN")
        self.assertEqual(value, "user=me@example.com\x01auth=Bearer TOKEN\x01\x01")
        self.assertTrue(oauth.xoauth2_b64("me@example.com", "TOKEN"))

    def test_token_normalisation_sets_an_expiry(self):
        out = oauth._normalise({"access_token": "a", "refresh_token": "r", "expires_in": 3600})
        self.assertEqual(out["access_token"], "a")
        self.assertGreater(out["expires_at"], 0)

    def test_status_exposes_the_redirect_uri(self):
        state = oauth.status()
        self.assertIn("redirect_uri", state)
        self.assertIn("google", state)


if __name__ == "__main__":
    unittest.main(verbosity=2)
