"""The Hugging Face bulk-data links, and the Common Crawl host-index job.

The job itself is only exercised against a fake here: the real one streams a shard of a
300 MB parquet from the Hub, which is not something a test suite should fetch.
"""
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine import db, hf_data  # noqa: E402
from engine.jobs import cc_hf  # noqa: E402
from engine.jobs.base import Context  # noqa: E402


class HfDataTests(unittest.TestCase):
    def test_the_links_point_at_the_dataset(self):
        info = hf_data.info()
        self.assertEqual(info["dataset"], "Jurajbrando/lead-studio-data")
        self.assertTrue(info["url"].startswith("https://huggingface.co/datasets/"))
        names = [entry["name"] for entry in info["files"]]
        self.assertIn("rec_results.duckdb", names)
        self.assertIn("domains.txt", names)
        for entry in info["files"]:
            self.assertTrue(entry["url"].endswith("/resolve/main/" + entry["name"]))

    def test_every_file_carries_a_label_and_a_note(self):
        for entry in hf_data.info()["files"]:
            self.assertTrue(entry["label"])
            self.assertTrue(entry["note"])


class HostIndexTests(unittest.TestCase):
    def setUp(self):
        db.init_db()                                 # the module can also run on its own
        db.execute("DELETE FROM domains WHERE source='commoncrawl-hf'")

    def tearDown(self):
        db.execute("DELETE FROM domains WHERE source='commoncrawl-hf'")

    def test_the_registered_domain_wins(self):
        self.assertEqual(
            cc_hf.domain_of_row({"url_host_registered_domain": "WWW.Example.com"}), "example.com")

    def test_a_surt_host_is_reversed_as_a_fallback(self):
        self.assertEqual(cc_hf.domain_of_row({"surt_host_name": "com,example,www"}), "example.com")

    def test_a_row_without_a_host_is_skipped(self):
        self.assertEqual(cc_hf.domain_of_row({}), "")
        self.assertEqual(cc_hf.domain_of_row({"surt_host_name": "nodots"}), "")

    def test_new_domains_are_stored_once_with_their_stats(self):
        rows = [
            ("alpha-test.com", {"fetch_200": 12, "nutch_fetched": 20, "hcrank": 1.5}),
            ("beta-test.com", {"fetch_200": 3}),
        ]
        added, known = cc_hf.store(rows, "CC-MAIN-2025-18")
        self.assertEqual((added, known), (2, 0))
        row = db.one("SELECT * FROM domains WHERE domain='alpha-test.com'")
        self.assertEqual(row["source"], "commoncrawl-hf")
        self.assertEqual(row["cc_pages"], 12)
        self.assertEqual(row["tld"], "com")
        self.assertIn("CC-MAIN-2025-18", row["data"])
        self.assertEqual(cc_hf.store(rows, "CC-MAIN-2025-18"), (0, 2))

    def test_the_stats_keep_only_known_numbers(self):
        stats = cc_hf.stats_of_row({"fetch_200": 5, "junk_column": "x"}, "CC-MAIN-2025-18")
        self.assertEqual(stats["fetch_200"], 5)
        self.assertEqual(stats["crawl"], "CC-MAIN-2025-18")
        self.assertNotIn("junk_column", stats)

    def test_the_job_explains_a_missing_library(self):
        job = cc_hf.JOBS[0]
        with mock.patch.object(cc_hf, "available", return_value=False), \
                mock.patch.object(cc_hf, "hub", side_effect=cc_hf.HubError("no pyarrow")):
            result = cc_hf.run(Context(job))
        self.assertFalse(result["ok"])
        self.assertIn("pyarrow", result["error"])

    def test_the_job_adds_rows_from_a_fake_shard(self):
        job = cc_hf.JOBS[0]
        fake_rows = [
            {"url_host_registered_domain": "gamma-test.com", "fetch_200": 9},
            {"url_host_registered_domain": "gamma-test.com", "fetch_200": 9},   # same host twice
            {"url_host_registered_domain": "delta-test.com", "fetch_200": 1},
            {"url_host_registered_domain": ""},                                 # no host at all
        ]
        with mock.patch.object(cc_hf, "available", return_value=True), \
                mock.patch.object(cc_hf, "crawls",
                                  return_value=[("CC-MAIN-2025-18", ["data/crawl=CC-MAIN-2025-18/x.parquet"])]), \
                mock.patch.object(cc_hf, "iter_hosts", return_value=iter(fake_rows)):
            result = cc_hf.run(Context(job, {"limit": 10}))
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["crawl"], "CC-MAIN-2025-18")
        self.assertEqual(result["added"], 2)
        self.assertEqual(db.scalar("SELECT count(*) FROM domains WHERE source='commoncrawl-hf'"), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
