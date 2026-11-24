"""Read one public page into offer fields with plain rules - no LLM is used.

It fetches the page, reads the title, the meta description, the headings and the visible
text, and runs the same heuristics radar.parse_offer() uses on a pasted paragraph.
Everything it returns is a suggestion: the offer form stays editable.
"""
import re
import unicodedata

from . import net, radar

META_RX = re.compile(r"(?is)<meta\b[^>]*>")
TITLE_RX = re.compile(r"(?is)<(title|h1|h2)\b[^>]*>(.*?)</\1>")
ATTR_RX = re.compile(r'''([\w:.-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')''')
WS_RX = re.compile(r"\s+")
SPLIT_RX = re.compile(r"\s+[|\-\u2013\u2014]\s+|:\s+")

STOP = set("""the and for with your you our that this from are was were will can all any
more most about into than then them they their there here what when where which who how
not but its have has had does did get got make made just one two new free now best top
use used using page home site click learn read see works work pick could would should
everything instantly together think bring build custom plug seconds thousands countless
start today book demo contact pricing""" .split())

# Navigation, consent and form furniture. On a small site these are often the most
# repeated words on the page, so they must never become an offer keyword.
NAV = set("""cookie cookies consent accept all reject manage settings setting preferences
privacy policy terms conditions legal imprint newsletter subscribe unsubscribe email
menu navigation home homepage search login signin signup register account cart basket
checkout shop store category categories filter sort previous next read more less load
share follow facebook instagram youtube linkedin twitter tiktok pinterest phone call
contact about us company team pricing blog news faq help support careers jobs
informacie informacia nastavenia nastavenie novinky kontakt kontaktne suhlas suhlasim
prijat povolit odmietnut upravit domov uvod vyhladat hladat kosik nakupny prihlasenie
prihlasit odhlasit registracia registrovat zabudnute heslo ochrana osobnych udajov
podmienky doprava platba reklamacie vratenie galeria referencia referencie stranka
stranky zdielat sledujte odber zlavy akcie vyhladavanie jazyk slovencina anglictina
nemecky zabudnut chyba nacitava pokracovat vyjadrujete reklamne sledovacie ucely
fungovania navstevnosti personalizaciu obsahu webova stranka cookies information sale close
width height size color colour quantity reviews shipping returns delivery wishlist compare
season biggest comfiest cheapest fastest latest newest ultimate perfect favourite favorite""".split())

SK_CZ = set("""firma firmy bola bolo boli roku roky rokov svojho vzniku zaobera zaoberame
ponuka ponukame moznost moznosti kvalitny kvalitnej kvalitne vyroba vyrobou vyrabame
montaz montazou ktore ktory ktora ktoreho ktorych preto pretoze alebo taktiez takisto
ktoru ktorym ktorymi kedy kde ako velmi kazdy kazda kazde vsetko vsetky vsechno vsechny
nebo proto protoze take taky tato tento tyto nase nasi vas vase jejich jeho jeji
informace informaci sluzby sluzeb produkty produktu proti podla neho nejmene vice""".split())

GENERIC = set("""software platform tool business company solution solutions service
services app product products best free online system systems data digital global
quality professional reliable modern simple smart""".split())

# Chrome, escaped markup and policy words that repeat on almost every page. A class
# name or a JS string can survive tag stripping, so they are kept out by name too.
NAV |= set("""choice affordable across private personal loading eager opacity
javascript enable browser skip content com www div span class href src alt button
input label svg path fill modal px flex grid such quick details enquire inquire
other provide""".split())

CODE_CHARS = set("<>{}[]()\\/|=;:\"'`*%$#@&~^!?")
COMMENT_RX = re.compile(r"(?is)<!--.*?-->")
OPEN_Q = r"""(?:"[^"]*"|'[^']*'|[^<>"'])*>"""
SCRIPT_RX = re.compile(r"(?is)<(script|style)\b" + OPEN_Q + r".*?</\1>")
BLOCK_RX = re.compile(r"(?is)<(template|noscript|svg)\b" + OPEN_Q + r".*?</\1>")
TOKEN_RX = re.compile(r"[^\W\d_][\w+-]*")
# A tag can hold '>' inside a quoted attribute ("x > 0 ? 'a' : 'b'"). The plain
# <[^>]+> then stops early and the rest of the tag is read as page text, so quotes
# are honoured here before the text is stripped.
TAG_Q_RX = re.compile(r"""(?s)<[a-zA-Z/!][^<>"']*(?:(?:"[^"]*"|'[^']*')[^<>"']*)*>""")
ATTR_SOUP_RX = re.compile(r"""(?<![^\s>])[\w:.\-]+=(?:"[^"]*"|'[^']*')""")


def _clean(value):
    return WS_RX.sub(" ", value or "").strip()


def fold(value):
    """'Žalúzie' -> 'zaluzie', so two spellings of one word count once."""
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(ch for ch in text if not unicodedata.combining(ch)).lower().strip()


def tokens(value):
    """Whole words, accents kept: 'markízy' stays one word instead of 'mark' + 'zy'."""
    return TOKEN_RX.findall(str(value or "").lower())


def _readable(key):
    """Letters only, not chrome, not a class name or a JS fragment."""
    return (len(key) >= 3 and key.isalpha() and key not in STOP and key not in SK_CZ
            and key not in NAV
            and key not in GENERIC and key not in radar.STOP_WORDS)


def visible_text(html):
    """The text a visitor sees: comments, hidden templates and code blocks removed."""
    text_value = COMMENT_RX.sub(" ", html or "")
    text_value = SCRIPT_RX.sub(" ", text_value)
    text_value = BLOCK_RX.sub(" ", text_value)
    text_value = TAG_Q_RX.sub(" ", text_value)
    text_value = ATTR_SOUP_RX.sub(" ", text_value)
    return net.strip_tags(text_value)


def _boilerplate_sentence(text):
    low = " " + fold(text) + " "
    return ("cookie" in low or "suhlas" in low or "consent" in low
            or "nastavenia" in low or "prijat vsetky" in low or "accept all" in low
            or "we use cookies" in low or "pouziva cookies" in low)


def _drop_boilerplate(text):
    """Remove the consent/notice sentences before an offer is read out of the page."""
    parts = re.split(r"(?<=[.!?])\s+|\n+", text or "")
    kept = [part for part in parts if part.strip() and not _boilerplate_sentence(part)]
    return " ".join(kept)


def _meta(html):
    out = {}
    for tag in META_RX.findall(html):
        attrs = {}
        for match in ATTR_RX.finditer(tag):
            key = (match.group(1) or "").lower()
            value = match.group(2) if match.group(2) is not None else match.group(3)
            attrs[key] = value or ""
        name = (attrs.get("property") or attrs.get("name") or "").lower()
        content = _clean(attrs.get("content"))
        if name and content:
            out.setdefault(name, content)
    return out


def _headings(html):
    out = {"title": "", "h1": [], "h2": []}
    for kind, body in TITLE_RX.findall(html):
        text = _clean(net.strip_tags(body))
        kind = kind.lower()
        if kind == "title":
            if not out["title"]:
                out["title"] = text
        elif text and text not in out[kind]:
            out[kind].append(text)
    return out


def _name(meta, heads):
    if meta.get("og:site_name"):
        return meta["og:site_name"][:80]
    for value in (heads["title"], heads["h1"][0] if heads["h1"] else ""):
        if value:
            parts = [p for p in SPLIT_RX.split(value) if p]
            return (parts[0] if parts else value)[:80]
    return ""


def _brand(host, heads):
    """The site's own name: the title part that matches the domain beats a slogan."""
    labels = [part for part in (host or "").lower().split(".") if part and part != "www"]
    brand = fold(labels[0]) if labels else ""
    if not brand:
        return ""
    for value in (heads["title"], heads["h1"][0] if heads["h1"] else ""):
        for part in SPLIT_RX.split(value or ""):
            if fold(part).strip(" .,-") == brand:
                return part.strip()[:80]
    return ""


def _description(meta, body):
    for key in ("og:description", "description", "twitter:description"):
        if len(meta.get(key, "")) > 20:
            return meta[key][:600]
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", body) if len(s.strip()) > 40]
    return " ".join(sentences[:2])[:600] if sentences else ""


def _keywords(meta, heads, body):
    out = []
    seen = set()

    def add(word, min_len=3):
        word = _clean(word).lower().strip(".,;:!?")
        if not word or any(ch in CODE_CHARS for ch in word):
            return
        key = fold(word)
        if len(word) >= min_len and _readable(key) and key not in seen:
            seen.add(key)
            out.append(word)

    # The headings that name what the business sells come first; nav headings come last
    # and are filtered anyway, so a page whose h2s are "Cookies" / "News" adds nothing.
    for text in heads["h1"][:3] + [heads["title"]]:
        for word in tokens(text):
            add(word, 4)
    for part in (meta.get("keywords") or "").split(","):
        add(part)
    counts = {}
    for word in tokens(body):
        key = fold(word)
        if not _readable(key):
            continue
        counts[key] = counts.get(key, 0) + 1
    from_body = 0
    for key, hits in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:40]:
        if from_body >= 6:
            break
        if hits >= 4 and len(key) >= 4 and key not in seen:
            seen.add(key)
            out.append(key)
            from_body += 1
    return out[:12]


def read_html(html, host=""):
    """A page already in memory -> the same fields read_site() returns."""
    meta = _meta(html)
    heads = _headings(html)
    body = _drop_boilerplate(_clean(visible_text(html)))[:20000]
    card = radar.parse_offer(body, strict=True)
    return {
        "name": _brand(host, heads) or _name(meta, heads),
        "category": card.get("category") or "",
        "description": _description(meta, body),
        "keywords": ", ".join(_keywords(meta, heads, body)),
        "buyer_role": card.get("buyer_role") or "",
        "price": card.get("price") or 0,
    }


def read_site(url):
    value = (url or "").strip()
    if not value:
        return {"ok": False, "error": "Paste the website first."}
    if not value.lower().startswith(("http://", "https://")):
        value = "https://" + value
    if not net.domain_of(value):
        return {"ok": False, "error": "That does not look like a web address."}
    try:
        html = net.text(value, timeout=25, retries=1)
    except net.NetError as exc:
        return {"ok": False, "error": "Could not open the page: %s" % exc}
    if len(html or "") < 200:
        return {"ok": False, "error": "The page came back empty. Is the address right?"}

    fields = read_html(html, net.domain_of(value))
    return {"ok": True, "url": value, "fields": fields,
            "found": [k for k, v in fields.items() if v],
            "note": "Read %d characters of the page with plain rules - no AI." % len(html or "")}
