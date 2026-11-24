"""The three jobs that call the older tooling: the report parser, and what happens without it."""
import os
import sys
import tempfile
import time
import unittest

if not os.environ.get("LS_DATA"):
    os.environ["LS_DATA"] = tempfile.mkdtemp(prefix="ls_external_")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine import external  # noqa: E402
from engine.jobs import hot_list, osm_business, pipeline_run  # noqa: E402

REPORT = """Lead Studio daily pipeline - 2026-11-14
============================================

stages
  nrd     ok                     3s
  fraud   ok                     12s

new domains      310557 pulled, 310557 new to the index
fraud listings   391679 newly listed, 117 brand hits

handoffs
  Modulario outreach   C:\\Users\\admin\\Desktop\\Modulario\\LEAD_STUDIO\\NRD\\out\\2026-11-14\\sk_cz.csv  (0 rows)
  Manyreach / forms    C:\\Users\\admin\\Desktop\\Modulario\\LEAD_STUDIO\\BRAND\\out\\2026-11-14\\redpoints_cases.csv  (23 rows)
  early warning        C:\\Users\\admin\\Desktop\\Modulario\\LEAD_STUDIO\\RADAR\\out\\2026-11-14\\scam_candidates.csv  (53204 rows)
  ads transparency     C:\\Users\\admin\\Desktop\\Modulario\\LEAD_STUDIO\\ADS_TRANSPARENCY\\out\\2026_W45\\all_ads_leads.csv  (3546070 rows)
"""


class HandoffTests(unittest.TestCase):
    def test_the_four_handoffs_are_parsed_with_their_rows(self):
        handoffs = external.parse_handoffs(REPORT)
        self.assertEqual(len(handoffs), 4)
        labels = [label for label, _, _ in handoffs]
        self.assertIn("Modulario outreach", labels)
        self.assertIn("Manyreach / forms", labels)
        rows = {label: count for label, _, count in handoffs}
        self.assertEqual(rows["Manyreach / forms"], 23)
        self.assertEqual(rows["ads transparency"], 3546070)
        self.assertEqual(rows["Modulario outreach"], 0)

    def test_a_report_without_a_handoff_section_gives_nothing(self):
        self.assertEqual(external.parse_handoffs("stages\n  nrd ok\n"), [])

    def test_a_file_without_rows_still_parses(self):
        text = ("handoffs\n"
                "  something   C:\\x\\y.csv\n")
        handoffs = external.parse_handoffs(text)
        self.assertEqual(handoffs[0][2], 0)


class FileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ls_external_files_")

    def test_newest_picks_the_most_recent_file(self):
        older = os.path.join(self.tmp, "hot_2026-11-13.csv")
        newer = os.path.join(self.tmp, "hot_2026-11-14.csv")
        for path, when in ((older, time.time() - 100), (newer, time.time())):
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("id\n1\n")
            os.utime(path, (when, when))
        self.assertEqual(os.path.basename(external.newest(self.tmp, "hot_*.csv")),
                         "hot_2026-11-14.csv")

    def test_row_count_ignores_the_header(self):
        path = os.path.join(self.tmp, "rows.csv")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("email,company\na@x.com,A\nb@x.com,B\n")
        self.assertEqual(external.row_count(path), 2)
        self.assertEqual(external.row_count(os.path.join(self.tmp, "missing.csv")), 0)

    def test_a_missing_folder_is_named(self):
        with self.assertRaises(ValueError) as ctx:
            external.require(os.path.join(self.tmp, "nope"), "daily_pipeline.py")
        self.assertIn("not there", str(ctx.exception))


class RunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ls_external_run_")

    def test_a_script_runs_and_its_tail_is_kept(self):
        with open(os.path.join(self.tmp, "ok.py"), "w", encoding="utf-8") as handle:
            handle.write("print('hello from the script')\n")
        result = external.run(self.tmp, "ok.py")
        self.assertTrue(result["ok"], result)
        self.assertIn("hello from the script", result["tail"])
        self.assertGreaterEqual(result["seconds"], 0)

    def test_a_failing_script_is_not_an_exception(self):
        with open(os.path.join(self.tmp, "bad.py"), "w", encoding="utf-8") as handle:
            handle.write("import sys\nprint('boom')\nsys.exit(3)\n")
        result = external.run(self.tmp, "bad.py")
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], 3)
        self.assertIn("boom", result["tail"])


class MissingFolderTests(unittest.TestCase):
    """Each job says which folder is missing instead of raising."""

    def run_job(self, job, params=None):
        ctx = type("Ctx", (), {
            "_params": params or {},
            "logs": [],
            "stats": {"signals": 0, "domains": 0, "leads": 0, "warnings": 0},
            "param": lambda self, name, default=None: self._params.get(name, default),
            "int_param": lambda self, name, default=0: int(self._params.get(name, default) or 0),
            "log": lambda self, message: self.logs.append(message),
            "warn": lambda self, message: self.logs.append(message),
            "summary": lambda self, **kwargs: kwargs,
            "add_signal": lambda self, *a, **k: True,
        })()
        return job(ctx)

    def test_pipeline_without_its_folder(self):
        original = external.DEFAULTS["studio"]
        external.DEFAULTS["studio"] = os.path.join(tempfile.gettempdir(), "no-pipeline-here")
        try:
            answer = self.run_job(pipeline_run.run)
        finally:
            external.DEFAULTS["studio"] = original
        self.assertFalse(answer["ok"])
        self.assertIn("not there", answer["error"])

    def test_osm_refuses_an_empty_search(self):
        answer = self.run_job(osm_business.run, {"text": "   "})
        self.assertFalse(answer["ok"])
        self.assertIn("Type what to look for", answer["error"])

    def test_hot_list_is_native_and_needs_no_folder(self):
        answer = self.run_job(hot_list.run, {"days": 2, "limit": 20})
        self.assertTrue(answer["ok"], answer)
        self.assertIn("rows", answer)


if __name__ == "__main__":
    unittest.main(verbosity=2)
