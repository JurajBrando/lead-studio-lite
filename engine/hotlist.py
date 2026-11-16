"""Today's hot list - the leads whose site changed in a way worth a first email.

Built from this app's own data: a signal says what changed, the lead says who to write to, the
offers say what to offer, and a per-kind template turns the change into the first question. The
result is `data/hot/hot_<date>.csv` with the same columns as the hot list the user already reads:

    heat, offer, signal, reason, first email question, email, first name, title,
    email confidence, company, domain, evidence
"""
import csv
import json
import os

from . import config, db

COLUMNS = ["heat", "offer", "signal", "reason", "first email question", "email", "first name",
           "title", "email confidence", "company", "domain", "evidence"]

# Signal -> (heat, the reason said plainly, the first question). {name}, {company}, {domain},
# {tool} and {place} are filled from the signal and the lead.
KINDS = {
    "dropped_attribution_tool": (
        "HOT", "{tool} is no longer on the site (since {when})",
        "Hi {first}, I noticed {tool} is no longer on {domain}. Are you currently looking for "
        "something to replace it for ad tracking, or have you moved that in-house?"),
    "dropped_booking_software": (
        "HOT", "{tool} is no longer on the site (since {when})",
        "Hi {first}, I noticed {tool} is no longer on {domain}. Are you currently changing how "
        "patients book and get followed up, or is a new system already in place?"),
    "new_lookalike_domain": (
        "HOT", "{tool} looks like your domain and was registered recently",
        "Hi {first}, A domain that looks like yours was registered recently: {tool}. Did you "
        "already know about it, and is someone on your side handling lookalike sites?"),
    "added_ops_tooling": (
        "WARM", "{tool} was just installed (since the earlier crawl)",
        "Hi {first}, I noticed you recently rolled out {tool}. Does your team keep its processes "
        "and knowledge base documented in one place, or is it spread across tools?"),
    "added_ad_channel": (
        "WARM", "{tool} tracking appeared on the site (since {when})",
        "Hi {first}, I saw {domain} recently started running {tool} ads. Quick question: are the "
        "sales the ad platform reports matching what actually lands in your store, or is there a gap?"),
    "agency_started_own_ads": (
        "WARM", "{tool} tracking appeared on the site (since the earlier crawl)",
        "Hi {first}, I saw your agency recently started running its own {tool} ads. Are you set up "
        "to nurture those leads automatically, or is it manual for now?"),
    "started_ads": (
        "WARM", "{tool} tracking appeared on the site (since {when})",
        "Hi {first}, I saw {domain} recently started advertising on {tool}. Are most of those leads "
        "turning into booked consultations, or is follow-up the bottleneck?"),
    "now_hiring": (
        "WARM", "the site now says they are hiring (since the earlier crawl)",
        "Hi {first}, I saw you are hiring. Is the team growing because of new client work, and are "
        "your internal processes documented for the new people?"),
    "hiring": (
        "WARM", "a new opening was posted (since the earlier crawl)",
        "Hi {first}, I saw {company} is hiring. Is the team growing because of new client work, and "
        "are your internal processes documented for the new people?"),
}
GENERIC = ("WARM", "{tool} appeared on the site (since {when})",
           "Hi {first}, I noticed {domain} changed recently - {tool}. Is that change part of a "
           "project you are working through at the moment?")
# Which signal a kind of signal is won with, by matching the offer keywords.
OFFER_HINTS = {
    "dropped_attribution_tool": ("hyros", "tracking", "attribution"),
    "started_ads": ("hyros", "tracking", "paid ads"),
    "added_ad_channel": ("hyros", "tracking", "paid ads"),
    "agency_started_own_ads": ("highlevel", "agency", "automation"),
    "now_hiring": ("highlevel", "agency", "automation"),
    "hiring": ("highlevel", "agency", "automation"),
    "dropped_booking_software": ("dermis", "booking", "dental", "patient"),
    "new_lookalike_domain": ("redpoints", "brand", "protection"),
    "added_ops_tooling": ("highlevel", "automation", "ops"),
}
GENERIC_MAILBOXES = ("info@", "hello@", "contact@", "sales@", "office@", "support@",
                     "service@", "admin@", "team@", "hi@")


def row_date(ts):
    try:
        from datetime import datetime
        return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return ""


def _payload(signal):
    try:
        return json.loads(signal.get("payload") or "{}")
    except (TypeError, ValueError):
        return {}


def _tool_of(signal):
    payload = _payload(signal)
    for key in ("tool", "technology", "tech", "brand", "keyword", "lookalike"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    title = str(signal.get("title") or "")
    for marker in (" is no longer on", " was just installed", " appeared on", " looks like"):
        if marker in title:
            return title.split(marker)[0].strip()
    return title[:60]


def _lead_for(signal):
    if signal.get("lead_id") and str(signal["lead_id"]) not in ("", "0"):
        lead = db.one("SELECT * FROM leads WHERE id=?", (signal["lead_id"],))
        if lead:
            return lead
    domain = str(signal.get("domain") or "").strip().lower()
    if domain:
        lead = db.one("SELECT * FROM leads WHERE lower(domain)=? LIMIT 1", (domain,))
        if lead:
            return lead
    company = str(signal.get("company") or "").strip()
    if company:
        return db.one("SELECT * FROM leads WHERE company = ? COLLATE NOCASE LIMIT 1", (company,))
    return None


def _offer_for(signal, lead):
    hints = OFFER_HINTS.get(str(signal.get("kind") or ""), ())
    text = " ".join([str(signal.get("kind") or ""), str(signal.get("title") or ""),
                     str(_tool_of(signal)), (lead or {}).get("company") or ""]).lower()
    best, best_score = "", 0
    for offer in db.rows("SELECT name, keywords FROM offers WHERE active=1"):
        keywords = [word.strip().lower() for word in (offer["keywords"] or "").split(",")
                    if word.strip()]
        score = sum(1 for word in keywords if word and word in text)
        score += 3 * sum(1 for word in hints if word in (offer["name"] or "").lower())
        if score > best_score:
            best, best_score = offer["name"].split(" - ")[0], score
    return best


def _confidence(lead):
    if not lead:
        return "no_email"
    if lead.get("first_name"):
        return "your_own_data"
    email = (lead.get("email") or "").lower()
    if not email:
        return "no_email"
    if email.startswith(GENERIC_MAILBOXES):
        return "general_inbox"
    if str(lead.get("source") or "") in ("contact-finder", "crawl", "commoncrawl"):
        return "email_printed_on_site"
    return "guess_verify_first"


def build(days=2, limit=500, write_csv=True):
    """The rows for today, hottest first, and the CSV on disk."""
    since = db.now() - max(1, int(days)) * 86400
    signals = db.rows("SELECT * FROM signals WHERE ts >= ? ORDER BY ts DESC LIMIT 5000",
                      (since,))
    rows, seen = [], set()
    skipped = 0
    for signal in signals:
        kind = str(signal.get("kind") or "")
        if kind not in KINDS:
            skipped += 1                      # mx_missing and the like are not a buying signal
            continue
        lead = _lead_for(signal)
        key = ((lead or {}).get("email") or "") + "|" + str(signal.get("kind")) + "|" + \
              str(signal.get("domain") or signal.get("company"))
        if key in seen:
            continue
        seen.add(key)
        kind = str(signal.get("kind") or "")
        heat, reason, question = KINDS.get(kind, GENERIC)
        tool = _tool_of(signal)
        when = row_date(signal.get("ts")) or "the earlier crawl"
        lead = lead or {}
        first = (lead.get("first_name") or "").strip()
        domain = (lead.get("domain") or signal.get("domain") or "").strip()
        company = (lead.get("company") or signal.get("company") or "").strip()
        values = {"first": first or "there", "company": company or "you", "domain": domain or "the site",
                  "tool": tool, "when": when}
        rows.append({
            "heat": heat,
            "offer": _offer_for(signal, lead),
            "signal": kind,
            "reason": reason.format(**values),
            "first email question": question.format(**values),
            "email": (lead.get("email") or "").strip().lower(),
            "first name": first,
            "title": (lead.get("title") or "").strip(),
            "email confidence": _confidence(lead),
            "company": company,
            "domain": domain,
            "evidence": tool,
        })
    order = {"HOT": 0, "WARM": 1}
    rank = {"your_own_data": 0, "email_printed_on_site": 1, "general_inbox": 2,
            "guess_verify_first": 3, "no_email": 9}
    rows.sort(key=lambda row: (order.get(row["heat"], 5),
                               rank.get(row["email confidence"], 5),
                               0 if row["email"] else 1, row["company"]))
    rows = rows[:max(1, int(limit or 500))]
    path = ""
    if write_csv:
        path = write(rows)
    counts = {"total": len(rows),
              "HOT": sum(1 for row in rows if row["heat"] == "HOT"),
              "WARM": sum(1 for row in rows if row["heat"] == "WARM"),
              "with_email": sum(1 for row in rows if row["email"]),
              "skipped": skipped}
    return {"ok": True, "date": db.now(), "rows": rows, "path": path, "counts": counts}


def write(rows, date=""):
    folder = os.path.join(config.DATA_DIR, "hot")
    os.makedirs(folder, exist_ok=True)
    day = date or row_date(db.now())
    path = os.path.join(folder, "hot_%s.csv" % day)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return path
