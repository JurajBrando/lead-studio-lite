"""Progress for a background run: percent, rate and an estimate of what is left."""
import os
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine import progress  # noqa: E402


class ProgressTests(unittest.TestCase):
    def test_a_run_reports_percent_and_an_estimate(self):
        run_id = progress.start("big.csv")
        progress.update(run_id, 0, 1000, "reading the file")
        snapshot = progress.snapshot(run_id)
        self.assertTrue(snapshot["ok"])
        self.assertEqual(snapshot["percent"], 0)
        self.assertEqual(snapshot["phase"], "reading the file")

        time.sleep(0.05)
        progress.update(run_id, 500, 1000, "importing", 420)
        snapshot = progress.snapshot(run_id)
        self.assertEqual(snapshot["percent"], 50)
        self.assertEqual(snapshot["rows"], 420)
        self.assertGreater(snapshot["rate"], 0)
        self.assertGreater(snapshot["eta"], 0)
        self.assertFalse(snapshot["finished"])

    def test_finishing_carries_the_result(self):
        run_id = progress.start("small.csv")
        progress.update(run_id, 10, 10, "importing")
        progress.finish(run_id, result={"ok": True, "imported": 10})
        snapshot = progress.snapshot(run_id)
        self.assertTrue(snapshot["finished"])
        self.assertEqual(snapshot["percent"], 100)
        self.assertEqual(snapshot["result"]["imported"], 10)
        self.assertEqual(snapshot["error"], "")

    def test_a_failure_is_reported_not_swallowed(self):
        run_id = progress.start("bad.csv")
        progress.finish(run_id, error="no header row")
        snapshot = progress.snapshot(run_id)
        self.assertTrue(snapshot["finished"])
        self.assertEqual(snapshot["phase"], "failed")
        self.assertIn("header", snapshot["error"])

    def test_an_unknown_id_says_so(self):
        self.assertFalse(progress.snapshot("nope")["ok"])

    def test_updating_after_the_end_is_ignored(self):
        run_id = progress.start("done.csv")
        progress.finish(run_id, result={"ok": True})
        progress.update(run_id, 50, 100, "late")
        self.assertEqual(progress.snapshot(run_id)["percent"], 100)

    def test_an_unknown_total_does_not_divide_by_zero(self):
        run_id = progress.start("stream.parquet")
        progress.update(run_id, 1200, 0, "reading the file", 1200)
        snapshot = progress.snapshot(run_id)
        self.assertEqual(snapshot["percent"], 0)
        self.assertEqual(snapshot["eta"], 0)
        self.assertEqual(snapshot["read_rows"], 1200)
        self.assertEqual(snapshot["rows"], 0)

    def test_a_streamed_import_shows_the_reader_percent(self):
        run_id = progress.start("big.csv")
        progress.update(run_id, 250, 1000, "reading the file", 250)
        progress.update(run_id, 200, 0, "importing", 200)   # no row total known
        snapshot = progress.snapshot(run_id)
        self.assertEqual(snapshot["percent"], 25)
        self.assertEqual(snapshot["total"], 0, "bytes must not be shown as a row total")
        self.assertGreater(snapshot["eta"], 0)

    def test_cancelling_marks_the_run_and_keeps_its_counts(self):
        run_id = progress.start("big.csv")
        progress.update(run_id, 10, 100, "importing", 5)
        self.assertTrue(progress.request_cancel(run_id))
        snapshot = progress.snapshot(run_id)
        self.assertTrue(snapshot["cancel"])
        self.assertEqual(snapshot["phase"], "stopping")
        self.assertEqual(snapshot["imported_rows"], 5)
        self.assertTrue(progress.is_cancelled(run_id))
        progress.finish(run_id, result={"ok": True, "cancelled": True}, cancelled=True)
        snapshot = progress.snapshot(run_id)
        self.assertEqual(snapshot["phase"], "cancelled")
        self.assertTrue(snapshot["finished"])
        self.assertEqual(snapshot["error"], "")

    def test_cancelling_a_finished_run_is_refused(self):
        run_id = progress.start("done.csv")
        progress.finish(run_id, result={"ok": True})
        self.assertFalse(progress.request_cancel(run_id))


if __name__ == "__main__":
    unittest.main(verbosity=2)
