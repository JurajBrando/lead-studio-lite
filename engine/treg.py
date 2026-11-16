"""Treg - one token for 4,100+ paid tool endpoints, metered per call.

Setup lives in Settings -> Connectors (`connectors.treg_key`, the team token from treg.to). Over
HTTP the catalog is `GET /catalog/search?q=...` and a tool is
`POST /call/<endpoint-id>` with a JSON body, the `X-Treg-Token` header and the answer verbatim.

The routed tools (`treg.<capability>`) are the ones worth using: treg picks the provider, falls
back when one fails, and bills only the answer it returns. Some routes are free -
`treg.people.email.verify` answered with `X-Treg-Cost-Micro: 0` in setup - and every response
carries `_treg.charged_micro`, so the UI can show the real price of the last call.
"""
import base64
import json
import urllib.error
import urllib.parse
import urllib.request

from . import config

BASE = "https://treg.to"
TIMEOUT = 180

# The routed tools Lead Studio actually needs, with what they cost in practice.
CAPABILITIES = (
    {"id": "treg.people.email.verify", "label": "Verify an email",
     "body": {"email": "{email}"}, "free": False},
    {"id": "treg.people.email.find", "label": "Find a work email",
     "body": {"domain": "{domain}"}, "free": False},
    {"id": "treg.google.serp.maps", "label": "Google Maps businesses",
     "body": {"q": "{query}"}, "free": False},
    {"id": "treg.google.maps.reviews", "label": "Google Maps reviews",
     "body": {"place_id": "{place_id}"}, "free": False},
)


def token():
    settings = config.load_settings() or {}
    return str((settings.get("connectors") or {}).get("treg_key") or "").strip()


def _token_or_error():
    key = token()
    if not key:
        raise ValueError("No Treg token - add your team token in Settings -> Connectors.")
    return key


def _decode(token_value):
    """The org and scope inside the team token, for the status line (no signature check)."""
    parts = str(token_value or "").split(".")
    candidates = [parts[1], parts[0]] if len(parts) >= 3 else [parts[0]]
    for payload in candidates:
        if not payload:
            continue
        try:
            padded = payload + "=" * (-len(payload) % 4)
            claims = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8", "replace"))
        except (ValueError, IndexError):
            continue
        if isinstance(claims, dict) and claims:
            return claims
    return {}


def _request(path, data=None, timeout=TIMEOUT):
    key = _token_or_error()
    request = urllib.request.Request(
        BASE + path, data=json.dumps(data).encode() if data is not None else None,
        headers={"X-Treg-Token": key, "Accept": "application/json",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(2 * 1048576).decode("utf-8", "replace") or "{}"
            cost = int(response.headers.get("X-Treg-Cost-Micro") or 0)
            return {"ok": True, "data": json.loads(body), "cost_micro": cost}
    except urllib.error.HTTPError as exc:
        body = exc.read(4096).decode("utf-8", "replace")
        try:
            detail = json.loads(body).get("detail")
        except (ValueError, AttributeError):
            detail = body[:300]
        return {"ok": False, "error": str(detail if isinstance(detail, str) else json.dumps(detail))[:400],
                "status": exc.code}
    except (urllib.error.URLError, OSError) as exc:
        return {"ok": False, "error": "Could not reach treg.to: %s" % str(exc)[:200]}


def status():
    key = token()
    claims = _decode(key) if key else {}
    return {"ok": bool(key), "has_token": bool(key), "org": str(claims.get("org") or ""),
            "scope": str(claims.get("scp") or ""),
            "note": "" if key else "Add the team token in Settings -> Connectors."}


def catalog_search(query, limit=20):
    answer = _request("/catalog/search?" + urllib.parse.urlencode(
        {"q": str(query or ""), "limit": max(1, min(int(limit or 20), 60))}))
    if not answer["ok"]:
        return answer
    data = answer["data"]
    return {"ok": True, "total": data.get("total"), "results": [
        {"id": row.get("id"), "provider": row.get("provider"),
         "name": row.get("name"), "display": row.get("provider_display")}
        for row in (data.get("results") or [])]}


def call(tool_id, body=None, timeout=TIMEOUT):
    answer = _request("/call/" + str(tool_id), data=body if body is not None else {})
    if not answer["ok"]:
        return answer
    data = answer.get("data") or {}
    treg_meta = data.get("_treg") or {}
    charged = int(treg_meta.get("charged_micro") or answer.get("cost_micro") or 0)
    return {"ok": True, "tool": tool_id, "output": data.get("output"), "raw": data.get("raw"),
            "provider": treg_meta.get("served_by") or treg_meta.get("provider") or "",
            "charged_micro": charged, "free": charged == 0}


def verify_email(email):
    email = str(email or "").strip().lower()
    if "@" not in email:
        return {"ok": False, "error": "That is not an email address."}
    answer = call("treg.people.email.verify", {"email": email})
    if not answer.get("ok"):
        return answer
    output = answer.get("output") or {}
    status_value = str(output.get("status") or ("valid" if output.get("valid") else "unknown"))
    return {"ok": True, "email": email, "status": status_value, "valid": bool(output.get("valid")),
            "provider": answer["provider"], "free": answer["free"],
            "charged_micro": answer["charged_micro"]}


def maps_search(query, limit=20):
    answer = call("treg.google.serp.maps", {"q": " ".join(str(query or "").split())})
    if not answer.get("ok"):
        return answer
    places = ((answer.get("output") or {}).get("places") or [])[:max(1, int(limit or 20))]
    rows = []
    for place in places:
        rows.append({"name": place.get("name") or "", "address": place.get("address") or "",
                     "phone": place.get("phone") or "", "website": place.get("website") or "",
                     "rating": place.get("rating"), "reviews": place.get("review_count"),
                     "place_id": place.get("place_id") or "", "category": place.get("category") or ""})
    return {"ok": True, "places": rows, "count": len(rows),
            "charged_micro": answer["charged_micro"], "free": answer["free"]}

