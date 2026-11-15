"""Matching domains against the Common Crawl URL index without keeping 170 GB on disk.

The index is the columnar (URL-level) table: `url_surtkey`, `url`, `url_host_registered_domain`,
`fetch_time`, `fetch_status`, `warc_*`. Common Crawl publishes it per crawl; the Hugging Face
mirror `open-index/ccrawl-urls` carries the same table as 300-odd Spark shards (~162 GB).

Each shard covers a **narrow band of `url_surtkey`** (one file spans `com,fagenmiller` to
`com,festivalkundalini`), so a lookup does not need the whole dataset:

    1. read every shard's footer once and remember its band  -> table `cc_bands`
    2. for a batch of domains, work out which band holds `com,example` and read only that shard
    3. stream the three columns that matter and aggregate per domain

A shard is still ~530 MB and 7M rows, so this is a batch operation, not a per-domain API. For a
handful of domains the CDX endpoint (`index.commoncrawl.org`) is faster; for thousands this is
what makes the difference between "possible" and "one request per domain".

A local copy of the same table (the `M:\\index` folder, say) can be used instead: point
`cc_index.path` at it and the shards are read from disk.
"""
import glob
import json
import os
import urllib.request

from . import config, db

REPO = "open-index/ccrawl-urls"
FIELDS = ("url_host_registered_domain", "fetch_time", "fetch_status")
BATCH = 200000


def ensure_table():
    db.execute(
        "CREATE TABLE IF NOT EXISTS cc_bands (repo TEXT NOT NULL, path TEXT NOT NULL, "
        "key_low TEXT DEFAULT '', key_high TEXT DEFAULT '', rows INTEGER DEFAULT 0, "
        "crawl TEXT DEFAULT '', PRIMARY KEY (repo, path))")


def surtkey(domain):
    """`example.com` -> `com,example`, the order the index sorts a site's URLs in."""
    parts = [part.strip().lower() for part in str(domain or "").strip().split(".") if part.strip()]
    return ",".join(reversed(parts))


def _request(url, timeout=40):
    request = urllib.request.Request(url, headers={"User-Agent": "LeadStudioLite/%s" % config.VERSION})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def shards(repo=REPO, crawl=""):
    """The parquet shards of the index, optionally only one crawl's folder."""
    tree = _request("https://huggingface.co/api/datasets/%s/tree/main?recursive=true" % repo)
    paths = [item.get("path") for item in tree if str(item.get("path") or "").endswith(".parquet")]
    if crawl:
        paths = [path for path in paths if path.split("/")[-2:-1] == [crawl] or crawl in path]
    return sorted(paths)


def _fs():
    from huggingface_hub import HfFileSystem
    return HfFileSystem()


def _band_of(parquet, rows):
    index = parquet.schema_arrow.get_field_index("url_surtkey")
    stats = parquet.metadata.row_group(0).column(index).statistics
    return {"key_low": (stats.min or "")[:200] if stats else "",
            "key_high": (stats.max or "")[:200] if stats else "",
            "rows": rows}


def load_bands(repo=REPO, crawl="", refresh=False, limit=0):
    """Read every shard's footer once (a few KB each) and cache the band it covers."""
    ensure_table()
    cached = db.rows("SELECT * FROM cc_bands WHERE repo=?", (repo,))
    if cached and not refresh:
        return cached
    paths = shards(repo, crawl)
    if limit:
        paths = paths[:limit]
    fs = _fs()
    out = []
    for path in paths:
        with fs.open("datasets/%s/%s" % (repo, path), "rb") as handle:
            parquet = _pf(handle)
            band = _band_of(parquet, parquet.metadata.num_rows)
        crawl_name = path.split("/")[-2] if path.count("/") > 1 else ""
        db.execute("INSERT OR REPLACE INTO cc_bands (repo, path, key_low, key_high, rows, crawl) "
                   "VALUES (?,?,?,?,?,?)",
                   (repo, path, band["key_low"], band["key_high"], band["rows"], crawl_name))
        out.append({"repo": repo, "path": path, "crawl": crawl_name, **band})
    return out


def _pf(handle):
    import pyarrow.parquet as pq
    return pq.ParquetFile(handle)


def locate(key, bands):
    """Shards whose band can contain `key` (a surtkey prefix like `com,example`)."""
    hits = []
    for band in bands:
        low, high = band.get("key_low") or "", band.get("key_high") or ""
        if not low and not high:
            hits.append(band)
        elif low <= key <= high or low.startswith(key) or high.startswith(key):
            hits.append(band)
    return hits


def match(domains, repo=REPO, crawl="", local_dir="", bands=None, limit_shards=0):
    """{domain: pages, last capture, 200s} for the domains that appear in the index."""
    wanted = {}
    for domain in domains:
        clean = str(domain or "").strip().lower()
        if clean:
            wanted[clean] = surtkey(clean)
    results = {domain: {"found": False, "pages": 0, "last": None, "ok": 0}
               for domain in wanted}
    if not wanted:
        return results

    plan = {}
    if bands:
        # A band list is enough on its own: it works for the Hugging Face shards and for a
        # local copy of the same table, and it keeps a lookup to the one shard that can match.
        for domain, key in wanted.items():
            for band in locate(key, bands):
                path = band["path"]
                if local_dir:
                    path = os.path.join(local_dir, os.path.basename(path))
                    if not os.path.exists(path):
                        continue
                plan.setdefault(path, set()).add(domain)
    elif local_dir:
        for path in sorted(glob.glob(os.path.join(local_dir, "*.parquet"))):
            plan[path] = set(wanted)
    else:
        bands = load_bands(repo, crawl)
        for domain, key in wanted.items():
            for band in locate(key, bands):
                plan.setdefault(band["path"], set()).add(domain)
    plan = {path: names for path, names in plan.items() if names}
    if limit_shards:
        plan = dict(list(plan.items())[:limit_shards])

    opener = (lambda path: open(path, "rb")) if local_dir else \
        (lambda path: _fs().open("datasets/%s/%s" % (repo, path), "rb"))
    for path, names in plan.items():
        with opener(path) as handle:
            parquet = _pf(handle)
            for batch in parquet.iter_batches(batch_size=BATCH, columns=list(FIELDS)):
                found = batch.column(0).to_pylist()
                times = batch.column(1).to_pylist()
                statuses = batch.column(2).to_pylist()
                for domain, when, status in zip(found, times, statuses):
                    if domain in names:
                        entry = results[domain]
                        entry["found"] = True
                        entry["pages"] += 1
                        if status == 200:
                            entry["ok"] += 1
                        if when and (entry["last"] is None or when > entry["last"]):
                            entry["last"] = when
    return results


def store(results):
    """Write the matches onto the domains table (cc_pages / cc_last_crawl / cc_checked_ts)."""
    ensure_table()
    now = db.now()
    written = 0
    for domain, entry in results.items():
        if not entry.get("found"):
            db.execute("UPDATE domains SET cc_checked_ts=? WHERE domain=?", (now, domain))
            continue
        last = entry.get("last")
        stamp = ""
        if hasattr(last, "strftime"):
            stamp = last.strftime("%Y-%m-%d")
        elif last:
            stamp = str(last)[:10]
        db.execute("UPDATE domains SET cc_pages=?, cc_last_crawl=?, cc_checked_ts=? "
                   "WHERE domain=?", (int(entry.get("pages") or 0), stamp, now, domain))
        written += 1
    return written


def plan_pages(domains, bands=None, repo=REPO, crawl="", local_dir="", per_domain=2, status=200):
    """Homepage + contact page for many domains, reading each shard **once**.

    The per-domain `pages()` re-opens a shard for every domain; a worklist builder groups the
    domains by the shard that can hold them and scans it once, which is what makes a batch of
    hundreds of thousands practical.
    """
    from . import cc_warc

    wanted = {str(domain or "").strip().lower() for domain in domains if str(domain or "").strip()}
    if not wanted:
        return []
    if bands:
        shard_plan = {}
        for domain in wanted:
            for band in locate(surtkey(domain), bands):
                path = band["path"]
                if local_dir:
                    path = os.path.join(local_dir, os.path.basename(path))
                    if not os.path.exists(path):
                        continue
                shard_plan.setdefault(path, set()).add(domain)
    elif local_dir:
        shard_plan = {path: set(wanted)
                      for path in sorted(glob.glob(os.path.join(local_dir, "*.parquet")))}
    else:
        shard_plan = {}
        for band in load_bands(repo, crawl):
            shard_plan.setdefault(band["path"], set())
        for domain in wanted:
            for band in locate(surtkey(domain), bands or load_bands(repo, crawl)):
                shard_plan.setdefault(band["path"], set()).add(domain)

    found = {}
    opener = (lambda path: open(path, "rb")) if local_dir else \
        (lambda path: _fs().open("datasets/%s/%s" % (repo, path), "rb"))
    for path, names in shard_plan.items():
        if not names:
            continue
        with opener(path) as handle:
            parquet = _pf(handle)
            columns = [name for name in PAGE_FIELDS if name in parquet.schema_arrow.names]
            for batch in parquet.iter_batches(batch_size=200000, columns=columns):
                data = {name: batch.column(index).to_pylist()
                        for index, name in enumerate(columns)}
                hosts = data.get("url_host_registered_domain") or []
                for position, host in enumerate(hosts):
                    if host not in names:
                        continue
                    if status and data.get("fetch_status") and data["fetch_status"][position] != status:
                        continue
                    if not (data.get("warc_filename") or [""])[position]:
                        continue
                    found.setdefault(host, []).append({
                        "url": (data.get("url") or [""])[position],
                        "fetch_time": (data.get("fetch_time") or [None])[position],
                        "warc_filename": data["warc_filename"][position],
                        "offset": int((data.get("warc_record_offset") or [0])[position] or 0),
                        "length": int((data.get("warc_record_length") or [0])[position] or 0)})
    planned = []
    for domain, pages in found.items():
        pages.sort(key=lambda page: (cc_warc.page_score(page["url"]), 0))
        seen, picked = set(), []
        for page in pages:
            kind = "c" if cc_warc.page_score(page["url"]) == 0 else "h"
            if kind in seen:
                continue
            seen.add(kind)
            picked.append({"domain": domain, "kind": kind, **page})
            if len(picked) >= per_domain:
                break
        planned.extend(picked)
    return planned


def pending(limit=25):
    """Domains that have not been looked up in the index yet, real TLDs first."""
    return [row["domain"] for row in db.rows(
        "SELECT domain FROM domains WHERE cc_checked_ts=0 AND domain<>'' "
        "ORDER BY first_seen_ts DESC LIMIT ?", (limit,))]


PAGE_FIELDS = ("url", "url_host_registered_domain", "fetch_time", "fetch_status",
               "warc_filename", "warc_record_offset", "warc_record_length")


def pages(domain, bands=None, repo=REPO, crawl="", local_dir="", max_pages=6, status=200):
    """The archived pages of one domain, with the WARC coordinates to fetch each one.

    Contact/about pages come first, then the homepage, then whatever is left - a domain usually
    publishes its address on one of those.
    """
    from . import cc_warc
    domain = str(domain or "").strip().lower()
    if not domain:
        return []
    key = surtkey(domain)
    plan = []
    if bands:
        plan = [band["path"] for band in locate(key, bands)]
        if local_dir:
            plan = [os.path.join(local_dir, os.path.basename(path)) for path in plan]
            plan = [path for path in plan if os.path.exists(path)]
    elif local_dir:
        plan = sorted(glob.glob(os.path.join(local_dir, "*.parquet")))
    else:
        plan = [band["path"] for band in locate(key, load_bands(repo, crawl))]

    found = []
    opener = (lambda path: open(path, "rb")) if local_dir else \
        (lambda path: _fs().open("datasets/%s/%s" % (repo, path), "rb"))
    for path in plan:
        with opener(path) as handle:
            parquet = _pf(handle)
            columns = [name for name in PAGE_FIELDS if name in parquet.schema_arrow.names]
            for batch in parquet.iter_batches(batch_size=200000, columns=columns):
                data = {name: batch.column(index).to_pylist()
                        for index, name in enumerate(columns)}
                hosts = data.get("url_host_registered_domain") or []
                for position, host in enumerate(hosts):
                    if host != domain:
                        continue
                    if status and data.get("fetch_status") and data["fetch_status"][position] != status:
                        continue
                    if not (data.get("warc_filename") or [""])[position]:
                        continue
                    found.append({
                        "url": (data.get("url") or [""])[position],
                        "fetch_time": (data.get("fetch_time") or [None])[position],
                        "warc_filename": data["warc_filename"][position],
                        "offset": int((data.get("warc_record_offset") or [0])[position] or 0),
                        "length": int((data.get("warc_record_length") or [0])[position] or 0)})
    found.sort(key=lambda page: (cc_warc.page_score(page["url"]),
                                 -(page["fetch_time"].timestamp()
                                   if hasattr(page["fetch_time"], "timestamp") else 0)))
    return found[:max_pages]
