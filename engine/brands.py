"""Brand watch: is anyone copying, cloning or scamming this business?

Given a monitored domain, this builds the names a fake shop or a phishing site
would actually use - the brand on another TLD, the brand plus "shop"/"official",
the brand with a letter dropped or two swapped - asks DNS which of them exist,
and scores what it finds. Live domains that accept mail are the ones worth a
phone call.

It then cross-checks the two corpuses the app already keeps: the daily
newly-registered-domain feed and the phishing list.

Nothing here touches the candidate website itself: a resolved name and an MX
record is enough to raise a case, and it keeps the scan fast and quiet.
"""
import re
import socket
import time

from . import db, net, verify

# Suffixes a lookalike actually uses. Keep this list short: every entry is DNS work.
TLD_SWAPS = ("com", "net", "org", "shop", "store", "site", "online", "co", "io", "eu",
             "de", "at", "cz", "sk", "app", "xyz", "biz")

# Words fake shops and clone sites put around a brand.
KEYWORDS = ("shop", "store", "official", "sale", "outlet", "buy", "order", "kaufen",
            "geschaft", "versand", "bestellen", "deutsch", "austria", "gmbh", "service")

# Characters that look like other characters in a URL bar.
HOMOGLYPHS = {"o": "0", "i": "1", "l": "1", "a": "4", "e": "3", "s": "5", "b": "6"}

SECOND_LEVEL = {"co", "com", "net", "org", "gov", "ac", "edu"}

KIND_SCORE = {"tld": 1, "hyphen": 1, "typo": 2, "keyword": 3, "keyword_prefix": 3,
              "homoglyph": 4, "nrd": 5, "listed": 6}

STRONG_WORDS = {"official", "shop", "store", "sale", "outlet", "kaufen", "geschaft",
                "versand", "bestellen"}


# ------------------------------------------------------------------ helpers
def split_domain(domain):
    """('acme-shop', 'co.uk') style split, tolerant of a plain 'acme.com'."""
    labels = [p for p in (net.domain_of(domain) or "").split(".") if p]
    if len(labels) < 2:
        return (labels[0] if labels else ""), ""
    label = labels[0]
    suffix = ".".join(labels[1:])
    if len(labels) >= 3 and labels[-2] in SECOND_LEVEL and len(labels[-1]) == 2:
        suffix = ".".join(labels[-2:])
    return label, suffix


def token(label):
    return re.sub(r"[^a-z0-9]", "", (label or "").lower())


def base_tld(domain):
    _label, suffix = split_domain(domain)
    return suffix.rsplit(".", 1)[-1] if suffix else ""


def candidates(domain, limit=80):
    """Deterministic list of plausible lookalike domains, best guesses first."""
    label, suffix = split_domain(domain)
    if not label or not suffix:
        return []
    stem = token(label)
    if not stem:
        return []
    tld = base_tld(domain)
    out = []
    seen = {net.domain_of(domain)}

    def add(name, kind):
        full = name.lower().strip(".-")
        if not full or full in seen or len(full) > 253:
            return
        if not re.match(r"^[a-z0-9][a-z0-9.\-]*\.[a-z]{2,}$", full):
            return
        seen.add(full)
        out.append({"domain": full, "kind": kind})

    for word in KEYWORDS:
        add(stem + word + "." + tld, "keyword")
        add(word + stem + "." + tld, "keyword_prefix")

    for swap in TLD_SWAPS:
        if swap != tld:
            add(stem + "." + swap, "tld")

    if "-" not in label:
        add(stem[:max(2, len(stem) // 2)] + "-" + stem[max(2, len(stem) // 2):] + "." + tld,
            "hyphen")

    for i in range(1, len(stem) - 1):
        add(stem[:i] + stem[i + 1:] + "." + tld, "typo")            # dropped letter
        if stem[i] != stem[i + 1]:
            add(stem[:i] + stem[i + 1] + stem[i] + stem[i + 2:] + "." + tld, "typo")  # swapped

    for i, char in enumerate(stem[:8]):
        if char in HOMOGLYPHS:
            add(stem[:i] + HOMOGLYPHS[char] + stem[i + 1:] + "." + tld, "homoglyph")

    return out[:limit]


def resolves(domain):
    try:
        socket.getaddrinfo(domain, None, proto=socket.IPPROTO_TCP)
        return True
    except (socket.gaierror, UnicodeError, OSError):
        return False


def has_mail(domain):
    try:
        hosts, _source = verify.mx_lookup(domain)
        return bool(hosts)
    except Exception:                       # noqa: BLE001 - DNS must never break a scan
        return False


# ------------------------------------------------------------------- brands
def add_brand(name, domain, aliases="", notes=""):
    domain = net.domain_of(domain) or (domain or "").strip().lower()
    if not domain or "." not in domain:
        return None
    name = (name or domain.split(".")[0]).strip()[:120]
    existing = db.one("SELECT * FROM brands WHERE domain=?", (domain,))
    if existing:
        db.execute("UPDATE brands SET name=?, aliases=?, notes=? WHERE id=?",
                   (name, aliases or "", notes or "", existing["id"]))
        return existing["id"]
    return db.insert("brands", {"name": name, "domain": domain, "aliases": aliases or "",
                                "notes": notes or "", "active": 1, "created_ts": db.now()})


def import_text(text_value):
    """One brand per line: 'Name, domain' or just a domain or URL."""
    added = 0
    for raw in (text_value or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split(",") if p.strip()]
        if len(parts) >= 2 and "." in parts[1]:
            name, domain = parts[0], parts[1]
        else:
            domain = net.domain_of(parts[0]) if "." in parts[0] else ""
            name = parts[0] if domain else ""
        if not domain:
            continue
        if db.one("SELECT id FROM brands WHERE domain=?", (domain,)):
            continue                      # already watched: importing again changes nothing
        if add_brand(name, domain):
            added += 1
    return added


def list_brands():
    rows = db.rows("SELECT * FROM brands ORDER BY name COLLATE NOCASE")
    for row in rows:
        row["findings"] = db.scalar(
            "SELECT COUNT(*) FROM signals WHERE company=? AND kind LIKE 'lookalike%'", (row["name"],))
        row["live"] = db.scalar(
            "SELECT COUNT(*) FROM signals WHERE company=? AND kind='lookalike_live'", (row["name"],))
        row["nrd"] = db.scalar(
            "SELECT COUNT(*) FROM signals WHERE company=? AND kind='lookalike_nrd'", (row["name"],))
        row["listed"] = db.scalar(
            "SELECT COUNT(*) FROM signals WHERE company=? AND kind='lookalike_listed'", (row["name"],))
        row["open"] = db.scalar(
            "SELECT COUNT(*) FROM signals WHERE company=? AND kind LIKE 'lookalike%' AND seen=0",
            (row["name"],))
    return rows


# --------------------------------------------------------------------- scan
def scan_brand(brand, candidates_per_brand=60, min_score=4, pause=0.15, mx_cap=40,
               include_nrd=True, include_phishing=True, ctx=None, dry_run=False):
    """Scan one brand. Returns a small report; writes signals unless dry_run."""
    domain = brand["domain"]
    name = brand["name"]
    label, _suffix = split_domain(domain)
    stem = token(label)
    original_tld = base_tld(domain)
    findings = []
    checked = live = 0

    for cand in candidates(domain, limit=candidates_per_brand):
        checked += 1
        time.sleep(max(0.0, pause))
        if not resolves(cand["domain"]):
            continue
        live += 1
        mail = has_mail(cand["domain"]) if live <= mx_cap else False
        score = KIND_SCORE.get(cand["kind"], 1)
        if cand["kind"] in ("keyword", "keyword_prefix"):
            word = next((w for w in KEYWORDS if w in cand["domain"]), "")
            if word in STRONG_WORDS:
                score += 2
        if mail:
            score += 2
        if base_tld(cand["domain"]) != original_tld:
            score += 1
        if score < min_score:
            continue
        finding = {"kind": "lookalike_live", "domain": cand["domain"], "company": name,
                   "how": cand["kind"], "score": score, "has_mail": mail,
                   "reason": "%s variant of %s%s" % (cand["kind"], domain,
                                                     ", accepts mail" if mail else "")}
        findings.append(finding)
        if not dry_run and ctx is not None:
            ctx.add_signal("lookalike_live", domain=cand["domain"], company=name,
                           title=cand["kind"], source="lookalike",
                           source_url="https://" + cand["domain"],
                           payload={"score": score, "has_mail": mail, "brand": domain,
                                    "reason": finding["reason"]})

    if include_nrd and stem:
        rows = db.rows("SELECT domain, first_seen_ts FROM domains WHERE domain LIKE ? "
                       "AND domain NOT LIKE ? LIMIT 200", ("%" + stem + "%", "%" + domain))
        for row in rows:
            if row["domain"] == domain or len(stem) < 4:
                continue
            findings.append({"kind": "lookalike_nrd", "domain": row["domain"], "company": name,
                             "how": "new registration", "score": KIND_SCORE["nrd"],
                             "has_mail": False,
                             "reason": "registered recently and contains the brand name"})
            if not dry_run and ctx is not None:
                ctx.add_signal("lookalike_nrd", domain=row["domain"], company=name,
                               title="new registration", source="nrd",
                               source_url="https://" + row["domain"],
                               payload={"brand": domain, "seen_ts": row["first_seen_ts"]})

    if include_phishing and stem:
        rows = db.rows("SELECT domain, source_url, ts FROM signals WHERE kind='phishing' "
                       "AND domain LIKE ? LIMIT 100", ("%" + stem + "%",))
        for row in rows:
            findings.append({"kind": "lookalike_listed", "domain": row["domain"], "company": name,
                             "how": "on a phishing list", "score": KIND_SCORE["listed"],
                             "has_mail": False, "reason": "already reported as phishing"})
            if not dry_run and ctx is not None:
                ctx.add_signal("lookalike_listed", domain=row["domain"], company=name,
                               title="listed as phishing", source="phishing",
                               source_url=row["source_url"] or "https://" + row["domain"],
                               payload={"brand": domain, "listed_ts": row["ts"]})

    if not dry_run:
        db.execute("UPDATE brands SET last_scan_ts=?, last_findings=?, checked=checked+1 WHERE id=?",
                   (db.now(), len(findings), brand["id"]))
    return {"brand": name, "domain": domain, "checked": checked, "live": live,
            "findings": findings, "count": len(findings)}
