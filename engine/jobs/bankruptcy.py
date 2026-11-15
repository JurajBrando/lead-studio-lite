"""Bankruptcy / insolvency watch.

Three providers, because no single free source covers everything:

  sec       US public companies filing an 8-K with Item 1.03 ("Bankruptcy or
            Receivership"), via SEC EDGAR full-text search. Structured JSON, no
            key, and the filing itself is linked. This is the default.

  gazette   The Gazette (UK official public record) insolvency notices as an
            Atom feed - companies entering administration, liquidation,
            receivership. Free, no key.

  omni      The recent-filings table at omniagentsolutions.com, which has the
            richest columns (debtor, district, attorney, asset and liability
            bands) but sits behind bot protection that blocks scripted clients
            from most networks. Kept as an option: if it works from your
            connection, you get the best data of the three.

Two uses in an SDR workflow:
  * a prospect that filed is no longer a prospect - flag it on the lead,
  * a competitor's distress is a buying signal for whoever serves that market.

Every row is a public record. Treat a filing as a fact, not a pitch: never email
a debtor about their own bankruptcy.
"""
import datetime as dt
import re

from .. import db, net
from .base import Job, Context

SEC_URL = ("https://efts.sec.gov/LATEST/search-index?q=%22Item%201.03%22&forms=8-K"
           "&dateRange=custom&startdt={start}&enddt={end}")
GAZETTE_URL = ("https://www.thegazette.co.uk/all-notices/notice/data.feed"
               "?categorycode=insolvency&results-page-size=100")
FILINGS_URL = "https://omniagentsolutions.com/filings"
AMOUNT_RX = re.compile(r"\$?\s*([0-9]+(?:\.[0-9]+)?)\s*([kKmMbB]?)")


def band_low(value):
    """'$1M-$10M' -> 1000000, '$500k-$1M' -> 500000, junk -> 0."""
    text_value = (value or "").strip()
    if not text_value or text_value.lower().startswith("unknown"):
        return 0
    match = AMOUNT_RX.search(text_value)
    if not match:
        return 0
    try:
        number = float(match.group(1))
    except ValueError:
        return 0
    unit = (match.group(2) or "").lower()
    return int(number * {"k": 1e3, "m": 1e6, "b": 1e9}.get(unit, 1))


# ----------------------------------------------------------------- SEC EDGAR
def parse_sec_filings(payload):
    """EDGAR full-text-search JSON -> filing rows (only real Item 1.03 filings)."""
    rows = []
    hits = ((payload or {}).get("hits") or {}).get("hits") or []
    for hit in hits:
        src = hit.get("_source") or {}
        items = [str(i) for i in (src.get("items") or [])]
        if "1.03" not in items:
            continue
        names = src.get("display_names") or []
        raw = str(names[0]).strip() if names else ""
        company = re.sub(r"\s*\(.*\)\s*$", "", raw).strip()
        if not company:
            continue
        cik_match = re.search(r"CIK\s*(\d+)", raw)
        ciks = src.get("ciks") or []
        cik = cik_match.group(1) if cik_match else (str(ciks[0]).lstrip("0") if ciks else "")
        accession = str(src.get("adsh") or "")
        filename = str(hit.get("_id") or "").split(":")[-1]
        if cik and accession and filename:
            url = "https://www.sec.gov/Archives/edgar/data/%s/%s/%s" % (
                cik.lstrip("0") or cik, accession.replace("-", ""), filename)
        else:
            url = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=%s&type=8-K" % cik
        rows.append({"company": company, "filed": str(src.get("file_date") or ""),
                     "case_no": accession, "cik": cik,
                     "items": items, "location": ", ".join(src.get("biz_locations") or []),
                     "state": ", ".join(src.get("biz_states") or []), "url": url,
                     "form": src.get("form") or "8-K", "provider": "sec"})
    return rows


# ------------------------------------------------------------------- Gazette
def parse_gazette(xml_text):
    rows = []
    for entry in net.atom_entries(xml_text or ""):
        title = entry.get("title") or ""
        if not title:
            continue
        rows.append({"company": re.sub(r"\s*[-–]\s*.*$", "", title).strip()[:200],
                     "title": title[:300], "filed": (entry.get("published") or "")[:10],
                     "url": entry.get("link") or "https://www.thegazette.co.uk/insolvency",
                     "provider": "gazette"})
    return rows


# ------------------------------------------------------- omni filings table
def parse_filings(html_text):
    """Return the filing rows from the omniagentsolutions rendered table."""
    rows = []
    for table in net.html_tables(html_text):
        header = None
        for row in table:
            joined = " ".join(row).lower()
            if "debtor" in joined and "filed" in joined and len(row) >= 4:
                header = [c.strip().lower() for c in row]
                continue
            if not header or len(row) < 8:
                continue
            cells = [c.strip() for c in row[:8]]
            state, district, filed, debtor, case_no, attorney, assets, liabilities = cells
            if not filed or not debtor:
                continue
            if not re.match(r"\d{1,2}/\d{1,2}/\d{4}", filed):
                continue
            rows.append({"state": state, "district": district, "filed": filed,
                         "debtor": debtor, "case_no": case_no, "attorney": attorney,
                         "assets": assets, "liabilities": liabilities,
                         "liabilities_low": band_low(liabilities),
                         "assets_low": band_low(assets), "company": debtor,
                         "provider": "omni"})
    return rows


# ----------------------------------------------------------------------- run
def _fetch_sec(ctx, days):
    today = dt.date.today()
    start = today - dt.timedelta(days=max(1, days))
    url = SEC_URL.format(start=start.isoformat(), end=today.isoformat())
    payload = net.json_body(url, timeout=60)
    ctx.log("SEC EDGAR: %d filings matched the phrase" % (
        ((payload or {}).get("hits") or {}).get("total", {}).get("value", 0)
        if isinstance(((payload or {}).get("hits") or {}).get("total"), dict)
        else len(((payload or {}).get("hits") or {}).get("hits") or [])))
    return url, parse_sec_filings(payload)


def _fetch_gazette(ctx, days):
    text_value = net.text(GAZETTE_URL, timeout=60)
    rows = parse_gazette(text_value)
    ctx.log("Gazette: %d insolvency notices in the feed" % len(rows))
    return GAZETTE_URL, rows


def _fetch_omni(ctx, days):
    page = net.text(FILINGS_URL, timeout=60)
    rows = parse_filings(page)
    ctx.log("omni filings: %d rows on the first page" % len(rows))
    return FILINGS_URL, rows


def run(ctx):
    provider = str(ctx.param("provider", "sec")).strip().lower()
    days = ctx.int_param("days", 7)
    limit = ctx.int_param("limit", 300)
    keywords = [k.lower() for k in ctx.list_param("keywords", [])]
    match_leads = bool(ctx.param("match_leads", True))
    min_liab = ctx.int_param("min_liabilities", 0)

    fetchers = {"sec": _fetch_sec, "gazette": _fetch_gazette, "omni": _fetch_omni}
    fetch = fetchers.get(provider)
    if not fetch:
        return {"ok": False, "error": "Unknown provider %r (use sec, gazette or omni)." % provider}

    try:
        source_url, rows = fetch(ctx, days)
    except net.NetError as e:
        hint = ""
        if provider == "omni":
            hint = (" The omni filings page blocks scripted clients from many networks - "
                    "try provider 'sec' or 'gazette'.")
        return {"ok": False, "error": "Could not read the %s source: %s.%s" % (provider, e, hint)}

    if not rows:
        return {"ok": True, "parsed": 0, "kept": 0, "new": 0, "matched_leads": 0,
                "provider": provider, "note": "No filings in that window."}

    kept, new, flagged = [], 0, 0
    for row in rows:
        company = row.get("company") or ""
        if keywords and not any(k in company.lower() for k in keywords):
            continue
        if min_liab and row.get("liabilities_low", 0) < min_liab:
            continue
        kept.append(row)

    for row in kept[:limit]:
        company = row.get("company") or ""
        source = "%s:%s" % (provider, row.get("case_no") or row.get("url") or company)
        payload = {k: v for k, v in row.items() if k not in ("company",)}
        payload["provider"] = provider
        created = ctx.add_signal(
            "bankruptcy", company=company,
            title=(row.get("title") or row.get("case_no") or row.get("filed") or ""),
            source=provider, source_url=row.get("url") or source_url, payload=payload)
        if not created:
            continue
        new += 1
        if match_leads:
            lead = ctx.find_lead(company=company)
            if lead:
                ctx.flag_lead(lead, "bankruptcy-filing", {
                    "provider": provider, "filed": row.get("filed"),
                    "case": row.get("case_no"), "url": row.get("url")})
                flagged += 1
    ctx.log("%s: %d rows, kept %d, new %d, matched leads %d" % (
        provider, len(rows), len(kept), new, flagged))
    return ctx.summary(provider=provider, parsed=len(rows), kept=len(kept), new=new,
                       matched_leads=flagged, source=source_url)


JOBS = [Job(
    key="bankruptcy_watch",
    name="Bankruptcy & insolvency watch",
    group="Signals",
    description="US 8-K Item 1.03 filings from SEC EDGAR (or UK Gazette insolvency notices, or the "
                "omni filings table). Flags leads that just filed and surfaces distressed companies.",
    run=run,
    schedule="daily 07:00",
    params={"provider": "sec", "days": 7, "limit": 300, "keywords": [], "min_liabilities": 0,
            "match_leads": True},
)]
