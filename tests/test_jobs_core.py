"""Tests for the automation layer: parsers, scheduling, merge and MX enrichment.

Offline only - no network calls, a throwaway data folder.
"""
import os
import tempfile
import time
import unittest
from unittest import mock

TMP = tempfile.mkdtemp(prefix="ls_jobs_")
os.environ["LS_DATA"] = TMP
os.environ["LS_PORT"] = "8797"

import sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine import db, scheduler  # noqa: E402
from engine.jobs import bankruptcy, merge, mx, nrd  # noqa: E402
from engine.jobs.base import Context, Job  # noqa: E402

FILINGS_HTML = """
<table>
  <tr><th>State</th><th>District</th><th>Filed</th><th>Debtor</th><th>Case No</th>
      <th>Attorney</th><th>Assets</th><th>Liabilities</th></tr>
  <tr><td>Iowa</td><td>Northern</td><td>11/13/2026</td><td>Commerce Ag, LLC</td>
      <td>26-01054</td><td>Joseph A. Peiffer</td><td>$10M-$50M</td><td>$10M-$50M</td></tr>
  <tr><td>New York</td><td>Eastern</td><td>11/12/2026</td><td>Zoila Junction, LLC</td>
      <td>26-45285</td><td>Charles Higgs</td><td>$1M-$10M</td><td>$500k-$1M</td></tr>
  <tr><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
  <tr><td>Page: 1 2 3</td></tr>
</table>
"""


def ctx_for(job, params=None):
    return Context(job, params or {})


class FilingsTests(unittest.TestCase):
    def test_band_low(self):
        self.assertEqual(bankruptcy.band_low("$1M-$10M"), 1_000_000)
        self.assertEqual(bankruptcy.band_low("$500k-$1M"), 500_000)
        self.assertEqual(bankruptcy.band_low("$0-$50k"), 0)
        self.assertEqual(bankruptcy.band_low("$10M-$50M"), 10_000_000)
        self.assertEqual(bankruptcy.band_low(""), 0)
        self.assertEqual(bankruptcy.band_low("Unknown"), 0)

    def test_parse_filings(self):
        rows = bankruptcy.parse_filings(FILINGS_HTML)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["debtor"], "Commerce Ag, LLC")
        self.assertEqual(rows[0]["case_no"], "26-01054")
        self.assertEqual(rows[0]["liabilities_low"], 10_000_000)
        self.assertEqual(rows[1]["state"], "New York")
        self.assertEqual(rows[1]["attorney"], "Charles Higgs")

    def test_parse_ignores_unrelated_tables(self):
        self.assertEqual(bankruptcy.parse_filings("<table><tr><td>hello</td></tr></table>"), [])

    def test_run_parses_and_filters(self):
        job = bankruptcy.JOBS[0]
        ctx = ctx_for(job, {"provider": "omni", "min_liabilities": 5_000_000, "limit": 10})
        with mock.patch.object(bankruptcy.net, "text", return_value=FILINGS_HTML):
            result = job.run(ctx)
        self.assertEqual(result["parsed"], 2)
        self.assertEqual(result["kept"], 1)      # only the $10M+ row passes the floor

    def test_parse_sec_filings(self):
        payload = {"hits": {"total": {"value": 2}, "hits": [
            {"_id": "0001213900-26-112265:ea0304695-8k_synergy.htm", "_source": {
                "display_names": ["Getty Images Holdings, Inc.  (GETY, GETYQ)  (CIK 0001898496)"],
                "file_date": "2026-10-26", "adsh": "0001213900-26-112265",
                "items": ["1.01", "1.03", "9.01"], "ciks": ["0001898496"],
                "biz_locations": ["Seattle, WA"], "biz_states": ["WA"], "form": "8-K"}},
            {"_id": "x:y", "_source": {"display_names": ["Not A Bankruptcy Inc"],
                                       "items": ["2.02"], "adsh": "1", "file_date": "2026-10-26"}},
        ]}}
        rows = bankruptcy.parse_sec_filings(payload)
        self.assertEqual(len(rows), 1)                     # only the Item 1.03 filing
        self.assertEqual(rows[0]["company"], "Getty Images Holdings, Inc.")
        self.assertEqual(rows[0]["cik"], "0001898496")
        self.assertIn("/Archives/edgar/data/1898496/000121390026112265/", rows[0]["url"])
        self.assertEqual(rows[0]["filed"], "2026-10-26")

    def test_parse_gazette(self):
        xml = ('<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">'
               '<entry><title>ACME WIDGETS LTD - Notice of administration</title>'
               '<link href="https://www.thegazette.co.uk/notice/1"/>'
               '<published>2026-11-10T09:00:00Z</published></entry></feed>')
        rows = bankruptcy.parse_gazette(xml)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["company"], "ACME WIDGETS LTD")
        self.assertEqual(rows[0]["filed"], "2026-11-10")


class NewDomainTests(unittest.TestCase):
    def test_parse_domains_filters_and_dedupes(self):
        text_value = "\n".join([
            "# header comment", "fresh-shop.com", "fresh-shop.com", "  another.sk  ",
            "no-tld", "https://third.io/path", "bad tld.xyz", "",
        ])
        got = nrd.parse_domains(text_value, ["com", "sk", "io"])
        self.assertEqual(got, ["fresh-shop.com", "another.sk", "third.io"])

    def test_run_with_a_fake_feed(self):
        job = nrd.JOBS[0]
        ctx = ctx_for(job, {"tlds": ["com"], "keywords": ["shop"], "limit": 10})
        fake = "<!doctype html>\nshop-one.com\nother.com\nshop-two.com\n"
        try:
            with mock.patch.object(nrd.net, "text", return_value=fake):
                result = job.run(ctx)
            self.assertEqual(result["kept"], 3)
            self.assertEqual(db.scalar("SELECT COUNT(*) FROM domains"), 3)
            self.assertEqual(db.scalar("SELECT COUNT(*) FROM signals WHERE kind='new_domain'"), 2)
        finally:
            db.execute("DELETE FROM domains")
            db.execute("DELETE FROM signals")


class MxTests(unittest.TestCase):
    def test_mx_job_marks_dead_domains(self):
        db.execute("DELETE FROM domains")      # other test modules may leave rows behind
        db.execute("DELETE FROM signals")
        for domain in ("alive-domain-test.com", "dead-domain-test.com"):
            db.insert("domains", {"domain": domain, "first_seen_ts": db.now(),
                                  "updated_ts": db.now()})
        job = mx.JOBS[0]
        ctx = ctx_for(job, {"limit": 10, "include_lead_domains": False})

        def fake_lookup(domain):
            return (["mx1.example.com"], "dnspython") if domain.startswith("alive") else ([], "nslookup")

        try:
            with mock.patch.object(mx.verify, "mx_lookup", side_effect=fake_lookup):
                result = job.run(ctx)
            self.assertEqual(result["checked"], 2)
            self.assertEqual(result["alive"], 1)
            self.assertEqual(result["dead"], 1)
            dead = db.one("SELECT * FROM domains WHERE domain='dead-domain-test.com'")
            self.assertEqual(dead["mx_status"], "none")
            self.assertEqual(db.scalar(
                "SELECT COUNT(*) FROM signals WHERE kind='mx_missing'"), 1)
        finally:
            db.execute("DELETE FROM domains")
            db.execute("DELETE FROM signals")


class MergeTests(unittest.TestCase):
    def test_merge_folder_imports_and_archives(self):
        folder = os.path.join(TMP, "imports")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, "batch.csv")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("email,first_name,company\n"
                     "merge-one@test-example.com,Ana,Acme\n"
                     "merge-two@test-example.com,Bo,Beta\n")
        job = merge.JOBS[0]
        ctx = ctx_for(job, {"folder": folder})
        result = job.run(ctx)
        self.assertEqual(result["files"], 1)
        self.assertEqual(result["imported"], 2)
        self.assertTrue(os.path.isdir(os.path.join(folder, "processed")))
        self.assertTrue(os.path.exists(os.path.join(folder, "processed", "batch.csv")))
        self.assertFalse(os.path.exists(path))
        # a second run has nothing left to do
        second = job.run(ctx_for(job, {"folder": folder}))
        self.assertEqual(second["files"], 0)


class ScheduleTests(unittest.TestCase):
    def test_parse_schedule(self):
        self.assertEqual(scheduler.parse_schedule("daily 06:30"), ("daily", None, 6, 30))
        self.assertEqual(scheduler.parse_schedule("weekly mon 07:15"), ("weekly", 0, 7, 15))
        self.assertEqual(scheduler.parse_schedule("weekly fri 18:00"), ("weekly", 4, 18, 0))
        self.assertIsNone(scheduler.parse_schedule(""))
        self.assertIsNone(scheduler.parse_schedule("every day"))
        self.assertIsNone(scheduler.parse_schedule("daily 25:00"))

    def test_next_run_is_in_the_future(self):
        base = time.mktime((2026, 11, 15, 12, 0, 0, 0, 0, -1))    # a Sunday, noon
        nxt = scheduler.next_run_ts("daily 06:30", base)
        self.assertGreater(nxt, base)
        self.assertLessEqual(nxt - base, 86400 * 2)
        self.assertEqual(time.localtime(nxt).tm_hour, 6)
        self.assertEqual(time.localtime(nxt).tm_min, 30)

    def test_weekly_lands_on_the_right_weekday(self):
        base = time.mktime((2026, 11, 15, 12, 0, 0, 0, 0, -1))
        nxt = scheduler.next_run_ts("weekly mon 07:30", base)
        self.assertEqual(time.localtime(nxt).tm_wday, 0)
        self.assertGreater(nxt, base)

    def test_empty_schedule_is_manual_only(self):
        self.assertEqual(scheduler.next_run_ts(""), 0)


class ContextTests(unittest.TestCase):
    def setUp(self):
        db.execute("DELETE FROM signals")
        db.execute("DELETE FROM domains")

    def test_signal_is_idempotent(self):
        job = Job("t", "test", "Test", "", run=lambda c: {})
        ctx = Context(job)
        self.assertTrue(ctx.add_signal("ads", company="Acme", source_url="u1"))
        self.assertFalse(ctx.add_signal("ads", company="Acme", source_url="u1"))
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM signals"), 1)

    def test_upsert_domain_merges_payload(self):
        job = Job("t", "test", "Test", "", run=lambda c: {})
        ctx = Context(job)
        ctx.upsert_domain("example-test.com", source="nrd", data={"a": 1})
        ctx.upsert_domain("example-test.com", cc_pages=12, data={"b": 2})
        row = db.one("SELECT * FROM domains WHERE domain='example-test.com'")
        self.assertEqual(row["cc_pages"], 12)
        self.assertEqual(row["source"], "nrd")
        self.assertIn('"a": 1', row["data"])
        self.assertIn('"b": 2', row["data"])

    def test_find_lead_by_domain_and_company(self):
        db.execute("DELETE FROM leads")
        db.insert("leads", {"email": "a@acme-test.com", "company": "Acme Testing Ltd",
                            "domain": "acme-test.com", "created_ts": db.now(),
                            "updated_ts": db.now()})
        job = Job("t", "test", "Test", "", run=lambda c: {})
        ctx = Context(job)
        self.assertIsNotNone(ctx.find_lead(domain="acme-test.com"))
        self.assertIsNotNone(ctx.find_lead(company="Acme Testing Ltd"))
        self.assertIsNone(ctx.find_lead(company="Nobody Here"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
