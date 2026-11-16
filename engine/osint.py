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
RED_POINTS = {"name": "Red Points - brand protection",
              "affiliate_url": "https://www.redpoints.com/request-demo-affiliate/?referredid=255256383275"}


def ensure_tables():
    # Brands typed here are names, not domains, so they cannot live in the `brands` table (its
    # domain column is NOT NULL UNIQUE). This one is ours.
    db.execute("CREATE TABLE IF NOT EXISTS osint_brands (name TEXT PRIMARY KEY, "
               "created_ts INTEGER DEFAULT 0)")
    db.execute("CREATE TABLE IF NOT EXISTS osint_domains (domain TEXT PRIMARY KEY, "
               "title TEXT DEFAULT '', description TEXT DEFAULT '', business TEXT DEFAULT '', "
               "emails TEXT DEFAULT '', phones TEXT DEFAULT '', text TEXT DEFAULT '', "
               "platforms TEXT DEFAULT '', favicon_hash TEXT DEFAULT '', rdap TEXT DEFAULT '', "
               "fetched_ts INTEGER DEFAULT 0)")


def _key(value):
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def brand_list(text=""):
    """Brands to protect: the textarea, the names saved for OSINT, the brands table, or the offer."""
    names = []
    for line in str(text or "").replace(",", "\n").splitlines():
        name = " ".join(line.split()).strip()
        if name and len(name) >= 2:
            names.append(name)
    if not names:
        ensure_tables()
        names = [row["name"] for row in db.rows("SELECT name FROM osint_brands WHERE name<>''")]
    if not names:
        names = [row["name"] for row in db.rows("SELECT name FROM brands WHERE name<>''")]
    if not names:
        offer = db.one("SELECT keywords FROM offers WHERE lower(category) LIKE '%brand%' "
                       "OR lower(name) LIKE '%red point%' LIMIT 1")
        names = [word.strip() for word in (offer["keywords"] if offer else "").split(",")
                 if len(word.strip()) >= 3]
    return sorted({name for name in names if len(_key(name)) >= 3})


def save_brands(names):
    ensure_tables()
    db.execute("DELETE FROM osint_brands")
    for name in names:
        db.insert("osint_brands", {"name": name, "created_ts": db.now()})
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
    platform = platforms_of(" ".join(texts) + " " + description)
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
           "text": " ".join(texts)[:4000], "platforms": ",".join(platform),
           "fetched_ts": db.now()}
    db.execute("INSERT INTO osint_domains (domain, title, description, business, emails, phones, "
               "text, fetched_ts) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(domain) DO UPDATE SET "
               "title=excluded.title, description=excluded.description, business=excluded.business, "
               "emails=excluded.emails, phones=excluded.phones, text=excluded.text, "
               "platforms=excluded.platforms, fetched_ts=excluded.fetched_ts", (
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


PLATFORMS = (
    ("shopify", ("cdn.shopify.com", "shopify.theme", "shopify-features", "myshopify")),
    ("woocommerce", ("woocommerce", "wc-ajax", "wp-content/plugins/woocommerce")),
    ("wix", ("wixstatic", "_wixcss", "wix-code")),
    ("bigcommerce", ("bigcommerce.com", "stencil-utils")),
    ("squarespace", ("squarespace.com", "static1.squarespace")),
    ("magento", ("magento", "mage/cookies")),
    ("prestashop", ("prestashop",)),
    ("wordpress", ("wp-content", "wp-includes")),
)


def _mmh3_32(data, seed=0):
    """MurmurHash3 x86_32 - the hash Shodan calls http.favicon.hash, without a dependency."""
    data = bytearray(data)
    length = len(data)
    c1, c2, h1 = 0xcc9e2d51, 0x1b873593, seed & 0xFFFFFFFF

    def rot(value, bits):
        return ((value << bits) | (value >> (32 - bits))) & 0xFFFFFFFF

    blocks = length // 4
    for index in range(blocks):
        base = index * 4
        k1 = data[base] | (data[base + 1] << 8) | (data[base + 2] << 16) | (data[base + 3] << 24)
        k1 = (k1 * c1) & 0xFFFFFFFF
        k1 = rot(k1, 15)
        k1 = (k1 * c2) & 0xFFFFFFFF
        h1 ^= k1
        h1 = rot(h1, 13)
        h1 = (h1 * 5 + 0xE6546B64) & 0xFFFFFFFF
    tail = length & 3
    k1 = 0
    base = blocks * 4
    if tail >= 3:
        k1 ^= data[base + 2] << 16
    if tail >= 2:
        k1 ^= data[base + 1] << 8
    if tail >= 1:
        k1 ^= data[base]
        k1 = (k1 * c1) & 0xFFFFFFFF
        k1 = rot(k1, 15)
        k1 = (k1 * c2) & 0xFFFFFFFF
        h1 ^= k1
    h1 ^= length
    h1 ^= h1 >> 16
    h1 = (h1 * 0x85EBCA6B) & 0xFFFFFFFF
    h1 ^= h1 >> 13
    h1 = (h1 * 0xC2B2AE35) & 0xFFFFFFFF
    h1 ^= h1 >> 16
    return h1 - 0x100000000 if h1 > 0x7FFFFFFF else h1


def favicon_hash(raw):
    import base64
    if not raw:
        return ""
    return str(_mmh3_32(base64.encodebytes(bytes(raw))))


def platforms_of(page):
    lower = (page or "").lower()
    return [name for name, markers in PLATFORMS if any(marker in lower for marker in markers)]


def certificates(domain, limit=300, timeout=90):
    """Domains that received a certificate containing the token - crt.sh, free, no key."""
    token = urllib.parse.quote(domain)
    request = urllib.request.Request("https://crt.sh/?q=" + token + "&output=json",
                                     headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        rows = json.loads(response.read().decode("utf-8", "replace") or "[]")
    names = set()
    for row in rows:
        for name in str(row.get("name_value") or "").split("\n"):
            name = name.strip().lower().lstrip("*.")
            if re.match(r"^(?:[a-z0-9-]+\.)+[a-z]{2,24}$", name):
                names.add(name)
            if len(names) >= int(limit or 300):
                break
    return sorted(names)


def rdap(domain, timeout=30):
    """Registration date, registrar, nameservers - if the free RDAP service answers."""
    request = urllib.request.Request("https://rdap.org/domain/" + urllib.parse.quote(domain),
                                     headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8", "replace") or "{}")
    out = {"domain": domain, "registered": "", "expires": "", "registrar": "", "nameservers": []}
    for event in data.get("events") or []:
        action = str(event.get("eventAction") or "").lower()
        when = str(event.get("eventDate") or "")[:10]
        if action == "registration":
            out["registered"] = when
        elif action == "expiration":
            out["expires"] = when
    for entity in data.get("entities") or []:
        if "registrar" in (entity.get("roles") or []):
            for item in (entity.get("vcardArray") or [None, []])[1] or []:
                if item and item[0] == "fn":
                    out["registrar"] = str(item[3])[:120]
    out["nameservers"] = [str(item.get("ldhName") or "").lower()
                          for item in (data.get("nameservers") or [])][:6]
    return out


def _connector_key(name):
    from . import connectors
    settings = connectors.settings()
    key = str(settings.get(name + "_key") or "").strip()
    return key


def enrich(domain):
    """RDAP + favicon hash + the shop platform - one call per suspect domain."""
    answer = profile(domain)
    page = ""
    try:
        page = fetch_page("https://" + domain)
    except (urllib.error.URLError, OSError, ValueError):
        pass
    favicon = ""
    for path in ("/favicon.ico", "/favicon.png"):
        try:
            request = urllib.request.Request("https://" + domain + path, headers={"User-Agent": UA})
            with urllib.request.urlopen(request, timeout=20) as response:
                favicon = favicon_hash(response.read(1 << 20))
            if favicon:
                break
        except (urllib.error.URLError, OSError, ValueError):
            continue
    platform = platforms_of(page)
    try:
        whois = rdap(domain)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        whois = {"error": str(exc)[:120]}
    db.execute("UPDATE osint_domains SET favicon_hash=?, platforms=?, rdap=? WHERE domain=?",
               (favicon, ",".join(platform), json.dumps(whois)[:4000], domain))
    return {"ok": True, "domain": domain, "favicon_hash": favicon, "platforms": platform,
            "rdap": whois, "title": answer.get("title", ""), "emails": answer.get("emails", "")}


def discover(brand, own=(), limit=200):
    """crt.sh for the brand token -> domains that got a certificate, minus the brand's own."""
    brand = str(brand or "").strip()
    if len(_key(brand)) < 3:
        raise ValueError("Type a brand to search certificates for.")
    own = [str(domain).strip().lower() for domain in own if str(domain).strip()]
    token = _key(brand)
    found = []
    for name in certificates(brand, limit=limit):
        if _key(name.split(".")[0]).find(token) < 0:
            continue
        if any(name == other or name.endswith("." + other) for other in own):
            continue
        found.append({"brand": brand, "domain": name, "source": "crt.sh"})
    for item in found:
        db.insert("signals", {"ts": db.now(), "kind": "fake_shop_candidate", "domain": item["domain"],
                              "company": brand, "title": "%s in a certificate for %s" % (
                                  item["domain"], brand),
                              "source": "crt.sh", "payload": json.dumps(item), "lead_id": 0,
                              "seen": 0})
    return {"ok": True, "brand": brand, "candidates": found, "count": len(found)}


def keys():
    """Only key-gated service left is Serper, which the dork sweep reuses."""
    return {"serper": bool(_connector_key("serper"))}


def state():
    """Everything the OSINT tab shows."""
    ensure_tables()
    red_points = db.one("SELECT name, affiliate_url FROM offers WHERE category='brand' "
                        "OR lower(name) LIKE '%red point%' LIMIT 1")
    red_points = dict(red_points) if red_points else dict(RED_POINTS)
    return {"ok": True, "brands": len(brand_list()),
            "fake_shops": db.scalar("SELECT COUNT(*) FROM signals WHERE kind='fake_shop'"),
            "recent_fake_shops": db.rows(
                "SELECT domain, company, title, ts FROM signals WHERE kind='fake_shop' "
                "ORDER BY ts DESC LIMIT 50"),
            "lookalikes": db.scalar("SELECT COUNT(*) FROM signals WHERE kind='new_lookalike_domain'"),
            "meta_advertisers": db.scalar("SELECT COUNT(*) FROM signals WHERE kind='meta_advertiser'"),
            "profiled": db.scalar("SELECT COUNT(*) FROM osint_domains"),
            "keys": keys(),
            "candidates": db.scalar("SELECT COUNT(*) FROM signals WHERE kind='fake_shop_candidate'"),
            "red_points": red_points,
            "scam_jobs": ["brand_watch", "phishing", "scam_radar", "meta_ads"],
            "brand_offer": red_points}
