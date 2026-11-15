"""Fraud watch: new phishing domains.

Source: Phishing.Database (https://github.com/Phishing-Database/Phishing.Database),
a public, daily-refreshed list of confirmed phishing domains and links, published
as plain text on raw.githubusercontent.com - one entry per line.

Two uses in an SDR workflow:
  * every entry becomes a `phishing` signal, so a host that later shows up in an
    imported lead list is already known to be hostile,
  * a phishing host that contains a lead's brand - acme-billing.example - means
    that lead's customers are being targeted right now: flag the lead.

Signals only. The job never turns a hostile domain into a lead and never emails
anyone: carrying a phishing domain in the CRM is evidence, not a prospect.
"""
import re

from .. import db, net
from .base import Job, Context

BASE_URL = "https://raw.githubusercontent.com/Phishing-Database/Phishing.Database/master/"
DOMAINS_URL = BASE_URL + "phishing-domains-ACTIVE.txt"
LINKS_URL = BASE_URL + "phishing-links-ACTIVE.txt"
SOURCES = [DOMAINS_URL, LINKS_URL]
BRAND_RX = re.compile(r"[^a-z0-9]+")
MIN_BRAND = 4                     # shorter tokens match everything


def _clean_domain(line):
    """Normalize one feed entry: a bare domain stays as-is, a link is reduced."""
    text_value = (line or "").strip()
    if not text_value:
        return ""
    if "://" in text_value:
        scheme, _, rest = text_value.partition("://")
        if "@" in rest.split("/", 1)[0]:
            rest = rest.split("@", 1)[1]        # drop http://user:pass@ tricks
        return net.domain_of(scheme + "://" + rest)
    if "/" in text_value or "@" in text_value:
        return net.domain_of(text_value)
    value = text_value.strip('"').strip("'").strip().lower().rstrip(".")
    if not value or "." not in value or " " in value or "\t" in value:
        return ""
    return value


def parse_feed(text_value):
    """Parse one feed body into a de-duplicated list of domains.

    Handles both feeds: the domain list gives bare domains, the link list gives
    full URLs whose host is extracted with `net.domain_of`. Blank lines, '#'
    comments and entries without a dot are skipped; order is preserved.
    """
    out, seen = [], set()
    if isinstance(text_value, bytes):
        text_value = text_value.decode("utf-8", "replace")
    for raw in (text_value or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        domain = _clean_domain(line)
        if not domain or domain in seen:
            continue
        seen.add(domain)
        out.append(domain)
    return out


def brand_tokens(lead):
    """Tokens that identify a lead's brand: cleaned company name, domain label."""
    tokens = []
    company = BRAND_RX.sub("", (lead.get("company") or "").lower())
    if len(company) >= MIN_BRAND:
        tokens.append(company)
    label = BRAND_RX.sub("", (lead.get("domain") or "").split(".")[0].lower())
    if len(label) >= MIN_BRAND and label not in tokens:
        tokens.append(label)
    return tokens


def load_brand_index():
    """[(lead_row, [tokens])] for every lead with a usable brand token."""
    index = []
    for lead in db.rows("SELECT id, company, domain, notes FROM leads"):
        tokens = brand_tokens(lead)
        if tokens:
            index.append((lead, tokens))
    return index


def match_impersonations(domain, index):
    """Leads whose brand appears inside `domain`, also in its punctuation-free form."""
    compact = BRAND_RX.sub("", domain)
    matches = []
    for lead, tokens in index:
        if any(token in domain or token in compact for token in tokens):
            matches.append(lead)
    return matches


def _fetch_feeds(ctx, sources):
    """Download every source -> (candidates, fetched, failed) in feed order."""
    candidates, seen = [], set()
    fetched, failed = 0, []
    for url in sources:
        try:
            body = net.text(url, timeout=90)
        except net.NetError as e:
            ctx.warn("feed fetch failed (%s): %s" % (url, e))
            failed.append(url)
            continue
        except Exception as e:              # noqa: BLE001 - one feed only
            ctx.warn("feed fetch failed (%s): %s" % (url, e))
            failed.append(url)
            continue
        domains = parse_feed(body)
        fetched += len(domains)
        ctx.log("parsed %d entries from %s" % (len(domains), url.rsplit("/", 1)[-1]))
        for domain in domains:
            if domain in seen:
                continue
            seen.add(domain)
            candidates.append((domain, url))
    return candidates, fetched, failed


def run(ctx):
    try:
        sources = ctx.list_param("sources", SOURCES)
        limit = ctx.int_param("limit", 2000)
        scan_limit = ctx.int_param("scan_limit", 500)
        if limit <= 0:
            limit = 2000
        if scan_limit < 0:
            scan_limit = 0

        if not sources:
            return ctx.summary(ok=False, fetched=0, new=0, impersonations=0,
                               error="No phishing feed sources are configured.")
        candidates, fetched, failed = _fetch_feeds(ctx, sources)
        if len(failed) == len(sources):
            return ctx.summary(ok=False, fetched=0, new=0, impersonations=0,
                               error="The phishing feeds could not be downloaded: %s"
                                     % failed[0])
        if not candidates:
            ctx.warn("no phishing entries could be read from %d source(s)" % len(sources))
            return ctx.summary(ok=False, fetched=fetched, new=0, impersonations=0,
                               error="None of the phishing feeds returned any entries.")

        new = impersonations = scanned = 0
        brands = None
        for domain, url in candidates:
            if new >= limit:
                break
            if db.one("SELECT 1 AS hit FROM signals WHERE kind='phishing' AND domain=? LIMIT 1",
                      (domain,)):
                continue                    # already known: a daily run only adds news

            if brands is None and scan_limit > 0:
                brands = load_brand_index()
                ctx.log("%d leads carry a brand token to match against" % len(brands))

            title = ""
            payload = {}
            if brands and scanned < scan_limit:
                scanned += 1
                for lead in match_impersonations(domain, brands):
                    ctx.flag_lead(lead, "phishing-impersonation", {"domain": domain})
                    impersonations += 1
                    if not title:
                        title = (lead.get("company") or "")[:300]
            if title:
                payload["title"] = title

            if ctx.add_signal("phishing", domain=domain, source="phishing.database",
                              source_url=url, title=title, payload=payload):
                new += 1

        ctx.log("%d entries fetched, %d new phishing domains, %d impersonation flags"
                % (fetched, new, impersonations))
        return ctx.summary(ok=True, fetched=fetched, new=new,
                           impersonations=impersonations)
    except net.NetError as e:
        ctx.warn("phishing feeds unavailable: %s" % e)
        return ctx.summary(ok=False, error=str(e), fetched=0, new=0, impersonations=0)
    except Exception as e:                  # noqa: BLE001 - a job must never raise
        ctx.warn("phishing watch failed: %s" % e)
        return ctx.summary(ok=False, error=str(e), fetched=0, new=0, impersonations=0)


JOBS = [Job(
    key="phishing_watch",
    name="Fraud watch: phishing domains",
    group="Signals",
    description="Pulls the public Phishing.Database feeds, stores each new hostile domain as a "
                "signal and flags leads whose brand is being impersonated.",
    run=run,
    schedule="daily 05:30",
    params={"sources": [DOMAINS_URL, LINKS_URL], "limit": 2000, "scan_limit": 500},
)]
