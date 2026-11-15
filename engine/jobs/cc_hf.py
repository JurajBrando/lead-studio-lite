"""Common Crawl from Hugging Face: the host index, one row per domain per crawl.

The official index (engine/jobs/cc.py) answers the other direction: "was this one
domain crawled, how many pages, when". This job brings domains *in*: the Common Crawl
Foundation publishes a host index on Hugging Face - one row per registered domain per
crawl, with the fetch statistics - and this reads one shard of the newest crawl, adds
the domains we do not know yet to `domains`, and keeps their numbers in `data`.

That is the "new domains from the last crawl, with their information" half. The daily
`commoncrawl` job then checks each new domain against the current official index, so a
domain added here gets a real footprint as well.

The mirror lags the official index by months (the newest crawl on the Hub at the time of
writing is CC-MAIN-2025-18), which is why both jobs exist.

The shard is streamed with range reads: a 300 MB parquet shard costs the batches we
actually read, not 300 MB of disk. Two caps keep a run bounded: `limit` new domains and
`max_rows` rows scanned.
"""
import json
import time

from .. import db, net
from .base import Context, Job

DATASET = "commoncrawl/host-index-testing-v2"
BATCH = 500
CACHE_TTL = 3600
_crawls = {"at": 0.0, "items": []}


class HubError(Exception):
    """pyarrow / huggingface_hub missing, or the Hub is unreachable."""


def hub():
    try:
        import pyarrow.parquet as pq
        from huggingface_hub import HfApi, HfFileSystem
    except ImportError as exc:
        raise HubError("Reading the Common Crawl host index on Hugging Face needs pyarrow "
                       "and huggingface_hub: pip install pyarrow huggingface_hub (%s)" % exc)
    return HfApi, HfFileSystem, pq


def available():
    try:
        hub()
        return True
    except HubError:
        return False


def crawls(refresh=False):
    """[(crawl_id, [parquet file, ...])], newest first."""
    HfApi, _, _ = hub()
    now = time.time()
    if not refresh and _crawls["items"] and now - _crawls["at"] < CACHE_TTL:
        return _crawls["items"]
    try:
        files = [f for f in HfApi().list_repo_files(DATASET, repo_type="dataset")
                 if f.endswith(".parquet")]
    except Exception as exc:                        # noqa: BLE001 - network, auth, rate limit
        raise HubError("Hugging Face did not list %s: %s" % (DATASET, exc))
    grouped = {}
    for path in files:
        parts = path.split("/")
        if len(parts) < 2 or "=" not in parts[1]:
            continue
        grouped.setdefault(parts[1].split("=", 1)[-1], []).append(path)
    items = sorted(grouped.items(), reverse=True)
    if not items:
        raise HubError("That dataset has no crawl shards.")
    _crawls.update({"at": now, "items": items})
    return items


def iter_hosts(files, batch=BATCH):
    """Stream row dicts out of the first shard, one batch at a time."""
    _, HfFileSystem, pq = hub()
    fs = HfFileSystem()
    path = "datasets/%s/%s" % (DATASET, files[0])
    with fs.open(path, "rb") as handle:
        parquet = pq.ParquetFile(handle)
        for chunk in parquet.iter_batches(batch_size=batch):
            for row in chunk.to_pylist():
                yield row


def domain_of_row(row):
    """The registered domain of one host-index row, or '' when the row has none."""
    value = str(row.get("url_host_registered_domain") or "").strip().lower()
    if value:
        return net.domain_of(value) or value
    surt = str(row.get("surt_host_name") or "").strip().lower()
    if surt and "," in surt:
        return net.domain_of(".".join(reversed(surt.split(",")))) or ""
    return ""


def stats_of_row(row, crawl):
    """The handful of numbers worth keeping for one domain."""
    keep = {"crawl": crawl}
    for key in ("fetch_200", "nutch_fetched", "nutch_numRecords",
                "warc_record_length_av", "hcrank", "url_host_tld"):
        value = row.get(key)
        if value is not None:
            keep[key] = value
    return keep


def store(rows, crawl):
    """Insert the domains we do not have. Returns (added, already_known)."""
    names = [domain for domain, _ in rows]
    known = set()
    for start in range(0, len(names), 400):
        chunk = names[start:start + 400]
        known.update(r["domain"] for r in db.rows(
            "SELECT domain FROM domains WHERE domain IN (%s)" % ",".join("?" * len(chunk)),
            tuple(chunk)))
    now = db.now()
    fresh = []
    for domain, row in rows:
        if domain in known:
            continue
        fresh.append({
            "domain": domain,
            "tld": domain.rsplit(".", 1)[-1],
            "source": "commoncrawl-hf",
            "first_seen_ts": now,
            "updated_ts": now,
            "cc_pages": int(row.get("fetch_200") or 0),
            "data": json.dumps(stats_of_row(row, crawl), default=str),
        })
    if fresh:
        db.insert_many("domains", fresh)
    return len(fresh), len(rows) - len(fresh)


def run(ctx):
    try:
        if not available():
            hub()
        limit = max(1, ctx.int_param("limit", 2000))
        max_rows = max(limit, ctx.int_param("max_rows", 200000))
        items = crawls()
        wanted = str(ctx.param("crawl", "") or "").strip()
        by_crawl = dict(items)
        crawl = wanted if wanted in by_crawl else items[0][0]
        ctx.log("host index: %s (%s)" % (crawl, by_crawl[crawl][0]))

        added = known = scanned = 0
        pending = []
        seen = set()
        for row in iter_hosts(by_crawl[crawl]):
            scanned += 1
            domain = domain_of_row(row)
            if domain and "." in domain and domain not in seen:
                seen.add(domain)
                pending.append((domain, row))
            if len(pending) >= BATCH:
                fresh, existing = store(pending, crawl)
                added += fresh
                known += existing
                pending = []
                if added >= limit or scanned >= max_rows:
                    break
        if pending:
            fresh, existing = store(pending, crawl)
            added += fresh
            known += existing

        ctx.log("%d rows scanned, %d new domains, %d already known" % (scanned, added, known))
        return ctx.summary(ok=True, crawl=crawl, scanned=scanned, added=added, known=known)
    except HubError as exc:
        ctx.warn(str(exc))
        return ctx.summary(ok=False, error=str(exc), added=0, scanned=0)
    except Exception as exc:                        # noqa: BLE001 - a job must never raise
        ctx.warn("host index job failed: %s" % exc)
        return ctx.summary(ok=False, error=str(exc), added=0, scanned=0)


JOBS = [Job(
    key="cc_hf",
    name="Common Crawl: new domains from the host index (Hugging Face)",
    group="Enrichment",
    description="Reads a shard of the Common Crawl host index on Hugging Face and adds the "
                "domains that are not in the list yet, with their fetch statistics. The "
                "mirror lags the official index, so it runs weekly and adds, it does not "
                "replace, the daily footprint check.",
    run=run,
    schedule="weekly sun 04:30",
    params={"limit": 2000, "max_rows": 200000, "crawl": ""},
    daily_cap=5000,
)]
