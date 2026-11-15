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
    imported = import_into_app()
    result = {"ok": True, "counts": counts, "errors": errors, "imported": imported}
    return result


def status():
    block = _block()
    counts = {kind: db.scalar("SELECT COUNT(*) FROM mcp_items WHERE connection=? AND kind=?",
                              (BLOCK, kind)) for kind in ("campaigns", "senders")}
    return {"has_key": bool(key()),
            "connected_at": int(block.get("connected_at") or 0),
            "last_sync": int(block.get("last_sync") or 0),
            "counts": counts,
            "in_app": {"mailboxes": db.scalar(
                            "SELECT COUNT(*) FROM mailboxes WHERE source='manyreach'"),
                       "sequences": db.scalar(
                            "SELECT COUNT(*) FROM sequences WHERE name LIKE '[MR] %'")},
            "base_url": BASE,
            "docs": "https://app.manyreach.com/api#v2"}


# ------------------------------------------------------- into the app itself
def _text_of(value):
    """The campaign body is HTML; a sequence step is plain text."""
    import html as html_mod
    import re as re_mod
    text = re_mod.sub(r"<br\s*/?>|</p>|</div>", "\n", value or "", flags=re_mod.I)
    text = re_mod.sub(r"<[^>]+>", "", text)
    text = html_mod.unescape(text)
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def _smtp_security(port):
    if int(port or 0) == 465:
        return "ssl"
    if int(port or 0) == 25:
        return "none"
    return "starttls"


def import_into_app():
    """Senders become mailboxes, campaigns become one-step sequences.

    Both keep their Manyreach mark in the name (`[MR]` for sequences) or the `source` column
    (mailboxes), so a second import never duplicates them, and a mailbox with no password is
    created inactive - it is visible and editable, but the send queue will not touch it until
    the password is filled in.
    """
    rows = db.rows("SELECT kind, payload FROM mcp_items WHERE connection=?", (BLOCK,))
    known_boxes = {str(row["from_email"] or "").lower() for row in db.rows("SELECT from_email FROM mailboxes")}
    known_sequences = {str(row["name"] or "").lower() for row in db.rows("SELECT name FROM sequences")}
    added_boxes = skipped_boxes = no_password = inactive = 0
    added_sequences = skipped_sequences = 0
    now = db.now()
    for row in rows:
        try:
            item = json.loads(row["payload"] or "{}")
        except ValueError:
            continue
        if row["kind"] == "senders":
            email = str(item.get("email") or "").strip().lower()
            if not email or email in known_boxes:
                skipped_boxes += 1
                continue
            smtp_port = int(item.get("customSmtpPort") or 587)
            password = ""            # the API never returns it
            active = 0 if item.get("disconnected") else 1
            if not password:
                no_password += 1
                active = 0
            db.insert("mailboxes", {
                "name": (item.get("fromName") or email)[:80],
                "from_name": str(item.get("fromName") or "")[:120],
                "from_email": email, "reply_to": str(item.get("replyTo") or "")[:200],
                "smtp_host": str(item.get("customSmtpServer") or "")[:200],
                "smtp_port": smtp_port, "smtp_security": _smtp_security(smtp_port),
                "smtp_user": str(item.get("customSmtpUsername") or email)[:200],
                "smtp_pass": password,
                "imap_host": str(item.get("customImapServer") or "")[:200],
                "imap_port": int(item.get("customImapPort") or 993),
                "imap_user": str(item.get("customImapUsername") or email)[:200],
                "imap_pass": password,
                "daily_limit": int(item.get("dailyLimit") or 30),
                "signature": str(item.get("signature") or "")[:4000],
                "active": active, "last_send_ts": 0, "created_ts": now,
                "auth_type": "password", "source": "manyreach"})
            known_boxes.add(email)
            added_boxes += 1
            inactive += 1 if not active else 0
        elif row["kind"] == "campaigns":
            name = str(item.get("name") or "").strip()
            if not name:
                skipped_sequences += 1
                continue
            label = ("[MR] " + name)[:120]
            if label.lower() in known_sequences:
                skipped_sequences += 1
                continue
            sequence_id = db.insert("sequences", {"name": label, "created_ts": now, "active": 1})
            db.insert("steps", {
                "sequence_id": sequence_id, "step_no": 1, "delay_days": 0,
                "subject": str(item.get("subject") or name)[:200],
                "body": _text_of(item.get("body"))[:8000]})
            known_sequences.add(label.lower())
            added_sequences += 1
    if added_boxes or added_sequences:
        db.event("manyreach", "imported %d mailboxes, %d sequences" % (added_boxes, added_sequences))
    return {"ok": True, "mailboxes": added_boxes, "mailboxes_skipped": skipped_boxes,
            "mailboxes_without_password": no_password, "sequences": added_sequences,
            "sequences_skipped": skipped_sequences}
