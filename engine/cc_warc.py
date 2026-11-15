"""Reading pages out of Common Crawl's WARC files, by byte range.

An index row says where a page lives: `warc_filename`, `warc_record_offset` and
`warc_record_length`. One HTTP Range request to data.commoncrawl.org returns exactly that
record - a few tens of KB, not a multi-gigabyte WARC - and the compressed member holds the
HTTP response. That is how a domain gets enriched from the archive even when the live site is
gone, parked or blocking bots.

The record layout is: optional gzip member, `WARC/1.0` headers, blank line, HTTP status line
and headers, blank line, payload.
"""
import gzip
import re
import urllib.error
import urllib.request

from . import config, crawl

BASE = "https://data.commoncrawl.org/"
TAG_RX = re.compile(r"<[^>]+>")
CONTACT_HINTS = ("contact", "kontakt", "impressum", "about", "team", "support", "company",
                 "legal", "mentions", "nosotros", "contatti", "contato")


def fetch_record(warc_filename, offset, length, timeout=60):
    """The raw bytes of one record, straight out of the archive."""
    url = BASE + str(warc_filename or "").lstrip("/")
    start = int(offset)
    end = start + max(1, int(length)) - 1
    request = urllib.request.Request(url, headers={
        "Range": "bytes=%d-%d" % (start, end),
        "User-Agent": "LeadStudioLite/%s (local lead enrichment)" % config.VERSION})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _gunzip(raw):
    if raw[:2] != b"\x1f\x8b":
        return raw
    try:
        return gzip.decompress(raw)
    except (OSError, EOFError):
        # A range that starts mid-stream still yields the record after the first member.
        buffer = gzip.GzipFile(fileobj=__import__("io").BytesIO(raw))
        try:
            return buffer.read()
        except (OSError, EOFError):
            return b""


def parse_record(raw):
    """{url, status, content_type, charset, html} from a WARC record's bytes."""
    data = _gunzip(raw or b"")
    if b"WARC/" not in data[:400]:
        return {"ok": False, "error": "not a WARC record"}
    head, _, rest = data.partition(b"\r\n\r\n")
    warc = {}
    for line in head.decode("utf-8", "replace").splitlines()[1:]:
        if ":" in line:
            key, _, value = line.partition(":")
            warc[key.strip().lower()] = value.strip()
    if not rest:
        return {"ok": False, "error": "the record has no HTTP part"}
    http_head, _, body = rest.partition(b"\r\n\r\n")
    lines = http_head.decode("utf-8", "replace").splitlines()
    status = 0
    if lines and lines[0].upper().startswith("HTTP/"):
        parts = lines[0].split()
        status = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            key, _, value = line.partition(":")
            headers[key.strip().lower()] = value.strip()
    content_type = headers.get("content-type", "")
    charset = "utf-8"
    if "charset=" in content_type.lower():
        charset = content_type.lower().split("charset=")[-1].split(";")[0].strip() or "utf-8"
    html = ""
    if "html" in content_type.lower() or not content_type:
        try:
            html = body.decode(charset, "replace")
        except LookupError:
            html = body.decode("utf-8", "replace")
    return {"ok": True, "url": warc.get("warc-target-uri", ""), "status": status,
            "content_type": content_type, "charset": charset, "html": html}


def contacts_of(html_text, domain=""):
    """Addresses and phones from one archived page, with the same cleaners the crawler uses."""
    if not html_text:
        return {"emails": [], "phones": [], "title": "", "description": ""}
    emails = crawl.clean_emails(html_text)
    for extra in crawl.decode_emails_in_html(html_text):
        if extra not in emails:
            emails.append(extra)
    emails = crawl.rank_emails(emails, domain) if domain else emails
    text = TAG_RX.sub(" ", html_text)
    phones = crawl.clean_phones(text)
    return {"emails": emails[:12], "phones": phones[:5],
            "title": crawl.page_title(html_text)[:200],
            "description": crawl.page_description(html_text)[:300]}


def page_score(url):
    """Contact and about pages first, the homepage next, the rest after."""
    lowered = str(url or "").lower()
    if any(hint in lowered for hint in CONTACT_HINTS):
        return 0
    path = lowered.split("://")[-1]
    if path.count("/") <= 1 or path.endswith("/"):
        return 1
    return 2
