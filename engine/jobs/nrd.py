"""New domains: the daily newly-registered-domain feed.

Source: smet.cz publishes a free daily list of newly registered domains under
CC BY 4.0. Attribution is required wherever the data surfaces, so the summary
says where it came from and the UI links to https://smet.cz/.

The list is a starting point, not a lead: a domain registered today has no
mailbox yet. The value is the pipeline behind it - MX check, Common Crawl
footprint, and the scam / phishing watchers, which all read the `domains` table.
"""
import time

from .. import db, net
from .base import Job, Context

TODAY_URL = "https://smet.cz/nrd/data/today.txt.gz"
DAY_URL = "https://smet.cz/nrd/data/daily/%s.txt.gz"
ATTRIBUTION = "Data: smet.cz (CC BY 4.0, https://smet.cz/)"


def parse_domains(text_value, tlds=None):
    """One domain per line; tolerate CSV noise, headers and comments."""
    out, seen = [], set()
    allowed = {t.lower().lstrip(".") for t in (tlds or [])}
    for raw in (text_value or "").splitlines():
        line = raw.strip().strip('"').lower()
        if not line or line.startswith(("#", "//")):
            continue
        if "," in line:
            line = line.split(",")[0].strip()
        if "://" in line:
            line = net.domain_of(line)
        line = line.rstrip(".")
        if "." not in line or " " in line or len(line) > 253:
            continue
        tld = line.rsplit(".", 1)[-1]
        if allowed and tld not in allowed:
            continue
        if line in seen:
            continue
        seen.add(line)
        out.append(line)
    return out


def run(ctx):
    tlds = ctx.list_param("tlds", ["com", "sk", "cz", "at", "de", "eu", "io", "co", "shop", "app"])
    limit = ctx.int_param("limit", 5000)
    keywords = [k.lower() for k in ctx.list_param("keywords", [])]
    signal_limit = ctx.int_param("signal_limit", 200)

    body = None
    used = ""
    for url in (TODAY_URL, DAY_URL % time.strftime("%Y-%m-%d", time.gmtime(time.time() - 86400))):
        try:
            body = net.text(url, timeout=60)
            used = url
            break
        except net.NetError as e:
            ctx.warn("feed fetch failed (%s): %s" % (url, e))
    if body is None:
        return {"ok": False, "error": "The new-domain feed could not be downloaded."}

    domains = parse_domains(body, tlds)
    ctx.log("parsed %d domains from %s" % (len(domains), net.domain_of(used) or used))
    kept = domains[:limit]
    now = db.now()
    new = 0
    for domain in kept:
        exists = db.one("SELECT domain FROM domains WHERE domain=?", (domain,))
        ctx.upsert_domain(domain, source="nrd", first_seen_ts=now if not exists else None)
        if not exists:
            new += 1

    signalled = 0
    if keywords:
        for domain in kept:
            if signalled >= signal_limit:
                break
            if any(k in domain for k in keywords):
                if ctx.add_signal("new_domain", domain=domain, source="nrd",
                                  source_url="https://smet.cz/nrd/", title=domain,
                                  payload={"matched": [k for k in keywords if k in domain]}):
                    signalled += 1
    else:
        for domain in kept[:signal_limit]:
            if ctx.add_signal("new_domain", domain=domain, source="nrd",
                              source_url="https://smet.cz/nrd/", title=domain):
                signalled += 1

    return ctx.summary(scraped=len(domains), kept=len(kept), new_domains=new,
                       signals=signalled, source=used, attribution=ATTRIBUTION)


JOBS = [Job(
    key="new_domains",
    name="New domains: daily feed",
    group="Domains",
    description="Today's newly registered domains (smet.cz, CC BY 4.0), filtered by TLD and "
                "written to the domains table for MX, Common Crawl and fraud enrichment.",
    run=run,
    schedule="daily 06:00",
    params={"tlds": ["com", "sk", "cz", "at", "de", "eu", "io", "co", "shop", "app"],
            "keywords": [], "limit": 5000, "signal_limit": 200},
)]
