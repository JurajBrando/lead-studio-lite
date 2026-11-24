"""One place that turns enrichment into a lead row.

Every source ends with the same sentence - "this domain is X, reachable at Y". This module
merges that into the lead that already exists for the domain instead of inserting a duplicate:
missing fields are filled, the new evidence is appended once to the notes, and only a domain
with nothing at all gets a new row.

A lead may exist without an e-mail. A phone number and a description are still a contact, and
the `leads` table allows several such rows (the unique index only covers non-empty addresses).
"""
from . import db, net


def upsert(domain="", company="", email="", phone="", website="", description="",
           city="", country="", industry="", source="", notes="", status=None):
    """Merge one contact fact into the leads table. Returns {id, created, changed}."""
    domain = net.domain_of(domain)
    email = (email or "").strip().lower()
    if email and "@" not in email:
        email = ""
    now = db.now()
    row = None
    if email:
        row = db.one("SELECT * FROM leads WHERE email=?", (email,))
    if not row and domain:
        row = db.one("SELECT * FROM leads WHERE domain=? "
                     "ORDER BY (email<>'') DESC, id LIMIT 1", (domain,))

    if row:
        patch = {}
        if email and not (row.get("email") or "").strip():
            if not db.one("SELECT id FROM leads WHERE email=?", (email,)):
                patch["email"] = email
        for key, value in (("company", company), ("phone", phone), ("website", website),
                           ("description", description), ("city", city), ("country", country),
                           ("industry", industry)):
            value = (value or "").strip()
            if value and not (row.get(key) or "").strip():
                patch[key] = value[:2000] if key == "description" else value[:400]
        if notes:
            existing = row.get("notes") or ""
            if notes[:120] not in existing:
                patch["notes"] = (existing + "\n" + notes).strip()[:4000]
        if source and not (row.get("source") or "").strip():
            patch["source"] = source[:80]
        if status and (row.get("status") or "new") == "new":
            patch["status"] = status
        if not patch:
            return {"id": row["id"], "created": False, "changed": False}
        patch["updated_ts"] = now
        db.update("leads", row["id"], patch)
        return {"id": row["id"], "created": False, "changed": True}

    if not (email or company or phone or description or domain):
        return {"id": 0, "created": False, "changed": False}
    payload = {"email": email, "company": (company or domain)[:200], "domain": domain,
               "phone": (phone or "")[:60], "website": (website or "")[:400],
               "description": (description or "")[:2000], "city": (city or "")[:120],
               "country": (country or "")[:120], "industry": (industry or "")[:120],
               "notes": (notes or "")[:4000], "source": (source or "")[:80],
               "status": status or "new", "verify_status": "unverified" if email else "",
               "created_ts": now, "updated_ts": now}
    return {"id": db.insert("leads", payload), "created": True, "changed": True}


def enrich(lead_id, payload):
    """Fill fields of one known lead without touching what is already there."""
    if not lead_id:
        return False
    row = db.one("SELECT * FROM leads WHERE id=?", (lead_id,))
    if not row:
        return False
    patch = {key: value for key, value in payload.items()
             if value and not (row.get(key) or "").strip() and key in row}
    if not patch:
        return False
    patch["updated_ts"] = db.now()
    db.update("leads", lead_id, patch)
    return True
