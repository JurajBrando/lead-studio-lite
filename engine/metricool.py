"""Metricool, through its official MCP server.

Why MCP and not the REST API: Metricool's API is only included in the Advanced and Custom
plans, while the MCP server (https://ai.metricool.com/mcp) works on every plan including
Free. The account's own limits still apply - on Free that is one brand and 20 posts a
month, and LinkedIn/X cannot be connected.

The flow, as the server advertises it in its OAuth metadata:
    1. register this app on the fly (RFC 7591 dynamic client registration, public client)
    2. open the browser at /oauth/authorize with PKCE (S256) and a loopback redirect
    3. exchange the code for an access token, refresh it when it expires
    4. speak JSON-RPC over the MCP endpoint with that bearer token

Everything here is standard library only, like the rest of the app.
"""
import base64
import hashlib
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config

MCP_URL = "https://ai.metricool.com/mcp"
METADATA_URL = "https://ai.metricool.com/.well-known/oauth-authorization-server"
APP_URL = "https://app.metricool.com"

# The link the app sends people through when they still need an account. Every "go to
# Metricool" link uses it, and it is disclosed on the settings page and in the README - an
# affiliate link that is not disclosed is not worth the commission.
SIGNUP_URL = "https://my.mtr.cool/NEMGNE"
AFFILIATE_NOTE = (
    "The Metricool links in this app are affiliate links: if you open a Metricool account "
    "through one, this project may earn a commission at no extra cost to you.")
NOT_AFFILIATED = (
    "Lead Studio Lite is an independent project. It is not affiliated with, endorsed by or "
    "sponsored by Metricool or any other service it connects to.")
SCOPE = "mcp:read mcp:write"
PROTOCOL_VERSION = "2025-06-18"
CLIENT_NAME = "Lead Studio Lite"
TIMEOUT = 45
METADATA_TTL = 3600

_META = {"at": 0, "data": {}}
_PENDING = {}


class MetricoolError(Exception):
    """Anything the user needs to see: unreachable, refused, or an OAuth problem."""


# ------------------------------------------------------------------ http
def _headers(extra=None):
    base = {"User-Agent": "LeadStudioLite/%s (Windows; local app)" % config.VERSION,
            "Accept": "application/json"}
    base.update(extra or {})
    return base


def _request(url, data=None, headers=None, form=False, timeout=TIMEOUT):
    """(status, headers, body). Never raises for an HTTP status; raises for no network."""
    hdrs = _headers(headers)
    body = None
    if data is not None:
        if form:
            body = urllib.parse.urlencode(data).encode("utf-8")
            hdrs["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            body = json.dumps(data).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raw = ""
        try:
            raw = exc.read().decode("utf-8", "replace")
        except (OSError, ValueError):
            pass
        return exc.code, dict(exc.headers or {}), raw
    except (urllib.error.URLError, OSError) as exc:
        raise MetricoolError("Could not reach Metricool: %s" % exc)


def _json(raw, what):
    try:
        return json.loads(raw or "{}")
    except ValueError:
        raise MetricoolError("Metricool sent something unexpected (%s): %s" % (what, (raw or "")[:200]))


def discover():
    """The authorization server metadata (cached for an hour)."""
    if _META["data"] and time.time() - _META["at"] < METADATA_TTL:
        return _META["data"]
    status, _, raw = _request(METADATA_URL)
    if status != 200:
        raise MetricoolError("Metricool's OAuth metadata is not reachable (HTTP %s)." % status)
    data = _json(raw, "metadata")
    if not data.get("authorization_endpoint") or not data.get("token_endpoint"):
        raise MetricoolError("Metricool's OAuth metadata is incomplete.")
    _META["data"] = data
    _META["at"] = time.time()
    return data


# ------------------------------------------------------------------ tokens
def saved():
    block = config.load_settings().get("metricool")
    return block if isinstance(block, dict) else {}


def _save(block):
    settings = config.load_settings()
    settings["metricool"] = block
    config.save_settings(settings)
    return block


def status():
    block = saved()
    expires = int(block.get("expires_at") or 0)
    return {
        "connected": bool(block.get("access_token")),
        "client_id": block.get("client_id") or "",
        "scope": block.get("scope") or "",
        "expires_at": expires,
        "expired": bool(expires and expires < time.time()),
        "connected_at": int(block.get("connected_at") or 0),
        "mcp_url": MCP_URL,
        "app_url": APP_URL,
        "signup_url": SIGNUP_URL,
        "affiliate_note": AFFILIATE_NOTE,
        "note": "Free plan: 1 brand, 20 posts a month, 30 days of analytics, no LinkedIn or X.",
    }


def disconnect():
    block = saved()
    block.pop("access_token", None)
    block.pop("refresh_token", None)
    block.pop("expires_at", None)
    block.pop("client_secret", None)
    _save(block)
    return {"ok": True}


def _pkce():
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode()
    return verifier, challenge


def begin(port):
    """Register (if needed) and return the URL the user has to open."""
    meta = discover()
    redirect_uri = "http://127.0.0.1:%d/oauth/metricool/callback" % int(port)
    block = saved()
    client = None
    if block.get("client_id") and block.get("redirect_uri") == redirect_uri:
        client = {"client_id": block["client_id"], "client_secret": block.get("client_secret") or ""}
    if not client:
        payload = {"client_name": CLIENT_NAME, "redirect_uris": [redirect_uri],
                   "grant_types": ["authorization_code", "refresh_token"],
                   "response_types": ["code"], "token_endpoint_auth_method": "none",
                   "scope": SCOPE}
        code, _, raw = _request(meta["registration_endpoint"], payload)
        if code not in (200, 201):
            raise MetricoolError("Metricool refused to register this app (HTTP %s). %s"
                                 % (code, (raw or "")[:300]))
        data = _json(raw, "client registration")
        client = {"client_id": data.get("client_id") or "",
                  "client_secret": data.get("client_secret") or ""}
        if not client["client_id"]:
            raise MetricoolError("Metricool registered no client id.")
        block.update({"client_id": client["client_id"],
                      "client_secret": client["client_secret"],
                      "redirect_uri": redirect_uri})
        _save(block)

    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(24)
    _PENDING[state] = {"verifier": verifier, "redirect_uri": redirect_uri,
                       "client": client, "ts": time.time()}
    query = {"response_type": "code", "client_id": client["client_id"],
             "redirect_uri": redirect_uri, "scope": SCOPE, "state": state,
             "code_challenge": challenge, "code_challenge_method": "S256"}
    return {"url": meta["authorization_endpoint"] + "?" + urllib.parse.urlencode(query),
            "state": state}


def finish(query):
    """Handle the browser redirect: verify state, exchange the code, store the tokens."""
    state = str((query or {}).get("state") or "")
    code = str((query or {}).get("code") or "")
    error = str((query or {}).get("error") or "")
    if error:
        return {"ok": False, "error": "Metricool sign-in was cancelled (%s)." % error}
    pending = _PENDING.pop(state, None)
    if not pending or not code:
        return {"ok": False, "error": "That sign-in link expired or was already used. Start again."}
    if time.time() - pending["ts"] > 900:
        return {"ok": False, "error": "That sign-in link took too long. Start again."}
    meta = discover()
    payload = {"grant_type": "authorization_code", "code": code,
               "redirect_uri": pending["redirect_uri"],
               "client_id": pending["client"]["client_id"],
               "code_verifier": pending["verifier"]}
    code_status, _, raw = _request(meta["token_endpoint"], payload, form=True)
    data = _json(raw, "token exchange") if raw else {}
    if code_status != 200 or not data.get("access_token"):
        return {"ok": False, "error": "Metricool would not issue a token (HTTP %s): %s"
                                      % (code_status, data.get("error_description")
                                         or data.get("error") or (raw or "")[:200])}
    block = saved()
    block.update({"access_token": data["access_token"],
                  "refresh_token": data.get("refresh_token") or block.get("refresh_token") or "",
                  "expires_at": int(time.time()) + int(data.get("expires_in") or 3600),
                  "scope": data.get("scope") or SCOPE,
                  "connected_at": int(time.time()),
                  "client_id": pending["client"]["client_id"],
                  "client_secret": pending["client"].get("client_secret") or "",
                  "redirect_uri": pending["redirect_uri"]})
    _save(block)
    config.log("metricool: connected")
    return {"ok": True}


def _refresh(block):
    if not block.get("refresh_token"):
        raise MetricoolError("The Metricool session expired. Connect again.")
    meta = discover()
    payload = {"grant_type": "refresh_token", "refresh_token": block["refresh_token"],
               "client_id": block.get("client_id") or ""}
    code, _, raw = _request(meta["token_endpoint"], payload, form=True)
    data = _json(raw, "token refresh") if raw else {}
    if code != 200 or not data.get("access_token"):
        raise MetricoolError("The Metricool session expired and could not be renewed. Connect again.")
    block.update({"access_token": data["access_token"],
                  "refresh_token": data.get("refresh_token") or block["refresh_token"],
                  "expires_at": int(time.time()) + int(data.get("expires_in") or 3600)})
    _save(block)
    return block["access_token"]


def access_token():
    block = saved()
    if not block.get("access_token"):
        raise MetricoolError("Metricool is not connected yet.")
    if int(block.get("expires_at") or 0) and int(block["expires_at"]) < time.time() + 60:
        return _refresh(block)
    return block["access_token"]


# ------------------------------------------------------------------ MCP
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
            if obj.get("id") == rid:
                return obj
        raise MetricoolError("Metricool sent an event stream without an answer.")
    obj = _json(text, "MCP response")
    if isinstance(obj, list):
        for item in obj:
            if isinstance(item, dict) and item.get("id") == rid:
                return item
        raise MetricoolError("Metricool sent no answer.")
    return obj


class Mcp:
    def __init__(self, token):
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
        status, hdrs, raw = _request(MCP_URL, payload, headers, timeout=timeout)
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
            raise MetricoolError("Metricool rejected the session token. Connect again.")
        if status == 403:
            raise MetricoolError("Metricool says this account may not do that (HTTP 403). %s"
                                 % (raw or "")[:200])
        if status >= 400:
            raise MetricoolError("Metricool answered HTTP %s: %s" % (status, (raw or "")[:300]))
        obj = _parse_rpc(raw, hdrs.get("content-type", ""), rid)
        if obj.get("error"):
            err = obj["error"]
            raise MetricoolError(str(err.get("message") or err))
        return obj.get("result") or {}

    def notify(self, method, params=None):
        payload = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        self._post(payload)

    def initialize(self):
        result = self.rpc("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": CLIENT_NAME, "version": config.VERSION},
        })
        self.protocol = result.get("protocolVersion") or PROTOCOL_VERSION
        self.notify("notifications/initialized")
        return result

    def tools(self):
        result = self.rpc("tools/list", {})
        return result.get("tools") or []

    def call(self, name, arguments=None):
        return self.rpc("tools/call", {"name": name, "arguments": arguments or {}}, timeout=90)


def _session():
    client = Mcp(access_token())
    try:
        client.initialize()
    except MetricoolError:
        # A stale token can look valid but be revoked: refresh once and retry.
        block = saved()
        if block.get("refresh_token"):
            client = Mcp(_refresh(block))
            client.initialize()
        else:
            raise
    return client


def _text_of(result):
    """MCP tool results carry a content list; join the text items."""
    parts = []
    for item in (result or {}).get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text") or ""))
    return "\n".join(p for p in parts if p)


# The tool list is stable for a session and every call costs an initialize round trip,
# so it is cached for a minute. load_tools() forces a fresh read.
_TOOLS = {"at": 0.0, "items": []}
TOOLS_TTL = 60


def tools(refresh=False):
    now = time.time()
    if not refresh and _TOOLS["items"] and now - _TOOLS["at"] < TOOLS_TTL:
        return list(_TOOLS["items"])
    client = _session()
    items = []
    for tool in client.tools():
        items.append({"name": tool.get("name") or "",
                      "description": (tool.get("description") or "")[:800],
                      "schema": tool.get("inputSchema") or {}})
    items.sort(key=lambda t: t["name"])
    _TOOLS["items"] = items
    _TOOLS["at"] = now
    return list(items)


def call(name, arguments=None):
    client = _session()
    result = client.call(name, arguments or {})
    return {"ok": not result.get("isError"), "result": result,
            "text": _text_of(result), "tool": name,
            "error": _text_of(result) if result.get("isError") else ""}


# ------------------------------------------------------------------ convenience
def find_tool(items, hints, exclude=()):
    """Pick the most likely tool for a job by name, then by description."""
    for hint in hints:
        for tool in items:
            name = tool["name"].lower()
            if hint in name and name not in exclude:
                return tool
    for hint in hints:
        for tool in items:
            text = (tool["name"] + " " + tool["description"]).lower()
            if hint in text and tool["name"].lower() not in exclude:
                return tool
    return None


def _properties(schema):
    props = (schema or {}).get("properties") or {}
    return {str(k).lower(): k for k in props}


def _pick(props, *needles):
    for needle in needles:
        for lower, original in props.items():
            if needle in lower:
                return original
    return ""


def build_arguments(schema, values):
    """Map our friendly names onto whatever the tool's schema calls them."""
    props = _properties(schema)
    if not props:
        return dict(values)
    args = {}
    for key, value in list(values.items()):
        if value in (None, "", [], {}):
            continue
        wanted = key.lower()
        if wanted in props:
            args[props[wanted]] = value
            continue
        match = _pick(props, wanted)
        if match:
            args[match] = value
    return args


def schedule(caption, when, networks, media_url, extra=None):
    """Create a scheduled post. The tool and its field names are discovered at runtime."""
    items = tools()
    tool = find_tool(items, ("schedule", "createpost", "create_post", "publish", "post"))
    if not tool:
        return {"ok": False, "error": "This Metricool account exposes no scheduling tool.",
                "tools": [t["name"] for t in items]}
    values = {"caption": caption, "content": caption, "text": caption,
              "network": networks, "networks": networks, "platform": networks,
              "when": when, "datetime": when, "date": when, "scheduledat": when,
              "media": media_url, "media_url": media_url, "url": media_url,
              "video": media_url, "image": media_url}
    values.update(extra or {})
    args = build_arguments(tool["schema"], values)
    result = call(tool["name"], args)
    result["tool"] = tool["name"]
    result["arguments"] = args
    return result


def analytics(network="", date_from="", date_to="", metrics=None, extra=None):
    """Pull analytics for a date range through whichever analytics tool exists."""
    items = tools()
    tool = find_tool(items, ("analytics", "getmetrics", "metrics", "stats", "performance"))
    if not tool:
        return {"ok": False, "error": "This Metricool account exposes no analytics tool.",
                "tools": [t["name"] for t in items]}
    values = {"network": network, "platform": network, "from": date_from,
              "init": date_from, "start": date_from, "to": date_to, "end": date_to,
              "finish": date_to, "metrics": metrics}
    values.update(extra or {})
    args = build_arguments(tool["schema"], values)
    result = call(tool["name"], args)
    result["tool"] = tool["name"]
    result["arguments"] = args
    return result


# ------------------------------------------------------------------ the workspace
# What the Social tab shows. Metricool decides which tools exist, so every section is
# matched on the tool list instead of being hard-coded: if the account exposes nothing
# for a section, the page says so instead of pretending.
FEATURES = {
    "inbox": ("inbox", "conversation", "message", "comment", "mention", "reply"),
    "planner": ("calendar", "schedule", "publish", "post", "queue"),
    "ads": ("ads", "adspending", "campaign", "budget", "ad_"),
    "reports": ("report", "analytics", "metrics", "stats", "dashboard", "hashtag"),
}
TITLES = {"inbox": "Inbox", "planner": "Planner", "ads": "Ads", "reports": "Reporting"}


def capabilities(items=None):
    """{section: tool name} for the sections this account can answer."""
    items = items if items is not None else tools()
    found = {}
    for name, hints in FEATURES.items():
        tool = find_tool(items, hints)
        found[name] = tool["name"] if tool else ""
    return found


def workspace(refresh=False):
    """The Social tab's opening state: connection, tools and what they cover."""
    items = tools(refresh=refresh)
    caps = capabilities(items)
    return {
        "ok": True,
        "brand": "default",
        "mcp_url": MCP_URL,
        "app_url": APP_URL,
        "count": len(items),
        "capabilities": caps,
        "sections": [{"key": key, "title": TITLES[key], "tool": caps[key],
                      "available": bool(caps[key])} for key in TITLES],
        "tools": [{"name": t["name"], "description": t["description"][:240]} for t in items],
    }


def section(name, params=None):
    """Run the tool that answers one Social section. Returns its raw answer."""
    name = str(name or "").strip().lower()
    if name not in FEATURES:
        return {"ok": False, "error": "Unknown section."}
    params = dict(params or {})
    items = tools()
    tool = find_tool(items, FEATURES[name])
    if not tool:
        return {"ok": False, "unavailable": True, "section": name,
                "error": "This Metricool account exposes no tool for %s." % TITLES[name],
                "tools": [t["name"] for t in items]}
    values = {key: value for key, value in params.items() if value not in (None, "", [], {})}
    args = build_arguments(tool["schema"], values)
    result = call(tool["name"], args)
    result["section"] = name
    result["tool"] = tool["name"]
    result["arguments"] = args
    return result


def reply(conversation, text, network="", extra=None):
    """Answer a conversation through whichever reply tool the account exposes."""
    body = str(text or "").strip()
    if not body:
        return {"ok": False, "error": "Write the reply first."}
    items = tools()
    tool = find_tool(items, ("reply", "respond", "message", "comment"))
    if not tool:
        return {"ok": False, "unavailable": True,
                "error": "This Metricool account exposes no reply tool.",
                "tools": [t["name"] for t in items]}
    values = {"conversation": conversation, "conversation_id": conversation,
              "thread": conversation, "id": conversation, "message": body,
              "text": body, "content": body, "body": body,
              "network": network, "platform": network}
    values.update(extra or {})
    args = build_arguments(tool["schema"], values)
    result = call(tool["name"], args)
    result["tool"] = tool["name"]
    result["arguments"] = args
    return result
