"""Local businesses from OpenStreetMap, for whatever the user sells.

No API key: a free-text offer ("roofers in Slovakia", "dentists, Bratislava", "yoga studio")
becomes OSM tags, a place name becomes a bounding box through Nominatim, and the Overpass API
returns the businesses with whatever contact details they publish. Rows land in `osm_business`
so the same search is never paid twice, and the three lists the lead flow needs - with an email,
with only a website, with only a phone - are queries over that table.

Data is ODbL: anything published from it must credit "(c) OpenStreetMap contributors". Emailing
people found this way is the user's own GDPR call, so the UI says so.
"""
import html
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config, db, importer

OVERPASS = ("https://overpass-api.de/api/interpreter",
            "https://overpass.kumi.systems/api/interpreter",
            "https://overpass.osm.ch/api/interpreter",
            "https://overpass.private.coffee/api/interpreter")
NOMINATIM = "https://nominatim.openstreetmap.org/search"
UA = "LeadStudioLite/%s (local lead research)" % config.VERSION

# What a user might type -> the OSM tags that describe it. Enough coverage that "any offer"
# works for the usual trades and services; anything else falls through to a name/craft/shop
# regex, which is how OSM itself searches.
CATEGORIES = {
    "roof": ["craft=roofer"], "gutter": ["craft=roofer"], "roofer": ["craft=roofer"],
    "plumb": ["craft=plumber"], "plumber": ["craft=plumber"],
    "electric": ["craft=electrician"], "electrician": ["craft=electrician"],
    "builder": ["craft=builder", "office=construction_company"],
    "construction": ["office=construction_company", "craft=builder"],
    "painter": ["craft=painter"], "painting": ["craft=painter"],
    "carpenter": ["craft=carpenter"], "joiner": ["craft=carpenter"],
    "hvac": ["craft=hvac"], "heating": ["craft=hvac"],
    "gardener": ["craft=gardener"], "landscap": ["craft=gardener", "leisure=garden_centre"],
    "clean": ["shop=cleaning", "office=cleaning", "craft=cleaning"],
    "dentist": ["healthcare=dentist", "amenity=dentist"], "dental": ["healthcare=dentist"],
    "doctor": ["amenity=doctors"], "clinic": ["amenity=clinic", "amenity=doctors"],
    "physio": ["healthcare=physiotherapist"], "veterinar": ["amenity=veterinary"],
    "pharmac": ["amenity=pharmacy"],
    "restaurant": ["amenity=restaurant"], "cafe": ["amenity=cafe"], "coffee": ["amenity=cafe"],
    "bar": ["amenity=bar"], "pub": ["amenity=pub"], "bakery": ["shop=bakery"],
    "butcher": ["shop=butcher"], "florist": ["shop=florist"],
    "hotel": ["tourism=hotel"], "guest house": ["tourism=guest_house"],
    "hairdress": ["shop=hairdresser"], "barber": ["shop=hairdresser"],
    "beauty": ["shop=beauty"], "spa": ["leisure=spa", "shop=beauty"],
    "massage": ["shop=massage"], "tattoo": ["shop=tattoo"],
    "gym": ["leisure=fitness_centre"], "fitness": ["leisure=fitness_centre"],
    "yoga": ["leisure=fitness_centre", "sport=yoga"], "pilates": ["leisure=fitness_centre"],
    "accountant": ["office=accountant", "office=tax_advisor"],
    "tax": ["office=tax_advisor"], "bookkeep": ["office=accountant"],
    "lawyer": ["office=lawyer"], "legal": ["office=lawyer"], "notary": ["office=notary"],
    "estate agent": ["office=estate_agent"], "real estate": ["office=estate_agent"],
    "insurance": ["office=insurance"], "financial": ["office=financial"],
    "marketing": ["office=marketing"], "advertis": ["office=advertising_agency"],
    "web design": ["office=it"], "software": ["office=it"], "it ": ["office=it"],
    "consult": ["office=consulting"],
    "photograph": ["craft=photographer", "shop=photo"],
    "car repair": ["shop=car_repair"], "mechanic": ["shop=car_repair"],
    "car dealer": ["shop=car"], "tyre": ["shop=tyres"],
    "travel": ["shop=travel_agency"], "driving school": ["amenity=driving_school"],
    "school": ["amenity=school"], "kindergarten": ["amenity=kindergarten"],
    "funeral": ["shop=funeral_directors"], "laundry": ["shop=laundry"],
    "optician": ["shop=optician"], "jewel": ["shop=jewelry"],
    "furniture": ["shop=furniture"], "hardware": ["shop=doityourself", "shop=hardware"],
}

QUESTION = "Hi, I found {name} on OpenStreetMap - do you still handle {what} in {place}?"


def ensure_table():
    db.execute(
        "CREATE TABLE IF NOT EXISTS osm_business (id TEXT PRIMARY KEY, name TEXT DEFAULT '', "
        "category TEXT DEFAULT '', email TEXT DEFAULT '', website TEXT DEFAULT '', "
        "phone TEXT DEFAULT '', city TEXT DEFAULT '', area TEXT DEFAULT '', "
        "address TEXT DEFAULT '', tags TEXT DEFAULT '', fetched_ts INTEGER DEFAULT 0)")


def clean(value):
    return " ".join(str(value or "").replace("\n", " ").split())[:300]


def parse_offer(text):
    """'roofers in Slovakia' / 'dentists, Bratislava' -> (offer, place)."""
    raw = clean(text).lower()
    if not raw:
        raise ValueError("Type what to look for - a trade or a business type.")
    place = ""
    match = re.search(r"\b(?:in|near|around|at)\s+(.+)$", raw)
    if match:
        place = match.group(1).strip()
        raw = raw[:match.start()].strip()
    elif "," in raw:
        head, tail = raw.split(",", 1)
        if head.strip() and tail.strip():
            raw, place = head.strip(), tail.strip()
    raw = re.sub(r"[,\s]+$", "", raw)
    tags = []
    for keyword, filters in CATEGORIES.items():
        if keyword in raw:
            for value in filters:
                if value not in tags:
                    tags.append(value)
    label = raw[:60]
    if not tags:
        # Anything OSM might name: a roofer that is not tagged craft=roofer still has "roofer"
        # in its name or craft tag, which is how a person would search for it.
        term = re.escape(raw[:40])
        tags = ['name~"%s",i' % term, 'craft~"%s",i' % term, 'shop~"%s",i' % term]
    return {"label": label, "tags": tags}, place


def geocode(place, timeout=30):
    """A bounding box for a free-text place, through Nominatim (one request per search)."""
    place = clean(place)
    if not place:
        raise ValueError("Name a country, region or city - Overpass needs an area.")
    query = urllib.parse.urlencode({"q": place, "format": "json", "limit": 1})
    request = urllib.request.Request(NOMINATIM + "?" + query, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8", "replace") or "[]")
    except (urllib.error.URLError, OSError) as exc:
        raise ValueError("Could not look up %r: %s" % (place, exc))
    if not data:
        raise ValueError("OpenStreetMap does not know a place called %r." % place)
    south, north, west, east = (float(value) for value in data[0]["boundingbox"])
    return {"south": south, "north": north, "west": west, "east": east,
            "display": data[0].get("display_name", place)[:120]}


def _cache_path(cache_key):
    folder = os.path.join(config.DATA_DIR, "osm_cache")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, cache_key + ".json")


def _overpass_query(tags, box, limit):
    south, west = box["south"], box["west"]
    north, east = box["north"], box["east"]
    bbox = "(%s,%s,%s,%s)" % (south, west, north, east)
    parts = []
    for tag in tags:
        if "~" in tag:                                  # name~"roofer",i
            key, _, rest = tag.partition("~")
            parts.append('node["%s"%s]%s;' % (key.strip(), rest, bbox))
            parts.append('way["%s"%s]%s;' % (key.strip(), rest, bbox))
        else:
            key, _, value = tag.partition("=")
            parts.append('node["%s"="%s"]%s;' % (key.strip(), value.strip(), bbox))
            parts.append('way["%s"="%s"]%s;' % (key.strip(), value.strip(), bbox))
    body = "[out:json][timeout:180];(" + "".join(parts) + ");" \
           "out center tags %d;" % max(1, min(int(limit or 200), 1000))
    return body


def fetch(tags, box, limit=200, timeout=200, refresh=False):
    """Overpass call, cached per tag set and bounding box, so a repeat costs nothing."""
    ensure_table()
    cache_key = "%s-%s" % (_hash(json.dumps([tags, box], sort_keys=True)), limit)
    path = _cache_path(cache_key)
    if not refresh and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                cached = json.load(handle)
            return {"ok": True, "elements": cached.get("elements") or [],
                    "cached": True, "fetched_ts": cached.get("fetched_ts", 0)}
        except (OSError, ValueError):
            pass
    body = _overpass_query(tags, box, limit)
    last_error = ""
    for attempt in range(2):
        for endpoint in OVERPASS:
            request = urllib.request.Request(endpoint, data=body.encode("utf-8"), headers={
                "User-Agent": UA, "Content-Type": "text/plain; charset=utf-8"})
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    data = json.loads(response.read().decode("utf-8", "replace") or "{}")
            except (urllib.error.URLError, OSError, ValueError) as exc:
                last_error = str(exc)[:160]
                continue
            elements = data.get("elements") or []
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"elements": elements, "fetched_ts": db.now()}, handle)
            return {"ok": True, "elements": elements, "cached": False, "fetched_ts": db.now()}
        if attempt == 0:
            time.sleep(3)                     # the public instances throttle bursts
    return {"ok": False, "error": "The Overpass API did not answer (%s). It is a free, shared "
                                  "service - try again in a minute; the answer is cached once it "
                                  "succeeds." % (last_error or "no response")}


def _hash(text):
    import hashlib
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def _contact(tags, keys):
    for key in keys:
        value = clean(tags.get(key))
        if value:
            return urllib.parse.unquote(value) if key.endswith(":url") else value
    return ""


def save(elements, category, area):
    """Upsert the OSM records, then hand back how many were new."""
    ensure_table()
    now = db.now()
    added = 0
    for element in elements or []:
        tags = element.get("tags") or {}
        name = clean(tags.get("name"))
        if not name:
            continue
        osm_id = "%s/%s" % (element.get("type", "node"), element.get("id"))
        email = _contact(tags, ("email", "contact:email"))
        website = _contact(tags, ("website", "contact:website", "url", "contact:url"))
        phone = _contact(tags, ("phone", "contact:phone", "contact:mobile"))
        address = " ".join(filter(None, [
            clean(tags.get("addr:street")), clean(tags.get("addr:housenumber")),
            clean(tags.get("addr:postcode")), clean(tags.get("addr:city"))]))
        row = db.one("SELECT id FROM osm_business WHERE id=?", (osm_id,))
        payload = {"name": name, "category": category, "email": email.lower(), "website": website,
                   "phone": phone, "city": clean(tags.get("addr:city")), "area": area,
                   "address": address, "tags": json.dumps(tags, ensure_ascii=False)[:4000],
                   "fetched_ts": now}
        if row:
            db.update("osm_business", row["id"], payload)
        else:
            db.insert("osm_business", {"id": osm_id, **payload})
            added += 1
    return added


def lists(category="", area=""):
    """The three lists the lead flow needs, as counts and a sample."""
    ensure_table()
    where, args = [], []
    if category:
        where.append("category=?")
        args.append(category)
    if area:
        where.append("area=?")
        args.append(area)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    total = db.scalar("SELECT COUNT(*) FROM osm_business" + clause, tuple(args))
    with_email = db.scalar("SELECT COUNT(*) FROM osm_business" + clause +
                           (" AND" if where else " WHERE") + " email<>''", tuple(args))
    website_only = db.scalar("SELECT COUNT(*) FROM osm_business" + clause +
                             (" AND" if where else " WHERE") + " email='' AND website<>''",
                             tuple(args))
    phone_only = db.scalar("SELECT COUNT(*) FROM osm_business" + clause +
                           (" AND" if where else " WHERE") +
                           " email='' AND website='' AND phone<>''", tuple(args))
    sample = db.rows("SELECT name, email, website, phone, city, category FROM osm_business" +
                     clause + " ORDER BY (email<>'') DESC, name LIMIT 8", tuple(args))
    return {"ok": True, "total": total, "with_email": with_email, "website_only": website_only,
            "phone_only": phone_only, "sample": sample}


def search(text, area="", limit=200, refresh=False, save_rows=True):
    """One search: offer + place -> OSM rows in the table. Returns the counts."""
    offer, place_from_text = parse_offer(text)
    place = clean(area) or place_from_text
    box = geocode(place)
    result = fetch(offer["tags"], box, limit=limit, refresh=refresh)
    if not result.get("ok"):
        return result
    added = save(result["elements"], " ".join(offer["tags"])[:80], place) if save_rows else 0
    counts = lists(category=" ".join(offer["tags"])[:80], area=place)
    return {"ok": True, "added": added, "cached": result.get("cached"),
            "label": offer["label"], "place": box["display"], "tags": offer["tags"],
            "category": " ".join(offer["tags"])[:80], "area": place,
            **{key: counts[key] for key in ("total", "with_email", "website_only", "phone_only")},
            "sample": counts["sample"]}


def import_ready(category="", area="", limit=2000, include_website=True):
    """Turn OSM rows into leads: the ones with an email, and optionally the website-only ones."""
    ensure_table()
    where, args = ["name<>''"], []
    if category:
        where.append("category=?")
        args.append(category)
    if area:
        where.append("area=?")
        args.append(area)
    wanted = "email<>''" + (" OR website<>''" if include_website else "")
    rows = db.rows("SELECT * FROM osm_business WHERE " + " AND ".join(where) +
                   " AND (" + wanted + ") ORDER BY (email<>'') DESC, name LIMIT ?",
                   tuple(args) + (int(limit),))
    if not rows:
        return {"ok": True, "imported": 0, "examined": 0,
                "note": "No OSM rows with a contact yet - run a search first."}
    payload = []
    for row in rows:
        email = (row["email"] or "").strip().lower()
        payload.append({
            "email": email, "company": row["name"], "domain": _domain_of(row["website"]),
            "website": row["website"], "phone": row["phone"], "city": row["city"],
            "country": "", "source": "osm", "notes": "OpenStreetMap: %s%s" % (
                row["category"], (" · " + row["address"]) if row["address"] else "")})
    summary = importer.import_rows("openstreetmap.csv", list(payload[0].keys()), payload,
                                   source="osm", only_email=False)
    return {"ok": bool(summary.get("ok")), "examined": len(payload),
            "imported": summary.get("imported", 0), "duplicates": summary.get("duplicates", 0),
            "no_email": summary.get("no_email", 0)}


def _domain_of(url):
    text = clean(url)
    if not text:
        return ""
    if "://" not in text:
        text = "https://" + text
    host = urllib.parse.urlsplit(text).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def lookup_domain(domain, name="", area="", timeout=25):
    """One business in OSM for an existing domain - enrichment, not a lead search.

    Nominatim answers with the element's tags (address, phone, email, website, opening
    hours). A match is preferred when the tagged website is the domain itself, then when
    the name matches exactly, so a common name does not attach the wrong shop.
    """
    domain = _domain_of(domain)
    if not domain or "." not in domain:
        return {}
    query = clean(name) or domain.split(".")[0].replace("-", " ").replace("_", " ")
    if area:
        query = "%s, %s" % (query, clean(area))
    params = {"q": query, "format": "jsonv2", "limit": 5, "addressdetails": 1, "extratags": 1}
    request = urllib.request.Request(NOMINATIM + "?" + urllib.parse.urlencode(params),
                                     headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8", "replace") or "[]")
    except (urllib.error.URLError, OSError):
        return {}
    if not isinstance(data, list) or not data:
        return {}
    wanted = query.split(",")[0].strip().lower()
    best, best_score = None, 0
    for item in data:
        tags = item.get("extratags") or {}
        website = _contact(tags, ("website", "contact:website", "url", "contact:url"))
        host = _domain_of(website)
        score = 0
        if host and (host == domain or host.endswith("." + domain) or domain.endswith("." + host)):
            score = 3
        elif clean(item.get("name")).lower() == wanted and wanted:
            score = 2
        elif clean(item.get("name")):
            score = 1
        if score > best_score:
            best, best_score = item, score
    if not best or best_score < 2:
        return {}
    tags = best.get("extratags") or {}
    address = best.get("address") or {}
    street = " ".join(filter(None, [clean(address.get("road")),
                                    clean(address.get("house_number"))]))
    return {"osm_id": "%s/%s" % (best.get("osm_type", ""), best.get("osm_id", "")),
            "name": clean(best.get("name")),
            "category": "%s/%s" % (clean(best.get("category")), clean(best.get("type"))),
            "email": _contact(tags, ("email", "contact:email")).lower(),
            "phone": _contact(tags, ("phone", "contact:phone", "contact:mobile")),
            "website": _contact(tags, ("website", "contact:website", "url", "contact:url")),
            "street": street,
            "city": clean(address.get("city") or address.get("town") or address.get("village")),
            "postcode": clean(address.get("postcode")),
            "country": clean(address.get("country")),
            "opening_hours": clean(tags.get("opening_hours")),
            "lat": str(best.get("lat") or ""), "lon": str(best.get("lon") or "")}
