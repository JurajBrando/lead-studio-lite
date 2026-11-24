"""One link in, a whole Radar view out.

It reads the business a link points at - the home page, /about, /pricing, /team
and /contact - and turns the pages into what Radar works with: what it sells and
to whom, the people and mailboxes named on the site, the rivals the site itself
names, the pains the offer answers, and six scored campaign ideas with a brief
for each.

Plain rules, no LLM: every name and number comes from a page that was fetched.
Nothing is invented, and a field the site does not show stays empty.
"""
import concurrent.futures
import re
import time
import urllib.parse

from . import daily, net, offers, radar, site_read
from . import hunt

TIMEOUT = 15
PAGE_CHARS = 30000
MAX_PEOPLE = 12
MAX_RIVALS = 8
ALT_CHECK = 12
ALT_KEEP = 4

# Pages that talk about a business instead of being one.
ALT_SKIP = {"wikipedia.org", "g2.com", "capterra.com", "reddit.com", "youtube.com", "linkedin.com",
            "facebook.com", "twitter.com", "x.com", "quora.com", "trustpilot.com", "crunchbase.com",
            "play.google.com", "apps.apple.com", "producthunt.com", "getapp.com", "softwareadvice.com",
            "ycombinator.com", "medium.com", "github.com", "pinterest.com", "instagram.com",
            "glassdoor.com", "indeed.com", "amazon.com", "ebay.com", "sitelike.org", "similarsites.com",
            "alternativeto.net", "cutestat.com", "similarweb.com", "semrush.com", "ahrefs.com",
            "spyfu.com", "builtwith.com", "wappalyzer.com", "saasworthy.com", "sourceforge.net"}

PAGES = (
    ("home", ("",)),
    ("about", ("/about", "/about-us", "/o-nas", "/company")),
    ("pricing", ("/pricing", "/prices", "/cennik", "/plans")),
    ("team", ("/team", "/people", "/our-team")),
    ("contact", ("/contact", "/contact-us", "/kontakt")),
)

EMAIL_RX = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
NAME_RX = re.compile(r"\b([A-Z][\w'\-]{1,18}(?:\s+[A-Z][\w'\-]{1,18}){1,2})\b")
RIVAL_CUE = re.compile(
    r"(?i)\b(?:vs\.?|versus|alternative(?:s)?\s+to|compared\s+to|compare(?:d)?\s+with|competitors?)\b")
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico")

PERSON_ROLES = ("co-founder", "founder", "owner", "ceo", "cto", "cmo", "coo",
                "managing director", "director", "head of", "manager", "partner",
                "account executive", "sales lead", "marketing lead", "specialist")

STOP_NAMES = {"contact", "about", "team", "pricing", "home", "blog", "login", "sign", "book",
              "demo", "free", "get", "start", "learn", "more", "read", "our", "your", "the",
              "meet", "join", "welcome", "hi", "hey", "say", "says", "said", "written", "posted", "by",
              "lead", "product", "design", "engineering", "operations", "success", "growth",
              "support", "sales", "marketing", "chief", "senior", "staff", "manager", "director",
              "privacy", "terms", "cookies", "careers", "features", "solutions", "case", "cases",
              "studies", "agency", "hook", "scale", "meta", "legal", "policy", "guide", "report",
              "template", "ebook", "webinar", "customers", "partners", "resources", "company",
              "signup", "trial", "article", "admin", "settings"}

RIVAL_START = {"run", "get", "try", "use", "see", "learn", "read", "start", "watch", "play",
               "explore", "download", "install", "switch", "compare", "vs", "versus", "how",
               "what", "why", "the", "a", "an", "our", "your", "their", "this", "that",
               "click", "book", "sign", "join", "meet", "find", "check", "build", "create"}

IDEAS = (
    {"key": "switch", "angle": "competitor", "kind": None,
     "title": "Switch: what {rival} hides",
     "segment": "companies already running {rival}",
     "hook": "{rival} gives them a number they cannot open up. {offer} shows the same spend "
             "row by row, so the next budget call is a fact, not an argument."},
    {"key": "cost", "angle": "pain", "kind": None,
     "title": "What {pain} costs them every month",
     "segment": "companies that show the problem on their own site",
     "hook": "Every week {pain} stays unfixed is money that never shows up in a report. "
             "{offer} puts a number on it and closes it."},
    {"key": "hiring", "angle": "timing", "kind": "hiring",
     "title": "They are hiring for it",
     "segment": "companies with a hiring signal in the last 60 days",
     "hook": "A new hire is the moment the old way stops fitting. {offer} is in place before "
             "the first month of their salary is spent."},
    {"key": "ads", "angle": "timing", "kind": "ads",
     "title": "They are buying ads right now",
     "segment": "companies with live ad spend",
     "hook": "The ad budget is already approved; the question is whether it can be measured. "
             "{offer} answers that for {buyer}."},
    {"key": "proof", "angle": "roi", "kind": None,
     "title": "One number for {buyer}",
     "segment": "companies where {buyer} owns the decision",
     "hook": "{buyer} does not buy a tool, they buy a number they can defend. {offer} gives "
             "them that number in the first report."},
    {"key": "quickwin", "angle": "pain", "kind": None,
     "title": "The first win inside a week",
     "segment": "small teams that want proof before a contract",
     "hook": "No migration, no new process: {offer} starts on what they already run and shows "
             "the difference in the first week."},
)


# ------------------------------------------------------------------ reading
def _base(value):
    value = (value or "").strip()
    if not value:
        return "", ""
    if not value.lower().startswith(("http://", "https://")):
        value = "https://" + value
    parts = urllib.parse.urlsplit(value)
    if not parts.netloc:
        return "", ""
    return "%s://%s" % (parts.scheme, parts.netloc), parts.netloc.lower()


def _fetch(url):
    try:
        return net.text(url, timeout=TIMEOUT, retries=0) or ""
    except net.NetError:
        return ""


def _text(html):
    body = re.sub(r"\s+", " ", site_read.visible_text(html or ""))
    return body.strip()[:PAGE_CHARS]


# ------------------------------------------------------------------ alternatives
def _domain_of(url):
    try:
        host = urllib.parse.urlsplit(str(url or "").strip()).netloc.lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def _terms(profile):
    words = [str(k).strip().lower() for k in (profile.get("keywords") or [])
             if len(str(k).strip()) >= 4 and site_read.fold(k) not in site_read.NAV]
    words += [w.lower() for w in re.split(r"[\s.\-]+", str(profile.get("name") or "")) if len(w) >= 4]
    out = []
    for word in words:
        if word not in out:
            out.append(word)
    return out[:12]


def _overlap(text, terms):
    low = (text or "").lower()
    return sum(1 for term in terms if term and term in low)


def _search_candidates(name, host):
    """The businesses that come up as alternatives to this one. Empty without a search key."""
    if not name:
        return [], "no business name was read"
    try:
        from . import dorks
        results = dorks.search('alternatives to "%s"' % name, num=10)
    except Exception as exc:                       # noqa: BLE001 - no key, no credits, no network
        if "key" in str(exc).lower():
            return [], "no search key - used the rivals the site names"
        return [], "search did not answer - used the rivals the site names"
    out, seen = [], {host}
    for item in results:
        domain = _domain_of(item.get("link"))
        if not domain or domain in seen:
            continue
        if host and (domain == host or host.endswith("." + domain)):
            continue
        if any(domain == skip or domain.endswith("." + skip) for skip in ALT_SKIP):
            continue
        if domain.count(".") > 2:                  # mirrors and parked aggregates
            continue
        seen.add(domain)
        out.append({"domain": domain, "title": item.get("title") or "", "snippet": item.get("snippet") or ""})
        if len(out) >= ALT_CHECK:
            break
    return out, ("%d result(s)" % len(out)) if out else "no result - used the rivals the site names"


def _check_candidates(candidates, terms):
    """Fetch each candidate and keep the ones whose own words overlap this business."""
    if not candidates:
        return []

    def worker(item):
        text = _text(_fetch("https://" + item["domain"]))[:6000]
        score = _overlap(" ".join([item.get("title", ""), item.get("snippet", ""), text]), terms)
        return dict(item, score=score, read=len(text))

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(candidates))) as pool:
        rows = list(pool.map(worker, candidates))
    rows.sort(key=lambda row: -row["score"])
    close = [row for row in rows if row["score"] > 0][:ALT_KEEP]
    return close or rows[:2]


def _collect(base):
    """Fetch the home page and the first existing /about, /pricing, /team, /contact."""
    out = {}
    first = [(label, base + paths[0]) for label, paths in PAGES]
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(first)) as pool:
        jobs = {pool.submit(_fetch, url): (label, url) for label, url in first}
        for job in concurrent.futures.as_completed(jobs):
            label, url = jobs[job]
            try:
                html = job.result()
            except Exception:                      # noqa: BLE001
                html = ""
            if html:
                out[label] = (url, html)
    retry = [(label, base + path) for label, paths in PAGES if label not in out
             for path in paths[1:]][:5]
    if retry:
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(retry)) as pool:
            jobs = {pool.submit(_fetch, url): (label, url) for label, url in retry}
            for job in concurrent.futures.as_completed(jobs):
                label, url = jobs[job]
                try:
                    html = job.result()
                except Exception:                  # noqa: BLE001
                    html = ""
                if html and label not in out:
                    out[label] = (url, html)
    return out


# ------------------------------------------------------------------ people
SEP_BEFORE = ",|-|(:;"
SEP_CHARS = ",|-|(:; \u2013\u2014"
GENERIC = {"software", "platform", "tool", "business", "company", "solution", "solutions",
           "service", "services", "app", "product", "products", "best", "free", "online"}


def _clean_name(name, self_name, host_words):
    name = (name or "").strip(" -,:;")
    words = name.split()
    if not (2 <= len(words) <= 3):
        return ""
    for word in words:
        if len(word) < 2 or not word[0].isupper() or word.isupper() or not word[1:].isalpha():
            return ""
        low = word.lower()
        if low in STOP_NAMES or low in host_words:
            return ""
    if self_name and name.lower() in self_name.lower():
        return ""
    return name


def _people(texts, self_name, host_words):
    found = []
    seen = set()
    for label in ("team", "about", "contact", "home"):
        text = texts.get(label, "")
        if not text:
            continue
        low = text.lower()
        for role in PERSON_ROLES:
            start = 0
            while len(found) < MAX_PEOPLE:
                at = low.find(role, start)
                if at < 0:
                    break
                start = at + len(role)
                window = text[max(0, at - 80):at].rstrip()
                if not window:
                    continue
                names = [n for n in NAME_RX.findall(window)
                         if _clean_name(n, self_name, host_words)]
                if not names:
                    continue
                name = _clean_name(names[-1], self_name, host_words)
                if not name or name.lower() in seen:
                    continue
                tail = window.rstrip(SEP_CHARS)
                if tail.endswith(name):
                    before = tail[:len(tail) - len(name)].rstrip()
                    ok = (not before) or before[-1] in ".!>|)]\u00b7" or before[-1] in SEP_BEFORE
                else:
                    ok = (window[-1] in SEP_BEFORE
                          or window.lower().endswith((" is a", " is our", " our", " as")))
                if not ok:
                    continue
                seen.add(name.lower())
                found.append({"name": name, "role": role, "page": label})
    mailboxes = []
    seen_mail = set()
    for label in ("contact", "team", "about", "home"):
        for email in EMAIL_RX.findall(texts.get(label, "")):
            address = email.lower().strip(".")
            if not address or address in seen_mail or address.endswith(IMAGE_EXT):
                continue
            seen_mail.add(address)
            mailboxes.append({"email": address, "page": label})
            if len(mailboxes) >= MAX_PEOPLE:
                break
    return {"people": found, "emails": mailboxes}


# ------------------------------------------------------------------ rivals
def _rivals(texts, self_name, host_words, card):
    names = []
    seen = set()
    own = set(word.lower() for word in (self_name or "").split()) | set(host_words)

    def add(value):
        value = (value or "").strip(" -,:;.()[]")
        low = value.lower()
        if not (2 <= len(value) <= 40) or low in seen or low in STOP_NAMES:
            return
        if low.split()[0] in RIVAL_START:
            return
        if any(word in own for word in (w.lower() for w in value.split())):
            return
        seen.add(low)
        names.append(value)

    for text in texts.values():
        for match in RIVAL_CUE.finditer(text):
            chunk = text[match.end():match.end() + 120]
            for cand in NAME_RX.findall(chunk)[:4]:
                add(cand)
    for name in card.get("competitors", []) or []:
        add(name)
    for name in card.get("solved_by", []) or []:
        add(name)
    return names[:MAX_RIVALS]


# ------------------------------------------------------------------ profile
def _use_cases(card, profile, host_words):
    out, seen = [], set()
    own = set(site_read.fold(word) for word in host_words)

    def add(name, why):
        name = str(name or "").strip()
        low = site_read.fold(name)
        if (len(name) < 4 or low in seen or low in own or low in GENERIC
                or low in site_read.GENERIC or low in site_read.NAV or low in site_read.SK_CZ):
            return
        seen.add(low)
        out.append({"name": name.title(), "why": why})

    for industry in (card.get("industries") or [])[:3]:
        add(industry, "named on the site as a customer segment")
    for keyword in (profile.get("keywords") or []):
        if len(out) >= 3:
            break
        add(keyword, "one of the site's strongest words")
    if not out and profile.get("buyer_role"):
        add(profile["buyer_role"], "the role the page speaks to")
    return out[:4]


PROBLEM_CUES = ("without ", "no more ", "so you ", "so that ", "stop ", "no need ", "instead of ",
                "struggl", "difficult", "hard to", "waste", "lose ", "missed", "protect", "save ")

# Footer and policy lines that are on every shop and say nothing about the business.
FURNITURE = ("all rights reserved", "refund policy", "return policy", "privacy policy",
             "terms and conditions", "terms of use", "cookie", "subscribe", "sign up",
             "follow us", "free shipping on orders", "powered by", "close menu")


def _furniture(text):
    low = site_read.fold(text)
    return any(cue in low for cue in FURNITURE)


def _problem_from(fields, texts):
    """The problem sentence from the page's own description, else ''. Never invented."""
    text = " ".join([str(fields.get("description") or ""), texts.get("home") or "",
                     texts.get("about") or ""])
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        low = sentence.lower()
        if (40 <= len(sentence.strip()) <= 320 and any(cue in low for cue in PROBLEM_CUES)
                and any(word in low for word in radar.PAIN_WORDS)
                and not _furniture(sentence) and radar.is_problem_sentence(sentence)):
            # "And software that is hard to use ..." reads as a fragment on a card.
            if radar.is_site_problem(sentence):
                return radar._problem_text(sentence)
    return ""


def _display_name(name, host):
    name = (name or "").strip()
    brand = ""
    labels = [part for part in (host or "").split(".") if part and part != "www"]
    if labels:
        brand = labels[0]
    if re.match(r"^[a-z0-9][a-z0-9.\-]*\.[a-z]{2,}$", name, re.I):
        name = ""                              # a bare domain is not a business name
    if name and len(name) <= 40 and not re.search(r"(?i)\b(best|top|free|buy|cheap)\b", name):
        return name
    if brand and len(brand) > 1:
        return brand[:1].upper() + brand[1:]
    return name[:60]


def _profile(fields, card, texts, host_words):
    keywords = [k.strip() for k in (fields.get("keywords") or "").split(",")
                if k.strip() and site_read.fold(k) not in host_words]
    profile = {
        "name": fields.get("name") or "",
        "category": card.get("category") or fields.get("category") or "",
        "buyer_role": card.get("buyer_role") or fields.get("buyer_role") or "",
        "price": card.get("price") or fields.get("price") or 0,
        "description": fields.get("description") or "",
        "keywords": keywords,
        "industries": list(card.get("industries") or []),
        "countries": list(card.get("countries") or []),
        "problem": card.get("problem") or "",
        "pages_read": [label for label, text in texts.items() if text],
    }
    if (not profile["problem"] or _furniture(profile["problem"])
            or not radar.is_problem_sentence(profile["problem"])):
        profile["problem"] = _problem_from(fields, texts)
    profile["use_cases"] = _use_cases(card, profile, host_words)
    summary = "%s" % (profile["name"] or "This business")
    if profile["category"]:
        summary += " sells %s" % profile["category"]
    role = str(profile["buyer_role"] or "").strip()
    if role:
        label = role.upper() if len(role) <= 4 else role
        summary += " to the %s" % label
    elif profile["keywords"]:
        summary += " - " + ", ".join(profile["keywords"][:4])
    profile["summary"] = summary + "."
    return profile


# ------------------------------------------------------------------ ideas
def _idea_text(template, card, profile, pains):
    rival = (card.get("competitors") or ["the tool they run now"])[0]
    pain = ((pains.get("signals") or []) or [profile.get("problem") or "the problem"])[0]
    pain = str(pain).strip().rstrip(".!?")
    if pain.lower().startswith(("and ", "but ", "or ")):
        pain = pain.split(" ", 1)[1]
    buyer = profile.get("buyer_role") or "the owner"
    if not buyer.lower().startswith("the "):
        buyer = "the " + buyer
    offer = profile.get("name") or "this offer"
    values = {"rival": rival, "pain": pain, "buyer": buyer, "offer": offer}
    return {key: (value.format(**values) if isinstance(value, str) else value)
            for key, value in template.items()}


def _score(card, weights, idea, pains, profile):
    weights = weights or {}
    w = {k: float(weights.get(k, 1.0) or 1.0) for k in ("fit", "pain", "timing", "pay")}
    text = (idea["title"] + " " + idea["hook"] + " " + idea["segment"]).lower()
    hits = sum(1 for word in (profile.get("keywords") or []) if word.lower() in text)
    fit = min(1.0, 0.5 + 0.1 * hits)
    pain = 0.8 if (pains.get("signals") or profile.get("problem")) else 0.5
    timing = float((pains.get("kinds") or {}).get(idea.get("kind") or "", 0.35))
    price = int(profile.get("price") or 0)
    pay = 0.85 if price >= 200 else 0.75 if price >= 100 else 0.6 if price >= 50 else 0.45
    total = (fit * w["fit"] + pain * w["pain"] + timing * w["timing"] + pay * w["pay"]) / \
        max(0.1, sum(w.values()))
    return max(1, min(99, int(round(total * 100))))


def _brief(idea, profile):
    offer = profile.get("name") or "this"
    subject = idea["title"] if len(idea["title"]) <= 60 else idea["title"][:57] + "..."
    body = ("Hi {{first_name|there}},\n\n"
            + idea["hook"] + "\n\n"
            + "If it is worth ten minutes, here is the link: {{offer_link}}\n\n"
            + "Best,\n{{sender_name}}")
    return {"subject": subject, "body": body,
            "send_to": idea["segment"], "angle": idea["angle"]}


def _ideas(card, weights, profile, pains):
    out = []
    for template in IDEAS:
        idea = _idea_text(template, card, profile, pains)
        idea["score"] = _score(card, weights, idea, pains, profile)
        idea["brief"] = _brief(idea, profile)
        out.append(idea)
    out.sort(key=lambda row: -row["score"])
    return out


# ------------------------------------------------------------------ writing back
def _signals(profile):
    """Every finding the daily jobs wrote, narrowed to words this business is not."""
    summary = daily.signals_summary()
    own = set(site_read.fold(word) for word in (profile.get("name") or "").split())
    own |= set(site_read.fold(word) for word in (profile.get("keywords") or []))
    summary["words"] = [word for word in (summary.get("words") or [])
                        if site_read.fold(word) not in own][:10]
    return summary


def _merge_card(card, profile, team, rivals, pains=None, source="", signals=None):
    merged = dict(card or {})
    for key in ("category", "buyer_role", "problem"):
        if not merged.get(key) and profile.get(key):
            merged[key] = profile[key]
    if not merged.get("price") and profile.get("price"):
        merged["price"] = profile["price"]
    for key in ("keywords", "industries", "countries"):
        current = list(merged.get(key) or [])
        lower = [str(v).lower() for v in current]
        for value in profile.get(key) or []:
            if str(value).lower() not in lower:
                current.append(value)
                lower.append(str(value).lower())
        merged[key] = current
    if rivals and not merged.get("competitors"):
        merged["competitors"] = rivals[:6]
    pains = pains or {}
    if not merged.get("pain_signals") and pains.get("signals"):
        merged["pain_signals"] = list(pains["signals"])[:8]
    own = str(profile.get("name") or "").strip().lower()
    solves = [name for name in (pains.get("solves") or []) if str(name).strip().lower() != own]
    if not merged.get("solved_by") and solves:
        merged["solved_by"] = solves[:8]
    merged["team"] = team.get("people", [])
    merged["rivals"] = rivals
    merged["targeting"] = {
        "buyer_role": profile.get("buyer_role") or "",
        "use_cases": [item.get("name") for item in (profile.get("use_cases") or [])
                      if isinstance(item, dict) and item.get("name")][:6],
        "needs": list((pains or {}).get("needs") or [])[:8],
        "kinds": dict((pains or {}).get("kinds") or {}),
        "markets": list(profile.get("countries") or [])[:6],
        "source": source or "",
        "read_at": int(time.time()),
    }
    signals = signals or {}
    if signals.get("total"):
        merged["targeting"]["signals"] = {
            "total": int(signals.get("total") or 0),
            "kinds": dict(signals.get("kinds") or {}),
            "words": list(signals.get("words") or [])[:10],
            "flags": list(signals.get("flags") or [])[:8],
            "markets": list(signals.get("markets") or [])[:6],
            "read_at": int(signals.get("read_at") or 0),
        }
    return merged


def _write_offer(offer_id, profile, url):
    offer = offers.get(offer_id)
    if not offer:
        return False
    patch = {"id": offer_id}
    if not (offer.get("name") or "").strip() and profile.get("name"):
        patch["name"] = profile["name"]
    if not (offer.get("description") or "").strip() and profile.get("description"):
        patch["description"] = profile["description"]
    if not (offer.get("category") or "").strip() and profile.get("category"):
        patch["category"] = profile["category"]
    if not (offer.get("keywords") or "").strip() and profile.get("keywords"):
        patch["keywords"] = ", ".join(profile["keywords"])
    if not (offer.get("affiliate_url") or "").strip() and url:
        patch["affiliate_url"] = url
    if len(patch) > 1:
        offers.save(patch)
    return True


# ------------------------------------------------------------------ the run
def build(url, offer_id=0):
    started = time.time()
    base, host = _base(url)
    if not base:
        return {"ok": False, "error": "That does not look like a web address."}
    steps = []
    pages = _collect(base)
    texts = {label: _text(html) for label, (page_url, html) in pages.items()}
    home = pages.get("home")
    if not home or len(texts.get("home") or "") < 120:
        return {"ok": False, "error": "Could not read %s - the page came back empty." % host}
    steps.append({"key": "fetch", "label": "fetching %s" % host, "ok": True,
                  "detail": "%d page(s) read" % len(pages)})
    sides = [label for label in ("pricing", "about") if texts.get(label)]
    steps.append({"key": "pages", "label": "reading /pricing and /about", "ok": bool(sides),
                  "detail": ", ".join("/" + s for s in sides) or "not published - used the home page"})

    html = home[1]
    fields = site_read.read_html(html, host)
    host_words = set(part for part in re.split(r"[.\-]", host) if len(part) > 2)
    fields["name"] = _display_name(fields.get("name"), host)
    combined = " ".join(texts.get(label, "") for label in ("home", "about", "pricing", "team"))
    card = radar.card_from_offer(offers.get(offer_id) or {}, radar.parse_offer(combined, strict=True))
    profile = _profile(fields, card, texts, host_words)
    steps.append({"key": "extract", "label": "extracting what you sell and to whom", "ok": True,
                  "detail": "%s, %s" % (profile["category"] or "no category read",
                                        profile["buyer_role"] or "no buyer read")})

    steps.append({"key": "keywords", "label": "listing product keywords", "ok": bool(profile["keywords"]),
                  "detail": ", ".join(profile["keywords"][:6]) or "no keyword on the site"})
    candidates, alt_note = _search_candidates(profile["name"], host)
    steps.append({"key": "alternatives", "label": 'searching "alternatives to %s"' % (profile["name"] or host),
                  "ok": bool(candidates), "detail": alt_note})
    kept = _check_candidates(candidates, _terms(profile))
    steps.append({"key": "candidates", "label": ("checking %d candidate(s)" % len(candidates)) if candidates
                  else "checking the candidates", "ok": True,
                  "detail": "%d read and scored" % len(kept) if kept else "nothing to check"})
    steps.append({"key": "closest", "label": ("keeping the %d closest" % len(kept)) if kept
                  else "keeping the closest ones", "ok": bool(kept),
                  "detail": ", ".join(row["domain"] for row in kept) or "no alternative found"})

    team = _people(texts, profile["name"], host_words)
    steps.append({"key": "team", "label": "finding the team named on the site", "ok": bool(team["people"]),
                  "detail": "%d person(s), %d mailbox(es)" % (len(team["people"]), len(team["emails"]))})

    site_rivals = _rivals(texts, profile["name"], host_words, card)
    rivals = []
    for name in [row["domain"] for row in kept] + list(site_rivals):
        if name and name.lower() not in [r.lower() for r in rivals]:
            rivals.append(name)
    rivals = rivals[:MAX_RIVALS]
    if rivals:
        merged_competitors = list(card.get("competitors") or [])
        for name in rivals:
            if name not in merged_competitors:
                merged_competitors.append(name)
        card["competitors"] = merged_competitors[:6]
    steps.append({"key": "profile", "label": "building the company profile - clustering use cases",
                  "ok": True, "detail": "%d use case(s), %d name(s) on the site"
                  % (len(profile["use_cases"]), len(team["people"]))})

    signals = _signals(profile)
    card["targeting"] = {"signals": {
        "total": int(signals.get("total") or 0),
        "kinds": dict(signals.get("kinds") or {}),
        "words": list(signals.get("words") or [])[:10],
        "flags": list(signals.get("flags") or [])[:8],
        "markets": list(signals.get("markets") or [])[:6],
        "read_at": int(signals.get("read_at") or 0),
    }}
    top_kinds = radar.lead_kinds(signals.get("kinds"))
    steps.append({"key": "signals", "label": "reading the job results",
                  "ok": bool(signals.get("total")),
                  "detail": ("%d finding(s) - %s" % (signals["total"],
                                                      ", ".join("%s %d" % item for item in top_kinds)))
                            if signals.get("total") else "no job results yet - the daily jobs fill these"})

    pains = radar.pain_profile(card)
    steps.append({"key": "pains", "label": "matching pains to buyers", "ok": True,
                  "detail": ", ".join((pains.get("signals") or [])[:4]) or "no pain words on the site"})

    offer_row, current_card, current_weights = hunt.card_for(int(offer_id or 0))
    weights = current_weights or {}
    ideas = _ideas(card, weights, profile, pains)
    steps.append({"key": "score", "label": "scoring %d campaign ideas" % len(ideas), "ok": True,
                  "detail": "best: %s (%d)" % (ideas[0]["title"], ideas[0]["score"]) if ideas else ""})
    steps.append({"key": "briefs", "label": "writing campaign briefs", "ok": True,
                  "detail": "%d brief(s)" % len(ideas)})

    payload = {"ok": True, "url": base, "host": host, "seconds": round(time.time() - started, 1),
               "steps": steps,
               "pages": [{"label": label, "url": pages[label][0], "chars": len(texts.get(label, ""))}
                         for label in ("home", "about", "pricing", "team", "contact") if label in pages],
               "profile": profile, "team": team, "rivals": rivals, "pains": pains,
               "signals": signals,
               "alternatives": kept, "ideas": ideas, "offer_id": int(offer_id or 0)}

    if offer_id and offer_row:
        merged = _merge_card(current_card or card, profile, team, rivals, pains, source=host,
                             signals=signals)
        merged["brief"] = payload
        hunt.save_card(int(offer_id), merged, weights or None)
        _write_offer(int(offer_id), profile, base)
        payload["saved"] = True
    else:
        payload["saved"] = False
    return payload
