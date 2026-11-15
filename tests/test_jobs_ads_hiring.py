"""Tests for the two signal jobs: ads transparency + hiring postings.

No network is used: both network-facing code paths are exercised through the
pure parsers and through `net.set_offline()`, which makes every `net` call
raise `NetError` so the `run()` fallbacks can be checked.

Run:  python -m unittest discover -s tests -v
"""
import csv
import io
import json
import os
import sys
import tempfile
import unittest

# Same pattern as tests/test_app.py: point LS_DATA at a throwaway folder
# *before* importing engine, so the SQLite file never lands in the app folder.
TMP = tempfile.mkdtemp(prefix="ls_jobs_")
os.environ["LS_DATA"] = TMP

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine import db, net  # noqa: E402
from engine.jobs import ads as ads_job  # noqa: E402
from engine.jobs import hiring as hiring_job  # noqa: E402
from engine.jobs.base import Context  # noqa: E402

ADS_HEADER = (
    "advertiser_id,advertiser_disclosed_name,advertiser_legal_name,advertiser_location,"
    "distinct_verification_statuses,verification_status_count,distinct_ad_format_types,"
    "ad_format_type_count,is_funded_by_google_ad_grants,funded_by_entities,"
    "is_third_party_funded,unique_creatives,first_ad_shown,last_ad_shown,"
    "advertiser_first_seen_date,advertiser_age_days,sum_times_shown_lower_bound,"
    "sum_times_shown_upper_bound,total_impressions_midpoint,avg_times_shown_midpoint,"
    "max_times_shown_upper_bound,min_times_shown_lower_bound,regions_count,"
    "distinct_regions,distinct_surfaces_count,distinct_surfaces,surfaces_missing_flag,"
    "sample_creative_page_url"
)

# 28 columns each; built from explicit field lists so the width stays auditable.
ADS_ROW_1 = ",".join([
    "AR001", "Acme Marketing GmbH", "Acme Marketing GmbH", '"Berlin, DE"',
    "VERIFIED", "1", "TEXT", "1", "true", "", "false", "42",
    "2026-01-05", "2026-02-01", "2026-01-01", "31",
    "120000", "180000", "150000", "12.5", "300", "5",
    "2", "DE|AT", "2", "SEARCH|DISPLAY", "false",
    "https://adstransparency.google.com/advertiser/AR001",
])

# blank impressions midpoint, funded through the third-party flag only
ADS_ROW_2 = ",".join([
    "AR002", "Northwind Dental Clinic", "Northwind Dental LLC", '"Austin, US"',
    "VERIFIED", "1", "VIDEO", "1", "false", "", "True", "7",
    "2026-02-10", "2026-03-01", "2026-02-01", "28",
    "", "", "", "", "", "",                     # 6 blank numeric columns
    "1", "US", "1", "VIDEO", "false",
    "https://adstransparency.google.com/advertiser/AR002",
])

# non-numeric impressions midpoint and no creative URL
ADS_ROW_3 = ",".join([
    "AR003", "Casa Verde Solar SL", "Casa Verde Solar SL", '"Lisbon, PT"',
    "UNVERIFIED", "0", "TEXT|IMAGE", "2", "false", "Casa Verde Holdings",
    "false", "3", "2026-03-01", "2026-03-05", "2026-03-01", "5",
    "0", "0", "n/a", "0", "0", "0", "1", "PT", "2", "TEXT|IMAGE", "true", "",
])

ADS_CSV = "\n".join([ADS_HEADER, ADS_ROW_1, ADS_ROW_2, ADS_ROW_3]) + "\n"

HIRING_HEADER = "source,company,title,location,department,url,posted_at,updated_at,id"
HIRING_CSV = "\n".join([
    HIRING_HEADER,
    "greenhouse,Acme Inc,Growth Marketer,Remote,Marketing,"
    "https://boards.example.com/acme/1,2026-11-14T10:00:00Z,2026-11-15T05:00:00Z,j1",
    "lever,Staffing Recruiters,Data Engineer,New York,Engineering,"
    "https://jobs.example.com/2,2026-11-14,2026-11-15,j2",
    "ashby,Northwind Dental,SDR,London,Sales,,2026-11-14,2026-11-15,j3",
    "greenhouse,No Title Co,,Remote,,https://boards.example.com/x/4,2026-11-14,2026-11-15,j4",
    "greenhouse,,Marketing Lead,Remote,,https://boards.example.com/x/5,2026-11-14,2026-11-15,j5",
]) + "\n"


class AdsParserTests(unittest.TestCase):
    def setUp(self):
        self.records = ads_job.parse_advertisers(ADS_CSV)

    def test_fixture_has_28_columns(self):
        rows = list(csv.reader(io.StringIO(ADS_CSV)))
        self.assertEqual(len(rows[0]), 28)
        for row in rows[1:]:
            self.assertEqual(len(row), 28)

    def test_rows_and_public_keys(self):
        self.assertEqual(len(self.records), 3)
        for record in self.records:
            self.assertEqual(set(record), set(ads_job.ADVERTISER_KEYS))

    def test_fields_are_normalised(self):
        first = self.records[0]
        self.assertEqual(first["advertiser_id"], "AR001")
        self.assertEqual(first["name"], "Acme Marketing GmbH")
        self.assertEqual(first["location"], "Berlin, DE")
        self.assertEqual(first["regions"], "DE|AT")
        self.assertEqual(first["surfaces"], "SEARCH|DISPLAY")
        self.assertEqual(first["impressions"], 150000)
        self.assertEqual(first["first_ad_shown"], "2026-01-05")
        self.assertEqual(first["last_ad_shown"], "2026-02-01")
        self.assertTrue(first["page_url"].endswith("/advertiser/AR001"))
        self.assertTrue(first["funded"])

    def test_blank_and_non_numeric_impressions_become_zero(self):
        self.assertEqual(self.records[1]["impressions"], 0)
        self.assertEqual(self.records[2]["impressions"], 0)

    def test_funded_uses_either_flag(self):
        self.assertTrue(self.records[1]["funded"])          # is_third_party_funded
        self.assertFalse(self.records[2]["funded"])

    def test_legal_name_is_available_for_matching(self):
        full = ads_job.parse_advertiser_records(ADS_CSV)
        self.assertEqual(full[1]["legal_name"], "Northwind Dental LLC")
        self.assertEqual(full[1]["name"], "Northwind Dental Clinic")

    def test_empty_csv_is_empty_list(self):
        self.assertEqual(ads_job.parse_advertisers(""), [])
        self.assertEqual(ads_job.parse_advertisers("a,b\n"), [])

    def test_utf8_bom_is_ignored(self):
        self.assertEqual(len(ads_job.parse_advertisers("\ufeff" + ADS_CSV)), 3)

    def test_keywords_match_disclosed_or_legal_name(self):
        self.assertTrue(ads_job.matches_keywords(("Acme Marketing GmbH", ""), ["marketing"]))
        self.assertFalse(ads_job.matches_keywords(("Acme Marketing GmbH", ""), ["dental"]))
        self.assertTrue(ads_job.matches_keywords(("", "Acme Legal Ltd"), ["legal"]))
        self.assertTrue(ads_job.matches_keywords(("anything",), []))
        self.assertFalse(ads_job.matches_keywords(("",), ["marketing"]))

    def test_region_matching(self):
        self.assertTrue(ads_job.matches_regions("DE|AT", ["DE"]))
        self.assertFalse(ads_job.matches_regions("US", ["de"]))
        self.assertTrue(ads_job.matches_regions("United States,Germany", ["united states"]))
        self.assertTrue(ads_job.matches_regions("US", []))
        self.assertFalse(ads_job.matches_regions("", ["DE"]))

    def test_select_latest_week_ignores_monthly_and_older_weeks(self):
        listing = [
            "data/monthly/Google_Ads_Transparency_Report_November_Aggregation_part1.csv.gz",
            "data/weekly/Google_Ads_Transparency_Report_October_Week_4_Aggregation_part1.csv.gz",
            "data/weekly/Google_Ads_Transparency_Report_November_Week_1_Aggregation_part1.csv.gz",
            "data/weekly/Google_Ads_Transparency_Report_November_Week_1_Aggregation_part2.csv.gz",
            "data/weekly/Google_Ads_Transparency_Report_November_Week_3_Aggregation_part1.csv.gz",
            "data/weekly/Google_Ads_Transparency_Report_November_Week_3_Aggregation_part2.csv.gz",
            ".gitattributes",
        ]
        latest = ads_job.select_latest_week(listing)
        self.assertIsNotNone(latest)
        self.assertEqual(latest["week"], 3)
        self.assertEqual(latest["month"], "November")
        self.assertEqual(latest["label"], "November_Week_3")
        self.assertEqual(sorted(latest["parts"]), [1, 2])

    def test_select_latest_week_tolerates_missing_part(self):
        listing = [
            "data/weekly/Google_Ads_Transparency_Report_September_Week_4_Aggregation_part1.csv.gz",
            "data/weekly/Google_Ads_Transparency_Report_September_Week_4_Aggregation_part2.csv.gz",
            "data/weekly/Google_Ads_Transparency_Report_October_Week_5_Aggregation_part2.csv.gz",
        ]
        latest = ads_job.select_latest_week(listing)
        self.assertEqual(latest["label"], "October_Week_5")
        self.assertEqual(sorted(latest["parts"]), [2])

    def test_select_latest_week_without_weeklies(self):
        self.assertIsNone(ads_job.select_latest_week([]))
        self.assertIsNone(ads_job.select_latest_week(
            ["data/monthly/Google_Ads_Transparency_Report_August_Aggregation_part1.csv.gz"]))

    def test_resolve_url_quotes_the_path(self):
        self.assertEqual(
            ads_job.resolve_url("data/weekly/a b.csv.gz"),
            "https://huggingface.co/datasets/najeh-halawani/google-ads-transparency"
            "/resolve/main/data/weekly/a%20b.csv.gz")


class HiringParserTests(unittest.TestCase):
    def setUp(self):
        self.postings = hiring_job.parse_postings(HIRING_CSV)

    def test_fixture_has_9_columns(self):
        rows = list(csv.reader(io.StringIO(HIRING_CSV)))
        self.assertEqual(len(rows[0]), 9)
        for row in rows[1:]:
            self.assertEqual(len(row), 9)

    def test_skips_rows_without_company_or_title(self):
        self.assertEqual(len(self.postings), 3)
        self.assertEqual([p["id"] for p in self.postings], ["j1", "j2", "j3"])
        for posting in self.postings:
            self.assertTrue(posting["company"])
            self.assertTrue(posting["title"])

    def test_utf8_bom_is_ignored(self):
        self.assertEqual(len(hiring_job.parse_postings("\ufeff" + HIRING_CSV)), 3)

    def test_fields(self):
        first = self.postings[0]
        self.assertEqual(first["source"], "greenhouse")
        self.assertEqual(first["company"], "Acme Inc")
        self.assertEqual(first["title"], "Growth Marketer")
        self.assertEqual(first["location"], "Remote")
        self.assertEqual(first["department"], "Marketing")
        self.assertEqual(first["url"], "https://boards.example.com/acme/1")
        self.assertEqual(first["posted_at"], "2026-11-14T10:00:00Z")

    def test_title_matching(self):
        self.assertTrue(hiring_job.matches_titles("Growth Marketer", ["growth"]))
        self.assertTrue(hiring_job.matches_titles("Senior SDR (EMEA)", ["sdr"]))
        self.assertFalse(hiring_job.matches_titles("Accountant", ["marketing", "data"]))
        self.assertTrue(hiring_job.matches_titles("Anything", []))
        self.assertFalse(hiring_job.matches_titles("", ["data"]))

    def test_ignore_companies(self):
        defaults = hiring_job.DEFAULT_IGNORE_COMPANIES
        self.assertTrue(hiring_job.company_ignored("Staffing Recruiters", defaults))
        self.assertTrue(hiring_job.company_ignored("Talent Bridge GmbH", defaults))
        self.assertFalse(hiring_job.company_ignored("Acme Inc", defaults))

    def test_snapshot_url_and_dates(self):
        self.assertEqual(hiring_job.snapshot_url("2026-11-15"),
                         "https://huggingface.co/datasets/cleanfeeds/tech-jobs-daily"
                         "/resolve/main/data/jobs-2026-11-15.csv")
        days = hiring_job.candidate_dates()
        self.assertEqual(len(days), 3)
        self.assertEqual((days[0] - days[2]).days, 2)
        self.assertGreater(days[0], days[1])


class JobRegistryTests(unittest.TestCase):
    def test_ads_job_metadata(self):
        jobs = ads_job.JOBS
        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job.key, "ads_transparency")
        self.assertEqual(job.name, "Google Ads Transparency: weekly advertisers")
        self.assertEqual(job.group, "Signals")
        self.assertEqual(job.schedule, "weekly mon 07:30")
        self.assertTrue(job.needs_network)
        self.assertEqual(job.params["min_impressions"], 10000)
        self.assertEqual(job.params["limit"], 500)
        self.assertEqual(job.params["regions"], [])
        self.assertIn("marketing", job.params["keywords"])
        self.assertIn("real estate", job.params["keywords"])

    def test_hiring_job_metadata(self):
        jobs = hiring_job.JOBS
        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job.key, "hiring_signals")
        self.assertEqual(job.group, "Signals")
        self.assertEqual(job.schedule, "daily 06:45")
        self.assertEqual(job.params["limit"], 400)
        self.assertEqual(job.params["ignore_companies"], ["recruit", "staffing", "talent"])
        self.assertIn("demand gen", job.params["titles"])
        self.assertIn("revops", job.params["titles"])


class ContextSignalTests(unittest.TestCase):
    """The jobs write through Context.add_signal; check the real SQLite path."""

    def setUp(self):
        db.init_db()
        self.cleanup()

    def tearDown(self):
        self.cleanup()

    @staticmethod
    def cleanup():
        db.execute("DELETE FROM signals WHERE source IN (?,?)", ("test-ads", "test-hiring"))

    def test_context_accepts_ads_signal(self):
        ctx = Context(ads_job.JOBS[0])
        created = ctx.add_signal(
            "ads", company="Acme Marketing GmbH", source="test-ads",
            source_url="https://example.test/advertiser/AR001", title="SEARCH|DISPLAY",
            payload={"advertiser_id": "AR001", "impressions": 150000})
        self.assertTrue(created)
        row = db.one("SELECT * FROM signals WHERE source='test-ads'")
        self.assertIsNotNone(row)
        self.assertEqual(row["kind"], "ads")
        self.assertEqual(row["company"], "Acme Marketing GmbH")
        self.assertEqual(row["title"], "SEARCH|DISPLAY")
        self.assertEqual(json.loads(row["payload"])["impressions"], 150000)
        self.assertEqual(ctx.summary()["signals"], 1)

    def test_duplicate_signal_is_rejected_early(self):
        ctx = Context(ads_job.JOBS[0])
        args = {"company": "Acme Marketing GmbH", "source": "test-ads",
                "source_url": "https://example.test/advertiser/AR001", "title": "SEARCH"}
        self.assertTrue(ctx.add_signal("ads", **args))
        self.assertFalse(ctx.add_signal("ads", **args))
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM signals WHERE source='test-ads'"), 1)
        self.assertEqual(ctx.stats["signals"], 1)

    def test_context_accepts_hiring_signal(self):
        ctx = Context(hiring_job.JOBS[0])
        created = ctx.add_signal(
            "hiring", company="Acme Inc", title="Growth Marketer", source="test-hiring",
            source_url="https://boards.example.com/acme/1",
            payload={"location": "Remote", "department": "Marketing", "postings": 3,
                     "source": "greenhouse"})
        self.assertTrue(created)
        row = db.one("SELECT * FROM signals WHERE source='test-hiring'")
        self.assertEqual(row["kind"], "hiring")
        self.assertEqual(json.loads(row["payload"])["postings"], 3)

    def test_summary_merges_counts(self):
        ctx = Context(ads_job.JOBS[0])
        ctx.add_signal("ads", company="Acme Marketing GmbH", source="test-ads",
                       source_url="https://example.test/x", title="SEARCH")
        summary = ctx.summary(week="November_Week_3", advertisers=10, kept=1)
        self.assertEqual(summary["signals"], 1)
        self.assertEqual(summary["week"], "November_Week_3")
        self.assertEqual(summary["advertisers"], 10)
        self.assertEqual(summary["kept"], 1)


class JobErrorTests(unittest.TestCase):
    """`run()` must never raise, even when the network layer refuses to work."""

    def setUp(self):
        db.init_db()
        net.set_offline(True)

    def tearDown(self):
        net.set_offline(False)

    def test_ads_run_returns_error_summary(self):
        result = ads_job.run(Context(ads_job.JOBS[0]))
        self.assertFalse(result["ok"])
        self.assertTrue(result["error"])
        self.assertIn("offline", result["error"].lower())

    def test_hiring_run_returns_error_summary(self):
        result = hiring_job.run(Context(hiring_job.JOBS[0]))
        self.assertFalse(result["ok"])
        self.assertTrue(result["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
