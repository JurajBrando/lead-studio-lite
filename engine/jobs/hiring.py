"""Hiring signals: daily job postings.

Download the newest daily snapshot of the HuggingFace dataset
`cleanfeeds/tech-jobs-daily` (one CSV per UTC day, e.g. `data/jobs-2026-11-15.csv`),
group the postings by company and turn every opening whose title matches our
keyword list into a `hiring` signal.

The snapshot for "today" is published during the day; when it is not there yet
the job walks back up to `MAX_LOOKBACK_DAYS` days and logs which one worked.

Standard library only; every HTTP call goes through `engine.net`.
"""
import datetime

from .. import net
from .base import Job

DATASET_URL = "https://huggingface.co/datasets/cleanfeeds/tech-jobs-daily"
SNAPSHOT_URL = DATASET_URL + "/resolve/main/data/jobs-%s.csv"

SOURCE = "hf-tech-jobs"
MAX_LOOKBACK_DAYS = 3

DEFAULT_TITLES = ["marketing", "growth", "performance", "demand gen", "paid media",
                  "sales development", "sdr", "revops", "revenue operations", "data",
                  "ai", "automation"]

DEFAULT_IGNORE_COMPANIES = ["recruit", "staffing", "talent"]


# ------------------------------------------------------------------ helpers
def _clean(value):
    return str(value or "").strip()


def _strip_bom(value):
    """Drop a UTF-8 BOM: otherwise the first header name would never match."""
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    return (value or "").lstrip("\ufeff")


def candidate_dates(today=None):
    """UTC dates to try, newest first (today, yesterday, the day before)."""
    if today is None:
        today = datetime.datetime.now(datetime.timezone.utc).date()
    return [today - datetime.timedelta(days=offset)
            for offset in range(MAX_LOOKBACK_DAYS)]


def snapshot_url(day):
    """Download URL for one daily snapshot; `day` is a date or 'YYYY-MM-DD'."""
    label = day.isoformat() if hasattr(day, "isoformat") else _clean(day)
    return SNAPSHOT_URL % label


def matches_titles(title, wanted):
    """Case-insensitive substring match of any wanted phrase in the job title.

    An empty phrase list means "no title filter" and keeps every posting.
    """
    phrases = [_clean(item).lower() for item in (wanted or []) if _clean(item)]
    if not phrases:
        return True
    haystack = _clean(title).lower()
    if not haystack:
        return False
    return any(phrase in haystack for phrase in phrases)


def company_ignored(company, ignore):
    """True when the employer name looks like a staffing agency / recruiter."""
    terms = [_clean(item).lower() for item in (ignore or []) if _clean(item)]
    if not terms:
        return False
    haystack = _clean(company).lower()
    return any(term in haystack for term in terms)


# ------------------------------------------------------------------ parsing
def parse_postings(csv_text):
    """Pure parser: one dict per posting, employer-only rows dropped."""
    _headers, rows = net.csv_rows(_strip_bom(csv_text))
    out = []
    for row in rows:
        company = _clean(row.get("company"))
        title = _clean(row.get("title"))
        if not company or not title:
            continue
        out.append({
            "source": _clean(row.get("source")),
            "company": company,
            "title": title,
            "location": _clean(row.get("location")),
            "department": _clean(row.get("department")),
            "url": _clean(row.get("url")),
            "posted_at": _clean(row.get("posted_at")),
            "id": _clean(row.get("id")),
        })
    return out


# ------------------------------------------------------------------ job body
def _run(ctx):
    postings = None
    used = ""
    failures = []
    for day in candidate_dates():
        label = day.isoformat()
        try:
            csv_text = net.text(snapshot_url(label))
        except net.NetError as exc:
            failures.append("%s (%s)" % (label, exc))
            ctx.log("no job snapshot for %s (%s); trying the day before" % (label, exc))
            continue
        postings = parse_postings(csv_text)
        used = label
        ctx.log("using job snapshot %s: %d postings" % (used, len(postings)))
        if not postings:
            ctx.warn("job snapshot %s is empty" % used)
        break
    if postings is None:
        raise net.NetError("no daily job snapshot available: " + "; ".join(failures))

    titles = ctx.list_param("titles", DEFAULT_TITLES)
    ignore = ctx.list_param("ignore_companies", DEFAULT_IGNORE_COMPANIES)
    limit = ctx.int_param("limit", 400)

    counts = {}
    for posting in postings:
        key = posting["company"].lower()
        counts[key] = counts.get(key, 0) + 1

    seen = set()
    matched = 0
    for posting in postings:
        if limit > 0 and matched >= limit:
            break
        company = posting["company"]
        if company_ignored(company, ignore):
            continue
        if not matches_titles(posting["title"], titles):
            continue
        key = (company.lower(), posting["title"].lower())
        if key in seen:
            continue
        seen.add(key)
        matched += 1
        ctx.add_signal(
            "hiring",
            company=company,
            title=posting["title"],
            source=SOURCE,
            source_url=posting["url"],
            payload={
                "location": posting["location"],
                "department": posting["department"],
                "posted_at": posting["posted_at"],
                "postings": counts.get(company.lower(), 0),
                "source": posting["source"],
            })

    if matched == 0:
        ctx.warn("no posting matched the current titles filter")
    return ctx.summary(date=used, postings=len(postings), matched=matched)


def run(ctx):
    """Job entry point: never raises, always returns a summary dict."""
    try:
        return _run(ctx)
    except net.NetError as exc:
        ctx.warn("hiring signals download failed: %s" % exc)
        return ctx.summary(ok=False, error=str(exc))
    except Exception as exc:  # noqa: BLE001 - a job must never break the scheduler
        ctx.warn("hiring signals job failed: %s" % exc)
        return ctx.summary(ok=False, error=str(exc))


JOBS = [Job(
    key="hiring_signals",
    name="Hiring signals: daily job postings",
    group="Signals",
    description="Newest daily job-postings snapshot from the HuggingFace mirror; matched against your job titles.",
    run=run,
    schedule="daily 06:45",
    params={"titles": DEFAULT_TITLES,
            "ignore_companies": DEFAULT_IGNORE_COMPANIES,
            "limit": 400},
)]
