"""Contacts out of the Common Crawl archive: index row -> WARC record -> address.

The contact finder reads the live site; this one reads the copy Common Crawl already made.
Same extraction, different source - and it works for domains whose site is parked, dead or
refuses bots, which is exactly the tail the live crawler leaves behind.

The fetch is batched the way a bulk scraper does it: plan every homepage/contact page first,
group the plan by WARC file so a run opens a few hundred files instead of thousands, download
in parallel threads, and remember each record so a stop resumes.
"""
import json

from .. import cc_fetch, cc_index, db
from .base import Job, Context

LIMIT = 10


def _has_contact(data_text):
    try:
        payload = json.loads(data_text or "{}")
    except ValueError:
        return False
    return bool(payload.get("emails") or payload.get("contact_checked_ts"))


def run(ctx):
    limit = ctx.int_param("limit", LIMIT)
    per_domain = ctx.int_param("max_pages", 2)
    parallel = ctx.int_param("parallel", 24)
    source = (ctx.param("source") or "bucket").strip()
    crawl = (ctx.param("crawl") or "").strip()
    local_dir = (ctx.param("path") or "").strip()

    rows = db.rows(
        "SELECT domain, data FROM domains WHERE domain NOT IN "
        "(SELECT domain FROM leads WHERE domain<>'') AND cc_checked_ts>0 "
        "ORDER BY cc_checked_ts DESC LIMIT ?", (limit * 5,))
    candidates = [row["domain"] for row in rows if not _has_contact(row.get("data"))][:limit]
    if not candidates:
        return ctx.summary(checked=0, found=0, leads=0,
                           note="No indexed domains without a contact. Run the bulk index "
                                "match first (or the live contact finder).")

    if local_dir:
        from .cc_bulk import local_bands
        bands = local_bands(local_dir)
    else:
        bands = cc_index.load_bands(crawl=crawl, source=source)

    plan_rows = cc_fetch.plan(candidates, bands, local_dir, per_domain=per_domain,
                              source=source, crawl=crawl)
    if not plan_rows:
        return ctx.summary(checked=len(candidates), found=0, leads=0,
                           note="The index has no page for those domains.")
    done = cc_fetch.done_keys()
    plan_rows = [row for row in plan_rows if cc_fetch.rkey(row[0], row[1]) not in done]
    if not plan_rows:
        return ctx.summary(checked=len(candidates), found=0, leads=0,
                           note="Every page of those domains was fetched already.")
    ctx.log("%d pages planned across %d domains, parallel %d" % (
        len(plan_rows), len(candidates), parallel))

    def progress(done_count, total, rate):
        if done_count % 200 == 0 or done_count == total:
            ctx.log("%d/%d pages | %.0f pages/s" % (done_count, total, rate))

    results = cc_fetch.run(plan_rows, parallel=parallel, progress=progress, source=source)
    summary = cc_fetch.save(results)
    ctx.log("checked %d domains, %d with an address, %d new leads" % (
        summary["domains"], summary["with_email"], summary["leads"]))
    return ctx.summary(checked=summary["domains"], found=summary["with_email"],
                       leads=summary["leads"], pages=len(results))


JOBS = [
    Job(key="cc_contacts",
        name="Contacts from the Common Crawl archive",
        group="Domains",
        description="For domains the live crawler could not read: takes the pages the index "
                    "points at (contact/about first), downloads their WARC records by byte range "
                    "in parallel grouped by WARC file, and extracts the addresses and phones.",
        run=run,
        schedule="daily 09:30",
        needs_network=True,
        params={"limit": LIMIT, "max_pages": 2, "parallel": 24, "path": "",
                "source": "bucket", "crawl": ""}),
]
