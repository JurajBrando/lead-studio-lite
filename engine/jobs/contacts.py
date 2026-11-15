"""Contact finder: read a domain's site and turn it into a lead.

The missing step between "we found a company" and "we have someone to email".
It reads the homepage and the contact/about/team pages, extracts addresses and
phone numbers (including [at]/[dot] and Cloudflare-obfuscated ones), and creates
a lead from the best address it finds.

If the site publishes nothing and a Hunter.io key is configured, it asks Hunter
for the domain's addresses instead. Without a key it simply reports the domains
where nothing was found - no error, no noise.
"""
import json
import time

from .. import connectors, crawl, db, net
from .base import Job, Context


def _already_checked(data_text):
    try:
        payload = json.loads(data_text or "{}")
    except ValueError:
        return False
    return bool(payload.get("contact_checked_ts"))


def run(ctx):
    limit = ctx.int_param("limit", 25)
    max_pages = ctx.int_param("max_pages", 4)
    respect = bool(ctx.param("respect_robots", True))
    pause = float(ctx.param("pause", 0.6))
    use_hunter = bool(ctx.param("hunter_fallback", False))
    hunter_ready = connectors.status().get("hunter", False)

    rows = db.rows(
        "SELECT domain, data FROM domains "
        "WHERE domain NOT IN (SELECT domain FROM leads WHERE domain<>'') "
        "ORDER BY first_seen_ts DESC LIMIT ?", (limit * 5,))
    candidates = [r["domain"] for r in rows if not _already_checked(r.get("data"))][:limit]

    if not candidates:
        return ctx.summary(checked=0, found=0, leads=0,
                           note="No unchecked domains without a lead. Run the new-domain job first.")

    checked = found = created = 0
    hunter_used = 0
    for domain in candidates:
        checked += 1
        result = crawl.crawl_domain(domain, max_pages=max_pages, respect_robots=respect,
                                    pause=pause)
        ctx.stats["domains"] += 1
        ctx.upsert_domain(domain, data={
            "contact_checked_ts": db.now(), "emails": result["emails"],
            "phones": result["phones"], "title": result["title"][:200],
            "description": result["description"][:300],
            "blocked_by_robots": result["blocked_by_robots"]})

        email = (result["emails"] or [""])[0]
        source = "crawler"
        if not email and use_hunter and hunter_ready:
            try:
                hunter = connectors.hunter_domain(domain, limit=5)
                best = sorted(hunter.get("emails") or [],
                              key=lambda e: -int(e.get("confidence") or 0))
                if best:
                    email = best[0]["email"]
                    source = "hunter"
                    hunter_used += 1
            except connectors.ConnectorError as exc:
                ctx.warn(str(exc))
                hunter_ready = False

        if email:
            found += 1
            company = (result["title"] or "").split("|")[0].split("—")[0].strip()[:120] or domain
            notes = "Found by the contact crawler on %s" % " ".join(result["pages"][:2])
            if result["phones"]:
                notes += " | phones: %s" % ", ".join(result["phones"][:3])
            lead_id = ctx.create_lead(
                email, company=company, domain=domain, source=source, notes=notes,
                city="", industry="", website="https://" + domain)
            if lead_id:
                created += 1
                ctx.add_signal("contact_found", domain=domain, company=company, title=email,
                               source=source, source_url="https://" + domain,
                               payload={"pages": result["pages"], "phones": result["phones"]})
        time.sleep(max(0.0, pause))

    ctx.log("checked %d domains, %d had an address, %d new leads (%d via Hunter)" % (
        checked, found, created, hunter_used))
    return ctx.summary(checked=checked, found=found, leads=created, hunter=hunter_used,
                       hunter_configured=hunter_ready)


JOBS = [Job(
    key="contact_finder",
    name="Contact finder: domains -> emails",
    group="Enrichment",
    description="Reads each new domain's site (robots-aware), extracts addresses and phones from the "
                "contact/about/team pages, and creates a lead from the best address it finds.",
    run=run,
    schedule="daily 09:00",
    params={"limit": 25, "max_pages": 4, "respect_robots": True, "pause": 0.6,
            "hunter_fallback": False},
)]
