"""The domain catalogue: the three open-data sources, the store, and the tab's endpoints."""
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine import db, domain_sources as ds  # noqa: E402


class SourceRegistryTests(unittest.TestCase):
    def test_three_sources_are_described(self):
        items = ds.describe()
        self.assertEqual([entry["key"] for entry in items], ["ccrawl", "registered", "ct"])
        for entry in items:
            self.assertTrue(entry["url"].startswith("https://huggingface.co/datasets/"))
            self.assertTrue(entry["note"])
            self.assertTrue(entry["license"])

    def test_the_promised_sizes_are_in_the_right_order_of_magnitude(self):
        by_key = {entry["key"]: entry for entry in ds.describe()}
        self.assertGreaterEqual(by_key["ccrawl"]["rows"], 300000000)
        self.assertGreaterEqual(by_key["registered"]["rows"], 150000000)
        self.assertGreaterEqual(by_key["ct"]["rows"], 1000000)

    def test_an_unknown_source_is_refused(self):
        with self.assertRaises(ds.SourceError):
            list(ds.rows("nope"))


class SelectionTests(unittest.TestCase):
    def test_the_newest_crawl_wins(self):
        names = [
            "README.md",
            "data/cc-main-2026-apr-may-jun/part-001.parquet",
            "data/cc-main-2026-apr-may-jun/part-000.parquet",
            "data/cc-main-2025-x/part-000.parquet",
        ]
        crawl, files = ds.newest_crawl(names)
        self.assertEqual(crawl, "cc-main-2026-apr-may-jun")
        self.assertEqual(files, ["data/cc-main-2026-apr-may-jun/part-000.parquet",
                                 "data/cc-main-2026-apr-may-jun/part-001.parquet"])

    def test_no_crawl_shards(self):
        self.assertEqual(ds.newest_crawl(["README.md"]), ("", []))

    def test_the_newest_day_wins(self):
        names = ["data/2026/11/13.parquet", "data/2026/11/14.parquet", "README.md"]
        name, day = ds.newest_day(names)
        self.assertEqual(name, "data/2026/11/14.parquet")
        self.assertEqual(day, "2026-11-14")


class StoreTests(unittest.TestCase):
    def setUp(self):
        db.init_db()
        db.execute("DELETE FROM domains WHERE source IN ('ccrawl', 'registered', 'ct')")

    def tearDown(self):
        db.execute("DELETE FROM domains WHERE source IN ('ccrawl', 'registered', 'ct')")

    def test_only_unknown_domains_are_inserted(self):
        items = [
            {"domain": "alpha-ccrawl.com", "data": {"pagerank": 3.5, "n_hosts": 2}},
            {"domain": "alpha-ccrawl.com", "data": {}},                 # same domain twice
            {"domain": "not a domain", "data": {}},                      # refused
        ]
        added, known = ds.store(items, "ccrawl")
        self.assertEqual((added, known), (1, 1))      # the invalid row is skipped, not counted
        row = db.one("SELECT * FROM domains WHERE domain='alpha-ccrawl.com'")
        self.assertEqual(row["source"], "ccrawl")
        self.assertIn("pagerank", row["data"])
        self.assertEqual(ds.store(items, "ccrawl"), (0, 2))

    def test_a_url_is_reduced_to_a_domain(self):
        ds.store([{"domain": "https://www.beta-ct.com/page", "data": {}}], "ct")
        self.assertTrue(db.one("SELECT domain FROM domains WHERE domain='beta-ct.com'"))


class PullTests(unittest.TestCase):
    def setUp(self):
        db.init_db()
        db.execute("DELETE FROM domains WHERE source='ct'")

    def tearDown(self):
        db.execute("DELETE FROM domains WHERE source='ct'")

    def test_pull_counts_what_it_stored(self):
        fake = [{"domain": "gamma-ct.com", "data": {"ct_day": "2026-11-14"}},
                {"domain": "delta-ct.com", "data": {}}]
        with mock.patch.object(ds, "rows", return_value=iter(fake)):
            result = ds.pull("ct", limit=10)
        self.assertTrue(result["ok"], result)
        self.assertEqual((result["added"], result["known"]), (2, 0))
        with mock.patch.object(ds, "rows", return_value=iter(fake)):
            again = ds.pull("ct", limit=10)
        self.assertEqual(again["added"], 0)

    def test_a_source_error_is_reported_not_raised(self):
        with mock.patch.object(ds, "rows", side_effect=ds.SourceError("no pyarrow")):
            result = ds.pull("ct")
        self.assertFalse(result["ok"])
        self.assertIn("pyarrow", result["error"])


class DomainEndpointTests(unittest.TestCase):
    def setUp(self):
        from engine import server
        self.server = server
        db.init_db()
        db.execute("DELETE FROM domains WHERE source='ct'")
        ds.store([{"domain": "endpoint-ct.com", "data": {"ct_day": "2026-11-14"}}], "ct")

    def tearDown(self):
        db.execute("DELETE FROM domains WHERE source='ct'")

    def test_the_list_returns_rows_and_stats(self):
        answer = self.server.r_domain_list({"q": "endpoint-ct"})
        self.assertTrue(answer["ok"])
        self.assertEqual([row["domain"] for row in answer["domains"]], ["endpoint-ct.com"])
        self.assertIn("total", answer["stats"])
        self.assertEqual(len(answer["sources"]), 3)

    def test_a_pull_needs_a_source(self):
        self.assertFalse(self.server.r_domain_pull({})["ok"])

    def test_a_scan_needs_a_domain(self):
        self.assertFalse(self.server.r_domain_scrape({"domain": "not a domain"})["ok"])


class LeadDomainSyncTests(unittest.TestCase):
    """Every lead's domain belongs in the catalogue, email or no email."""

    def setUp(self):
        db.init_db()
        db.execute("DELETE FROM domains WHERE source='lead'")
        for domain in ("only-domain-test.com", "email-only-test.com", "ensure-test.com"):
            db.execute("DELETE FROM domains WHERE domain=?", (domain,))
            db.execute("DELETE FROM leads WHERE domain=?", (domain,))

    def tearDown(self):
        for domain in ("only-domain-test.com", "email-only-test.com", "ensure-test.com"):
            db.execute("DELETE FROM domains WHERE domain=?", (domain,))
            db.execute("DELETE FROM leads WHERE domain=?", (domain,))

    def test_a_lead_with_only_a_domain_lands_in_the_catalogue(self):
        db.insert("leads", {"email": "", "domain": "only-domain-test.com",
                            "company": "Only Domain", "created_ts": db.now(),
                            "updated_ts": db.now()})
        result = ds.sync_lead_domains()
        self.assertTrue(result["ok"])
        self.assertGreaterEqual(result["added"], 1)
        row = db.one("SELECT * FROM domains WHERE domain='only-domain-test.com'")
        self.assertEqual(row["source"], "lead")
        self.assertEqual(row["tld"], "com")

    def test_an_email_only_lead_contributes_its_domain(self):
        from engine import importer
        importer.import_data("emails.csv", b"email\nsomeone@email-only-test.com\n")
        ds.sync_lead_domains()
        self.assertTrue(db.one("SELECT domain FROM domains WHERE domain='email-only-test.com'"))

    def test_the_sync_does_not_duplicate(self):
        db.insert("leads", {"email": "", "domain": "only-domain-test.com",
                            "created_ts": db.now(), "updated_ts": db.now()})
        first = ds.sync_lead_domains()
        self.assertGreaterEqual(first["added"], 1)
        second = ds.sync_lead_domains()
        self.assertEqual(second["added"], 0)

    def test_ensure_domain_adds_once_and_normalises(self):
        self.assertEqual(ds.ensure_domain("https://www.Ensure-Test.com/x"), "ensure-test.com")
        self.assertEqual(ds.ensure_domain("ensure-test.com"), "")
        self.assertTrue(db.one("SELECT domain FROM domains WHERE domain='ensure-test.com'"))

    def test_the_endpoint_runs_the_sync(self):
        from engine import server
        db.insert("leads", {"email": "", "domain": "only-domain-test.com",
                            "created_ts": db.now(), "updated_ts": db.now()})
        answer = server.r_domain_sync({})
        self.assertTrue(answer["ok"])
        self.assertGreaterEqual(answer["added"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
