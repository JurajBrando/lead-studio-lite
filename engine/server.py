"""Local web server: JSON API, the single-page UI, open/click tracking and the worker."""
import base64
import html
import json
import mimetypes
import os
import socket
import sys
import threading
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import ads, brands, chat, connectors, crawl, daily, demo, dorks, domain_sources, hf_data, hotlist, hyros_air, importer, llm, mail, manyreach_api, match, mcp_client, metricool, net, oauth, offers, osint, osm, pipeline, progress, scheduler, tasks, verify, video
from . import window
from . import config, db, picker
from .jobs import cc as cc_job
from .jobs.base import Context, Job

PIXEL = base64.b64decode(
    "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")

# A JSON body carrying a base64 file. 64 MB is ~48 MB of source file - far past any
# sane lead list, and small enough that a stray request cannot exhaust memory.
MAX_REQUEST_BYTES = 64 * 1024 * 1024

WORKER = {"last_tick": 0, "last_tick_result": {}, "last_sync": 0, "last_sync_result": {},
          "running": False, "started": 0}
WORKER_LOCK = threading.Lock()

# HTML the app itself renders (OAuth result pages) is never allowed to run script:
# these pages sit on the same origin as the API, so an injected tag could read the
# session token out of the UI and call every write endpoint.
PAGE_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


# ------------------------------------------------------------------- routes
def r_state():
    s = config.load_settings()
    public = {k: v for k, v in s.items() if k != "session_token"}
    conn = dict(public.get("connectors") or {})
    for secret_key, flag in (("hunter_key", "has_hunter"), ("ai_key", "has_ai"),
                             ("serper_key", "has_serper")):
        conn[flag] = bool(str(conn.get(secret_key) or "").strip())
        conn.pop(secret_key, None)
    public["connectors"] = conn
    public["connector_status"] = connectors.status()
    public["osint"] = osint.state()
    oauth_public = dict(public.get("oauth") or {})
    for secret in ("google_client_secret", "microsoft_client_secret"):
        oauth_public["has_" + secret] = bool(str(oauth_public.get(secret) or "").strip())
        oauth_public.pop(secret, None)
    public["oauth"] = oauth_public
    mailboxes = []
    for b in db.rows("SELECT * FROM mailboxes ORDER BY id"):
        mailboxes.append({
            "id": b["id"], "name": b["name"], "from_name": b["from_name"], "from_email": b["from_email"],
            "reply_to": b["reply_to"], "smtp_host": b["smtp_host"], "smtp_port": b["smtp_port"],
            "smtp_security": b["smtp_security"], "smtp_user": b["smtp_user"],
            "has_smtp_pass": bool(b["smtp_pass"]),
            "imap_host": b["imap_host"], "imap_port": b["imap_port"], "imap_user": b["imap_user"],
            "has_imap_pass": bool(b["imap_pass"]), "daily_limit": b["daily_limit"],
            "signature": b["signature"], "active": b["active"],
            "sent_today": pipeline.sent_today(b["id"]),
        })
    seqs = []
    day_ago = int(time.time()) - 86400
    for sq in db.rows("SELECT * FROM sequences ORDER BY COALESCE(created_ts,0) DESC, id DESC"):
        steps = db.rows("SELECT * FROM steps WHERE sequence_id=? ORDER BY step_no", (sq["id"],))
        mid = sq["id"]
        metrics = db.one(
            "SELECT "
            "(SELECT COUNT(*) FROM enrollments e WHERE e.sequence_id=?) AS prospects,"
            "(SELECT COUNT(*) FROM sends s JOIN enrollments e ON e.id=s.enrollment_id "
            " WHERE e.sequence_id=? AND s.status='sent') AS reached,"
            "(SELECT COUNT(*) FROM sends s JOIN enrollments e ON e.id=s.enrollment_id "
            " WHERE e.sequence_id=? AND s.opened_ts>0) AS opened,"
            "(SELECT COUNT(*) FROM sends s JOIN enrollments e ON e.id=s.enrollment_id "
            " WHERE e.sequence_id=? AND s.clicked_ts>0) AS clicked,"
            "(SELECT COUNT(DISTINCT r.lead_id) FROM replies r JOIN enrollments e ON e.lead_id=r.lead_id "
            " WHERE e.sequence_id=? AND r.is_bounce=0) AS replied,"
            "(SELECT COUNT(DISTINCT l.id) FROM leads l JOIN enrollments e ON e.lead_id=l.id "
            " WHERE e.sequence_id=? AND l.status IN ('interested','won')) AS interested,"
            "(SELECT COUNT(DISTINCT l.id) FROM leads l JOIN enrollments e ON e.lead_id=l.id "
            " WHERE e.sequence_id=? AND l.status='won') AS won,"
            "(SELECT COUNT(*) FROM sends s JOIN enrollments e ON e.id=s.enrollment_id "
            " WHERE e.sequence_id=? AND s.status='sent' AND s.sent_ts>=?) AS last24,"
            "(SELECT COUNT(DISTINCT s.mailbox_id) FROM sends s JOIN enrollments e ON e.id=s.enrollment_id "
            " WHERE e.sequence_id=? AND s.mailbox_id>0) AS senders",
            (mid, mid, mid, mid, mid, mid, mid, mid, day_ago, mid))
        state = db.one(
            "SELECT SUM(CASE WHEN status='running' THEN 1 ELSE 0 END) AS running, "
            "SUM(CASE WHEN status='paused' THEN 1 ELSE 0 END) AS paused, "
            "COUNT(*) AS total FROM enrollments WHERE sequence_id=?", (mid,)) or {}
        if (state.get("running") or 0) > 0:
            status = "running"
        elif (state.get("total") or 0) == 0:
            status = "draft"
        elif (state.get("paused") or 0) > 0:
            status = "paused"
        else:
            status = "finished"
        # A campaign imported from a sending platform carries its own numbers; those win over
        # the local counters, which know only what this app has sent.
        stats = {}
        try:
            stats = json.loads(sq.get("stats_json") or "{}") or {}
        except ValueError:
            stats = {}
        if stats.get("source"):
            for key in ("prospects", "reached", "opened", "clicked", "replied",
                        "interested", "won", "senders"):
                if key in stats:
                    metrics[key] = int(stats.get(key) or 0)
            status = str(stats.get("status") or status).lower()
            if status not in ("draft", "running", "paused", "finished"):
                status = "running" if status == "active" else "draft"
        seqs.append({**sq, "steps": steps, "enrolled": metrics["prospects"],
                     "sent": metrics["reached"], "metrics": metrics,
                     "status": status, "created_ts": int(sq.get("created_ts") or 0),
                     "source": stats.get("source") or "",
                     "external_id": stats.get("external_id") or "",
                     "stats_at": int(stats.get("synced_ts") or 0)})
    with WORKER_LOCK:
        worker = dict(WORKER)
    return {"ok": True, "settings": public, "stats": pipeline.stats(), "mailboxes": mailboxes,
            "sequences": seqs, "worker": worker, "version": config.VERSION,
            "python": config.python_hint(), "port": PORT, "data_dir": config.DATA_DIR,
            "legal": {"affiliate_note": metricool.AFFILIATE_NOTE,
                      "not_affiliated": metricool.NOT_AFFILIATED,
                      "signup_url": metricool.SIGNUP_URL,
                      "licence": "MIT"},
            "hf": hf_data.info(),
            "automation": {
                "jobs": db.scalar("SELECT COUNT(*) FROM jobs"),
                "enabled": db.scalar("SELECT COUNT(*) FROM jobs WHERE enabled=1"),
                "due": len(scheduler.due_keys()),
                "running": db.scalar("SELECT COUNT(*) FROM jobs WHERE running=1"),
                "last_status": (db.one("SELECT status FROM runs ORDER BY id DESC LIMIT 1") or {}).get("status", ""),
                "signals": db.scalar("SELECT COUNT(*) FROM signals"),
                "signals_new": db.scalar("SELECT COUNT(*) FROM signals WHERE seen=0"),
                "domains": db.scalar("SELECT COUNT(*) FROM domains"),
                "dead_domains": db.scalar("SELECT COUNT(*) FROM domains WHERE mx_status='none'"),
                "brands": db.scalar("SELECT COUNT(*) FROM brands"),
                "brand_findings": db.scalar(
                    "SELECT COUNT(*) FROM signals WHERE kind LIKE 'lookalike%'"),
                "offers": db.scalar("SELECT COUNT(*) FROM offers"),
                "offers_active": db.scalar("SELECT COUNT(*) FROM offers WHERE active=1")},
            "llm": llm.status(),
            "ads": ads.status_all(),
            "offers": [{"id": row["id"], "name": row["name"]}
                       for row in db.rows("SELECT id, name FROM offers WHERE active=1 ORDER BY id")],
            "mcp": mcp_client.status_all(),
            "manyreach": manyreach_api.status(),
            "oauth": oauth.status()}


LEAD_SORTS = {
    "newest": "id DESC",
    "company": "lower(company) ASC, id DESC",
    "country": "lower(country) ASC, lower(company) ASC, id DESC",
    "title": "lower(title) ASC, id DESC",
    "size": "company_size ASC, lower(company) ASC, id DESC",
}


def _terms(value):
    """'a, b; c' -> ['a', 'b', 'c'] - one list for every multi-value filter."""
    raw = str(value or "").replace(";", ",")
    return [part.strip() for part in raw.split(",") if part.strip()]


def _any_like(column, terms):
    clause = "(" + " OR ".join("lower(%s) LIKE ?" % column for _ in terms) + ")"
    return clause, ["%%%s%%" % term.lower() for term in terms]


def r_leads(q=""):
    page = max(1, int(q.get("page", 1) or 1))
    per = min(200, max(10, int(q.get("per", 50) or 50)))
    search = (q.get("q") or "").strip()
    status = (q.get("status") or "").strip()
    sort = (q.get("sort") or "newest").strip()
    country = _terms(q.get("country"))
    title = (q.get("title") or "").strip()
    industry = _terms(q.get("industry"))
    must = _terms(q.get("must_industry"))
    size = (q.get("company_size") or "").strip()
    location = (q.get("location") or "").strip()
    technologies = _terms(q.get("technologies"))
    source = (q.get("source") or "").strip()

    where, args = [], []
    if search:
        where.append("(email LIKE ? OR company LIKE ? OR first_name LIKE ? OR last_name LIKE ? "
                     "OR domain LIKE ? OR title LIKE ? OR description LIKE ? OR notes LIKE ?)")
        args.extend(["%%%s%%" % search] * 8)
    if status:
        where.append("status=?")
        args.append(status)
    if country:
        # A country comes from the facet list and is compared through norm_country, so
        # "US" and "United States" land in the same bucket - and "US" does not also pull
        # in Australia, Belarus or Cyprus.
        where.append("(" + " OR ".join("norm_country(country)=norm_country(?)"
                                       for _ in country) + ")")
        args += country
    if title:
        where.append("lower(title) LIKE ?")
        args.append("%%%s%%" % title.lower())
    if industry:
        clause, values = _any_like("industry", industry)
        where.append(clause)
        args += values
    for term in must:                       # must-have: every term has to be present
        where.append("lower(industry) LIKE ?")
        args.append("%%%s%%" % term.lower())
    if size == "unknown":
        where.append("coalesce(company_size,'')=''")
    elif size:
        where.append("company_size=?")
        args.append(size)
    if location:
        where.append("(lower(city) LIKE ? OR lower(country) LIKE ?)")
        args.extend(["%%%s%%" % location.lower()] * 2)
    if technologies:
        clause, values = _any_like("technologies", technologies)
        where.append(clause)
        args += values
    if source:
        where.append("source=?")
        args.append(source)

    clause = (" WHERE " + " AND ".join(where)) if where else ""
    order = LEAD_SORTS.get(sort, LEAD_SORTS["newest"])
    total = db.scalar("SELECT COUNT(*) FROM leads" + clause, tuple(args))
    items = db.rows("SELECT * FROM leads" + clause + " ORDER BY " + order +
                    " LIMIT ? OFFSET ?", tuple(args) + (per, (page - 1) * per))
    return {"ok": True, "leads": items, "total": total, "page": page, "per": per,
            "pages": max(1, (total + per - 1) // per),
            "facets": {
                "countries": [r["country"] for r in db.rows(
                    "SELECT norm_country(country) AS country, COUNT(*) n FROM leads "
                    "WHERE trim(coalesce(country,''))<>'' GROUP BY 1 ORDER BY n DESC LIMIT 80")],
                "sources": [r["source"] for r in db.rows(
                    "SELECT source, COUNT(*) n FROM leads WHERE trim(source)<>'' "
                    "GROUP BY source ORDER BY n DESC LIMIT 40")],
                "sizes": list(importer.SIZE_BUCKETS),
            }}


LEAD_FIELDS = ("email", "first_name", "last_name", "company", "title", "domain", "description",
               "phone", "city", "country", "industry", "company_size", "technologies",
               "linkedin", "website", "notes", "tags", "source", "status")


def r_mcp_status(q=None):
    return {"ok": True, "connections": mcp_client.status_all()}


def r_mcp_start(p):
    try:
        answer = mcp_client.begin(str(p.get("name") or ""), PORT)
    except mcp_client.McpError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, **answer}


def r_mcp_tools(p):
    try:
        items = mcp_client.tools(str(p.get("name") or ""))
    except mcp_client.McpError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "tools": items}


def r_mcp_call(p):
    arguments = p.get("arguments") or {}
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments or "{}")
        except ValueError:
            return {"ok": False, "error": "Those arguments are not valid JSON."}
    if not isinstance(arguments, dict):
        return {"ok": False, "error": "The arguments must be a JSON object."}
    tool = str(p.get("tool") or "").strip()
    if not tool:
        return {"ok": False, "error": "Pick a tool first."}
    try:
        return mcp_client.call(str(p.get("name") or ""), tool, arguments)
    except mcp_client.McpError as exc:
        return {"ok": False, "error": str(exc)}


def r_mcp_pull(p):
    try:
        answer = mcp_client.pull(str(p.get("name") or ""), str(p.get("kind") or ""))
    except mcp_client.McpError as exc:
        return {"ok": False, "error": str(exc)}
    if answer.get("ok"):
        answer["items"] = mcp_client.items(str(p.get("name") or ""))
        if str(p.get("name") or "") == "manyreach":
            answer["imported"] = manyreach_api.import_into_app()
    return answer


def r_manyreach_import(p=None):
    return manyreach_api.import_into_app()


def r_mcp_items(q):
    try:
        rows = mcp_client.items(str(q.get("name") or ""), str(q.get("kind") or ""))
    except mcp_client.McpError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "items": rows}


def r_mcp_custom(p):
    return mcp_client.save_custom(p)


def r_manyreach_status(q=None):
    return {"ok": True, **manyreach_api.status()}


def r_manyreach_key(p):
    return manyreach_api.set_key(p.get("key") or "")


def r_manyreach_sync(p):
    answer = manyreach_api.sync()
    if answer.get("ok"):
        answer["items"] = mcp_client.items("manyreach")
        answer["status"] = manyreach_api.status()
    return answer


def r_mcp_disconnect(p):
    try:
        return mcp_client.disconnect(str(p.get("name") or ""))
    except mcp_client.McpError as exc:
        return {"ok": False, "error": str(exc)}


def r_ads(q=None):
    return {"ok": True, "platforms": ads.status_all()}


def r_ads_save(p):
    try:
        return ads.save(p.get("name") or "", p)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}


def r_ads_test(p):
    try:
        return ads.test(p.get("name") or "")
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}


def r_ads_pull(p):
    try:
        return ads.pull(p.get("name") or "", p.get("path") or "")
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}


def r_hyros_accounts(p=None):
    return ads.hyros_ad_accounts()


def r_hyros_attribution(p):
    return ads.hyros_attribution(int(p.get("days") or 30), p.get("model") or "last_click",
                                 p.get("level") or "", p.get("ad_account_id") or "")


def r_hyros_mcp(p=None):
    """Register the official Hyros MCP server with the same API key, as a token connection."""
    key = ads.api_key_of("hyros")
    if not key:
        return {"ok": False, "error": "Add the HYROS API key first (Ads -> HYROS Ad Tracking)."}
    return mcp_client.save_custom({"name": "hyros", "label": "Hyros MCP",
                                   "url": "https://mcp.hyros.com/mcp", "token": key})


def r_hyros_status(q=None):
    return {"ok": True, **hyros_air.status()}


def r_hyros_pull(p):
    return hyros_air.pull(p.get("kind") or "leads", int(p.get("limit") or 100))


def r_hyros_paths(p):
    return hyros_air.save_paths(p.get("paths") or {})


def r_lead_save(p):
    lead_id = p.get("id")
    current = None
    if lead_id:
        current = db.one("SELECT * FROM leads WHERE id=?", (lead_id,))
        if not current:
            return {"ok": False, "error": "That lead no longer exists."}
    # Only touch the fields that were actually sent. The edit form shows a subset of the
    # columns, and writing "" for everything else used to wipe phone, industry, tags,
    # source... on every save.
    fields = {}
    for key in LEAD_FIELDS:
        if key in p:
            value = p.get(key)
            fields[key] = ("" if value is None else str(value)).strip()
    if "email" in fields:
        fields["email"] = fields["email"].lower()
        if fields["email"] and not mail.valid_email(fields["email"]):
            return {"ok": False, "error": "That email address doesn't look valid."}
    merged = dict(current or {})
    merged.update(fields)
    # A lead without an address is allowed, but it still has to be *someone*.
    if not any((merged.get(key) or "").strip() for key in
               ("email", "company", "domain", "linkedin", "first_name", "last_name")):
        return {"ok": False,
                "error": "Give the lead an email, a company, a domain, a LinkedIn URL or a name."}
    email = (merged.get("email") or "").strip().lower()
    fields["email"] = email
    if fields.get("country"):
        fields["country"] = net.normalise_country(fields["country"])
    email_changed = bool(current) and email != (current.get("email") or "").lower()
    # Follow the address when it changes (or when the caller cleared the domain), but never
    # overwrite a domain the user typed by hand.
    if email and not merged.get("domain") and (current is None or email_changed or "domain" in fields):
        fields["domain"] = email.rsplit("@", 1)[-1]
    if "status" in fields:
        fields["status"] = fields["status"] or "new"
    ts = db.now()
    if email:
        dup = db.one("SELECT id FROM leads WHERE email=? AND id<>?", (email, lead_id or 0))
        if dup:
            return {"ok": False, "error": "Another lead already uses that email address."}
    if lead_id:
        if email_changed:
            fields["verify_status"] = "unverified" if email else "none"
        fields["updated_ts"] = ts
        db.update("leads", lead_id, fields)
        domain_sources.ensure_domain(
            fields.get("domain") or merged.get("domain") or (email.rsplit("@", 1)[-1] if email else ""))
        return {"ok": True, "id": lead_id}
    for key in LEAD_FIELDS:
        fields.setdefault(key, "")
    fields["status"] = fields.get("status") or "new"
    fields.update({"created_ts": ts, "updated_ts": ts,
                   "verify_status": "unverified" if email else "none"})
    new_id = db.insert("leads", fields)
    domain_sources.ensure_domain(fields.get("domain") or (email.rsplit("@", 1)[-1] if email else ""))
    return {"ok": True, "id": new_id}


def r_lead_delete(p):
    ids = p.get("ids") or ([p["id"]] if p.get("id") else [])
    if not ids:
        return {"ok": False, "error": "No lead selected."}
    n = 0
    for i in ids:
        # Take the person's email content with them: sends keeps the full body and replies
        # keeps the snippet, so "delete lead" used to leave both behind forever.
        db.execute("DELETE FROM sends WHERE lead_id=?", (i,))
        db.execute("DELETE FROM replies WHERE lead_id=?", (i,))
        db.execute("DELETE FROM enrollments WHERE lead_id=?", (i,))
        db.execute("DELETE FROM signals WHERE lead_id=?", (i,))
        db.execute("DELETE FROM leads WHERE id=?", (i,))
        n += 1
    db.event("delete", "%d lead(s) removed" % n)
    return {"ok": True, "deleted": n}


# Changing a lead's status by hand must also stop its sequence. "replied" and "interested"
# mean the prospect answered, "won"/"lost" mean the deal is over - none of them should keep
# sending follow-ups. The value is the enrollment status to write.
ENROLLMENT_STOP = {
    "unsubscribed": "unsubscribed",
    "bounced": "bounced",
    "replied": "replied",
    "interested": "finished",
    "won": "finished",
    "lost": "finished",
}


def r_lead_status(p):
    ids = p.get("ids") or ([p["id"]] if p.get("id") else [])
    status = p.get("status") or "new"
    allowed = {"new", "contacted", "replied", "interested", "won", "lost", "bounced", "unsubscribed"}
    if status not in allowed:
        return {"ok": False, "error": "Unknown status."}
    ts = db.now()
    for i in ids:
        lead = db.one("SELECT email, replied_ts FROM leads WHERE id=?", (i,))
        if not lead:
            continue
        patch = {"status": status, "updated_ts": ts}
        if status == "replied" and not lead.get("replied_ts"):
            patch["replied_ts"] = ts
        db.update("leads", i, patch)
        if status in ("unsubscribed", "bounced"):
            db.add_suppression(lead["email"], status)
        if status in ENROLLMENT_STOP:
            db.execute("UPDATE enrollments SET status=?, finished_ts=? "
                       "WHERE lead_id=? AND status='running'",
                       (ENROLLMENT_STOP[status], ts, i))
    return {"ok": True, "updated": len(ids)}


def _import_run(label, work):
    """Run an import in a thread and hand back the id the browser polls."""
    run_id = progress.start(label)

    def reporter(done, total, phase, rows=0):
        progress.update(run_id, done, total, phase, rows)

    def worker():
        try:
            result = work(reporter)
        except Exception as exc:                   # noqa: BLE001 - reported to the UI
            config.log("import failed: %s\n%s" % (exc, traceback.format_exc()))
            progress.finish(run_id, error=str(exc))
            return
        try:
            if result.get("ok"):
                result["lead_domains"] = domain_sources.sync_lead_domains().get("added", 0)
        except Exception as exc:                   # noqa: BLE001
            config.log("domain sync after import failed: %s" % exc)
        progress.finish(run_id, result=result)

    threading.Thread(target=worker, name="import", daemon=True).start()
    return run_id


def r_import_progress(q):
    return progress.snapshot(q.get("id") or "")


def r_osm_search(p):
    """Offer + place -> OpenStreetMap businesses, split by how they can be reached."""
    try:
        return osm.search(p.get("text") or p.get("offer") or "",
                          area=str(p.get("area") or ""),
                          limit=int(p.get("limit") or 200),
                          refresh=bool(p.get("refresh")))
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:                      # noqa: BLE001 - the API is the outside world
        config.log("osm search failed: %s\n%s" % (exc, traceback.format_exc()))
        return {"ok": False, "error": "OpenStreetMap did not answer: %s" % exc}


def r_osm_import(p):
    """The OSM rows with an email (and the website-only ones) become leads."""
    try:
        return osm.import_ready(category=str(p.get("category") or ""),
                                area=str(p.get("area") or ""),
                                limit=int(p.get("limit") or 2000),
                                include_website=bool(p.get("include_website", True)))
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}


def r_osm_status(p):
    return osm.lists(category=str(p.get("category") or ""), area=str(p.get("area") or ""))


def r_hot_today(p):
    """Today's hot list from the app's own signals, leads and offers."""
    try:
        answer = hotlist.build(days=int(p.get("days") or 2),
                               limit=int(p.get("limit") or 500))
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    if p.get("table"):
        answer["rows"] = answer["rows"][:300]
    return answer


def r_osint_state(p):
    return osint.state()


def r_osint_scan(p):
    try:
        return osint.scan(str(p.get("source") or ""), brands_text=str(p.get("brands") or ""),
                          limit=int(p.get("limit") or 5000))
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}


def r_osint_profile(p):
    try:
        if p.get("domain"):
            return osint.profile(str(p["domain"]))
        return osint.profile_new(limit=int(p.get("limit") or 20))
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}


def r_osint_dorks(p):
    try:
        return dorks.sweep_brands(str(p.get("brands") or ""),
                                  own_domains=[d for d in str(p.get("own") or "").split(",") if d.strip()],
                                  per_brand=int(p.get("per_brand") or 5),
                                  limit=int(p.get("limit") or 200))
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}


def r_osint_discover(p):
    brands = str(p.get("brands") or "")
    names = [" ".join(line.split()) for line in brands.replace(",", "\n").splitlines() if line.strip()]
    if not names:
        return {"ok": False, "error": "Type a brand to search certificates for."}
    found, errors = [], []
    for brand in names[:10]:
        try:
            answer = osint.discover(brand, limit=int(p.get("limit") or 200))
            found.extend(answer["candidates"])
        except ValueError as exc:
            errors.append(str(exc))
    return {"ok": True, "candidates": found[:300], "count": len(found),
            "error": "; ".join(errors[:2]) if not found else ""}


def r_osint_enrich(p):
    try:
        return osint.enrich(str(p.get("domain") or ""))
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}


def r_osint_meta_ads(p):
    try:
        return osint.meta_ads(str(p.get("keywords") or "shopify store"),
                              country=str(p.get("country") or ""),
                              limit=int(p.get("limit") or 25))
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}


def r_lead_import(p):
    name = p.get("name") or "upload.csv"
    text = p.get("text")
    b64 = p.get("b64")
    if b64:
        raw = base64.b64decode(b64)
    elif text is not None:
        raw = text.encode("utf-8")
    else:
        return {"ok": False, "error": "No file received."}
    options = {"update_existing": bool(p.get("update_existing")),
               "mark_status": p.get("status") or "new",
               "only_email": bool(p.get("only_email"))}
    run_id = _import_run(name, lambda report: importer.import_data(name, raw, source="import",
                                                                  report=report, **options))
    return {"ok": True, "async": True, "import_id": run_id, "name": name}


def r_pick_file(p):
    """Open a native file dialog on this computer and return the path it chose."""
    try:
        return picker.ask(str(p.get("title") or "Choose a file to import"),
                          [str(x) for x in (p.get("extensions") or [])])
    except picker.PickerError as exc:
        return {"ok": False, "error": str(exc)}


def r_lead_inspect(p):
    """What a file on this computer contains: tables, columns, rows."""
    return importer.inspect_source(p.get("path") or "", str(p.get("table") or ""))


def r_lead_import_path(p):
    """Import from a file on this computer, streaming and capped (LEAD_INDEX files are GBs)."""
    try:
        limit = int(p.get("limit") or 20000)
    except (TypeError, ValueError):
        return {"ok": False, "error": "The row limit must be a number."}
    limit = max(1, min(limit, 100000000))
    options = {"table": str(p.get("table") or ""), "where": str(p.get("where") or ""),
               "limit": limit, "update_existing": bool(p.get("update_existing")),
               "mark_status": p.get("status") or "new",
               "only_email": bool(p.get("only_email"))}
    name = os.path.basename(str(p.get("path") or "")) or "file"
    run_id = _import_run(name, lambda report: importer.import_path(
        p.get("path") or "", report=report, **options))
    return {"ok": True, "async": True, "import_id": run_id, "name": name}


def r_lead_verify(p):
    ids = p.get("ids") or []
    limit = min(500, int(p.get("limit", 200) or 200))
    opts = config.load_settings().get("verify", {})
    if ids:
        leads = [r for r in db.rows("SELECT * FROM leads WHERE id IN (%s)" % ",".join("?" * len(ids)),
                                    tuple(ids)) if r["email"]]
    else:
        leads = db.rows("SELECT * FROM leads WHERE verify_status='unverified' AND email<>'' "
                        "ORDER BY id LIMIT ?", (limit,))
    checked = {"valid": 0, "risky": 0, "invalid": 0}
    for lead in leads[:limit]:
        res = verify.verify(lead["email"], opts)
        db.update("leads", lead["id"], {"verify_status": res["status"], "verify_detail": res["detail"],
                                        "updated_ts": db.now()})
        checked[res["status"]] = checked.get(res["status"], 0) + 1
    db.event("verify", "%d addresses checked" % len(leads[:limit]))
    return {"ok": True, "checked": len(leads[:limit]), "results": checked}


def r_seq_save(p):
    name = (p.get("name") or "").strip()
    if not name:
        return {"ok": False, "error": "Give the sequence a name."}
    steps = p.get("steps") or []
    if not steps:
        return {"ok": False, "error": "A sequence needs at least one step."}
    ts = db.now()
    seq_id = p.get("id")
    if seq_id:
        db.update("sequences", seq_id, {"name": name, "description": p.get("description") or "",
                                        "active": 1 if p.get("active", True) else 0, "updated_ts": ts})
    else:
        seq_id = db.insert("sequences", {"name": name, "description": p.get("description") or "",
                                         "active": 1, "created_ts": ts, "updated_ts": ts})
    db.execute("DELETE FROM steps WHERE sequence_id=?", (seq_id,))
    for i, st in enumerate(steps, start=1):
        db.insert("steps", {
            "sequence_id": seq_id, "step_no": i,
            "delay_days": max(0, int(st.get("delay_days") or 0)),
            "subject": st.get("subject") or "", "body": st.get("body") or "",
            "stop_on_reply": 1 if st.get("stop_on_reply", True) else 0,
        })
    db.event("sequence", "saved %s with %d steps" % (name, len(steps)))
    return {"ok": True, "id": seq_id}


def r_seq_delete(p):
    if not p.get("id"):
        return {"ok": False, "error": "No sequence selected."}
    db.execute("DELETE FROM sequences WHERE id=?", (p["id"],))
    return {"ok": True}


def r_enroll(p):
    seq_id = p.get("sequence_id")
    if not seq_id:
        return {"ok": False, "error": "Choose a sequence first."}
    ids = p.get("leads") or []
    if p.get("filter") == "new":
        ids = [r["id"] for r in db.rows(
            "SELECT id FROM leads WHERE status='new' AND email<>'' LIMIT 500")]
    if not ids:
        return {"ok": False, "error": "No leads selected."}
    return pipeline.enroll(int(seq_id), [int(i) for i in ids], int(p.get("mailbox_id") or 0))


def r_enrollments(q):
    status = (q.get("status") or "").strip()
    sql = ("SELECT e.*, l.email, l.first_name, l.last_name, l.company, s.name AS sequence_name "
           "FROM enrollments e JOIN leads l ON l.id=e.lead_id JOIN sequences s ON s.id=e.sequence_id")
    args = ()
    if status:
        sql += " WHERE e.status=?"
        args = (status,)
    sql += " ORDER BY e.next_run_ts LIMIT 500"
    return {"ok": True, "enrollments": db.rows(sql, args)}


def r_enroll_status(p):
    status = p.get("status") or "paused"
    if p.get("all"):
        db.execute("UPDATE enrollments SET status=? WHERE status IN ('running','paused')", (status,))
        return {"ok": True}
    if not p.get("id"):
        return {"ok": False, "error": "No enrollment selected."}
    patch = {"status": status}
    if status == "running":
        patch["next_run_ts"] = db.now()
    db.update("enrollments", p["id"], patch)
    return {"ok": True}


def r_tick(p):
    res = pipeline.tick(force=bool(p.get("force")))
    with WORKER_LOCK:
        WORKER["last_tick"] = int(time.time())
        WORKER["last_tick_result"] = res
    return {"ok": True, **res}


def r_send_test(p):
    box = pipeline.mailbox_for(p.get("mailbox_id"))
    if not box:
        return {"ok": False, "error": "Add a mailbox first."}
    to = (p.get("to") or box.get("from_email") or "").strip()
    if not mail.valid_email(to):
        return {"ok": False, "error": "Enter a valid address to send the test to."}
    subject = p.get("subject") or "Lead Studio Lite test message"
    body = p.get("body") or ("This is a test message from Lead Studio Lite.\n\n"
                             "If you're reading this, your mailbox is configured correctly.")
    ok, detail = mail.smtp_send(box, to, subject, body, mail.plain_to_html(body))
    db.event("test_send", "%s -> %s: %s" % (box["name"], to, "ok" if ok else detail[:120]))
    return {"ok": ok, "to": to, "detail": detail if ok else None, "error": None if ok else detail}


def r_box_save(p):
    required = ["name", "from_email", "smtp_host"]
    for f in required:
        if not (p.get(f) or "").strip():
            return {"ok": False, "error": "Name, from-address and SMTP host are required."}
    if not mail.valid_email(p.get("from_email")):
        return {"ok": False, "error": "The from-address is not a valid email."}
    data = {k: (p.get(k) if p.get(k) is not None else "") for k in (
        "name", "from_name", "from_email", "reply_to", "smtp_host", "smtp_user", "smtp_security",
        "imap_host", "imap_user", "signature")}
    data["smtp_port"] = int(p.get("smtp_port") or 587)
    data["imap_port"] = int(p.get("imap_port") or 993)
    data["daily_limit"] = max(1, min(500, int(p.get("daily_limit") or 30)))
    data["active"] = 1 if p.get("active", True) else 0
    for field, key in (("smtp_pass", "smtp_pass"), ("imap_pass", "imap_pass")):
        if p.get(key):
            data[field] = p[key]
    if p.get("id"):
        db.update("mailboxes", p["id"], data)
        return {"ok": True, "id": p["id"]}
    data["created_ts"] = db.now()
    return {"ok": True, "id": db.insert("mailboxes", data)}


def r_box_delete(p):
    if not p.get("id"):
        return {"ok": False, "error": "No mailbox selected."}
    db.execute("DELETE FROM mailboxes WHERE id=?", (p["id"],))
    return {"ok": True}


def r_box_test(p):
    box = db.one("SELECT * FROM mailboxes WHERE id=?", (p.get("id"),))
    if not box:
        return {"ok": False, "error": "Save the mailbox first, then test it."}
    out = {"ok": False, "smtp": "not tested", "imap": "not set", "smtp_detail": "", "imap_detail": ""}
    conn, err = mail.smtp_open(box)
    if conn:
        out["smtp"] = "ok"
        try:
            conn.quit()
        except Exception:
            pass
    else:
        out["smtp"] = "failed"
        out["smtp_detail"] = err
    if (box.get("imap_host") or "").strip():
        m, ierr = mail.imap_open(box)
        if m:
            out["imap"] = "ok"
            try:
                m.select("INBOX", readonly=True)
                typ, data = m.search(None, "UNSEEN")
                out["unread"] = len((data[0] or b"").split())
                m.logout()
            except Exception as e:
                out["imap_detail"] = str(e)
        else:
            out["imap"] = "failed"
            out["imap_detail"] = ierr
    out["ok"] = out["smtp"] == "ok"
    return out


def r_inbox_sync(p):
    res = pipeline.sync_inbox(int(p.get("mailbox_id") or 0), days=int(p.get("days") or 14))
    with WORKER_LOCK:
        WORKER["last_sync"] = int(time.time())
        WORKER["last_sync_result"] = res
    return res


def r_inbox(q):
    """One inbox for every mailbox: the list, and a per-mailbox count beside it."""
    mailbox = int(q.get("mailbox_id") or 0)
    where, args = "", []
    if mailbox:
        where = " WHERE r.mailbox_id=?"
        args.append(mailbox)
    rows = db.rows(
        "SELECT r.*, l.company, l.status AS lead_status, m.name AS mailbox_name, "
        "m.from_email AS mailbox_email FROM replies r "
        "LEFT JOIN leads l ON l.id=r.lead_id "
        "LEFT JOIN mailboxes m ON m.id=r.mailbox_id" + where +
        " ORDER BY r.received_ts DESC LIMIT ?",
        tuple(args) + (min(300, int(q.get("limit") or 100)),))
    boxes = db.rows(
        "SELECT m.id, m.name, m.from_email, COUNT(r.id) AS total, "
        "COALESCE(SUM(CASE WHEN r.seen=0 THEN 1 ELSE 0 END), 0) AS unread "
        "FROM mailboxes m LEFT JOIN replies r ON r.mailbox_id=m.id "
        "GROUP BY m.id ORDER BY (unread > 0) DESC, m.name")
    orphans = db.scalar("SELECT COUNT(*) FROM replies WHERE mailbox_id=0")
    return {"ok": True, "replies": rows, "mailboxes": boxes, "orphans": orphans,
            "unread": db.scalar("SELECT COUNT(*) FROM replies WHERE seen=0")}


def r_inbox_seen(p):
    if p.get("all"):
        db.execute("UPDATE replies SET seen=1 WHERE seen=0")
    elif p.get("id"):
        db.execute("UPDATE replies SET seen=1 WHERE id=?", (p["id"],))
    return {"ok": True}


def r_wipe(p):
    """Delete the working data but keep mailboxes and settings."""
    for table in ("sends", "replies", "enrollments", "steps", "sequences", "list_members",
                  "lists", "leads", "suppressions", "events"):
        db.execute("DELETE FROM %s" % table)
    db.event("wipe", "all working data deleted")
    return {"ok": True}


def r_settings_save(p):
    old = config.load_settings()
    s = config.load_settings()
    incoming = p.get("settings") or {}
    for k, v in incoming.items():
        if k == "session_token":
            continue
        if isinstance(v, dict) and isinstance(s.get(k), dict):
            s[k].update(v)
        else:
            s[k] = v
    # A blank API-key field means "keep the stored key", never "erase it".
    conn = s.get("connectors") or {}
    old_conn = old.get("connectors") or {}
    if isinstance(conn, dict):
        for key in ("hunter_key", "ai_key", "serper_key"):
            if key in conn and not str(conn.get(key) or "").strip():
                conn[key] = old_conn.get(key, "")
        s["connectors"] = conn
    oauth_cfg = s.get("oauth") or {}
    old_oauth = old.get("oauth") or {}
    if isinstance(oauth_cfg, dict):
        for key in ("google_client_secret", "microsoft_client_secret"):
            if key in oauth_cfg and not str(oauth_cfg.get(key) or "").strip():
                oauth_cfg[key] = old_oauth.get(key, "")
        s["oauth"] = oauth_cfg
    # Refuse a sending window that can never come true: the queue would sit idle forever
    # while the sidebar still says sending is on.
    sending = s.get("sending")
    if isinstance(sending, dict):
        try:
            start = int(sending.get("window_start", 8))
            end = int(sending.get("window_end", 18))
            gap = int(sending.get("gap_seconds", 45))
            jitter = int(sending.get("jitter_seconds", 30))
        except (TypeError, ValueError):
            return {"ok": False, "error": "Sending hours and delays must be whole numbers."}
        if not (0 <= start <= 23) or not (0 <= end <= 23):
            return {"ok": False, "error": "Sending hours must be between 0 and 23."}
        if start == end:
            return {"ok": False, "error": "The sending window needs different start and end hours."}
        if gap < 0 or jitter < 0:
            return {"ok": False, "error": "Delay and jitter cannot be negative."}
        if gap > 3600 or jitter > 3600:
            return {"ok": False, "error": "Keep the delay and jitter at one hour or less."}
        sending.update({"window_start": start, "window_end": end,
                        "gap_seconds": gap, "jitter_seconds": jitter})
        s["sending"] = sending
    config.save_settings(s)
    net.set_contact(s.get("contact_email"))
    return {"ok": True}


def r_events(q):
    return {"ok": True, "events": db.rows("SELECT * FROM events ORDER BY id DESC LIMIT ?",
                                          (min(200, int(q.get("limit") or 80)),))}


def r_diagnose():
    deps = {}
    for mod in ("openpyxl", "dns", "pyarrow", "duckdb", "pandas", "smtplib", "imaplib"):
        try:
            __import__(mod)
            deps[mod] = True
        except ImportError:
            deps[mod] = False
    return {"ok": True, "version": config.VERSION, "python": sys.version,
            "data_dir": config.DATA_DIR, "db": config.DB_PATH, "port": PORT,
            "deps": deps, "platform": sys.platform,
            "window": window.reason(),
            "settings_exists": os.path.exists(config.SETTINGS_PATH)}


def r_jobs(q=None):
    return {"ok": True, "jobs": scheduler.list_jobs()}


def r_job_update(p):
    key = p.get("key")
    row = db.one("SELECT * FROM jobs WHERE key=?", (key,))
    if not row:
        return {"ok": False, "error": "Unknown job."}
    patch = {}
    if "enabled" in p:
        patch["enabled"] = 1 if p.get("enabled") else 0
    if "schedule" in p:
        sched = (p.get("schedule") or "").strip()
        if sched and not scheduler.parse_schedule(sched):
            return {"ok": False, "error": "Use 'daily HH:MM' or 'weekly mon HH:MM', or leave it empty."}
        patch["schedule"] = sched
        patch["next_run_ts"] = scheduler.next_run_ts(sched) if sched else 0
    if isinstance(p.get("params"), dict):
        patch["params"] = json.dumps(p["params"], ensure_ascii=False)
    if patch:
        db.execute("UPDATE jobs SET %s WHERE key=?" % ", ".join("%s=?" % k for k in patch),
                   list(patch.values()) + [key])
    return {"ok": True}


def r_job_runs(q):
    limit = min(100, int(q.get("limit") or 30))
    return {"ok": True, "runs": scheduler.run_logs(q.get("key") or None, limit=limit)}


def r_signals(q):
    kind = (q.get("kind") or "").strip()
    limit = min(500, int(q.get("limit") or 200))
    rows = daily.feed(limit=limit, kind=kind, offer_id=int(q.get("offer_id") or 0),
                      min_fit=int(q.get("min_fit") or 0))
    kinds = db.rows("SELECT kind, COUNT(*) AS n FROM signals GROUP BY kind ORDER BY n DESC")
    offers = [{"id": row["id"], "name": row["name"]} for row in daily.active_offers()]
    return {"ok": True, "signals": rows, "kinds": kinds, "offers": offers,
            "total": db.scalar("SELECT COUNT(*) FROM signals"),
            "matching": sum(1 for row in rows if row["fit"]),
            "unseen": db.scalar("SELECT COUNT(*) FROM signals WHERE seen=0")}


def r_signal_seen(p):
    if p.get("all"):
        db.execute("UPDATE signals SET seen=1 WHERE seen=0")
    elif p.get("id"):
        db.execute("UPDATE signals SET seen=1 WHERE id=?", (p["id"],))
    return {"ok": True}


def r_signal_promote(p):
    try:
        if p.get("id"):
            return daily.promote(int(p["id"]), p.get("email") or "")
        raise ValueError("Pick a finding first.")
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}


def r_signal_draft(p):
    try:
        return daily.draft(int(p.get("id") or 0), int(p.get("offer_id") or 0),
                           use_ai=bool(p.get("use_ai")))
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}


def r_signal_campaign(p):
    try:
        return daily.to_campaign(int(p.get("id") or 0), int(p.get("sequence_id") or 0),
                                 p.get("email") or "", int(p.get("offer_id") or 0))
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}


def r_signal_contact(p):
    try:
        return daily.contact(int(p.get("id") or 0), p.get("subject") or "", p.get("body") or "",
                             p.get("email") or "", int(p.get("mailbox_id") or 0),
                             int(p.get("offer_id") or 0))
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}


def r_domains(q):
    search = (q.get("q") or "").strip()
    limit = min(500, int(q.get("limit") or 200))
    where, args = [], []
    if search:
        where.append("(domain LIKE ? OR name LIKE ?)")
        args.extend(["%%%s%%" % search] * 2)
    if q.get("mx_status"):
        where.append("mx_status=?")
        args.append(q["mx_status"])
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    rows = db.rows("SELECT * FROM domains" + clause + " ORDER BY updated_ts DESC LIMIT ?",
                   tuple(args) + (limit,))
    return {"ok": True, "domains": rows,
            "total": db.scalar("SELECT COUNT(*) FROM domains"),
            "dead": db.scalar("SELECT COUNT(*) FROM domains WHERE mx_status='none'")}


# ------------------------------------------------------------------ Domains tab
def _domain_stats():
    week_ago = db.now() - 7 * 86400
    return {
        "total": db.scalar("SELECT COUNT(*) FROM domains"),
        "crawled": db.scalar("SELECT COUNT(*) FROM domains WHERE cc_pages>0"),
        "with_mx": db.scalar("SELECT COUNT(*) FROM domains WHERE mx_status='ok'"),
        "new_week": db.scalar("SELECT COUNT(*) FROM domains WHERE first_seen_ts>?", (week_ago,)),
        "leads": db.scalar("SELECT COUNT(DISTINCT domain) FROM leads WHERE domain<>''"),
    }


def r_domain_list(q):
    page = max(1, int(q.get("page") or 1))
    per = max(10, min(200, int(q.get("per") or 50)))
    search = (q.get("q") or "").strip()
    source = (q.get("source") or "").strip()
    where, args = [], []
    if search:
        where.append("d.domain LIKE ?")
        args.append("%%%s%%" % search)
    if source:
        where.append("d.source=?")
        args.append(source)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    total = db.scalar("SELECT COUNT(*) FROM domains d" + clause, tuple(args))
    rows = db.rows(
        "SELECT d.*, (SELECT COUNT(*) FROM leads l WHERE l.domain=d.domain) AS leads "
        "FROM domains d" + clause +
        " ORDER BY d.updated_ts DESC, d.domain LIMIT ? OFFSET ?",
        tuple(args) + (per, (page - 1) * per))
    for row in rows:
        raw = row.get("data") or "{}"
        try:
            row["data"] = json.loads(raw)
        except ValueError:
            row["data"] = {}
    return {"ok": True, "domains": rows, "total": total, "page": page,
            "pages": max(1, (total + per - 1) // per),
            "stats": _domain_stats(), "sources": domain_sources.describe()}


def r_domain_sync(p):
    """Copy every lead's domain into the catalogue - including leads that only have one."""
    return domain_sources.sync_lead_domains()


def r_domain_pull(p):
    source = str(p.get("source") or "").strip()
    if not source:
        return {"ok": False, "error": "Pick a source first."}
    try:
        limit = int(p.get("limit") or 5000)
    except (TypeError, ValueError):
        return {"ok": False, "error": "The limit must be a number."}
    return domain_sources.pull(source, limit=limit)


def r_domain_enrich(p):
    """Check a few domains against the current Common Crawl index, right now.

    Interactive on purpose, so it is small: the index is often slow (a 504 after half a
    minute is normal for a big host), and the answer arrives in the page, not in a log.
    """
    names = [str(d).strip().lower() for d in (p.get("domains") or []) if str(d).strip()]
    try:
        limit = max(1, min(3, int(p.get("limit") or 3)))
    except (TypeError, ValueError):
        limit = 3
    if names:
        rows = [row for row in (db.one("SELECT * FROM domains WHERE domain=?", (name,))
                                for name in names[:limit]) if row]
    else:
        rows = db.rows("SELECT * FROM domains WHERE cc_checked_ts=0 "
                       "ORDER BY first_seen_ts DESC LIMIT ?", (limit,))
    if not rows:
        return {"ok": True, "checked": 0, "hits": 0, "index": "", "errors": [],
                "note": "Nothing to check - every domain here already has a footprint."}
    try:
        index = cc_job.latest_index()
    except Exception as exc:                        # noqa: BLE001 - the index may be down
        return {"ok": False, "error": "Common Crawl index unavailable: %s" % exc}
    checked = hits = 0
    errors = []
    for row in rows:
        domain = row["domain"]
        try:
            body = net.text(cc_job.index_url(index, domain), timeout=15)
        except net.NetError as exc:
            if exc.status != 404:
                errors.append("%s: HTTP %s" % (domain, exc.status))
                continue
            body = ""
        except Exception as exc:                    # noqa: BLE001 - one bad domain only
            errors.append("%s: %s" % (domain, exc))
            continue
        parsed = cc_job.parse_cc_lines(body, domain)
        checked += 1
        if parsed["pages"]:
            hits += 1
        db.execute("UPDATE domains SET cc_pages=?, cc_last_crawl=?, cc_checked_ts=?, "
                   "updated_ts=? WHERE domain=?",
                   (parsed["pages"], parsed["last_crawl"], db.now(), db.now(), domain))
        time.sleep(0.3)
    db.event("domains", "footprint checked %d domains, %d with captures" % (checked, hits))
    return {"ok": True, "index": index, "checked": checked, "hits": hits, "errors": errors}


def r_domain_scrape(p):
    """Read the live site for contact details. Does not create leads on its own."""
    domain = net.domain_of(p.get("domain") or "") or str(p.get("domain") or "").strip().lower()
    if not domain or "." not in domain:
        return {"ok": False, "error": "That is not a domain."}
    try:
        pages = max(1, min(8, int(p.get("pages") or 4)))
    except (TypeError, ValueError):
        pages = 4
    result = crawl.crawl_domain(domain, max_pages=pages)
    if result.get("ok"):
        row = db.one("SELECT data FROM domains WHERE domain=?", (domain,))
        payload = {}
        try:
            payload = json.loads((row or {}).get("data") or "{}")
        except ValueError:
            payload = {}
        payload.update({"title": result["title"][:200], "description": result["description"][:300],
                        "scraped_ts": db.now(), "emails_found": len(result["emails"])})
        db.execute("UPDATE domains SET data=?, name=COALESCE(NULLIF(name,''), ?), updated_ts=? "
                   "WHERE domain=?", (json.dumps(payload, default=str),
                                      (result["title"] or "")[:120], db.now(), domain))
        db.event("domains", "scraped %s: %d emails" % (domain, len(result["emails"])))
    return result


def r_brands(q=None):
    rows = brands.list_brands()
    return {"ok": True, "brands": rows,
            "totals": {"brands": len(rows),
                       "live": sum(r.get("live", 0) for r in rows),
                       "findings": sum(r.get("findings", 0) for r in rows),
                       "open": sum(r.get("open", 0) for r in rows)}}


def r_brand_save(p):
    domain = net.domain_of(p.get("domain") or "")
    if not domain:
        return {"ok": False, "error": "Enter a domain, e.g. acme.com"}
    name = (p.get("name") or domain.split(".")[0]).strip()
    if p.get("id"):
        db.update("brands", p["id"], {"name": name[:120], "domain": domain,
                                      "aliases": (p.get("aliases") or "")[:400],
                                      "notes": (p.get("notes") or "")[:1000],
                                      "active": 1 if p.get("active", True) else 0})
        return {"ok": True, "id": p["id"]}
    brand_id = brands.add_brand(name, domain, p.get("aliases") or "", p.get("notes") or "")
    if not brand_id:
        return {"ok": False, "error": "That domain is already monitored or invalid."}
    return {"ok": True, "id": brand_id}


def r_brand_delete(p):
    if not p.get("id"):
        return {"ok": False, "error": "No brand selected."}
    db.execute("DELETE FROM brands WHERE id=?", (p["id"],))
    return {"ok": True}


def r_brand_import(p):
    added = brands.import_text(p.get("text") or "")
    return {"ok": True, "added": added}


def r_brand_scan(p):
    brand = db.one("SELECT * FROM brands WHERE id=?", (p.get("id"),))
    if not brand:
        return {"ok": False, "error": "That brand no longer exists."}
    ctx = Context(Job("brand_watch", "brand watch", "Signals", "", run=None), {})
    report = brands.scan_brand(
        brand, candidates_per_brand=int(p.get("candidates") or 40),
        min_score=int(p.get("min_score") or 4), pause=float(p.get("pause") or 0.08),
        include_nrd=bool(p.get("include_nrd", True)),
        include_phishing=bool(p.get("include_phishing", True)), ctx=ctx)
    db.event("brand_scan", "%s: %d findings" % (brand["domain"], report["count"]))
    return {"ok": True, **report}


def r_brand_cases(q):
    brand_id = q.get("brand_id")
    brand = db.one("SELECT * FROM brands WHERE id=?", (brand_id,)) if brand_id else None
    if not brand and q.get("domain"):
        brand = db.one("SELECT * FROM brands WHERE domain=?", (q["domain"],))
    if not brand:
        return {"ok": False, "error": "Choose a brand first."}
    limit = min(500, int(q.get("limit") or 200))
    rows = db.rows("SELECT * FROM signals WHERE company=? AND kind LIKE 'lookalike%' "
                   "ORDER BY id DESC LIMIT ?", (brand["name"], limit))
    return {"ok": True, "brand": brand, "cases": rows,
            "counts": {kind: db.scalar("SELECT COUNT(*) FROM signals WHERE company=? AND kind=?",
                                       (brand["name"], kind))
                       for kind in ("lookalike_live", "lookalike_nrd", "lookalike_listed")}}


def r_brand_cases_csv(q):
    brand_id = q.get("brand_id")
    brand = db.one("SELECT * FROM brands WHERE id=?", (brand_id,)) if brand_id else None
    if not brand:
        return "", 0
    rows = db.rows("SELECT * FROM signals WHERE company=? AND kind LIKE 'lookalike%' "
                   "ORDER BY id DESC", (brand["name"],))
    import csv as _csv
    import io as _io
    buf = _io.StringIO()
    writer = _csv.writer(buf)
    writer.writerow(["brand", "brand_domain", "lookalike_domain", "kind", "how", "score",
                     "has_mail", "found_at", "source_url"])
    for row in rows:
        payload = {}
        try:
            payload = json.loads(row.get("payload") or "{}")
        except ValueError:
            payload = {}
        writer.writerow([brand["name"], brand["domain"], row["domain"], row["kind"],
                         row["title"], payload.get("score", ""), payload.get("has_mail", ""),
                         row["ts"], row["source_url"]])
    return buf.getvalue(), len(rows)


def r_offers(q=None):
    rows = offers.list_offers()
    return {"ok": True, "offers": rows,
            "totals": {"offers": len(rows), "active": sum(1 for r in rows if r["active"]),
                       "with_link": sum(1 for r in rows if r.get("affiliate_url"))}}


def r_offer_save(p):
    return offers.save(p)


def r_offer_delete(p):
    if not p.get("id"):
        return {"ok": False, "error": "No offer selected."}
    return offers.delete(p["id"])


def r_offer_matches(q):
    offer = offers.get(q.get("offer_id"))
    if not offer:
        return {"ok": False, "error": "Choose an offer first."}
    limit = min(200, int(q.get("limit") or 50))
    return {"ok": True, "offer": offer,
            "matches": offers.matches(offer, limit=limit,
                                      min_score=int(q.get("min_score") or 2))}


def r_offer_import(p):
    if p.get("pack"):
        return offers.import_pack()
    rows = p.get("offers")
    if rows is None and p.get("text"):
        try:
            data = json.loads(p["text"])
        except ValueError:
            return {"ok": False, "error": "That is not valid JSON. Paste the offers array or the pack file."}
        rows = data.get("offers") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        return {"ok": False, "error": "Expected a list of offers."}
    return offers.import_rows(rows)


def r_offer_pitch(p):
    offer = offers.get(p.get("offer_id"))
    if not offer:
        return {"ok": False, "error": "Choose an offer first."}
    lead = None
    if p.get("lead_id"):
        lead = db.one("SELECT * FROM leads WHERE id=?", (p["lead_id"],))
        if not lead:
            return {"ok": False, "error": "That lead no longer exists."}
    return {"ok": True, **offers.render_pitch(offer, lead, use_ai=bool(p.get("ai")),
                                              provider_id=p.get("provider_id"))}


def r_offer_connection_add(p):
    return offers.add_connection(p)


def r_offer_connection_delete(p):
    if not p.get("id"):
        return {"ok": False, "error": "No connection selected."}
    return offers.delete_connection(p["id"])


def r_offer_connections(q):
    offer = offers.get(q.get("offer_id"))
    if not offer:
        return {"ok": False, "error": "Choose an offer first."}
    return {"ok": True, "connections": offers.connections(offer["id"]), "offer": offer}


def r_llm_providers(q=None):
    return {"ok": True, "providers": llm.list_providers(), "presets": llm.presets(),
            "status": llm.status()}


def r_llm_save(p):
    return llm.save(p)


def r_llm_delete(p):
    if not p.get("id"):
        return {"ok": False, "error": "No provider selected."}
    return llm.delete(p["id"])


def r_llm_default(p):
    if not p.get("id"):
        return {"ok": False, "error": "No provider selected."}
    return llm.set_default(p["id"])


def r_llm_test(p):
    return llm.test(p.get("id"))


OAUTH_PENDING = {}


def r_match(p):
    return match.match(text=p.get("text"), url=p.get("url"),
                       limit=min(200, int(p.get("limit") or 25)),
                       use_ai=bool(p.get("ai", True)),
                       min_score=int(p.get("min_score") or 2))


def r_chat(p):
    return chat.run(p.get("message"), history=p.get("history") or [],
                    provider_id=p.get("provider_id"))


def r_oauth_status(q=None):
    return {"ok": True, **oauth.status()}


def r_oauth_start(p):
    provider = (p.get("provider") or "google").strip().lower()
    mailbox_id = p.get("mailbox_id")
    if not mailbox_id:
        return {"ok": False, "error": "Save the mailbox first, then connect it."}
    if not db.one("SELECT id FROM mailboxes WHERE id=?", (mailbox_id,)):
        return {"ok": False, "error": "That mailbox no longer exists."}
    try:
        url, state = oauth.auth_url(provider, port=PORT)
    except oauth.OAuthError as exc:
        return {"ok": False, "error": str(exc)}
    OAUTH_PENDING[state] = {"mailbox_id": mailbox_id, "provider": provider,
                            "ts": db.now()}
    return {"ok": True, "url": url, "state": state}


def r_oauth_callback(q):
    state = (q.get("state") or "").strip()
    code = (q.get("code") or "").strip()
    error = (q.get("error") or "").strip()
    pending = OAUTH_PENDING.pop(state, None)
    if error:
        return _oauth_page("Connection cancelled", "The provider said: %s" % error, False)
    if not pending or not code:
        return _oauth_page("Connection failed",
                           "That sign-in link expired or was already used. Start again from Mailboxes.",
                           False)
    try:
        tokens = oauth.exchange_code(pending["provider"], code, port=PORT)
        email = oauth.account_email(pending["provider"], tokens.get("access_token"))
        conf = oauth.PROVIDERS[pending["provider"]]
        mailbox = db.one("SELECT * FROM mailboxes WHERE id=?", (pending["mailbox_id"],))
        patch = {"auth_type": "oauth", "oauth_provider": pending["provider"],
                 "oauth_access": tokens["access_token"],
                 "oauth_refresh": tokens["refresh_token"],
                 "oauth_expires": tokens["expires_at"],
                 "smtp_host": mailbox.get("smtp_host") or conf["smtp_host"],
                 "smtp_port": conf["smtp_port"],
                 "smtp_security": "starttls",
                 "imap_host": mailbox.get("imap_host") or conf["imap_host"],
                 "imap_port": conf["imap_port"]}
        if email:
            patch.update({"oauth_email": email,
                          "smtp_user": mailbox.get("smtp_user") or email,
                          "imap_user": mailbox.get("imap_user") or email,
                          "from_email": mailbox.get("from_email") or email})
        db.update("mailboxes", pending["mailbox_id"], patch)
        db.event("oauth", "%s connected for mailbox %s" % (pending["provider"], pending["mailbox_id"]))
        return _oauth_page("Connected", "%s is connected. You can close this tab and go back to "
                                        "Lead Studio." % (email or "The mailbox"), True)
    except oauth.OAuthError as exc:
        return _oauth_page("Connection failed", str(exc), False)


def _oauth_page(title, message, ok):
    colour = "#1f9d55" if ok else "#c0392b"
    safe_title = html.escape(str(title))
    return ("<!doctype html><meta charset=utf-8><title>%s</title>"
            "<body style=\"font:16px/1.6 system-ui;max-width:560px;margin:80px auto;color:#1d2330\">"
            "<h2 style=\"color:%s\">%s</h2><p>%s</p></body>" % (
                safe_title, colour, safe_title, html.escape(str(message))))


# ------------------------------------------------------------------ Social tab
def r_social_state():
    return {"ok": True, "video": video.state(), "metricool": metricool.status(), "port": PORT}


def r_social_workspace(q):
    """What this Metricool account can answer: tools discovered, sections they cover."""
    if not metricool.status()["connected"]:
        return {"ok": False, "connected": False,
                "error": "Connect Metricool to see what this account exposes."}
    try:
        data = metricool.workspace(refresh=str(q.get("refresh") or "") == "1")
    except metricool.MetricoolError as exc:
        return {"ok": False, "connected": True, "error": str(exc)}
    data["connected"] = True
    return data


def r_social_section(q):
    """Run the tool behind one section (inbox, planner, ads, reports)."""
    if not metricool.status()["connected"]:
        return {"ok": False, "connected": False, "error": "Connect Metricool first."}
    name = str(q.get("name") or "")
    params = {}
    for key in ("brand", "from", "limit", "network", "status", "to"):
        if q.get(key):
            params[key] = q.get(key)
    raw = q.get("params")
    if raw:
        try:
            extra = json.loads(raw)
        except ValueError:
            return {"ok": False, "error": "Those parameters are not valid JSON."}
        if isinstance(extra, dict):
            params.update(extra)
    try:
        return metricool.section(name, params)
    except metricool.MetricoolError as exc:
        return {"ok": False, "error": str(exc)}


def r_social_reply(p):
    try:
        return metricool.reply(str(p.get("conversation") or ""), str(p.get("text") or ""),
                               str(p.get("network") or ""), p.get("extra") or None)
    except metricool.MetricoolError as exc:
        return {"ok": False, "error": str(exc)}


def r_social_task(q):
    task = tasks.get(q.get("id") or "")
    return {"ok": bool(task), "task": task,
            "error": "" if task else "That render is no longer in memory."}


def r_social_upload(p):
    encoded = p.get("b64") or ""
    if not encoded:
        return {"ok": False, "error": "No file received."}
    try:
        raw = base64.b64decode(str(encoded).split(",")[-1])
    except (ValueError, TypeError):
        return {"ok": False, "error": "That file could not be decoded."}
    return video.save_upload(p.get("name") or "", raw)


def r_social_remove(p):
    return video.remove(p.get("kind") or "input", p.get("name") or "")


def r_social_make(p):
    task_id, error = video.make(p.get("spec") or p)
    if error:
        return {"ok": False, "error": error}
    return {"ok": True, "task": task_id}


def r_social_cancel(p):
    return {"ok": tasks.cancel(p.get("id") or "")}


def r_metricool_start(p):
    try:
        return {"ok": True, **metricool.begin(PORT)}
    except metricool.MetricoolError as exc:
        return {"ok": False, "error": str(exc)}


def r_metricool_tools(p):
    try:
        return {"ok": True, "tools": metricool.tools()}
    except metricool.MetricoolError as exc:
        return {"ok": False, "error": str(exc)}


def r_metricool_call(p):
    name = str(p.get("tool") or "").strip()
    if not name:
        return {"ok": False, "error": "Pick a tool first."}
    try:
        return metricool.call(name, p.get("arguments") or {})
    except metricool.MetricoolError as exc:
        return {"ok": False, "error": str(exc)}


def r_metricool_schedule(p):
    caption = str(p.get("caption") or "")
    if not caption.strip():
        return {"ok": False, "error": "Write the caption first."}
    try:
        return metricool.schedule(caption, str(p.get("when") or ""), p.get("networks") or [],
                                  str(p.get("media_url") or ""), p.get("extra") or None)
    except metricool.MetricoolError as exc:
        return {"ok": False, "error": str(exc)}


def r_metricool_analytics(p):
    try:
        return metricool.analytics(str(p.get("network") or ""), str(p.get("from") or ""),
                                   str(p.get("to") or ""), p.get("metrics") or None,
                                   p.get("extra") or None)
    except metricool.MetricoolError as exc:
        return {"ok": False, "error": str(exc)}


ROUTES_GET = {
    "/api/state": lambda q: r_state(),
    "/api/leads": r_leads,
    "/api/enrollments": r_enrollments,
    "/api/inbox": r_inbox,
    "/api/events": r_events,
    "/api/diagnose": lambda q: r_diagnose(),
    "/api/jobs": r_jobs,
    "/api/jobs/runs": r_job_runs,
    "/api/signals": r_signals,
    "/api/domains": r_domains,
    "/api/domain/list": r_domain_list,
    "/api/mcp/status": r_mcp_status,
    "/api/mcp/items": r_mcp_items,
    "/api/manyreach/status": r_manyreach_status,
    "/api/ads": r_ads,
    "/api/hyros": r_hyros_status,
    "/api/brands": r_brands,
    "/api/brand/cases": r_brand_cases,
    "/api/offers": r_offers,
    "/api/offer/matches": r_offer_matches,
    "/api/offer/connections": r_offer_connections,
    "/api/llm/providers": r_llm_providers,
    "/api/oauth/status": r_oauth_status,
    "/api/oauth/start": r_oauth_start,
    "/api/social/state": lambda q: r_social_state(),
    "/api/social/task": r_social_task,
    "/api/social/workspace": r_social_workspace,
    "/api/social/section": r_social_section,
}

ROUTES_POST = {
    "/api/lead/save": r_lead_save,
    "/api/lead/delete": r_lead_delete,
    "/api/lead/status": r_lead_status,
    "/api/lead/import": r_lead_import,
    "/api/lead/import/path": r_lead_import_path,
    "/api/import/progress": r_import_progress,
    "/api/lead/inspect": r_lead_inspect,
    "/api/dialog/pick": r_pick_file,
    "/api/domain/pull": r_domain_pull,
    "/api/domain/sync": r_domain_sync,
    "/api/domain/enrich": r_domain_enrich,
    "/api/domain/scrape": r_domain_scrape,
    "/api/mcp/start": r_mcp_start,
    "/api/mcp/tools": r_mcp_tools,
    "/api/mcp/call": r_mcp_call,
    "/api/mcp/pull": r_mcp_pull,
    "/api/mcp/custom": r_mcp_custom,
    "/api/manyreach/key": r_manyreach_key,
    "/api/manyreach/sync": r_manyreach_sync,
    "/api/manyreach/import": r_manyreach_import,
    "/api/ads/save": r_ads_save,
    "/api/ads/test": r_ads_test,
    "/api/ads/pull": r_ads_pull,
    "/api/ads/hyros/accounts": r_hyros_accounts,
    "/api/ads/hyros/attribution": r_hyros_attribution,
    "/api/ads/hyros/mcp": r_hyros_mcp,
    "/api/hyros": r_hyros_status,
    "/api/hyros/pull": r_hyros_pull,
    "/api/hyros/paths": r_hyros_paths,
    "/api/mcp/disconnect": r_mcp_disconnect,
    "/api/lead/verify": r_lead_verify,
    "/api/sequence/save": r_seq_save,
    "/api/sequence/delete": r_seq_delete,
    "/api/enroll": r_enroll,
    "/api/enrollment/status": r_enroll_status,
    "/api/send/tick": r_tick,
    "/api/send/test": r_send_test,
    "/api/mailbox/save": r_box_save,
    "/api/mailbox/delete": r_box_delete,
    "/api/mailbox/test": r_box_test,
    "/api/inbox/sync": r_inbox_sync,
    "/api/inbox/seen": r_inbox_seen,
    "/api/settings/save": r_settings_save,
    "/api/wipe": r_wipe,
    "/api/demo/seed": lambda p: demo.seed(),
    "/api/demo/remove": lambda p: demo.remove(),
    "/api/jobs/run": lambda p: scheduler.run_job(p.get("key"), trigger="manual"),
    "/api/jobs/update": r_job_update,
    "/api/signals/seen": r_signal_seen,
    "/api/signals/promote": r_signal_promote,
    "/api/signals/draft": r_signal_draft,
    "/api/signals/campaign": r_signal_campaign,
    "/api/signals/contact": r_signal_contact,
    "/api/brand/save": r_brand_save,
    "/api/brand/delete": r_brand_delete,
    "/api/brand/import": r_brand_import,
    "/api/brand/scan": r_brand_scan,
    "/api/offer/save": r_offer_save,
    "/api/offer/delete": r_offer_delete,
    "/api/offer/pitch": r_offer_pitch,
    "/api/offer/import": r_offer_import,
    "/api/offer/connection/add": r_offer_connection_add,
    "/api/offer/connection/delete": r_offer_connection_delete,
    "/api/llm/save": r_llm_save,
    "/api/llm/delete": r_llm_delete,
    "/api/llm/default": r_llm_default,
    "/api/llm/test": r_llm_test,
    "/api/match": r_match,
    "/api/chat": r_chat,
    "/api/social/upload": r_social_upload,
    "/api/social/remove": r_social_remove,
    "/api/social/make": r_social_make,
    "/api/social/cancel": r_social_cancel,
    "/api/social/reply": r_social_reply,
    "/api/social/metricool/start": r_metricool_start,
    "/api/social/metricool/disconnect": lambda p: metricool.disconnect(),
    "/api/social/metricool/tools": r_metricool_tools,
    "/api/social/metricool/call": r_metricool_call,
    "/api/social/metricool/schedule": r_metricool_schedule,
    "/api/social/metricool/analytics": r_metricool_analytics,
    "/api/osm/search": r_osm_search,
    "/api/osm/import": r_osm_import,
    "/api/osm/status": r_osm_status,
    "/api/hot/today": r_hot_today,
    "/api/osint/state": r_osint_state,
    "/api/osint/scan": r_osint_scan,
    "/api/osint/profile": r_osint_profile,
    "/api/osint/meta-ads": r_osint_meta_ads,
    "/api/osint/dorks": r_osint_dorks,
    "/api/osint/discover": r_osint_discover,
    "/api/osint/enrich": r_osint_enrich,
}


# ------------------------------------------------------------------ worker
def worker_loop(stop_event):
    last_sync = 0
    with WORKER_LOCK:
        WORKER["running"] = True
        WORKER["started"] = int(time.time())
    while not stop_event.is_set():
        try:
            res = pipeline.tick()
            with WORKER_LOCK:
                WORKER["last_tick"] = int(time.time())
                WORKER["last_tick_result"] = res
        except Exception as e:
            config.log("worker tick failed: %s\n%s" % (e, traceback.format_exc()))
        if time.time() - last_sync > 600:
            try:
                res = pipeline.sync_inbox()
                last_sync = time.time()
                with WORKER_LOCK:
                    WORKER["last_sync"] = int(last_sync)
                    WORKER["last_sync_result"] = res
            except Exception as e:
                config.log("worker sync failed: %s" % e)
        stop_event.wait(20)
    with WORKER_LOCK:
        WORKER["running"] = False


# ---------------------------------------------------------------- handler
class Handler(BaseHTTPRequestHandler):
    server_version = "LeadStudioLite/" + config.VERSION
    sys_version = ""

    def log_message(self, fmt, *args):
        if os.environ.get("LS_DEBUG"):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # ---- helpers
    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _text(self, body, code=200, ctype="text/html; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _track(self, path, query):
        """Open pixel, click redirect and unsubscribe page - addressed by their token."""
        if path.startswith("/t/o/"):
            pipeline.record_open(path[5:].replace(".gif", ""))
            return self._text(PIXEL, 200, "image/gif",
                              {"Cache-Control": "no-store, max-age=0",
                               "Referrer-Policy": "no-referrer"})
        if path.startswith("/t/u/"):
            pipeline.unsubscribe(path[5:])
            return self._text(
                "<!doctype html><meta charset=utf-8><title>Unsubscribed</title>"
                "<body style=\"font:16px/1.6 system-ui;max-width:520px;margin:80px auto;color:#222\">"
                "<h2>You're unsubscribed</h2><p>You won't receive any more emails from this sender.</p>",
                200, "text/html; charset=utf-8", {"Referrer-Policy": "no-referrer"})
        if path.startswith("/t/c/"):
            pipeline.record_click(path[5:])
            target = (query.get("u") or "").strip()
            if not target.lower().startswith(("http://", "https://")):
                target = "/"
            return self._text("", 302, "text/plain",
                              {"Location": target, "Referrer-Policy": "no-referrer"})
        if path.startswith("/t/v/"):
            # A finished video, reachable through tracking.base_url so Metricool can fetch
            # it when scheduling. Only files that exist in the video output folder, by name.
            return self._send_video(urllib.parse.unquote(path[5:]))
        return self._json({"ok": False, "error": "Unknown link."}, 404)

    def _stream(self, full):
        try:
            size = os.path.getsize(full)
            handle = open(full, "rb")
        except OSError:
            return self._json({"ok": False, "error": "That file could not be opened."}, 404)
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        with handle:
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition",
                             'inline; filename="%s"' % os.path.basename(full))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            while True:
                chunk = handle.read(256 * 1024)
                if not chunk:
                    break
                self.wfile.write(chunk)

    def _send_video(self, name):
        full = video.file_for("output", name)
        if not full:
            return self._json({"ok": False, "error": "No such video."}, 404)
        return self._stream(full)

    def _offer_page(self, path):
        try:
            offer_id = int(path.rsplit("/", 1)[-1])
        except ValueError:
            return self._json({"ok": False, "error": "Unknown offer."}, 404)
        offer = offers.get(offer_id)
        if not offer:
            return self._text("<!doctype html><meta charset=utf-8><h1>Offer not found</h1>", 404)
        return self._text(offers.build_page_html(offer, config.load_settings()), 200,
                          "text/html; charset=utf-8", {"X-Content-Type-Options": "nosniff"})

    def _local_request(self):
        """Only answer requests addressed to this machine (blocks DNS rebinding)."""
        host = (self.headers.get("Host") or "").split(":")[0].strip().lower()
        return host in ("127.0.0.1", "localhost", "::1", "[::1]", "")

    def _raw(self):
        """Read the request body once, always - leaving it unread makes Windows
        reset the connection instead of delivering the response.

        Returns None (after answering) when the declared length is unusable, so the
        caller just stops: an unparsable Content-Length used to raise before any
        response went out, and the browser got a dropped connection instead of JSON.
        """
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            self.close_connection = True
            self._json({"ok": False, "error": "Bad request: invalid Content-Length."}, 400)
            return None
        if length <= 0:
            return b""
        if length > MAX_REQUEST_BYTES:
            self.close_connection = True
            self._json({"ok": False, "error": "That request is too large (limit %d MB)."
                        % (MAX_REQUEST_BYTES // (1024 * 1024))}, 413)
            return None
        return self.rfile.read(length)

    # ---- GET
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = dict(urllib.parse.parse_qsl(parsed.query))

        # Recipients open these through tracking.base_url, which is a public host (a tunnel,
        # a domain): the Host check below only guards the local API and the UI itself.
        # The token in the URL is the capability.
        if path.startswith("/t/"):
            return self._track(path, query)
        if path.startswith("/offer/"):
            return self._offer_page(path)

        if not self._local_request():
            return self._json({"ok": False, "error": "Not available."}, 403)

        if path == "/oauth/callback":
            try:
                return self._text(r_oauth_callback(query), 200, "text/html; charset=utf-8",
                                  PAGE_HEADERS)
            except Exception as exc:                 # noqa: BLE001
                config.log("oauth callback failed: %s" % exc)
                return self._text("<!doctype html><meta charset=utf-8>"
                                  "<h2>Connection failed</h2><p>%s</p>" % html.escape(str(exc)),
                                  500, "text/html; charset=utf-8", PAGE_HEADERS)

        if path == "/oauth/metricool/callback":
            try:
                result = metricool.finish(query)
            except metricool.MetricoolError as exc:
                result = {"ok": False, "error": str(exc)}
            ok = bool(result.get("ok"))
            page = _oauth_page(
                "Connected" if ok else "Connection failed",
                "Metricool is connected. You can close this tab and go back to Lead Studio."
                if ok else str(result.get("error") or "Metricool did not accept the sign-in."),
                ok)
            return self._text(page, 200, "text/html; charset=utf-8", PAGE_HEADERS)

        if path.startswith("/oauth/mcp/"):
            parts = path.strip("/").split("/")          # oauth / mcp / <name> / callback
            name = parts[2] if len(parts) >= 3 else ""
            try:
                result = mcp_client.finish(name, query)
            except mcp_client.McpError as exc:
                result = {"ok": False, "error": str(exc)}
            ok = bool(result.get("ok"))
            label = (mcp_client.CONNECTIONS.get(name) or {}).get("label") or name.title()
            page = _oauth_page(
                "Connected" if ok else "Connection failed",
                "%s is connected. You can close this tab and go back to Lead Studio." % label
                if ok else str(result.get("error") or "The sign-in was refused."),
                ok)
            return self._text(page, 200, "text/html; charset=utf-8", PAGE_HEADERS)

        if path == "/api/social/file":
            return self._send_file(query)

        if path in ROUTES_GET:
            try:
                return self._json(ROUTES_GET[path](query))
            except Exception as e:
                config.log("GET %s failed: %s\n%s" % (path, e, traceback.format_exc()))
                return self._json({"ok": False, "error": str(e)}, 500)

        if path == "/export.csv":
            status = query.get("status") or ""
            data, n = importer.export_csv(status=status, search=query.get("q") or "")
            return self._text(data, 200, "text/csv; charset=utf-8",
                              {"Content-Disposition": "attachment; filename=leads.csv"})

        if path in ("/export.parquet", "/export.duckdb"):
            try:
                if path.endswith(".parquet"):
                    data = importer.export_parquet(status=query.get("status") or "",
                                                   search=query.get("q") or "")
                    fname, ctype = "leads.parquet", "application/octet-stream"
                else:
                    data = importer.export_duckdb(status=query.get("status") or "",
                                                  search=query.get("q") or "")
                    fname, ctype = "leads.duckdb", "application/octet-stream"
            except ValueError as exc:
                return self._json({"ok": False, "error": str(exc)}, 400)
            return self._text(data, 200, ctype,
                              {"Content-Disposition": "attachment; filename=" + fname})

        if path == "/brand/cases.csv":
            data, n = r_brand_cases_csv(query)
            if not data:
                return self._json({"ok": False, "error": "Choose a brand first."}, 400)
            return self._text(data, 200, "text/csv; charset=utf-8",
                              {"Content-Disposition": "attachment; filename=brand-cases.csv"})

        return self._static(path)

    # ---- POST
    def do_POST(self):
        raw = self._raw()          # drain first, so early returns still answer cleanly
        if raw is None:            # unusable Content-Length / body too large - already answered
            return
        path = urllib.parse.urlparse(self.path).path
        # RFC 8058 one-click unsubscribe: Gmail and Outlook POST to the https address from
        # the List-Unsubscribe header. The token in the URL is the capability, and the
        # recipient is not on this machine, so neither the Host nor the session check applies.
        if path.startswith("/t/u/"):
            pipeline.unsubscribe(path[5:])
            return self._text("ok", 200, "text/plain", {"Referrer-Policy": "no-referrer"})
        if not self._local_request():
            return self._json({"ok": False, "error": "Not available."}, 403)
        token = config.load_settings().get("session_token")
        if (self.headers.get("X-LS-Token") or "") != token:
            return self._json({"ok": False, "error": "Session expired. Refresh the page."}, 403)
        handler = ROUTES_POST.get(path)
        if not handler:
            return self._json({"ok": False, "error": "Unknown endpoint."}, 404)
        payload = {}
        if raw.strip():
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return self._json({"ok": False, "error": "Malformed request."}, 400)
        try:
            return self._json(handler(payload))
        except Exception as e:
            config.log("POST %s failed: %s\n%s" % (path, e, traceback.format_exc()))
            return self._json({"ok": False, "error": str(e)}, 500)

    def _send_file(self, query):
        """Serve an uploaded file or a finished video from the video folder."""
        kind = "input" if (query.get("kind") or "") == "input" else "output"
        full = video.file_for(kind, query.get("name") or "")
        if not full:
            return self._json({"ok": False, "error": "That file is not there."}, 404)
        return self._stream(full)

    # ---- static files
    def _static(self, path):
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        if rel not in ("index.html", "app.js", "styles.css", "favicon.svg", "logo.svg"):
            rel = "index.html"
        full = os.path.join(config.UI_DIR, rel)
        if not os.path.isfile(full):
            return self._text("<!doctype html><h1>Lead Studio Lite</h1><p>UI files are missing. "
                              "Reinstall the folder.</p>", 404)
        ctype = {"index.html": "text/html; charset=utf-8", "app.js": "text/javascript; charset=utf-8",
                 "styles.css": "text/css; charset=utf-8",
                 "favicon.svg": "image/svg+xml", "logo.svg": "image/svg+xml"}.get(
            rel) or mimetypes.guess_type(rel)[0] or "application/octet-stream"
        with open(full, "rb") as fh:
            body = fh.read()
        if rel == "index.html":
            token = config.load_settings().get("session_token") or ""
            body = body.replace(b"__LS_TOKEN__", token.encode())
            headers = {"X-Frame-Options": "DENY",
                       "Referrer-Policy": "no-referrer",
                       "Content-Security-Policy":
                           "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                           "script-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'"}
        else:
            headers = {"Cache-Control": "no-store"}
        return self._text(body, 200, ctype, headers)


def _port_free(port, host="127.0.0.1"):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def make_server(port, host="127.0.0.1"):
    return ThreadingHTTPServer((host, port), Handler)


PORT = config.DEFAULT_PORT


def serve(port=None, open_browser=True, window=False, tray=True):
    global PORT
    db.init_db()
    net.set_contact(config.load_settings().get("contact_email"))
    scheduler.start()
    port = int(port or config.DEFAULT_PORT)
    for candidate in [port] + list(range(port + 1, port + 12)):
        if _port_free(candidate):
            port = candidate
            break
    PORT = port
    httpd = make_server(port)
    httpd.daemon_threads = True
    stop = threading.Event()
    threading.Thread(target=worker_loop, args=(stop,), daemon=True).start()
    url = "http://127.0.0.1:%d/" % port
    print("=" * 62)
    print("  %s %s" % (config.APP_NAME, config.VERSION))
    print("  %s" % url)
    print("  Files stay on this computer: %s" % config.DATA_DIR)
    print("  Close this window to stop the app (or press Ctrl+C).")
    print("=" * 62)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    tray_icon = None
    if tray:
        from . import tray as tray_mod
        if tray_mod.available():
            def _quit_from_tray():
                stop.set()
                try:
                    httpd.shutdown()
                except Exception:                      # noqa: BLE001
                    pass
                os._exit(0)
            try:
                tray_icon = tray_mod.start(url, _quit_from_tray)
                print("  Tray icon active - right-click it to quit.")
            except Exception as exc:                   # noqa: BLE001
                print("  Tray icon unavailable: %s" % exc)
        else:
            print("  %s" % tray_mod.reason())

    if window:
        from . import window as window_mod
        if window_mod.available():
            print("  Opening the app window (%s)..." % window_mod.reason())
            started = time.time()
            try:
                window_mod.run(url)
            except Exception as exc:                    # noqa: BLE001 - never block start-up
                print("  Could not open the window: %s" % exc)
                print("  Falling back to the browser.")
                open_browser = True
            else:
                if time.time() - started >= 2:
                    if tray_icon:
                        try:
                            tray_icon.stop()
                        except Exception:              # noqa: BLE001
                            pass
                    stop.set()
                    httpd.shutdown()
                    httpd.server_close()
                    return
                print("  The window closed immediately - falling back to the browser.")
                open_browser = True
        else:
            print("  pywebview is not installed - opening the browser instead.")
            open_browser = True

    if open_browser:
        threading.Timer(1.2, lambda: _open_browser(url)).start()
    try:
        while True:
            time.sleep(0.4)
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        stop.set()
        httpd.shutdown()
        httpd.server_close()


def _open_browser(url):
    import webbrowser
    try:
        webbrowser.open(url)
    except Exception:
        pass
