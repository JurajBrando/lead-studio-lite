"""The bulk passes over the whole domain catalogue.

The Domains tab has a button for each: cleaning the junk, the DNSniffer live check, a
downloaded live-websites list, the mail-server check, the Common Crawl archive, the
OpenStreetMap lookup and a live scrape. They run on *every* domain in the list (or a batch,
when `all` is false), are cancellable, resume from what is already stored, and end the same
way: a domain that shows a contact - an address, a phone, a name and a description - becomes
a lead, and an existing lead for that domain is enriched rather than duplicated.

Each pass stores its own marker in `domains.data`, so pressing the button again continues
instead of paying for the same domain twice. Set `refresh` to redo every domain.
"""
import json
import time

from .. import (cc_fetch, cc_warc, crawl, db, dnsniffer, domain_clean, leadbook, live_list,
                net, osm, verify)
from . import cc as cc_job
from .base import Job


def _where(marker, refresh):
    clause = "domain<>''"
    if marker and not refresh:
        clause += " AND data NOT LIKE '%%\"%s\"%%'" % marker
    return clause


def _iter_domains(ctx, where, batch=200, columns="domain, name, data"):
    """Feed the work list in keyset pages, so a catalogue of millions stays cheap to walk."""
    all_flag = bool(ctx.param("all", False))
    limit = ctx.int_param("limit", 0)
    if not all_flag and limit <= 0:
        limit = 500
    only = [name.strip().lower() for name in ctx.list_param("domains") if name.strip()]
    params = ()
    if only:
        where = "(%s) AND domain IN (%s)" % (where, ",".join("?" for _ in only))
        params = tuple(only)
    total = db.scalar("SELECT COUNT(*) FROM domains WHERE " + where, params)
    if limit > 0:
        total = min(total, limit)
    cursor, sent = "", 0
    while True:
        take = batch
        if limit > 0:
            take = min(batch, limit - sent)
            if take <= 0:
                break
        rows = db.rows("SELECT %s FROM domains WHERE %s AND domain>? ORDER BY domain LIMIT ?"
                       % (columns, where), params + (cursor, take))
        if not rows:
            break
        cursor = rows[-1]["domain"]
        sent += len(rows)
        yield rows, total, sent


def _data(row):
    try:
        return json.loads(row.get("data") or "{}")
    except ValueError:
        return {}


def _company(domain, row_data, title=""):
    name = (title or row_data.get("title") or row_data.get("name") or "").split("|")[0]
    name = name.split(" - ")[0].strip()
    return name[:120] or domain


def _save_lead(ctx, counts, **fields):
    answer = leadbook.upsert(**fields)
    if answer["created"]:
        counts["leads"] += 1
        ctx.stats["leads"] += 1
    elif answer["changed"]:
        counts["enriched"] += 1
    return answer


# ------------------------------------------------------------------ 1. MX
def run_mx(ctx):
    days = ctx.int_param("days", 30)
    cutoff = db.now() + 1 if days <= 0 else db.now() - days * 86400
    where = "domain<>'' AND (mx_checked_ts=0 OR mx_checked_ts<%d)" % int(cutoff)
    counts = {"checked": 0, "alive": 0, "dead": 0}
    for rows, total, sent in _iter_domains(ctx, where, columns="domain"):
        for row in rows:
            ctx.check_cancel()
            domain = row["domain"]
            hosts, source = verify.mx_lookup(domain)
            status = "ok" if hosts else (
                "none" if source in ("dnspython", "nslookup") else "unknown")
            ctx.upsert_domain(domain, mx_status=status, mx_host=(hosts[0] if hosts else ""),
                              mx_checked_ts=db.now())
            counts["checked"] += 1
            if hosts:
                counts["alive"] += 1
            else:
                counts["dead"] += 1
                ctx.add_signal("mx_missing", domain=domain, source="dns",
                               title="No mail server", source_url="https://dns.google/",
                               payload={"source": source})
        ctx.progress(sent, total, "MX check")
        ctx.log("%d/%d domains | %d can receive mail | %d cannot" % (
            sent, total, counts["alive"], counts["dead"]))
    return ctx.summary(ok=True, **counts)


# ------------------------------------------------------------------ 2. Common Crawl
def run_cc(ctx):
    refresh = bool(ctx.param("refresh", False))
    per_domain = max(1, min(8, ctx.int_param("per_domain", 3)))
    parallel = max(1, min(64, ctx.int_param("parallel", 24)))
    batch = max(10, min(500, ctx.int_param("batch", 150)))
    source = (ctx.param("source") or "bucket").strip()
    crawl_id = (ctx.param("crawl") or "").strip()
    local_dir = (ctx.param("path") or "").strip()
    index_workers = max(1, min(12, ctx.int_param("index_workers", 6)))
    index_delay = max(0.0, min(3.0, float(ctx.param("index_delay", 0.2))))

    where = _where("cc_bulk_ts", refresh)
    counts = {"domains": 0, "indexed": 0, "with_contact": 0, "leads": 0, "enriched": 0,
              "index_failed": 0}
    bands = None
    index = ""
    if local_dir:
        try:
            bands = _local_bands(local_dir)
        except Exception as exc:                 # noqa: BLE001 - reported as the job result
            return {"ok": False, "error": "The local index could not be read: %s" % exc}
        ctx.log("%d local index shards ready" % len(bands))
    else:
        try:
            index = crawl_id or cc_job.latest_index()
        except Exception as exc:                 # noqa: BLE001
            return {"ok": False, "error": "Common Crawl index unavailable: %s" % exc}
        ctx.log("Common Crawl index: %s" % index)

    for rows, total, sent in _iter_domains(ctx, where, batch=batch):
        ctx.check_cancel()
        names = [row["domain"] for row in rows]
        ctx.progress(sent, total, "Common Crawl index")
        failed = 0
        ok_domains = set(names)
        if bands is not None:
            try:
                plan_rows = cc_fetch.plan(names, bands, local_dir, per_domain=per_domain,
                                          source=source)
            except Exception as exc:             # noqa: BLE001 - one bad shard is not fatal
                ctx.warn("index read failed for %d domains: %s" % (len(names), exc))
                plan_rows = []
                ok_domains = set()
        else:
            plan_rows, ok_domains, failed = _plan_cdx(names, index, per_domain,
                                                      index_workers, index_delay)
            if failed:
                ctx.warn("%d index queries failed in this batch" % failed)
                counts["index_failed"] += failed
        planned = {row[0] for row in plan_rows}
        done = cc_fetch.done_keys(names)
        plan_rows = [row for row in plan_rows if cc_fetch.rkey(row[0], row[1]) not in done]
        if plan_rows:
            ctx.progress(sent, total, "downloading %d archived pages" % len(plan_rows))
            results = cc_fetch.run(plan_rows, parallel=parallel, source=source)
            answer = cc_fetch.save(results)
            counts["domains"] += answer["domains"]
            counts["with_contact"] += answer["with_contact"]
            counts["leads"] += answer["leads"]
            counts["enriched"] += answer["enriched"]
        now = db.now()
        for row in rows:
            domain = row["domain"]
            if domain not in ok_domains:
                continue                         # index was down: try again next run
            if domain in planned:
                counts["indexed"] += 1
            ctx.upsert_domain(domain, data={"cc_bulk_ts": now},
                              **({} if domain in planned else {"cc_checked_ts": now,
                                                               "cc_pages": 0}))
        ctx.log("%d/%d domains | %d with a contact | %d leads | %d enriched" % (
            sent, total, counts["with_contact"], counts["leads"], counts["enriched"]))
    if counts["index_failed"] and not counts["indexed"]:
        ctx.log("The Common Crawl index is not answering; no domain was marked as done, "
                "so pressing the button again retries them.")
    return ctx.summary(ok=True, **counts)


def _plan_cdx(domains, index, per_domain, workers, delay):
    """Ask the per-domain CDX index in parallel and keep the about/contact captures.

    The columnar shards are the wrong shape here: a batch of domains scattered over the
    alphabet touches almost every shard, so it downloads most of the index for a handful of
    lookups. The CDX endpoint answers one domain at a time and hands back the WARC file and
    byte range of each capture, which is all the downloader needs.
    """
    from concurrent.futures import ThreadPoolExecutor

    def one(domain):
        for attempt in range(2):
            if delay:
                time.sleep(delay)
            try:
                return domain, net.text(cc_job.index_url(index, domain, limit=100),
                                        timeout=60), True
            except net.NetError as exc:
                if exc.status == 404:
                    return domain, "", True       # the index's answer for "never captured"
                if attempt == 0 and exc.status in (429, 502, 503, 504):
                    time.sleep(2.0)               # the CDX server is busy: one quick retry
                    continue
                return domain, None, False
            except Exception:                     # noqa: BLE001 - one domain only
                return domain, None, False
        return domain, None, False

    plan_rows, ok_domains, failed = [], set(), 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for domain, body, ok in pool.map(one, domains):
            if not ok:
                failed += 1
                continue
            ok_domains.add(domain)
            if body is None:
                continue
            plan_rows.extend(_captures(body, domain, per_domain))
    return plan_rows, ok_domains, failed


def _captures(body, domain, per_domain):
    """The best about/contact and homepage captures of one domain, newest first."""
    items = []
    for record in cc_job._json_records(body or ""):
        url = str(record.get("url") or "").strip()
        filename = str(record.get("filename") or "").strip()
        if not url or not filename or str(record.get("status") or "") != "200":
            continue
        try:
            offset = int(record.get("offset") or 0)
            length = int(record.get("length") or 0)
            stamp = int(str(record.get("timestamp") or "0") or 0)
        except (TypeError, ValueError):
            continue
        if offset <= 0 or length <= 0:
            continue
        items.append({"url": url, "file": filename, "offset": offset, "length": length,
                      "score": cc_warc.page_score(url), "stamp": stamp})
    items.sort(key=lambda item: (item["score"], -item["stamp"]))
    picked, seen = [], set()
    for item in items:
        kind = "c" if item["score"] == 0 else "h"
        if kind in seen:
            continue
        seen.add(kind)
        picked.append((domain, kind, item["url"], item["file"], item["offset"], item["length"]))
        if len(picked) >= per_domain:
            break
    return picked


def _local_bands(folder):
    from .cc_bulk import local_bands
    return local_bands(folder)


# ------------------------------------------------------------------ 3. OpenStreetMap
def run_osm(ctx):
    refresh = bool(ctx.param("refresh", False))
    area = str(ctx.param("area") or "").strip()
    pause = max(0.0, float(ctx.param("pause", 1.1)))
    batch = max(10, min(500, ctx.int_param("batch", 100)))
    where = _where("osm_ts", refresh)
    counts = {"checked": 0, "found": 0, "leads": 0, "enriched": 0}
    for rows, total, sent in _iter_domains(ctx, where, batch=batch):
        for row in rows:
            ctx.check_cancel()
            domain = row["domain"]
            data = _data(row)
            name = (row.get("name") or data.get("title") or "").strip()
            found = osm.lookup_domain(domain, name=name, area=area) or {}
            now = db.now()
            ctx.upsert_domain(domain, data={"osm_ts": now,
                                            "osm": found or {"found": False}})
            counts["checked"] += 1
            if found:
                counts["found"] += 1
                address = " ".join(filter(None, [found.get("street"), found.get("postcode"),
                                                 found.get("city"), found.get("country")]))
                notes = "OpenStreetMap: %s (%s)%s" % (found.get("name") or domain,
                                                      found.get("category") or "business",
                                                      (" | " + address) if address else "")
                _save_lead(ctx, counts, domain=domain,
                           company=found.get("name") or name or domain,
                           email=found.get("email") or "", phone=found.get("phone") or "",
                           website="https://" + domain, city=found.get("city") or "",
                           country=found.get("country") or "", industry=found.get("category") or "",
                           source="osm", notes=notes)
                ctx.add_signal("osm_match", domain=domain, company=found.get("name") or "",
                               title=found.get("category") or "business", source="osm",
                               source_url=found.get("website") or "https://" + domain,
                               payload={"osm_id": found.get("osm_id"), "address": address,
                                        "phone": found.get("phone"), "email": found.get("email")})
            if pause:
                time.sleep(pause)
        ctx.progress(sent, total, "OpenStreetMap")
        ctx.log("%d/%d domains | %d matched | %d leads" % (
            sent, total, counts["found"], counts["leads"]))
    return ctx.summary(ok=True, **counts)


# ------------------------------------------------------------------ 4. Live scrape
def run_scrape(ctx):
    refresh = bool(ctx.param("refresh", False))
    max_pages = max(1, min(8, ctx.int_param("max_pages", 4)))
    respect = bool(ctx.param("respect_robots", True))
    delay = max(0.0, float(ctx.param("delay", 0.8)))
    batch = max(5, min(200, ctx.int_param("batch", 40)))
    where = _where("scrape_ts", refresh)
    counts = {"checked": 0, "read": 0, "with_contact": 0, "leads": 0, "enriched": 0,
              "blocked": 0}
    for rows, total, sent in _iter_domains(ctx, where, batch=batch):
        for row in rows:
            ctx.check_cancel()
            domain = row["domain"]
            data = _data(row)
            result = crawl.crawl_domain(domain, max_pages=max_pages, respect_robots=respect,
                                        pause=0.2)
            contact_url = next((page for page in result["pages"][1:]
                                if not page.rstrip("/") == ("https://" + domain)), "")
            contact_url = contact_url or (result["pages"][-1] if result["pages"] else "")
            now = db.now()
            ctx.upsert_domain(domain, data={
                "scrape_ts": now, "source": "crawl",
                "title": (result["title"] or "")[:200],
                "description": (result["description"] or "")[:400],
                "emails": result["emails"][:12], "phones": result["phones"][:5],
                "pages": result["pages"][:6], "contact_url": contact_url,
                "blocked_by_robots": result["blocked_by_robots"],
                "scrape_error": result["error"][:200]})
            counts["checked"] += 1
            if result["blocked_by_robots"]:
                counts["blocked"] += 1
            if result["ok"]:
                counts["read"] += 1
            if result["emails"] or result["phones"]:
                counts["with_contact"] += 1
            if result["emails"] or result["phones"] or result["description"]:
                company = _company(domain, data, result["title"])
                notes = "Read live on %s" % ", ".join(result["pages"][:2] or ["https://" + domain])
                _save_lead(ctx, counts, domain=domain, company=company,
                           email=(result["emails"] or [""])[0],
                           phone=(result["phones"] or [""])[0], website="https://" + domain,
                           description=result["description"], source="crawler", notes=notes)
                ctx.add_signal("site_scraped", domain=domain, company=company,
                               title=(result["title"] or "")[:200], source="crawler",
                               source_url=contact_url or "https://" + domain,
                               payload={"emails": result["emails"][:6],
                                        "phones": result["phones"][:3],
                                        "pages": result["pages"][:4]})
            if delay:
                time.sleep(delay)
        ctx.progress(sent, total, "scraping sites")
        ctx.log("%d/%d domains | %d read | %d with a contact | %d leads" % (
            sent, total, counts["read"], counts["with_contact"], counts["leads"]))
    return ctx.summary(ok=True, **counts)


def run_clean(ctx):
    """Junk and dead rows out of the catalogue, before any pass spends a query on them."""
    return domain_clean.run(
        ctx,
        dry_run=bool(ctx.param("dry_run", False)),
        dns=bool(ctx.param("dns", True)),
        workers=max(1, min(128, ctx.int_param("workers", 64))),
        batch=max(50, min(5000, ctx.int_param("batch", 1000))),
        sample=max(0, min(5000, ctx.int_param("sample", 1000))))


def run_dnsniffer(ctx):
    """Keep only the domains DNSniffer lists as live websites."""
    return dnsniffer.run(
        ctx,
        dry_run=bool(ctx.param("dry_run", False)),
        require_cert=bool(ctx.param("require_cert", False)),
        workers=max(1, min(32, ctx.int_param("workers", 8))),
        batch=max(20, min(1000, ctx.int_param("batch", 200))),
        sample=max(0, min(5000, ctx.int_param("sample", 200))),
        refresh=bool(ctx.param("refresh", False)))


def run_live_list(ctx):
    """Keep only the domains a downloaded live-websites list contains."""
    return live_list.run(ctx, path=str(ctx.param("path") or ""),
                         dry_run=bool(ctx.param("dry_run", False)))


JOBS = [
    Job(key="domains_clean_all",
        name="Clean the catalogue: junk and dead domains",
        group="Domains",
        description="Walks the whole catalogue and removes what cannot produce a contact: "
                    "phone numbers and addresses in the domain column, wildcards, IP "
                    "addresses, test names, and (with DNS on) names that no longer exist. "
                    "Removed rows go to a quarantine so an import cannot silently add them "
                    "back; the leads table is never touched. Preview first to see the sizes.",
        run=run_clean,
        schedule="",
        needs_network=True,
        params={"dry_run": False, "dns": True, "workers": 64, "batch": 1000,
                "sample": 1000}),
    Job(key="domains_dnsniffer_all",
        name="DNSniffer: keep only live websites",
        group="Domains",
        description="Checks every domain against DNSniffer's index of about 380 million "
                    "domains seen in zone files and Certificate Transparency, and removes "
                    "the ones it does not list. With an API key in Settings > Connectors "
                    "each check is one exact lookup; without a key the free public MCP "
                    "search is used, which is slower. This is a 'seen live' test, not an "
                    "HTTP probe. `require_cert` also removes an indexed domain with no "
                    "certificate seen (off by default: the certificate column has gaps). "
                    "Removed domains go to the quarantine, the leads table is never "
                    "touched, and a domain that already has a lead is kept. Preview first.",
        run=run_dnsniffer,
        schedule="",
        needs_network=True,
        params={"all": True, "limit": 0, "dry_run": False, "require_cert": False,
                "workers": 8, "batch": 200, "sample": 200, "refresh": False}),
    Job(key="domains_live_list_all",
        name="Live websites list: keep only domains the file contains",
        group="Domains",
        description="Reads a downloaded live-websites list once (the DNSniffer Live "
                    "Websites Dataset is a ZIP of CSV parts with one Domain column, about "
                    "285 million names) and removes every catalogue domain the list does "
                    "not contain. The catalogue is held in memory and the file is streamed "
                    "through it, so the answer is exact and no index has to be built or "
                    "kept in date - a full pass takes ten to fifteen minutes. Removed domains "
                    "go to the quarantine, the leads table is never touched, and a domain "
                    "that already has a lead is kept. `path` is the file to read; blank "
                    "uses the one already downloaded on this computer. `dry_run` reads the "
                    "whole file and removes nothing.",
        run=run_live_list,
        schedule="",
        needs_network=False,
        params={"path": "", "dry_run": False}),
    Job(key="domains_mx_all",
        name="MX check: every domain",
        group="Domains",
        description="One DNS lookup per domain in the catalogue, so a domain that cannot "
                    "receive mail at all is known before any address on it is emailed. "
                    "Unchecked and stale domains come first; `days` 0 rechecks every one.",
        run=run_mx,
        schedule="",
        needs_network=True,
        params={"all": True, "limit": 0, "days": 30}),
    Job(key="domains_cc_all",
        name="Common Crawl: every domain to description, contacts, about page",
        group="Domains",
        description="Asks the Common Crawl CDX index about every domain, downloads its homepage "
                    "and contact/about capture from the archive, and keeps the title, "
                    "description, e-mails, phones and the contact-page link. Domains with a "
                    "contact become leads; existing leads are enriched.",
        run=run_cc,
        schedule="",
        needs_network=True,
        params={"all": True, "limit": 0, "per_domain": 3, "parallel": 24, "batch": 150,
                "source": "bucket", "crawl": "", "path": "", "refresh": False,
                "index_workers": 6, "index_delay": 0.2}),
    Job(key="domains_osm_all",
        name="OpenStreetMap: enrich every domain",
        group="Domains",
        description="Looks each domain up in OpenStreetMap (Nominatim, free) as an enrichment "
                    "pass, not a lead search: name, category, address, e-mail, phone, website "
                    "and opening hours are attached to the domain and its lead.",
        run=run_osm,
        schedule="",
        needs_network=True,
        params={"all": True, "limit": 0, "area": "", "pause": 1.1, "batch": 100,
                "refresh": False}),
    Job(key="domains_scrape_all",
        name="Scrape every domain: contacts, name, description",
        group="Domains",
        description="A normal crawler pass over every domain: reads the homepage and the "
                    "contact/about/team pages (robots.txt respected), keeps the title, "
                    "description, e-mails, phones and the contact-page link, and turns a domain "
                    "with a contact into a lead.",
        run=run_scrape,
        schedule="",
        needs_network=True,
        params={"all": True, "limit": 0, "max_pages": 4, "respect_robots": True,
                "delay": 0.8, "batch": 40, "refresh": False}),
]
