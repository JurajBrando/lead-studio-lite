"""Batched archive fetching: the shape that makes 76 pages a second possible.

A per-domain loop pays a full round trip per page and re-opens a WARC for every page. The fast
shape is the one a bulk scraper uses:

  1. plan once - for every pending domain, take its homepage and its contact page from the index,
     with the WARC file and byte range of each;
  2. **group by WARC file**, biggest group first, so a run touches a few hundred files, not
     thousands, and every range in a group is close to the previous one;
  3. download in parallel threads (stdlib only, one keep-alive connection per thread);
  4. parse and extract as the bytes arrive, and write progress per record so a stop is resumable.

This module does that inside the app: no aiohttp, no duckdb, same batching idea.
"""
import csv
import http.client
import os
import threading
import time
import urllib.error
import urllib.request

from . import cc_warc, config, crawl, db

TABLE = "cc_fetch"
HOST = "data.commoncrawl.org"
BUCKET = "https://huggingface.co/buckets/commoncrawl/commoncrawl/resolve/"
BUCKET_HOST = "huggingface.co"
BUCKET_PREFIX = "/buckets/commoncrawl/commoncrawl/resolve/"
CSV_HEADER = ["domain", "kind", "url", "status", "emails", "phones", "title", "file", "off", "len"]
_LOCAL = threading.local()


def _connection(source="http"):
    """One keep-alive connection per worker thread and source."""
    conn = getattr(_LOCAL, "conn", None)
    if conn is None or getattr(_LOCAL, "source", None) != source:
        host = BUCKET_HOST if source == "bucket" else HOST
        conn = http.client.HTTPSConnection(host, timeout=60)
        _LOCAL.conn = conn
        _LOCAL.source = source
    return conn


def _target(path, source="http"):
    clean = "/" + str(path or "").lstrip("/")
    return BUCKET_PREFIX + clean.lstrip("/") if source == "bucket" else clean


def fetch_range(path, offset, length, timeout=60, attempts=2, source="http"):
    """A byte range from the archive over a reused connection.

    `source="bucket"` reads the Common Crawl copy on Hugging Face
    (`huggingface.co/buckets/commoncrawl/commoncrawl/resolve/...`), which answers range requests
    publicly and follows the same `crawl-data/...` layout; `"http"` reads data.commoncrawl.org.
    """
    start = int(offset)
    end = start + max(1, int(length)) - 1
    target = _target(path, source)
    headers = {"Range": "bytes=%d-%d" % (start, end),
               "User-Agent": "LeadStudioLite/%s (local lead enrichment)" % config.VERSION,
               "Accept-Encoding": "identity"}
    last_error = None
    for _ in range(max(1, attempts)):
        try:
            if source == "bucket":
                # The bucket answers 302 to a signed CDN URL; urllib follows it and keeps the
                # Range header, so ask it rather than the keep-alive connection.
                request = urllib.request.Request(BUCKET + str(path or "").lstrip("/"),
                                                 headers=headers)
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    if response.status not in (200, 206):
                        raise http.client.HTTPException("HTTP %s" % response.status)
                    return response.read()
            conn = _connection(source)
            conn.request("GET", target, headers=headers)
            response = conn.getresponse()
            data = response.read()
            if response.status not in (200, 206):
                raise http.client.HTTPException("HTTP %s" % response.status)
            return data
        except Exception as exc:                       # noqa: BLE001 - retried once
            last_error = exc
            try:
                _LOCAL.conn.close()
            except Exception:                          # noqa: BLE001
                pass
            _LOCAL.conn = None
    raise OSError(str(last_error)[:120])


def ensure_table():
    db.execute(
        "CREATE TABLE IF NOT EXISTS cc_fetch (rkey TEXT PRIMARY KEY, domain TEXT NOT NULL, "
        "kind TEXT DEFAULT '', url TEXT DEFAULT '', status TEXT DEFAULT '', emails TEXT DEFAULT '', "
        "phones TEXT DEFAULT '', title TEXT DEFAULT '', ts INTEGER DEFAULT 0)")


def rkey(domain, kind):
    return "%s|%s" % (str(domain or "").lower(), kind)


def done_keys():
    ensure_table()
    return {row["rkey"] for row in db.rows("SELECT rkey FROM cc_fetch WHERE status='OK'")}


def plan(domains, bands, local_dir="", per_domain=2, status=200):
    """[(domain, kind, url, file, off, len)] - homepage and contact page of each domain."""
    from . import cc_index
    rows = cc_index.plan_pages(domains, bands=bands, local_dir=local_dir,
                               per_domain=per_domain, status=status)
    return [(row["domain"], row["kind"], row["url"], row["warc_filename"],
             row["offset"], row["length"]) for row in rows]


def group_by_file(plan_rows):
    groups = {}
    for row in plan_rows:
        groups.setdefault(row[3], []).append(row)
    return sorted(groups.items(), key=lambda item: -len(item[1]))


def _fetch_one(row, timeout=60, source="http"):
    domain, kind, url, warc_file, offset, length = row
    try:
        raw = fetch_range(warc_file, offset, length, timeout=timeout, source=source)
    except (OSError, http.client.HTTPException) as exc:
        return {"rkey": rkey(domain, kind), "domain": domain, "kind": kind, "url": url,
                "status": "FAILED:%s" % str(exc)[:60], "emails": [], "phones": [], "title": ""}
    parsed = cc_warc.parse_record(raw)
    if not parsed.get("ok"):
        return {"rkey": rkey(domain, kind), "domain": domain, "kind": kind, "url": url,
                "status": "BADRECORD", "emails": [], "phones": [], "title": ""}
    info = cc_warc.contacts_of(parsed.get("html"), domain)
    return {"rkey": rkey(domain, kind), "domain": domain, "kind": kind, "url": url,
            "status": "OK" if parsed.get("status") == 200 else "HTTP%d" % parsed.get("status"),
            "emails": info["emails"], "phones": info["phones"], "title": info["title"]}


def run(plan_rows, parallel=24, timeout=60, progress=None, source="http"):
    """Fetch every planned record in parallel threads, keeping the WARC-file order.

    One pool for the whole plan, not one per group: a plan whose records span many files would
    otherwise fetch one record at a time. The order still follows the WARC file, so consecutive
    requests land in the same file whenever a site contributes more than one page.
    """
    from concurrent.futures import ThreadPoolExecutor

    started = time.time()
    ordered = []
    for _, rows in group_by_file(plan_rows):
        ordered.extend(rows)
    results = []
    total = len(ordered)
    if not total:
        return results
    with ThreadPoolExecutor(max_workers=max(1, int(parallel))) as pool:
        futures = [pool.submit(_fetch_one, row, timeout, source) for row in ordered]
        for future in futures:
            results.append(future.result())
            if progress:
                elapsed = max(1e-6, time.time() - started)
                progress(len(results), total, len(results) / elapsed)
    return results


def save(results):
    """One row per record for resume, then the domain and the lead."""
    ensure_table()
    now = db.now()
    by_domain = {}
    for result in results:
        db.execute(
            "INSERT OR REPLACE INTO cc_fetch (rkey, domain, kind, url, status, emails, phones, "
            "title, ts) VALUES (?,?,?,?,?,?,?,?,?)",
            (result["rkey"], result["domain"], result["kind"], result["url"], result["status"],
             ";".join(result["emails"]), ";".join(result["phones"]), result["title"][:200], now))
        entry = by_domain.setdefault(result["domain"], {"emails": [], "phones": [],
                                                        "title": "", "pages": []})
        entry["pages"].append(result["url"])
        entry["title"] = entry["title"] or result["title"]
        for email in result["emails"]:
            if email not in entry["emails"]:
                entry["emails"].append(email)
        for phone in result["phones"]:
            if phone not in entry["phones"]:
                entry["phones"].append(phone)
    created = 0
    for domain, entry in by_domain.items():
        db.execute("UPDATE domains SET cc_checked_ts=? WHERE domain=?", (now, domain))
        db.execute("UPDATE domains SET data=? WHERE domain=?", (_merge_data(domain, entry), domain))
        if not entry["emails"]:
            continue
        if db.one("SELECT id FROM leads WHERE domain=? AND email<>'' LIMIT 1", (domain,)):
            continue
        company = (entry["title"].split("|")[0].split("—")[0].strip()[:120] or domain)
        notes = "Found in the Common Crawl archive on %s" % (entry["pages"][0] if entry["pages"] else domain)
        if entry["phones"]:
            notes += " | phones: %s" % ", ".join(entry["phones"][:3])
        db.insert("leads", {"email": entry["emails"][0].lower(), "company": company,
                            "domain": domain, "source": "cc-warc", "status": "new",
                            "verify_status": "unverified", "notes": notes[:2000],
                            "created_ts": now, "updated_ts": now})
        created += 1
    return {"domains": len(by_domain), "leads": created,
            "with_email": sum(1 for entry in by_domain.values() if entry["emails"])}


def _merge_data(domain, entry):
    import json
    row = db.one("SELECT data FROM domains WHERE domain=?", (domain,))
    try:
        payload = json.loads((row or {}).get("data") or "{}")
    except ValueError:
        payload = {}
    payload.update({"contact_checked_ts": db.now(), "emails": entry["emails"][:12],
                    "phones": entry["phones"][:5], "title": entry["title"][:200],
                    "pages": entry["pages"][:5], "source": "cc-warc"})
    return json.dumps(payload)[:8000]


def export_csv(path):
    """The same shape a bulk scraper writes, so the results can be merged elsewhere."""
    ensure_table()
    rows = db.rows("SELECT * FROM cc_fetch")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(CSV_HEADER)
        for row in rows:
            writer.writerow([row["domain"], row["kind"], row["url"], row["status"],
                             row["emails"], row["phones"], row["title"],
                             "", "", ""])
    return {"ok": True, "rows": len(rows), "path": path}
