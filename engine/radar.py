"""Radar: turn a pasted offer into an offer card, then score a company against it.

Ported from Sales Guy (radar.gd + hunt.gd). Pure rules - no model, no network, no
database - so every number on the Radar tab can be explained and tested.

    parse_offer(text)            -> card
    score(company, card, w, now) -> Fit x Pain x Timing x Ability to pay, with "why"
    rank(scored)                 -> the hunt's 0-100 sort key (a weighted sum)
    pull_block / confidence / guesses / angle   -> the hunt's stage rules

A *company* is a dict: domain, name, industry, size, country, city, technologies, tags,
notes, description, email, tranco, evidence[{kind, title, snippet, detail, source, ts,
payload}].
"""
import math
import re
import unicodedata

from . import net

ATTRIBUTION_TOOLS = ["Hyros", "Triple Whale", "Northbeam", "DataFast"]

TOOL_NEEDLES = {
    "Shopify": ["shopify"], "WooCommerce": ["woocommerce"], "WordPress": ["wp-content", "wordpress"],
    "Webflow": ["webflow"], "Wix": ["wix.com"], "Squarespace": ["squarespace"],
    "ClickFunnels": ["clickfunnels"], "GoHighLevel": ["gohighlevel", "highlevel", "msgsndr"],
    "Kajabi": ["kajabi"], "HubSpot": ["hubspot", "hs-scripts"], "Salesforce": ["salesforce"],
    "Pipedrive": ["pipedrive"], "Klaviyo": ["klaviyo"], "Mailchimp": ["mailchimp"],
    "ActiveCampaign": ["activecampaign"], "SendGrid": ["sendgrid"], "Brevo": ["sendinblue", "brevo"],
    "Intercom": ["intercom"], "Zendesk": ["zendesk"], "Calendly": ["calendly"],
    "Stripe": ["stripe.com", "stripe "], "Meta pixel": ["meta pixel", "facebook.net", "fbq("],
    "Google Tag": ["google tag", "googletagmanager", "gtag("],
    "TikTok pixel": ["tiktok pixel", "analytics.tiktok.com"], "LinkedIn Insight": ["linkedin insight", "snap.licdn.com"],
    "Hyros": ["hyros"], "Triple Whale": ["triple whale", "triplewhale"],
    "Northbeam": ["northbeam"], "DataFast": ["datafast", "datafa.st"],
    "Google Ads": ["spends on google ads", "google ads transparency"],
    "Microsoft 365": ["microsoft 365"], "Google Workspace": ["google workspace"],
    "Booksy": ["booksy"], "Reservio": ["reservio"], "Acuity": ["acuityscheduling", "acuity scheduling"],
    "SimplyBook": ["simplybook"], "Zapier": ["zapier"], "Make": ["make.com", "integromat"],
    "SAP": ["sap erp", "sap business one", "s/4hana"], "Pohoda": ["pohoda"], "Money S3": ["money s3"],
    "Helios": ["helios orange", "helios green", "helios inuvio"], "Odoo": ["odoo"],
    "Abra": ["abra flexi", "abra gen"], "K2": ["k2 atmitec"], "Microsoft Dynamics": ["dynamics 365", "navision"],
    "Lemlist": ["lemlist"], "Instantly": ["instantly.ai"],
    "Manyreach": ["manyreach"], "Semrush": ["semrush"], "Ahrefs": ["ahrefs"],
}

ROLE_WORDS = ["founder", "owner", "ceo", "cmo", "cto", "marketing manager", "head of marketing",
              "marketing lead", "growth lead", "growth marketer", "media buyer", "performance marketer",
              "paid social", "ppc", "ecommerce manager", "head of ecommerce", "practice manager",
              "clinic manager", "operations manager", "sales director", "head of sales"]

CATEGORY_HINTS = {
    "attribution": ["attribution", "ad tracking", "roas", "conversion api", "tracking"],
    "brand protection": ["counterfeit", "fake shop", "copycat", "takedown", "brand protection", "impersonat"],
    "crm and follow-up": ["crm", "follow up", "pipeline", "speed to lead", "lead response"],
    "booking": ["booking", "appointment", "no-show", "schedule"],
    "email outreach": ["outreach", "cold email", "deliverability", "mailbox", "inbox"],
    "seo": ["seo", "backlink", "organic traffic", "content marketing"],
    "ads management": ["ad management", "media buying", "campaign", "creative"],
    "automation": ["automation", "workflow", "no-code", "zapier"],
    "staffing": ["virtual assistant", "staffing", "remote assistant", "recruit"],
    "ecommerce": ["ecommerce", "e-commerce", "shopify", "online store"],
    "erp": ["erp", " mes ", "manufactur", "production planning", "inventory", "vyrob", "výrob",
            "warehouse", "operations software"],
}
CATEGORIES = list(CATEGORY_HINTS)

# What the problem an offer solves looks like on a company, per kind of offer.
#   signals  words on the site or in the findings that show the problem
#   needs    tools that create the problem (two ad pixels create the attribution problem)
#   solves   tools that mean it is already handled (unless they are a competitor)
#   kinds    findings that point at the problem, with a weight
PAIN_PROFILES = {
    "attribution": {"signals": ["roas", "conversion", "paid social", "media buyer", "performance marketing"],
                    "needs": ["Meta pixel", "Google Tag", "TikTok pixel", "LinkedIn Insight", "Google Ads"],
                    "solves": ["Hyros", "Triple Whale", "Northbeam", "DataFast"],
                    "kinds": {"ads": 0.6, "meta_advertiser": 0.6, "tracking_share": 0.3}},
    "brand protection": {"signals": ["counterfeit", "fake shop", "scam", "impersonat", "official store",
                                     "authorized dealer"],
                         "needs": [], "solves": [],
                         "kinds": {"lookalike": 1.0, "fake_shop_candidate": 1.0, "phishing": 1.0}},
    "crm and follow-up": {"signals": ["request a quote", "get a quote", "free estimate", "contact form",
                                      "call us", "book a call"],
                          "needs": ["Meta pixel", "Google Tag", "Google Ads"],
                          "solves": ["HubSpot", "Salesforce", "Pipedrive", "GoHighLevel"],
                          "kinds": {"ads": 0.7, "meta_advertiser": 0.6, "hiring": 0.5}},
    "booking": {"signals": ["call to book", "call us to book", "book by phone", "zavolajte", "objednajte sa",
                            "objednávky telefonicky", "no-show", "waiting list", "opening hours",
                            "ordinačné hodiny", "ordinacne hodiny"],
                "needs": [], "solves": ["Calendly", "Booksy", "Reservio", "Acuity", "SimplyBook"],
                "kinds": {"hiring": 0.6, "ads": 0.4, "change": 0.3}},
    "email outreach": {"signals": ["b2b", "business development", "sales team", "partnerships", "wholesale"],
                       "needs": [], "solves": ["Lemlist", "Instantly", "Manyreach"],
                       "kinds": {"hiring": 0.8, "change": 0.3}},
    "seo": {"signals": ["blog", "articles", "news"], "needs": ["Google Ads"], "solves": ["Semrush", "Ahrefs"],
            "kinds": {"ads": 0.6, "change": 0.5, "hiring": 0.3}},
    "ads management": {"signals": ["campaign", "promo", "sale", "discount"],
                       "needs": ["Meta pixel", "Google Tag", "TikTok pixel", "Google Ads"], "solves": [],
                       "kinds": {"ads": 0.8, "meta_advertiser": 0.8, "hiring": 0.5}},
    "automation": {"signals": ["excel", "spreadsheet", "manual", "copy paste", "paper", "tabuľk", "tabulk"],
                   "needs": [], "solves": ["Zapier", "Make"],
                   "kinds": {"hiring": 0.6, "change": 0.3}},
    "staffing": {"signals": ["we are hiring", "we're hiring", "join our team", "careers", "kariéra", "kariera",
                             "hľadáme", "hladame"],
                 "needs": [], "solves": [], "kinds": {"hiring": 1.0}},
    "ecommerce": {"signals": ["free shipping", "add to cart", "checkout"], "needs": ["Shopify", "WooCommerce"],
                  "solves": [], "kinds": {"ads": 0.6, "meta_advertiser": 0.5, "change": 0.3}},
    "erp": {"signals": ["excel", "spreadsheet", "manual", "paper", "tabuľk", "tabulk", "iso 9001", "cnc",
                        "production planning", "plánovanie výroby", "planovanie vyroby", "zákazková výroba",
                        "zakazkova vyroba", "custom manufacturing", "warehouse", "sklad", "dispatch",
                        "subcontract", "kooperácia", "kooperacia", "lead time", "dodacia lehota"],
            "needs": [], "solves": ["SAP", "Helios", "Pohoda", "Money S3", "Odoo", "Abra", "K2",
                                    "Microsoft Dynamics"],
            "kinds": {"hiring": 0.8, "change": 0.4, "company": 0.3}},
}
PAIN_DEFAULT = {"signals": [], "needs": [], "solves": [], "kinds": {"hiring": 0.4, "ads": 0.3, "change": 0.3}}

MUST_CUES = ["must", "need", "needs", "require", "requires", "runs ", "spends ", "uses ",
             "sells ", "has a ", "have a ", "with a funnel", "in the "]
DISQUALIFY_CUES = ["out:", "disqualif", "already use", "already has", "already run",
                   "too small", "no ad spend", "no ads", "no budget", "doesn't", "does not", "never"]
GENERIC_TRIGGERS = ["hiring", "new site", "launch", "ad spend", "copied", "counterfeit", "rebrand", "funding"]

KIND_HALF_LIFE = {"hiring": 21.0, "ads": 30.0, "meta_advertiser": 45.0,
                  "lookalike": 60.0, "fake_shop_candidate": 60.0, "phishing": 60.0, "company": 90.0,
                  "tech": 180.0, "profile": 365.0, "contact": 365.0, "angle": 120.0,
                  "change": 90.0, "tracking_share": 180.0}

# Lead Studio's job kinds, folded onto the kinds the scoring understands.
KIND_ALIASES = {"google-ads": "ads", "ads-spend": "ads", "ad-spend": "ads", "google_ads": "ads",
                "meta-ads": "meta_advertiser", "hiring-signal": "hiring", "job": "hiring",
                "jobs": "hiring", "new-domain": "change", "nrd": "change", "bankruptcy": "company",
                "fake-shop": "fake_shop_candidate", "fake_shop": "fake_shop_candidate",
                "lookalike-domain": "lookalike", "typosquat": "lookalike",
                "qualify": "profile", "site": "profile", "cc": "change", "commoncrawl": "change"}

TRACKING_PATTERNS = [
    ("GTM", r"GTM-[A-Z0-9]{5,9}"),
    ("GA4", r"G-[A-Z0-9]{8,12}"),
    ("Universal Analytics", r"UA-[0-9]{4,12}-[0-9]{1,4}"),
    ("Meta pixel", r"fbq\(\s*['\"]init['\"]\s*,\s*['\"]([0-9]{8,20})['\"]"),
    ("Google Ads", r"AW-[0-9]{6,12}"),
]

SIZE_ORDER = {"solo": 0, "small": 1, "medium": 2, "large": 3}
SIZE_VALUE = {"solo": 0.2, "small": 0.45, "medium": 0.7, "large": 1.0}

STOP_WORDS = {"the", "and", "for", "with", "that", "this", "your", "you", "are", "our",
              "from", "their", "them", "they", "have", "has", "will", "can", "not", "but", "all", "any",
              "who", "what", "when", "into", "over", "more", "most", "than", "then", "also", "just",
              "about", "because", "while", "where", "which", "there", "these", "those", "would",
              "problem", "must", "need", "needs", "price", "month", "per", "competitors", "countries",
              "industries", "out:", "solves", "solve", "helps", "help", "every", "without", "they're",
              "agencies", "freelancers", "already", "wrong", "scale", "spending",
              "only", "keywords", "know", "trusted", "thousands", "stop", "guessing", "winning",
              "play", "store", "cutting", "edge", "businesses", "feature"}


SECTION_CUES = (" our mission", " our vision", " our story", " our team", " our values",
                " who we are", " about us", " our customers", " our partners", " our results",
                " our services", " our products", " get started", " book a demo", " contact us")

PROBLEM_STRONG = re.compile(
    r"(?i)(\bproblem we solve\b|\bthe (?:problem|challenge)\b(?!\s*solv)|\bso they\b|\bso that they\b)")

# Sentences a page prints for legal or polite reasons. They are on the site, but they
# say nothing about what the business sells or what it needs, so they are never a problem.
NOT_A_PROBLEM = ("personal information", "personal data", "legitimate interest",
                 "legitimate purpose", "applicable law", "data protection", "your privacy",
                 "privacy policy", "cookie", "terms and conditions", "terms of use",
                 "your feedback", "we value your feedback", "without you", "thank you for",
                 "help us improve", "let us know", "all rights reserved", "gdpr",
                 "your rights", "give consent", "opt out", "third parties", "newsletter",
                 "subscribe to", "these rights", "goods or services", "data subject",
                 "supervisory authority", "exercise your", "without limitation",
                 "personally identifiable", "commercial purposes", "any purpose",
                 "we reserve", "reserve the right", "this website", "this site",
                 "by using", "by accessing", "by continuing", "at any time",
                 "your account", "the platform", "errors, inaccuracies")

# A second-person promise ("You can finally get the smile of your dreams") reads like a
# problem only because of its "without"; it is marketing copy, not what the buyer lacks.
PROMISE_RX = re.compile(
    r"(?i)^(?:you can|you may|you'll|you will|get |finally |discover |enjoy |introducing |"
    r"book |start |shop |try |call |sign up|subscribe)|\b(?:of your dreams|your dreams|"
    r"free quote|book (?:a|your)|get started|call today|in minutes)\b")

WEAK_PROBLEM = re.compile(r"(?i)\b(?:the problem|problems? we solve|the challenge)\b|\bproblem:")

# Words that make a sentence about something going wrong for the buyer. A sentence needs
# one of these before it is read as a problem, so a 404 list or a terms page adds nothing.
PAIN_WORDS = ("lose", "lost", "waste", "struggl", "guess", "miss", "risk", "manual",
              "hours", "slow", "costly", "expensive", "complex", "chaos", "blind",
              "uncertain", "leak", "duplicate", "spreadsheet", "endless", "frustrat",
              "difficult", "hard to", "broken", "cannot", "can't", "unable", "inaccurate",
              "wrong", "gap ", "no visibility", "guesswork")


def is_problem_sentence(value):
    """False for policy, cookie and thank-you lines that only look like a problem."""
    low = fold(value)
    if not low or not re.search(r"[a-z]", low):
        return False
    if any(cue in low for cue in NOT_A_PROBLEM):
        return False
    return not PROMISE_RX.search(low)


QUOTE_START = re.compile(r"(?i)^(?:i|i'm|i\u2019m|i've|i\u2019ve|i'll|my|me)\b")


def is_site_problem(value):
    """A problem sentence lifted off a page: a closed sentence in the company's voice.

    A customer quote ("I do not feel them ... !!") and a row of menu labels run
    together ("Prototype sharing permissions Share prototypes ...") both pass a
    plain word check, and both read as noise on a card.
    """
    value = (value or "").strip()
    if not is_problem_sentence(value):
        return False
    if re.search(r"[!?]{2,}", value):
        return False
    if QUOTE_START.match(value):
        return False
    if re.search(r"\bI\b", value):
        return False                      # first person: a founder quote, not the card
    if re.search(r"(?i)\bhttps?://|\b[\w-]+\.(?:com|net|org|io|sk|cz)/", value):
        return False                      # a menu row with a path in it, not a sentence
    if re.search(r"^[^.!?]{0,45}[a-z]{3,}\s+[A-Z][a-z]{2,}\s+[a-z]", _collapse_repeats(value)):
        return False                      # "Prototype sharing permissions Share prototypes with ..."
    return True


def _collapse_repeats(value, min_words=2, max_words=60):
    """Drop a phrase the page prints twice in a row ("Our mission Our mission")."""
    words = value.split()
    out, i = [], 0
    while i < len(words):
        size = min(max_words, len(words) - i)
        while size >= min_words:
            block = [w.lower() for w in words[i:i + size]]
            nxt = [w.lower() for w in words[i + size:i + 2 * size]]
            if len(nxt) == size and block == nxt:
                out.extend(words[i:i + size])
                i += 2 * size
                break
            size -= 1
        else:
            out.append(words[i])
            i += 1
    return " ".join(out)


def _problem_text(text):
    """One sentence -> a readable problem line: no repeated text, no word cut in half."""
    clean = re.sub(r"\s+", " ", text or "").strip()
    parts, seen = [], set()
    for part in re.split(r"(?<=[.!?])\s+", clean):
        key = part.strip().lower()
        if key and key not in seen:
            seen.add(key)
            parts.append(part.strip())
    clean = _collapse_repeats(" ".join(parts))
    clean = re.sub(r"(?is)^(?:and|but|or|plus|so)\s+(?=[a-z])", "", clean).strip()
    if clean and clean[0].islower():
        clean = clean[0].upper() + clean[1:]
    inside = re.search(r"(?i)\bthe problem(?: we solve)?\b[\s:.,\-]*", clean)
    if inside and inside.start() > 0 and len(clean) - inside.end() >= 60:
        rest = clean[inside.end():]
        clean = rest[0].upper() + rest[1:]
    cue = re.match(r"(?i)^(?:the\s+)?(?:problem|challenge)(?:\s+we\s+solve)?[\s:.,\-]+(.+)$", clean)
    if cue and len(cue.group(1)) >= 60:
        rest = cue.group(1)
        clean = rest[0].upper() + rest[1:]
    low = clean.lower()
    ends = [low.find(word) for word in SECTION_CUES if low.find(word) > 40]
    if ends:
        clean = clean[:min(ends)].strip()
    if len(clean) < 25:
        return ""
    if len(clean) <= 200:
        return clean
    cut = clean[:200]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    if end >= 60:
        return cut[:end + 1].strip()
    space = cut.rfind(" ")
    return (cut[:space].rstrip(",;:") if space > 40 else cut) + "..."


def kind_of(kind):
    kind = (kind or "").strip().lower()
    return KIND_ALIASES.get(kind, kind)


# Which findings actually point at a company worth writing to, best first. A table full
# of phishing domains is data, but it is not a reason to call a business a prospect.
LEAD_KINDS = ("ads", "meta_advertiser", "hiring", "profile", "change", "new_domain")


def lead_kinds(kinds, limit=3):
    """The market findings in a signal count, most useful first: [(kind, count), ...]."""
    rows = [(kind, int(count)) for kind, count in (kinds or {}).items() if kind in LEAD_KINDS]
    rows.sort(key=lambda row: (LEAD_KINDS.index(row[0]), -row[1]))
    return rows[:limit]


def clamp(value, lo=0.0, hi=1.0):
    return max(lo, min(hi, value))


def fold(value):
    """'Zaluzie' and 'zaluzie' compare equal: no accent, lower case."""
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(ch for ch in text if not unicodedata.combining(ch)).lower().strip()


def tracking_ids(html):
    out = []
    for label, pattern in TRACKING_PATTERNS:
        for hit in re.finditer(pattern, html or ""):
            value = hit.group(1) if hit.groups() and hit.group(1) else hit.group(0)
            ident = "%s:%s" % (label, value.strip())
            if ident not in out:
                out.append(ident)
            if len(out) >= 12:
                return out
    return out


def tranco_value(rank):
    rank = int(rank or 0)
    if rank <= 0:
        return 0.0
    if rank <= 50000:
        return 1.0
    if rank <= 200000:
        return 0.85
    if rank <= 500000:
        return 0.65
    return 0.45


def decay(age_days, half_life_days):
    if age_days <= 0:
        return 1.0
    return math.pow(0.5, age_days / max(1.0, half_life_days))


def size_for_price(price):
    price = int(price or 0)
    if price <= 0:
        return ""
    if price < 80:
        return "solo"
    if price < 300:
        return "small"
    if price < 1500:
        return "medium"
    return "large"


def size_band(label):
    return SIZE_ORDER.get(str(label or "").strip().lower(), -1)


def size_label(value):
    """Map Lead Studio's free-text company sizes ("11-50", "201-500 employees") to a band."""
    text = str(value or "").strip().lower()
    if text in SIZE_ORDER:
        return text
    nums = [int(n) for n in re.findall(r"\d+", text.replace(",", ""))]
    if not nums:
        if "self" in text or "freelanc" in text:
            return "solo"
        return ""
    top = max(nums)
    if top <= 1:
        return "solo"
    if top <= 50:
        return "small"
    if top <= 500:
        return "medium"
    return "large"


def _sentences(text_value):
    out = []
    flat = text_value.replace(";", "\n").replace("!", "\n").replace("?", "\n")
    for line in flat.split("\n"):
        chunk = line.strip()
        if not chunk:
            continue
        if len(chunk) <= 140:
            out.append(chunk)
            continue
        for part in chunk.split(". "):
            piece = part.strip().rstrip(".")
            if piece:
                out.append(piece)
    return out


def _category_of(lower):
    best, best_hits = "", 0
    for label, words in CATEGORY_HINTS.items():
        hits = sum(1 for word in words if word in lower)
        if hits > best_hits:
            best_hits, best = hits, label
    # One word in common is not a category: a blinds maker "vyraba tieniacu techniku" is
    # not an ERP. Two hints, or nothing - an unknown site then gets the neutral profile.
    return best if best_hits >= 2 else ""


def _role_of(lower):
    for role in ROLE_WORDS:
        if " " + role in lower:
            return role
    return ""


def _price_of(text_value):
    for hit in re.finditer(r"[\$€]\s?([0-9][0-9,.]*)\s?([kK])?", text_value):
        before = text_value[max(0, hit.start() - 16):hit.start()].lower()
        if any(w in before for w in ("spend", "under ", "over ", "budget")):
            continue
        after = text_value[hit.end():hit.end() + 12].lower()
        monthly = bool(re.search(r"/\s*(?:mo|mes|m\u011bs)|per month|monthly|mesacne|m\u011bs\u00ed\u010dn",
                                 before + " " + after))
        try:
            amount = float(hit.group(1).replace(",", "").rstrip("."))
        except ValueError:
            continue
        if (hit.group(2) or "").lower() == "k":
            amount *= 1000.0
        # "Shipping $5.00" is a fee on the page, not what the offer costs.
        if amount < 10 and not monthly:
            continue
        return int(amount)
    other = re.search(r"([0-9][0-9,.]*)\s?(?:usd|eur)\s?(?:/mo|per month)?", text_value, re.I)
    if not other:
        return 0
    try:
        return int(float(other.group(1).replace(",", "").rstrip(".")))
    except ValueError:
        return 0


def _clean_phrase(text_value):
    phrase = text_value.strip()
    for lead in ("must have ", "must ", "needs to ", "need to ", "needs ", "need ", "requires ",
                 "require ", "should ", "the product needs "):
        if phrase.lower().startswith(lead):
            phrase = phrase[len(lead):].strip()
    return phrase[:90].lstrip("-").strip().rstrip(".;,").strip()


def _alternatives(phrase):
    if " or " not in phrase.lower():
        return phrase
    parts = [p.strip().lstrip("+").strip() for p in re.split(r"(?i) or ", phrase)]
    return "|".join(p for p in parts if p)


def _keywords_of(lower):
    out = []
    for word in re.findall(r"[a-z0-9][a-z0-9+-]{3,}", lower):
        if word in STOP_WORDS or word in out:
            continue
        out.append(word)
        if len(out) >= 20:
            break
    return out


def _accept_problem(value, strict):
    return is_site_problem(value) if strict else is_problem_sentence(value)


def parse_offer(text_value, strict=False):
    """A pasted offer paragraph -> an editable offer card.

    strict=True is for text lifted off a website: there a problem has to be a
    closed sentence in the company's voice, so a customer quote or a row of
    menu labels is not read as one. A paragraph the person typed keeps the
    lenient check.
    """
    raw = (text_value or "").strip()
    lower = " " + raw.lower().replace("\n", " ") + " "
    sentences = _sentences(raw)
    card = {"category": _category_of(lower), "buyer_role": _role_of(lower), "problem": "",
            "price": _price_of(raw), "min_size": "", "must_have": [], "disqualify": [],
            "competitors": [], "complements": [], "triggers": [], "keywords": _keywords_of(lower),
            "countries": [], "industries": [], "pain_signals": [], "solved_by": [], "language": ""}
    card["min_size"] = size_for_price(card["price"])
    weak_problem = ""
    for text in sentences:
        low = text.lower()
        if PROBLEM_STRONG.search(text):
            if (not card["problem"] and _accept_problem(text, strict)
                    and any(word in low for word in PAIN_WORDS)):
                candidate = _problem_text(text)
                if 30 <= len(candidate) <= 320:
                    card["problem"] = candidate
        elif not weak_problem and (WEAK_PROBLEM.search(text)
                                   or ("without " in low
                                       and any(word in low for word in PAIN_WORDS))):
            if _accept_problem(text, strict):
                candidate = _problem_text(text)
                if 30 <= len(candidate) <= 320:
                    weak_problem = candidate
        if low.startswith("competitor"):
            for part in text[text.find(":") + 1:].split(","):
                name = part.strip().rstrip(".")
                if name and name not in card["competitors"]:
                    card["competitors"].append(name)
            continue
        if low.startswith("complement") or low.startswith("integrates"):
            for part in text[text.find(":") + 1:].split(","):
                name = part.strip().rstrip(".")
                if name and name not in card["complements"]:
                    card["complements"].append(name)
            continue
        if (":" in text and re.match(r"(?i)^(countries?|markets?|krajiny|krajina|trhy|trh|"
                                     r"posobi v|pôsobí v)\b", text.strip())):
            for part in text[text.find(":") + 1:].split(","):
                name = net.known_country(part.strip().rstrip("."))
                if name and name not in card["countries"]:
                    card["countries"].append(name)
            continue
        if low.startswith("industr") or low.startswith("sector"):
            for part in text[text.find(":") + 1:].split(","):
                name = part.strip().rstrip(".").lower()
                if name and name not in card["industries"]:
                    card["industries"].append(name)
            continue
        if any(cue in low for cue in DISQUALIFY_CUES):
            phrase = text
            cut = low.find("out:")
            skip = 4
            if cut < 0:
                cut = low.find("disqualif")
                skip = 10
            if cut >= 0:
                phrase = text[cut + skip:]
            clean = _clean_phrase(phrase)
            if clean and len(card["disqualify"]) < 6:
                card["disqualify"].append(clean)
            continue
        if any(cue in low for cue in MUST_CUES) and len(card["must_have"]) < 6:
            clean = _alternatives(_clean_phrase(text))
            if clean:
                card["must_have"].append(clean)
    for role in ROLE_WORDS:
        if " " + role in lower and len(card["triggers"]) < 8:
            card["triggers"].append(role)
    for word in GENERIC_TRIGGERS:
        if word in lower and len(card["triggers"]) < 10:
            card["triggers"].append(word)
    for tool, needles in TOOL_NEEDLES.items():
        if not any(n in lower for n in needles):
            continue
        switching = False
        for sentence in sentences:
            low = sentence.lower()
            if tool.lower() not in low:
                continue
            if any(cue in low for cue in ("instead of", "already use", "already has", "already run",
                                          "switch", "replace", "vs ", "migrate")):
                switching = True
                break
        bucket = card["competitors"] if switching else card["complements"]
        if tool not in bucket:
            bucket.append(tool)
    card["complements"] = [c for c in card["complements"] if c not in card["competitors"]]
    card["problem"] = card["problem"] or weak_problem
    return card


def _list(value):
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        return [p.strip() for p in value.split(",") if p.strip()]
    return []


def normalise_card(card):
    """Fill every key so the UI and the scorer can trust the shape."""
    base = parse_offer("")
    out = dict(base)
    for key, value in (card or {}).items():
        out[key] = value
    for key in ("must_have", "disqualify", "competitors", "complements", "triggers", "keywords",
                "countries", "industries", "pain_signals", "solved_by"):
        out[key] = _list(out.get(key))
    countries = []
    for value in out["countries"]:
        name = (net.known_country(value)
                or (str(value).strip() if len(str(value).strip()) <= 24 else ""))
        if name and name not in countries:
            countries.append(name)
    out["countries"] = countries
    try:
        out["price"] = int(float(out.get("price") or 0))
    except (TypeError, ValueError):
        out["price"] = 0
    for key in ("category", "buyer_role", "problem", "min_size", "language"):
        out[key] = str(out.get(key) or "").strip()
    return out


def card_from_offer(offer, saved_card=None):
    if saved_card:
        return normalise_card(saved_card)
    text = "%s. %s. %s" % (offer.get("name", ""), offer.get("description", ""), offer.get("keywords", ""))
    card = parse_offer(text)
    kws = _list(offer.get("keywords", ""))
    if kws:
        card["keywords"] = kws
    cat = (offer.get("category") or "").strip().lower()
    if cat in CATEGORY_HINTS and not card["category"]:
        card["category"] = cat
    return normalise_card(card)


def assistant_view(card):
    """What the Assistant read about the business, in the shape the hunt targets with.

    It reads `targeting`, which the read writes on the card, and falls back to the stored
    read itself, so a card saved before that field existed still points the hunt right.
    """
    card = card or {}
    view = card.get("targeting") if isinstance(card.get("targeting"), dict) else {}
    brief = card.get("brief") if isinstance(card.get("brief"), dict) else {}
    profile = brief.get("profile") if isinstance(brief.get("profile"), dict) else {}
    pains = brief.get("pains") if isinstance(brief.get("pains"), dict) else {}
    cases = view.get("use_cases") or [item.get("name") for item in (profile.get("use_cases") or [])
                                      if isinstance(item, dict) and item.get("name")]
    kinds = view.get("kinds") or pains.get("kinds") or {}
    raw = view.get("signals") if isinstance(view.get("signals"), dict) else {}
    if not raw and isinstance(brief.get("signals"), dict):
        raw = brief["signals"]          # a card saved before targeting carried them
    sig_kinds = raw.get("kinds") if isinstance(raw.get("kinds"), dict) else {}
    return {"buyer_role": str(view.get("buyer_role") or profile.get("buyer_role")
                              or card.get("buyer_role") or "").strip(),
            "use_cases": [case for case in _list(cases) if len(case) >= 3][:6],
            "needs": _list(view.get("needs") or pains.get("needs"))[:8],
            "kinds": {kind_of(str(k)): float(v) for k, v in dict(kinds).items()
                      if isinstance(v, (int, float)) and not isinstance(v, bool)},
            "markets": _list(view.get("markets") or profile.get("countries"))[:6],
            "signals": {"total": int(raw.get("total") or 0),
                        "kinds": {kind_of(str(k)): int(v) for k, v in sig_kinds.items()
                                  if isinstance(v, (int, float)) and not isinstance(v, bool)},
                        "words": _list(raw.get("words"))[:12],
                        "flags": _list(raw.get("flags"))[:10],
                        "markets": _list(raw.get("markets"))[:6],
                        "read_at": int(raw.get("read_at") or 0)},
            "source": str(view.get("source") or brief.get("host") or "").strip(),
            "read_at": int(view.get("read_at") or 0)}


def company_text(company):
    parts = [str(company.get(k, "") or "") for k in
             ("name", "domain", "industry", "size", "technologies", "tags", "notes", "description")]
    for ev in company.get("evidence", []) or []:
        parts.append("%s %s %s %s %s" % (ev.get("kind", ""), ev.get("title", ""), ev.get("snippet", ""),
                                         ev.get("detail", ""), ev.get("source", "")))
    return " ".join(parts).lower()


def tools_in(text_value):
    text_value = (text_value or "").lower()
    return [tool for tool, needles in TOOL_NEEDLES.items() if any(n in text_value for n in needles)]


def _has_any(text_value, names):
    for name in names or []:
        if str(name).lower() and str(name).lower() in text_value:
            return str(name)
    return ""


def _must_match(text_value, phrase, tools, evidence):
    low = phrase.lower().strip()
    if low and low in text_value:
        return True
    if _has_any(low, tools):
        return True
    words = [w.lstrip("+") for w in low.split() if len(w.lstrip("+")) >= 4 and w not in STOP_WORDS]
    if not words:
        return False
    hits = sum(1 for w in words if w in text_value)
    if hits / float(len(words)) >= 0.6:
        return True
    if any(w in low for w in ("spend", "ads", "paid ad", "advert")):
        if any(kind_of(ev.get("kind")) == "ads" for ev in evidence):
            return True
    if any(w in low for w in ("funnel", "store", "ecommerce")):
        if any(t in tools for t in ("Shopify", "WooCommerce", "ClickFunnels", "GoHighLevel", "Kajabi",
                                    "Wix", "Squarespace")):
            return True
    return False


def _fit(company, card, text_value, tools):
    view = assistant_view(card)
    terms = list(card.get("keywords", []) or []) + view["use_cases"] + view["signals"]["words"]
    folded = fold(text_value)
    hits = sum(1 for t in terms if len(str(t).strip()) >= 4 and fold(t).strip() in folded)
    term_score = clamp(hits / 4.0) if terms else 0.5
    want = size_band(card.get("min_size", ""))
    have = size_band(company.get("size", ""))
    size_score = 0.5
    if want >= 0 and have >= 0:
        size_score = 1.0 if have >= want else 0.25
    elif want >= 0:
        size_score = 0.4
    category = str(card.get("category", "")).lower()
    cat_score = 0.4
    if category and any(w in text_value for w in CATEGORY_HINTS.get(category, [])):
        cat_score = 1.0
    if card.get("industries"):
        if any(str(i).lower() in text_value for i in card["industries"]):
            cat_score = 1.0
    return clamp(0.45 * term_score + 0.35 * cat_score + 0.2 * size_score)


def pain_profile(card):
    category = str(card.get("category", "")).lower()
    base = dict(PAIN_PROFILES.get(category, PAIN_DEFAULT))
    base = {k: (list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v)
            for k, v in base.items()}
    view = assistant_view(card)
    if view["needs"]:
        base["needs"] = view["needs"]
    if view["kinds"]:
        base["kinds"] = dict(view["kinds"])
    own_signals = [str(s).strip().lower() for s in card.get("pain_signals", []) or [] if str(s).strip()]
    if own_signals:
        base["signals"] = own_signals
    elif category not in PAIN_PROFILES:
        words = []
        for word in re.findall(r"[a-z]{5,}", fold(str(card.get("problem") or ""))):
            if word not in STOP_WORDS and word not in words:
                words.append(word)
        base["signals"] = words[:8]
    sig = view["signals"]
    if sig.get("flags"):
        # A job's flag ("no online booking") is a pain only when the card's own words
        # already cover it; otherwise it is another offer's finding, not this one.
        own = fold(" ".join([category, str(card.get("problem") or "")]
                            + [str(k) for k in card.get("keywords", []) or []]
                            + view["use_cases"]))
        flags = [flag for flag in sig["flags"]
                 if any(len(word) >= 4 and word in own for word in fold(flag).split())]
        if flags and not own_signals:
            base["signals"] = (list(base.get("signals") or []) + flags)[:8]
    if sig.get("kinds") and base.get("kinds"):
        # Findings you actually have lift the kinds that point at this offer's problem.
        top = max([sig["kinds"].get(kind, 0) for kind in base["kinds"]] or [0])
        if top:
            for kind, weight in list(base["kinds"].items()):
                seen = sig["kinds"].get(kind, 0)
                if seen:
                    base["kinds"][kind] = round(max(float(weight), 0.4 + 0.5 * min(1.0, seen / float(top))), 2)
    own_solves = [str(s).strip() for s in card.get("solved_by", []) or [] if str(s).strip()]
    if own_solves:
        base["solves"] = own_solves
    base["category"] = category
    return base


def pain_detail(text_value, card, tools, evidence, now):
    """How clearly the company shows the problem the offer solves, 0..1, with reasons."""
    profile = pain_profile(card)
    pain, reasons = 0.0, []
    needed = []
    for tool in profile.get("needs", []):
        if tool in tools and tool not in needed:
            needed.append(tool)
    if len(needed) >= 2:
        pain += 0.4
    elif len(needed) == 1:
        pain += 0.2
    solver = ""
    for tool in profile.get("solves", []):
        if tool in tools or tool.lower() in text_value:
            solver = tool
            break
    competitors = card.get("competitors", []) or []
    competitor = _has_any(text_value, competitors)
    if not competitor and solver and solver in competitors:
        competitor = solver
    if competitor:
        pain += 0.25
        reasons.append("runs %s - a switch" % competitor)
    elif solver:
        pain *= 0.4
        reasons.append("already runs " + solver)
    elif needed:
        pain += 0.4
        reasons.append("runs %s and nothing that handles it" % ", ".join(needed[:3]))
    hits = []
    for word in profile.get("signals", []):
        needle = str(word).lower()
        if len(needle) >= 3 and needle in text_value and needle not in hits:
            hits.append(needle)
    if hits:
        pain += min(0.5, 0.2 * len(hits))
        reasons.append("their site or findings say " + ", ".join('"%s"' % h for h in hits[:3]))
    kinds = profile.get("kinds", {})
    best, best_title = 0.0, ""
    for ev in evidence or []:
        kind = kind_of(ev.get("kind"))
        if kind not in kinds:
            continue
        age = max(0, now - int(ev.get("ts") or now)) / 86400.0 if now else 0.0
        if age > 180:
            continue
        if kinds[kind] > best:
            best = kinds[kind]
            best_title = str(ev.get("title") or kind)
    if best > 0:
        pain += 0.5 * best
        if best_title and len(reasons) < 3:
            reasons.append(best_title[:70].lower())
    if _has_any(text_value, card.get("complements", [])):
        pain += 0.1
    return {"value": clamp(pain), "reasons": reasons, "profile": profile.get("category", "")}


def _kind_strength(kind, row, card):
    title = str(row.get("title", "")).lower()
    if kind == "hiring":
        for trigger in card.get("triggers", []) or []:
            if str(trigger).lower() in title:
                return 1.0
        return 0.55
    if kind in ("ads", "meta_advertiser"):
        return 0.8
    if kind in ("lookalike", "fake_shop_candidate", "phishing"):
        return 0.9 if "brand protection" in str(card.get("category", "")).lower() else 0.5
    return {"tech": 0.3, "company": 0.45, "contact": 0.1, "profile": 0.1, "angle": 0.1,
            "change": 0.75, "tracking_share": 0.4}.get(kind, 0.2 if kind else 0.0)


def _timing(company, card, now):
    best, recent = 0.0, 0
    for ev in company.get("evidence", []) or []:
        kind = kind_of(ev.get("kind"))
        strength = _kind_strength(kind, ev, card)
        if strength <= 0:
            continue
        age = max(0, now - int(ev.get("ts") or 0)) / 86400.0
        value = strength * decay(age, KIND_HALF_LIFE.get(kind, 120.0))
        if value > 0.25:
            recent += 1
        best = max(best, value)
    return clamp(best + 0.1 * max(0, recent - 1))


def _pay(company, now):
    pay = SIZE_VALUE.get(str(company.get("size", "")).lower(), 0.0)
    pay = max(pay, tranco_value(company.get("tranco", 0)))
    jobs = 0
    for ev in company.get("evidence", []) or []:
        kind = kind_of(ev.get("kind"))
        if kind == "hiring":
            jobs += 1
        if kind in ("ads", "meta_advertiser"):
            payload = ev.get("payload") or {}
            if isinstance(payload, dict):
                imp = int(payload.get("impressions") or 0)
                if imp >= 1000000:
                    pay = max(pay, 1.0)
                elif imp >= 250000:
                    pay = max(pay, 0.8)
                elif imp >= 50000:
                    pay = max(pay, 0.6)
                elif imp > 0:
                    pay = max(pay, 0.4)
                else:
                    pay = max(pay, 0.55)
            else:
                pay = max(pay, 0.55)
    if not company.get("size"):
        if jobs >= 10:
            pay = max(pay, 0.85)
        elif jobs >= 5:
            pay = max(pay, 0.65)
        elif jobs >= 2:
            pay = max(pay, 0.45)
        elif jobs >= 1:
            pay = max(pay, 0.3)
    if pay <= 0 and now > 0:
        pay = 0.3
    return clamp(pay)


def _why(company, card, tools, now, pain_reasons=None):
    bits = []
    category = str(card.get("category", "")).lower()
    attribution_offer = category == "attribution"
    pixels = sum(1 for t in ("Meta pixel", "Google Tag", "TikTok pixel", "LinkedIn Insight") if t in tools)
    attribution = next((t for t in ATTRIBUTION_TOOLS if t in tools), "")
    for ev in company.get("evidence", []) or []:
        if len(bits) >= 3:
            break
        kind = kind_of(ev.get("kind"))
        age = int(max(0, now - int(ev.get("ts") or 0)) / 86400)
        when = "today" if age <= 0 else ("%d days ago" % age if age < 60 else "months ago")
        if kind == "ads":
            payload = ev.get("payload") or {}
            imp = int(payload.get("impressions") or 0) if isinstance(payload, dict) else 0
            bits.append("spends on Google ads%s (%s)" % ((" - %d impressions" % imp) if imp else "", when))
        elif kind == "hiring":
            title = str(ev.get("title") or "hiring")
            cut = title.lower().find("hiring:")
            role = (title[cut + 7:].strip() if cut >= 0 else title)[:48]
            if not any(b.startswith("hiring " + role) for b in bits):
                bits.append("hiring %s (%s)" % (role, when))
        elif kind in ("lookalike", "fake_shop_candidate", "phishing"):
            bits.append("%s (%s)" % (ev.get("title") or "a copycat site", when))
        elif kind == "meta_advertiser":
            bits.append("%s (%s)" % (ev.get("title") or "advertising on Meta", when))
        elif kind == "change":
            bits.append("%s (%s)" % (str(ev.get("title") or "the site changed").lower()[:80], when))
        elif kind == "tracking_share":
            bits.append(str(ev.get("title") or "shares a tracking ID").lower())
    if attribution_offer or not category:
        if pixels >= 2 and not attribution:
            bits.append("runs %d ad pixels and no attribution tool" % pixels)
        elif attribution:
            bits.append("already runs " + attribution)
        elif pixels == 1:
            bits.append("runs an ad pixel")
    text_value = company_text(company)
    folded = fold(text_value)
    view = assistant_view(card)
    for case in view["use_cases"][:3]:
        if len(bits) >= 3:
            break
        if len(case) >= 4 and fold(case) in folded and not any(case.lower() in b.lower() for b in bits):
            bits.append("their site talks about " + case.lower())
    for word in view["signals"]["words"]:
        if len(bits) >= 3:
            break
        if len(word) >= 5 and fold(word) in folded and not any(word.lower() in b.lower() for b in bits):
            bits.append("your job results list " + word.lower() + " too")
    for reason in pain_reasons or []:
        if len(bits) >= 3:
            break
        dupe = any(reason[:24].lower() in b.lower() for b in bits)
        if dupe or (attribution_offer and reason.startswith("runs ")):
            continue
        bits.append(reason)
    traffic = [t for t in ("Shopify", "WooCommerce", "ClickFunnels", "GoHighLevel", "Kajabi", "Klaviyo",
                           "HubSpot") if t in tools]
    if traffic:
        bits.append("runs " + ", ".join(traffic[:3]))
    if not bits:
        return "no signal yet - read the site to find one"
    return "; ".join(bits[:3])[:220]


def score(company, card, weights=None, now=0):
    """Fit x Pain x Timing x Ability to pay -> 0..100, with the reasons and the blockers."""
    weights = weights or {}
    text = company_text(company)
    tools = tools_in(text)
    evidence = company.get("evidence", []) or []
    fit = _fit(company, card, text, tools)
    pain_info = pain_detail(text, card, tools, evidence, now)
    pain = pain_info["value"]
    timing = _timing(company, card, now)
    pay = _pay(company, now)

    def w(key):
        try:
            return clamp(float(weights.get(key, 1.0)), 0.0, 2.0)
        except (TypeError, ValueError):
            return 1.0
    f, p, t, a = (clamp(fit * w("fit")), clamp(pain * w("pain")), clamp(timing * w("timing")),
                  clamp(pay * w("pay")))
    score_value = int(round(100.0 * f * p * t * a))
    blockers = []
    for group in card.get("must_have", []) or []:
        if not any(_must_match(text, alt, tools, evidence) for alt in str(group).split("|") if alt.strip()):
            blockers.append("no sign of " + str(group))
    for phrase in card.get("disqualify", []) or []:
        low = str(phrase).lower()
        if (low and low in text) or _has_any(low, tools):
            blockers.append("out: " + str(phrase))
    want = size_band(card.get("min_size", ""))
    have = size_band(company.get("size", ""))
    if want >= 0 and have >= 0 and have < want:
        blockers.append("too small for the price (needs %s)" % card.get("min_size", ""))
    blockers = blockers[:3]
    return {"score": score_value, "fit": round(fit, 2), "pain": round(pain, 2),
            "timing": round(timing, 2), "pay": round(pay, 2),
            "why": _why(company, card, tools, now, pain_info["reasons"]),
            "blockers": blockers, "pain_reasons": pain_info["reasons"], "pass": not blockers,
            "tools": tools, "domain": company.get("domain", ""), "name": company.get("name", ""),
            "email": company.get("email", ""), "size": company.get("size", ""),
            "evidence_count": len(evidence)}


# ------------------------------------------------------------------ hunt rules
DEFAULT_PULL = 2000
DEFAULT_KEEP = 200
TTL_DNS = 30 * 86400
TTL_CRAWL = 14 * 86400

TLD_COUNTRY = {"sk": "Slovakia", "cz": "Czechia", "de": "Germany", "at": "Austria",
               "uk": "United Kingdom", "us": "United States", "fr": "France", "es": "Spain", "it": "Italy",
               "nl": "Netherlands", "pl": "Poland", "ie": "Ireland", "pt": "Portugal", "ch": "Switzerland",
               "hu": "Hungary", "ro": "Romania", "ca": "Canada", "au": "Australia", "be": "Belgium",
               "dk": "Denmark", "se": "Sweden", "fi": "Finland", "no": "Norway", "gr": "Greece",
               "hr": "Croatia", "si": "Slovenia"}
ROLE_GUESSES = {"Slovakia": ["info", "kontakt", "office", "obchod"],
                "Czechia": ["info", "kontakt", "office", "obchod"],
                "Germany": ["info", "kontakt", "office"], "Austria": ["info", "office", "kontakt"],
                "Poland": ["biuro", "kontakt", "info"], "Hungary": ["info", "iroda"]}
ROLE_DEFAULT = ["info", "hello", "contact", "office", "sales"]
ROLE_LOCALS = ["info", "contact", "kontakt", "office", "sales", "hello", "enquiries", "inquiries",
               "support", "admin", "team", "mail", "obchod", "biuro", "iroda"]
FREE_MAIL = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "seznam.cz", "centrum.sk",
             "azet.sk", "zoznam.sk", "post.sk", "gmx.de", "web.de", "icloud.com"}
PHRASE_STOP = {"a", "an", "the", "and", "or", "of", "for", "with", "who", "that", "any", "companies",
               "company", "businesses", "business", "firms", "people", "already", "use", "uses",
               "using", "run", "runs", "eur", "usd", "month", "year"}


def country_of(company):
    stated = net.normalise_country(company.get("country", ""))
    if stated:
        return stated
    domain = str(company.get("domain", ""))
    return TLD_COUNTRY.get(domain.rsplit(".", 1)[-1].lower() if "." in domain else "", "")


def card_countries(card):
    out = []
    for item in _list(card.get("countries", [])) or assistant_view(card)["markets"]:
        name = net.known_country(item) or (str(item).strip() if len(str(item).strip()) <= 24 else "")
        if name and name not in out:
            out.append(name)
    return out


def include_terms(card):
    terms = [i.lower() for i in _list(card.get("industries", []))]
    if not terms:
        terms = [k.lower() for k in _list(card.get("keywords", [])) if len(k) >= 3]
    for case in assistant_view(card)["use_cases"]:
        if case.lower() not in terms:
            terms.append(case.lower())
    return terms


def _forms(phrase):
    out = [phrase]
    if phrase.endswith("ies") and len(phrase) > 4:
        out.append(phrase[:-3] + "y")
    elif phrase.endswith("s") and not phrase.endswith("ss") and len(phrase) > 4:
        out.append(phrase[:-1])
    elif phrase.endswith("y") and len(phrase) > 3:
        out.append(phrase[:-1] + "ies")
    return out


def exclude_terms(card):
    terms = []
    for item in _list(card.get("disqualify", [])):
        text = item.lower()
        for sep in (";", "/", ".", " and ", " or ", " nor ", "(", ")", ":"):
            text = text.replace(sep, ",")
        for part in text.split(","):
            words = []
            for word in part.strip().split():
                word = word.strip().rstrip("!")
                if re.fullmatch(r"[\d.,]+", word):
                    continue
                if word not in PHRASE_STOP:
                    words.append(word)
            if not words or len(words) > 3:
                continue
            phrase = " ".join(words)
            if len(phrase) < 3:
                continue
            for form in _forms(phrase):
                if form not in terms:
                    terms.append(form)
    return terms


def pull_block(company, card, countries, include, exclude):
    """'' when the company may enter the hunt, else the reason it was left out."""
    text = company_text(company)
    folded = fold(text)
    if countries:
        country = country_of(company)
        if not country or country not in countries:
            return "outside the countries on the card"
    if include and not any(fold(word) in folded for word in include):
        view = assistant_view(card)
        extra = view["use_cases"] + list(view["needs"]) + list(card.get("pain_signals") or [])
        if not any(len(str(word)) >= 4 and fold(word) in folded for word in extra):
            return "not in the industries on the card"
    for word in exclude:
        if fold(word) in folded:
            return "out: " + word
    want = size_band(card.get("min_size", ""))
    have = size_band(company.get("size", ""))
    if want >= 0 and have >= 0 and have < want:
        return "too small for the price"
    return ""


def rank(scored):
    """The hunt sorts by a weighted sum, so fit still orders companies with no fresh trigger."""
    base = 100.0 * (0.4 * scored.get("fit", 0) + 0.2 * scored.get("pain", 0) +
                    0.25 * scored.get("timing", 0) + 0.15 * scored.get("pay", 0))
    return round(clamp(base + 0.3 * float(scored.get("score", 0)), 0.0, 100.0), 1)


def angle(company, sector, offer_name, why):
    """The opening line a soldier writes from."""
    bits = []
    if why and not why.startswith("no signal yet"):
        bits.append(why[:1].upper() + why[1:].rstrip(".") + ".")
    who = sector or str(company.get("industry", "")).lower() or "business"
    size = str(company.get("size", ""))
    lead_in = ("A %s %s" % (size, who)) if size in SIZE_ORDER else ("A " + who)
    if size == "solo":
        lead_in = "A one-person " + who
    city = str(company.get("city", "")).strip()
    if city:
        lead_in += " in " + city
    tools = []
    for part in str(company.get("technologies", "")).split(","):
        tool = part.strip()
        if tool and len(tools) < 2 and tool not in tools:
            tools.append(tool)
    if tools:
        lead_in += " running " + " and ".join(tools)
    bits.append(lead_in + ".")
    short = offer_name.split(" - ")[0].strip()
    if short:
        bits.append("Open with what %s changes for a %s like theirs." % (short, who))
    return " ".join(bits)[:320]


def hard_blockers(blockers):
    return [b for b in blockers or [] if b.startswith("out:") or b.startswith("too small")]


def email_domain(address):
    return address.split("@", 1)[1].lower() if "@" in (address or "") else ""


def is_role(address):
    local = (address or "").split("@", 1)[0].lower()
    return any(local == r or local.startswith(r + ".") for r in ROLE_LOCALS)


def confidence(address, source, domain, extra=None):
    """0-100: how far an address can be trusted, from where it came from."""
    extra = extra or {}
    edom = email_domain(address)
    on_domain = edom == domain or edom.endswith("." + domain)
    free = edom in FREE_MAIL
    value = 0
    if source == "site":
        value = 78 if is_role(address) else 88
    elif source == "dataset":
        value = 72
    elif source == "archive":
        value = 62
    elif source == "guess":
        if extra.get("accepted") and not extra.get("catch_all"):
            value = 55
        elif extra.get("catch_all"):
            value = 32
        else:
            value = 22
    if not on_domain:
        value -= 10 if free else 18
    if not extra.get("mx", True):
        value -= 40
    return int(clamp(value, 1, 99))


def source_label(source, extra=None):
    extra = extra or {}
    if source == "site":
        return "found on their site"
    if source == "dataset":
        return "already in your leads"
    if source == "archive":
        return "on an archived contact page"
    if source == "guess":
        if extra.get("catch_all"):
            return "guessed - their server accepts every address"
        if extra.get("accepted"):
            return "guessed - their mail server accepted it"
        return "guessed - not checked"
    return source


_ASCII = str.maketrans("áäčďéěíĺľňóôöřŕšťúůüýžÁÄČĎÉĚÍĹĽŇÓÔÖŘŔŠŤÚŮÜÝŽß",
                       "aacdeeillnooorrstuuuyzAACDEEILLNOOORRSTUUUYZs")


def guesses(domain, first="", last="", country=""):
    out = []
    f = (first or "").strip().lower().translate(_ASCII).replace(" ", "")
    l = (last or "").strip().lower().translate(_ASCII).replace(" ", "")
    if f and l:
        out += ["%s.%s@%s" % (f, l, domain), "%s@%s" % (f, domain), "%s%s@%s" % (f[:1], l, domain)]
    elif f:
        out.append("%s@%s" % (f, domain))
    for role in ROLE_GUESSES.get(country, ROLE_DEFAULT):
        address = "%s@%s" % (role, domain)
        if address not in out:
            out.append(address)
    return out[:5]


def funnel_rows(funnel):
    return [
        ["pulled", "Pulled by the card", int(funnel.get("pulled", 0))],
        ["checked", "Can receive mail", int(funnel.get("checked", 0))],
        ["kept", "Best by score", int(funnel.get("kept", 0))],
        ["with_email", "With an address", int(funnel.get("with_email", 0))],
        ["approved", "Approved", int(funnel.get("approved", 0))],
        ["sent", "Sent", int(funnel.get("sent", 0))],
    ]
