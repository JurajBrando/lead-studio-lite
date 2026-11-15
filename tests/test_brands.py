"""Tests for brand watch: candidate generation, scanning, import. Offline only."""
import os
import tempfile
import unittest
from unittest import mock

TMP = tempfile.mkdtemp(prefix="ls_brands_")
os.environ["LS_DATA"] = TMP
os.environ["LS_PORT"] = "8795"

import sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine import brands, db  # noqa: E402
from engine.jobs.base import Context, Job  # noqa: E402


def ctx():
    return Context(Job("brand_watch", "brand watch", "Signals", "", run=None), {})


class DomainSplitTests(unittest.TestCase):
    def test_plain_and_multi_level(self):
        self.assertEqual(brands.split_domain("acme.com"), ("acme", "com"))
        self.assertEqual(brands.split_domain("acme-widgets.co.uk"), ("acme-widgets", "co.uk"))
        self.assertEqual(brands.split_domain("https://www.acme.io/path"), ("acme", "io"))

    def test_token(self):
        self.assertEqual(brands.token("Acme-Widgets"), "acmewidgets")
        self.assertEqual(brands.token("..."), "")


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.cands = brands.candidates("acme-test.com", limit=300)
        self.domains = [c["domain"] for c in self.cands]
        self.kinds = {c["domain"]: c["kind"] for c in self.cands}

    def test_generates_the_expected_families(self):
        self.assertIn("acmetest.shop", self.domains)              # other TLD
        self.assertIn("acmetestshop.com", self.domains)           # brand + shop
        self.assertIn("officialacmetest.com", self.domains)       # official + brand
        self.assertEqual(self.kinds["acmetest.shop"], "tld")
        self.assertEqual(self.kinds["acmetestshop.com"], "keyword")

    def test_generates_typos_and_homoglyphs(self):
        self.assertTrue(any(k == "typo" for k in self.kinds.values()))
        self.assertTrue(any(k == "homoglyph" for k in self.kinds.values()))

    def test_excludes_the_original_and_duplicates(self):
        self.assertNotIn("acme-test.com", self.domains)
        self.assertEqual(len(self.domains), len(set(self.domains)))

    def test_limit_and_invalid_input(self):
        self.assertLessEqual(len(brands.candidates("acme.com", limit=5)), 5)
        self.assertEqual(brands.candidates("nodots"), [])


class BrandStoreTests(unittest.TestCase):
    def setUp(self):
        db.execute("DELETE FROM brands")

    def test_add_is_idempotent(self):
        first = brands.add_brand("Acme", "acme-test.com")
        second = brands.add_brand("Acme Ltd", "https://www.acme-test.com/x")
        self.assertEqual(first, second)
        row = db.one("SELECT * FROM brands WHERE id=?", (first,))
        self.assertEqual(row["name"], "Acme Ltd")
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM brands"), 1)

    def test_import_text(self):
        text_value = ("Acme Widgets, acme-test.com\n"
                      "https://beta-test.example.com/path\n"
                      "duplicate.domain\n"
                      "# a comment\n"
                      "Acme Widgets, acme-test.com\n")
        added = brands.import_text(text_value)
        self.assertEqual(added, 3)
        row = db.one("SELECT * FROM brands WHERE domain='beta-test.example.com'")
        self.assertIsNotNone(row)


class ScanTests(unittest.TestCase):
    def setUp(self):
        db.execute("DELETE FROM brands")
        db.execute("DELETE FROM signals")
        db.execute("DELETE FROM domains")
        self.brand_id = brands.add_brand("Acme", "acme-test.com")
        self.brand = db.one("SELECT * FROM brands WHERE id=?", (self.brand_id,))

    def _scan(self, live=(), mail=False, **kwargs):
        with mock.patch.object(brands, "resolves", side_effect=lambda d: d in live), \
                mock.patch.object(brands, "has_mail", return_value=mail), \
                mock.patch.object(brands.time, "sleep", return_value=None):
            return brands.scan_brand(self.brand, candidates_per_brand=120, min_score=4,
                                     pause=0, ctx=ctx(), **kwargs)

    def test_strong_lookalike_is_recorded_as_live(self):
        report = self._scan(live={"acmetestshop.com"}, mail=True)
        self.assertGreaterEqual(report["count"], 1)
        row = db.one("SELECT * FROM signals WHERE kind='lookalike_live' AND domain=?",
                     ("acmetestshop.com",))
        self.assertIsNotNone(row)
        self.assertIn('"has_mail": true', row["payload"])
        brand = db.one("SELECT * FROM brands WHERE id=?", (self.brand_id,))
        self.assertGreater(brand["last_scan_ts"], 0)
        self.assertGreaterEqual(brand["last_findings"], 1)

    def test_weak_match_is_not_recorded(self):
        # a bare TLD swap scores 1+1=2, below the threshold of 4
        report = self._scan(live={"acmetest.net"}, mail=False)
        self.assertEqual(report["count"], 0)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM signals WHERE kind='lookalike_live'"), 0)

    def test_new_domain_feed_is_cross_checked(self):
        db.insert("domains", {"domain": "acmetest-outlet.com", "first_seen_ts": db.now(),
                              "updated_ts": db.now()})
        report = self._scan(live=set())
        kinds = [f["kind"] for f in report["findings"]]
        self.assertIn("lookalike_nrd", kinds)

    def test_phishing_list_is_cross_checked(self):
        ctx().add_signal("phishing", domain="acmetest-login.com", source="phishing.database",
                         source_url="https://example.test/list")
        report = self._scan(live=set())
        kinds = [f["kind"] for f in report["findings"]]
        self.assertIn("lookalike_listed", kinds)

    def test_scan_twice_does_not_duplicate(self):
        self._scan(live={"acmetestshop.com"}, mail=True)
        first = db.scalar("SELECT COUNT(*) FROM signals")
        self._scan(live={"acmetestshop.com"}, mail=True)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM signals"), first)


class JobTests(unittest.TestCase):
    def test_job_is_registered_with_a_schedule(self):
        from engine.jobs import all_jobs
        job = next((j for j in all_jobs() if j.key == "brand_watch"), None)
        self.assertIsNotNone(job)
        self.assertEqual(job.schedule, "daily 08:00")
        self.assertIn("brands_per_run", job.params)


if __name__ == "__main__":
    unittest.main(verbosity=2)
