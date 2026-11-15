"""Domain MX verification.

One DNS lookup per *domain*, not per address, cached in the `domains` table so
the same domain is never checked twice within the refresh window. A domain with
no MX record cannot receive mail, which makes every address on it dead - the
cheapest filter there is before spending sends or verification credits.
"""
from .. import db
from .. import verify
from .base import Job, Context


def run(ctx):
    limit = ctx.int_param("limit", 500)
    days = ctx.int_param("days", 30)
    cutoff = db.now() - days * 86400

    rows = db.rows(
        "SELECT domain FROM domains WHERE mx_checked_ts=0 OR mx_checked_ts<? "
        "ORDER BY (mx_status='') DESC, first_seen_ts DESC LIMIT ?", (cutoff, limit))
    domains = [r["domain"] for r in rows]

    if ctx.param("include_lead_domains", True):
        known = {d.lower() for d in domains}
        for lead in db.rows("SELECT DISTINCT domain FROM leads WHERE domain<>'' LIMIT ?", (limit,)):
            value = (lead["domain"] or "").lower()
            if value and value not in known:
                domains.append(value)
                known.add(value)
        domains = domains[:limit]

    if not domains:
        return ctx.summary(checked=0, alive=0, dead=0, note="Every domain is already checked.")

    alive = dead = 0
    for domain in domains:
        hosts, source = verify.mx_lookup(domain)
        status = "ok" if hosts else ("none" if source in ("dnspython", "nslookup") else "unknown")
        ctx.upsert_domain(domain, mx_status=status, mx_host=(hosts[0] if hosts else ""),
                          mx_checked_ts=db.now())
        if hosts:
            alive += 1
        else:
            dead += 1
            ctx.add_signal("mx_missing", domain=domain, source="dns", title="No mail server",
                           source_url="https://dns.google/", payload={"source": source})

    ctx.log("checked %d domains: %d can receive mail, %d cannot" % (len(domains), alive, dead))
    return ctx.summary(checked=len(domains), alive=alive, dead=dead)


JOBS = [Job(
    key="domain_mx",
    name="Domain MX verification",
    group="Enrichment",
    description="Checks whether each domain has a mail server at all (one DNS lookup per domain, "
                "cached). Dead domains are flagged so no address on them is ever emailed.",
    run=run,
    schedule="daily 04:30",
    params={"limit": 500, "days": 30, "include_lead_domains": True},
)]
