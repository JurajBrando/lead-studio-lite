"""Tests for the buying-signal qualification gate.

Offline only: every page is a string in this file, no network and no model calls.
"""
import os
import tempfile
import unittest
from unittest import mock

TMP = tempfile.mkdtemp(prefix="ls_qualify_")
os.environ["LS_DATA"] = TMP
os.environ["LS_PORT"] = "8798"

import sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine import db, qualify                       # noqa: E402
from engine.jobs import qualify as qualify_job       # noqa: E402
from engine.jobs.base import Context                 # noqa: E402

SHELL = ("<html><head><title>%s</title>"
         "<meta name=\"description\" content=\"%s\"></head><body>%s</body></html>")
FILLER = ("We have been serving customers since 2009 from our workshop. " * 12)
META = "<script src='https://connect.facebook.net/en_US/fbevents.js'></script>"
GOOGLE = "<script src='https://www.googleadservices.com/pagead/conversion.js'></script>"
PHONE = "<a href='tel:+421900123456'>Call us</a>"
FORM = "<form><input type='email' name='email'></form>"


def page(title="A Company", description="What we do", body=""):
    return SHELL % (title, description, body + FILLER)


def site(html, domain="example.com", emails=("info@example.com",)):
    return qualify.site_from_pages(domain, [("https://" + domain, html)],
                                   title="t", description="d", emails=list(emails))


ECOM_NO_ATTRIBUTION = page("Shop", "Buy online", META + GOOGLE + PHONE)
ECOM_WITH_HYROS = page("Shop", "Buy online",
                       META + "<script src='https://t.hyros.com/v1/lst/u.js'></script>" + PHONE)
COACH_FUNNEL = page("Coaching", "Scale your business",
                    META + "<p>Book a call with our team. Free training inside.</p>" + PHONE)
ROOFER = page("Smith Roofing", "Roofing and gutters",
              "<p>Free estimate on any roof. Areas we serve: Dallas and Fort Worth.</p>" + PHONE)
ROOFER_WITH_JOBBER = page("Smith Roofing", "Roofing",
                          "<p>Free estimate. Areas we serve: Dallas.</p>"
                          "<script src='https://getjobber.com/widget.js'></script>" + PHONE + FORM)
DENTAL_PHONE_ONLY = page("Zubna ambulancia", "Starostlivost o chrup",
                         "<p>Otvaracie hodiny: Pondelok 8:00 - 16:00</p>"
                         "<a href='tel:+421911222333'>Zavolajte</a>")
DENTAL_WITH_ADS = page("Dental Clinic", "Implants and aesthetics",
                       META + GOOGLE + "<p>Opening hours: Monday 8-16</p>" + PHONE)
DENTAL_WITH_BOOKING = page("Dental Clinic", "Care",
                           "<p>Opening hours: Monday 8-16</p>"
                           "<a href='https://calendly.com/clinic'>Book online</a>")
AGENCY = page("Performance marketing agency", "We grow brands",
              META + "<p>Our clients and case studies.</p>" + FORM)
AGENCY_WHITE_LABEL = page("Digital agency", "We grow brands",
                          META + "<p>Our clients. White label our stack under your own brand, "
                          "with sub-accounts for every reseller.</p>" + FORM)
US_AI_COMPANY = page("Acme Data Labs", "Machine learning services",
                     "<p>Acme Data Labs LLC, Austin, TX. (512) 555-0134. Our team of engineers "
                     "delivers data annotation and computer vision. We are hiring: open positions, "
                     "apply now.</p>" + FORM)
BRAND_SHOP = page("Official store", "Our brand",
                  "<script src='https://cdn.shopify.com/s/files/shop.js'></script>"
                  "<p>We are the official store of our brand (R). Also on amazon.de</p>" + PHONE)
MANUFACTURER = page("Zakazkova vyroba", "CNC obrabanie a zvaranie",
                    "<p>Sme certifikovani podla ISO 9001. Hladame operator vyroby a zvarac.</p>"
                    + PHONE)
PARKED = SHELL % ("Domain", "", "<h1>This domain is parked</h1>")
THIN = SHELL % ("Hi", "", "<p>Hello.</p>")


class CatalogueTests(unittest.TestCase):
    def test_every_signal_sits_in_a_cohort(self):
        self.assertEqual(len(set(qualify.SIGNAL_KEYS)), qualify.SIGNAL_COUNT)
        for item in qualify.SIGNALS:
            self.assertIn(item["cohort"], qualify.COHORTS)

    def test_catalogue_covers_every_signal(self):
        listed = sum(len(c["signals"]) for c in qualify.catalogue())
        self.assertEqual(listed, qualify.SIGNAL_COUNT)

    def test_every_affiliate_offer_has_a_cohort(self):
        offers = " ".join(c["offer"] for c in qualify.COHORTS.values())
        for name in ("HYROS", "QuoteIQ", "Dermis", "HighLevel", "micro1", "Red Points"):
            self.assertIn(name, offers, name)

    def test_no_model_call_is_possible(self):
        path = os.path.join(ROOT, "engine", "qualify.py")
        with open(path, encoding="utf-8") as handle:
            source = handle.read()
        for forbidden in ("import llm", "llm.chat", "openai", "anthropic"):
            self.assertNotIn(forbidden, source)


class DisqualifierTests(unittest.TestCase):
    def test_parked_domain_is_out(self):
        answer = qualify.scan(site(PARKED))
        self.assertEqual(answer["verdict"], qualify.OUT)
        self.assertIn("parked", answer["disqualified"])
        self.assertEqual(answer["score"], 0)

    def test_thin_page_is_out(self):
        self.assertEqual(qualify.scan(site(THIN))["verdict"], qualify.OUT)

    def test_no_contact_channel_is_out(self):
        answer = qualify.scan(site(page("A", "B", "<p>Nothing here.</p>"), emails=()))
        self.assertIn("no_contact", answer["disqualified"])

    def test_free_host_is_out(self):
        answer = qualify.scan(site(ECOM_NO_ATTRIBUTION, domain="shop.wixsite.com"))
        self.assertIn("free_host", answer["disqualified"])


class HyrosTests(unittest.TestCase):
    def test_ads_without_attribution_fires(self):
        answer = qualify.scan(site(ECOM_NO_ATTRIBUTION), ["tracking_gap", "paid_ads"])
        self.assertIn("ads_without_attribution", answer["signals"])
        self.assertEqual(answer["verdict"], qualify.HOT)
        self.assertEqual(answer["cohort"], "tracking_gap")
        self.assertEqual(answer["offer"], "HYROS Tracking")

    def test_hyros_already_installed_does_not_fire_the_gap(self):
        answer = qualify.scan(site(ECOM_WITH_HYROS), ["tracking_gap"])
        self.assertNotIn("ads_without_attribution", answer["signals"])

    def test_coach_funnel_counts(self):
        answer = qualify.scan(site(COACH_FUNNEL), ["tracking_gap"])
        self.assertIn("high_ticket_funnel", answer["signals"])


class QuoteIQTests(unittest.TestCase):
    def test_roofer_quoting_by_hand(self):
        answer = qualify.scan(site(ROOFER), ["home_service"])
        self.assertIn("home_service_trade", answer["signals"])
        self.assertIn("quotes_by_hand", answer["signals"])
        self.assertIn("service_area_pages", answer["signals"])
        self.assertEqual(answer["cohort"], "home_service")

    def test_phone_only_trade_bundles_ataraxis(self):
        answer = qualify.scan(site(ROOFER), ["home_service"])
        self.assertIn("phone_only_intake", answer["signals"])
        self.assertEqual(answer["offer"], "QuoteIQ Pro + Ataraxis")

    def test_existing_quoting_software_is_displacement_not_a_gap(self):
        answer = qualify.scan(site(ROOFER_WITH_JOBBER), ["home_service"])
        self.assertIn("quoting_software_present", answer["signals"])
        self.assertNotIn("quotes_by_hand", answer["signals"])
        self.assertEqual(answer["offer"], "QuoteIQ Pro")


class DermisTests(unittest.TestCase):
    def test_clinic_without_booking(self):
        answer = qualify.scan(site(DENTAL_PHONE_ONLY), ["clinic_booking"])
        self.assertIn("medical_clinic", answer["signals"])
        self.assertIn("no_online_booking", answer["signals"])
        self.assertEqual(answer["offer"], "Dermis")
        self.assertEqual(answer["verdict"], qualify.HOT)

    def test_clinic_running_ads_bundles_hyros(self):
        answer = qualify.scan(site(DENTAL_WITH_ADS), ["clinic_booking", "tracking_gap"])
        self.assertEqual(answer["cohort"], "clinic_booking")
        self.assertEqual(answer["offer"], "Dermis + HYROS")

    def test_booking_tool_present_is_displacement(self):
        answer = qualify.scan(site(DENTAL_WITH_BOOKING), ["clinic_booking"])
        self.assertIn("booking_tool_present", answer["signals"])
        self.assertNotIn("no_online_booking", answer["signals"])


class HighLevelTests(unittest.TestCase):
    def test_agency_running_own_ads_gets_pro(self):
        answer = qualify.scan(site(AGENCY), ["agency_ops"])
        self.assertIn("agency_runs_own_ads", answer["signals"])
        self.assertEqual(answer["offer"], "HighLevel SaaS Pro + HYROS AIR")

    def test_white_label_agency_gets_unlimited(self):
        answer = qualify.scan(site(AGENCY_WHITE_LABEL), ["agency_ops"])
        self.assertIn("white_label_offer", answer["signals"])
        self.assertIn("HighLevel Unlimited", answer["offer"])


class Micro1Tests(unittest.TestCase):
    def test_us_ai_company_matches(self):
        answer = qualify.scan(site(US_AI_COMPANY), ["enterprise_data"])
        self.assertIn("us_presence", answer["signals"])
        self.assertIn("engineering_or_ai_work", answer["signals"])
        self.assertIn("company_scale", answer["signals"])
        self.assertIn("hiring_at_scale", answer["signals"])
        self.assertEqual(answer["offer"], "micro1")
        self.assertEqual(answer["verdict"], qualify.HOT)

    def test_slovak_manufacturer_is_not_a_micro1_referral(self):
        answer = qualify.scan(site(MANUFACTURER), ["enterprise_data"])
        self.assertNotIn("us_presence", answer["signals"])
        self.assertEqual(answer["cohort"], "")


class OtherCohortTests(unittest.TestCase):
    def test_brand_risk(self):
        answer = qualify.scan(site(BRAND_SHOP), ["brand_risk"])
        self.assertIn("official_store_language", answer["signals"])
        self.assertIn("marketplace_presence", answer["signals"])
        self.assertEqual(answer["offer"], "Red Points")

    def test_manufacturer_matches_modulario(self):
        answer = qualify.scan(site(MANUFACTURER), ["industrial_ops"])
        self.assertIn("quality_certifications", answer["signals"])
        self.assertEqual(answer["offer"], "Modulario")

    def test_unconfirmed_cohorts_do_not_earn_a_score(self):
        answer = qualify.scan(site(MANUFACTURER), ["tracking_gap"])
        self.assertIn("manufacturing_terms", answer["signals"])      # still recorded
        self.assertEqual(answer["cohort"], "")                       # but not sold into
        self.assertEqual(answer["verdict"], qualify.COLD)

    def test_liveness_alone_never_qualifies(self):
        answer = qualify.scan(site(DENTAL_PHONE_ONLY), ["liveness"])
        self.assertEqual(answer["verdict"], qualify.COLD)

    def test_a_broken_check_cannot_kill_the_scan(self):
        broken = dict(qualify.SIGNALS[-1])
        broken["detect"] = lambda s: 1 / 0
        with mock.patch.object(qualify, "SIGNALS", qualify.SIGNALS[:-1] + [broken]):
            answer = qualify.scan(site(MANUFACTURER), ["industrial_ops"])
        self.assertEqual(answer["verdict"], qualify.HOT)


class JobTests(unittest.TestCase):
    def setUp(self):
        db.init_db()
        db.execute("DELETE FROM leads")
        db.execute("DELETE FROM domains")
        db.execute("DELETE FROM signals")

    def _lead(self, email, domain, company="Co"):
        return db.insert("leads", {"email": email, "domain": domain, "company": company,
                                   "status": "new", "created_ts": db.now(),
                                   "updated_ts": db.now()})

    def _run(self, pages_by_domain, params=None):
        job = qualify_job.JOBS[0]
        ctx = Context(job, params or {})

        def fake_read(domain, **kw):
            html = pages_by_domain.get(domain)
            if html is None:
                return None, "could not connect"
            return site(html, domain=domain), ""

        with mock.patch.object(qualify_job, "read_site", side_effect=fake_read):
            with mock.patch.object(qualify_job.time, "sleep", lambda *a: None):
                return qualify_job.run(ctx)

    def test_hot_parked_and_dead_are_separated(self):
        self._lead("a@good.com", "good.com")
        self._lead("b@parked.com", "parked.com")
        self._lead("c@dead.com", "dead.com")
        summary = self._run({"good.com": ECOM_NO_ATTRIBUTION, "parked.com": PARKED},
                            {"cohorts": "tracking_gap,paid_ads", "pause": 0})
        self.assertEqual(summary["checked"], 3)
        self.assertEqual(summary["hot"], 1)
        self.assertEqual(summary["disqualified"], 1)
        self.assertEqual(summary["unreachable"], 1)

        good = db.one("SELECT * FROM leads WHERE domain='good.com'")
        self.assertIn("q:hot", good["tags"])
        self.assertIn("cohort:tracking_gap", good["tags"])
        self.assertEqual(good["status"], "new")

        for domain in ("parked.com", "dead.com"):
            row = db.one("SELECT * FROM leads WHERE domain=?", (domain,))
            self.assertEqual(row["status"], "unqualified", domain)
            self.assertIn("q:disqualified", row["tags"])

    def test_one_request_per_domain_not_per_lead(self):
        for i in range(5):
            self._lead("p%d@shared.com" % i, "shared.com")
        seen = []
        job = qualify_job.JOBS[0]
        ctx = Context(job, {"cohorts": "tracking_gap", "pause": 0})

        def fake_read(domain, **kw):
            seen.append(domain)
            return site(ECOM_NO_ATTRIBUTION, domain=domain), ""

        with mock.patch.object(qualify_job, "read_site", side_effect=fake_read):
            with mock.patch.object(qualify_job.time, "sleep", lambda *a: None):
                summary = qualify_job.run(ctx)
        self.assertEqual(seen, ["shared.com"])
        self.assertEqual(summary["checked"], 1)
        self.assertEqual(summary["sendable"], 5)        # every contact behind it

    def test_second_run_skips_what_it_already_judged(self):
        self._lead("a@good.com", "good.com")
        self._run({"good.com": ECOM_NO_ATTRIBUTION}, {"cohorts": "tracking_gap", "pause": 0})
        again = self._run({"good.com": ECOM_NO_ATTRIBUTION},
                          {"cohorts": "tracking_gap", "pause": 0})
        self.assertEqual(again["checked"], 0)

    def test_csv_carries_the_offer_to_pitch(self):
        self._lead("a@roofer.com", "roofer.com")
        summary = self._run({"roofer.com": ROOFER}, {"cohorts": "home_service", "pause": 0})
        self.assertTrue(os.path.isfile(summary["file"]))
        with open(summary["file"], encoding="utf-8") as handle:
            body = handle.read()
        self.assertIn("QuoteIQ Pro + Ataraxis", body)
        self.assertIn("quotes_by_hand", body)

    def test_signal_is_written_for_sendable_leads(self):
        self._lead("a@good.com", "good.com")
        self._run({"good.com": ECOM_NO_ATTRIBUTION}, {"cohorts": "tracking_gap", "pause": 0})
        row = db.one("SELECT * FROM signals WHERE kind='qualified'")
        self.assertIsNotNone(row)
        self.assertIn("HOT", row["title"])

    def test_park_disqualified_can_be_turned_off(self):
        self._lead("b@parked.com", "parked.com")
        self._run({"parked.com": PARKED}, {"cohorts": "tracking_gap", "pause": 0,
                                           "park_disqualified": False})
        self.assertEqual(db.one("SELECT * FROM leads WHERE domain='parked.com'")["status"],
                         "new")


if __name__ == "__main__":
    unittest.main()
