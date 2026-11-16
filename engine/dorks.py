"""Google dorks through Serper - the cheapest way to find shops claiming a brand.

For a brand, a handful of dork templates ("official store", "shop now", "outlet",
`site:myshopify.com`) are run through Serper's Google API (the key the app already has), the
domains in the organic results are collected, the brand's own domains are thrown away, and every
other domain is stored as a `fake_shop_candidate` signal with the query and the result as evidence.
"""
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from . import db

SERPER = "https://google.serper.dev/search"
TEMPLATES = (
    '"{brand}" official store',
    '"{brand}" shop now',
    '"{brand}" outlet -site:{domain}',
    'intitle:"{brand}" "buy online"',
    '"{brand}" "free shipping"',
    'site:myshopify.com "{brand}"',
    '"{brand}" "official website" -site:{domain}',
)
EMAIL_RX = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


def _key():
    from . import connectors
    key = (connectors.settings().get("serper_key") or "").strip()
    if not key:
        raise ValueError("No Serper API key - add one in Settings -> Connectors.")
    return key


def search(query, num=10, timeout=30):
    """One Google search through Serper; the organic results as {title, link, snippet}."""
    body = json.dumps({"q": query, "num": max(1, min(int(num or 10), 20))}).encode()
    request = urllib.request.Request(SERPER, data=body, headers={
        "X-API-KEY": _key(), "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as exc:
        raise ValueError("Serper said %s - the key may be out of credits." % exc.code)
    except (urllib.error.URLError, OSError) as exc:
        raise ValueError("Could not reach Serper: %s" % exc)
    results = []
    for item in data.get("organic") or []:
        link = str(item.get("link") or "").strip()
        if link:
            results.append({"title": str(item.get("title") or "")[:200],
                            "link": link[:400],
                            "snippet": str(item.get("snippet") or "")[:300]})
    return results


def _domain_of(url):
    text = str(url or "").strip()
    if "://" not in text:
        text = "https://" + text
    host = urllib.parse.urlsplit(text).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _brand_words(brand):
    return [word for word in re.split(r"[^a-z0-9]+", brand.lower()) if len(word) >= 3]


def domain_is_own(domain, own):
    domain = domain.lower()
    return any(domain == other or domain.endswith("." + other) for other in own)


def sweep(brand, own_domains=(), per_template=10, limit=100):
    """All dork templates for one brand; returns the candidate domains, storing each as a signal."""
    brand = str(brand or "").strip()
    if not brand:
        raise ValueError("Type a brand to sweep for.")
    own = [str(domain).strip().lower() for domain in own_domains if str(domain).strip()]
    queries = [template.format(brand=brand, domain=own[0] if own else "") for template in TEMPLATES]
    words = _brand_words(brand)
    candidates, seen, queries_run = [], set(), 0
    for query in queries:
        try:
            results = search(query, num=per_template)
        except ValueError:
            continue                     # one dud query should not kill the sweep
        queries_run += 1
        for result in results:
            domain = _domain_of(result["link"])
            if not domain or domain in seen or domain_is_own(domain, own):
                continue
            seen.add(domain)
            text = (result["title"] + " " + result["snippet"] + " " + domain).lower()
            if words and not any(word in text for word in words):
                continue                 # the result does not actually mention the brand
            item = {"brand": brand, "domain": domain, "query": query,
                    "title": result["title"], "link": result["link"]}
            candidates.append(item)
            db.insert("signals", {"ts": db.now(), "kind": "fake_shop_candidate", "domain": domain,
                                  "company": brand, "title": result["title"][:200],
                                  "source": "serper-dorks", "source_url": result["link"],
                                  "payload": json.dumps(item), "lead_id": 0, "seen": 0})
            if len(candidates) >= int(limit or 100):
                return {"ok": True, "brand": brand, "queries": queries_run,
                        "candidates": candidates, "truncated": True}
    return {"ok": True, "brand": brand, "queries": queries_run, "candidates": candidates,
            "truncated": False}


def sweep_brands(text, own_domains=(), per_brand=1, limit=200):
    """Several brands at once, from a textarea of names."""
    brands = [" ".join(line.split()) for line in str(text or "").replace(",", "\n").splitlines()]
    brands = [brand for brand in brands if brand][:int(per_brand or 10)]
    if not brands:
        raise ValueError("Type a brand to sweep for.")
    rows, errors = [], []
    for brand in brands:
        try:
            answer = sweep(brand, own_domains=own_domains, limit=max(1, limit // len(brands)))
            rows.extend(answer["candidates"])
        except ValueError as exc:
            errors.append("%s: %s" % (brand, exc))
    return {"ok": bool(rows), "brands": len(brands), "candidates": rows,
            "error": "" if rows else "; ".join(errors[:3]) or "Nothing found."}


def state():
    return {"candidates": db.scalar("SELECT COUNT(*) FROM signals WHERE kind='fake_shop_candidate'"),
            "recent": db.rows("SELECT domain, company, title, source_url, ts FROM signals "
                              "WHERE kind='fake_shop_candidate' ORDER BY ts DESC LIMIT 40")}
