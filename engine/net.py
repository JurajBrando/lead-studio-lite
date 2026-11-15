"""Tiny HTTP layer for the automation jobs.

Standard library only: one place that sets a user agent, follows redirects,
decodes gzip, retries transient failures and can be switched offline by tests.
"""
import gzip
import io
import json
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

UA_CONTACT = ""          # filled from settings.contact_email at start-up
_offline = False


class NetError(Exception):
    def __init__(self, message, status=0, url=""):
        super().__init__(message)
        self.status = status
        self.url = url


def set_contact(email):
    global UA_CONTACT
    UA_CONTACT = (email or "").strip()


def set_offline(value=True):
    global _offline
    _offline = bool(value)


def offline():
    return _offline


def user_agent():
    base = "LeadStudioLite/1.1 (+local lead enrichment)"
    return base + (" contact:%s" % UA_CONTACT if UA_CONTACT else "")


def fetch(url, timeout=45, headers=None, retries=2, method=None, data=None):
    """Return the response body as bytes. Raises NetError on failure."""
    if _offline:
        raise NetError("Network access is disabled (offline mode).", url=url)
    h = {"User-Agent": user_agent(), "Accept": "*/*", "Accept-Encoding": "gzip"}
    h.update(headers or {})
    last = ""
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=h, method=method, data=data)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read()
                enc = (resp.headers.get("Content-Encoding") or "").lower()
            if enc == "gzip" or body[:2] == b"\x1f\x8b":
                try:
                    body = gzip.decompress(body)
                except OSError:
                    pass
            return body
        except urllib.error.HTTPError as e:
            last = "HTTP %d from %s" % (e.code, urllib.parse.urlsplit(url).netloc)
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise NetError(last, status=e.code, url=url)
        except (urllib.error.URLError, socket.timeout, OSError) as e:
            last = "%s: %s" % (urllib.parse.urlsplit(url).netloc, e)
            if attempt < retries:
                time.sleep(1.0 * (attempt + 1))
                continue
            raise NetError(last, url=url)
    raise NetError(last, url=url)


def text(url, **kw):
    raw = fetch(url, **kw)
    for enc in ("utf-8", "cp1250", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def json_body(url, **kw):
    return json.loads(text(url, **kw) or "null")


# --------------------------------------------------------------- parsing
def csv_rows(value):
    """Parse CSV text into (headers, list_of_dicts). Handles large fields."""
    import csv as _csv
    _csv.field_size_limit(10 ** 9)
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    reader = _csv.reader(io.StringIO(value, newline=""))
    try:
        headers = [h.strip() for h in next(reader)]
    except StopIteration:
        return [], []
    rows = []
    for values in reader:
        if not any((v or "").strip() for v in values):
            continue
        rows.append({headers[i]: (values[i] or "").strip()
                     for i in range(min(len(headers), len(values)))})
    return headers, rows


TAG_RX = re.compile(r"<[^>]+>")
WS_RX = re.compile(r"\s+")


def strip_tags(html_text):
    text_value = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html_text or "")
    text_value = re.sub(r"(?i)<br\s*/?>|</p>|</tr>|</div>", "\n", text_value)
    text_value = TAG_RX.sub(" ", text_value)
    text_value = (text_value.replace("&nbsp;", " ").replace("&amp;", "&")
                  .replace("&lt;", "<").replace("&gt;", ">").replace("&#39;", "'")
                  .replace("&quot;", '"'))
    return WS_RX.sub(" ", text_value).strip()


def html_tables(html_text):
    """Very small HTML table reader -> list of list-of-cell-text."""
    tables = []
    for table in re.findall(r"(?is)<table[^>]*>(.*?)</table>", html_text or ""):
        rows = []
        for tr in re.findall(r"(?is)<tr[^>]*>(.*?)</tr>", table):
            cells = re.findall(r"(?is)<t[dh][^>]*>(.*?)</t[dh]>", tr)
            if cells:
                rows.append([strip_tags(c) for c in cells])
        if rows:
            tables.append(rows)
    return tables


def atom_entries(value):
    """Generic RSS/Atom reader -> [{title, link, published, summary, id}]."""
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(value.strip() if isinstance(value, str) else value)
    except ET.ParseError:
        return []
    ns = {"a": "http://www.w3.org/2005/Atom"}
    out = []
    for entry in root.iter():
        tag = entry.tag.split("}")[-1]
        if tag not in ("entry", "item"):
            continue
        item = {}
        for child in entry:
            name = child.tag.split("}")[-1]
            if name == "link":
                href = child.get("href") or (child.text or "")
                if href and not item.get("link"):
                    item["link"] = href.strip()
            elif name in ("title", "summary", "description", "published", "updated", "pubDate", "id", "guid"):
                key = {"description": "summary", "pubDate": "published", "guid": "id"}.get(name, name)
                text_value = (child.text or "").strip()
                if text_value and not item.get(key):
                    item[key] = strip_tags(text_value) if key == "summary" else text_value
        if item.get("title") or item.get("link"):
            out.append(item)
    return out


def quote(value):
    return urllib.parse.quote(str(value), safe="")


def domain_of(value):
    """Pull a bare registrable-ish domain out of a URL or an email."""
    v = (value or "").strip().lower()
    if not v:
        return ""
    if "@" in v and "/" not in v:
        v = v.rsplit("@", 1)[-1]
    if "://" in v:
        v = urllib.parse.urlsplit(v).netloc
    v = v.split("/")[0].split(":")[0]
    v = re.sub(r"^www\.", "", v)
    return v if "." in v else ""
