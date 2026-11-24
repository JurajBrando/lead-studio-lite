"""Offers: the money layer.

An offer is a product you can refer a lead to - automated accounting, ads
tracking, social media management, referrals, whatever you resell - with the
affiliate link that pays you. Each offer carries keywords so the app can match
leads to it, a pitch template so the email writes itself, and optional
connections (an API key, an MCP command or a webhook) so the customer can be
hooked up to the tool after they say yes.
"""
import json
import re

from . import config, db, llm, mail, net

CONNECTION_KINDS = ("link", "api_key", "mcp", "webhook")
SECRET_HINTS = ("key", "token", "secret", "password")
MATCH_FIELDS = (("industry", 4), ("tags", 3), ("company", 2), ("title", 2), ("notes", 1))

DEFAULT_SUBJECT = "{{first_name|Hi}} - a thought for {{company}}"
DEFAULT_BODY = (
    "Hi {{first_name|there}},\n\n"
    "Quick note about {{offer_description}}\n\n"
    "If it is useful, here is the link: {{offer_link}}\n\n"
    "Best,\n{{sender_name}}")

# Wording deliberately uses the terms the rules use: "material connection",
# "commission or other compensation", "at no additional cost to you".
DEFAULT_DISCLOSURE = (
    "Affiliate disclosure: {company} may receive a commission or other compensation from "
    "{partner} if you purchase through the links on this page. This is a material connection "
    "between us and {partner}, disclosed in accordance with the applicable endorsement and "
    "consumer-protection rules, including the U.S. FTC Endorsement Guides (16 C.F.R. Part 255) "
    "and the EU Unfair Commercial Practices Directive (Directive 2005/29/EC, as amended). "
    "The commission is paid by {partner} and is already included in the price you pay: it is at "
    "no additional cost to you, it does not increase the price, and you pay the same amount as "
    "when buying directly. "
    "Our recommendation is our own assessment; we act neither as {partner}'s agent nor as a "
    "party to your agreement with them."
)


def disclosure_for(offer, settings=None):
    """The disclosure text for one offer, with the placeholders filled in."""
    settings = settings or config.load_settings()
    template = (offer.get("disclosure") or "").strip() or DEFAULT_DISCLOSURE
    partner = (net.domain_of(offer.get("affiliate_url") or "") or offer.get("name")
               or "the provider").strip()
    company = (settings.get("company_name") or "").strip() or "We"
    return (template.replace("{company}", company)
                    .replace("{partner}", partner)
                    .replace("{offer_name}", partner))


# ------------------------------------------------------------------- import
def import_rows(rows):
    """Insert offers from a list of dicts, skipping names or links already present."""
    added = skipped = 0
    for row in rows or []:
        if not isinstance(row, dict):
            skipped += 1
            continue
        name = (row.get("name") or "").strip()
        url = _clean_url(row.get("affiliate_url"))
        if not name:
            skipped += 1
            continue
        existing = db.one("SELECT id FROM offers WHERE name=?", (name,))
        if existing:
            skipped += 1
            continue
        result = save({**row, "affiliate_url": url})
        if result.get("ok"):
            added += 1
        else:
            skipped += 1
    return {"ok": True, "added": added, "skipped": skipped}


# --------------------------------------------------------------------- CRUD
def _clean_url(value):
    value = (value or "").strip()
    if not value:
        return ""
    if not value.lower().startswith(("http://", "https://")):
        value = "https://" + value
    return value if net.domain_of(value) or "localhost" in value else ""


def list_offers():
    rows = db.rows("SELECT * FROM offers ORDER BY active DESC, name COLLATE NOCASE")
    for row in rows:
        row["connections"] = len(db.rows("SELECT id FROM offer_connections WHERE offer_id=?",
                                         (row["id"],)))
        row["keyword_list"] = keywords(row)
    return rows


def get(offer_id):
    return db.one("SELECT * FROM offers WHERE id=?", (offer_id,))


def save(payload):
    name = (payload.get("name") or "").strip()[:120]
    if not name:
        return {"ok": False, "error": "Give the offer a name."}
    data = {
        "name": name,
        "category": (payload.get("category") or "").strip()[:80],
        "description": (payload.get("description") or "").strip()[:2000],
        "affiliate_url": _clean_url(payload.get("affiliate_url")),
        "keywords": (payload.get("keywords") or "").strip()[:600],
        "pitch_subject": (payload.get("pitch_subject") or "").strip()[:300],
        "pitch_body": (payload.get("pitch_body") or "").strip()[:6000],
        "commission": (payload.get("commission") or "").strip()[:200],
        "disclosure": (payload.get("disclosure") or "").strip()[:4000],
        "active": 1 if payload.get("active", True) else 0,
        "updated_ts": db.now(),
    }
    offer_id = payload.get("id")
    if offer_id:
        if not get(offer_id):
            return {"ok": False, "error": "That offer no longer exists."}
        # Only the keys the caller sent are written: a partial save (a rename, an
        # active flip) never blanks a column it never mentioned.
        data = {key: value for key, value in data.items()
                if key == "updated_ts" or key in payload}
        db.update("offers", offer_id, data)
        return {"ok": True, "id": offer_id}
    data["created_ts"] = db.now()
    return {"ok": True, "id": db.insert("offers", data)}


def delete(offer_id):
    db.execute("DELETE FROM offer_connections WHERE offer_id=?", (offer_id,))
    db.execute("DELETE FROM offers WHERE id=?", (offer_id,))
    return {"ok": True}


# ------------------------------------------------------------- connections
def _mask_config(config_value):
    out = {}
    for key, value in (config_value or {}).items():
        text_value = str(value or "")
        if any(hint in key.lower() for hint in SECRET_HINTS) and text_value:
            out[key] = "saved:" + ("*" * 6) + text_value[-2:]
        else:
            out[key] = text_value
    return out


def connections(offer_id):
    rows = db.rows("SELECT * FROM offer_connections WHERE offer_id=? ORDER BY id", (offer_id,))
    for row in rows:
        try:
            stored = json.loads(row.get("config") or "{}")
        except ValueError:
            stored = {}
        row["config_masked"] = _mask_config(stored)
        row["has_secret"] = any(v.startswith("saved:") for v in row["config_masked"].values())
    return rows


def add_connection(payload):
    offer_id = payload.get("offer_id")
    if not offer_id or not get(offer_id):
        return {"ok": False, "error": "Choose an offer first."}
    kind = payload.get("kind") if payload.get("kind") in CONNECTION_KINDS else "link"
    values = {}
    if kind == "link":
        values["url"] = _clean_url(payload.get("url") or payload.get("value"))
    elif kind == "api_key":
        values["key"] = (payload.get("key") or payload.get("value") or "").strip()
        values["base_url"] = _clean_url(payload.get("base_url") or "")
    elif kind == "mcp":
        values["command"] = (payload.get("command") or payload.get("value") or "").strip()[:300]
    else:
        values["url"] = _clean_url(payload.get("url") or payload.get("value"))
    values = {k: v for k, v in values.items() if v}
    if not values:
        return {"ok": False, "error": "Nothing to save for that connection type."}
    connection_id = db.insert("offer_connections", {
        "offer_id": offer_id, "kind": kind,
        "label": (payload.get("label") or kind).strip()[:80],
        "config": json.dumps(values, ensure_ascii=False), "created_ts": db.now()})
    return {"ok": True, "id": connection_id}


def delete_connection(connection_id):
    db.execute("DELETE FROM offer_connections WHERE id=?", (connection_id,))
    return {"ok": True}


# ---------------------------------------------------------------- matching
def keywords(offer):
    raw = (offer.get("keywords") or "") + "," + (offer.get("category") or "")
    out = []
    for part in re.split(r"[,;]", raw):
        word = part.strip().lower()
        if len(word) >= 3 and word not in out:
            out.append(word)
    return out


def score_lead(offer, lead):
    words = keywords(offer)
    if not words:
        return 0
    score = 0
    for field, weight in MATCH_FIELDS:
        haystack = str(lead.get(field) or "").lower()
        if not haystack:
            continue
        for word in words:
            if word in haystack:
                score += weight
    return score


def matches(offer, limit=50, min_score=2):
    """Leads that look like they need this offer, best first."""
    rows = db.rows("SELECT * FROM leads ORDER BY id DESC LIMIT 4000")
    scored = []
    for lead in rows:
        score = score_lead(offer, lead)
        if score >= min_score:
            scored.append({"lead": lead, "score": score})
    scored.sort(key=lambda item: (-item["score"], item["lead"]["id"]))
    return scored[:limit]


def match_counts(min_score=2):
    rows = db.rows("SELECT * FROM offers WHERE active=1")
    leads = db.rows("SELECT id, industry, tags, company, title, notes FROM leads LIMIT 4000")
    counts = {}
    for offer in rows:
        words = keywords(offer)
        if not words:
            counts[offer["id"]] = 0
            continue
        total = 0
        for lead in leads:
            score = 0
            for field, weight in MATCH_FIELDS:
                haystack = str(lead.get(field) or "").lower()
                if haystack:
                    for word in words:
                        if word in haystack:
                            score += weight
            if score >= min_score:
                total += 1
        counts[offer["id"]] = total
    return counts


# ------------------------------------------------------------------- pitch
def _fields(offer, lead):
    settings = config.load_settings()
    fields = mail.lead_fields(lead or {})
    fields.update({
        "offer_name": offer.get("name") or "",
        "offer_description": offer.get("description") or "",
        "offer_link": offer.get("affiliate_url") or "",
        "commission": offer.get("commission") or "",
        "sender_name": settings.get("sender_name") or "",
        "my_company": settings.get("company_name") or "",
    })
    return fields


def render_pitch(offer, lead, use_ai=False, provider_id=None):
    """Return the email to send: template first, AI rewrite when asked and available."""
    fields = _fields(offer, lead)
    subject, _ = mail.merge(offer.get("pitch_subject") or DEFAULT_SUBJECT, fields)
    body, _ = mail.merge(offer.get("pitch_body") or DEFAULT_BODY, fields)
    used_ai = False
    ai_error = ""
    link = offer.get("affiliate_url") or ""

    if use_ai:
        try:
            prompt = (
                "Write a short cold email (max 90 words, no bullet points, no hype) from "
                "%s at %s to %s at %s (%s). The product: %s - %s. End with one clear question "
                "and do not invent facts. Return JSON with keys subject and body."
                % (fields.get("sender_name") or "me", fields.get("my_company") or "my company",
                   fields.get("first_name") or "the contact", fields.get("company") or "their company",
                   fields.get("title") or "unknown role", offer.get("name"),
                   offer.get("description") or ""))
            data = llm.chat([{"role": "user", "content": prompt}], provider_id=provider_id,
                            max_tokens=500, temperature=0.4)
            match = re.search(r"\{.*\}", data, re.S)
            payload = json.loads(match.group(0)) if match else {}
            if payload.get("subject"):
                subject = str(payload["subject"])[:200]
            if payload.get("body"):
                body = str(payload["body"])[:4000]
            used_ai = True
        except (llm.LLMError, ValueError) as exc:
            ai_error = str(exc)

    if link and link not in body:
        body = body.rstrip() + "\n\n" + link
    disclosure = disclosure_for(offer)
    if disclosure and disclosure not in body:
        body = body.rstrip() + "\n\n--\n" + disclosure
    return {"subject": subject, "body": body, "link": link, "used_ai": used_ai,
            "ai_error": ai_error, "offer": offer.get("name"), "disclosure": disclosure,
            "lead_id": (lead or {}).get("id", 0)}


def build_page_html(offer, settings=None):
    """A self-contained offer page with the disclosure at the bottom."""
    import html as html_mod
    settings = settings or config.load_settings()
    esc = html_mod.escape
    name = esc(offer.get("name") or "Offer")
    description = esc(offer.get("description") or "")
    link = esc(offer.get("affiliate_url") or "#")
    commission = esc(offer.get("commission") or "")
    raw_disclosure = disclosure_for(offer, settings)
    if raw_disclosure.startswith("Affiliate disclosure:"):
        body_disclosure = raw_disclosure[len("Affiliate disclosure:"):].strip()
        disclosure = ("<strong>Affiliate disclosure.</strong> "
                      + esc(body_disclosure).replace("\n", "<br>"))
    else:
        disclosure = esc(raw_disclosure).replace("\n", "<br>")
    company = esc(settings.get("company_name") or "")
    sender = esc(settings.get("sender_name") or "")
    footer_who = (company + (" · " + sender if sender else "")) if company else sender
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%(name)s</title>
<style>
 :root{color-scheme:light}
 body{margin:0;background:#f6f7fb;color:#171a21;font:16px/1.65 -apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
 .wrap{max-width:720px;margin:0 auto;padding:56px 24px 32px}
 .card{background:#fff;border:1px solid #e4e7ef;border-radius:16px;padding:32px 30px;box-shadow:0 12px 32px rgba(20,25,40,.06)}
 h1{font-size:30px;line-height:1.2;margin:0 0 12px;letter-spacing:-.02em}
 p.lead{color:#4a5468;margin:0 0 24px;font-size:17px}
 .cta{display:inline-block;background:#2f6fed;color:#fff;text-decoration:none;font-weight:700;
      padding:17px 34px;border-radius:12px;font-size:17px;letter-spacing:-.01em}
 .cta:hover{background:#235ad0}
 .meta{margin-top:18px;font-size:13px;color:#6b7385}
 footer{margin-top:28px;padding-top:18px;border-top:1px solid #e4e7ef;font-size:12.5px;line-height:1.6;color:#5b6478}
 footer strong{color:#171a21}
 .who{margin-top:26px;font-size:12.5px;color:#8a91a3}
</style></head>
<body><div class="wrap"><div class="card">
 <h1>%(name)s</h1>
 <p class="lead">%(description)s</p>
 <a class="cta" href="%(link)s" rel="sponsored noopener" target="_blank">Get started</a>
 %(commission)s
 <footer>
  %(disclosure)s
 </footer>
 <div class="who">%(who)s</div>
</div></div></body></html>""" % {
        "name": name, "description": description, "link": link,
        "commission": ('<div class="meta">%s</div>' % commission) if commission else "",
        "disclosure": disclosure, "who": footer_who}
