"""Fake shops, scam domains and OSINT - the work behind the Red Points offer.

Three things live here, all native:

  * `scan()` - the fake-shop radar. A domain list (a domains-monitor.com zip/txt, any CSV of
    domains, or a URL) is matched against the brands being protected: the brand name must appear
    in the domain, with the extra pieces that make a fake shop recognisable ("shop", "store",
    "official", "outlet", a country code, "-" joins). No compiled matcher needed: 3.5k brands
    against 300k domains is a dict lookup per token.
  * `profile()` - what a bare domain actually is: title, description, visible text, emails,
    phones, and a guess at the business. This is what turns a domain with no email into a lead.
  * `meta_ads()` - advertisers in the public Meta Ad Library (no key, no credits), the HYROS ICP.

Everything is standard library; the only outside calls are the page fetches and the Ad Library.
"""
import html
import io
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import zipfile

from . import config, db, importer, mail

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/126.0 Safari/537.36")
SHOP_WORDS = ("shop", "store", "official", "outlet", "sale", "megastore", "boutique", "discount",
              "cheap", "buy", "order", "deals", "clearance", "factory")
TLDS = ("shop", "store", "online", "site", "website", "top", "xyz", "vip", "live", "club",
        "buzz", "fun", "icu", "cyou", "sbs", "cfd", "bargains", "deal", "outlet")
EMAIL_RX = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RX = re.compile(r"\+?\d[\d\s().-]{7,}\d")
META_DOC_ID = "24396387196335141"


def ensure_tables():
    db.execute("CREATE TABLE IF NOT EXISTS brands (id INTEGER PRIMARY KEY AUTOINCREMENT, "
               "name TEXT DEFAULT '', website TEXT DEFAULT '', company TEXT DEFAULT '', "
               "email TEXT DEFAULT '', created_ts INTEGER DEFAULT 0)")
    db.execute("CREATE TABLE IF NOT EXISTS osint_domains (domain TEXT PRIMARY KEY, "
               "title TEXT DEFAULT '', description TEXT DEFAULT '', business TEXT DEFAULT '', "
               "emails TEXT DEFAULT '', phones TEXT DEFAULT '', text TEXT DEFAULT '', "
               "fetched_ts INTEGER DEFAULT 0)")


def _key(value):
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def brand_list(text=""):
    """Brands to protect: the textarea, the brands table, or the brand offer keywords."""
    names = []
    for line in str(text or "").replace(",", "\n").splitlines():
        name = " ".join(line.split()).strip()
        if name and len(name) >= 2:
            names.append(name)
    if not names:
        ensure_tables()
        names = [row["name"] for row in db.rows("SELECT name FROM brands WHERE name<>''")]
    if not names:
        offer = db.one("SELECT keywords FROM offers WHERE lower(category) LIKE '%brand%' "
                       "OR lower(name) LIKE '%red point%' LIMIT 1")
        names = [word.strip() for word in (offer["keywords"] if offer else "").split(",")
                 if len(word.strip()) >= 3]
    return sorted({name for name in names if len(_key(name)) >= 3})


def save_brands(names):
    ensure_tables()
    db.execute("DELETE FROM brands")
    for name in names:
        db.insert("brands", {"name": name, "created_ts": db.now()})
    return len(names)


def _domains_from_bytes(raw, name=""):
    found = []
    if (name or "").lower().endswith(".zip") or raw[:2] == b"PK":
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                for member in archive.namelist()[:20]:
                    if member.endswith("/"):
                        continue
                    found.extend(_domains_from_bytes(archive.read(member), member))
        except zipfile.BadZipFile:
            pass
        return found
    text = raw.decode("utf-8", "replace")
    for line in text.splitlines():
        line = line.strip().strip('"').lower()
        if not line or line.startswith(("#", "domain")):
            continue
        candidate = line.split(",")[0].strip()
        candidate = re.sub(r"^https?://", "", candidate).split("/")[0]
        if re.match(r"^(?:[a-z0-9-]+\.)+[a-z]{2,24}$", candidate):
            found.append(candidate)
    return found


def read_domains(source):
    """Domains from a local zip/txt/csv or a URL; the file is read once, never kept."""
    source = str(source or "").strip()
    if not source:
        raise ValueError("Give the domains-monitor zip or a text file of domains.")
    if source.lower().startswith(("http://", "https://")):
        request = urllib.request.Request(source, headers={"User-Agent": UA})
        with urllib.request.urlopen(request, timeout=120) as response:
            raw = response.read(300 * 1048576)
        return _domains_from_bytes(raw, source)
    if not os.path.exists(source):
        raise ValueError("No file at %s" % source)
    with open(source, "rb") as handle:
        raw = handle.read(300 * 1048576)
    return _domains_from_bytes(raw, source)


def match_domain(domain, brands):
    """(brand, why) when the domain looks like a fake shop of that brand, else (None, '')."""
    label, _, tld = domain.partition(".")
    compact = _key(label)
    for brand in brands:
        key = _key(brand)
        if len(key) < 3 or key not in compact:
            continue
        if compact == key and tld not in TLDS:
            continue
        joined = bool(re.search(r"[.-][a-z0-9]+$", domain))
        extra = [word for word in SHOP_WORDS if word in compact]
        if compact != key:
            why = "brand + extra word" if (extra or joined) else "brand inside the domain"
        elif tld in TLDS:
            why = "brand on a shop-style ending (.%s)" % tld
        else:
            continue
        return brand, why
    return None, ""


def scan(source="", brands_text="", limit=5000, save_brands_list=True):
    """Match a domain feed against the brands; store the hits as fake-shop signals."""
    ensure_tables()
    brands = brand_list(brands_text)
    if not brands:
        raise ValueError("No brands to protect - type a few names or add them to the brands table.")
    if save_brands_list and str(brands_text or "").strip():
        save_brands(brands)
    domains = read_domains(source)
    hits = []
    for domain in domains[:int(limit or 5000)]:
        brand, why = match_domain(domain, brands)
        if brand:
            hits.append({"domain": domain, "brand": brand, "why": why})
    for hit in hits:
        db.insert("signals", {"ts": db.now(), "kind": "fake_shop", "domain": hit["domain"],
                              "company": hit["brand"], "title": "%s looks like %s (%s)" % (
                                  hit["domain"], hit["brand"], hit["why"]),
                              "source": "osint-radar", "payload": json.dumps(hit),
                              "lead_id": 0, "seen": 0})
    db.event("osint", "%d domains scanned, %d fake-shop hits" % (len(domains), len(hits)))
    return {"ok": True, "scanned": len(domains), "brands": len(brands),
            "hits": hits[:300], "hit_count": len(hits)}


def fetch_page(url, timeout=20):
    request = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(2 * 1048576).decode("utf-8", "replace")


def profile(domain, timeout=20):
    """What a domain is: title, description, emails, phones, a guess at the business."""
    ensure_tables()
    domain = re.sub(r"^https?://", "", str(domain or "").strip().lower()).split("/")[0]
    if not re.match(r"^(?:[a-z0-9-]+\.)+[a-z]{2,24}$", domain):
        raise ValueError("That is not a domain: %r" % domain)
    pages, texts, emails, phones, title, description = 0, [], set(), set(), "", ""
    for path in ("", "/contact", "/kontakt", "/about", "/o-nas", "/contact-us"):
        try:
            raw = fetch_page("https://" + domain + path, timeout=timeout)
        except (urllib.error.URLError, OSError, ValueError):
            try:
                raw = fetch_page("http://" + domain + path, timeout=timeout)
            except (urllib.error.URLError, OSError, ValueError):
                continue
        pages += 1
        lower = raw.lower()
        match = re.search(r"<title[^>]*>(.*?)</title>", raw, re.S | re.I)
        if match and not title:
            title = html.unescape(re.sub(r"\s+", " ", match.group(1))).strip()[:300]
        match = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']',
                          raw, re.S | re.I)
        if match and not description:
            description = html.unescape(match.group(1)).strip()[:400]
        text = html.unescape(re.sub(r"<[^>]+>", " ", raw))
        text = re.sub(r"\s+", " ", text)
        texts.append(text[:4000])
        emails.update(EMAIL_RX.findall(raw))
        phones.update(PHONE_RX.findall(text)[:10])
        if pages >= 2 and emails:
            break
    cleaned = [email.lower() for email in emails if not email.lower().endswith((".png", ".jpg", ".gif"))]
    business = ""
    blob = (" ".join([title, description] + texts)).lower()
    for keyword, label in (("dent", "dental"), ("roof", "roofing"), ("plumb", "plumbing"),
                           ("law", "legal"), ("advertis", "marketing agency"),
                           ("shop", "e-commerce"), ("store", "retail"), ("clinic", "clinic"),
                           ("software", "software"), ("hotel", "hospitality")):
        if keyword in blob:
            business = label
            break
    row = {"title": title, "description": description, "business": business,
           "emails": ",".join(sorted(cleaned)[:20]), "phones": ",".join(sorted(phones)[:10]),
           "text": " ".join(texts)[:4000], "fetched_ts": db.now()}
    db.execute("INSERT INTO osint_domains (domain, title, description, business, emails, phones, "
               "text, fetched_ts) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(domain) DO UPDATE SET "
               "title=excluded.title, description=excluded.description, business=excluded.business, "
               "emails=excluded.emails, phones=excluded.phones, text=excluded.text, "
               "fetched_ts=excluded.fetched_ts", (
                   domain, row["title"], row["description"], row["business"], row["emails"],
                   row["phones"], row["text"], row["fetched_ts"]))
    if cleaned:
        db.execute("UPDATE leads SET domain=? WHERE lower(email) LIKE ? AND coalesce(domain,'')=''",
                   (domain, "%@" + domain))
    return {"ok": True, "domain": domain, "pages": pages, **row}


def profile_new(limit=20):
    """Profile domains that have a lead with no email - the gap this closes."""
    ensure_tables()
    rows = db.rows("SELECT DISTINCT domain FROM leads WHERE coalesce(domain,'')<>'' "
                   "AND coalesce(email,'')='' LIMIT ?", (int(limit),))
    done = []
    for row in rows:
        try:
            done.append(profile(row["domain"]))
        except (ValueError, urllib.error.URLError, OSError):
            continue
    return {"ok": True, "profiled": len(done), "domains": done[:20]}


def meta_ads(keywords, country="", limit=30, timeout=40):
    """Ad Library search by keyword - page name, page URL and the ads running."""
    terms = [word.strip() for word in str(keywords or "").replace(",", "\n").splitlines() if word.strip()]
    if not terms:
        raise ValueError("Give a few keywords to search the Ad Library for.")
    results, errors = [], []
    for term in terms[:5]:
        payload = {"doc_id": META_DOC_ID, "variables": json.dumps({
            "activeStatus": "ACTIVE", "adType": "ALL", "bylines": [], "collationToken": None,
            "contentLanguages": [], "countries": [country.upper()] if country else [],
            "excludedIDs": None, "first": max(1, min(int(limit or 30), 50)),
            "isTargetingIreland": False, "queryString": term, "searchType": "KEYWORD_EXACT_PHRASE",
            "sessionID": str(db.now()), "sortData": "RELEVANCE", "viewAllPageID": None})}
        request = urllib.request.Request(
            "https://www.facebook.com/api/graphql/",
            data=urllib.parse.urlencode(payload).encode(),
            headers={"User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded",
                     "X-FB-LSD": "", "Origin": "https://www.facebook.com"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8", "replace") or "{}")
        except (urllib.error.URLError, OSError, ValueError) as exc:
            errors.append("%s: %s" % (term, str(exc)[:90]))
            continue
        for item in _ad_items(data):
            results.append(item)
    for item in results:
        db.insert("signals", {"ts": db.now(), "kind": "meta_advertiser",
                              "domain": item.get("domain", ""), "company": item.get("page", ""),
                              "title": "%s is advertising on Meta" % (item.get("page") or ""),
                              "source": "meta-ad-library", "source_url": item.get("page_url", ""),
                              "payload": json.dumps(item), "lead_id": 0, "seen": 0})
    return {"ok": bool(results), "found": len(results), "advertisers": results[:200],
            "error": "" if results else ("Meta did not answer: " + "; ".join(errors[:2]) if errors
                                         else "Nothing found for those keywords.")}


def _ad_items(data):
    """Walk the Ad Library answer for the advertiser rows, whatever its exact shape."""
    items = []

    def walk(node, depth=0):
        if depth > 8:
            return
        if isinstance(node, dict):
            page = node.get("page_name") or node.get("pageName") or (node.get("page") or {}).get("name")
            if page:
                url = (node.get("page_url") or (node.get("page") or {}).get("url") or "")
                snapshot = node.get("snapshot") or {}
                link = snapshot.get("link_url") or snapshot.get("linkUrl") or ""
                domain = re.sub(r"^https?://", "", link).split("/")[0] if link else ""
                items.append({"page": str(page)[:120], "page_url": str(url)[:300],
                              "domain": domain, "link_url": str(link)[:300],
                              "ads": int(node.get("collation_count") or 1)})
            for value in node.values():
                walk(value, depth + 1)
        elif isinstance(node, list):
            for value in node[:200]:
                walk(value, depth + 1)

    walk(data)
    seen, unique = set(), []
    for item in items:
        key = item["page"].lower()
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def state():
    """Everything the OSINT tab shows."""
    ensure_tables()
    red_points = db.one("SELECT name, affiliate_url FROM offers WHERE category='brand' "
                        "OR lower(name) LIKE '%red point%' LIMIT 1")
    return {"ok": True, "brands": len(brand_list()),
            "fake_shops": db.scalar("SELECT COUNT(*) FROM signals WHERE kind='fake_shop'"),
            "recent_fake_shops": db.rows(
                "SELECT domain, company, title, ts FROM signals WHERE kind='fake_shop' "
                "ORDER BY ts DESC LIMIT 50"),
            "lookalikes": db.scalar("SELECT COUNT(*) FROM signals WHERE kind='new_lookalike_domain'"),
            "meta_advertisers": db.scalar("SELECT COUNT(*) FROM signals WHERE kind='meta_advertiser'"),
            "profiled": db.scalar("SELECT COUNT(*) FROM osint_domains"),
            "red_points": dict(red_points) if red_points else {},
            "scam_jobs": ["brand_watch", "phishing", "scam_radar", "meta_ads"],
            "brand_offer": dict(red_points) if red_points else {}}
