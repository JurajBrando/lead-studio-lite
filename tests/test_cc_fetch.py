"""The batched fetcher: bucket routing, one pool, and the resume table."""
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

if not os.environ.get("LS_DATA"):
    os.environ["LS_DATA"] = tempfile.mkdtemp(prefix="ls_ccfetch_")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine import cc_fetch, db  # noqa: E402


class FakeResponse:
    status = 206

    def __init__(self, body=b"payload"):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class TargetTests(unittest.TestCase):
    def test_a_bucket_path_uses_the_commoncrawl_bucket(self):
        target = cc_fetch._target("/crawl-data/CC-MAIN-2026-17/x.warc.gz", "bucket")
        self.assertTrue(target.startswith("/buckets/commoncrawl/commoncrawl/resolve/"))
        self.assertIn("crawl-data/CC-MAIN-2026-17/x.warc.gz", target)

    def test_an_http_path_stays_a_plain_path(self):
        self.assertEqual(cc_fetch._target("crawl-data/x.warc.gz", "http"),
                         "/crawl-data/x.warc.gz")


class FetchRangeTests(unittest.TestCase):
    def test_the_bucket_source_follows_the_signed_url(self):
        seen = {}

        def fake(request, timeout=0):
            seen["url"] = request.full_url
            seen["range"] = request.get_header("Range")
            return FakeResponse(b"abc")

        with mock.patch.object(cc_fetch.urllib.request, "urlopen", side_effect=fake):
            data = cc_fetch.fetch_range("crawl-data/x.warc.gz", 100, 50, source="bucket")
        self.assertEqual(data, b"abc")
        self.assertTrue(seen["url"].startswith("https://huggingface.co/buckets/commoncrawl/commoncrawl/resolve/"))
        self.assertEqual(seen["range"], "bytes=100-149")


class RunTests(unittest.TestCase):
    def test_the_whole_plan_runs_in_parallel_not_one_group_at_a_time(self):
        plan = [("a.example", "h", "https://a.example/", "file-%d.warc.gz" % i, 0, 10)
                for i in range(6)]
        seen_threads = set()
        gate = threading.Barrier(6, timeout=5)

        def fake_fetch(row, timeout=60, source="http"):
            seen_threads.add(threading.current_thread().name)
            try:
                gate.wait()
            except threading.BrokenBarrierError:
                pass
            return {"rkey": cc_fetch.rkey(row[0], row[1]), "domain": row[0], "kind": row[1],
                    "url": row[2], "status": "OK", "emails": [], "phones": [], "title": ""}

        with mock.patch.object(cc_fetch, "_fetch_one", side_effect=fake_fetch):
            started = time.time()
            results = cc_fetch.run(plan, parallel=6)
        self.assertEqual(len(results), 6)
        self.assertEqual(len(seen_threads), 6)
        self.assertLess(time.time() - started, 5)

    def test_a_failed_page_is_a_result_not_an_exception(self):
        plan = [("b.example", "h", "https://b.example/", "x.warc.gz", 0, 10)]

        def boom(path, offset, length, **kwargs):
            raise OSError("connection reset")

        with mock.patch.object(cc_fetch, "fetch_range", side_effect=boom):
            results = cc_fetch.run(plan, parallel=2)
        self.assertIn("FAILED", results[0]["status"])

    def test_the_resume_table_remembers_finished_records(self):
        db.init_db()
        cc_fetch.ensure_table()
        db.execute("DELETE FROM cc_fetch WHERE domain='resume.example'")
        results = [{"rkey": cc_fetch.rkey("resume.example", "h"), "domain": "resume.example",
                    "kind": "h", "url": "https://resume.example/", "status": "OK",
                    "emails": ["a@resume.example"], "phones": [], "title": "Resume"}]
        db.execute("INSERT OR REPLACE INTO domains (domain, tld, first_seen_ts, updated_ts) "
                   "VALUES ('resume.example','example',0,0)")
        summary = cc_fetch.save(results)
        self.assertEqual(summary["leads"], 1)
        self.assertIn(cc_fetch.rkey("resume.example", "h"), cc_fetch.done_keys())
        db.execute("DELETE FROM cc_fetch WHERE domain='resume.example'")
        db.execute("DELETE FROM leads WHERE domain='resume.example'")
        db.execute("DELETE FROM domains WHERE domain='resume.example'")


if __name__ == "__main__":
    unittest.main(verbosity=2)
