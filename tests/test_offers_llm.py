"""Tests for offers, pitch rendering and managed LLM providers. Offline only."""
import json
import os
import tempfile
import unittest
from unittest import mock

TMP = tempfile.mkdtemp(prefix="ls_offers_")
os.environ["LS_DATA"] = TMP
os.environ["LS_PORT"] = "8794"

import sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine import config, db, llm, offers  # noqa: E402


def lead(**fields):
    base = {"email": "jana@acme-test.com", "first_name": "Jana", "company": "Acme Accounting",
            "industry": "accounting", "title": "Partner", "tags": "bookkeeping",
            "notes": "payroll runs on spreadsheets"}
    base.update(fields)
    return base


class OfferStoreTests(unittest.TestCase):
    def setUp(self):
        db.execute("DELETE FROM offers")
        db.execute("DELETE FROM offer_connections")

    def test_save_requires_a_name(self):
        self.assertFalse(offers.save({"name": ""})["ok"])

    def test_save_and_read_back(self):
        res = offers.save({"name": "Automated accounting", "category": "accounting",
                           "affiliate_url": "partner.example.com/?ref=me",
                           "keywords": "accounting, bookkeeping", "commission": "20%"})
        self.assertTrue(res["ok"])
        row = offers.get(res["id"])
        self.assertEqual(row["affiliate_url"], "https://partner.example.com/?ref=me")
        self.assertEqual(offers.keywords(row), ["accounting", "bookkeeping"])

    def test_invalid_link_is_dropped(self):
        res = offers.save({"name": "x", "affiliate_url": "not a url at all"})
        self.assertEqual(offers.get(res["id"])["affiliate_url"], "")

    def test_connections_mask_secrets(self):
        offer_id = offers.save({"name": "Ads tracking"})["id"]
        offers.add_connection({"offer_id": offer_id, "kind": "api_key", "label": "Portal",
                               "key": "sk-verysecret123"})
        rows = offers.connections(offer_id)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["has_secret"])
        self.assertNotIn("verysecret", json.dumps(rows[0]["config_masked"]))
        self.assertTrue(rows[0]["config_masked"]["key"].startswith("saved:"))


class MatchingTests(unittest.TestCase):
    def setUp(self):
        db.execute("DELETE FROM offers")
        db.execute("DELETE FROM leads")
        self.offer = offers.get(offers.save({
            "name": "Automated accounting", "category": "accounting",
            "keywords": "accounting, bookkeeping, payroll"})["id"])
        db.insert("leads", {"email": "a@acct-test.com", "industry": "accounting",
                            "company": "Acct Co", "created_ts": db.now(), "updated_ts": db.now()})
        db.insert("leads", {"email": "b@shop-test.com", "industry": "retail",
                            "company": "Shop Co", "created_ts": db.now(), "updated_ts": db.now()})

    def test_score_prefers_the_right_industry(self):
        good = db.one("SELECT * FROM leads WHERE email='a@acct-test.com'")
        bad = db.one("SELECT * FROM leads WHERE email='b@shop-test.com'")
        self.assertGreaterEqual(offers.score_lead(self.offer, good), 4)
        self.assertEqual(offers.score_lead(self.offer, bad), 0)

    def test_matches_returns_only_fitting_leads(self):
        rows = offers.matches(self.offer)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["lead"]["email"], "a@acct-test.com")

    def test_match_counts(self):
        counts = offers.match_counts()
        self.assertEqual(counts[self.offer["id"]], 1)


class PitchTests(unittest.TestCase):
    def setUp(self):
        db.execute("DELETE FROM offers")
        db.execute("DELETE FROM llm_providers")
        settings = config.load_settings()
        settings["sender_name"] = "Alex"
        settings["company_name"] = "Acme Ltd"
        config.save_settings(settings)
        self.offer = offers.get(offers.save({
            "name": "Automated accounting",
            "description": "monthly bookkeeping for small firms",
            "affiliate_url": "https://partner.example.com/?ref=me",
            "pitch_subject": "{{first_name}} - books on autopilot?",
            "pitch_body": "Hi {{first_name|there}},\n\n{{offer_description}}\n\nAlex"})["id"])

    def test_template_is_merged_and_link_appended(self):
        out = offers.render_pitch(self.offer, lead())
        self.assertEqual(out["subject"], "Jana - books on autopilot?")
        self.assertIn("monthly bookkeeping", out["body"])
        self.assertIn("https://partner.example.com/?ref=me", out["body"])
        self.assertFalse(out["used_ai"])
        self.assertEqual(out["ai_error"], "")

    def test_ai_falls_back_to_template_without_provider(self):
        out = offers.render_pitch(self.offer, lead(), use_ai=True)
        self.assertFalse(out["used_ai"])
        self.assertIn("No LLM provider", out["ai_error"])
        self.assertIn("Jana", out["subject"])

    def test_ai_pitch_is_parsed_when_a_provider_answers(self):
        llm.save({"name": "Test", "base_url": "https://ai.test/v1", "model": "m", "api_key": "k"})
        answer = json.dumps({"choices": [{"message": {"content":
                                 '{"subject": "Quick idea", "body": "Hi Jana, worth a look?"}'}}]})
        with mock.patch.object(llm.net, "fetch", return_value=answer.encode()):
            out = offers.render_pitch(self.offer, lead(), use_ai=True)
        self.assertTrue(out["used_ai"])
        self.assertEqual(out["subject"], "Quick idea")
        self.assertIn("https://partner.example.com/?ref=me", out["body"])


class ProviderTests(unittest.TestCase):
    def setUp(self):
        db.execute("DELETE FROM llm_providers")
        settings = config.load_settings()
        settings["connectors"] = {"hunter_key": "", "ai_base": "", "ai_key": "",
                                  "ai_model": "", "serper_key": ""}
        config.save_settings(settings)

    def test_save_masks_the_key(self):
        res = llm.save({"name": "DeepSeek", "kind": "openai", "base_url": "https://api.deepseek.com/v1",
                        "model": "deepseek-chat", "api_key": "sk-secret"})
        self.assertTrue(res["ok"])
        rows = llm.list_providers()
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["has_key"])
        self.assertNotIn("api_key", rows[0])
        self.assertEqual(rows[0]["is_default"], 1)      # first one becomes the default

    def test_missing_fields_are_rejected(self):
        self.assertFalse(llm.save({"name": "x", "base_url": "", "model": ""})["ok"])

    def test_default_switch(self):
        first = llm.save({"name": "A", "base_url": "https://a.test/v1", "model": "a"})["id"]
        second = llm.save({"name": "B", "base_url": "https://b.test/v1", "model": "b"})["id"]
        llm.set_default(second)
        rows = {r["id"]: r for r in llm.list_providers()}
        self.assertEqual(rows[second]["is_default"], 1)
        self.assertEqual(rows[first]["is_default"], 0)

    def test_openai_call(self):
        llm.save({"name": "OpenAI", "base_url": "https://api.openai.com/v1", "model": "m",
                  "api_key": "k"})
        payload = json.dumps({"choices": [{"message": {"content": "hello"}}]})
        with mock.patch.object(llm.net, "fetch", return_value=payload.encode()) as fetch:
            self.assertEqual(llm.chat([{"role": "user", "content": "hi"}]), "hello")
        args, kwargs = fetch.call_args
        self.assertTrue(args[0].endswith("/chat/completions"))
        body = json.loads(kwargs["data"].decode())
        self.assertEqual(body["model"], "m")

    def test_anthropic_call_uses_messages_api(self):
        llm.save({"name": "Claude", "kind": "anthropic", "base_url": "https://api.anthropic.com/v1",
                  "model": "claude-3-5-sonnet-latest", "api_key": "k"})
        payload = json.dumps({"content": [{"type": "text", "text": "ready"}]})
        with mock.patch.object(llm.net, "fetch", return_value=payload.encode()) as fetch:
            self.assertEqual(llm.chat([{"role": "user", "content": "hi"}]), "ready")
        args, kwargs = fetch.call_args
        self.assertTrue(args[0].endswith("/messages"))
        self.assertEqual(kwargs["headers"]["x-api-key"], "k")
        self.assertEqual(kwargs["headers"]["anthropic-version"], "2023-06-01")

    def test_test_endpoint_reports_success(self):
        provider_id = llm.save({"name": "Ollama", "base_url": "http://127.0.0.1:11434/v1",
                                "model": "llama3.1"})["id"]
        payload = json.dumps({"choices": [{"message": {"content": "ready"}}]})
        with mock.patch.object(llm.net, "fetch", return_value=payload.encode()):
            out = llm.test(provider_id)
        self.assertTrue(out["ok"])
        self.assertEqual(llm.list_providers()[0]["last_test_ok"], 1)

    def test_legacy_settings_endpoint_is_used_as_fallback(self):
        settings = config.load_settings()
        settings["connectors"] = {"ai_base": "https://legacy.test/v1", "ai_key": "k",
                                  "ai_model": "legacy-model"}
        config.save_settings(settings)
        provider = llm.get()
        self.assertEqual(provider["model"], "legacy-model")
        self.assertTrue(llm.status()["configured"])


class DisclosureTests(unittest.TestCase):
    def setUp(self):
        db.execute("DELETE FROM offers")
        settings = config.load_settings()
        settings["company_name"] = "Acme Ltd"
        settings["sender_name"] = "Alex"
        config.save_settings(settings)

    def test_default_wording_uses_the_legal_terms(self):
        text = offers.disclosure_for({"name": "Automated accounting"})
        for phrase in ("material connection", "commission or other compensation",
                       "at no additional cost to you", "does not increase the price",
                       "16 C.F.R. Part 255", "Unfair Commercial Practices Directive"):
            self.assertIn(phrase, text)
        self.assertIn("Acme Ltd", text)
        self.assertIn("Automated accounting", text)

    def test_custom_wording_fills_placeholders(self):
        text = offers.disclosure_for({"name": "Ads tracking",
                                      "disclosure": "{company} earns from {partner}."})
        self.assertEqual(text, "Acme Ltd earns from Ads tracking.")

    def test_pitch_ends_with_the_disclosure(self):
        offer = offers.get(offers.save({
            "name": "Automated accounting",
            "affiliate_url": "https://partner.example.com/?ref=me"})["id"])
        out = offers.render_pitch(offer, lead())
        self.assertIn("Affiliate disclosure", out["body"])
        self.assertIn(out["disclosure"], out["body"])
        self.assertLess(out["body"].index("partner.example.com"), out["body"].index("Affiliate disclosure"))

    def test_offer_page_has_disclosure_at_the_bottom(self):
        offer = offers.get(offers.save({
            "name": "Social media management", "description": "Posts and scheduling.",
            "affiliate_url": "https://social.example.com/?ref=me"})["id"])
        page = offers.build_page_html(offer)
        self.assertIn("Social media management", page)
        self.assertIn('rel="sponsored', page)
        self.assertIn("Affiliate disclosure", page)
        self.assertGreater(page.index("Affiliate disclosure"), page.index("Get started"))
        self.assertIn("at no additional cost to you", page)

    def test_migration_added_the_column(self):
        cols = {row[1] for row in db.conn().execute("PRAGMA table_info(offers)").fetchall()}
        self.assertIn("disclosure", cols)


class PackTests(unittest.TestCase):
    def setUp(self):
        db.execute("DELETE FROM offers")

    def test_pack_is_bundled_and_well_formed(self):
        rows = offers.load_pack()
        self.assertGreaterEqual(len(rows), 25)
        for row in rows:
            self.assertTrue(row.get("name"), row)
            self.assertTrue(str(row.get("affiliate_url", "")).startswith("http"), row.get("name"))
            self.assertTrue(row.get("keywords"), row.get("name"))
            self.assertTrue(row.get("category"), row.get("name"))

    def test_import_is_idempotent(self):
        rows = offers.load_pack()
        first = offers.import_pack()
        self.assertEqual(first["added"], len(rows))
        second = offers.import_pack()
        self.assertEqual(second["added"], 0)
        self.assertEqual(second["skipped"], len(rows))
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM offers"), len(rows))

    def test_two_offers_may_share_one_link(self):
        result = offers.import_rows([
            {"name": "HYROS", "affiliate_url": "https://x.test/?fpr=1"},
            {"name": "HYROS AIR", "affiliate_url": "https://x.test/?fpr=1"},
        ])
        self.assertEqual(result["added"], 2)

    def test_rows_without_a_name_are_skipped(self):
        self.assertEqual(offers.import_rows([{"affiliate_url": "https://a.test"}])["skipped"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
