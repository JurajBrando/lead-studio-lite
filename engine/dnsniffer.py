"""Keeping only the domains DNSniffer lists as live websites.

DNSniffer indexes close to 380 million domains seen in zone files and Certificate
Transparency. A domain in that index is a registration that has been seen live; a domain
that is not there cannot be contacted by any later pass, so it goes into the same
quarantine the junk cleaner uses. The leads table is never touched, and a domain that
already has a lead is kept even when the index does not know it - a contact proves it is
real.

This is a "seen live" test, not an HTTP probe: DNSniffer does not fetch the page. The
optional `require_cert` rule also removes a domain that is indexed but has never shown a
certificate. It is off by default: the certificate column has gaps (DNSniffer's own
`dnsniffer.com` has none), so it is the strictest reading, not the safe one.

Two ways in, picked automatically:

* With an API key (Settings > Connectors, or the field on the Domains card) each check is
  one exact REST call, `GET /api/v1/domains/<name>`; 404 means "not in the index".
* Without a key the public MCP tool `search_domains` is free, but it is a substring
  search capped at 500 rows. Only a complete answer that lacks the exact name counts as
  missing, so an answer the server truncates makes the check slower, never wrong.
"""
import json
import threading
import urllib.parse

from . import config, db, domain_clean, mcp_client, net

API = "https://dnsniffer.com/api/v1"
MCP_NAME = "dnsniffer"
MCP_PAGE = 500

_LOCAL = threading.local()


class DnsError(Exception):
    """The key was refused: every further check would fail the same way."""


class RateLimited(Exception):
    """The server asked us to slow down; the batch stops instead of hammering it."""


def api_key():
    connectors = config.load_settings().get("connectors") or {}
    return str(connectors.get("dnsniffer_key") or "").strip()


def status():
    """What the Domains card shows: is there a key, and did the MCP connection work."""
    key = bool(api_key())
    connected = False
    try:
        connected = bool(mcp_client.status(MCP_NAME).get("connected"))
    except Exception:                          # noqa: BLE001 - a badge is never fatal
        connected = False
    return {"api_key": key, "mcp_connected": connected,
            "mode": "api" if key else "mcp"}


# ------------------------------------------------------------------ REST
def _rest(domain, key, require_cert):
    url = "%s/domains/%s" % (API, urllib.parse.quote(domain, safe=""))
    try:
        raw = net.fetch(url, timeout=20, retries=0,
                        headers={"Authorization": "Bearer " + key,
                                 "Accept": "application/json"})
    except net.NetError as exc:
        if exc.status == 404:
            return {"state": "missing"}
        if exc.status in (401, 403):
            raise DnsError("DNSniffer refused the API key (HTTP %d). Paste a current key "
                           "on the Domains card." % exc.status)
        if exc.status == 429:
            raise RateLimited("DNSniffer rate limit reached (HTTP 429).")
        return {"state": "unknown", "detail": str(exc)}
    try:
        payload = json.loads(raw.decode("utf-8", "replace") or "{}")
    except ValueError:
        return {"state": "unknown", "detail": "unreadable answer"}
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return {"state": "unknown", "detail": "answer without data"}
    seen = (data.get("cert_last_seen") or data.get("cert_expires_at")
            or data.get("cert_valid_from") or "")
    if seen:
        return {"state": "live", "detail": str(seen)}
    return {"state": "nocert" if require_cert else "live", "detail": "no certificate seen"}


# ------------------------------------------------------------------ MCP (free, no key)
def _client():
    client = getattr(_LOCAL, "client", None)
    if client is None:
        client = mcp_client.session(MCP_NAME)
        _LOCAL.client = client
    return client


def _mcp(domain, require_cert):
    arguments = {"query": domain, "limit": MCP_PAGE}
    try:
        result = _client().call("search_domains", arguments)
    except mcp_client.McpError as exc:
        text = str(exc)
        if "429" in text:
            raise RateLimited("DNSniffer rate limit reached (HTTP 429).")
        return {"state": "unknown", "detail": text[:200]}
    text = "\n".join(str(item.get("text") or "")
                     for item in (result or {}).get("content") or []
                     if isinstance(item, dict) and item.get("type") == "text")
    if (result or {}).get("isError"):
        return {"state": "unknown", "detail": text[:200] or "the tool reported an error"}
    try:
        data = json.loads(text or "{}")
    except ValueError:
        return {"state": "unknown", "detail": "unreadable answer"}
    if not isinstance(data, dict):
        return {"state": "unknown", "detail": "unreadable answer"}
    if isinstance(data.get("result"), dict):
        data = data["result"]
    items = data.get("domains") or []
    for item in items:
        name = str((item or {}).get("name") or "").strip().lower()
        if name == domain:
            seen = ((item or {}).get("cert_last_seen") or (item or {}).get("cert_expires_at")
                    or (item or {}).get("cert_valid_from") or "")
            if seen:
                return {"state": "live", "detail": str(seen)}
            return {"state": "nocert" if require_cert else "live",
                    "detail": "no certificate seen"}
    if data.get("has_more"):
        return {"state": "unknown", "detail": "the search answer was truncated"}
    return {"state": "missing"}


def check(domain, mode, key, require_cert):
    """One domain: live, missing, nocert, junk, or unknown when the answer cannot decide."""
    reason = domain_clean.junk_reason(domain)
    if reason:
        return {"state": "junk", "reason": reason}
    try:
        if mode == "api":
            return _rest(domain, key, require_cert)
        return _mcp(domain, require_cert)
    except RateLimited as exc:
        return {"state": "unknown", "rate": str(exc)}
    except DnsError as exc:
        return {"state": "unknown", "fatal": str(exc)}
    except Exception as exc:                   # noqa: BLE001 - one bad name is not fatal
        return {"state": "unknown", "detail": str(exc)[:200]}


def _where(refresh):
    clause = "domain<>''"
    if not refresh:
        clause += " AND data NOT LIKE '%\"dnsniffer_ts\"%'"
    return clause


def _scopes(ctx, refresh):
    """The rows to check: the whole catalogue, a batch, or the domains named by hand."""
    where = _where(refresh)
    only = [name.strip().lower() for name in ctx.list_param("domains") if name.strip()]
    params = ()
    if only:
        where = "(%s) AND domain IN (%s)" % (where, ",".join("?" for _ in only))
        params = tuple(only)
    all_flag = bool(ctx.param("all", False))
    limit = ctx.int_param("limit", 0)
    if not all_flag and limit <= 0:
        limit = 500
    return where, params, limit


def _preview(ctx, mode, key, require_cert, sample, workers):
    """Check a sample, so the cost is known before anything is removed."""
    from concurrent.futures import ThreadPoolExecutor

    rows = db.rows("SELECT domain FROM domains WHERE domain<>'' LIMIT ?",
                   (max(5, int(sample)),))
    names = [row["domain"] for row in rows]
    out = {"checked": len(names), "live": 0, "missing": 0, "nocert": 0, "junk": 0,
           "unknown": 0}
    if not names:
        ctx.log("preview: the catalogue has no domains to check")
        return out
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda name: check(name, mode, key, require_cert), names))
    error = ""
    for result in results:
        state = result.get("state")
        if state in out:
            out[state] += 1
        if not error:
            error = result.get("fatal") or result.get("rate") or ""
    gone = out["missing"] + out["nocert"] + out["junk"]
    out["would_remove"] = gone
    out["would_remove_pct"] = round(100.0 * gone / len(names), 1)
    ctx.log("preview: %d sampled | %d live | %d not in the index | %d without a "
            "certificate | %d junk | %d unknown | %.1f%% would be removed" % (
                len(names), out["live"], out["missing"], out["nocert"], out["junk"],
                out["unknown"], out["would_remove_pct"]))
    if error:
        out["error"] = error
        ctx.warn(error)
    return out


def _mark(rows):
    """Remember that these domains were checked, so the next run continues where this stopped."""
    now = db.now()
    patch = []
    for row in rows:
        try:
            data = json.loads(row.get("data") or "{}")
        except ValueError:
            data = {}
        data["dnsniffer_ts"] = now
        patch.append((json.dumps(data, ensure_ascii=False), now, row["domain"]))
    if patch:
        db.execute_many("UPDATE domains SET data=?, updated_ts=? WHERE domain=?", patch)


def _leads_in(names):
    """Which of these catalogue rows already produced a lead; those are never removed."""
    found = set()
    for start in range(0, len(names), 400):
        chunk = names[start:start + 400]
        found.update(row["domain"] for row in db.rows(
            "SELECT DISTINCT domain FROM leads WHERE domain IN (%s)"
            % ",".join("?" for _ in chunk), chunk))
    return found


def run(ctx, dry_run=False, require_cert=False, workers=8, batch=200, sample=200,
        refresh=False):
    """Walk the catalogue once against DNSniffer and quarantine what it does not list."""
    from concurrent.futures import ThreadPoolExecutor

    key = api_key()
    mode = "api" if key else "mcp"
    where, params, limit = _scopes(ctx, refresh)
    total = db.scalar("SELECT COUNT(*) FROM domains WHERE " + where, params)
    if limit > 0:
        total = min(total, limit)
    before = db.scalar("SELECT COUNT(*) FROM domains")
    counts = {"checked": 0, "live": 0, "missing": 0, "nocert": 0, "junk": 0,
              "kept_lead": 0, "unknown": 0, "removed": 0}
    ctx.log("DNSniffer: %s%s" % (
        "API key, one exact lookup per domain" if mode == "api"
        else "no API key, so the free public MCP search is used (slower, and a key checks "
             "the whole catalogue faster)",
        " - PREVIEW, nothing is removed" if dry_run else ""))
    ctx.log("%d domains to check%s" % (
        total, "" if refresh else " (already checked ones are skipped)"))
    if dry_run:
        preview = _preview(ctx, mode, key, require_cert, sample or 200, workers)
        summary = {"ok": not preview.get("error"), "dry_run": True, "mode": mode,
                   "require_cert": bool(require_cert), "sample": sample,
                   "total_before": before, "total_after": before}
        summary.update(preview)
        return ctx.summary(**summary)

    stop = threading.Event()
    fatal = ""
    rate_note = ""
    streak = 0
    last_detail = ""
    cursor, sent = "", 0
    pool = ThreadPoolExecutor(max_workers=workers)

    def one(domain):
        if stop.is_set():
            return {"state": "skipped"}
        return check(domain, mode, key, require_cert)

    try:
        while not stop.is_set():
            ctx.check_cancel()
            take = batch if limit <= 0 else min(batch, limit - sent)
            if take <= 0:
                break
            rows = db.rows("SELECT domain, source, data FROM domains WHERE " + where +
                           " AND domain>? ORDER BY domain LIMIT ?", params + (cursor, take))
            if not rows:
                break
            cursor = rows[-1]["domain"]
            names = [row["domain"] for row in rows]
            results = list(pool.map(one, names))
            kept, doomed = [], []
            sources = {row["domain"]: (row.get("source") or "dnsniffer") for row in rows}
            with_lead = set()
            for result, name in zip(results, names):
                if result.get("fatal") and not fatal:
                    fatal = result["fatal"]
                    stop.set()
                if result.get("rate") and not rate_note:
                    rate_note = result["rate"]
                    stop.set()
            if fatal or rate_note:
                continue
            dubious = [name for result, name in zip(results, names)
                       if result.get("state") in ("missing", "nocert")]
            if dubious:
                with_lead = _leads_in(dubious)
            for row, result in zip(rows, results):
                domain = row["domain"]
                state = result.get("state")
                if state == "skipped":
                    continue
                counts["checked"] += 1
                if state == "unknown":
                    counts["unknown"] += 1
                    streak += 1
                    last_detail = result.get("detail") or "no usable answer"
                    continue
                streak = 0
                if state == "live":
                    counts["live"] += 1
                    kept.append(row)
                elif state == "junk":
                    counts["junk"] += 1
                    doomed.append((domain, result.get("reason") or "junk",
                                   sources.get(domain, "dnsniffer")))
                elif domain in with_lead:
                    counts["kept_lead"] += 1
                    kept.append(row)
                elif state == "missing":
                    counts["missing"] += 1
                    doomed.append((domain, "not in the DNSniffer index",
                                   sources.get(domain, "dnsniffer")))
                else:
                    counts["nocert"] += 1
                    doomed.append((domain, "no certificate in DNSniffer",
                                   sources.get(domain, "dnsniffer")))
            if streak >= 40:
                stop.set()
                fatal = ("DNSniffer stopped answering (%s). Nothing more was removed; "
                         "check the connection and run again later." % last_detail)
                continue
            if not dry_run:
                if kept:
                    _mark(kept)
                if doomed:
                    domain_clean._drop(doomed)
                    counts["removed"] += len(doomed)
            sent += len(rows)
            ctx.progress(sent, total, "DNSniffer live check")
            ctx.log("%d/%d | %d live | %d not indexed | %d no certificate | %d kept "
                    "(has a lead) | %d unknown" % (
                        sent, total, counts["live"], counts["missing"], counts["nocert"],
                        counts["kept_lead"], counts["unknown"]))
    finally:
        if pool is not None:
            pool.shutdown(wait=False)

    if fatal:
        ctx.warn(fatal)
    if rate_note:
        ctx.warn(rate_note + " The batch stopped there; run it again later, it continues "
                             "where it ended.")
    ctx.log("%d checked | %d live | %d not indexed | %d no certificate | %d kept "
            "(has a lead) | %d unknown | %d quarantined" % (
                counts["checked"], counts["live"], counts["missing"], counts["nocert"],
                counts["kept_lead"], counts["unknown"], counts["removed"]))
    summary = dict(counts)
    summary.update({"ok": not fatal, "mode": mode, "dry_run": bool(dry_run),
                    "require_cert": bool(require_cert), "total_before": before,
                    "total_after": db.scalar("SELECT COUNT(*) FROM domains")})
    if fatal:
        summary["error"] = fatal
    if rate_note:
        summary["error"] = rate_note
    return ctx.summary(**summary)
