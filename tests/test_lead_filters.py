"""Company size buckets and the advanced lead filters (country, industry, size, ...)."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine import db, importer  # noqa: E402
from engine import server  # noqa: E402


class SizeBucketTests(unittest.TestCase):
    def test_numbers_land_in_a_bucket(self):
        self.assertEqual(importer.size_bucket("8"), "1-10")
        self.assertEqual(importer.size_bucket("120"), "51-200")
        self.assertEqual(importer.size_bucket("5,000"), "1000+")

    def test_ranges_are_kept(self):
        self.assertEqual(importer.size_bucket("51-200"), "51-200")
        self.assertEqual(importer.size_bucket("51 - 200"), "51-200")
        self.assertEqual(importer.size_bucket("201–500"), "201-500")

    def test_a_plus_means_the_top_bucket(self):
        self.assertEqual(importer.size_bucket("1,000+"), "1000+")
        self.assertEqual(importer.size_bucket("1000"), "501-1000")

    def test_junk_is_empty(self):
        self.assertEqual(importer.size_bucket(""), "")
        self.assertEqual(importer.size_bucket("n/a"), "")


class CountryNameTests(unittest.TestCase):
    def test_common_variants_collapse(self):
        from engine import net
        for value in ("US", "usa", "u.s.", "united states", "United States of America"):
            self.assertEqual(net.normalise_country(value), "United States")
        self.assertEqual(net.normalise_country("  uk "), "United Kingdom")
        self.assertEqual(net.normalise_country("Slovensko"), "Slovakia")

    def test_unknown_names_are_kept_as_written(self):
        from engine import net
        self.assertEqual(net.normalise_country("Côte d'Ivoire"), "Côte d'Ivoire")
        self.assertEqual(net.normalise_country(""), "")

    def test_the_country_filter_merges_variants(self):
        db.init_db()
        db.execute("DELETE FROM leads WHERE email LIKE '%variant-test.com'")
        for email, country in (("a@variant-test.com", "US"),
                               ("b@variant-test.com", "United States"),
                               ("c@variant-test.com", "Germany")):
            db.insert("leads", {"email": email, "country": country, "created_ts": db.now(),
                                "updated_ts": db.now()})
        try:
            answer = server.r_leads({"q": "variant-test", "country": "United States"})
            self.assertEqual(answer["total"], 2)
            facets = answer["facets"]["countries"]
            self.assertIn("United States", facets)
            self.assertNotIn("US", facets)
        finally:
            db.execute("DELETE FROM leads WHERE email LIKE '%variant-test.com'")


class FilterTests(unittest.TestCase):
    ROWS = [
        {"email": "a@one-filter-test.com", "country": "Slovakia", "city": "Bratislava",
         "industry": "SaaS B2B", "company_size": "11-50",
         "technologies": "shopify, klaviyo", "source": "import", "company": "One"},
        {"email": "", "domain": "two-filter-test.com", "country": "Germany", "city": "Berlin",
         "industry": "Manufacturing Food", "company_size": "201-500",
         "technologies": "sap", "source": "crawl", "company": "Two"},
        {"email": "c@three-filter-test.com", "country": "Slovakia", "city": "Kosice",
         "industry": "Manufacturing Machinery", "company_size": "1000+",
         "technologies": "wordpress", "source": "import", "company": "Three"},
    ]

    def setUp(self):
        db.init_db()
        db.execute("DELETE FROM leads WHERE email LIKE '%filter-test.com' OR domain LIKE '%filter-test.com'")
        for row in self.ROWS:
            payload = {"created_ts": db.now(), "updated_ts": db.now(), "status": "new"}
            payload.update(row)
            db.insert("leads", payload)

    def tearDown(self):
        db.execute("DELETE FROM leads WHERE email LIKE '%filter-test.com' OR domain LIKE '%filter-test.com'")

    def find(self, **filters):
        query = {"q": "filter-test"}
        query.update(filters)
        answer = server.r_leads(query)
        self.assertTrue(answer["ok"], answer)
        return answer

    def test_country(self):
        self.assertEqual(self.find(country="Slovakia")["total"], 2)
        self.assertEqual(self.find(country="Slovakia, Germany")["total"], 3)

    def test_must_have_industries_are_all_required(self):
        self.assertEqual(self.find(must_industry="manufacturing")["total"], 2)
        self.assertEqual(self.find(industry="manufacturing")["total"], 2)
        self.assertEqual(self.find(must_industry="manufacturing, machinery")["total"], 1)

    def test_industry_any_of(self):
        self.assertEqual(self.find(industry="saas, wordpress")["total"], 1)

    def test_company_size(self):
        self.assertEqual(self.find(company_size="201-500")["total"], 1)
        self.assertEqual(self.find(company_size="1000+")["total"], 1)
        self.assertEqual(self.find(company_size="unknown")["total"], 0)

    def test_technologies(self):
        self.assertEqual(self.find(technologies="shopify")["total"], 1)
        self.assertEqual(self.find(technologies="shopify, sap")["total"], 2)

    def test_location_matches_city_or_country(self):
        self.assertEqual(self.find(location="bratislava")["total"], 1)
        self.assertEqual(self.find(location="slovakia")["total"], 2)

    def test_source(self):
        self.assertEqual(self.find(source="crawl")["total"], 1)
        self.assertEqual(self.find(source="import")["total"], 2)

    def test_title_filter(self):
        db.execute("UPDATE leads SET title='Head of Growth' WHERE email='a@one-filter-test.com'")
        self.assertEqual(self.find(title="growth")["total"], 1)

    def test_sorting(self):
        names = [row["company"] for row in self.find(sort="company")["leads"]]
        self.assertEqual(names, ["One", "Three", "Two"])

    def test_facets_are_returned(self):
        facets = self.find()["facets"]
        self.assertIn("Slovakia", facets["countries"])
        self.assertIn("crawl", facets["sources"])
        self.assertEqual(facets["sizes"][0], "1-10")

    def test_an_import_fills_the_size_bucket_and_technologies(self):
        res = importer.import_data(
            "sizes.csv",
            b"company,email,employees,technologies\nBucket Co,bucket@bucket-filter-test.com,120,shopify\n")
        self.assertTrue(res["ok"], res)
        row = db.one("SELECT * FROM leads WHERE email='bucket@bucket-filter-test.com'")
        self.assertEqual(row["company_size"], "51-200")
        self.assertEqual(row["technologies"], "shopify")


if __name__ == "__main__":
    unittest.main(verbosity=2)
