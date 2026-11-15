"""Manyreach through its public REST API v2.

The MCP connection (`engine/mcp_client.py`) is the agent-friendly way in; this is the plain
API, which needs one header - `X-API-Key: <guid>` - and paginates up to 1000 rows a page, so
a full account fits in a couple of requests. It writes the same `mcp_items` rows the MCP
download writes, so the card shows one list whichever way the data arrived.

Spec: https://app.manyreach.com/openapi/v2.json (v2.21.0 at the time of writing).
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config, db

BASE = "https://app.manyreach.com/api/v2"
BLOCK = "manyreach"
MAX_PAGES = 25
PAGE_SIZE = 1000


def _block():
    return dict((config.load_settings().get(BLOCK) or {}))


def _save(patch):
    settings = config.load_settings()
    block = dict(settings.get(BLOCK) or {})
    block.update(patch)
    settings[BLOCK] = block
    config.save_settings(settings)
    return block


def key():
    return str(_block().get("api_key") or "").strip()


def _request(path, params=None):
    token = key()
    if not token:
        return {"ok": False, "error": "No Manyreach API key yet - paste it first."}
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={
        "X-API-Key": token, "Accept": "application/json",
        "User-Agent": "LeadStudioLite/%s (local app)" % config.VERSION})
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            body = response.read().decode("utf-8", "replace")
            return {"ok": True, "data": json.loads(body or "{}")}
    except urllib.error.HTTPError as exc:
        raw = ""
        try:
            raw = exc.read().decode("utf-8", "replace")
        except (OSError, ValueError):
            pass
        if exc.code == 401:
            return {"ok": False, "error": "Manyreach rejected that API key."}
        if exc.code == 429:
            return {"ok": False, "error": "Manyreach rate limit hit (60 requests a minute). "
                                          "Wait a moment and try again."}
        return {"ok": False, "error": "Manyreach answered HTTP %s: %s" % (exc.code, raw[:200])}
    except (urllib.error.URLError, OSError) as exc:
        return {"ok": False, "error": "Could not reach Manyreach: %s" % exc}


def account():
    answer = _request("/account")
    if not answer["ok"]:
        return answer
    data = answer["data"]
    return {"ok": True, "account": {"id": data.get("id"), "title": data.get("title") or "",
                                    "key_type": data.get("keyType") or ""}}


def set_key(value):
    value = str(value or "").strip()
    if not value:
        return {"ok": False, "error": "Paste the API key first."}
    previous = key()
    _save({"api_key": value})
    answer = account()
    if not answer["ok"]:
        _save({"api_key": previous})            # never keep a key that does not work
        return answer
    _save({"connected_at": int(time.time())})
    return {"ok": True, "account": answer.get("account")}


def _collect(path, page_key="page", limit_key="limit", extra=None, max_pages=MAX_PAGES):
    """Every row of a list endpoint, page by page.

    Manyreach returns `pagination.totalItems`, so the loop stops on the real total rather than
    guessing from a short page.
    """
    rows, page, seen, total = [], 1, set(), 0
    while page <= max_pages:
        query = dict(extra or {})
        query[page_key] = page
        query[limit_key] = PAGE_SIZE
        answer = _request(path, query)
        if not answer["ok"]:
            return answer, rows
        data = answer["data"]
        batch = data.get("items") or []
        fresh = 0
        for item in batch:
            marker = item.get("campaignId") or item.get("senderId") or json.dumps(item, sort_keys=True)
            if marker in seen:
                continue
            seen.add(marker)
            rows.append(item)
            fresh += 1
        total = int((data.get("pagination") or {}).get("totalItems") or total or 0)
        if not batch or not fresh or (total and len(rows) >= total):
            break
        if len(batch) < PAGE_SIZE and not total:
            break
        page += 1
    return {"ok": True, "total": total}, rows


def campaigns():
    return _collect("/campaigns", page_key="pageQuery.page", limit_key="pageQuery.limit")


def senders():
    return _collect("/senders")


def _store(kind, rows):
    """Same rows the MCP download writes, so the card shows one list."""
    from .mcp_client import _item_row
    records, now = [], db.now()
    for item in rows:
        if not isinstance(item, dict):
            continue
        item_id, label, state = _item_row(kind, item)
        if not item_id:
            continue
        records.append({"connection": BLOCK, "kind": kind, "item_id": item_id,
                        "name": label[:200], "status": state[:40],
                        "payload": json.dumps(item, default=str)[:8000],
                        "updated_ts": now})
    if records:
        db.execute("DELETE FROM mcp_items WHERE connection=? AND kind=?", (BLOCK, kind))
        db.insert_many("mcp_items", records)
    return len(records)


def sync():
    """Download every campaign and sender into `mcp_items`."""
    answer_campaigns, campaign_rows = campaigns()
    answer_senders, sender_rows = senders()
    errors = [answer["error"] for answer in (answer_campaigns, answer_senders) if not answer["ok"]]
    if not answer_campaigns["ok"] and not answer_senders["ok"]:
        return {"ok": False, "error": errors[0] if errors else "Manyreach did not answer."}
    counts = {}
    if answer_campaigns["ok"]:
        counts["campaigns"] = _store("campaigns", campaign_rows)
    if answer_senders["ok"]:
        counts["senders"] = _store("senders", sender_rows)
    _save({"last_sync": db.now(), "last_counts": counts})
    db.event("manyreach", "API sync: %s" % ", ".join("%s %d" % item for item in counts.items()))
    result = {"ok": True, "counts": counts, "errors": errors}
    return result


def status():
    block = _block()
    counts = {kind: db.scalar("SELECT COUNT(*) FROM mcp_items WHERE connection=? AND kind=?",
                              (BLOCK, kind)) for kind in ("campaigns", "senders")}
    return {"has_key": bool(key()),
            "connected_at": int(block.get("connected_at") or 0),
            "last_sync": int(block.get("last_sync") or 0),
            "counts": counts,
            "base_url": BASE,
            "docs": "https://app.manyreach.com/api#v2"}
