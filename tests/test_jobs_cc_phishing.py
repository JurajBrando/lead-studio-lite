"""Unit tests for the Common Crawl and phishing-watch jobs.

Run:  python -m unittest discover -s tests -v

No network is touched: the parsers are pure functions, the two run() tests patch
`net.text` / `cc.latest_index`, and everything is written to a throwaway data
folder. Fixture rows all carry the "lswatch" marker so the tests also pass when
the suite shares one database with tests/test_app.py (whichever module imported
engine first owns LS_DATA).
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

TMP = tempfile.mkdtemp(prefix="ls_jobs_")
os.environ["LS_DATA"] = TMP

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine import db, jobs, net  # noqa: E402
from engine.jobs import cc, phishing  # noqa: E402
from engine.jobs.base import Context  # noqa: E402

MARKER = "lswatch"
CC_INDEX = "CC-MAIN-2026-43"

# Two captures of one site, newest last - the index answers with NDJSON.
TWO_RECORDS = (
    '{"url": "https://footprint.lswatch.test/", "status": "200", "mime": "text/html", '
    '"timestamp": "20260115093000", "length": "512", "digest": "AAA"}\n'
    '{"url": "https://footprint.lswatch.test/about", "status": "200", "mime": "text/html", '
    '"timestamp": "20261015093000", "length": "1024", "digest": "BBB"}\n'
)


def setUpModule():
    db.init_db()


def tearDownModule():
    wipe_fixtures()
    db.close()


def wipe_fixtures():
    """Remove only the rows these tests own - never the rest of the app's data."""
    like = "%" + MARKER + "%"
    db.execute("DELETE FROM signals WHERE domain LIKE ?", (like,))
    db.execute("DELETE FROM domains WHERE domain LIKE ?", (like,))
    db.execute("DELETE FROM leads WHERE email LIKE ?", ("%" + MARKER + "%",))


def add_domain(domain, seen_offset=60):
    """Insert a domain that sorts ahead of everything else in the sweep."""
    db.insert("domains", {"domain": domain, "tld": domain.rsplit(".", 1)[-1],
                          "first_seen_ts": db.now() + seen_offset,
                          "updated_ts": db.now()})


class CcParseTests(unittest.TestCase):
    def test_two_json_lines(self):
        parsed = cc.parse_cc_lines(TWO_RECORDS, "footprint.lswatch.test")
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["pages"], 2)
        self.assertEqual(parsed["last_crawl"], "20261015093000")
        self.assertEqual(parsed["statuses"], {"200": 2})

    def test_empty_body(self):
        for body in ("", "   ", "\n\n", b""):
            parsed = cc.parse_cc_lines(body, "empty.lswatch.test")
            self.assertEqual(parsed, {"pages": 0, "last_crawl": "", "ok": False,
                                      "statuses": {}})

    def test_no_captures_plain_text(self):
        body = "No Captures found for: empty.lswatch.test/*"
        parsed = cc.parse_cc_lines(body, "empty.lswatch.test")
        self.assertFalse(parsed["ok"])
        self.assertEqual(parsed["pages"], 0)
        self.assertEqual(parsed["last_crawl"], "")
        self.assertEqual(parsed["statuses"], {})

    def test_malformed_lines_are_skipped(self):
        body = ('<html>error</html>\n'
                '{"status": "200"}\n'
                '{"url": "https://mixed.lswatch.test/", "status": "301", '
                '"timestamp": "20251201000000"}\n'
                '[1, 2, 3]\n')
        parsed = cc.parse_cc_lines(body, "mixed.lswatch.test")
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["pages"], 1)
        self.assertEqual(parsed["statuses"], {"301": 1})
        self.assertEqual(parsed["last_crawl"], "20251201000000")

    def test_latest_index_reads_collinfo(self):
        collinfo = [{"id": CC_INDEX, "name": "October 2026 Index"},
                    {"id": "CC-MAIN-2026-38", "name": "September 2026 Index"}]
        with mock.patch.object(net, "json_body", return_value=collinfo):
            self.assertEqual(cc.latest_index(), CC_INDEX)
        with mock.patch.object(net, "json_body", return_value=[]):
            with self.assertRaises(net.NetError):
                cc.latest_index()


class CcRunTests(unittest.TestCase):
    def setUp(self):
        db.init_db()
        wipe_fixtures()

    def ctx(self, **params):
        values = {"limit": 1, "days": 30, "delay": 0, "include_lead_domains": False}
        values.update(params)
        return Context(cc.JOBS[0], values)

    def test_stores_footprint_and_signals_new_hit(self):
        domain = "footprint.lswatch.test"
        add_domain(domain)
        with mock.patch.object(cc, "latest_index", return_value=CC_INDEX), \
                mock.patch.object(net, "text", return_value=TWO_RECORDS):
            summary = cc.run(self.ctx())

        self.assertTrue(summary["ok"], summary)
        self.assertEqual(summary["index"], CC_INDEX)
        self.assertEqual(summary["checked"], 1)
        self.assertEqual(summary["hits"], 1)

        row = db.one("SELECT * FROM domains WHERE domain=?", (domain,))
        self.assertEqual(row["cc_pages"], 2)
        self.assertEqual(row["cc_last_crawl"], "20261015093000")
        self.assertGreater(row["cc_checked_ts"], 0)

        signal = db.one("SELECT * FROM signals WHERE kind='cc' AND domain=?", (domain,))
        self.assertIsNotNone(signal)
        self.assertEqual(signal["source"], "commoncrawl")
        payload = json.loads(signal["payload"])
        self.assertEqual(payload["pages"], 2)
        self.assertEqual(payload["last_crawl"], "20261015093000")
        self.assertEqual(payload["statuses"], {"200": 2})

    def test_rechecking_the_same_crawl_adds_no_signal(self):
        domain = "footprint.lswatch.test"
        add_domain(domain)
        with mock.patch.object(cc, "latest_index", return_value=CC_INDEX), \
                mock.patch.object(net, "text", return_value=TWO_RECORDS):
            first = cc.run(self.ctx())
            self.assertEqual(first["hits"], 1)
            db.execute("UPDATE domains SET cc_checked_ts=0 WHERE domain=?", (domain,))
            second = cc.run(self.ctx())

        self.assertTrue(second["ok"], second)
        self.assertEqual(second["checked"], 1)
        self.assertEqual(second["hits"], 1)          # still crawled, but nothing new
        count = db.scalar("SELECT COUNT(*) FROM signals WHERE kind='cc' AND domain=?", (domain,))
        self.assertEqual(count, 1)

    def test_no_captures_still_marks_the_domain_checked(self):
        domain = "empty.lswatch.test"
        add_domain(domain)

        def fake_text(url, **kw):
            raise net.NetError("HTTP 404 from index.commoncrawl.org", status=404, url=url)

        with mock.patch.object(cc, "latest_index", return_value=CC_INDEX), \
                mock.patch.object(net, "text", side_effect=fake_text):
            summary = cc.run(self.ctx())

        self.assertTrue(summary["ok"], summary)
        self.assertEqual(summary["checked"], 1)
        self.assertEqual(summary["hits"], 0)
        row = db.one("SELECT * FROM domains WHERE domain=?", (domain,))
        self.assertEqual(row["cc_pages"], 0)
        self.assertEqual(row["cc_last_crawl"], "")
        self.assertGreater(row["cc_checked_ts"], 0)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM signals WHERE kind='cc' AND domain=?",
                                   (domain,)), 0)

    def test_one_bad_domain_does_not_kill_the_run(self):
        good, bad = "good.lswatch.test", "bad.lswatch.test"
        add_domain(good, 61)
        add_domain(bad, 60)
        ctx = self.ctx(limit=2)

        def fake_text(url, **kw):
            if bad in url:
                raise net.NetError("HTTP 503 from index.commoncrawl.org", status=503, url=url)
            return TWO_RECORDS

        with mock.patch.object(cc, "latest_index", return_value=CC_INDEX), \
                mock.patch.object(net, "text", side_effect=fake_text):
            summary = cc.run(ctx)

        self.assertTrue(summary["ok"], summary)
        self.assertEqual(summary["checked"], 1)
        self.assertEqual(summary["hits"], 1)
        self.assertGreaterEqual(summary["warnings"], 1)
        self.assertIsNotNone(db.one("SELECT domain FROM domains WHERE domain=?", (good,)))
        self.assertEqual(db.scalar("SELECT cc_checked_ts FROM domains WHERE domain=?", (bad,)), 0)

    def test_index_failure_returns_error_summary(self):
        with mock.patch.object(cc, "latest_index",
                               side_effect=net.NetError("index down")):
            summary = cc.run(self.ctx())
        self.assertFalse(summary["ok"])
        self.assertEqual(summary["error"], "index down")

    def test_lead_only_domains_are_picked_up(self):
        domain = "leadside.lswatch.test"
        db.insert("leads", {"email": "lead@lswatch.test", "company": "Leadside Ltd",
                            "domain": domain, "status": "new", "notes": "",
                            "created_ts": db.now(), "updated_ts": db.now()})
        picked = cc.pick_domains(self.ctx(include_lead_domains=True, limit=200), 200, 30)
        self.assertIn(domain, picked)

        picked = cc.pick_domains(self.ctx(include_lead_domains=False), 200, 30)
        self.assertNotIn(domain, picked)


class PhishingParseTests(unittest.TestCase):
    def test_domain_feed_with_comments_and_duplicates(self):
        body = ("# Phishing.Database - ACTIVE domains\n"
                "Acme-Login.Phish-Test.example\n"
                "\n"
                "acme-login.phish-test.example\n"
                "verify-login.phish-test.example\n"
                "not-a-domain\n"
                "   \n"
                "# trailing comment\n")
        self.assertEqual(phishing.parse_feed(body),
                         ["acme-login.phish-test.example",
                          "verify-login.phish-test.example"])

    def test_link_feed_extracts_hosts(self):
        body = ("# links\n"
                "http://acme-login.phish-test.example/verify?id=1\n"
                "https://www.phish-test.example/a/b\n"
                "http://acme-login.phish-test.example/again\n"
                "http://user:secret@cred-test.example/go\n"
                "javascript:void(0)\n")
        self.assertEqual(phishing.parse_feed(body),
                         ["acme-login.phish-test.example", "phish-test.example",
                          "cred-test.example"])

    def test_empty_and_garbage_only(self):
        self.assertEqual(phishing.parse_feed(""), [])
        self.assertEqual(phishing.parse_feed(None), [])
        self.assertEqual(phishing.parse_feed("# only a comment\n\n"), [])
        self.assertEqual(phishing.parse_feed(b"\n\n"), [])

    def test_brand_tokens_and_matching(self):
        lead = {"id": 1, "company": "Acme Test s.r.o.", "domain": "acmetest.lswatch.test",
                "notes": ""}
        # company -> alphanumerics only, domain -> first label
        self.assertEqual(phishing.brand_tokens(lead), ["acmetestsro", "acmetest"])
        index = [(lead, phishing.brand_tokens(lead))]
        self.assertEqual(len(phishing.match_impersonations("acme-test-billing.example", index)), 1)
        self.assertEqual(len(phishing.match_impersonations("acmetest-billing.example", index)), 1)
        self.assertEqual(phishing.match_impersonations("unrelated.example", index), [])
        tiny = {"company": "Ab", "domain": ""}
        self.assertEqual(phishing.brand_tokens(tiny), [])


class PhishingSignalTests(unittest.TestCase):
    def setUp(self):
        db.init_db()
        wipe_fixtures()

    def add_lead(self):
        return db.insert("leads", {"email": "founder@lswatch.test",
                                   "company": "Acme Test s.r.o.",
                                   "domain": "acmetest.lswatch.test", "status": "new",
                                   "notes": "", "created_ts": db.now(),
                                   "updated_ts": db.now()})

    def test_a_context_stores_a_phishing_signal(self):
        # every DB fixture carries the marker so tearDownModule leaves no rows behind
        domain = "acme-login.phish.lswatch.example"
        ctx = Context(phishing.JOBS[0], {"limit": 10})
        self.assertTrue(ctx.add_signal("phishing", domain=domain, source="phishing.database",
                                       source_url=phishing.DOMAINS_URL))

        row = db.one("SELECT * FROM signals WHERE kind='phishing' AND domain=?", (domain,))
        self.assertIsNotNone(row)
        self.assertEqual(row["source"], "phishing.database")
        self.assertEqual(row["source_url"], phishing.DOMAINS_URL)
        self.assertEqual(ctx.summary()["signals"], 1)
        # the signal also lands in `domains`, which is what the enrichment jobs read
        self.assertIsNotNone(db.one("SELECT domain FROM domains WHERE domain=?", (domain,)))

    def test_new_domains_are_stored_and_impersonations_flagged(self):
        lead_id = self.add_lead()
        hit = "lswatch-acmetest-billing.example"
        plain = "lswatch-plain-billing.example"
        body = "# feed\n%s\n%s\n%s\n" % (hit, plain, hit)

        ctx = Context(phishing.JOBS[0], {"limit": 10, "scan_limit": 10})
        with mock.patch.object(net, "text", return_value=body):
            summary = phishing.run(ctx)

        self.assertTrue(summary["ok"], summary)
        self.assertEqual(summary["fetched"], 4)          # two feeds x two entries
        self.assertEqual(summary["new"], 2)
        self.assertGreaterEqual(summary["impersonations"], 1)

        signal = db.one("SELECT * FROM signals WHERE kind='phishing' AND domain=?", (hit,))
        self.assertIsNotNone(signal)
        self.assertEqual(signal["title"], "Acme Test s.r.o.")
        self.assertEqual(json.loads(signal["payload"])["title"], "Acme Test s.r.o.")

        lead = db.one("SELECT * FROM leads WHERE id=?", (lead_id,))
        self.assertIn("phishing-impersonation", lead["notes"])
        self.assertIn(hit, lead["notes"])

        quiet = db.one("SELECT * FROM signals WHERE kind='phishing' AND domain=?", (plain,))
        self.assertIsNotNone(quiet)
        self.assertEqual(quiet["title"], "")

    def test_a_second_run_only_adds_news(self):
        self.add_lead()
        body = "lswatch-acmetest-billing.example\nlswatch-plain-billing.example\n"
        with mock.patch.object(net, "text", return_value=body):
            first = phishing.run(Context(phishing.JOBS[0], {"limit": 10, "scan_limit": 10}))
            second = phishing.run(Context(phishing.JOBS[0], {"limit": 10, "scan_limit": 10}))

        self.assertEqual(first["new"], 2)
        self.assertEqual(second["new"], 0)
        self.assertEqual(second["impersonations"], 0)
        count = db.scalar("SELECT COUNT(*) FROM signals WHERE kind='phishing' AND domain LIKE ?",
                          ("%lswatch%",))
        self.assertEqual(count, 2)

    def test_dead_feeds_return_an_error_summary(self):
        def broken(url, **kw):
            raise net.NetError("HTTP 503 from raw.githubusercontent.com", status=503, url=url)

        with mock.patch.object(net, "text", side_effect=broken):
            summary = phishing.run(Context(phishing.JOBS[0], {"limit": 10}))
        self.assertFalse(summary["ok"])
        self.assertEqual(summary["new"], 0)
        self.assertIn("could not be downloaded", summary["error"])


class JobMetaTests(unittest.TestCase):
    def test_commoncrawl_job_metadata(self):
        job = cc.JOBS[0]
        self.assertEqual(job.key, "commoncrawl")
        self.assertEqual(job.name, "Common Crawl: domain footprint")
        self.assertEqual(job.group, "Enrichment")
        self.assertEqual(job.schedule, "daily 03:00")
        self.assertEqual(job.params, {"limit": 60, "days": 30, "delay": 0.4,
                                      "include_lead_domains": True})
        self.assertTrue(job.description)
        self.assertTrue(callable(job.run))

    def test_phishing_job_metadata(self):
        job = phishing.JOBS[0]
        self.assertEqual(job.key, "phishing_watch")
        self.assertEqual(job.name, "Fraud watch: phishing domains")
        self.assertEqual(job.group, "Signals")
        self.assertEqual(job.schedule, "daily 05:30")
        self.assertEqual(job.params["sources"],
                         ["https://raw.githubusercontent.com/Phishing-Database/"
                          "Phishing.Database/master/phishing-domains-ACTIVE.txt",
                          "https://raw.githubusercontent.com/Phishing-Database/"
                          "Phishing.Database/master/phishing-links-ACTIVE.txt"])
        self.assertEqual(job.params["limit"], 2000)
        self.assertEqual(job.params["scan_limit"], 500)
        self.assertTrue(callable(job.run))

    def test_registry_lists_both_jobs(self):
        keys = {job.key for job in jobs.all_jobs()}
        self.assertIn("commoncrawl", keys)
        self.assertIn("phishing_watch", keys)


if __name__ == "__main__":
    unittest.main(verbosity=2)
