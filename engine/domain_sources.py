"""Where a domain list comes from.

Three public datasets on Hugging Face, each a different answer to "give me domains":

  ccrawl      open-index/ccrawl-domains           370M domains from the current Common
                                                   Crawl web graph, ranked by centrality
  registered  humbleworth/registered-domains       ~180M registered domains, one per line
  ct          xfox25/ct-daily-domains              one file per day of Certificate
                                                   Transparency names, the freshness feed

They are read from the Hub where they live and streamed: nothing is downloaded whole,
and only the domains the `domains` table does not have yet are inserted. That is the only
way a 370M-row list fits on a laptop - it never becomes 370M rows of SQLite.
"""
import json
import time

from . import db, net

CACHE_TTL = 900
_files = {"at": 0.0, "data": {}}

SOURCES = {
    "ccrawl": {
        "label": "Common Crawl web graph",
        "dataset": "open-index/ccrawl-domains",
        "note": "Domains in the current Common Crawl web graph, ordered by PageRank and "
                "harmonic centrality. The best general-purpose list: they were crawled, so "
                "they are live.",
        "license": "ODC-BY",
        "rows": 370000000,
        "column": "domain",
    },
    "registered": {
        "label": "Registered domains",
        "dataset": "humbleworth/registered-domains",
        "note": "About 180M registered domains, one per line. Registered is not the same as "
                "live - plenty of these are parked - and no licence is declared.",
        "license": "not declared",
        "rows": 180000000,
        "column": "(text line)",
    },
    "ct": {
        "label": "Certificate Transparency (daily)",
        "dataset": "xfox25/ct-daily-domains",
        "note": "Names that appeared in Certificate Transparency logs on one day (~42M rows). "
                "The freshness feed: a certificate was issued, so the name is new or renewed.",
        "license": "CC-BY-4.0",
        "rows": 42000000,
        "column": "name",
    },
}


class SourceError(Exception):
    """The dataset cannot be read: missing library, unreachable Hub, unknown source."""


def describe():
    out = []
    for key, entry in SOURCES.items():
        out.append({
            "key": key, "label": entry["label"], "dataset": entry["dataset"],
            "note": entry["note"], "license": entry["license"], "rows": entry["rows"],
            "column": entry["column"],
            "url": "https://huggingface.co/datasets/" + entry["dataset"],
        })
    return out


def _hub():
    try:
        import pyarrow.parquet as pq
        from huggingface_hub import HfApi, HfFileSystem
    except ImportError as exc:
        raise SourceError("Reading these datasets needs pyarrow and huggingface_hub: "
                          "pip install pyarrow huggingface_hub (%s)" % exc)
    return HfApi, HfFileSystem, pq


def _listing(dataset, refresh=False):
    """File names in the dataset, cached for a quarter of an hour."""
    HfApi, _, _ = _hub()
    now = time.time()
    if not refresh and dataset in _files["data"] and now - _files["at"] < CACHE_TTL:
        return _files["data"][dataset]
    try:
        names = list(HfApi().list_repo_files(dataset, repo_type="dataset"))
    except Exception as exc:                        # noqa: BLE001 - network, auth, rate limit
        raise SourceError("Hugging Face did not list %s: %s" % (dataset, exc))
    _files["data"][dataset] = names
    _files["at"] = now
    return names


def newest_crawl(names):
    """The newest 'data/<crawl>/part-*.parquet' group, as an ordered file list."""
    groups = {}
    for name in names:
        parts = name.split("/")
        if len(parts) >= 3 and parts[0] == "data" and name.endswith(".parquet"):
            groups.setdefault(parts[1], []).append(name)
    if not groups:
        return "", []
    crawl = sorted(groups)[-1]
    return crawl, sorted(groups[crawl])


def newest_day(names):
    """The newest 'data/YYYY/MM/DD.parquet' file, as (path, 'YYYY-MM-DD')."""
    days = sorted(n for n in names if n.startswith("data/") and n.endswith(".parquet")
                  and len(n.split("/")) == 4)
    if not days:
        return "", ""
    name = days[-1]
    stamp = "-".join(name.split("/")[1:4]).replace(".parquet", "")
    return name, stamp


def _shard_rows(fs, pq, dataset, name, batch=2000):
    path = "datasets/%s/%s" % (dataset, name)
    with fs.open(path, "rb") as handle:
        parquet = pq.ParquetFile(handle)
        for chunk in parquet.iter_batches(batch_size=batch):
            for row in chunk.to_pylist():
                yield row


def _text_lines(fs, dataset, name, batch=2000):
    """Stream a plain text file of one domain per line without loading it."""
    path = "datasets/%s/%s" % (dataset, name)
    pending = b""
    with fs.open(path, "rb") as handle:
        while True:
            block = handle.read(1 << 20)
            if not block:
                break
            pending += block
            lines = pending.split(b"\n")
            pending = lines.pop()
            for line in lines:
                value = line.decode("utf-8", "replace").strip()
                if value:
                    yield value
    if pending:
        value = pending.decode("utf-8", "replace").strip()
        if value:
            yield value


def rows(source, crawl="", day="", limit=5000, batch=2000):
    """Yield {'domain': ..., 'data': {...}} for one source. Streams, never loads whole."""
    if source not in SOURCES:
        raise SourceError("Unknown source: %s" % source)
    entry = SOURCES[source]
    _, HfFileSystem, pq = _hub()
    fs = HfFileSystem()
    dataset = entry["dataset"]
    names = _listing(dataset)
    seen = 0

    if source == "ccrawl":
        chosen, files = newest_crawl(names)
        if not files:
            raise SourceError("No crawl shards in %s." % dataset)
        for name in files:
            for row in _shard_rows(fs, pq, dataset, name):
                domain = str(row.get("domain") or "").strip().lower()
                if not domain:
                    continue
                yield {"domain": domain, "data": {
                    "pagerank": row.get("pagerank_val"), "pagerank_pos": row.get("pagerank_pos"),
                    "harmonic": row.get("harmonic_val"), "n_hosts": row.get("n_hosts"),
                    "crawl": chosen}}
                seen += 1
                if limit and seen >= limit:
                    return
        return

    if source == "registered":
        for value in _text_lines(fs, dataset, "domains.txt"):
            yield {"domain": value, "data": {}}
            seen += 1
            if limit and seen >= limit:
                return
        return

    # ct
    name, when = newest_day(names)
    if not name:
        raise SourceError("No daily files in %s." % dataset)
    for row in _shard_rows(fs, pq, dataset, name):
        domain = str(row.get("name") or "").strip().lower()
        if not domain:
            continue
        yield {"domain": domain, "data": {"ct_day": when, "tld": row.get("tld")}}
        seen += 1
        if limit and seen >= limit:
            return


def store(items, source, batch=500):
    """Insert the domains we do not have. Returns (added, already_known)."""
    added = known = 0
    pending = []
    for item in items:
        domain = net.domain_of(item.get("domain") or "") or ""
        if not domain or "." not in domain:
            continue
        pending.append((domain, item.get("data") or {}))
        if len(pending) >= batch:
            fresh, existed = _store_batch(pending, source)
            added += fresh
            known += existed
            pending = []
    if pending:
        fresh, existed = _store_batch(pending, source)
        added += fresh
        known += existed
    return added, known


def _store_batch(pairs, source):
    names = [domain for domain, _ in pairs]
    existing = set()
    for start in range(0, len(names), 400):
        chunk = names[start:start + 400]
        existing.update(r["domain"] for r in db.rows(
            "SELECT domain FROM domains WHERE domain IN (%s)" % ",".join("?" * len(chunk)),
            tuple(chunk)))
    now = db.now()
    records = []
    seen = set()
    for domain, data in pairs:
        if domain in existing or domain in seen:
            continue
        seen.add(domain)
        records.append({
            "domain": domain,
            "tld": domain.rsplit(".", 1)[-1],
            "source": source,
            "first_seen_ts": now,
            "updated_ts": now,
            "data": json.dumps(data or {}, default=str),
        })
    if records:
        db.insert_many("domains", records)
    return len(records), len(pairs) - len(records)


def pull(source, limit=5000, crawl="", day=""):
    """Import up to `limit` domains from one source into `domains`."""
    limit = max(1, min(int(limit or 5000), 200000))
    try:
        added, known = store(rows(source, crawl=crawl, day=day, limit=limit), source)
    except SourceError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:                        # noqa: BLE001 - report, do not crash the app
        return {"ok": False, "error": "Could not read %s: %s" % (source, exc)}
    db.event("domains", "%s: +%d domains, %d already known" % (source, added, known))
    return {"ok": True, "source": source, "added": added, "known": known, "limit": limit}


def ensure_domain(domain, source="lead"):
    """Put one domain in the catalogue if it is not there. Returns the domain or ''."""
    value = net.domain_of(domain) or ""
    if not value or "." not in value:
        return ""
    if db.one("SELECT domain FROM domains WHERE domain=?", (value,)):
        return ""
    now = db.now()
    db.insert("domains", {"domain": value, "tld": value.rsplit(".", 1)[-1], "source": source,
                          "first_seen_ts": now, "updated_ts": now})
    return value


def sync_lead_domains(limit=200000):
    """Every lead's domain belongs in the catalogue - with an email, or with only a domain.

    A lead imported from a `domain` column and nothing else still names a site worth
    checking; this copies those domains into `domains` once, so the tab can enrich them.
    """
    names = [row["domain"] for row in db.rows(
        "SELECT DISTINCT lower(trim(domain)) AS domain FROM leads "
        "WHERE trim(coalesce(domain,''))<>'' AND lower(trim(domain)) NOT IN "
        "(SELECT domain FROM domains) LIMIT ?", (int(limit),))]
    names = [name for name in names if name and "." in name]
    if not names:
        return {"ok": True, "added": 0}
    now = db.now()
    records = [{"domain": name, "tld": name.rsplit(".", 1)[-1], "source": "lead",
                "first_seen_ts": now, "updated_ts": now} for name in names]
    for start in range(0, len(records), 500):
        db.insert_many("domains", records[start:start + 500])
    db.event("domains", "lead domains: +%d in the catalogue" % len(records))
    return {"ok": True, "added": len(records)}
