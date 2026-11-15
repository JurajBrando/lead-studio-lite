"""Other MCP servers the app connects to, the way it connects to Metricool.

A connection is a name, an MCP URL, and the OAuth details discovered from the server. The
flow is the standard one for a remote MCP server:

    1. GET  <origin>/.well-known/oauth-protected-resource   -> authorization server
    2. GET  <issuer>/.well-known/oauth-authorization-server -> endpoints + capabilities
    3. POST registration_endpoint                           -> client id (dynamic, public)
    4. open authorization_endpoint with PKCE (S256) and a loopback redirect
    5. POST token_endpoint with the code                    -> access + refresh token
    6. JSON-RPC over the MCP endpoint with that bearer token

Manyreach is the first one; adding another is a dict entry, because nothing below is
service-specific.

Tokens live in data/settings.json next to the mailbox credentials, never in the repository.
"""
import base64
import hashlib
import json
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config, db

CONNECTIONS = {
    "manyreach": {
        "label": "Manyreach",
        "url": "https://mcp.manyreach.com/mcp",
        "note": "Campaigns, prospects and replies, through Manyreach's remote MCP server. "
                "Signing in asks for your Manyreach API key.",
    },
}

# What each connection can be asked to download, and how. Manyreach exposes a small set of
# generic tools (`read`, `create`, ...) whose "name" argument selects the operation, so a
# dataset is a tool plus the arguments that go with it.
DATASETS = {
    "manyreach": {
        "campaigns": {"label": "Campaigns", "tool": "read",
                      "arguments": {"name": "campaign/get",
                                    "arguments": {"pageQuery": {"page": 1, "limit": 100}}}},
        "senders": {"label": "Senders", "tool": "read",
                    "arguments": {"name": "sender/get",
                                  "arguments": {"page": 1, "limit": 30}}},
    },
}
INTENT = {"objective": "Keep a local copy of this workspace's campaigns and senders",
          "step": "read"}
SCOPE = "mcp"
PROTOCOL_VERSION = "2025-06-18"
CLIENT_NAME = "Lead Studio Lite"
TIMEOUT = 45
METADATA_TTL = 3600

_META = {}
_PENDING = {}


class McpError(Exception):
    """Anything the user needs to see: unreachable, refused, or an OAuth problem."""


# ------------------------------------------------------------------ http
def _request(url, data=None, headers=None, form=False, timeout=TIMEOUT):
    base = {"User-Agent": "LeadStudioLite/%s (Windows; local app)" % config.VERSION,
            "Accept": "application/json"}
    base.update(headers or {})
    body = None
    if data is not None:
        if form:
            body = urllib.parse.urlencode(data).encode("utf-8")
            base["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            body = json.dumps(data).encode("utf-8")
            base["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=base,
                                     method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, dict(response.headers), response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raw = ""
        try:
            raw = exc.read().decode("utf-8", "replace")
        except (OSError, ValueError):
            pass
        return exc.code, dict(exc.headers or {}), raw
    except (urllib.error.URLError, OSError) as exc:
        raise McpError("Could not reach the MCP server: %s" % exc)


def _json(raw, what):
    try:
        return json.loads(raw or "{}")
    except ValueError:
        raise McpError("The server sent something unexpected (%s): %s" % (what, (raw or "")[:200]))


def connection(name):
    key = str(name or "").strip().lower()
    if key in CONNECTIONS:
        return CONNECTIONS[key]
    custom = (config.load_settings().get("mcp_custom") or {})
    if key in custom:
        return custom[key]
    raise McpError("Unknown MCP connection.")


def save_custom(payload):
    """A server the user added: a name, a URL, and optionally a bearer token."""
    name = re_slug(payload.get("name") or payload.get("label") or "")
    url = str(payload.get("url") or "").strip()
    if not name:
        return {"ok": False, "error": "Give the server a name."}
    if not url.lower().startswith(("http://", "https://")):
        return {"ok": False, "error": "The MCP URL has to start with http:// or https://"}
    if name in CONNECTIONS:
        return {"ok": False, "error": "That name is taken by a built-in connection."}
    settings = config.load_settings()
    custom = dict(settings.get("mcp_custom") or {})
    custom[name] = {"label": str(payload.get("label") or name).strip()[:60], "url": url,
                    "note": "A server you added. " + (
                        "It uses the token you pasted." if payload.get("token")
                        else "It has to support OAuth for the browser sign-in.")}
    settings["mcp_custom"] = custom
    config.save_settings(settings)
    if payload.get("token"):
        block = _saved(name)
        block.update({"access_token": str(payload["token"]).strip(), "expires_at": 0,
                      "connected_at": int(time.time())})
        _save(name, block)
    return {"ok": True, "name": name}


def re_slug(value):
    return re.sub(r"[^a-z0-9_-]+", "-", str(value or "").strip().lower()).strip("-")[:40]


def connections():
    """Built-in plus the ones the user added."""
    out = dict(CONNECTIONS)
    out.update(config.load_settings().get("mcp_custom") or {})
    return out


def _saved(name):
    return dict((config.load_settings().get("mcp_connections") or {}).get(name) or {})


def _save(name, block):
    settings = config.load_settings()
    connections = dict(settings.get("mcp_connections") or {})
    connections[name] = block
    settings["mcp_connections"] = connections
    config.save_settings(settings)
    return block


def disconnect(name):
    connection(name)
    block = _saved(name)
    for key in ("access_token", "refresh_token", "expires_at", "scope", "connected_at"):
        block.pop(key, None)
    _save(name, block)
    return {"ok": True}


# ------------------------------------------------------------------ discovery
def metadata(name):
    """{issuer, authorization_endpoint, token_endpoint, registration_endpoint, ...}."""
    entry = connection(name)
    cached = _META.get(name)
    if cached and time.time() - cached["at"] < METADATA_TTL:
        return cached["data"]
    origin = "{0.scheme}://{0.netloc}".format(urllib.parse.urlsplit(entry["url"]))
    status, _, raw = _request(origin + "/.well-known/oauth-protected-resource")
    if status != 200:
        raise McpError("%s did not publish its OAuth metadata (HTTP %s)." % (entry["label"], status))
    protected = _json(raw, "protected resource metadata")
    servers = protected.get("authorization_servers") or []
    if not servers:
        raise McpError("%s names no authorization server." % entry["label"])
    issuer = str(servers[0]).rstrip("/")
    status, _, raw = _request(issuer + "/.well-known/oauth-authorization-server")
    if status != 200:
        raise McpError("%s's authorization server is not reachable (HTTP %s)."
                       % (entry["label"], status))
    data = _json(raw, "authorization server metadata")
    if not data.get("authorization_endpoint") or not data.get("token_endpoint"):
        raise McpError("%s's authorization metadata is incomplete." % entry["label"])
    data.setdefault("issuer", issuer)
    _META[name] = {"at": time.time(), "data": data}
    return data


def _pkce():
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode()
    return verifier, challenge


def begin(name, port):
    """Register the app if needed and return the URL the user has to open."""
    entry = connection(name)
    meta = metadata(name)
    redirect_uri = "http://127.0.0.1:%d/oauth/mcp/%s/callback" % (int(port), name)
    block = _saved(name)
    client_id = block.get("client_id") or ""
    if not client_id or block.get("redirect_uri") != redirect_uri:
        if not meta.get("registration_endpoint"):
            raise McpError("%s does not allow registering this app. Use any API key "
                           "export instead, or connect through the vendor's own client."
                           % entry["label"])
        payload = {"client_name": CLIENT_NAME, "redirect_uris": [redirect_uri],
                   "grant_types": ["authorization_code", "refresh_token"],
                   "response_types": ["code"], "token_endpoint_auth_method": "none",
                   "scope": SCOPE}
        status, _, raw = _request(meta["registration_endpoint"], payload)
        if status not in (200, 201):
            raise McpError("%s refused to register this app (HTTP %s). %s"
                           % (entry["label"], status, (raw or "")[:300]))
        data = _json(raw, "client registration")
        client_id = data.get("client_id") or ""
        if not client_id:
            raise McpError("%s registered no client id." % entry["label"])
        block.update({"client_id": client_id,
                      "client_secret": data.get("client_secret") or "",
                      "redirect_uri": redirect_uri})
        _save(name, block)

    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(24)
    _PENDING[state] = {"name": name, "verifier": verifier, "redirect_uri": redirect_uri,
                       "client_id": client_id, "ts": time.time()}
    query = {"response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri,
             "scope": SCOPE, "state": state, "code_challenge": challenge,
             "code_challenge_method": "S256"}
    return {"url": meta["authorization_endpoint"] + "?" + urllib.parse.urlencode(query),
            "state": state}


def finish(name, query):
    """Handle the browser redirect: verify state, exchange the code, store the tokens."""
    connection(name)
    state = str((query or {}).get("state") or "")
    code = str((query or {}).get("code") or "")
    error = str((query or {}).get("error") or "")
    if error:
        return {"ok": False, "error": "The sign-in was cancelled (%s)." % error}
    pending = _PENDING.pop(state, None)
    if not pending or not code or pending.get("name") != name:
        return {"ok": False, "error": "That sign-in link expired or was already used. Start again."}
    if time.time() - pending["ts"] > 900:
        return {"ok": False, "error": "That sign-in link took too long. Start again."}
    meta = metadata(name)
    payload = {"grant_type": "authorization_code", "code": code,
               "redirect_uri": pending["redirect_uri"], "client_id": pending["client_id"],
               "code_verifier": pending["verifier"]}
    status, _, raw = _request(meta["token_endpoint"], payload, form=True)
    data = _json(raw, "token exchange") if raw else {}
    if status != 200 or not data.get("access_token"):
        return {"ok": False, "error": "The server would not issue a token (HTTP %s): %s"
                                      % (status, data.get("error_description")
                                         or data.get("error") or (raw or "")[:200])}
    block = _saved(name)
    block.update({"access_token": data["access_token"],
                  "refresh_token": data.get("refresh_token") or block.get("refresh_token") or "",
                  "expires_at": int(time.time()) + int(data.get("expires_in") or 3600),
                  "scope": data.get("scope") or SCOPE,
                  "connected_at": int(time.time()),
                  "client_id": pending["client_id"],
                  "redirect_uri": pending["redirect_uri"]})
    _save(name, block)
    config.log("mcp %s: connected" % name)
    return {"ok": True}


def _refresh(name, block):
    if not block.get("refresh_token"):
        raise McpError("The session expired. Connect again.")
    meta = metadata(name)
    payload = {"grant_type": "refresh_token", "refresh_token": block["refresh_token"],
               "client_id": block.get("client_id") or ""}
    status, _, raw = _request(meta["token_endpoint"], payload, form=True)
    data = _json(raw, "token refresh") if raw else {}
    if status != 200 or not data.get("access_token"):
        raise McpError("The session expired and could not be refreshed (HTTP %s). Connect again."
                       % status)
    block.update({"access_token": data["access_token"],
                  "refresh_token": data.get("refresh_token") or block.get("refresh_token") or "",
                  "expires_at": int(time.time()) + int(data.get("expires_in") or 3600)})
    _save(name, block)
    return block


def access_token(name):
    block = _saved(name)
    if not block.get("access_token"):
        raise McpError("Not connected yet.")
    if int(block.get("expires_at") or 0) and int(block["expires_at"]) < time.time() + 60:
        block = _refresh(name, block)
    return block["access_token"]


def status(name):
    entry = connection(name)
    block = _saved(name)
    expires = int(block.get("expires_at") or 0)
    return {"name": name, "label": entry["label"], "url": entry["url"],
            "note": entry.get("note") or "",
            "connected": bool(block.get("access_token")),
            "expired": bool(expires and expires < time.time()),
            "connected_at": int(block.get("connected_at") or 0),
            "client_id": block.get("client_id") or ""}


def status_all():
    return [status(name) for name in connections()]


# ------------------------------------------------------------------ MCP transport
def _parse_rpc(raw, content_type, rid):
    """MCP answers either as JSON or as a server-sent-event stream."""
    text = (raw or "").strip()
    if "text/event-stream" in (content_type or ""):
        for block in text.replace("\r\n", "\n").split("\n\n"):
            data = "\n".join(line[5:].strip() for line in block.split("\n")
                             if line.startswith("data:"))
            if not data:
                continue
            try:
                obj = json.loads(data)
            except ValueError:
                continue
            if isinstance(obj, dict) and obj.get("id") == rid:
                return obj
        raise McpError("The server sent an event stream without an answer.")
    obj = _json(text, "MCP response")
    if isinstance(obj, list):
        for item in obj:
            if isinstance(item, dict) and item.get("id") == rid:
                return item
        raise McpError("The server sent no answer.")
    return obj


class Client:
    def __init__(self, name, token):
        self.name = name
        self.url = connection(name)["url"]
        self.token = token
        self.session = ""
        self.protocol = ""
        self._id = 0

    def _post(self, payload, timeout=TIMEOUT):
        headers = {"Authorization": "Bearer " + self.token,
                   "Accept": "application/json, text/event-stream",
                   "MCP-Protocol-Version": self.protocol or PROTOCOL_VERSION}
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        status, hdrs, raw = _request(self.url, payload, headers, timeout=timeout)
        lower = {str(k).lower(): v for k, v in (hdrs or {}).items()}
        if lower.get("mcp-session-id"):
            self.session = lower["mcp-session-id"]
        return status, lower, raw

    def rpc(self, method, params=None, timeout=TIMEOUT):
        self._id += 1
        rid = self._id
        payload = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            payload["params"] = params
        status, hdrs, raw = self._post(payload, timeout=timeout)
        if status == 401:
            raise McpError("The server rejected the session. Connect again.")
        if status >= 400:
            raise McpError("The server answered HTTP %s: %s" % (status, (raw or "")[:300]))
        obj = _parse_rpc(raw, hdrs.get("content-type", ""), rid)
        if obj.get("error"):
            err = obj["error"]
            raise McpError(str(err.get("message") or err))
        return obj.get("result") or {}

    def notify(self, method, params=None):
        payload = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        self._post(payload)

    def initialize(self):
        result = self.rpc("initialize", {
            "protocolVersion": PROTOCOL_VERSION, "capabilities": {},
            "clientInfo": {"name": CLIENT_NAME, "version": config.VERSION}})
        self.protocol = result.get("protocolVersion") or PROTOCOL_VERSION
        self.notify("notifications/initialized")
        return result

    def tools(self):
        return (self.rpc("tools/list", {}) or {}).get("tools") or []

    def call(self, tool, arguments=None):
        return self.rpc("tools/call", {"name": tool, "arguments": arguments or {}}, timeout=90)


def session(name):
    client = Client(name, access_token(name))
    try:
        client.initialize()
    except McpError:
        block = _saved(name)
        if block.get("refresh_token"):
            client = Client(name, _refresh(name, block)["access_token"])
            client.initialize()
        else:
            raise
    return client


def tools(name):
    items = session(name).tools()
    return [{"name": tool.get("name") or "", "description": (tool.get("description") or "")[:400],
             "schema": tool.get("inputSchema") or {}} for tool in items]


def call(name, tool, arguments=None):
    result = session(name).call(tool, arguments)
    text = []
    for item in (result or {}).get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            text.append(str(item.get("text") or ""))
    return {"ok": not result.get("isError"), "text": "\n".join(text),
            "result": result, "tool": tool}


# ------------------------------------------------------------------ downloading
def _json_from_text(text_value):
    """Manyreach answers with a header line, then the JSON body."""
    body = (text_value or "").strip()
    start = body.find("{")
    if start < 0:
        raise McpError("The server answered without JSON: %s" % body[:160])
    try:
        return json.loads(body[start:])
    except ValueError:
        raise McpError("The server answered with malformed JSON: %s" % body[start:start + 160])


def _item_row(kind, item):
    """(id, label, status) for one record of a dataset."""
    if kind == "campaigns":
        return (str(item.get("campaignId") or item.get("id") or ""),
                str(item.get("name") or ""), str(item.get("status") or ""))
    return (str(item.get("senderId") or item.get("id") or ""),
            str(item.get("email") or item.get("fromName") or ""),
            "warmup" if item.get("warmup") else str(item.get("status") or ""))


def pull(name, kind=""):
    """Download one dataset (or all of them) from a connection into `mcp_items`."""
    connection(name)
    available = DATASETS.get(name) or {}
    if not available:
        return {"ok": False, "error": "%s does not declare anything to download yet." % name}
    wanted = [kind] if kind else list(available.keys())
    results = []
    for one in wanted:
        spec = available.get(one)
        if not spec:
            return {"ok": False, "error": "Unknown dataset: %s" % one}
        arguments = dict(spec["arguments"])
        arguments.setdefault("intent", INTENT)
        answer = call(name, spec["tool"], arguments)
        if not answer.get("ok"):
            results.append({"kind": one, "ok": False, "error": (answer.get("text") or "")[:300]})
            continue
        data = _json_from_text(answer.get("text"))
        items = data.get("items") or []
        records = []
        now = db.now()
        for item in items:
            if not isinstance(item, dict):
                continue
            item_id, label, state = _item_row(one, item)
            if not item_id:
                continue
            records.append({"connection": name, "kind": one, "item_id": item_id,
                            "name": label[:200], "status": state[:40],
                            "payload": json.dumps(item, default=str)[:8000],
                            "updated_ts": now})
        if records:
            db.execute("DELETE FROM mcp_items WHERE connection=? AND kind=?", (name, one))
            db.insert_many("mcp_items", records)
        results.append({"kind": one, "ok": True, "count": len(records)})
    db.event("mcp", "%s downloaded: %s" % (
        name, ", ".join("%s %d" % (row["kind"], row.get("count", 0))
                        for row in results if row.get("ok"))))
    return {"ok": any(row.get("ok") for row in results), "results": results}


def items(name, kind=""):
    sql = "SELECT * FROM mcp_items WHERE connection=?"
    args = [name]
    if kind:
        sql += " AND kind=?"
        args.append(kind)
    sql += " ORDER BY kind, name"
    return db.rows(sql, tuple(args))
