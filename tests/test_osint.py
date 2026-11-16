"""Fake shops, bare domains and the Meta Ad Library - the OSINT tab's engine."""
import json
import os
import sys
import tempfile
import unittest

if not os.environ.get("LS_DATA"):
    os.environ["LS_DATA"] = tempfile.mkdtemp(prefix="ls_osint_")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine import db, osint  # noqa: E402


class BrandMatchingTests(unittest.TestCase):
    def setUp(self):
        db.init_db()
        osint.ensure_tables()
        db.execute("DELETE FROM signals WHERE source='osint-radar'")

    def test_a_brand_in_a_shop_domain_is_a_hit(self):
        brand, why = osint.match_domain("nikeshop-online.xyz", ["Nike"])
        self.assertEqual(brand, "Nike")
        self.assertTrue(why)

    def test_a_brand_on_a_shop_style_tld_is_a_hit(self):
        brand, _ = osint.match_domain("swatch.deal", ["Swatch"])
        self.assertEqual(brand, "Swatch")

    def test_the_brand_own_domain_is_not_a_hit(self):
        brand, _ = osint.match_domain("nike.com", ["Nike"])
        self.assertIsNone(brand)

    def test_an_unrelated_domain_is_not_a_hit(self):
        brand, _ = osint.match_domain("morningcoffee.sk", ["Nike"])
        self.assertIsNone(brand)

    def test_a_scan_reads_a_txt_file_and_stores_the_hits(self):
        path = os.path.join(tempfile.gettempdir(), "osint_domains_test.txt")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("domain\nnikeshop.xyz\nmorningcoffee.sk\nnike.com\nadidas-outlet.top\n")
        answer = osint.scan(path, brands_text="Nike\nAdidas", limit=100)
        self.assertTrue(answer["ok"], answer)
        self.assertEqual(answer["scanned"], 4)
        domains = {hit["domain"] for hit in answer["hits"]}
        self.assertIn("nikeshop.xyz", domains)
        self.assertIn("adidas-outlet.top", domains)
        self.assertNotIn("nike.com", domains)
        stored = db.scalar("SELECT COUNT(*) FROM signals WHERE kind='fake_shop' "
                           "AND source='osint-radar'")
        self.assertGreaterEqual(stored, 2)
        os.remove(path)

    def test_a_feed_without_brands_is_refused(self):
        with self.assertRaises(ValueError):
            osint.scan("", brands_text="")

    def test_state_has_the_red_points_link(self):
        state = osint.state()
        self.assertTrue(state["ok"])
        self.assertIn("affiliate_url", state["red_points"])
        self.assertIn("redpoints.com", state["red_points"]["affiliate_url"])


class MetaWalkerTests(unittest.TestCase):
    def test_advertiser_rows_are_found_and_deduplicated(self):
        answer = {"data": {"ad_library_main": {"search_results_connection": {"edges": [
            {"node": {"page_name": "Glow Skincare", "page_url": "https://fb.com/glow",
                      "collation_count": 12,
                      "snapshot": {"link_url": "https://glowskin.sk/shop"}}},
            {"node": {"page_name": "Glow Skincare", "page_url": "https://fb.com/glow",
                      "collation_count": 3, "snapshot": {}}},
            {"node": {"page_name": "Peak Supplements", "page_url": "https://fb.com/peak",
                      "collation_count": 4, "snapshot": {"link_url": "https://peak.sk"}}},
        ]}}}}
        rows = osint._ad_items(answer)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["page"], "Glow Skincare")
        self.assertEqual(rows[0]["domain"], "glowskin.sk")
        self.assertEqual(rows[0]["ads"], 12)


class ProfileTests(unittest.TestCase):
    def test_a_bad_domain_is_refused_before_any_request(self):
        with self.assertRaises(ValueError):
            osint.profile("not a domain")

    def test_a_page_is_turned_into_a_profile(self):
        page = ("<html><head><title>Smile Dental Bratislava</title>" +
                '<meta name="description" content="Dental clinic in Bratislava">' +
                "</head><body>Book online: info@smiledental.sk or +421 900 123 456</body></html>")

        def fake(url, timeout=20):
            if url.endswith(".sk") or "/contact" in url:
                return page
            raise OSError("no")

        saved = osint.fetch_page
        osint.fetch_page = fake
        try:
            answer = osint.profile("smiledental.sk")
        finally:
            osint.fetch_page = saved
        self.assertTrue(answer["ok"], answer)
        self.assertIn("Smile Dental", answer["title"])
        self.assertIn("info@smiledental.sk", answer["emails"])
        self.assertEqual(answer["business"], "dental")


if __name__ == "__main__":
    unittest.main(verbosity=2)
