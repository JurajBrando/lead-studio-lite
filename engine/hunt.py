"""The Radar hunt: offer card -> companies worth writing to -> approved leads.

Stages (each one cheaper than the next, so the expensive ones see few companies):

  1 pull     companies the card allows: your signals first (they carry evidence), then your
             leads, then the domain catalogue. Big tables are read in id-chunks under a time
             budget, and a cursor per offer remembers where the last hunt stopped.
  2 cheap    can the domain receive mail (MX, cached 30 days on the domains table)
  3 score    radar.score -> drop hard blockers -> keep the best N by radar.rank
  4 deep     read the site (cached 14 days): what it says, the tools it runs, its addresses,
             the buying signals (qualify), then the angle a soldier writes from
  5 approve  the row becomes a lead plus a radar_leads row - the Army's territory

Approving is always a person's click. Nothing here sends mail.
"""
import json
import re
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

from . import config, crawl, daily, db, net, offers, qualify, radar, verify

HUNT_KEEP_HISTORY = 12
APPROVE_MIN_CONFIDENCE = 30
PULL_BUDGET_SECONDS = 25
CHUNK = 50000

RUN = {"running": False, "hunt_id": 0, "phase": "", "progress": 0.0, "cancel": False, "error": ""}
RUN_LOCK = threading.Lock()


# ------------------------------------------------------------------ small helpers
def _j(raw, default):
    try:
        value = json.loads(raw) if raw else default
    except (TypeError, ValueError):
        return default
    return value if isinstance(value, type(default)) else default


def kv_get(key, default=""):
    row = db.one("SELECT value FROM app_kv WHERE key=?", (key,))
    return row["value"] if row else default


def kv_set(key, value):
    db.execute("INSERT INTO app_kv(key, value) VALUES(?, ?) "
               "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


def _phase(text, progress=None):
    with RUN_LOCK:
        RUN["phase"] = text
        if progress is not None:
            RUN["progress"] = round(float(progress), 3)


def _cancelled():
    return bool(RUN.get("cancel"))


def _domain(value):
    return net.domain_of(value or "") or ""


# ------------------------------------------------------------------ offer cards
def card_for(offer_id):
    offer = offers.get(offer_id)
    if not offer:
        return None, None, None
    row = db.one("SELECT * FROM radar_cards WHERE offer_id=?", (offer_id,))
    saved = _j(row["card"], {}) if row else {}
    card = radar.card_from_offer(offer, saved or None)
    weights = _j(row["weights"], {}) if row else {}
    weights = {k: float(weights.get(k, 1.0)) for k in ("fit", "pain", "timing", "pay")}
    return offer, card, weights


def save_card(offer_id, card=None, weights=None):
    offer = offers.get(offer_id)
    if not offer:
        return {"ok": False, "error": "Pick the offer first."}
    _, current, current_w = card_for(offer_id)
    # The dialog sends the form fields only; keep what the Assistant read saved beside them
    # (its profile, the team, the rivals and the targeting it left for the hunt).
    merged = dict(current or {})
    if isinstance(card, dict):
        merged.update(card)
    card = radar.normalise_card(merged)
    weights = weights if isinstance(weights, dict) else current_w
    weights = {k: max(0.0, min(2.0, float(weights.get(k, 1.0) or 0))) for k in ("fit", "pain", "timing", "pay")}
    db.execute("INSERT INTO radar_cards(offer_id, card, weights, updated_ts) VALUES(?,?,?,?) "
               "ON CONFLICT(offer_id) DO UPDATE SET card=excluded.card, weights=excluded.weights, "
               "updated_ts=excluded.updated_ts",
               (offer_id, json.dumps(card), json.dumps(weights), db.now()))
    return {"ok": True, "card": card, "weights": weights}


def offer_from_text(text, name="", affiliate_url="", category=""):
    """Paste an offer -> a new offer in the Toolbox with its card saved beside it."""
    text = (text or "").strip()
    if len(text) < 20:
        return {"ok": False, "error": "Paste the offer first - a paragraph is enough."}
    card = radar.parse_offer(text)
    if not name:
        first = text.split("\n")[0].split(".")[0].strip()
        name = first[:80] or "New offer"
    res = offers.save({"name": name, "description": text[:2000], "affiliate_url": affiliate_url,
                       "category": category or card.get("category", ""),
                       "keywords": ", ".join(card.get("keywords", [])[:15]), "active": True})
    if not res.get("ok"):
        return res
    own = name.lower()
    card["complements"] = [c for c in card.get("complements", []) if c.lower() not in own]
    card["competitors"] = [c for c in card.get("competitors", []) if c.lower() not in own]
    own_words = set(re.findall(r"[a-z0-9]+", own))
    for rival in card.get("competitors", []):
        own_words |= set(re.findall(r"[a-z0-9]+", rival.lower()))
    card["keywords"] = [k for k in card.get("keywords", []) if k.lower() not in own_words]
    save_card(res["id"], card)
    return {"ok": True, "offer_id": res["id"], "card": radar.normalise_card(card)}


# ------------------------------------------------------------------ the company universe
SIGNAL_SCAN = 30000
LEAD_COLS = ("id, email, first_name, last_name, company, title, domain, website, city, country, "
             "industry, company_size, technologies, description, tags, notes, status")


def _company_from_lead(lead):
    domain = _domain(lead.get("domain") or lead.get("website") or "")
    if not domain and "@" in (lead.get("email") or ""):
        edom = lead["email"].split("@", 1)[1].lower()
        if edom not in radar.FREE_MAIL:
            domain = edom
    return {"domain": domain, "name": (lead.get("company") or "").strip() or domain,
            "industry": lead.get("industry") or "", "size": radar.size_label(lead.get("company_size")),
            "country": lead.get("country") or "", "city": lead.get("city") or "",
            "technologies": lead.get("technologies") or "", "tags": lead.get("tags") or "",
            "notes": lead.get("notes") or "", "description": (lead.get("description") or "")[:1500],
            "email": lead.get("email") or "", "first_name": lead.get("first_name") or "",
            "last_name": lead.get("last_name") or "", "title": lead.get("title") or "",
            "lead_id": lead.get("id") or 0, "lead_status": lead.get("status") or "new",
            "is_lead": True, "tranco": 0, "evidence": []}


def signal_companies(days=180, limit=SIGNAL_SCAN):
    """Companies your daily feeds found, each with its findings as evidence."""
    cutoff = db.now() - days * 86400
    rows = db.rows("SELECT kind, domain, company, title, source, source_url, payload, ts "
                   "FROM signals ORDER BY id DESC LIMIT ?", (limit,))
    by_domain = {}
    for row in rows:
        if int(row.get("ts") or 0) < cutoff:
            continue
        domain = _domain(row.get("domain"))
        if not domain:
            continue
        company = by_domain.get(domain)
        if not company:
            company = {"domain": domain, "name": (row.get("company") or "").strip() or domain,
                       "industry": "", "size": "", "country": "", "city": "", "technologies": "",
                       "tags": "", "notes": "", "description": "", "email": "", "is_lead": False,
                       "tranco": 0, "evidence": [], "lead_id": 0}
            by_domain[domain] = company
        if len(company["evidence"]) < 40:
            company["evidence"].append({
                "kind": radar.kind_of(row.get("kind")), "title": row.get("title") or "",
                "source": row.get("source") or "", "url": row.get("source_url") or "",
                "ts": int(row.get("ts") or 0), "payload": _j(row.get("payload"), {})})
    return by_domain


def _merge_lead(company, lead_row):
    lead_c = _company_from_lead(lead_row)
    for key, value in lead_c.items():
        if key == "evidence":
            continue
        if value and not company.get(key):
            company[key] = value
    company["is_lead"] = True
    return company


def _skip_domains():
    return {r["value"].lower(): "on the do-not-contact list"
            for r in db.rows("SELECT value FROM army_suppress WHERE kind='domain'")}


def _contacted(lead_status):
    return (lead_status or "new") not in ("", "new")


def _like_clause(columns, terms):
    parts, args = [], []
    for term in terms[:12]:
        for col in columns:
            parts.append("%s LIKE ?" % col)
            args.append("%" + term + "%")
    return ("(" + " OR ".join(parts) + ")") if parts else "1=1", args


def _scan_leads(card, want, skip, seen, offer_id, budget):
    """Leads that pass the card, read in id-chunks so a 30 GB table never blocks the app."""
    include = radar.include_terms(card)
    clause, args = _like_clause(("industry", "company", "description", "tags", "title"), include)
    countries = radar.card_countries(card)
    top = db.scalar("SELECT MAX(id) FROM leads", default=0) or 0
    if not top:
        return [], 0
    cursor_key = "radar_cursor_leads_%d" % offer_id
    start = int(kv_get(cursor_key, "0") or 0)
    if start >= top:
        start = 0
    pos, wrapped, out = start, False, []
    deadline = time.time() + budget
    while len(out) < want and time.time() < deadline and not _cancelled():
        hi = pos + CHUNK
        rows = db.rows("SELECT " + LEAD_COLS + " FROM leads WHERE id>? AND id<=? AND status='new' "
                       "AND (domain<>'' OR website<>'' OR email<>'') AND " + clause + " LIMIT ?",
                       (pos, hi) + tuple(args) + (want - len(out) + 50,))
        for lead in rows:
            company = _company_from_lead(lead)
            d = company["domain"]
            if not d or d in seen or d in skip:
                continue
            if countries and radar.country_of(company) not in countries:
                continue
            seen.add(d)
            out.append(company)
        pos = hi
        if pos >= top:
            if wrapped or start == 0:
                break
            pos, wrapped = 0, True
        if wrapped and pos >= start:
            break
    kv_set(cursor_key, pos if pos < top else 0)
    return out, pos


def _scan_domains(card, want, skip, seen, offer_id, budget):
    include = radar.include_terms(card)
    if not include:
        return []
    clause, args = _like_clause(("domain", "name", "data"), include)
    top = db.scalar("SELECT MAX(rowid) FROM domains", default=0) or 0
    if not top:
        return []
    cursor_key = "radar_cursor_domains_%d" % offer_id
    pos = int(kv_get(cursor_key, "0") or 0)
    if pos >= top:
        pos = 0
    out, deadline = [], time.time() + budget
    while len(out) < want and time.time() < deadline and pos < top and not _cancelled():
        hi = pos + CHUNK
        rows = db.rows("SELECT rowid AS rid, domain, name, data, first_seen_ts FROM domains "
                       "WHERE rowid>? AND rowid<=? AND " + clause + " LIMIT ?",
                       (pos, hi) + tuple(args) + (want - len(out) + 50,))
        for row in rows:
            d = _domain(row["domain"])
            if not d or d in seen or d in skip:
                continue
            data = _j(row.get("data"), {})
            company = {"domain": d, "name": row.get("name") or d, "industry": data.get("industry", ""),
                       "size": "", "country": "", "city": "", "technologies": ", ".join(data.get("dns_tools", []) or []),
                       "tags": "", "notes": "", "description": str(data.get("description") or data.get("title") or "")[:600],
                       "email": "", "is_lead": False, "tranco": int(data.get("tranco_rank") or 0),
                       "evidence": [], "lead_id": 0}
            seen.add(d)
            out.append(company)
        pos = hi
    kv_set(cursor_key, pos if pos < top else 0)
    return out


def pull(hunt, card, offer_id):
    countries = radar.card_countries(card)
    include = radar.include_terms(card)
    exclude = radar.exclude_terms(card)
    limit = int(hunt["params"].get("pull", radar.DEFAULT_PULL))
    skip = _skip_domains()
    out, seen = [], set()
    sources = {"signals": 0, "leads": 0, "catalogue": 0}

    def admit(company, origin):
        reason = radar.pull_block(company, card, countries, include, exclude)
        if reason:
            _drop(hunt, reason)
            return False
        company["origin"] = origin
        out.append(company)
        sources[origin] += 1
        return True

    _phase("reading your signals", 0.03)
    for domain, company in signal_companies().items():
        if len(out) >= limit:
            break
        if domain in skip:
            _drop(hunt, skip[domain])
            continue
        lead = db.one("SELECT " + LEAD_COLS + " FROM leads WHERE domain=? ORDER BY "
                      "(email<>'') DESC LIMIT 1", (domain,))
        if lead:
            if _contacted(lead.get("status")):
                _drop(hunt, "already contacted")
                continue
            _merge_lead(company, lead)
        seen.add(domain)
        admit(company, "signals")
    if len(out) < limit and not _cancelled():
        _phase("searching your leads", 0.06)
        rows, _ = _scan_leads(card, limit - len(out), skip, seen, offer_id, PULL_BUDGET_SECONDS)
        for company in rows:
            if len(out) >= limit:
                break
            admit(company, "leads")
    if len(out) < limit and not _cancelled():
        _phase("searching the domain catalogue", 0.09)
        for company in _scan_domains(card, limit - len(out), skip, seen, offer_id, PULL_BUDGET_SECONDS // 2):
            if len(out) >= limit:
                break
            admit(company, "catalogue")
    hunt["sources"] = sources
    return out


# ------------------------------------------------------------------ stage 2: can it receive mail
def _domain_row(domain):
    return db.one("SELECT domain, mx_status, mx_host, mx_checked_ts, data FROM domains WHERE domain=?",
                  (domain,))


def _ensure_domain(domain, name=""):
    db.execute("INSERT OR IGNORE INTO domains(domain, name, source, tld, first_seen_ts, updated_ts) "
               "VALUES(?,?,?,?,?,?)", (domain, name, "radar", domain.rsplit(".", 1)[-1], db.now(), db.now()))


def check_mx(company, now):
    domain = company["domain"]
    row = _domain_row(domain)
    if row and row.get("mx_status") and now - int(row.get("mx_checked_ts") or 0) < radar.TTL_DNS:
        company["mx"] = row["mx_status"] == "ok"
        company["mx_host"] = row.get("mx_host") or ""
        company["cached_dns"] = True
        return company
    try:
        hosts, source = verify.mx_lookup(domain)
    except Exception:                                   # a resolver crash must not stop the hunt
        hosts, source = [], "none"
    company["mx"] = bool(hosts)
    company["mx_host"] = hosts[0] if hosts else ""
    company["live"] = bool(hosts) or source == "a_record"
    _ensure_domain(domain, company.get("name", ""))
    db.execute("UPDATE domains SET mx_status=?, mx_host=?, mx_checked_ts=?, updated_ts=? WHERE domain=?",
               ("ok" if hosts else ("no-mx" if source in ("dnspython", "nslookup", "a_record") else "error"),
                company["mx_host"], now, now, domain))
    return company


def _parallel(items, worker, width, label, lo, hi):
    total = len(items)
    if not total:
        return
    done = [0]
    lock = threading.Lock()

    def run(item):
        if _cancelled():
            return
        try:
            worker(item)
        except Exception as exc:                        # one bad site never ends the hunt
            config.log("hunt %s failed for %s: %s" % (label, item.get("domain"), exc))
        with lock:
            done[0] += 1
            _phase("%s %d / %d" % (label, done[0], total), lo + (hi - lo) * done[0] / float(total))
    with ThreadPoolExecutor(max_workers=width) as pool:
        list(pool.map(run, items))


# ------------------------------------------------------------------ stage 4: read the site
def read_site(domain, now, force=False):
    """What the site says, the tools it runs, its addresses and its buying signals. Cached."""
    row = _domain_row(domain)
    data = _j(row.get("data") if row else "{}", {})
    cached = data.get("radar_site")
    if cached and not force and now - int(cached.get("ts") or 0) < radar.TTL_CRAWL:
        return cached
    site = {"ts": now, "ok": False, "title": "", "description": "", "text": "", "tools": [],
            "emails": [], "tracking": [], "qualify": {}, "pages": []}
    pages = []
    start = "https://" + domain
    try:
        home = crawl.fetch_page(start)
    except net.NetError:
        try:
            start = "http://" + domain
            home = crawl.fetch_page(start)
        except net.NetError as exc:
            site["error"] = str(exc)[:200]
            home = ""
    if home:
        pages.append((start, home))
        for url in crawl.contact_links(start, home, limit=3):
            try:
                pages.append((url, crawl.fetch_page(url)))
            except net.NetError:
                continue
    if pages:
        html = "\n".join(body for _, body in pages)
        emails = []
        for _, body in pages:
            emails.extend(crawl.decode_emails_in_html(body))
        site["title"] = crawl.page_title(home)[:200]
        site["description"] = crawl.page_description(home)[:400]
        text = net.strip_tags(home)
        site["text"] = text[:1800]
        site["tools"] = radar.tools_in(html.lower())
        site["tracking"] = radar.tracking_ids(html)
        site["emails"] = crawl.rank_emails(emails, domain)[:6]
        site["pages"] = [url for url, _ in pages]
        try:
            q = qualify.scan(qualify.site_from_pages(domain, pages, site["title"], site["description"],
                                                     site["emails"]))
            site["qualify"] = {"verdict": q.get("verdict"), "score": q.get("score"), "reason": q.get("reason"),
                               "cohort": q.get("cohort"), "signals": q.get("signals", [])[:12],
                               "disqualified": q.get("disqualified", [])}
        except Exception as exc:
            site["qualify"] = {"error": str(exc)[:120]}
        site["ok"] = True
    _ensure_domain(domain)
    data["radar_site"] = site
    db.execute("UPDATE domains SET data=?, updated_ts=? WHERE domain=?",
               (json.dumps(data)[:60000], now, domain))
    return site


def apply_site(company, site, now):
    """Fold what the site showed into the company, so the final score sees it."""
    if not site or not site.get("ok"):
        return company
    if site.get("description") and len(company.get("description") or "") < 40:
        company["description"] = site["description"]
    extra = " ".join([site.get("title", ""), site.get("description", ""), site.get("text", "")[:1200]])
    company["description"] = ((company.get("description") or "") + " " + extra).strip()[:2500]
    tools = [t.strip() for t in (company.get("technologies") or "").split(",") if t.strip()]
    for tool in site.get("tools", []):
        if tool not in tools:
            tools.append(tool)
    company["technologies"] = ", ".join(tools[:12])
    q = site.get("qualify") or {}
    if q.get("reason"):
        company["evidence"] = list(company.get("evidence") or []) + [
            {"kind": "profile", "title": "site: " + str(q["reason"]), "ts": now, "source": "site"}]
    return company


def find_email(company, site):
    domain = company["domain"]
    for address in (site or {}).get("emails", []):
        edom = radar.email_domain(address)
        if edom == domain or edom.endswith("." + domain):
            return address, "site", {}
    existing = (company.get("email") or "").strip()
    if existing:
        return existing, "dataset", {}
    lead = db.one("SELECT email FROM leads WHERE domain=? AND email<>'' LIMIT 1", (domain,))
    if lead:
        return lead["email"], "dataset", {}
    for address in (site or {}).get("emails", []):
        return address, "site", {}
    country = radar.country_of(company)
    guesses = radar.guesses(domain, company.get("first_name", ""), company.get("last_name", ""), country)
    if guesses:
        return guesses[0], "guess", {"mx": bool(company.get("mx"))}
    return "", "", {}


def deep(hunt, rows, card, weights, offer_name):
    now = db.now()
    sector = (card.get("industries") or [""])[0]

    def worker(company):
        site = read_site(company["domain"], now)
        apply_site(company, site, now)
        address, source, extra = find_email(company, site)
        if address:
            company["email"] = address
            company["email_source"] = source
            company["email_confidence"] = radar.confidence(address, source, company["domain"], extra)
            company["email_note"] = radar.source_label(source, extra)
        company["site"] = {k: site.get(k) for k in ("title", "description", "text", "tools", "tracking",
                                                    "qualify", "pages")} if site else {}
    _parallel(rows, worker, 8, "reading sites", 0.45, 0.95)
    for company in rows:
        scored = radar.score(company, card, weights, db.now())
        company.update({k: scored[k] for k in ("score", "fit", "pain", "timing", "pay", "why", "tools",
                                               "blockers", "pass")})
        company["rank"] = radar.rank(scored)
        company["angle"] = radar.angle(company, sector, offer_name, scored["why"])


# ------------------------------------------------------------------ the run
def _drop(hunt, reason):
    hunt["dropped"][reason] = hunt["dropped"].get(reason, 0) + 1


SLIM_KEYS = ("domain", "name", "industry", "size", "country", "city", "technologies", "email",
             "email_source", "email_confidence", "email_note", "first_name", "last_name", "title",
             "description", "angle", "why", "score", "rank", "fit", "pain", "timing", "pay", "blockers",
             "pass", "tranco", "mx_host", "lead_id", "approved", "skipped", "origin", "fresh", "site", "tools")


def _slim(rows):
    out = []
    for row in rows:
        slim = {k: row[k] for k in SLIM_KEYS if k in row}
        slim["description"] = (slim.get("description") or "")[:600]
        slim["evidence_count"] = len(row.get("evidence") or [])
        slim["evidence"] = [{"kind": e.get("kind"), "title": (e.get("title") or "")[:120], "ts": e.get("ts")}
                            for e in (row.get("evidence") or [])[:6]]
        out.append(slim)
    return out


def _save(hunt):
    db.update("hunts", hunt["id"], {
        "status": hunt["status"], "finished_ts": hunt.get("finished_ts", 0),
        "funnel": json.dumps(hunt["funnel"]), "dropped": json.dumps(hunt["dropped"]),
        "sources": json.dumps(hunt.get("sources", {})), "note": hunt.get("note", ""),
        "rows": json.dumps(hunt.get("rows", []))})


def _run(hunt, offer, card, weights):
    now = db.now()
    try:
        view = radar.assistant_view(card)
        sig = view["signals"]
        if sig.get("total"):
            top = radar.lead_kinds(sig["kinds"])
            _phase("reading %d job results - %s" % (sig["total"],
                                                    ", ".join("%s %d" % item for item in top)), 0.012)
        if view["buyer_role"] or view["use_cases"]:
            _phase("targeting %s - %s" % (view["buyer_role"] or "the companies the read fits",
                                          ", ".join(view["use_cases"][:3]) or "the card's words"), 0.02)
        pool = pull(hunt, card, offer["id"])
        hunt["funnel"]["pulled"] = len(pool)
        if not pool:
            hunt["note"] = ("Nothing matched the card. Loosen the countries or industries, run the "
                            "daily feeds, or import more leads.")
            return
        if _cancelled():
            return
        _parallel(pool, lambda c: check_mx(c, now), 16, "checking mail servers", 0.1, 0.35)
        alive = []
        for company in pool:
            if company.get("mx"):
                alive.append(company)
            else:
                _drop(hunt, "no mail server" if company.get("live") else "domain has no mail")
        hunt["funnel"]["checked"] = len(alive)
        if _cancelled():
            return
        _phase("scoring %d companies against the card" % len(alive), 0.38)
        scored_rows = []
        for company in alive:
            scored = radar.score(company, card, weights, now)
            hard = radar.hard_blockers(scored["blockers"])
            if hard:
                _drop(hunt, hard[0].split(" (")[0])
                continue
            company.update({k: scored[k] for k in ("score", "fit", "pain", "timing", "pay", "why", "tools")})
            company["rank"] = radar.rank(scored)
            scored_rows.append(company)
        scored_rows.sort(key=lambda r: -r["rank"])
        keep = int(hunt["params"].get("keep", radar.DEFAULT_KEEP))
        if len(scored_rows) > keep:
            hunt["dropped"]["below the top %d" % keep] = len(scored_rows) - keep
            scored_rows = scored_rows[:keep]
        hunt["funnel"]["kept"] = len(scored_rows)
        _phase("reading %d sites and finding addresses" % len(scored_rows), 0.45)
        deep(hunt, scored_rows, card, weights, offer["name"])
        fresh = {f["domain"] for f in fresh_triggers(offer["id"]) if f.get("domain")}
        for row in scored_rows:
            row["fresh"] = row["domain"] in fresh
        scored_rows.sort(key=lambda r: (not r.get("pass"), not r.get("email"), -r.get("rank", 0)))
        hunt["funnel"]["with_email"] = sum(1 for r in scored_rows if r.get("email"))
        hunt["rows"] = _slim(scored_rows)
    finally:
        hunt["finished_ts"] = db.now()
        if RUN.get("error"):
            hunt["status"] = "failed"
        elif _cancelled():
            hunt["status"] = "cancelled"
        else:
            hunt["status"] = "done"
        _save(hunt)


def start(offer_id, pull_n=None, keep_n=None):
    offer, card, weights = card_for(int(offer_id or 0))
    if not offer:
        return {"ok": False, "error": "Add or pick an offer first - the hunt works from its card."}

    # The jobs keep writing findings; a hunt reads them again instead of trusting the copy
    # the read saved, so a week of new ad spenders and hiring changes the target.
    signals = daily.signals_summary()
    if signals.get("total"):
        target = dict(card.get("targeting") or {})
        target["signals"] = {"total": int(signals.get("total") or 0),
                             "kinds": dict(signals.get("kinds") or {}),
                             "words": list(signals.get("words") or [])[:10],
                             "flags": list(signals.get("flags") or [])[:8],
                             "markets": list(signals.get("markets") or [])[:6],
                             "read_at": int(signals.get("read_at") or 0)}
        card = radar.normalise_card(dict(card, targeting=target))
    with RUN_LOCK:
        if RUN["running"]:
            return {"ok": False, "error": "A hunt is already running."}
        RUN.update({"running": True, "phase": "starting", "progress": 0.0, "cancel": False, "error": ""})
    params = {"pull": max(50, min(20000, int(pull_n or radar.DEFAULT_PULL))),
              "keep": max(10, min(1000, int(keep_n or radar.DEFAULT_KEEP)))}
    view = radar.assistant_view(card)
    params["targeting"] = {"buyer_role": view["buyer_role"], "use_cases": view["use_cases"][:4],
                           "needs": view["needs"][:4], "source": view["source"],
                           "pains": list(card.get("pain_signals") or [])[:6],
                           "keywords": list(card.get("keywords") or [])[:6],
                           "solved_by": list(card.get("solved_by") or [])[:4],
                           "signals": {"total": view["signals"]["total"],
                                       "kinds": dict(view["signals"]["kinds"]),
                                       "words": list(view["signals"]["words"])[:6]},
                           "countries": radar.card_countries(card)}
    hunt = {"offer_id": offer["id"], "params": params, "status": "running",
            "funnel": {"pulled": 0, "checked": 0, "kept": 0, "with_email": 0, "approved": 0, "sent": 0},
            "dropped": {}, "sources": {}, "note": "", "rows": []}
    hunt["id"] = db.insert("hunts", {"offer_id": offer["id"], "ts": db.now(), "status": "running",
                                     "params": json.dumps(params), "funnel": json.dumps(hunt["funnel"])})
    RUN["hunt_id"] = hunt["id"]
    old = db.rows("SELECT id FROM hunts WHERE offer_id=? ORDER BY id DESC LIMIT -1 OFFSET ?",
                  (offer["id"], HUNT_KEEP_HISTORY))
    for row in old:
        db.execute("DELETE FROM hunts WHERE id=?", (row["id"],))

    def work():
        try:
            _run(hunt, offer, card, weights)
        except Exception as exc:
            RUN["error"] = str(exc)
            config.log("hunt failed: %s\n%s" % (exc, traceback.format_exc()))
            hunt["status"] = "failed"
            hunt["note"] = "The hunt stopped: %s" % exc
            _save(hunt)
        finally:
            with RUN_LOCK:
                RUN.update({"running": False, "phase": "", "progress": 1.0})
    threading.Thread(target=work, name="radar-hunt", daemon=True).start()
    return {"ok": True, "hunt_id": hunt["id"]}


def stop():
    RUN["cancel"] = True
    return {"ok": True}


def _hunt_row(hunt_id=0, offer_id=0):
    if hunt_id:
        row = db.one("SELECT * FROM hunts WHERE id=?", (hunt_id,))
    else:
        row = db.one("SELECT * FROM hunts WHERE offer_id=? ORDER BY id DESC LIMIT 1", (offer_id,))
    if not row:
        return None
    hunt = dict(row)
    for key, default in (("params", {}), ("funnel", {}), ("dropped", {}), ("sources", {}), ("rows", [])):
        hunt[key] = _j(row.get(key), default)
    return hunt


def _sent_count(offer_id):
    return db.scalar("SELECT COUNT(*) FROM radar_leads WHERE offer_id=? AND sent_ts>0", (offer_id,))


def state(offer_id=0):
    offer_list = [{"id": o["id"], "name": o["name"], "category": o.get("category", ""),
                   "active": o.get("active", 1)} for o in offers.list_offers()]
    if not offer_id and offer_list:
        active = [o for o in offer_list if o["active"]]
        offer_id = (active or offer_list)[0]["id"]
    out = {"ok": True, "offers": offer_list, "offer_id": int(offer_id or 0), "run": dict(RUN),
           "categories": radar.CATEGORIES, "approve_min": APPROVE_MIN_CONFIDENCE}
    if not offer_id:
        return out
    offer, card, weights = card_for(int(offer_id))
    if not offer:
        return out
    out.update({"offer": {k: offer.get(k) for k in ("id", "name", "description", "affiliate_url", "category",
                                                    "keywords", "pitch_subject", "pitch_body")},
                "card": card, "weights": weights, "pain_profile": radar.pain_profile(card),
                "fresh": fresh_triggers(offer["id"]),
                "territory": territory_counts(offer["id"])})
    hunt = _hunt_row(offer_id=offer["id"])
    if hunt:
        hunt["funnel"]["sent"] = _sent_count(offer["id"])
        hunt["funnel"]["approved"] = sum(1 for r in hunt["rows"] if r.get("approved"))
        hunt["stages"] = radar.funnel_rows(hunt["funnel"])
        out["hunt"] = hunt
    return out


def territory_counts(offer_id):
    rows = db.rows("SELECT status, COUNT(*) AS n FROM radar_leads WHERE offer_id=? GROUP BY status", (offer_id,))
    return {r["status"]: r["n"] for r in rows}


def fresh_triggers(offer_id, days=3, limit=12):
    """Findings from the last few days that fit this offer's keywords."""
    try:
        rows = daily.feed(limit=300, offer_id=offer_id, min_fit=1)
    except Exception:
        return []
    cutoff = db.now() - days * 86400
    out = []
    for row in rows:
        if int(row.get("ts") or 0) < cutoff:
            continue
        out.append({"id": row["id"], "domain": _domain(row.get("domain")), "company": row.get("company", ""),
                    "title": row.get("title", ""), "kind": row.get("kind", ""), "fit": row.get("fit", 0),
                    "ts": row.get("ts", 0), "matched": row.get("matched", [])})
        if len(out) >= limit:
            break
    return out


def live(offer_id, limit=60):
    """Companies your feeds already found, scored against the card - no hunt needed."""
    offer, card, weights = card_for(int(offer_id or 0))
    if not offer:
        return {"ok": False, "error": "Pick an offer first."}
    now = db.now()
    rows, blocked = [], 0
    for domain, company in signal_companies().items():
        scored = radar.score(company, card, weights, now)
        if not scored["pass"]:
            blocked += 1
            continue
        if scored["score"] <= 0:
            continue
        scored["rank"] = radar.rank(scored)
        rows.append(scored)
    rows.sort(key=lambda r: -r["score"])
    return {"ok": True, "rows": rows[:limit], "total": len(rows), "blocked": blocked}


# ------------------------------------------------------------------ approve / skip
def _upsert_lead(row, offer_name):
    email = (row.get("email") or "").strip().lower()
    now = db.now()
    existing = db.one("SELECT * FROM leads WHERE email=?", (email,)) if email else None
    tag = "radar:" + offer_name[:40]
    if existing:
        tags = [t.strip() for t in (existing.get("tags") or "").split(",") if t.strip()]
        if tag not in tags:
            tags.append(tag)
        db.update("leads", existing["id"], {"tags": ", ".join(tags)[:500], "updated_ts": now})
        return existing["id"]
    return db.insert("leads", {
        "email": email, "company": (row.get("name") or row.get("domain") or "")[:200],
        "domain": row.get("domain", ""), "website": "https://" + row.get("domain", ""),
        "first_name": row.get("first_name", ""), "last_name": row.get("last_name", ""),
        "title": row.get("title", ""), "industry": row.get("industry", ""),
        "country": radar.country_of(row), "city": row.get("city", ""),
        "technologies": row.get("technologies", ""),
        "description": ((row.get("site") or {}).get("description") or row.get("description") or "")[:1500],
        "source": "radar", "tags": tag, "notes": (row.get("angle") or "")[:1000], "status": "new",
        "verify_status": "unverified", "created_ts": now, "updated_ts": now})


def approve(hunt_id, domains=None, top=0, soldier_id=0, allow_low=False):
    hunt = _hunt_row(hunt_id=int(hunt_id or 0))
    if not hunt:
        return {"ok": False, "error": "That hunt is gone - run a new one."}
    offer = offers.get(hunt["offer_id"])
    if not offer:
        return {"ok": False, "error": "The offer of this hunt was deleted."}
    wanted = {d.lower() for d in (domains or [])}
    approved, skipped_low, errors = 0, 0, []
    budget = int(top or 0)
    for row in hunt["rows"]:
        if row.get("approved") or row.get("skipped"):
            continue
        if wanted and row.get("domain", "").lower() not in wanted:
            continue
        if not wanted:
            if budget and approved >= budget:
                break
            if not row.get("pass"):
                continue
        if not row.get("email"):
            errors.append("%s has no address" % row.get("domain"))
            continue
        if int(row.get("email_confidence") or 0) < APPROVE_MIN_CONFIDENCE and not (allow_low or wanted):
            skipped_low += 1
            continue
        if db.one("SELECT value FROM army_suppress WHERE (kind='domain' AND value=?) OR (kind='email' AND value=?)",
                  (row["domain"], row["email"])) or db.is_suppressed(row["email"]):
            errors.append("%s is on the do-not-contact list" % row["domain"])
            continue
        lead_id = _upsert_lead(row, offer["name"])
        profile = {"title": (row.get("site") or {}).get("title", ""),
                   "description": (row.get("site") or {}).get("description", "") or row.get("description", ""),
                   "text": (row.get("site") or {}).get("text", "")[:1500],
                   "tools": row.get("tools") or (row.get("site") or {}).get("tools", []),
                   "qualify": (row.get("site") or {}).get("qualify", {}),
                   "industry": row.get("industry", ""), "country": radar.country_of(row),
                   "city": row.get("city", ""), "size": row.get("size", ""),
                   "evidence": row.get("evidence", []), "name": row.get("name", ""),
                   "first_name": row.get("first_name", ""), "title_person": row.get("title", "")}
        db.execute(
            "INSERT INTO radar_leads(offer_id, lead_id, hunt_id, domain, company, email, email_source, "
            "email_confidence, score, rank, why, angle, profile, soldier_id, status, approved_ts) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'approved', ?) "
            "ON CONFLICT(offer_id, domain) DO UPDATE SET lead_id=excluded.lead_id, email=excluded.email, "
            "score=excluded.score, rank=excluded.rank, why=excluded.why, angle=excluded.angle, "
            "profile=excluded.profile, soldier_id=CASE WHEN excluded.soldier_id>0 THEN excluded.soldier_id "
            "ELSE radar_leads.soldier_id END",
            (offer["id"], lead_id, hunt["id"], row["domain"], row.get("name", ""), row["email"],
             row.get("email_source", ""), int(row.get("email_confidence") or 0), int(row.get("score") or 0),
             float(row.get("rank") or 0), row.get("why", ""), row.get("angle", ""), json.dumps(profile),
             int(soldier_id or 0), db.now()))
        row["approved"] = True
        row["lead_id"] = lead_id
        approved += 1
    hunt["funnel"]["approved"] = sum(1 for r in hunt["rows"] if r.get("approved"))
    db.update("hunts", hunt["id"], {"rows": json.dumps(hunt["rows"]), "funnel": json.dumps(hunt["funnel"])})
    if approved:
        db.event("radar", "approved %d companies for %s" % (approved, offer["name"]))
    return {"ok": True, "approved": approved, "skipped_low_confidence": skipped_low, "errors": errors[:10]}


def skip(hunt_id, domain, never=False, undo=False):
    hunt = _hunt_row(hunt_id=int(hunt_id or 0))
    if not hunt:
        return {"ok": False, "error": "That hunt is gone."}
    for row in hunt["rows"]:
        if row.get("domain", "").lower() == (domain or "").lower():
            row["skipped"] = not undo
    if never and domain:
        db.execute("INSERT OR REPLACE INTO army_suppress(kind, value, reason, ts) VALUES('domain', ?, ?, ?)",
                   (domain.lower(), "skipped on the Radar", db.now()))
    db.update("hunts", hunt["id"], {"rows": json.dumps(hunt["rows"])})
    return {"ok": True}


def territory(offer_id=0, status="", limit=200):
    where, args = ["1=1"], []
    if offer_id:
        where.append("r.offer_id=?")
        args.append(int(offer_id))
    if status:
        where.append("r.status=?")
        args.append(status)
    rows = db.rows("SELECT r.id, r.offer_id, r.lead_id, r.domain, r.company, r.email, r.email_confidence, "
                   "r.score, r.rank, r.why, r.angle, r.soldier_id, r.status, r.approved_ts, r.sent_ts, "
                   "o.name AS offer_name, s.name AS soldier_name FROM radar_leads r "
                   "LEFT JOIN offers o ON o.id=r.offer_id LEFT JOIN soldiers s ON s.id=r.soldier_id "
                   "WHERE " + " AND ".join(where) + " ORDER BY r.rank DESC LIMIT ?", tuple(args) + (limit,))
    return {"ok": True, "rows": rows}
