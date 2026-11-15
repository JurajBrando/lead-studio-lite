"""HYROS AIR is a paid Hyros service, not a model this app runs.

You subscribe to it at Hyros; the app is only a client. Everything AIR does lives in your Hyros
account, and the HYROS API exposes it with the same `API-Key` the Ad Tracking tab already stores,
so this module reads the leads, sales and calls behind that key and hands the rows to the page.

There is deliberately no LLM here: an answer typed by this app would not be HYROS AIR, and
pretending otherwise is exactly the confusion worth avoiding.
"""
from . import ads, db

BASE = "https://api.hyros.com/v1"
LINKS = {
    "product": "https://hyros.com/enterprise",
    "docs": "https://docs.hyros.com/",
    "api": "https://api-docs.hyros.com/",
    "mcp": "https://mcp.hyros.com/mcp",
}
# Paths from the published Hyros API (v1.46). Editable per installation in data/settings.json
# under `hyros_air.paths` if Hyros moves them.
PATHS = {
    "leads": ads.HYROS_PATHS["leads"],
    "sales": ads.HYROS_PATHS["sales"],
    "calls": ads.HYROS_PATHS["calls"],
    "ad-accounts": ads.HYROS_PATHS["ad_accounts"],
}


def _block():
    from . import config
    return dict(config.load_settings().get("hyros_air") or {})


def _paths():
    merged = dict(PATHS)
    stored = _block().get("paths")
    if isinstance(stored, dict):
        merged.update({key: value for key, value in stored.items()
                       if isinstance(value, str) and value.strip()})
    return merged


def _product_link():
    """The HYROS link: the affiliate URL from the Toolbox when there is one, else the plain page."""
    rows = db.rows("SELECT name, affiliate_url FROM offers WHERE active=1 "
                   "AND affiliate_url<>'' ORDER BY id")
    for row in rows:
        if "HYROS" in (row["name"] or "").upper():
            return row["affiliate_url"], True
    return LINKS["product"], False


def status():
    hyros = ads.status("hyros")
    block = _block()
    product, affiliate = _product_link()
    return {"has_key": hyros["has_key"],
            "base_url": hyros["base_url"] or BASE,
            "last_ok": hyros["last_ok"], "last_status": hyros["last_status"],
            "last_pull": int(block.get("last_pull") or 0),
            "last_count": int(block.get("last_count") or 0),
            "paths": _paths(),
            "links": {**LINKS, "product": product, "affiliate": affiliate},
            "note": "HYROS AIR is a paid Hyros add-on (Enterprise). This page reads what it did "
                    "in your Hyros account through the HYROS API key from the Ad Tracking tab."}


def pull(kind="leads", limit=100):
    """Read one HYROS collection (leads / sales / calls) with the stored HYROS API key."""
    kind = str(kind or "").strip().lower()
    paths = _paths()
    if kind not in paths:
        return {"ok": False, "error": "Unknown collection: %s" % kind}
    if not ads.status("hyros")["has_key"]:
        return {"ok": False,
                "error": "No HYROS API key yet. Add it in Ads -> HYROS Ad Tracking, then come back."}
    path = paths[kind]
    separator = "&" if "?" in path else "?"
    answer = ads.pull("hyros", "%s%slimit=%d" % (path, separator, max(1, min(500, int(limit)))))
    if not answer.get("ok"):
        return answer
    rows = _rows_of(answer.get("data"))
    _remember(len(rows))
    return {"ok": True, "kind": kind, "count": len(rows), "rows": rows[:200],
            "raw": answer.get("data") if not rows else None, "url": answer.get("url"),
            "status": answer.get("status")}


def _rows_of(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("data", "items", "results", "leads", "sales", "calls"):
            value = data.get(key)
            if isinstance(value, list):
                return value
    return []


def _remember(count):
    from . import config
    settings = config.load_settings()
    block = dict(settings.get("hyros_air") or {})
    block.update({"last_pull": db.now(), "last_count": int(count)})
    settings["hyros_air"] = block
    config.save_settings(settings)


def save_paths(paths):
    """Let the user correct an endpoint path if Hyros moves it."""
    from . import config
    clean = {}
    for key, value in (paths or {}).items():
        if key in PATHS and isinstance(value, str) and value.strip().startswith("/"):
            clean[key] = value.strip()
    settings = config.load_settings()
    block = dict(settings.get("hyros_air") or {})
    block["paths"] = {**PATHS, **clean}
    settings["hyros_air"] = block
    config.save_settings(settings)
    return {"ok": True, "paths": _paths()}
