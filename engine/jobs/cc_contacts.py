"""Contacts out of the Common Crawl archive: index row -> WARC record -> address.

The contact finder reads the live site; this one reads the copy Common Crawl already made.
Same extraction, different source - and it works for domains whose site is parked, dead or
refuses bots, which is exactly the tail the live crawler leaves behind.
"""
import json

from .. import cc_index, cc_warc, db
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
    max_pages = ctx.int_param("max_pages", 4)
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

    bands = None
    if local_dir:
        from .cc_bulk import local_bands
        bands = local_bands(local_dir)
    else:
        bands = cc_index.load_bands()

    checked = found = created = 0
    for domain in candidates:
        checked += 1
        pages = cc_index.pages(domain, bands=bands, local_dir=local_dir, max_pages=max_pages)
        if not pages:
            ctx.upsert_domain(domain, data={"contact_checked_ts": db.now(), "emails": [],
                                            "source": "cc-warc"})
            continue
        emails, phones, title, page_urls = [], [], "", []
        for page in pages:
            try:
                raw = cc_warc.fetch_record(page["warc_filename"], page["offset"], page["length"])
            except Exception as exc:                       # noqa: BLE001 - one page failing is fine
                ctx.warn("%s: %s" % (page["url"][:60], exc))
                continue
            parsed = cc_warc.parse_record(raw)
            if not parsed.get("ok"):
                continue
            info = cc_warc.contacts_of(parsed.get("html"), domain)
            page_urls.append(page["url"])
            title = title or info["title"]
            for email in info["emails"]:
                if email not in emails:
                    emails.append(email)
            for phone in info["phones"]:
                if phone not in phones:
                    phones.append(phone)
            if emails:
                break
        ctx.stats["domains"] += 1
        ctx.upsert_domain(domain, data={"contact_checked_ts": db.now(), "emails": emails,
                                        "phones": phones, "title": title[:200],
                                        "pages": page_urls[:5], "source": "cc-warc"})
        if emails:
            found += 1
            company = (title.split("|")[0].split("—")[0].strip()[:120] or domain)
            notes = "Found in the Common Crawl archive on %s" % (page_urls[0] if page_urls else domain)
            if phones:
                notes += " | phones: %s" % ", ".join(phones[:3])
            lead_id = ctx.create_lead(emails[0], company=company, domain=domain,
                                      source="cc-warc", notes=notes, website="https://" + domain)
            if lead_id:
                created += 1
                ctx.add_signal("contact_found", domain=domain, company=company,
                               title=emails[0], source="cc-warc",
                               source_url=page_urls[0] if page_urls else "https://" + domain,
                               payload={"emails": emails[:5], "phones": phones[:3],
                                        "pages": page_urls[:5]})
    ctx.log("checked %d domains, %d with an address, %d new leads" % (checked, found, created))
    return ctx.summary(checked=checked, found=found, leads=created)


JOBS = [
    Job(key="cc_contacts",
        name="Contacts from the Common Crawl archive",
        group="Domains",
        description="For domains the live crawler could not read: takes the page the index "
                    "points at (contact/about first), fetches that one WARC record by byte range "
                    "and extracts the addresses and phones, then creates the lead.",
        run=run,
        schedule="daily 09:30",
        needs_network=True,
        params={"limit": LIMIT, "max_pages": 4, "path": ""}),
]
