"""Tests for the contact crawler and the key-gated connectors. Offline only."""
import os
import tempfile
import unittest
from unittest import mock

TMP = tempfile.mkdtemp(prefix="ls_crawl_")
os.environ["LS_DATA"] = TMP
os.environ["LS_PORT"] = "8796"

import sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine import config, connectors, crawl  # noqa: E402

HOME = """<html><head><title>Acme Widgets | Home</title>
<meta name="description" content="We make widgets in Bratislava."></head><body>
<a href="/kontakt">Kontakt</a>
<a href="/about-us">About us</a>
<a href="https://facebook.com/acme">Facebook</a>
<a href="mailto:info@acme-test.com">Write to us</a>
<p>Call +421 900 123 456 or write to sales [at] acme-test [dot] com</p>
<span data-cfemail="%s"></span>
<a href="/brochure.pdf">Brochure</a>
</body></html>"""

CONTACT = """<html><head><title>Kontakt</title></head><body>
<p>Email: jana.novakova@acme-test.com</p>
</body></html>"""


def cfemail(address, key=0x42):
    raw = bytes([key] + [ord(c) ^ key for c in address])
    return raw.hex()


def fake_fetch(url, **_kwargs):
    return (CONTACT if "kontakt" in url else HOME).encode("utf-8")


class EmailParsingTests(unittest.TestCase):
    def test_obfuscated_and_plain(self):
        found = crawl.clean_emails("write to sales [at] acme-test [dot] com or info@acme-test.com")
        self.assertIn("sales@acme-test.com", found)
        self.assertIn("info@acme-test.com", found)

    def test_junk_is_dropped(self):
        found = crawl.clean_emails("noreply@acme-test.com logo@2x.png postmaster@acme-test.com")
        self.assertEqual(found, [])

    def test_cloudflare_decoding(self):
        address = "info@acme-test.com"
        self.assertEqual(crawl.decode_cfemail(cfemail(address)), address)
        self.assertEqual(crawl.decode_cfemail("zzzz"), "")

    def test_rank_prefers_company_domain(self):
        ranked = crawl.rank_emails(
            ["someone@gmail.com", "noreply@acme-test.com", "info@acme-test.com",
             "jana.novakova@acme-test.com"], "acme-test.com")
        self.assertEqual(ranked[0], "info@acme-test.com")
        self.assertEqual(ranked[-1], "someone@gmail.com")

    def test_guess_patterns(self):
        patterns = crawl.guess_patterns("acme-test.com", "Jana", "Nováková")
        self.assertIn("info@acme-test.com", patterns)
        self.assertIn("jana@acme-test.com", patterns)


class LinkTests(unittest.TestCase):
    def test_contact_links_are_same_site_and_scored(self):
        links = crawl.contact_links("https://acme-test.com/", HOME, limit=5)
        self.assertTrue(links)
        self.assertTrue(links[0].endswith("/kontakt"))
        self.assertIn("https://acme-test.com/about-us", links)
        self.assertFalse(any("facebook.com" in link for link in links))
        self.assertFalse(any(link.endswith(".pdf") for link in links))


class CrawlTests(unittest.TestCase):
    def test_crawl_reads_home_and_contact_page(self):
        with mock.patch.object(crawl.net, "fetch", side_effect=fake_fetch), \
                mock.patch.object(crawl.net, "text", return_value="User-agent: *\nAllow: /\n"):
            result = crawl.crawl_domain("acme-test.com", max_pages=3, pause=0)
        self.assertTrue(result["ok"])
        self.assertEqual(result["title"], "Acme Widgets | Home")
        self.assertIn("info@acme-test.com", result["emails"])
        self.assertIn("jana.novakova@acme-test.com", result["emails"])
        self.assertIn("sales@acme-test.com", result["emails"])
        self.assertTrue(any(link.endswith("/kontakt") for link in result["pages"]))
        self.assertTrue(result["phones"])

    def test_robots_disallow_blocks_the_crawl(self):
        robots = "User-agent: *\nDisallow: /\n"
        with mock.patch.object(crawl.net, "text", return_value=robots), \
                mock.patch.object(crawl.net, "fetch") as fetch:
            crawl._ROBOTS = crawl.Robots()
            result = crawl.crawl_domain("blocked-test.com", pause=0)
            fetch.assert_not_called()
        self.assertTrue(result["blocked_by_robots"])
        crawl._ROBOTS = crawl.Robots()

    def test_bad_domain_is_reported_not_raised(self):
        result = crawl.crawl_domain("", pause=0)
        self.assertFalse(result["ok"])
        self.assertTrue(result["error"])


class ConnectorTests(unittest.TestCase):
    def setUp(self):
        self.settings = config.load_settings()
        self.original = dict(self.settings.get("connectors") or {})

    def tearDown(self):
        self.settings["connectors"] = self.original
        config.save_settings(self.settings)

    def _set(self, **values):
        merged = dict(self.original)
        merged.update(values)
        self.settings["connectors"] = merged
        config.save_settings(self.settings)

    def test_status_reports_missing_keys(self):
        self._set(hunter_key="", ai_key="", ai_model="", serper_key="")
        state = connectors.status()
        self.assertFalse(state["hunter"])
        self.assertFalse(state["ai"])
        self.assertFalse(state["serper"])

    def test_missing_key_raises_with_a_useful_message(self):
        self._set(hunter_key="")
        with self.assertRaises(connectors.ConnectorError) as ctx:
            connectors.hunter_domain("acme-test.com")
        self.assertIn("Hunter", str(ctx.exception))

    def test_hunter_parses_a_response(self):
        self._set(hunter_key="test-key")
        payload = ('{"data": {"organization": "Acme", "pattern": "{first}@{domain}",'
                   ' "emails": [{"value": "Jana@Acme-Test.com", "first_name": "Jana",'
                   ' "last_name": "N", "position": "CEO", "confidence": 91, "type": "personal"}]}}')
        with mock.patch.object(connectors.net, "text", return_value=payload):
            result = connectors.hunter_domain("acme-test.com")
        self.assertEqual(result["emails"][0]["email"], "jana@acme-test.com")
        self.assertEqual(result["emails"][0]["confidence"], 91)
        self.assertEqual(result["organization"], "Acme")

    def test_ai_json_parses_a_chat_completion(self):
        self._set(ai_key="k", ai_model="m", ai_base="https://ai.test/v1")
        payload = ('{"choices": [{"message": {"content": "{\\"industry\\": \\"Widgets\\",'
                   ' \\"angle\\": \\"They just hired a marketer.\\"}"}}]}')
        with mock.patch.object(connectors.net, "fetch", return_value=payload.encode()):
            out = connectors.classify_company("acme-test.com", "Acme", "widgets")
        self.assertEqual(out["industry"], "Widgets")
        self.assertIn("marketer", out["angle"])

    def test_ai_without_model_is_rejected(self):
        self._set(ai_key="k", ai_model="")
        with self.assertRaises(connectors.ConnectorError):
            connectors.ai_chat([{"role": "user", "content": "hi"}])

    def test_serper_parses_results(self):
        self._set(serper_key="s")
        payload = ('{"organic": [{"title": "Acme", "link": "https://acme-test.com/",'
                   ' "snippet": "widgets"}]}')
        with mock.patch.object(connectors.net, "fetch", return_value=payload.encode()):
            hits = connectors.serper_search("acme widgets")
        self.assertEqual(hits[0]["link"], "https://acme-test.com/")


class JobRegistrationTests(unittest.TestCase):
    def test_new_jobs_are_registered(self):
        from engine.jobs import all_jobs
        keys = {job.key for job in all_jobs()}
        self.assertIn("contact_finder", keys)
        self.assertIn("ai_enrich", keys)


if __name__ == "__main__":
    unittest.main(verbosity=2)
