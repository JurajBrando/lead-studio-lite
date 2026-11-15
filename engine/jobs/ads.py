"""Google Ads Transparency: weekly advertisers.

Pulls the newest weekly advertiser aggregation from the HuggingFace mirror
`najeh-halawani/google-ads-transparency` (one gzip CSV per week part, split in
`part1` / `part2`), keeps the advertisers whose disclosed or legal name looks
like a keyword we sell to, and writes one `ads` signal per advertiser.

Everything stays idempotent: the newest week is picked from the dataset file
listing, advertisers are remembered by `advertiser_id`, and ids already stored
in `signals` are skipped before any database write happens.

Standard library only; every HTTP call goes through `engine.net`.
"""
import json
import re

from .. import db, net
from .base import Job

DATASET_ID = "najeh-halawani/google-ads-transparency"
DATASET_URL = "https://huggingface.co/datasets/" + DATASET_ID
DATASET_API = "https://huggingface.co/api/datasets/" + DATASET_ID
RESOLVE_BASE = DATASET_URL + "/resolve/main/"

SOURCE = "google-ads-transparency"

# One weekly part is a large gzip CSV; allow a slow link enough time.
DOWNLOAD_TIMEOUT = 300

DEFAULT_KEYWORDS = ["marketing", "agency", "shopify", "clinic", "dental", "solar",
                    "roofing", "real estate", "fitness", "ecommerce"]

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}

# data/weekly/Google_Ads_Transparency_Report_November_Week_3_Aggregation_part1.csv.gz
WEEKLY_RX = re.compile(
    r"Google_Ads_Transparency_Report_([A-Za-z]+)_Week_(\d+)_Aggregation_part(\d+)\.csv\.gz$",
    re.IGNORECASE)

ADVERTISER_KEYS = ("advertiser_id", "name", "location", "regions", "surfaces",
                   "impressions", "first_ad_shown", "last_ad_shown", "page_url",
                   "funded")


# ------------------------------------------------------------------ helpers
def _clean(value):
    return str(value or "").strip()


def _strip_bom(value):
    """Drop a UTF-8 BOM: otherwise the first header name would never match."""
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    return (value or "").lstrip("\ufeff")


def _to_int(value):
    """Lenient integer: '1,234', '1234.0', 'n/a' and blank all yield a number."""
    text = _clean(value).replace(",", "").replace("_", "").replace(" ", "")
    if not text:
        return 0
    try:
        return int(float(text))
    except (TypeError, ValueError, OverflowError):
        return 0


def _to_bool(value):
    return _clean(value).lower() in ("1", "true", "t", "yes", "y")


def _norm(value):
    return re.sub(r"[^a-z0-9]+", " ", _clean(value).lower()).strip()


def weekly_filenames(info):
    """Pull `siblings[].rfilename` out of the dataset info JSON."""
    if isinstance(info, dict):
        siblings = info.get("siblings") or []
    elif isinstance(info, list):
        siblings = info
    else:
        siblings = []
    names = []
    for entry in siblings:
        if isinstance(entry, dict):
            name = entry.get("rfilename")
        else:
            name = entry
        name = _clean(name)
        if name:
            names.append(name)
    return names


def select_latest_week(filenames):
    """Newest weekly aggregation from a dataset file listing.

    The mirrored filenames carry no year (the mirror only keeps the current
    rolling window), so weeklies are ordered by (month, week). Missing parts
    are tolerated: the newest week is used with whatever parts exist.

    Returns {"month", "week", "label", "parts": {part_no: rfilename}} or None.
    """
    best_key = None
    best = None
    for name in filenames or []:
        text = _clean(name)
        match = WEEKLY_RX.search(text)
        if not match:
            continue
        month = match.group(1)
        month_no = MONTHS.get(month.lower())
        if not month_no:
            continue
        week = int(match.group(2))
        part = int(match.group(3))
        key = (month_no, week)
        if best_key is None or key > best_key:
            best_key = key
            label = "%s_Week_%d" % (month.capitalize(), week)
            best = {"month": month.capitalize(), "week": week, "label": label,
                    "parts": {}}
        if key == best_key:
            best["parts"][part] = text
    if not best:
        return None
    return {"month": best["month"], "week": best["week"], "label": best["label"],
            "parts": dict(sorted(best["parts"].items()))}


def resolve_url(rfilename):
    """HuggingFace `resolve/main` download URL for one repository file."""
    path = "/".join(net.quote(part) for part in _clean(rfilename).split("/") if part)
    return RESOLVE_BASE + path


# ------------------------------------------------------------------ parsing
def parse_advertiser_records(csv_text):
    """Full parser: one dict per CSV row, including the legal name.

    Kept separate from `parse_advertisers` so `run` can match keywords against
    both the disclosed and the legal name without widening the public shape.
    """
    _headers, rows = net.csv_rows(_strip_bom(csv_text))
    out = []
    for row in rows:
        disclosed = _clean(row.get("advertiser_disclosed_name"))
        legal = _clean(row.get("advertiser_legal_name"))
        name = disclosed or legal
        advertiser_id = _clean(row.get("advertiser_id"))
        if not name and not advertiser_id:
            continue
        funded = (_to_bool(row.get("is_funded_by_google_ad_grants"))
                  or _to_bool(row.get("is_third_party_funded")))
        out.append({
            "advertiser_id": advertiser_id,
            "name": name,
            "legal_name": legal,
            "location": _clean(row.get("advertiser_location")),
            "regions": _clean(row.get("distinct_regions")),
            "surfaces": _clean(row.get("distinct_surfaces")),
            "impressions": _to_int(row.get("total_impressions_midpoint")),
            "first_ad_shown": _clean(row.get("first_ad_shown")),
            "last_ad_shown": _clean(row.get("last_ad_shown")),
            "page_url": _clean(row.get("sample_creative_page_url")),
            "funded": funded,
        })
    return out


def parse_advertisers(csv_text):
    """Pure parser: the public adapter shape used by the rest of the app."""
    return [{key: record[key] for key in ADVERTISER_KEYS}
            for record in parse_advertiser_records(csv_text)]


# ------------------------------------------------------------------ filters
def matches_keywords(values, keywords):
    """Case-insensitive substring match of any keyword in any value.

    An empty keyword list means "no keyword filter" and keeps every row.
    """
    wanted = [_clean(word).lower() for word in (keywords or []) if _clean(word)]
    if not wanted:
        return True
    haystack = " ".join(_clean(value).lower() for value in (values or []) if _clean(value))
    if not haystack:
        return False
    return any(word in haystack for word in wanted)


def matches_regions(regions_value, wanted):
    """True when any wanted country code/name appears as a token in regions."""
    tokens = [_norm(item) for item in (wanted or []) if _clean(item)]
    tokens = [token for token in tokens if token]
    if not tokens:
        return True
    haystack = " %s " % _norm(regions_value)
    if not haystack.strip():
        return False
    return any((" %s " % token) in haystack for token in tokens)


def _already_seen_advertisers(week_label=None):
    """Advertiser ids already stored as `ads` signals (for one week when given).

    Scoping to the week keeps a re-run of the same week a no-op while a newer
    week can still produce fresh signals (the UNIQUE index still prevents true
    duplicates).
    """
    seen = set()
    try:
        stored = db.rows("SELECT payload FROM signals WHERE kind='ads'")
    except Exception:
        return seen
    for row in stored:
        try:
            payload = json.loads(row.get("payload") or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        if week_label is not None and _clean(payload.get("week")) != _clean(week_label):
            continue
        advertiser_id = _clean(payload.get("advertiser_id"))
        if advertiser_id:
            seen.add(advertiser_id)
    return seen


# ------------------------------------------------------------------ job body
def _run(ctx):
    info = net.json_body(DATASET_API)
    latest = select_latest_week(weekly_filenames(info))
    if not latest:
        raise ValueError("no weekly aggregation files found in the dataset listing")
    ctx.log("newest weekly aggregation: %s (%d part(s))"
            % (latest["label"], len(latest["parts"])))

    keywords = ctx.list_param("keywords", DEFAULT_KEYWORDS)
    regions = ctx.list_param("regions", [])
    min_impressions = ctx.int_param("min_impressions", 10000)
    limit = ctx.int_param("limit", 500)

    seen = _already_seen_advertisers(latest["label"])
    parsed_total = 0
    kept = 0
    skipped_existing = 0
    part_failures = []

    for part_no in sorted(latest["parts"]):
        if limit > 0 and kept >= limit:
            break
        rfilename = latest["parts"][part_no]
        ctx.log("downloading %s" % rfilename)
        try:
            # A weekly part is ~130 MB of gzip: the default 45 s socket timeout
            # is too tight for a slow line, so give the download room.
            csv_text = net.text(resolve_url(rfilename), timeout=DOWNLOAD_TIMEOUT)
        except net.NetError as exc:
            # A missing part is survivable: parse the parts that did arrive.
            part_failures.append("%s: %s" % (rfilename, exc))
            ctx.warn("download failed for %s: %s" % (rfilename, exc))
            continue
        records = parse_advertiser_records(csv_text)
        del csv_text
        parsed_total += len(records)
        ctx.log("%s: %d advertiser rows" % (rfilename, len(records)))
        for record in records:
            if limit > 0 and kept >= limit:
                break
            advertiser_id = record["advertiser_id"]
            if not advertiser_id:
                continue
            if advertiser_id in seen:
                skipped_existing += 1
                continue
            if not matches_keywords((record["name"], record["legal_name"]), keywords):
                continue
            if not matches_regions(record["regions"], regions):
                continue
            if min_impressions > 0 and record["impressions"] < min_impressions:
                continue
            seen.add(advertiser_id)
            kept += 1
            ctx.add_signal(
                "ads",
                company=record["name"],
                source=SOURCE,
                source_url=record["page_url"] or DATASET_URL,
                title=record["surfaces"],
                payload={
                    "advertiser_id": advertiser_id,
                    "name": record["name"],
                    "legal_name": record["legal_name"],
                    "location": record["location"],
                    "regions": record["regions"],
                    "surfaces": record["surfaces"],
                    "impressions": record["impressions"],
                    "first_ad_shown": record["first_ad_shown"],
                    "last_ad_shown": record["last_ad_shown"],
                    "funded": record["funded"],
                    "week": latest["label"],
                })

    if parsed_total == 0 and part_failures:
        raise net.NetError("no weekly part could be downloaded: "
                           + "; ".join(part_failures))
    if kept == 0 and skipped_existing == 0:
        ctx.warn("no advertiser matched the current keywords/regions/impressions filter")
    return ctx.summary(week=latest["label"], advertisers=parsed_total, kept=kept)


def run(ctx):
    """Job entry point: never raises, always returns a summary dict."""
    try:
        return _run(ctx)
    except net.NetError as exc:
        ctx.warn("ads transparency download failed: %s" % exc)
        return ctx.summary(ok=False, error=str(exc))
    except Exception as exc:  # noqa: BLE001 - a job must never break the scheduler
        ctx.warn("ads transparency job failed: %s" % exc)
        return ctx.summary(ok=False, error=str(exc))


JOBS = [Job(
    key="ads_transparency",
    name="Google Ads Transparency: weekly advertisers",
    group="Signals",
    description="Newest weekly EU advertiser aggregation from the HuggingFace mirror; matched against your keywords.",
    run=run,
    schedule="weekly mon 07:30",
    params={"keywords": DEFAULT_KEYWORDS, "regions": [], "min_impressions": 10000, "limit": 500},
)]
