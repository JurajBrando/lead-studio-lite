"""One WARC record out of the archive: fetch by range, parse, extract contacts."""
import datetime
import gzip
import os
import sys
import tempfile
import unittest

import pyarrow as pa
import pyarrow.parquet as pq

if not os.environ.get("LS_DATA"):
    os.environ["LS_DATA"] = tempfile.mkdtemp(prefix="ls_ccwarc_")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine import cc_index, cc_warc, db  # noqa: E402

HTML = ("<html><head><title>Northwind Roofing | Contact</title>"
        "<meta name='description' content='Roof repairs in Leeds'></head>"
        "<body><p>Call us on +44 113 496 0123 or email sales@northwind-roofing.example</p>"
        "<p>Legal: legal@northwind-roofing.example</p></body></html>")


def warc_record(url="https://northwind-roofing.example/contact",
                html=HTML, status=200, gzipped=True):
    http = ("HTTP/1.1 %d OK\r\nContent-Type: text/html; charset=utf-8\r\n"
            "Content-Length: %d\r\n\r\n%s" % (status, len(html), html))
    record = ("WARC/1.0\r\nWARC-Type: response\r\nWARC-Target-URI: %s\r\n"
              "WARC-Date: 2026-05-01T10:00:00Z\r\n\r\n%s" % (url, http)).encode("utf-8")
    return gzip.compress(record) if gzipped else record


class ParseTests(unittest.TestCase):
    def test_a_gzipped_record_yields_url_status_and_html(self):
        parsed = cc_warc.parse_record(warc_record())
        self.assertTrue(parsed["ok"], parsed)
        self.assertEqual(parsed["url"], "https://northwind-roofing.example/contact")
        self.assertEqual(parsed["status"], 200)
        self.assertIn("northwind-roofing.example", parsed["html"])

    def test_an_uncompressed_record_works_too(self):
        parsed = cc_warc.parse_record(warc_record(gzipped=False))
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["status"], 200)

    def test_something_that_is_not_a_record_is_reported(self):
        parsed = cc_warc.parse_record(b"<html>nope</html>")
        self.assertFalse(parsed["ok"])

    def test_contacts_are_extracted_and_deduplicated(self):
        info = cc_warc.contacts_of(HTML, "northwind-roofing.example")
        self.assertIn("sales@northwind-roofing.example", info["emails"])
        self.assertIn("legal@northwind-roofing.example", info["emails"])
        self.assertIn("Northwind Roofing", info["title"])
        self.assertIn("Roof repairs in Leeds", info["description"])

    def test_contact_pages_rank_before_the_homepage(self):
        self.assertEqual(cc_warc.page_score("https://x.example/contact"), 0)
        self.assertEqual(cc_warc.page_score("https://x.example/about-us"), 0)
        self.assertEqual(cc_warc.page_score("https://x.example/"), 1)
        self.assertEqual(cc_warc.page_score("https://x.example/blog/post-1"), 2)


class PagesTests(unittest.TestCase):
    def setUp(self):
        db.init_db()
        self.tmp = tempfile.mkdtemp(prefix="ccwarc_shard_")
        path = os.path.join(self.tmp, "part-00000.parquet")
        schema = pa.schema([
            ("url", pa.string()),
            ("url_host_registered_domain", pa.string()),
            ("fetch_time", pa.timestamp("ms", tz="UTC")),
            ("fetch_status", pa.int16()),
            ("warc_filename", pa.string()),
            ("warc_record_offset", pa.int32()),
            ("warc_record_length", pa.int32()),
        ])
        moment = datetime.datetime(2026, 5, 1, tzinfo=datetime.timezone.utc)
        table = pa.table({
            "url": ["https://northwind-roofing.example/blog/1",
                    "https://northwind-roofing.example/contact",
                    "https://northwind-roofing.example/",
                    "https://other.example/contact",
                    "https://northwind-roofing.example/missing"],
            "url_host_registered_domain": ["northwind-roofing.example"] * 3 + ["other.example"] + ["northwind-roofing.example"],
            "fetch_time": [moment] * 5,
            "fetch_status": [200, 200, 200, 200, 404],
            "warc_filename": ["crawl-data/x.warc.gz"] * 5,
            "warc_record_offset": [100, 200, 300, 400, 500],
            "warc_record_length": [10, 20, 30, 40, 50],
        }, schema=schema)
        pq.write_table(table, path)

    def test_only_this_domain_and_only_successful_pages(self):
        pages = cc_index.pages("northwind-roofing.example", local_dir=self.tmp, max_pages=5)
        urls = [page["url"] for page in pages]
        self.assertEqual(len(urls), 3)
        self.assertTrue(all("northwind-roofing.example" in url for url in urls))
        self.assertNotIn("https://northwind-roofing.example/missing", urls)

    def test_the_contact_page_comes_first(self):
        pages = cc_index.pages("northwind-roofing.example", local_dir=self.tmp, max_pages=5)
        self.assertTrue(pages[0]["url"].endswith("/contact"))

    def test_the_warc_coordinates_come_along(self):
        pages = cc_index.pages("northwind-roofing.example", local_dir=self.tmp, max_pages=1)
        self.assertEqual(pages[0]["warc_filename"], "crawl-data/x.warc.gz")
        self.assertEqual(pages[0]["offset"], 200)
        self.assertEqual(pages[0]["length"], 20)

    def test_a_domain_that_is_not_there_has_no_pages(self):
        self.assertEqual(cc_index.pages("nothing.example", local_dir=self.tmp), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
