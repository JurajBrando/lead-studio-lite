"""Matching domains against the Common Crawl columnar index, from Hugging Face or a local copy."""
import datetime
import os
import sys
import tempfile
import unittest

import pyarrow as pa
import pyarrow.parquet as pq

if not os.environ.get("LS_DATA"):
    os.environ["LS_DATA"] = tempfile.mkdtemp(prefix="ls_ccindex_")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine import cc_index, db  # noqa: E402

SCHEMA = pa.schema([
    ("url_surtkey", pa.string()),
    ("url", pa.string()),
    ("url_host_registered_domain", pa.string()),
    ("fetch_time", pa.timestamp("ms", tz="UTC")),
    ("fetch_status", pa.int16()),
])


def write_shard(path, rows):
    table = pa.table({
        "url_surtkey": [row[0] for row in rows],
        "url": ["https://" + row[1] + "/" for row in rows],
        "url_host_registered_domain": [row[1] for row in rows],
        "fetch_time": [row[2] for row in rows],
        "fetch_status": [row[3] for row in rows],
    }, schema=SCHEMA)
    pq.write_table(table, path)


class SurtKeyTests(unittest.TestCase):
    def test_a_domain_is_reversed(self):
        self.assertEqual(cc_index.surtkey("example.com"), "com,example")
        self.assertEqual(cc_index.surtkey("shop.example.co.uk"), "uk,co,example,shop")
        self.assertEqual(cc_index.surtkey("EXAMPLE.COM "), "com,example")
        self.assertEqual(cc_index.surtkey(""), "")

    def test_locate_finds_the_shard_whose_band_holds_the_key(self):
        bands = [
            {"path": "a.parquet", "key_low": "com,fagenmiller", "key_high": "com,festival"},
            {"path": "b.parquet", "key_low": "it,dipa", "key_high": "it,ilgiornale"},
        ]
        self.assertEqual([b["path"] for b in cc_index.locate("com,fagenmiller", bands)], ["a.parquet"])
        self.assertEqual([b["path"] for b in cc_index.locate("it,dipastaimpasta", bands)], ["b.parquet"])
        self.assertEqual(cc_index.locate("com,zzz", bands), [])

    def test_a_band_that_starts_with_the_key_still_matches(self):
        bands = [{"path": "a.parquet", "key_low": "com,example,www", "key_high": "com,example,zzz"}]
        self.assertEqual(len(cc_index.locate("com,example", bands)), 1)


class MatchTests(unittest.TestCase):
    def setUp(self):
        db.init_db()
        self.tmp = tempfile.mkdtemp(prefix="ccindex_shard_")
        self.path = os.path.join(self.tmp, "part-00000.parquet")
        write_shard(self.path, [
            ("com,example)/", "example.com", datetime.datetime(2026, 5, 1, tzinfo=datetime.timezone.utc), 200),
            ("com,example)/about", "example.com", datetime.datetime(2026, 6, 2, tzinfo=datetime.timezone.utc), 200),
            ("com,example)/old", "example.com", datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc), 404),
            ("com,other)/", "other.com", datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc), 200),
        ])

    def test_it_aggregates_pages_last_capture_and_200s(self):
        results = cc_index.match(["example.com", "missing.com"], local_dir=self.tmp)
        self.assertTrue(results["example.com"]["found"])
        self.assertEqual(results["example.com"]["pages"], 3)
        self.assertEqual(results["example.com"]["ok"], 2)
        self.assertEqual(str(results["example.com"]["last"])[:10], "2026-06-02")
        self.assertFalse(results["missing.com"]["found"])

    def test_the_result_is_written_onto_the_domains_table(self):
        db.execute("DELETE FROM domains WHERE domain LIKE '%ccindex-test.com'")
        db.insert("domains", {"domain": "example.com", "tld": "com", "first_seen_ts": db.now(),
                              "updated_ts": db.now()})
        results = cc_index.match(["example.com"], local_dir=self.tmp)
        written = cc_index.store(results)
        self.assertEqual(written, 1)
        row = db.one("SELECT cc_pages, cc_last_crawl, cc_checked_ts FROM domains WHERE domain='example.com'")
        self.assertEqual(row["cc_pages"], 3)
        self.assertEqual(row["cc_last_crawl"], "2026-06-02")
        self.assertGreater(row["cc_checked_ts"], 0)

    def test_a_domain_that_is_not_there_is_still_marked_checked(self):
        db.insert("domains", {"domain": "nothing-here-ccindex.com", "tld": "com",
                              "first_seen_ts": db.now(), "updated_ts": db.now()})
        cc_index.store(cc_index.match(["nothing-here-ccindex.com"], local_dir=self.tmp))
        row = db.one("SELECT cc_pages, cc_checked_ts FROM domains WHERE domain='nothing-here-ccindex.com'")
        self.assertEqual(row["cc_pages"], 0)
        self.assertGreater(row["cc_checked_ts"], 0)
        db.execute("DELETE FROM domains WHERE domain='nothing-here-ccindex.com'")


class PendingTests(unittest.TestCase):
    def test_pending_skips_domains_already_checked(self):
        db.init_db()
        db.execute("DELETE FROM domains WHERE domain LIKE '%pending-test.com'")
        db.insert("domains", {"domain": "new-pending-test.com", "tld": "com",
                              "first_seen_ts": db.now(), "updated_ts": db.now()})
        db.insert("domains", {"domain": "done-pending-test.com", "tld": "com",
                              "first_seen_ts": db.now(), "updated_ts": db.now(),
                              "cc_checked_ts": db.now()})
        pending = cc_index.pending(50)
        self.assertIn("new-pending-test.com", pending)
        self.assertNotIn("done-pending-test.com", pending)
        db.execute("DELETE FROM domains WHERE domain LIKE '%pending-test.com'")


if __name__ == "__main__":
    unittest.main(verbosity=2)
