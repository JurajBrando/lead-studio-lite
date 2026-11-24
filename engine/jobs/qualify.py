"""Qualify leads against the buying-signal catalogue - one request per domain, no LLM.

The problem this job exists for: the database holds millions of contacts and almost
none of them are worth an email. Everything upstream of here (Common Crawl, OSM, the
contact finder, bought lists) optimises for volume; nothing until now decided whether
a row deserves to be written to.

What one run does, per domain and not per lead - a domain is read once however many
contacts sit behind it:

    read the home page (+ a few obvious pages)
      -> disqualify the parked, dead and contentless ones outright
      -> score the rest against the confirmed cohorts in `engine/qualify.py`
      -> write the verdict onto `domains`, one `qualified` signal, and a tag per lead
      -> park the disqualified ones so no sequence can ever reach them

Nothing is deleted. A disqualified lead gets `status='unqualified'`, which is simply
not `new`, so every existing filter, export and enrollment query skips it. Set the
status back and the lead is in play again.

Robots.txt is honoured, one request at a time, with a pause - these are other people's
servers and we are reading a lot of them.
"""
import csv
import os
import time

from .. import config, crawl, db, net, qualify
from .base import Job

COLUMNS = ["verdict", "score", "cohort", "offer", "reason", "evidence", "domain", "company",
           "email", "first name", "title", "signals", "pages read"]

# Pages worth one extra request when qualifying: what they sell, what it costs, who they
# are hiring. `crawl.contact_links` already finds contact/about; these add the rest.
EXTRA_PAGE_RX = ("pricing", "cennik", "cenik", "services", "sluzby", "produkty", "products",
                 "career", "careers", "kariera", "praca", "prace", "jobs", "shop", "eshop",
                 "booking", "rezervacia", "objednat", "about", "contact", "kontakt")


def _folder():
    path = os.path.join(config.DATA_DIR, "qualified")
    os.makedirs(path, exist_ok=True)
    return path


def _extra_links(start, home, limit):
    """Same-site URLs whose path looks like one of the pages above, best first."""
    if limit <= 0:
        return []
    found = crawl.contact_links(start, home, limit=limit * 3)
    ranked = []
    for url in found:
        path = url.lower()
        hit = next((i for i, word in enumerate(EXTRA_PAGE_RX) if word in path), None)
        ranked.append((hit if hit is not None else 99, url))
    ranked.sort(key=lambda item: (item[0], len(item[1])))
    return [url for _, url in ranked[:limit]]


def read_site(domain, max_pages=2, pause=0.6, respect_robots=True):
    """Fetch the pages one scan needs. Returns (Site, error)."""
    domain = net.domain_of(domain) or (domain or "").strip().lower()
    if not domain or "." not in domain:
        return None, "not a domain"
    start = "https://" + domain
    if respect_robots and not crawl.robots_allowed(start):
        return None, "robots.txt disallows crawling"
    try:
        home = crawl.fetch_page(start)
    except net.NetError:
        start = "http://" + domain
        try:
            home = crawl.fetch_page(start)
        except net.NetError as exc:
            return None, str(exc)[:120]

    pages = [(start, home)]
    for url in _extra_links(start, home, max(0, int(max_pages) - 1)):
        if respect_robots and not crawl.robots_allowed(url):
            continue
        time.sleep(max(0.0, pause))
        try:
            pages.append((url, crawl.fetch_page(url)))
        except net.NetError:
            continue

    blob = "\n".join(html for _, html in pages)
    site = qualify.site_from_pages(
        domain, pages, title=crawl.page_title(home), description=crawl.page_description(home),
        emails=crawl.decode_emails_in_html(blob))
    return site, ""


def _candidates(limit, recheck_ts, only_status=""):
    """Leads with a domain nobody has judged yet (or judged long enough ago)."""
    where = ["l.domain <> ''"]
    args = []
    if only_status:
        where.append("l.status = ?")
        args.append(only_status)
    where.append("(d.domain IS NULL OR d.data NOT LIKE '%\"qualified_ts\"%' OR d.updated_ts < ?)")
    args.append(int(recheck_ts))
    sql = ("SELECT l.* FROM leads l LEFT JOIN domains d ON d.domain = l.domain "
           "WHERE %s ORDER BY l.id DESC LIMIT ?" % " AND ".join(where))
    return db.rows(sql, tuple(args) + (int(limit),))


def _group_by_domain(leads):
    groups = {}
    for lead in leads:
        key = (lead.get("domain") or "").strip().lower()
        if key:
            groups.setdefault(key, []).append(lead)
    return groups


def _apply_to_lead(lead, answer, tag_leads, park):
    """Write the verdict onto one lead: a tag, a note, and a status when it is out."""
    patch = {"updated_ts": db.now()}
    if tag_leads:
        tags = [t.strip() for t in (lead.get("tags") or "").split(",") if t.strip()]
        tags = [t for t in tags if not t.startswith(("q:", "cohort:"))]
        tags.append("q:" + answer["verdict"])
        if answer["cohort"]:
            tags.append("cohort:" + answer["cohort"])
        patch["tags"] = ",".join(tags)[:300]
    line = "[qualify] %s %d/%s %s" % (answer["verdict"], answer["score"],
                                      answer["cohort"] or "-",
                                      answer["reason"] or (answer["disqualified"] or [""])[0])
    notes = "\n".join(l for l in (lead.get("notes") or "").splitlines()
                      if not l.startswith("[qualify] "))
    patch["notes"] = (notes + "\n" + line).strip()[:4000]
    if park and answer["verdict"] == qualify.OUT and (lead.get("status") or "new") == "new":
        patch["status"] = "unqualified"
    db.update("leads", lead["id"], patch)


def run(ctx):
    cohorts = ctx.list_param("cohorts", []) or qualify.confirmed_cohorts()
    cohorts = [c for c in cohorts if c in qualify.COHORTS]
    limit = ctx.int_param("limit", 200)
    max_pages = max(1, ctx.int_param("max_pages", 2))
    pause = float(ctx.param("pause", 0.6))
    recheck_days = ctx.int_param("recheck_days", 90)
    park = bool(ctx.param("park_disqualified", True))
    tag_leads = bool(ctx.param("tag_leads", True))
    respect_robots = bool(ctx.param("respect_robots", True))
    only_status = str(ctx.param("only_status", "new") or "")

    recheck_ts = db.now() - max(1, recheck_days) * 86400
    leads = _candidates(limit * 4, recheck_ts, only_status)
    groups = _group_by_domain(leads)
    domains = list(groups)[:limit]
    if not domains:
        return ctx.summary(checked=0, note="Every lead with a domain has been qualified.")

    ctx.log("qualifying %d domains (%d leads) against: %s"
            % (len(domains), sum(len(groups[d]) for d in domains), ", ".join(cohorts)))
    rows = []
    counts = {qualify.HOT: 0, qualify.WARM: 0, qualify.COLD: 0, qualify.OUT: 0, "unreachable": 0}

    for index, domain in enumerate(domains, 1):
        ctx.check_cancel()
        ctx.progress(index, len(domains), "reading %s" % domain)
        site, error = read_site(domain, max_pages=max_pages, pause=pause,
                                respect_robots=respect_robots)
        if site is None:
            counts["unreachable"] += 1
            ctx.upsert_domain(domain, data={"qualified_ts": db.now(), "qualify": {
                "verdict": qualify.OUT, "score": 0, "signals": [],
                "disqualified": ["unreachable"], "reason": error}})
            for lead in groups[domain]:
                _apply_to_lead(lead, {"verdict": qualify.OUT, "score": 0, "cohort": "",
                                      "reason": error or "unreachable",
                                      "disqualified": ["unreachable"]}, tag_leads, park)
            time.sleep(max(0.0, pause))
            continue

        answer = qualify.scan(site, cohorts)
        counts[answer["verdict"]] = counts.get(answer["verdict"], 0) + 1
        ctx.upsert_domain(domain, score=int(answer["score"]), data={
            "qualified_ts": db.now(),
            "qualify": {"verdict": answer["verdict"], "score": answer["score"],
                        "cohort": answer["cohort"], "offer": answer["offer"],
                        "signals": answer["signals"], "disqualified": answer["disqualified"],
                        "reason": answer["reason"]}})

        if answer["verdict"] in (qualify.HOT, qualify.WARM):
            ctx.add_signal("qualified", domain=domain,
                           company=(groups[domain][0].get("company") or ""),
                           title="%s: %s" % (answer["verdict"].upper(), answer["reason"]),
                           source="qualify", source_url="https://" + domain,
                           payload={"score": answer["score"], "cohort": answer["cohort"],
                                    "offer": answer["offer"], "signals": answer["signals"]})

        for lead in groups[domain]:
            _apply_to_lead(lead, answer, tag_leads, park)
            if answer["verdict"] in (qualify.HOT, qualify.WARM):
                rows.append({
                    "verdict": answer["verdict"], "score": answer["score"],
                    "cohort": answer["cohort"], "offer": answer["offer"],
                    "reason": answer["reason"], "evidence": answer["evidence"],
                    "domain": domain, "company": lead.get("company") or "",
                    "email": (lead.get("email") or "").lower(),
                    "first name": lead.get("first_name") or "",
                    "title": lead.get("title") or "",
                    "signals": " ".join(answer["signals"]),
                    "pages read": len(site.pages)})
        time.sleep(max(0.0, pause))

    path = ""
    if rows and bool(ctx.param("write_csv", True)):
        order = {qualify.HOT: 0, qualify.WARM: 1}
        rows.sort(key=lambda row: (order.get(row["verdict"], 5), -int(row["score"]),
                                   0 if row["email"] else 1, row["domain"]))
        path = os.path.join(_folder(), "qualified_%s.csv" % time.strftime("%Y-%m-%d"))
        with open(path, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=COLUMNS)
            writer.writeheader()
            writer.writerows(rows)

    ctx.log("%d domains: %d hot, %d warm, %d cold, %d out, %d unreachable"
            % (len(domains), counts[qualify.HOT], counts[qualify.WARM], counts[qualify.COLD],
               counts[qualify.OUT], counts["unreachable"]))
    if path:
        ctx.log("sendable rows -> %s" % path)
    return ctx.summary(checked=len(domains), hot=counts[qualify.HOT], warm=counts[qualify.WARM],
                       cold=counts[qualify.COLD], disqualified=counts[qualify.OUT],
                       unreachable=counts["unreachable"], sendable=len(rows), file=path)


JOBS = [Job(
    key="qualify",
    name="Qualify leads (buying signals)",
    group="Signals",
    description="Reads each lead's website once and checks it against the buying-signal "
                "catalogue, one cohort per offer: trades quoting by hand (QuoteIQ + Ataraxis), "
                "clinics with no online booking (Dermis), ads with no attribution (HYROS), "
                "agencies with no follow-up system (HighLevel, HYROS AIR), US companies at "
                "referral scale (micro1), own-brand ecommerce (Red Points) and manufacturers "
                "(Modulario). No AI and no API keys, so it is free to run over the whole "
                "database. Parked, dead and contentless sites are marked 'unqualified' so no "
                "sequence can reach them; the rest get a score, a cohort, the offer to pitch "
                "and a row in qualified_<date>.csv.",
    run=run,
    schedule="daily 05:30",
    params={"limit": 200, "max_pages": 2, "pause": 0.6, "cohorts": "", "recheck_days": 90,
            "park_disqualified": True, "tag_leads": True, "respect_robots": True,
            "only_status": "new", "write_csv": True},
)]
