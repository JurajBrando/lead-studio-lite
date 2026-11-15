"""Robots-aware contact crawler: domain -> contact pages -> emails and phones.

This is the step that turns a signal into a lead. It reads the site as it is
today (the live counterpart to the Common Crawl enrichment), follows only the
pages that plausibly carry contact details, and ranks what it finds.

Deliberate limits, because these are other people's servers:
  * robots.txt is honoured (and cached per run),
  * a small number of pages per domain,
  * one request at a time per domain, with a pause between them,
  * no forms are submitted and nothing is sent to the site.
"""
import html as html_mod
import re
import time
import urllib.parse
import urllib.robotparser

from . import net

EMAIL_RX = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
PHONE_RX = re.compile(r"(?:\+\d{1,3}[\s\-.]?)?(?:\(?\d{2,5}\)?[\s\-.]?){2,5}\d{2,4}")
CFEMAIL_RX = re.compile(r'data-cfemail="([0-9a-fA-F]+)"')
TITLE_RX = re.compile(r"(?is)<title[^>]*>(.*?)</title>")
DESC_RX = re.compile(r'(?is)<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']*)')
ANCHOR_RX = re.compile(r"(?is)<a\s[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>")
TAG_RX = re.compile(r"<[^>]+>")
WS_RX = re.compile(r"\s+")

CONTACT_WORDS = re.compile(
    r"(contact|kontakt|kontakty|about|o-nas|o nás|impressum|imprint|team|our-team|people|staff|"
    r"support|help|customer|careers|jobs|locations?|find-us|visit-us|book|appointment|"
    r"legal|privacy|terms|company|firma|who-we-are|leadership)", re.I)

BAD_EMAIL_HINTS = ("noreply", "no-reply", "donotreply", "postmaster", "abuse@", "webmaster@",
                   "privacy@", "dmarc", "example.com", "sentry", "wixpress", "@2x", ".png", ".jpg")

ROLE_PREFIXES = ("info", "contact", "kontakt", "office", "sales", "hello", "enquiries", "inquiries",
                 "support", "admin", "team", "mail", "firma", "podatelna", "obchod", "predaj")

SKIP_EXT = (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".zip", ".rar", ".doc",
            ".docx", ".xls", ".xlsx", ".mp4", ".mp3", ".css", ".js", ".ico", ".woff", ".woff2")


# ------------------------------------------------------------------ parsing
def decode_cfemail(value):
    """Decode Cloudflare's data-cfemail obfuscation."""
    try:
        raw = bytes.fromhex(value)
    except ValueError:
        return ""
    if len(raw) < 2:
        return ""
    key = raw[0]
    return "".join(chr(b ^ key) for b in raw[1:])


def clean_emails(text_value):
    """All plausible addresses in a blob of text, de-obfuscated and de-duplicated."""
    if not text_value:
        return []
    blob = html_mod.unescape(text_value)
    blob = re.sub(r"\s*[\[\(\{]\s*(at|@)\s*[\]\)\}]\s*", "@", blob, flags=re.I)
    blob = re.sub(r"\s*[\[\(\{]\s*(dot|\.)\s*[\]\)\}]\s*", ".", blob, flags=re.I)
    blob = blob.replace("mailto:", " ")
    out = []
    for match in EMAIL_RX.findall(blob):
        addr = match.strip(".,;:()<>\"'").lower()
        if len(addr) > 254 or addr.count("@") != 1:
            continue
        if any(bad in addr for bad in BAD_EMAIL_HINTS):
            continue
        if addr.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")):
            continue
        if addr not in out:
            out.append(addr)
    return out


def decode_emails_in_html(html_text):
    found = list(clean_emails(html_text))
    for value in CFEMAIL_RX.findall(html_text or ""):
        addr = decode_cfemail(value).strip().lower()
        if addr and addr not in found and EMAIL_RX.fullmatch(addr):
            found.append(addr)
    return found


def page_title(html_text):
    match = TITLE_RX.search(html_text or "")
    if not match:
        return ""
    return WS_RX.sub(" ", TAG_RX.sub("", html_mod.unescape(match.group(1)))).strip()[:200]


def page_description(html_text):
    match = DESC_RX.search(html_text or "")
    if not match:
        return ""
    return WS_RX.sub(" ", html_mod.unescape(match.group(1))).strip()[:300]


def contact_links(base_url, html_text, limit=5):
    """Absolute same-site URLs that look like contact / about / team pages."""
    host = urllib.parse.urlsplit(base_url).netloc.lower().lstrip("www.")
    scored = []
    seen = set()
    for href, anchor in ANCHOR_RX.findall(html_text or ""):
        href = (href or "").strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        url = urllib.parse.urljoin(base_url, href)
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in ("http", "https"):
            continue
        target_host = parts.netloc.lower().lstrip("www.")
        if target_host != host and not target_host.endswith("." + host):
            continue
        path = parts.path.lower()
        if path.endswith(SKIP_EXT):
            continue
        clean = url.split("#")[0].rstrip("/")
        if clean in seen or clean == base_url.rstrip("/"):
            continue
        label = WS_RX.sub(" ", TAG_RX.sub(" ", anchor or "")).strip()[:80]
        score = 0
        if re.search(r"(contact|kontakt)", path, re.I):
            score += 5
        if re.search(r"(about|o-nas|impressum|team|people|staff)", path, re.I):
            score += 3
        if CONTACT_WORDS.search(label):
            score += 2
        if parts.query:
            score -= 2
        if score <= 0:
            continue
        seen.add(clean)
        scored.append((score, clean))
    scored.sort(key=lambda item: (-item[0], len(item[1])))
    return [url for _, url in scored[:limit]]


def clean_phones(text_value):
    out = []
    for match in PHONE_RX.findall(text_value or ""):
        digits = re.sub(r"\D", "", match)
        if len(digits) < 7 or len(digits) > 16:
            continue
        value = match.strip(" .-")
        if value not in out:
            out.append(value)
    return out[:10]


# ------------------------------------------------------------------- robots
class Robots:
    def __init__(self):
        self._cache = {}

    def allowed(self, url, agent="LeadStudioLite"):
        parts = urllib.parse.urlsplit(url)
        origin = "%s://%s" % (parts.scheme, parts.netloc)
        parser = self._cache.get(origin)
        if parser is None:
            parser = urllib.robotparser.RobotFileParser()
            try:
                body = net.text(origin + "/robots.txt", timeout=10, retries=0)
                parser.parse((body or "").splitlines())
            except (net.NetError, OSError, ValueError):
                parser.parse([])          # no robots.txt: everything allowed
            self._cache[origin] = parser
        try:
            return parser.can_fetch(agent, url)
        except (ValueError, UnicodeError):
            return True


_ROBOTS = Robots()


# --------------------------------------------------------------------- crawl
def fetch_page(url, timeout=15):
    body = net.fetch(url, timeout=timeout, retries=0)
    return body.decode("utf-8", "replace")


def rank_emails(emails, domain):
    """Company addresses first, role addresses next, personal ones last."""
    def score(addr):
        local, _, host = addr.partition("@")
        value = 0
        if host == domain or host.endswith("." + domain):
            value -= 10
        else:
            value += 10                      # another company's address on their page
        if local.split("+")[0] in ROLE_PREFIXES:
            value -= 3
        return value
    return sorted(set(emails), key=lambda a: (score(a), len(a)))


def crawl_domain(domain, max_pages=4, respect_robots=True, pause=0.6):
    """Read a site and return what it says about contacting it."""
    domain = net.domain_of(domain) or (domain or "").strip().lower()
    result = {"domain": domain, "ok": False, "emails": [], "phones": [], "pages": [],
              "title": "", "description": "", "blocked_by_robots": False, "error": ""}
    if not domain or "." not in domain:
        result["error"] = "Not a domain."
        return result

    start = "https://" + domain
    if respect_robots and not _ROBOTS.allowed(start):
        result["blocked_by_robots"] = True
        result["error"] = "robots.txt disallows crawling."
        return result

    try:
        home = fetch_page(start)
    except net.NetError:
        try:
            start = "http://" + domain
            home = fetch_page(start)
        except net.NetError as exc:
            result["error"] = str(exc)
            return result

    result["title"] = page_title(home)
    result["description"] = page_description(home)
    emails = decode_emails_in_html(home)
    phones = clean_phones(TAG_RX.sub(" ", home))
    pages = [(start, home)]

    for url in contact_links(start, home, limit=max(0, max_pages - 1)):
        if respect_robots and not _ROBOTS.allowed(url):
            continue
        time.sleep(max(0.0, pause))
        try:
            body = fetch_page(url)
        except net.NetError:
            continue
        pages.append((url, body))
        emails.extend(decode_emails_in_html(body))
        phones.extend(clean_phones(TAG_RX.sub(" ", body)))
        if len(pages) >= max_pages:
            break

    result["emails"] = rank_emails(emails, domain)[:8]
    result["phones"] = clean_phones(" ".join(phones))[:5]
    result["pages"] = [url for url, _ in pages]
    result["ok"] = bool(result["emails"] or result["phones"] or result["title"])
    return result


def guess_patterns(domain, first="", last=""):
    """The five addresses a small company most often uses, for manual checking."""
    first = re.sub(r"[^a-z]", "", (first or "").lower())
    last = re.sub(r"[^a-z]", "", (last or "").lower())
    out = ["info@" + domain, "contact@" + domain]
    if first:
        out.append(first + "@" + domain)
    if first and last:
        out.extend([first + "." + last + "@" + domain, first[0] + last + "@" + domain])
    return out
