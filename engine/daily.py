"""The Daily feed, aimed at what you sell.

Every job in the Daily group writes rows into `signals`. A signal by itself is just a finding;
this module scores it against the offers you actually sell (their keywords, over the company,
title, kind and domain), and then turns a good one into the three things you want next:

    promote()      the finding becomes a lead
    to_campaign()  the lead enters a sequence
    draft()        an email written from your offer's pitch
    contact()      that email goes out now, from a real mailbox, inside the daily cap

Nothing here invents data: the email address comes from the signal's domain (`info@`) or from
what you type, and a finding with no domain cannot be contacted.
"""
import json
import re
import unicodedata

from . import config, db, mail, net, offers, pipeline, radar

# Where the offer's keywords are looked for, and how much each field counts.
FIELDS = (("company", 3), ("title", 2), ("kind", 1), ("domain", 1))


def active_offers():
    rows = db.rows("SELECT * FROM offers ORDER BY active DESC, id")
    return [row for row in rows if int(row.get("active") or 0)] or rows


def score(offer, signal):
    """How well one offer fits one signal: {fit 0-100, matched words}."""
    words = offers.keywords(offer)
    if not words:
        return {"fit": 0, "matched": []}
    matched, raw = [], 0
    for field, weight in FIELDS:
        haystack = str(signal.get(field) or "").lower()
        for word in words:
            if word in haystack and word not in matched:
                matched.append(word)
                raw += weight
    payload = str(signal.get("payload") or "").lower()
    for word in words:
        if word in payload and word not in matched:
            matched.append(word)
            raw += 1
    return {"fit": min(100, raw * 25) if matched else 0, "matched": matched[:6]}


def best_offer(signal, offer_rows):
    best = None
    for offer in offer_rows:
        scored = score(offer, signal)
        if not scored["fit"]:
            continue
        if best is None or scored["fit"] > best["fit"]:
            best = {"id": offer["id"], "name": offer["name"],
                    "fit": scored["fit"], "matched": scored["matched"]}
    return best


def feed(limit=200, kind="", offer_id=0, min_fit=0):
    """Signals with the offer they fit, best fit first."""
    if kind:
        rows = db.rows("SELECT * FROM signals WHERE kind=? ORDER BY id DESC LIMIT ?", (kind, limit))
    else:
        rows = db.rows("SELECT * FROM signals ORDER BY id DESC LIMIT ?", (limit,))
    offer_rows = [offer for offer in active_offers()
                  if not offer_id or offer["id"] == int(offer_id)]
    out = []
    for row in rows:
        item = dict(row)
        best = best_offer(row, offer_rows)
        item["fit"] = best["fit"] if best else 0
        item["offer"] = best["name"] if best else ""
        item["offer_id"] = best["id"] if best else 0
        item["matched"] = best["matched"] if best else []
        item["payload"] = _payload(row.get("payload"))
        if min_fit and item["fit"] < int(min_fit):
            continue
        out.append(item)
    out.sort(key=lambda item: (-item["fit"], -item["id"]))
    return out


def _payload(raw):
    try:
        data = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


# ------------------------------------------------------------------ what the jobs found
SIGNAL_DAYS = 180
SIGNAL_LIMIT = 9000
SUMMARY_TTL = 300
_SUMMARY = {"key": None, "at": 0, "data": None}

# Words every job repeats ("hiring", "senior", "manager", a company suffix, a country):
# they say nothing about who the signal is about.
SIGNAL_STOP = set(radar.STOP_WORDS) | set("""hiring job jobs role remote senior junior staff
lead head chief principal manager director specialist engineer developer designer analyst
consultant assistant associate coordinator executive officer president founder owner partner
team department division group full part time contract permanent hybrid office based location
posted new open apply careers career salary position description responsibilities requirements
experience years work working company companies business businesses services solutions platform
software technology technologies digital online global international national regional local
marketing media agency shop store ecommerce e-commerce retail wholesale trade industry
get started learn more book demo contact today mail server data warm hot cold site
home runs run page pages visit view read search duck duckduckgo google bing tool tools""".split())

SIGNAL_CORP = set("""gmbh srl sro ltd llc inc corp co company group holding holdings international
consulting solutions services service agency marketing digital media shop store online global
trading enterprises industries systems technology technologies partners ventures capital invest
sarl spa sl sa lda kft sia ou bv nv ab oy plc pty pte ug kg mbh eood doo limited""".split())


def _fold(value):
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(ch for ch in text if not unicodedata.combining(ch)).lower().strip()


COUNTRY_FOLD = ({_fold(alias) for alias in net.COUNTRY_ALIASES}
                | {_fold(name) for name in net.COUNTRIES})


def _signal_words(value):
    text = re.sub(r"\[[^\]]*\]", " ", str(value or ""))
    # A domain or a URL in a title names the source, not what the finding is about.
    text = re.sub(r"(?i)\b(?:https?://)?(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)*"
                  r"\.(?:com|net|org|io|co|eu|sk|cz|de|at|uk|pl|hu|fr|es|it|nl|be|ch|se|"
                  r"no|dk|fi|ro|bg|hr|si|rs|ua|us|ca|au|nz|info|biz|shop|store|online|"
                  r"site|app|ai|dev)\b(?:/\S*)?", " ", text)
    out = []
    for token in re.findall(r"[^\W\d_][\w'+-]*", text.lower()):
        token = token.strip("'+-")
        if (len(token) < 4 or token.isdigit() or token in SIGNAL_STOP or token in SIGNAL_CORP
                or _fold(token) in COUNTRY_FOLD or not re.search(r"[a-z]", token)):
            continue
        out.append(token)
    return out


def _top(counts, limit):
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [name for name, _count in ranked[:limit]]


def signals_summary(days=SIGNAL_DAYS, limit=SIGNAL_LIMIT):
    """Every finding the daily jobs wrote, folded into the points a hunt can aim at.

    It reads the `signals` table the jobs already fill - ad spenders, hiring, qualified
    sites and their flags, new domains, copycats - and returns what repeats: the kinds and
    their counts, the words in the companies and titles, the flags the qualifying jobs
    found, the cohorts and offers they ran for, and the countries they sit in. Counts only;
    nothing here is inferred from a page.
    """
    key = (int(days), int(limit))
    if _SUMMARY["key"] == key and _SUMMARY["at"] and db.now() - _SUMMARY["at"] < SUMMARY_TTL:
        return dict(_SUMMARY["data"])
    cutoff = db.now() - int(days) * 86400
    rows = db.rows("SELECT kind, company, domain, title, payload, ts FROM signals "
                   "ORDER BY id DESC LIMIT ?", (int(limit),))
    kinds, words, flags, cohorts, offer_names, markets = {}, {}, {}, {}, {}, {}
    samples, total = [], 0
    for row in rows:
        ts = int(row.get("ts") or 0)
        if ts and ts < cutoff:
            continue
        kind = radar.kind_of(row.get("kind"))
        total += 1
        if kind != "mx_missing":            # a dead mailbox is not a market finding
            kinds[kind] = kinds.get(kind, 0) + 1
        payload = _payload(row.get("payload"))
        for field in ("signals", "flags"):
            value = payload.get(field)
            if isinstance(value, list):
                for item in value:
                    label = str(item).replace("_", " ").strip().lower()
                    if 3 <= len(label) <= 40:
                        flags[label] = flags.get(label, 0) + 1
        cohort = str(payload.get("cohort") or "").replace("_", " ").strip().lower()
        if cohort:
            cohorts[cohort] = cohorts.get(cohort, 0) + 1
        offer_name = str(payload.get("offer") or "").strip()
        if offer_name:
            offer_names[offer_name] = offer_names.get(offer_name, 0) + 1
        for field in ("location", "country"):
            name = net.country_in(payload.get(field))
            if name:
                markets[name] = markets.get(name, 0) + 1
        regions = payload.get("regions")
        if isinstance(regions, str):
            for part in re.split(r"[,;/]", regions):
                name = net.country_in(part.strip())
                if name:
                    markets[name] = markets.get(name, 0) + 1
        if kind != "mx_missing":
            # One company counts once per word, however many job posts it wrote: a big
            # brand with 400 postings must not outrank an industry that 40 firms name.
            source = str(row.get("company") or row.get("domain") or ts)
            for field in ("company", "title"):
                for word in _signal_words(row.get(field)):
                    words.setdefault(word, set()).add(source)
        if (row.get("domain") and kind not in ("mx_missing", "new_domain")
                and len(samples) < 8):
            samples.append({"company": str(row.get("company") or ""), "domain": row.get("domain"),
                            "kind": kind, "title": str(row.get("title") or "")[:80], "ts": ts})
    ranked = [word for word, sources in sorted(words.items(),
                                               key=lambda kv: (-len(kv[1]), kv[0]))
              if len(sources) >= 4][:10]
    data = {"total": total, "days": int(days), "kinds": kinds, "words": ranked,
            "flags": _top(flags, 8), "cohorts": _top(cohorts, 4),
            "offers": _top(offer_names, 4), "markets": _top(markets, 6),
            "samples": samples, "read_at": db.now()}
    _SUMMARY.update({"key": key, "at": data["read_at"], "data": data})
    return dict(data)


def signal(signal_id):
    row = db.one("SELECT * FROM signals WHERE id=?", (signal_id,))
    if not row:
        raise ValueError("That finding no longer exists.")
    return row


def lead_for(signal_row, email=""):
    """The lead this finding points at: the existing one, or a new one, or a clear error."""
    email = (email or "").strip().lower()
    if not email and signal_row["domain"]:
        email = "info@" + signal_row["domain"]
    if not email:
        return {"ok": False, "error": "This finding has no domain - type the email address to use."}
    if not mail.valid_email(email):
        return {"ok": False, "error": "That is not a valid email address."}
    note = "Signal %s: %s (%s)" % (signal_row["kind"],
                                   signal_row["company"] or signal_row["title"],
                                   signal_row["source_url"] or "")
    existing = db.one("SELECT * FROM leads WHERE email=?", (email,))
    if existing:
        notes = (existing.get("notes") or "").strip()
        if note not in notes:
            db.update("leads", existing["id"], {"notes": (notes + "\n" + note).strip()[:4000],
                                                "updated_ts": db.now()})
        db.execute("UPDATE signals SET lead_id=?, seen=1 WHERE id=?",
                   (existing["id"], signal_row["id"]))
        return {"ok": True, "lead_id": existing["id"], "existing": True, "email": email}

    ts = db.now()
    payload = _payload(signal_row.get("payload"))
    lead_id = db.insert("leads", {
        "email": email, "company": (signal_row["company"] or "")[:200],
        "domain": signal_row["domain"] or email.rsplit("@", 1)[-1],
        "title": (signal_row["title"] or "")[:200],
        "industry": str(payload.get("industry") or "")[:120],
        "city": str(payload.get("city") or "")[:120],
        "country": str(payload.get("country") or "")[:120],
        "source": signal_row["kind"], "status": "new", "verify_status": "unverified",
        "notes": note[:2000], "created_ts": ts, "updated_ts": ts})
    db.execute("UPDATE signals SET lead_id=?, seen=1 WHERE id=?", (lead_id, signal_row["id"]))
    db.event("promote", "%s -> %s" % (signal_row["kind"], email))
    return {"ok": True, "lead_id": lead_id, "existing": False, "email": email}


def promote(signal_id, email=""):
    return lead_for(signal(signal_id), email)


def _offer_for(signal_row, offer_id):
    """The requested offer, else the best fit for this signal, else the first one."""
    rows = active_offers()
    if offer_id:
        chosen = [row for row in rows if row["id"] == int(offer_id)]
        if chosen:
            return chosen[0]
    best = best_offer(signal_row, rows)
    if best:
        return offers.get(best["id"])
    return rows[0] if rows else None


def draft(signal_id, offer_id=0, use_ai=False):
    """An email for this finding, written from your offer's pitch."""
    signal_row = signal(signal_id)
    offer = _offer_for(signal_row, offer_id)
    if not offer:
        return {"ok": False, "error": "No offer to pitch. Add one in Offers first."}
    lead = {"company": signal_row["company"] or signal_row["title"],
            "title": signal_row["title"] or "", "email": "",
            "first_name": "", "industry": str(_payload(signal_row.get("payload")).get("industry") or ""), "notes": ""}
    pitch = offers.render_pitch(offer, lead, use_ai=use_ai)
    to_email = (signal_row["domain"] and "info@" + signal_row["domain"]) or ""
    return {"ok": True, "to": to_email, "subject": pitch["subject"], "body": pitch["body"],
            "offer": offer["name"], "offer_id": offer["id"], "used_ai": pitch["used_ai"],
            "ai_error": pitch["ai_error"], "link": pitch["link"]}


def to_campaign(signal_id, sequence_id, email="", offer_id=0):
    signal_row = signal(signal_id)
    if not sequence_id:
        return {"ok": False, "error": "Pick a sequence first."}
    sequence = db.one("SELECT * FROM sequences WHERE id=?", (int(sequence_id),))
    if not sequence:
        return {"ok": False, "error": "That sequence no longer exists."}
    answer = lead_for(signal_row, email)
    if not answer["ok"]:
        return answer
    enrolled = pipeline.enroll(int(sequence_id), [answer["lead_id"]], mailbox_id=0)
    if not enrolled.get("ok"):
        return {"ok": False, "error": enrolled.get("error") or "Could not enrol that lead."}
    return {"ok": True, "lead_id": answer["lead_id"], "sequence": sequence["name"],
            "email": answer["email"], "existing": answer["existing"]}


def contact(signal_id, subject, body, email="", mailbox_id=0, offer_id=0):
    """Send the email now, from a real mailbox, inside its daily cap."""
    signal_row = signal(signal_id)
    answer = lead_for(signal_row, email)
    if not answer["ok"]:
        return answer
    lead = db.one("SELECT * FROM leads WHERE id=?", (answer["lead_id"],))
    box = pipeline.mailbox_for(int(mailbox_id or 0))
    if not box:
        return {"ok": False, "error": "No active mailbox - add one in Mailboxes."}
    if pipeline.sent_today(box["id"]) >= int(box.get("daily_limit") or 30):
        return {"ok": False, "error": "%s is at its daily limit (%s)."
                                      % (box["name"], box.get("daily_limit"))}
    settings = config.load_settings()
    fields = mail.lead_fields(lead)
    fields["sender_name"] = settings.get("sender_name") or ""
    fields["my_company"] = settings.get("company_name") or ""
    subject, _ = mail.merge(subject or "", fields)
    body, _ = mail.merge(body or "", fields)
    ok, detail = mail.smtp_send(box, lead["email"], subject, body)
    if not ok:
        return {"ok": False, "error": detail}
    ts = db.now()
    db.insert("sends", {"lead_id": lead["id"], "mailbox_id": box["id"],
                        "to_email": lead["email"], "subject": subject, "body": body,
                        "message_id": detail, "status": "sent", "sent_ts": ts})
    db.update("leads", lead["id"], {"status": "contacted", "updated_ts": ts})
    db.execute("UPDATE mailboxes SET last_send_ts=? WHERE id=?", (ts, box["id"]))
    db.event("daily", "%s contacted %s from %s" % (signal_row["kind"], lead["email"], box["name"]))
    return {"ok": True, "message_id": detail, "mailbox": box["name"], "to": lead["email"],
            "lead_id": lead["id"]}
