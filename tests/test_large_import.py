"""The import must not be capped, and must not eat gigabytes deduplicating 62M rows."""
import os
import sys
import tempfile
import unittest

if not os.environ.get("LS_DATA"):
    os.environ["LS_DATA"] = tempfile.mkdtemp(prefix="ls_large_")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine import db, importer  # noqa: E402


class LargeImportTests(unittest.TestCase):
    def setUp(self):
        db.init_db()
        db.execute("DELETE FROM leads WHERE email LIKE '%large-import.example'")

    def tearDown(self):
        db.execute("DELETE FROM leads WHERE email LIKE '%large-import.example'")

    def rows(self, count=3):
        return [{"email": "user%d@large-import.example" % index, "company": "Large %d" % index}
                for index in range(count)]

    def test_a_huge_limit_is_not_refused_or_capped(self):
        answer = importer.import_rows("big.csv", ["email", "company"], self.rows(3),
                                      limit=80000000, total=62759990)
        self.assertTrue(answer["ok"], answer)
        self.assertFalse(answer["stopped_at_limit"])
        self.assertEqual(answer["imported"], 3)

    def test_the_in_file_duplicate_set_is_skipped_on_a_huge_file(self):
        answer = importer.import_rows("big.csv", ["email", "company"], self.rows(3),
                                      limit=80000000, total=62759990)
        self.assertTrue(answer["duplicate_check_skipped"])

    def test_a_normal_file_still_deduplicates_in_memory(self):
        rows = self.rows(2) + self.rows(2)          # the same two people twice
        answer = importer.import_rows("small.csv", ["email", "company"], rows, limit=5000,
                                      total=4)
        self.assertFalse(answer["duplicate_check_skipped"])
        self.assertEqual(answer["duplicates"], 2)

    def test_the_duplicate_email_index_still_protects_a_rerun(self):
        first = importer.import_rows("big.csv", ["email", "company"], self.rows(3),
                                     limit=80000000, total=62759990)
        self.assertEqual(first["imported"], 3)
        second = importer.import_rows("big.csv", ["email", "company"], self.rows(3),
                                      limit=80000000, total=62759990)
        self.assertEqual(second["imported"], 0)
        self.assertEqual(second["existing_email"], 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
