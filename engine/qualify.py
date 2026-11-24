"""Buying-signal qualification: one read of a site, no model calls.

The database holds millions of contacts and almost all of them are noise. This module
is the gate: it reads a company's site once, as it is today, and answers three
questions that cost nothing to answer.

  1. Is this a real, living business?   (the disqualifiers below)
  2. Does it show a signal we sell to?  (the signals, grouped in cohorts)
  3. Which offer does that signal point at?

No LLM is involved at any point, so the whole thing is free to run over the entire
database - the only cost is one HTTP request per domain, and a domain is read once.

The difference to `hotlist.py` is deliberate: the hot list fires on a *change* between
two crawls, which needs crawl history we do not have for a cold import. This fires on a
*state*, so a lead imported five minutes ago can be qualified immediately.

One cohort is one offer's buying situation, and the cohorts are the segments the
offers are actually sold into: blue-collar and home service to QuoteIQ and Ataraxis,
medical clinics to Dermis, e-commerce and coaches to HYROS, agencies to HighLevel and
HYROS AIR, brands to Red Points, US data work to micro1. You confirm which cohorts you
sell into; signals outside them are still recorded, but they do not earn a lead a place
in the outbox.
"""
import datetime
import re

from . import config

# --------------------------------------------------------------- cohorts
# Order matters: when two cohorts score the same the one higher up wins, because the
# one higher up is the one an email can open with. "They advertise" is a budget;
# "they advertise and cannot see what it returns" is a conversation.
COHORTS = {
    # Order decides ties, and a tie goes to the cohort that says *who the company is*
    # over the one that says *what it does*. A clinic with no booking is a Dermis lead
    # that also runs ads; it is not an ads lead that happens to be a clinic - and the
    # offer refiner below bundles HYROS on top of it either way.
    "home_service": {
        "label": "Blue-collar trade quoting by hand",
        "offer": "QuoteIQ Pro",
        "note": "Roofing, cleaning, landscaping, HVAC: free estimates, no quoting software.",
    },
    "clinic_booking": {
        "label": "Medical clinic taking bookings by phone",
        "offer": "Dermis",
        "note": "Dental, dermatology, aesthetics - the phone is the only way in.",
    },
    "agency_ops": {
        "label": "Agency running on manual follow-up",
        "offer": "HighLevel SaaS Pro",
        "note": "Clients and ads, nothing capturing or nurturing the leads.",
    },
    "enterprise_data": {
        "label": "US company at the scale micro1 refers",
        "offer": "micro1",
        "note": "30+ people, English-speaking, engineering or AI work. $50K per referral.",
    },
    "industrial_ops": {
        "label": "Makes things to order",
        "offer": "Modulario",
        "note": "Production, certifications, shop-floor hiring: an ERP/MES buyer. Your own "
                "B2B offer rather than an affiliate one - unconfirm the cohort to drop it.",
    },
    "brand_risk": {
        "label": "Has a brand worth copying",
        "offer": "Red Points",
        "note": "Own-brand goods sold online - the precondition for fakes to exist.",
    },
    "tracking_gap": {
        "label": "Spends on ads and cannot attribute it",
        "offer": "HYROS Tracking",
        "note": "Ad pixels on the site, no attribution tool. E-commerce and coaches.",
    },
    "paid_ads": {
        "label": "Spends money on ads",
        "offer": "HYROS",
        "note": "Corroboration: a pixel proves a budget, not a problem worth solving.",
    },
    "liveness": {
        "label": "Alive and moving",
        "offer": "",
        "note": "Not an offer of its own - evidence that somebody still works here.",
    },
}
COHORT_ORDER = list(COHORTS)
# These two corroborate a lead, they are never the reason to write to one. A pixel means
# a budget exists; it is not a problem anybody wants solved. They add to the score but
# they do not get to name the cohort or the offer, unless they are all you confirmed.
SUPPORT_COHORTS = ("paid_ads", "liveness")

# --------------------------------------------------------------- markers
# Everything below is matched against the lowercased, diacritic-folded HTML.
AD_PIXELS = {
    "meta": ("connect.facebook.net", "fbevents.js", "fbq(", "facebook-jssdk"),
    "google_ads": ("googleadservices.com", "googlesyndication.com", "gtag/js?id=aw-",
                   "google_conversion_id", "/pagead/"),
    "tiktok": ("analytics.tiktok.com", "ttq.load", "tiktok-pixel"),
    "linkedin": ("snap.licdn.com", "_linkedin_partner_id", "linkedin_data_partner_id"),
    "pinterest": ("pintrk(", "s.pinimg.com/ct"),
    "bing": ("bat.bing.com", "uetq"),
}
ATTRIBUTION = ("hyros", "t.hyros.com", "triplewhale", "northbeam", "datafa.st", "datafast",
               "wickedreports", "wicked reports", "rockerbox", "hightouch",
               "segment.com/analytics.js")
COMPETITOR_ATTRIBUTION = ("triplewhale", "northbeam", "wickedreports", "rockerbox")
HIGH_TICKET = ("book a call", "schedule a call", "book a free call", "apply now",
               "free consultation", "strategy session", "discovery call", "masterclass",
               "free training", "watch the webinar", "free webinar", "case study call")
BOOKING = ("calendly.com", "acuityscheduling", "booksy", "simplybook", "reservio.com", "noona",
           "setmore", "squareup.com/appointments", "fresha.com", "timify", "bookla", "cal.com",
           "youcanbook.me", "zenoti", "mindbody", "appointlet", "bookeo", "objednat sa online",
           "rezervovat termin", "book online", "book an appointment", "online booking")

# -- QuoteIQ: the trades, and the software that would already be doing the job
HOME_SERVICE_TRADES = ("roofing", "roofer", "plumbing", "plumber", "hvac", "heating and cooling",
                       "landscaping", "lawn care", "pressure washing", "power washing",
                       "window cleaning", "gutter", "junk removal", "pest control", "paving",
                       "fencing", "remodeling", "remodeler", "handyman", "painting contractor",
                       "carpet cleaning", "pool service", "tree service", "electrician",
                       "garage door", "concrete", "siding", "deck building", "moving company",
                       "rope access", "facade cleaning", "cistenie oken", "upratovanie")
QUOTING_SOFTWARE = ("jobber", "housecall pro", "housecallpro", "servicetitan", "quoteiq",
                    "service fusion", "workiz", "jobnimbus", "fieldedge", "markate",
                    "getjobber", "thryv", "service autopilot")
ESTIMATE_LANGUAGE = ("free estimate", "free quote", "get a quote", "request a quote",
                     "request an estimate", "get your free estimate", "no obligation quote",
                     "instant quote", "cenova ponuka", "nezavazna ponuka")
SERVICE_AREA = ("areas we serve", "service area", "areas served", "proudly serving",
                "we serve", "locations we serve", "serving the", "posobime v")

# -- Dermis: clinics
MEDICAL_WORDS = ("dental", "dentist", "dentistry", "orthodont", "implantolog", "dermatolog",
                 "aesthetic clinic", "medical spa", "medspa", "plastic surgery", "cosmetic clinic",
                 "skin clinic", "physiotherapy", "zubna", "zubar", "stomatolog", "ambulancia",
                 "klinika", "ordinace", "estetick", "dermatologie", "chirurgie")

# -- HighLevel / HYROS AIR: agencies
AGENCY_WORDS = ("marketing agency", "digital agency", "seo agency", "ppc agency", "ad agency",
                "growth agency", "performance marketing", "marketingova agentura",
                "digitalna agentura", "reklamna agentura", "we grow brands", "lead generation",
                "media buying")
WHITE_LABEL = ("white label", "white-label", "resell", "reseller", "sub-account", "sub accounts",
               "saas mode", "done-for-you", "partner program", "our agency partners",
               "under your own brand")
CRM_EMBED = ("hubspot", "activecampaign", "mailchimp", "klaviyo", "convertkit", "gohighlevel",
             "msgsndr.com", "leadconnectorhq.com", "zoho", "pipedrive", "intercom", "drift.com",
             "tawk.to", "crisp.chat", "smartsuppchat", "livechatinc")
CASE_STUDY_WORDS = ("case study", "case studies", "our clients", "pripadova studia",
                    "nasi klienti", "referencie", "reference", "testimonials",
                    "what our clients say")

# -- micro1: US companies with 30+ people doing engineering or AI work
US_MARKERS = ("united states", " usa", "u.s.a", ", ca ", ", ny ", ", tx ", ", fl ", ", il ",
              ", wa ", ", ga ", ", ma ", ", co ", ", az ", ", nc ", "llc", ", inc", "inc.",
              "+1 (", "1-800-", "1-888-")
TECH_AI_WORDS = ("artificial intelligence", "machine learning", "data annotation", "data labeling",
                 "llm", "generative ai", "software development", "software engineering",
                 "engineering services", "outsourcing", "bpo", "staffing", "talent solutions",
                 "managed services", "it services", "data science", "computer vision",
                 "nearshore", "offshore development")
SCALE_WORDS = ("our team of", "employees", "offices in", "global team", "enterprise clients",
               "fortune 500", "worldwide", "across the globe", "our leadership team",
               "board of directors", "investor", "series a", "series b")

# -- Red Points: brands
ECOMMERCE = ("cdn.shopify.com", "shopify", "woocommerce", "wp-content/plugins/woocommerce",
             "prestashop", "magento", "bigcommerce", "shoptet", "/cart", "add-to-cart",
             "pridat do kosika", "do kosika", "do kosiku")
MARKETPLACES = ("amazon.", "ebay.", "aliexpress.", "etsy.com", "allegro.", "alza.sk", "alza.cz",
                "mall.sk", "mall.cz", "heureka.", "emag.")
OFFICIAL_STORE = ("official store", "official shop", "authorized dealer", "authorised dealer",
                  "official distributor", "oficialny predajca", "autorizovany predajca",
                  "our brand", "brand protection", "beware of counterfeit", "genuine product")
TRADEMARK_MARKS = ("®", "™", "trademark", "ochranna znamka", "registered trademark")

# -- Modulario: manufacturing
MANUFACTURING = ("manufacturing", "vyroba", "zakazkova vyroba", "custom manufacturing", "cnc",
                 "obrabanie", "machining", "zvaranie", "welding", "fabrication", "lisovanie",
                 "montaz", "assembly line", "production line", "strojarstvo", "sheet metal",
                 "injection molding", "toolmaking", "nastrojaren")
CERTIFICATIONS = ("iso 9001", "iso9001", "iso 14001", "iatf 16949", "iso 45001", "ohsas 18001",
                  "en 1090", "ce certifikat", "certified to iso", "quality management system")
PRODUCTION_ROLES = ("operator vyroby", "cnc operator", "zvarac", "welder", "machine operator",
                    "production worker", "shift supervisor", "majster vyroby", "skladnik",
                    "quality inspector", "technolog")

# -- shared
HIRING_WORDS = ("we're hiring", "we are hiring", "join our team", "job openings", "open positions",
                "careers", "career", "kariera", "volne pracovne miesta", "ponuka prace",
                "nabidka prace", "volna mista", "pridaj sa k nam", "hledame", "hladame")
OPENING_HOURS = ("opening hours", "otvaracie hodiny", "otevirac", "oteviraci doba", "mon -", "po -",
                 "monday", "pondelok", "pondeli", "business hours", "hours of operation")
UTM_MARKERS = ("utm_source=", "utm_campaign=", "gclid=", "fbclid=")

# --------------------------------------------------------------- disqualifiers
PARKED_MARKERS = ("domain is for sale", "buy this domain", "this domain is parked",
                  "domain parking", "under construction", "coming soon", "stranka sa pripravuje",
                  "pripravujeme pre vas", "webove stranky sa pripravuju", "default web site page",
                  "apache2 ubuntu default page", "welcome to nginx", "index of /",
                  "wordpress › installation", "site not found", "account suspended",
                  "this site can't be reached", "future home of something quite cool")
FREE_HOSTS = ("wixsite.com", "weebly.com", "blogspot.", "wordpress.com", "webnode.",
              "webs.com", "sites.google.com", "business.site", "mystrikingly.com", "carrd.co",
              "jimdosite.com", "tilda.ws", "estranky.")
MIN_TEXT_CHARS = 400

TAG_RX = re.compile(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>")
STRIP_RX = re.compile(r"<[^>]+>")
WS_RX = re.compile(r"\s+")
TEL_RX = re.compile(r"(?i)(tel:|href=[\"']tel:|\+\d{2,3}[\s/\-.]?\d{3})")
US_PHONE_RX = re.compile(r"(\(\d{3}\)\s?\d{3}[\s\-]\d{4}|\b1[\s\-]8\d{2}[\s\-]\d{3}[\s\-]\d{4})")
FORM_RX = re.compile(r"(?is)<form\b.*?</form>")
EMAIL_FIELD_RX = re.compile(r"(?i)(type=[\"']?email|name=[\"']?e?-?mail)")
JOB_POST_RX = re.compile(r"(?i)(apply (now|today)|view (the )?(job|role|position)|"
                         r"open (roles|positions)|we are looking for|full[- ]time)")
DIACRITICS = str.maketrans(
    "áäčďéíľĺňóôŕšťúýžěřů",
    "aacdeillnoorstuyzeru")


def _fold(value):
    """Lowercase and strip Slovak/Czech diacritics, so one marker matches both spellings."""
    return (value or "").lower().translate(DIACRITICS)


class Site:
    """The pages of one domain, in the three shapes the checks need."""

    def __init__(self, domain, pages=None, title="", description="", emails=None):
        self.domain = (domain or "").strip().lower()
        self.pages = list(pages or [])                      # [(url, html), ...]
        self.title = title or ""
        self.description = description or ""
        self.emails = list(emails or [])
        raw = "\n".join(html for _, html in self.pages)
        self.html = _fold(raw)                              # markup kept: pixels live in it
        readable = STRIP_RX.sub(" ", TAG_RX.sub(" ", raw))
        self.text = _fold(WS_RX.sub(" ", readable))
        self.urls = _fold(" ".join(url for url, _ in self.pages))
        self.meta = _fold(self.title + " " + self.description)

    # -- small helpers, so the checks below read like sentences
    def has(self, markers):
        return any(marker in self.html for marker in markers)

    def says(self, markers):
        return any(marker in self.text or marker in self.meta for marker in markers)

    def pixels(self):
        return [name for name, markers in AD_PIXELS.items() if self.has(markers)]

    def has_form(self):
        return any(EMAIL_FIELD_RX.search(block) for block in FORM_RX.findall(self.html))

    def has_phone(self):
        return bool(TEL_RX.search(self.html))

    def looks_us(self):
        return bool(US_PHONE_RX.search(self.text)) or self.says(US_MARKERS)


def site_from_pages(domain, pages, title="", description="", emails=None):
    return Site(domain, pages, title, description, emails)


def _s(key, cohort, label, weight, detect, evidence=""):
    return {"key": key, "cohort": cohort, "label": label, "weight": weight,
            "detect": detect, "evidence": evidence}


def _current_year_on(site):
    year = datetime.date.today().year
    return str(year) in site.text or str(year - 1) in site.text


# --------------------------------------------------------------- the signals
SIGNALS = [
    # -- tracking_gap -> HYROS Tracking (4) --------------------------------
    _s("ads_without_attribution", "tracking_gap",
       "Runs ads with no attribution tool", 5,
       lambda s: bool(s.pixels()) and not s.has(ATTRIBUTION),
       "spending blind - the HYROS conversation writes itself"),
    _s("multi_channel_without_attribution", "tracking_gap",
       "Two or more ad channels, nothing tying them together", 4,
       lambda s: len(s.pixels()) >= 2 and not s.has(ATTRIBUTION)),
    _s("competitor_attribution", "tracking_gap",
       "Already pays for another attribution tool", 3,
       lambda s: s.has(COMPETITOR_ATTRIBUTION), "displacement, not education"),
    _s("high_ticket_funnel", "tracking_gap",
       "Runs a call or application funnel", 3,
       lambda s: s.says(HIGH_TICKET) and bool(s.pixels()),
       "coach or consultant: every booked call has a cost per lead"),

    # -- home_service -> QuoteIQ (+ Ataraxis) (5) --------------------------
    _s("home_service_trade", "home_service", "Is a home-service trade", 4,
       lambda s: s.says(HOME_SERVICE_TRADES)),
    _s("quotes_by_hand", "home_service", "Offers estimates with no quoting software", 4,
       lambda s: s.says(ESTIMATE_LANGUAGE) and not s.has(QUOTING_SOFTWARE),
       "every quote costs a site visit and an evening"),
    _s("quoting_software_present", "home_service", "Runs quoting software already", 2,
       lambda s: s.has(QUOTING_SOFTWARE), "displacement target"),
    _s("service_area_pages", "home_service", "Publishes the areas it serves", 2,
       lambda s: s.says(SERVICE_AREA), "a real route-based business, not a directory entry"),
    _s("phone_only_intake", "home_service", "Phone is the only way to reach them", 3,
       lambda s: s.has_phone() and not s.has_form() and not s.has(BOOKING),
       "nobody answers while the crew is on a roof - the Ataraxis half"),

    # -- clinic_booking -> Dermis (4) --------------------------------------
    _s("medical_clinic", "clinic_booking", "Is a clinic or practice", 4,
       lambda s: s.says(MEDICAL_WORDS)),
    _s("no_online_booking", "clinic_booking", "No way to book online", 4,
       lambda s: not s.has(BOOKING), "every appointment costs somebody a phone call"),
    _s("booking_tool_present", "clinic_booking", "Uses a booking tool already", 2,
       lambda s: s.has(BOOKING), "displacement target"),
    _s("opening_hours", "clinic_booking", "Publishes opening hours", 1,
       lambda s: s.says(OPENING_HOURS), "a real place with real walk-ins"),

    # -- agency_ops -> HighLevel / HYROS AIR (5) ---------------------------
    _s("is_agency", "agency_ops", "Describes itself as an agency", 3,
       lambda s: s.says(AGENCY_WORDS)),
    _s("agency_runs_own_ads", "agency_ops", "Agency advertising for itself", 4,
       lambda s: s.says(AGENCY_WORDS) and bool(s.pixels()),
       "buying leads it has no system to work"),
    _s("no_crm_embed", "agency_ops", "No CRM or chat capturing the traffic", 2,
       lambda s: not s.has(CRM_EMBED)),
    _s("white_label_offer", "agency_ops", "Resells or white-labels to its own clients", 3,
       lambda s: s.says(WHITE_LABEL), "the Unlimited tier rather than SaaS Pro"),
    _s("case_studies", "agency_ops", "Shows clients or case studies", 1,
       lambda s: s.says(CASE_STUDY_WORDS)),

    # -- enterprise_data -> micro1 (4) -------------------------------------
    _s("us_presence", "enterprise_data", "Operates in the United States", 3,
       lambda s: s.looks_us(), "micro1 prioritises US companies"),
    _s("engineering_or_ai_work", "enterprise_data", "Does engineering, data or AI work", 4,
       lambda s: s.says(TECH_AI_WORDS)),
    _s("company_scale", "enterprise_data", "Reads like a company of 30+", 3,
       lambda s: s.says(SCALE_WORDS)),
    _s("hiring_at_scale", "enterprise_data", "Has real open positions", 2,
       lambda s: s.says(HIRING_WORDS) and bool(JOB_POST_RX.search(s.text))),

    # -- brand_risk -> Red Points (4) --------------------------------------
    _s("ecommerce_platform", "brand_risk", "Sells online", 3,
       lambda s: s.has(ECOMMERCE)),
    _s("trademark_marks", "brand_risk", "Marks its name as a trademark", 2,
       lambda s: s.says(TRADEMARK_MARKS) or any(m in s.html for m in TRADEMARK_MARKS)),
    _s("official_store_language", "brand_risk", "Calls itself the official seller", 3,
       lambda s: s.says(OFFICIAL_STORE), "has already met a counterfeiter"),
    _s("marketplace_presence", "brand_risk", "Also sells on marketplaces", 2,
       lambda s: s.has(MARKETPLACES), "where the fakes show up first"),

    # -- industrial_ops -> Modulario (3) -----------------------------------
    _s("manufacturing_terms", "industrial_ops", "Makes or machines things", 4,
       lambda s: s.says(MANUFACTURING)),
    _s("quality_certifications", "industrial_ops", "Holds a quality certification", 3,
       lambda s: s.says(CERTIFICATIONS), "ISO means documented processes to run"),
    _s("production_hiring", "industrial_ops", "Hiring for the shop floor", 3,
       lambda s: s.says(PRODUCTION_ROLES), "growing output on paper-based planning"),

    # -- paid_ads, corroboration (6) ---------------------------------------
    _s("meta_pixel", "paid_ads", "Meta pixel on the site", 3,
       lambda s: "meta" in s.pixels(), "connect.facebook.net / fbq()"),
    _s("google_ads_tag", "paid_ads", "Google Ads conversion tag", 3,
       lambda s: "google_ads" in s.pixels(), "googleadservices / AW- tag"),
    _s("tiktok_pixel", "paid_ads", "TikTok pixel", 2,
       lambda s: "tiktok" in s.pixels()),
    _s("linkedin_insight", "paid_ads", "LinkedIn Insight tag", 2,
       lambda s: "linkedin" in s.pixels()),
    _s("multi_channel_ads", "paid_ads", "Advertises on two or more channels", 4,
       lambda s: len(s.pixels()) >= 2, "a real media budget, not an experiment"),
    _s("campaign_links", "paid_ads", "Campaign tracking in its own links", 1,
       lambda s: s.has(UTM_MARKERS)),

    # -- liveness (2) ------------------------------------------------------
    _s("hiring_now", "liveness", "Hiring right now", 2,
       lambda s: s.says(HIRING_WORDS)),
    _s("current_year", "liveness", "Site mentions this year", 1,
       lambda s: _current_year_on(s), "somebody still maintains it"),
]
SIGNAL_KEYS = [item["key"] for item in SIGNALS]
BY_KEY = {item["key"]: item for item in SIGNALS}
SIGNAL_COUNT = len(SIGNALS)

# --------------------------------------------------------------- offers
# Several offers are sold into the same cohort, and which one depends on what the site
# showed. This is where a cohort's generic offer becomes the actual pitch.
def _offer_tracking(hits):
    return "HYROS Tracking" + (" (displacement)" if "competitor_attribution" in hits else "")


def _offer_home_service(hits):
    if "phone_only_intake" in hits:
        return "QuoteIQ Pro + Ataraxis"          # nobody answers the phone on a job site
    return "QuoteIQ Pro"


def _offer_clinic(hits):
    return "Dermis + HYROS" if "ads_without_attribution" in hits else "Dermis"


def _offer_agency(hits):
    tier = "HighLevel Unlimited" if "white_label_offer" in hits else "HighLevel SaaS Pro"
    return tier + (" + HYROS AIR" if "agency_runs_own_ads" in hits else "")


OFFER_REFINE = {
    "tracking_gap": _offer_tracking,
    "home_service": _offer_home_service,
    "clinic_booking": _offer_clinic,
    "agency_ops": _offer_agency,
}


def offer_for(cohort, hits):
    refine = OFFER_REFINE.get(cohort)
    if refine:
        return refine(set(hits))
    return COHORTS.get(cohort, {}).get("offer", "")


# --------------------------------------------------------------- disqualifiers
DISQUALIFIERS = [
    ("parked", "Parked, placeholder or default server page",
     lambda s: s.says(PARKED_MARKERS) or any(m in s.html for m in PARKED_MARKERS)),
    ("thin", "Almost no readable content",
     lambda s: len(s.text) < MIN_TEXT_CHARS),
    ("free_host", "Lives on a free site builder subdomain",
     lambda s: any(host in s.domain for host in FREE_HOSTS)),
    ("no_contact", "No email, no phone, no form",
     lambda s: not s.emails and not s.has_phone() and not s.has_form()),
]

# --------------------------------------------------------------- verdicts
HOT, WARM, COLD, OUT = "hot", "warm", "cold", "disqualified"
HOT_AT = 8
WARM_AT = 4


def verdict_for(score):
    if score >= HOT_AT:
        return HOT
    if score >= WARM_AT:
        return WARM
    return COLD


def confirmed_cohorts():
    """The cohorts you sell into. Empty / unset means all of them."""
    block = (config.load_settings().get("qualify") or {})
    chosen = block.get("cohorts") or []
    if isinstance(chosen, str):
        chosen = [part.strip() for part in chosen.split(",") if part.strip()]
    chosen = [c for c in chosen if c in COHORTS]
    return chosen or list(COHORTS)


def confirm(cohorts):
    """Sellbird's `signals --confirm`, stored in settings.json."""
    chosen = [c for c in (cohorts or []) if c in COHORTS]
    settings = config.load_settings()
    block = dict(settings.get("qualify") or {})
    block["cohorts"] = chosen
    settings["qualify"] = block
    config.save_settings(settings)
    return chosen


def _safe(test, site):
    """A broken check must never take a batch of 10 000 domains down with it."""
    try:
        return bool(test(site))
    except Exception:                                  # noqa: BLE001
        return False


def scan(site, cohorts=None):
    """Run every check over one site. No network, no model, no database."""
    cohorts = [c for c in (cohorts or confirmed_cohorts()) if c in COHORTS]
    blocked = [(key, why) for key, why, test in DISQUALIFIERS if _safe(test, site)]
    if blocked:
        return {"domain": site.domain, "verdict": OUT, "score": 0, "signals": [], "hits": [],
                "cohort": "", "offer": "", "per_cohort": {},
                "disqualified": [key for key, _ in blocked],
                "reason": blocked[0][1], "evidence": "", "title": site.title}

    hits = []
    for item in SIGNALS:
        if _safe(item["detect"], site):
            hits.append({"key": item["key"], "label": item["label"], "cohort": item["cohort"],
                         "weight": item["weight"], "evidence": item["evidence"]})
    keys = [h["key"] for h in hits]

    per_cohort = {}
    for hit in hits:
        per_cohort[hit["cohort"]] = per_cohort.get(hit["cohort"], 0) + hit["weight"]
    scoring = {c: v for c, v in per_cohort.items() if c in cohorts and c != "liveness"}
    selling = {c: v for c, v in scoring.items() if c not in SUPPORT_COHORTS}
    # `liveness` only corroborates something that already scored.
    score = sum(scoring.values()) + min(per_cohort.get("liveness", 0), 2 if scoring else 0)

    best = ""
    naming = selling or scoring          # support cohorts only when nothing else is confirmed
    if naming:
        top = max(naming, key=lambda c: (naming[c], -COHORT_ORDER.index(c)))
        # One stray point is not a buying situation: a cohort has to carry the lead on
        # its own before it gets to name the offer we would pitch.
        best = top if naming[top] >= WARM_AT else ""
    lead_hit = next((h for h in sorted(hits, key=lambda h: -h["weight"])
                     if h["cohort"] == best), None)
    return {"domain": site.domain, "verdict": verdict_for(score), "score": score,
            "signals": keys, "hits": hits, "cohort": best, "offer": offer_for(best, keys),
            "per_cohort": per_cohort, "disqualified": [],
            "reason": lead_hit["label"] if lead_hit else "",
            "evidence": lead_hit["evidence"] if lead_hit else "", "title": site.title}


def catalogue():
    """What the Automation tab shows when you pick the cohorts."""
    out = []
    for key, meta in COHORTS.items():
        out.append({"key": key, "label": meta["label"], "offer": meta["offer"],
                    "note": meta["note"],
                    "signals": [{"key": s["key"], "label": s["label"], "weight": s["weight"]}
                                for s in SIGNALS if s["cohort"] == key]})
    return out
