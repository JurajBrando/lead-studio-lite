"""The Daily page runs domains first, then signals, then data, and the AI last."""
import os
import sys
import tempfile
import unittest

if not os.environ.get("LS_DATA"):
    os.environ["LS_DATA"] = tempfile.mkdtemp(prefix="ls_jobs_")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine import db, scheduler  # noqa: E402


class JobOrderTests(unittest.TestCase):
    def setUp(self):
        db.init_db()
        self.jobs = scheduler.list_jobs()

    def keys(self):
        return [job["key"] for job in self.jobs]

    def test_domains_come_before_signals_before_data_before_ai(self):
        groups = [job["grp"] for job in self.jobs]
        self.assertEqual(groups[0], "Domains")
        self.assertEqual(groups[-1], "AI")
        self.assertLess(groups.index("Domains"), groups.index("Signals"))
        self.assertLess(groups.index("Signals"), groups.index("Data"))
        self.assertLess(groups.index("Data"), groups.index("AI"))

    def test_the_domain_chain_is_in_order(self):
        keys = self.keys()
        self.assertLess(keys.index("new_domains"), keys.index("domain_mx"))
        self.assertLess(keys.index("domain_mx"), keys.index("commoncrawl"))
        self.assertLess(keys.index("commoncrawl"), keys.index("contact_finder"))

    def test_the_ai_job_is_the_very_last_one(self):
        self.assertEqual(self.jobs[-1]["key"], "ai_enrich")

    def test_every_group_is_known(self):
        for job in self.jobs:
            self.assertIn(job["grp"], scheduler.GROUP_ORDER)

    def test_the_ai_job_stays_optional(self):
        ai = next(job for job in self.jobs if job["key"] == "ai_enrich")
        self.assertIn("provider", (ai.get("description") or "").lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
