"""Common Crawl: domain footprint enrichment.

Source: the public Common Crawl index at https://index.commoncrawl.org/.
`collinfo.json` names the available crawls (the first element is the newest) and
each crawl answers a per-domain query with *newline-delimited JSON* - one record
per captured page - not a JSON array. A domain the crawler never saw answers
HTTP 404 with a plain-text body, or HTTP 200 with an empty body.

The job only enriches the `domains` table: was the site crawled, how many pages
does the index hold, when did the last capture happen. A domain registered last
week with zero pages and a domain with three hundred pages are very different
prospects, and this is the cheapest way to tell them apart.

It never creates leads: a footprint is context, not a lead.

The run summary reports `checked` (queries made) and `hits` (domains the newest
index actually has captures for). A signal is written only when a footprint is
new or its newest capture moved, so a quiet daily run stays quiet.
"""
import json
import time

from .. import db, net
from .base import Job, Context

COLLINFO_URL = "https://index.commoncrawl.org/collinfo.json"
INDEX_URL = "https://index.commoncrawl.org/%s-index?url=%s/*&output=json&limit=%d"
INDEX_PAGE = "https://index.commoncrawl.org/"     # recorded as a signal's source_url
RECORD_LIMIT = 100                                # captures read per domain query
NO_CAPTURES = "no captures found"
MAX_DELAY = 10.0                                  # never let a bad param stall a run


def _json_records(body):
    """Decode newline-delimited JSON; tolerate a JSON array in case of a change."""
    stripped = body.strip()
    if stripped.startswith("["):
        try:
            data = json.loads(stripped)
        except ValueError:
            data = None
        if isinstance(data, list):
            return data
    records = []
    for line in stripped.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue                        # malformed line: skip, never raise
        if isinstance(record, dict):
            records.append(record)
    return records


def _stamp_key(stamp):
    """Crawl timestamps are 'YYYYMMDDHHMMSS' strings, so compare them as ints."""
    try:
        return int(str(stamp or "").strip())
    except (TypeError, ValueError):
        return -1


def parse_cc_lines(text_value, domain=""):
    """Parse one Common Crawl index response into a small dict.

    Keys: pages (records that parsed), last_crawl (newest timestamp or ""),
    ok (at least one record parsed) and statuses (HTTP status -> count).
    Never raises: empty bodies, "No Captures found" text and broken lines all
    come back as a zero-page result, which is what a caller wants to store.

    `domain` is accepted so every call site can pass the queried domain; the
    query is already scoped server-side, so nothing is filtered here.
    """
    result = {"pages": 0, "last_crawl": "", "ok": False, "statuses": {}}
    if isinstance(text_value, bytes):
        text_value = text_value.decode("utf-8", "replace")
    body = (text_value or "").strip()
    if not body or body.lower().startswith(NO_CAPTURES):
        return result

    for record in _json_records(body):
        if not str(record.get("url") or "").strip():
            continue                        # not a capture record
        result["pages"] += 1
        status = str(record.get("status") or "").strip()
        if status:
            result["statuses"][status] = result["statuses"].get(status, 0) + 1
        stamp = str(record.get("timestamp") or "").strip()
        if stamp and _stamp_key(stamp) > _stamp_key(result["last_crawl"]):
            result["last_crawl"] = stamp

    result["ok"] = result["pages"] > 0
    return result


def latest_index():
    """Return the newest crawl id, e.g. 'CC-MAIN-2026-43'."""
    try:
        data = net.json_body(COLLINFO_URL, timeout=30)
    except ValueError as e:
        raise net.NetError("Common Crawl collinfo.json is not valid JSON: %s" % e)
    items = data if isinstance(data, list) else [data]
    for item in items:
        if isinstance(item, dict) and str(item.get("id") or "").strip():
            return str(item["id"]).strip()
    raise net.NetError("Common Crawl collinfo.json listed no crawl id.")


def index_url(index_id, domain, limit=RECORD_LIMIT):
    """Build the per-domain query URL for one crawl."""
    return INDEX_URL % (index_id, net.quote(domain), limit)


def _delay_param(ctx):
    try:
        value = float(ctx.param("delay", 0.4))
    except (TypeError, ValueError):
        return 0.4
    return max(0.0, min(value, MAX_DELAY))


def pick_domains(ctx, limit, days):
    """Domains due for a footprint check, plus lead-only domains if asked."""
    cutoff = db.now() - days * 86400
    rows = db.rows(
        "SELECT domain FROM domains WHERE cc_checked_ts=0 OR cc_checked_ts<? "
        "ORDER BY first_seen_ts DESC LIMIT ?", (cutoff, limit))
    domains = [r["domain"] for r in rows if r.get("domain")]
    if not bool(ctx.param("include_lead_domains", True)):
        return domains[:limit]

    known = {d.lower() for d in domains}
    for lead in db.rows("SELECT DISTINCT domain FROM leads WHERE domain<>''"):
        value = (lead["domain"] or "").strip().lower()
        if not value or "." not in value or value in known:
            continue
        if db.one("SELECT domain FROM domains WHERE domain=?", (value,)):
            continue                        # already tracked: the sweep will get it
        known.add(value)
        domains.append(value)
    return domains[:limit]


def run(ctx):
    try:
        limit = ctx.int_param("limit", 60)
        days = ctx.int_param("days", 30)
        delay = _delay_param(ctx)

        index = latest_index()
        ctx.log("Common Crawl index: %s" % index)

        domains = pick_domains(ctx, limit, days)
        if not domains:
            return ctx.summary(ok=True, index=index, checked=0, hits=0,
                               note="Every known domain is already checked.")

        checked = hits = 0
        for position, domain in enumerate(domains):
            if position:
                time.sleep(delay)
            try:
                body = net.text(index_url(index, domain), timeout=60)
            except net.NetError as e:
                if e.status != 404:
                    ctx.warn("%s: %s" % (domain, e))
                    continue
                body = ""                   # 404 is the index's "no captures" answer
            except Exception as e:          # noqa: BLE001 - one bad domain only
                ctx.warn("%s: %s" % (domain, e))
                continue

            parsed = parse_cc_lines(body, domain)
            checked += 1
            if parsed["pages"]:
                hits += 1
            row = db.one("SELECT cc_last_crawl FROM domains WHERE domain=?", (domain,)) or {}
            previous = str(row.get("cc_last_crawl") or "")
            ctx.upsert_domain(domain, cc_pages=parsed["pages"],
                              cc_last_crawl=parsed["last_crawl"],
                              cc_checked_ts=db.now())

            newly_found = not previous and parsed["pages"] > 0
            changed = bool(previous) and bool(parsed["last_crawl"]) \
                and parsed["last_crawl"] != previous
            if not (newly_found or changed):
                continue
            ctx.add_signal("cc", domain=domain, source="commoncrawl", source_url=INDEX_PAGE,
                           payload={"pages": parsed["pages"],
                                    "last_crawl": parsed["last_crawl"],
                                    "statuses": parsed["statuses"]})

        ctx.log("footprint checked %d domains, %d with captures" % (checked, hits))
        return ctx.summary(ok=True, index=index, checked=checked, hits=hits)
    except net.NetError as e:
        ctx.warn("Common Crawl index unavailable: %s" % e)
        return ctx.summary(ok=False, error=str(e), checked=0, hits=0)
    except Exception as e:                  # noqa: BLE001 - a job must never raise
        ctx.warn("common crawl job failed: %s" % e)
        return ctx.summary(ok=False, error=str(e))


JOBS = [Job(
    key="commoncrawl",
    name="Common Crawl: domain footprint",
    group="Domains",
    description="Checks known and lead domains against the Common Crawl index and stores whether "
                "the site was crawled, how many pages and when the last capture happened.",
    run=run,
    schedule="daily 03:00",
    params={"limit": 60, "days": 30, "delay": 0.4, "include_lead_domains": True},
)]
