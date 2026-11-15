"""The Ads hub: one page for the advertising stack, one tab per platform.

Metricool is read through its MCP server (already connected in Social). HYROS, PLAI and
DataFast are plain REST APIs with different auth headers, so each one keeps its own key,
base URL and optional path in `data/settings.json`. Nothing here invents an endpoint: a
platform is only queried at the URL you give it, and the raw JSON comes back.
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config, db

PLATFORMS = {
    "metricool": {
        "label": "Metricool Ads", "kind": "mcp",
        "note": "Ad accounts and campaign performance through the Metricool connection in Social.",
        "docs": "https://metricool.com/",
    },
    "hyros": {
        "label": "HYROS Ad Tracking", "kind": "api",
        "note": "Attribution, leads and sales. Auth header: API-Key.",
        "docs": "https://hyros.com/updates/changelog/version-2-2/",
        "base_url": "https://api.hyros.com/v1", "auth": "API-Key", "path": "",
    },
    "plai": {
        "label": "PLAI", "kind": "api",
        "note": "AI ad management for Meta, Instagram and LinkedIn. API access is per account, "
                "so paste the base URL your PLAI contact gives you.",
        "docs": "https://www.plai.io/enterprise",
        "base_url": "", "auth": "Bearer", "path": "",
    },
    "datafast": {
        "label": "DataFast", "kind": "api",
        "note": "Revenue attribution. A df_ website key or a dft_ account token.",
        "docs": "https://datafa.st/docs/api",
        "base_url": "https://datafa.st/api", "auth": "Bearer", "path": "",
    },
}


def platform(name):
    key = str(name or "").strip().lower()
    if key not in PLATFORMS:
        raise ValueError("Unknown ad platform.")
    return dict(PLATFORMS[key])


def _block(name):
    return dict((config.load_settings().get("ads") or {}).get(name) or {})


def _save(name, patch):
    settings = config.load_settings()
    ads = dict(settings.get("ads") or {})
    block = dict(ads.get(name) or {})
    block.update(patch)
    ads[name] = block
    settings["ads"] = ads
    config.save_settings(settings)
    return block


def save(name, payload):
    """Store the key, base URL and path for one platform (a blank key keeps the stored one)."""
    entry = platform(name)
    key = str(payload.get("api_key") or "").strip()
    base = str(payload.get("base_url") or "").strip().rstrip("/")
    path = str(payload.get("path") or "").strip()
    if base and not base.lower().startswith(("http://", "https://")):
        return {"ok": False, "error": "The base URL has to start with http:// or https://"}
    patch = {"base_url": base, "path": path, "updated_ts": db.now()}
    if key:
        patch["api_key"] = key
    _save(name, patch)
    return {"ok": True, "platform": entry["label"]}


def status(name):
    entry = platform(name)
    block = _block(name)
    return {"name": name, "label": entry["label"], "kind": entry["kind"],
            "note": entry["note"], "docs": entry["docs"],
            "has_key": bool(str(block.get("api_key") or "").strip()),
            "base_url": block.get("base_url") or entry.get("base_url") or "",
            "path": block.get("path") or "",
            "auth": entry.get("auth") or ("Bearer" if entry["kind"] == "api" else ""),
            "updated_ts": int(block.get("updated_ts") or 0),
            "last_test": int(block.get("last_test") or 0),
            "last_ok": int(block.get("last_ok") or 0),
            "last_status": int(block.get("last_status") or 0)}


def status_all():
    return [status(name) for name in PLATFORMS]


def _request(name, path=""):
    entry = platform(name)
    block = _block(name)
    key = str(block.get("api_key") or "").strip()
    base = str(block.get("base_url") or entry.get("base_url") or "").strip().rstrip("/")
    target = str(path or block.get("path") or "").strip()
    if not base:
        return {"ok": False, "error": "No base URL yet - paste the one from %s." % entry["docs"]}
    url = base + (target if target.startswith("/") or not target else "/" + target)
    headers = {"Accept": "application/json",
               "User-Agent": "LeadStudioLite/%s (local app)" % config.VERSION}
    if key:
        if (entry.get("auth") or "Bearer") == "API-Key":
            headers["API-Key"] = key
        else:
            headers["Authorization"] = "Bearer " + key
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            body = response.read().decode("utf-8", "replace")
            return {"ok": True, "status": response.status, "url": url,
                    "data": _maybe_json(body)}
    except urllib.error.HTTPError as exc:
        raw = ""
        try:
            raw = exc.read().decode("utf-8", "replace")
        except (OSError, ValueError):
            pass
        return {"ok": False, "status": exc.code, "url": url,
                "error": "%s answered HTTP %s: %s" % (entry["label"], exc.code, raw[:300])}
    except (urllib.error.URLError, OSError) as exc:
        return {"ok": False, "status": 0, "url": url,
                "error": "Could not reach %s: %s" % (entry["label"], exc)}


def _maybe_json(body):
    try:
        data = json.loads(body or "{}")
    except ValueError:
        return {"text": (body or "")[:4000]}
    return data if isinstance(data, (dict, list)) else {"value": data}


def test(name):
    entry = platform(name)
    if entry["kind"] != "api":
        return {"ok": False, "error": "%s is read through its MCP connection, not an API key."
                                     % entry["label"]}
    answer = _request(name)
    _save(name, {"last_test": db.now(), "last_ok": 1 if answer["ok"] else 0,
                 "last_status": answer.get("status") or 0})
    return {**answer, "platform": entry["label"]}


def pull(name, path=""):
    entry = platform(name)
    if entry["kind"] != "api":
        return {"ok": False, "error": "%s has no REST pull - use the Metricool connection."
                                     % entry["label"]}
    answer = _request(name, path)
    if answer["ok"]:
        db.event("ads", "%s pulled %s" % (entry["label"], urllib.parse.urlsplit(answer["url"]).path))
    return {**answer, "platform": entry["label"]}
