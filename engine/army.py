"""The Army: soldiers that each work one mailbox and one offer, and level up from replies.

Ported from Sales Guy's army and laid over Lead Studio's own plumbing:

  * a soldier = 1 mailbox + 1 offer; its territory is what Radar approved for that offer
  * ranks from XP (send 5, reply 10, positive reply 50); a rank's energy caps sends a day,
    and so does the mailbox's own daily limit (shared with the sequence queue)
  * the weekly mission (3 positive replies) and its leaderboard
  * doctrine: which angle (pain, timing, competitor, roi) earns replies, per offer,
    chosen by Thompson sampling once every angle has 20 sends behind it
  * a 14-day claim per company so two soldiers never write to the same one
  * a mailbox with a bounce rate over 3% benches its soldier
  * every message is written for one company from what the Radar read on its site,
    by the LLM you pick (OpenRouter or any provider), with a template fallback
  * replies are sorted into positive / not_now / unsub / bounce / auto_reply and pay XP

Messages wait for your approval by default ("review first"); switch it off to let the
soldiers send on their own inside the sending window.
"""
import email.utils
import json
import random
import re
import secrets
import threading
import time
import traceback

from . import config, db, hunt, llm, mail, offers, pipeline, radar

RANKS = [
    {"name": "Recruit", "xp": 0, "energy": 30, "unlock": "10 a day while training, then 30"},
    {"name": "Soldier", "xp": 200, "energy": 40, "unlock": "40 a day"},
    {"name": "Sergeant", "xp": 600, "energy": 50, "unlock": "50 a day, follow-ups after three days"},
    {"name": "Captain", "xp": 1500, "energy": 75, "unlock": "75 a day, works the top of the territory first"},
    {"name": "Major", "xp": 3500, "energy": 100, "unlock": "100 a day"},
]
STATES = ("training", "active", "paused", "benched")
ANGLES = ("pain", "timing", "competitor", "roi")
ANGLE_HELP = {
    "pain": "Open with the problem their site shows, in their words.",
    "timing": "Open with why now - the fresh trigger (hiring, ads, a change on the site).",
    "competitor": "Open with the tool they already run and what switching or adding changes.",
    "roi": "Open with one concrete number: time or money saved for a company of their size.",
}
BENCH_RATE = 0.03
BENCH_MIN_BOUNCES = 2
CLAIM_DAYS = 14
TRAINING_LIMIT = 10
TRAINING_GRADUATE = 20
FOLLOWUP_DAYS = 3
FOLLOWUP_MAX = 1
FOLLOWUP_RANK = "Sergeant"
BANDIT_MIN = 20
XP_SEND = 5
XP_REPLY = 10
XP_POSITIVE = 50
NOT_NOW_DAYS = 90
AUTO_REPLY_SHIFT_DAYS = 2
MISSION_TARGET = 3
MISSION_XP = 150
REPLY_CLASSES = ("positive", "not_now", "unsub", "bounce", "auto_reply")
CALLSIGNS = ["Falcon", "Viper", "Wolf", "Hawk", "Bison", "Lynx", "Raven", "Tatra", "Orava", "Bear",
             "Eagle", "Fox", "Stag", "Badger", "Kestrel", "Otter", "Cobra", "Jaguar", "Heron", "Puma",
             "Mamba", "Condor", "Marten", "Ibex"]

REPLY_STEMS = {
    "unsub": ["odhlás", "odhlas", "unsubscrib", "vymaž", "vymaz", "neželám si", "nezelam si",
              "nechcem dostáva", "nechcem dostava", "take me off", "remove me", "stop emailing",
              "odstrániť z databáz", "odstranit z databaz", "nepiště mi", "nepiste mi"],
    "bounce": ["nedoručiteľ", "nedorucitel", "undelivered", "delivery status notification",
               "mail delivery failed", "550 5.1.1", "user unknown", "recipient not found",
               "mailbox unavailable", "adresát neexistuje", "adresat neexistuje",
               "nebyla doručena", "nebyla dorucena", "nedoručiteln", "nedoruciteln", "undeliverable"],
    "auto_reply": ["automatická odpoveď", "automaticka odpoved", "autoreply", "auto-reply", "automatic reply",
                   "out of office", "mimo kancelár", "mimo kancelar", "na dovolenke", "na dovolené",
                   "mimo prace", "mimo práce", "automatická odpověď", "mimo kancelář",
                   "dovolená", "dovolena", "abwesenheit"],
    "not_now": ["neskôr", "neskor", "nemám teraz", "nemam teraz", "nemám záujem", "nemam zaujem",
                "budúci rok", "buduci rok", "next quarter", "not a good time", "not now", "not interested",
                "in a few months", "no thanks", "no, thanks", "nie, ďakujem", "nie, dakujem", "příští měsíc",
                "pristi mesic", "teď to nejde", "ted to nejde", "nemám zájem", "nemam zajem", "maybe later"],
    "positive": ["zaujím", "zaujim", "pošlite", "poslite", "cenník", "cennik", "stretnut",
                 "schôdz", "schodz", "mám čas", "mam cas", "sounds good", "interested", "pricing",
                 "send me", "call me", "book a", "let's talk", "let us talk", "radšej", "radsej",
                 "pošlete", "poslete", "ceník", "sejít", "sejit", "můžeme se", "muzeme se",
                 "zájem", "zajem", "tell me more", "more info", "happy to chat", "schedule a call"],
}
REPLY_WORDS = {"unsub": ["stop", "unsubscribe"], "positive": ["yes", "áno", "ano", "sure"]}

DEFAULT_SETTINGS = {"auto": False, "review_first": True, "writer_provider_id": 0, "include_link": True,
                    "followups": True, "tick_minutes": 5, "drafts_per_tick": 3}
TICK_LOCK = threading.Lock()


# ------------------------------------------------------------------ settings
def settings():
    s = config.load_settings()
    out = dict(DEFAULT_SETTINGS)
    out.update(s.get("army") or {})
    return out


def save_settings(p):
    s = config.load_settings()
    cur = dict(DEFAULT_SETTINGS)
    cur.update(s.get("army") or {})
    for key in ("auto", "review_first", "include_link", "followups"):
        if key in p:
            cur[key] = bool(p[key])
    for key, lo, hi in (("writer_provider_id", 0, 10 ** 9), ("tick_minutes", 1, 120), ("drafts_per_tick", 1, 20)):
        if key in p:
            try:
                cur[key] = max(lo, min(hi, int(p[key] or 0)))
            except (TypeError, ValueError):
                pass
    s["army"] = cur
    config.save_settings(s)
    return {"ok": True, "settings": cur}


def connect_openrouter(api_key, model="", make_writer=True):
    """One click: an OpenRouter provider in Settings -> LLM providers, used as the Army's writer."""
    api_key = (api_key or "").strip()
    model = (model or "").strip() or "openai/gpt-4o-mini"
    existing = db.one("SELECT * FROM llm_providers WHERE base_url LIKE 'https://openrouter.ai%' ORDER BY id LIMIT 1")
    if not api_key and not existing:
        return {"ok": False, "error": "Paste your OpenRouter API key (openrouter.ai/keys)."}
    payload = {"name": "OpenRouter", "kind": "openai", "base_url": "https://openrouter.ai/api/v1",
               "model": model}
    if api_key:
        payload["api_key"] = api_key
    if existing:
        payload["id"] = existing["id"]
        payload["name"] = existing["name"]
    res = llm.save(payload)
    if not res.get("ok"):
        return res
    if make_writer:
        save_settings({"writer_provider_id": res["id"]})
    test = llm.test(res["id"])
    return {"ok": True, "id": res["id"], "test": test}


def writer_status():
    s = settings()
    pid = int(s.get("writer_provider_id") or 0)
    provider = llm.get(pid or None)
    rows = llm.list_providers()
    return {"configured": bool(provider), "provider_id": (provider or {}).get("id", 0),
            "name": (provider or {}).get("name", ""), "model": (provider or {}).get("model", ""),
            "openrouter": any("openrouter.ai" in (r.get("base_url") or "") for r in rows),
            "providers": [{"id": r["id"], "name": r["name"], "model": r["model"]} for r in rows]}


# ------------------------------------------------------------------ helpers
def _now():
    return db.now()


def _day_start():
    return int(time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1)))


def _week_start():
    lt = time.localtime()
    start = time.mktime(lt[:3] + (0, 0, 0, 0, 0, -1))
    return int(start - lt.tm_wday * 86400)


def rank_for(xp):
    xp = int(xp or 0)
    found, next_xp = RANKS[0], 0
    for item in RANKS:
        if xp >= item["xp"]:
            found = item
        elif not next_xp:
            next_xp = item["xp"]
    level = RANKS.index(found) + 1
    return {"name": found["name"], "level": level, "energy": found["energy"], "unlock": found["unlock"],
            "xp": xp, "next_xp": next_xp, "floor_xp": found["xp"]}


def _rank_at_least(xp, name):
    want = next((i for i, r in enumerate(RANKS) if r["name"] == name), 0)
    return rank_for(xp)["level"] - 1 >= want


def soldier(sid):
    return db.one("SELECT * FROM soldiers WHERE id=?", (int(sid or 0),))


def company_key(domain, company=""):
    domain = (domain or "").strip().lower()
    if domain:
        return "domain:" + domain
    name = re.sub(r"[^a-z0-9]+", " ", (company or "").lower()).strip()
    return ("company:" + name) if name else ""


def event(sid, kind, lead_id=0, offer_id=0, angle="", send_id=0, xp=0, detail=""):
    return db.insert("army_events", {"ts": _now(), "soldier_id": int(sid or 0), "lead_id": int(lead_id or 0),
                                     "offer_id": int(offer_id or 0), "kind": kind, "angle": angle or "",
                                     "send_id": int(send_id or 0), "xp": int(xp or 0),
                                     "detail": (detail or "")[:400]})


def add_xp(sid, delta, reason, lead_id=0):
    row = soldier(sid)
    if not row or not delta:
        return None
    before = rank_for(row["xp"])["name"]
    db.execute("UPDATE soldiers SET xp=xp+?, updated_ts=? WHERE id=?", (int(delta), _now(), row["id"]))
    after = rank_for(int(row["xp"]) + int(delta))
    event(sid, "xp", lead_id=lead_id, xp=delta, detail=reason)
    if after["name"] != before:
        event(sid, "rank", detail="promoted to " + after["name"])
        db.event("army", "%s promoted to %s" % (row["name"], after["name"]))
    return after


# ------------------------------------------------------------------ mailbox health and energy
def health(mailbox_id, days=30):
    since = _now() - days * 86400
    sent = db.scalar("SELECT COUNT(*) FROM sends WHERE mailbox_id=? AND status='sent' AND sent_ts>=?",
                     (mailbox_id, since))
    bounces = db.scalar("SELECT COUNT(*) FROM replies WHERE mailbox_id=? AND received_ts>=? AND "
                        "(is_bounce=1 OR class='bounce')", (mailbox_id, since))
    rate = bounces / float(max(1, sent + bounces))
    status = "ok"
    if bounces >= BENCH_MIN_BOUNCES and rate > BENCH_RATE:
        status = "benched"
    elif bounces:
        status = "watch"
    return {"sent": sent, "bounces": bounces, "rate": round(rate, 4), "status": status}


def _pending_drafts(sid):
    return db.scalar("SELECT COUNT(*) FROM army_drafts WHERE soldier_id=? AND status IN ('pending','approved') "
                     "AND created_ts>=?", (sid, _day_start()))


def energy(row, box=None):
    box = box or pipeline.mailbox_for(row["mailbox_id"])
    h = health(row["mailbox_id"])
    rk = rank_for(row["xp"])
    limit = int((box or {}).get("daily_limit") or 30) if box else 0
    limit = min(limit, rk["energy"])
    if row["state"] == "training":
        limit = min(limit, TRAINING_LIMIT)
    if row["state"] in ("benched", "paused") or h["status"] == "benched" or not box or not box.get("active", 1):
        limit = 0
    used = pipeline.sent_today(row["mailbox_id"]) if box else 0
    queued = _pending_drafts(row["id"])
    return {"limit": limit, "used": used, "queued": queued, "left": max(0, limit - used - queued),
            "health": h, "rank": rk}


def check_bench(sid):
    row = soldier(sid)
    if not row or row["state"] == "benched":
        return False
    h = health(row["mailbox_id"])
    if h["status"] != "benched":
        return False
    db.update("soldiers", row["id"], {"state": "benched", "updated_ts": _now()})
    event(sid, "benched", detail="bounce rate %.1f%% on its mailbox" % (h["rate"] * 100))
    db.event("army", "%s benched: bounce rate %.1f%%" % (row["name"], h["rate"] * 100))
    return True


# ------------------------------------------------------------------ soldiers
def _free_callsign():
    used = {r["name"] for r in db.rows("SELECT name FROM soldiers")}
    for name in CALLSIGNS:
        if name not in used:
            return name
    return "Soldier %d" % (len(used) + 1)


def save_soldier(p):
    sid = int(p.get("id") or 0)
    row = soldier(sid) if sid else None
    mailbox_id = int(p.get("mailbox_id") or (row or {}).get("mailbox_id") or 0)
    if not pipeline.mailbox_for(mailbox_id):
        return {"ok": False, "error": "Pick the mailbox this soldier sends from."}
    owner = db.one("SELECT id, name FROM soldiers WHERE mailbox_id=? AND id<>?", (mailbox_id, sid))
    if owner:
        return {"ok": False, "error": "%s already sends from that mailbox - one soldier per inbox." % owner["name"]}
    offer_id = int(p.get("offer_id") if p.get("offer_id") is not None else (row or {}).get("offer_id") or 0)
    if offer_id and not offers.get(offer_id):
        return {"ok": False, "error": "That offer no longer exists."}
    data = {"name": (p.get("name") or (row or {}).get("name") or _free_callsign()).strip()[:40],
            "mailbox_id": mailbox_id, "offer_id": offer_id,
            "persona": (p.get("persona") if p.get("persona") is not None else (row or {}).get("persona") or "")[:600],
            "language": p.get("language") or (row or {}).get("language") or "auto",
            "updated_ts": _now()}
    if p.get("angle") is not None:
        angle = p.get("angle") or ""
        data["angle"] = angle if angle in ANGLES else ""
        data["angle_locked"] = 1 if angle in ANGLES else 0
    if p.get("state") in STATES:
        data["state"] = p["state"]
    if row:
        if row["offer_id"] != offer_id:
            release_claims(row["id"])
        db.update("soldiers", row["id"], data)
        return {"ok": True, "id": row["id"]}
    data.update({"state": data.get("state", "training"), "xp": 0, "created_ts": _now()})
    new_id = db.insert("soldiers", data)
    event(new_id, "recruited", offer_id=offer_id, detail="joined on %s" % (pipeline.mailbox_for(mailbox_id) or {}).get("from_email", ""))
    return {"ok": True, "id": new_id}


def delete_soldier(sid):
    row = soldier(sid)
    if not row:
        return {"ok": False, "error": "That soldier is already gone."}
    release_claims(sid)
    db.execute("UPDATE army_drafts SET status='rejected', error='soldier dismissed' "
               "WHERE soldier_id=? AND status IN ('pending','approved')", (sid,))
    db.execute("UPDATE radar_leads SET soldier_id=0 WHERE soldier_id=? AND status='approved'", (sid,))
    db.execute("DELETE FROM soldiers WHERE id=?", (sid,))
    return {"ok": True}


def set_state(sid=0, state="", everyone=False):
    if state not in STATES:
        return {"ok": False, "error": "Unknown state."}
    if everyone:
        if state == "paused":
            db.execute("UPDATE soldiers SET state='paused', updated_ts=? WHERE state IN ('active','training')", (_now(),))
        elif state == "active":
            db.execute("UPDATE soldiers SET state=CASE WHEN xp>=? THEN 'active' ELSE 'training' END, updated_ts=? "
                       "WHERE state='paused'", (TRAINING_GRADUATE * XP_SEND, _now()))
        return {"ok": True}
    row = soldier(sid)
    if not row:
        return {"ok": False, "error": "That soldier is gone."}
    if state == "active" and row["state"] == "benched":
        event(sid, "unbenched", detail="put back by hand")
    db.update("soldiers", sid, {"state": state, "updated_ts": _now()})
    return {"ok": True}


def deploy(offer_ids, mode="spread", reassign=False):
    """One click: a soldier on every free mailbox, on one offer or spread across several."""
    ids = [int(i) for i in (offer_ids or []) if offers.get(int(i))]
    if not ids:
        return {"ok": False, "error": "Pick at least one offer for the soldiers to sell."}
    if mode == "same":
        ids = ids[:1]
    boxes = [b for b in pipeline.active_mailboxes() if (b.get("from_email") or "").strip()]
    if not boxes:
        return {"ok": False, "error": "Add a mailbox first - every soldier needs its own inbox."}
    taken = {r["mailbox_id"]: r for r in db.rows("SELECT * FROM soldiers")}
    # Spread by need: the offer with the most approved companies per soldier gets the next one.
    load = {i: db.scalar("SELECT COUNT(*) FROM soldiers WHERE offer_id=?", (i,)) for i in ids}
    if reassign:
        load = {i: 0 for i in ids}
    territory = {i: db.scalar("SELECT COUNT(*) FROM radar_leads WHERE offer_id=? AND status='approved'", (i,))
                 for i in ids}
    turn = [0]

    def next_offer():
        if len(ids) == 1:
            return ids[0]
        empty = [i for i in ids if load[i] == 0]      # every ticked offer gets one soldier first
        if empty:
            load[empty[0]] += 1
            return empty[0]
        if any(territory.values()):
            pick = max(ids, key=lambda i: (territory[i] / float(load[i] + 1), -load[i]))
        else:
            pick = ids[turn[0] % len(ids)]
            turn[0] += 1
        load[pick] += 1
        return pick
    created, changed = 0, 0
    for box in boxes:
        row = taken.get(box["id"])
        if row:
            if reassign and row["state"] != "benched":
                offer_id = next_offer()
                if row["offer_id"] != offer_id:
                    release_claims(row["id"])
                    db.update("soldiers", row["id"], {"offer_id": offer_id, "updated_ts": _now()})
                    changed += 1
            continue
        res = save_soldier({"mailbox_id": box["id"], "offer_id": next_offer()})
        if res.get("ok"):
            created += 1
    return {"ok": True, "created": created, "reassigned": changed,
            "soldiers": db.scalar("SELECT COUNT(*) FROM soldiers")}


# ------------------------------------------------------------------ claims and suppression
def expire_claims():
    db.execute("DELETE FROM army_claims WHERE ts<?", (_now() - CLAIM_DAYS * 86400,))


def claim(key, sid):
    if not key:
        return False
    held = db.one("SELECT * FROM army_claims WHERE key=?", (key,))
    if held:
        return held["soldier_id"] == sid
    db.execute("INSERT OR IGNORE INTO army_claims(key, soldier_id, ts) VALUES(?,?,?)", (key, sid, _now()))
    held = db.one("SELECT * FROM army_claims WHERE key=?", (key,))
    return bool(held and held["soldier_id"] == sid)


def release_claim(key):
    db.execute("DELETE FROM army_claims WHERE key=?", (key,))


def release_claims(sid):
    db.execute("DELETE FROM army_claims WHERE soldier_id=?", (sid,))


def suppress(kind, value, reason):
    value = (value or "").strip().lower()
    if not value or kind not in ("email", "domain", "company"):
        return {"ok": False, "error": "Nothing to block."}
    db.execute("INSERT OR REPLACE INTO army_suppress(kind, value, reason, ts) VALUES(?,?,?,?)",
               (kind, value, reason[:200], _now()))
    if kind == "email":
        db.add_suppression(value, reason)
    return {"ok": True}


def suppressed(email_addr, domain="", company=""):
    if email_addr and db.is_suppressed(email_addr):
        return "unsubscribed or bounced"
    row = db.one("SELECT reason FROM army_suppress WHERE (kind='email' AND value=?) OR (kind='domain' AND value=?) "
                 "OR (kind='company' AND value=?)",
                 ((email_addr or "").lower(), (domain or "").lower(), (company or "").lower()))
    return row["reason"] if row else ""


def _company_contacted(domain, lead_id):
    if not domain:
        return False
    return bool(db.one("SELECT id FROM leads WHERE domain=? AND id<>? AND status NOT IN ('new','') LIMIT 1",
                       (domain, lead_id)))


# ------------------------------------------------------------------ doctrine (which angle wins)
def _bandit_bump(offer_id, angle, field):
    if not offer_id or angle not in ANGLES:
        return
    db.execute("INSERT INTO army_bandit(offer_id, angle, segment, sends, replies, positives, ts) "
               "VALUES(?,?,'',0,0,0,?) ON CONFLICT(offer_id, angle, segment) DO NOTHING", (offer_id, angle, _now()))
    if field == "send":
        db.execute("UPDATE army_bandit SET sends=sends+1, ts=? WHERE offer_id=? AND angle=? AND segment=''",
                   (_now(), offer_id, angle))
    elif field == "reply":
        db.execute("UPDATE army_bandit SET replies=replies+1, ts=? WHERE offer_id=? AND angle=? AND segment=''",
                   (_now(), offer_id, angle))
    elif field == "positive":
        db.execute("UPDATE army_bandit SET replies=replies+1, positives=positives+1, ts=? "
                   "WHERE offer_id=? AND angle=? AND segment=''", (_now(), offer_id, angle))


def best_angle(row):
    if row.get("angle_locked") and row.get("angle") in ANGLES:
        return row["angle"]
    stats = {r["angle"]: r for r in db.rows("SELECT * FROM army_bandit WHERE offer_id=? AND segment=''",
                                            (row["offer_id"],))}
    sends = {a: int((stats.get(a) or {}).get("sends") or 0) for a in ANGLES}
    # Drafts waiting for approval count too, so a batch of drafts rotates the angles.
    for r in db.rows("SELECT angle, COUNT(*) AS n FROM army_drafts WHERE offer_id=? AND status IN "
                     "('pending','approved') GROUP BY angle", (row["offer_id"],)):
        if r["angle"] in sends:
            sends[r["angle"]] += r["n"]
    if min(sends.values()) < BANDIT_MIN:
        return min(ANGLES, key=lambda a: (sends[a], ANGLES.index(a)))
    best, best_theta = ANGLES[0], -1.0
    for a in ANGLES:
        replies = int((stats.get(a) or {}).get("replies") or 0)
        theta = random.betavariate(1 + replies, 1 + max(0, sends[a] - replies))
        if theta > best_theta:
            best, best_theta = a, theta
    return best


def doctrine():
    rows = db.rows("SELECT b.*, o.name AS offer FROM army_bandit b LEFT JOIN offers o ON o.id=b.offer_id "
                   "ORDER BY b.offer_id, b.angle")
    for row in rows:
        row["rate"] = round(row["replies"] / float(row["sends"]), 3) if row["sends"] else 0.0
        row["ready"] = row["sends"] >= BANDIT_MIN
    return rows


# ------------------------------------------------------------------ targets
def next_target(row):
    """The best approved company in this soldier's territory that nobody else holds."""
    expire_claims()
    captain = _rank_at_least(row["xp"], "Captain")
    order = "r.rank DESC" if captain else "(r.soldier_id=?) DESC, r.rank DESC"
    args = (row["offer_id"], row["id"]) + (() if captain else (row["id"],))
    rows = db.rows("SELECT r.* FROM radar_leads r WHERE r.offer_id=? AND r.status='approved' AND r.email<>'' "
                   "AND (r.soldier_id=0 OR r.soldier_id=?) ORDER BY " + order + " LIMIT 60", args)
    for rl in rows:
        lead = db.one("SELECT * FROM leads WHERE id=?", (rl["lead_id"],)) if rl["lead_id"] else None
        if not lead or (lead.get("status") or "new") not in ("new", ""):
            db.execute("UPDATE radar_leads SET status='skipped' WHERE id=?", (rl["id"],))
            continue
        if suppressed(lead["email"], rl["domain"], rl["company"]):
            db.execute("UPDATE radar_leads SET status='skipped' WHERE id=?", (rl["id"],))
            continue
        if _company_contacted(rl["domain"], lead["id"]):
            db.execute("UPDATE radar_leads SET status='skipped' WHERE id=?", (rl["id"],))
            continue
        key = company_key(rl["domain"], rl["company"])
        if not claim(key, row["id"]):
            continue
        db.execute("UPDATE radar_leads SET soldier_id=? WHERE id=?", (row["id"], rl["id"]))
        return {"lead": lead, "rl": rl, "key": key, "kind": "send"}
    return None


def due_followup(row):
    if not settings().get("followups") or not _rank_at_least(row["xp"], FOLLOWUP_RANK):
        return None
    cutoff = _now() - FOLLOWUP_DAYS * 86400
    rows = db.rows(
        "SELECT lead_id, MAX(ts) AS ts, MAX(angle) AS angle FROM army_events WHERE soldier_id=? AND kind='send' "
        "GROUP BY lead_id HAVING MAX(ts)<? ORDER BY MAX(ts) LIMIT 40", (row["id"], cutoff))
    for item in rows:
        stop = db.one("SELECT kind, ts FROM army_events WHERE lead_id=? AND kind IN "
                      "('followup','reply','positive','not_now','unsub','bounce','auto_reply') "
                      "ORDER BY id DESC LIMIT 1", (item["lead_id"],))
        if stop and (stop["kind"] != "auto_reply" or stop["ts"] > _now() - AUTO_REPLY_SHIFT_DAYS * 86400):
            continue
        followups = db.scalar("SELECT COUNT(*) FROM army_events WHERE lead_id=? AND kind='followup'", (item["lead_id"],))
        if followups >= FOLLOWUP_MAX:
            continue
        if db.scalar("SELECT COUNT(*) FROM army_drafts WHERE lead_id=? AND kind='followup' AND status IN "
                     "('pending','approved')", (item["lead_id"],)):
            continue
        lead = db.one("SELECT * FROM leads WHERE id=?", (item["lead_id"],))
        if not lead or lead.get("status") not in ("contacted",) or lead.get("replied_ts"):
            continue
        if suppressed(lead["email"], lead.get("domain")):
            continue
        rl = db.one("SELECT * FROM radar_leads WHERE lead_id=? AND offer_id=?", (lead["id"], row["offer_id"])) or {}
        return {"lead": lead, "rl": rl, "key": "", "kind": "followup", "angle": item.get("angle") or ""}
    return None


# ------------------------------------------------------------------ writing the message
LANG_BY_COUNTRY = {"Slovakia": "Slovak", "Czechia": "Czech", "Germany": "German", "Austria": "German",
                   "Poland": "Polish", "Hungary": "Hungarian", "France": "French", "Spain": "Spanish",
                   "Italy": "Italian"}
LANG_CODES = {"en": "English", "sk": "Slovak", "cs": "Czech", "de": "German", "pl": "Polish", "hu": "Hungarian"}


def _language(row, lead, profile, card):
    choice = (row.get("language") or "auto").lower()
    if choice in LANG_CODES:
        return LANG_CODES[choice]
    if (card or {}).get("language"):
        return card["language"]
    country = profile.get("country") or radar.country_of({"country": lead.get("country", ""),
                                                          "domain": lead.get("domain", "")})
    return LANG_BY_COUNTRY.get(country, "English")


def _sender(box, row):
    s = config.load_settings()
    return {"name": (box.get("from_name") or s.get("sender_name") or "").strip(),
            "company": (s.get("company_name") or "").strip(),
            "persona": (row.get("persona") or "").strip()}


def _template(offer, lead, rl, kind):
    fields = mail.lead_fields(lead)
    fields.setdefault("first_name", "")
    if kind == "followup":
        subject = "Re: " + (fields.get("company") or "a quick follow-up")
        body = ("Hi %s,\n\nfloating this back up in case it got buried - would it be worth a short "
                "look for %s?\n\nThanks" % (fields.get("first_name") or "there", fields.get("company") or "you"))
        return {"subject": subject, "body": body}
    pitch = offers.render_pitch(offer, lead, use_ai=False)
    body = pitch["body"]
    why = (rl or {}).get("why") or ""
    if why and not why.startswith("no signal"):
        body = "Noticed %s - %s.\n\n" % (lead.get("company") or lead.get("domain") or "your site", why) + body
    return {"subject": pitch["subject"], "body": body}


def compose(row, lead, rl, offer, box, kind="send", angle="pain"):
    """Write one message for one company. Returns subject, body, writer."""
    s = settings()
    profile = hunt._j((rl or {}).get("profile"), {}) if rl else {}
    _, card, _ = hunt.card_for(offer["id"])
    sender = _sender(box, row)
    language = _language(row, lead, profile, card)
    link = (offer.get("affiliate_url") or "").strip() if s.get("include_link") else ""
    provider_id = int(s.get("writer_provider_id") or 0) or None
    writer = ""
    out = None
    if llm.get(provider_id):
        company_bits = {
            "company": lead.get("company") or (rl or {}).get("company") or lead.get("domain"),
            "domain": lead.get("domain") or (rl or {}).get("domain"),
            "contact_first_name": lead.get("first_name") or profile.get("first_name") or "",
            "contact_title": lead.get("title") or profile.get("title_person") or "",
            "industry": lead.get("industry") or profile.get("industry") or "",
            "country": profile.get("country") or lead.get("country") or "",
            "city": profile.get("city") or lead.get("city") or "",
            "size": profile.get("size") or lead.get("company_size") or "",
            "site_title": profile.get("title") or "",
            "site_description": profile.get("description") or lead.get("description") or "",
            "site_text_excerpt": (profile.get("text") or "")[:1200],
            "tools_they_run": profile.get("tools") or [],
            "buying_signal": (profile.get("qualify") or {}).get("reason", ""),
            "why_now": (rl or {}).get("why", ""),
            "recent_findings": [e.get("title") for e in (profile.get("evidence") or [])[:4]],
        }
        offer_bits = {"name": offer.get("name"), "description": (offer.get("description") or "")[:900],
                      "problem_it_solves": (card or {}).get("problem", ""),
                      "buyer": (card or {}).get("buyer_role", ""),
                      "competitors": (card or {}).get("competitors", [])[:5],
                      "price": (card or {}).get("price", 0)}
        task = ("a first cold email" if kind == "send" else
                "a short follow-up (2-3 sentences) to an email they did not answer; refer back to it lightly")
        rules = [
            "Write %s in %s." % (task, language),
            "Angle: %s - %s" % (angle, ANGLE_HELP.get(angle, "")),
            "Use ONLY facts from the company profile; never invent numbers, clients or events.",
            "One specific observation about their business in the first line, from the profile.",
            "Max 110 words for a first email, 60 for a follow-up. Plain text, no bullet points, no hype, "
            "no exclamation marks, no 'I hope this finds you well'.",
            "End with one easy yes/no question.",
            "Subject: under 7 words, lower-key, no clickbait, no emojis.",
            "Greet the contact by first name if given, else a neutral greeting in %s." % language,
            "Do not include a signature, links or an unsubscribe line - they are added after.",
            'Answer with JSON only: {"subject": "...", "body": "..."}',
        ]
        system = ("You write short, specific B2B emails for %s%s. %s" %
                  (sender["name"] or "the sender", (" at " + sender["company"]) if sender["company"] else "",
                   ("Voice: " + sender["persona"]) if sender["persona"] else "Voice: plain, friendly, direct."))
        prompt = ("THE OFFER:\n%s\n\nTHE COMPANY:\n%s\n\nRULES:\n- %s" %
                  (json.dumps(offer_bits, ensure_ascii=False, indent=1),
                   json.dumps(company_bits, ensure_ascii=False, indent=1), "\n- ".join(rules)))
        try:
            text = llm.chat([{"role": "user", "content": prompt}], provider_id=provider_id,
                            max_tokens=500, temperature=0.6, system=system)
            match = re.search(r"\{.*\}", text, re.S)
            data = json.loads(match.group(0)) if match else {}
            if str(data.get("body") or "").strip():
                out = {"subject": str(data.get("subject") or "").strip()[:150],
                       "body": str(data["body"]).strip()[:3000]}
                p = llm.get(provider_id) or {}
                writer = "%s · %s" % (p.get("name", "LLM"), p.get("model", ""))
        except (llm.LLMError, ValueError, TypeError) as exc:
            writer = "template (AI failed: %s)" % str(exc)[:120]
    if not out:
        out = _template(offer, lead, rl, kind)
        writer = writer or "template"
        link = ""                                   # render_pitch already added link + disclosure
    if not out["subject"]:
        out["subject"] = (lead.get("company") or "quick question")[:60]
    if kind == "followup" and not out["subject"].lower().startswith("re:"):
        prior = db.one("SELECT subject FROM sends WHERE lead_id=? AND status='sent' ORDER BY id DESC LIMIT 1",
                       (lead["id"],))
        if prior and prior["subject"]:
            out["subject"] = "Re: " + prior["subject"]
    body = out["body"]
    if link and link not in body:
        body = body.rstrip() + "\n\n" + link
        disclosure = offers.disclosure_for(offer)
        if disclosure and disclosure not in body:
            body = body.rstrip() + "\n\n--\n" + disclosure
    return {"subject": out["subject"], "body": body, "writer": writer, "language": language}


# ------------------------------------------------------------------ sending
def send_message(row, lead, subject, body, kind="send", angle="", rl=None, box=None):
    """Send one email from the soldier's mailbox and book it everywhere it counts."""
    s = pipeline.settings()
    box = box or pipeline.mailbox_for(row["mailbox_id"])
    if not box:
        return {"ok": False, "error": "%s has no mailbox to send from." % row["name"]}
    to = (lead.get("email") or "").strip()
    if not mail.valid_email(to):
        return {"ok": False, "error": "%s has no valid address." % (lead.get("company") or "That lead")}
    why = suppressed(to, lead.get("domain"), lead.get("company"))
    if why:
        return {"ok": False, "error": "Blocked: " + why}
    token = secrets.token_hex(8)
    pixel, unsub = pipeline._tracking_urls(s, token)
    signature = (box.get("signature") or "").strip()
    if signature and signature not in body:
        body = body.rstrip() + "\n\n" + signature
    if unsub:
        body = body.rstrip() + "\n\n--\nUnsubscribe: %s" % unsub
    html = mail.plain_to_html(body, track_url=pixel)
    frm = (box.get("from_email") or "").strip()
    msg_id = email.utils.make_msgid(domain=frm.split("@")[-1] if "@" in frm else None)
    earlier = None
    if kind == "followup":
        earlier = db.one("SELECT message_id FROM sends WHERE lead_id=? AND status='sent' ORDER BY id DESC LIMIT 1",
                         (lead["id"],))
    send_id = db.insert("sends", {"enrollment_id": 0, "step_id": 0, "lead_id": lead["id"], "mailbox_id": box["id"],
                                  "to_email": to, "subject": subject, "body": body, "message_id": msg_id,
                                  "token": token, "status": "sending", "sent_ts": _now(), "error": "",
                                  "attempts": 1})
    ok, detail = mail.smtp_send(box, to, subject, body, html, unsub_mailto=frm, message_id=msg_id,
                                unsub_url=unsub or "", in_reply_to=(earlier or {}).get("message_id") or "")
    ts = _now()
    if not ok:
        db.update("sends", send_id, {"status": "unknown" if detail.startswith("Sending failed") else "failed",
                                     "error": detail[:500], "sent_ts": ts})
        event(row["id"], "send_failed", lead_id=lead["id"], offer_id=row["offer_id"], detail=detail)
        return {"ok": False, "error": detail}
    db.update("sends", send_id, {"status": "sent", "message_id": detail or msg_id, "sent_ts": ts})
    db.execute("UPDATE mailboxes SET last_send_ts=? WHERE id=?", (ts, box["id"]))
    db.execute("UPDATE leads SET status=CASE WHEN status='new' THEN 'contacted' ELSE status END, "
               "last_contact_ts=?, updated_ts=? WHERE id=?", (ts, ts, lead["id"]))
    if rl and rl.get("id"):
        db.execute("UPDATE radar_leads SET status='sent', sent_ts=?, soldier_id=? WHERE id=?", (ts, row["id"], rl["id"]))
    event(row["id"], kind, lead_id=lead["id"], offer_id=row["offer_id"], angle=angle, send_id=send_id,
          detail="%s · %s" % (to, subject[:80]))
    if kind == "send":
        _bandit_bump(row["offer_id"], angle, "send")
    add_xp(row["id"], XP_SEND, "sent to " + to, lead_id=lead["id"])
    fresh = soldier(row["id"])
    if fresh and fresh["state"] == "training":
        sent_total = db.scalar("SELECT COUNT(*) FROM army_events WHERE soldier_id=? AND kind IN ('send','followup')",
                               (row["id"],))
        if sent_total >= TRAINING_GRADUATE and health(row["mailbox_id"])["status"] != "benched":
            db.update("soldiers", row["id"], {"state": "active", "updated_ts": ts})
            event(row["id"], "graduated", detail="out of training after %d sends" % sent_total)
    return {"ok": True, "send_id": send_id, "to": to}


def _ready(row):
    if row["state"] == "paused":
        return None, "%s is paused." % row["name"]
    if row["state"] == "benched":
        return None, "%s is benched - its mailbox bounced too much." % row["name"]
    box = pipeline.mailbox_for(row["mailbox_id"])
    if not box:
        return None, "%s has no mailbox." % row["name"]
    if not row["offer_id"] or not offers.get(row["offer_id"]):
        return None, "%s has no offer - give it one." % row["name"]
    return box, ""


def turn(sid, force=False, send_direct=None):
    """One soldier picks its next company, writes, and queues (or sends) the message."""
    row = soldier(sid)
    if not row:
        return {"ok": False, "error": "That soldier is gone."}
    box, why = _ready(row)
    if not box:
        return {"ok": False, "error": why}
    offer = offers.get(row["offer_id"])
    en = energy(row, box)
    if en["left"] <= 0:
        return {"ok": False, "error": "%s is out of energy today (%d sent, %d queued of %d)." % (
            row["name"], en["used"], en["queued"], en["limit"])}
    s = settings()
    review = s.get("review_first", True) if send_direct is None else not send_direct
    if not review and not force:
        ps = pipeline.settings()
        ok, reason = pipeline.within_window(ps)
        if not ok:
            return {"ok": False, "error": reason}
        ready, wait = pipeline.gap_ready(box, ps)
        if not ready:
            return {"ok": False, "error": "Waiting %ss for the send gap." % wait}
    target = due_followup(row) or next_target(row)
    if not target:
        return {"ok": False, "error": "%s has nothing left in its territory - approve more companies on the Radar."
                % row["name"], "empty": True}
    lead, rl, kind = target["lead"], target.get("rl") or {}, target["kind"]
    angle = target.get("angle") or best_angle(row)
    msg = compose(row, lead, rl, offer, box, kind=kind, angle=angle)
    if review:
        draft_id = db.insert("army_drafts", {
            "soldier_id": row["id"], "lead_id": lead["id"], "radar_lead_id": rl.get("id", 0),
            "offer_id": row["offer_id"], "kind": kind, "angle": angle, "to_email": lead["email"],
            "subject": msg["subject"], "body": msg["body"], "writer": msg["writer"], "status": "pending",
            "created_ts": _now()})
        if rl.get("id"):
            db.execute("UPDATE radar_leads SET status='drafted' WHERE id=?", (rl["id"],))
        event(row["id"], "drafted", lead_id=lead["id"], offer_id=row["offer_id"], angle=angle,
              detail="%s · %s" % (lead["email"], msg["subject"][:80]))
        return {"ok": True, "drafted": draft_id, "soldier": row["name"], "to": lead["email"],
                "company": lead.get("company"), "subject": msg["subject"], "writer": msg["writer"], "kind": kind}
    res = send_message(row, lead, msg["subject"], msg["body"], kind=kind, angle=angle, rl=rl, box=box)
    if not res.get("ok") and target.get("key"):
        release_claim(target["key"])
        if rl.get("id"):
            db.execute("UPDATE radar_leads SET status='approved' WHERE id=?", (rl["id"],))
    res.update({"soldier": row["name"], "company": lead.get("company"), "subject": msg["subject"],
                "writer": msg["writer"], "kind": kind})
    return res


# ------------------------------------------------------------------ drafts (review first)
def drafts(status="pending", limit=100):
    where = "d.status=?" if status != "all" else "1=1"
    args = (status,) if status != "all" else ()
    return db.rows("SELECT d.*, s.name AS soldier_name, l.company, l.domain, o.name AS offer_name, "
                   "r.why, r.rank FROM army_drafts d LEFT JOIN soldiers s ON s.id=d.soldier_id "
                   "LEFT JOIN leads l ON l.id=d.lead_id LEFT JOIN offers o ON o.id=d.offer_id "
                   "LEFT JOIN radar_leads r ON r.id=d.radar_lead_id WHERE " + where +
                   " ORDER BY d.id DESC LIMIT ?", args + (limit,))


def update_draft(did, subject=None, body=None):
    d = db.one("SELECT * FROM army_drafts WHERE id=?", (did,))
    if not d or d["status"] not in ("pending", "approved"):
        return {"ok": False, "error": "That draft is no longer waiting."}
    data = {}
    if subject is not None:
        data["subject"] = subject.strip()[:200]
    if body is not None:
        data["body"] = body.strip()[:6000]
    if data:
        db.update("army_drafts", did, data)
    return {"ok": True}


def approve_drafts(ids=None, all_pending=False, send_now=False):
    if all_pending:
        rows = db.rows("SELECT id FROM army_drafts WHERE status='pending'")
        ids = [r["id"] for r in rows]
    ids = [int(i) for i in ids or []]
    db.execute("UPDATE army_drafts SET status='approved' WHERE status='pending' AND id IN (%s)" %
               ",".join("?" * len(ids)), ids) if ids else None
    out = {"ok": True, "approved": len(ids), "sent": 0, "errors": []}
    if send_now:
        for did in ids:
            res = send_draft(did)
            if res.get("ok"):
                out["sent"] += 1
            else:
                out["errors"].append(res.get("error"))
    return out


def send_draft(did):
    d = db.one("SELECT * FROM army_drafts WHERE id=?", (did,))
    if not d or d["status"] not in ("approved", "pending"):
        return {"ok": False, "error": "That draft is no longer waiting."}
    row = soldier(d["soldier_id"])
    lead = db.one("SELECT * FROM leads WHERE id=?", (d["lead_id"],))
    if not row or not lead:
        db.update("army_drafts", did, {"status": "failed", "error": "soldier or lead is gone"})
        return {"ok": False, "error": "The soldier or the lead is gone."}
    if d["kind"] == "send" and (lead.get("status") or "new") not in ("new", ""):
        db.update("army_drafts", did, {"status": "rejected", "error": "lead already " + lead["status"]})
        return {"ok": False, "error": "%s is already %s." % (lead.get("company") or lead["email"], lead["status"])}
    rl = db.one("SELECT * FROM radar_leads WHERE id=?", (d["radar_lead_id"],)) if d["radar_lead_id"] else None
    res = send_message(row, lead, d["subject"], d["body"], kind=d["kind"], angle=d["angle"], rl=rl)
    if res.get("ok"):
        db.update("army_drafts", did, {"status": "sent", "sent_ts": _now()})
    else:
        db.update("army_drafts", did, {"status": "failed", "error": res.get("error", "")[:300]})
    return res


def reject_draft(did, never=False):
    d = db.one("SELECT * FROM army_drafts WHERE id=?", (did,))
    if not d:
        return {"ok": False, "error": "That draft is gone."}
    db.update("army_drafts", did, {"status": "rejected"})
    lead = db.one("SELECT * FROM leads WHERE id=?", (d["lead_id"],)) or {}
    if d["radar_lead_id"]:
        db.execute("UPDATE radar_leads SET status='skipped' WHERE id=?", (d["radar_lead_id"],))
    release_claim(company_key(lead.get("domain"), lead.get("company")))
    if never and lead.get("domain"):
        suppress("domain", lead["domain"], "rejected in the Army")
    return {"ok": True}


def rewrite_draft(did, angle=""):
    d = db.one("SELECT * FROM army_drafts WHERE id=?", (did,))
    if not d or d["status"] not in ("pending", "approved"):
        return {"ok": False, "error": "That draft is no longer waiting."}
    row = soldier(d["soldier_id"])
    lead = db.one("SELECT * FROM leads WHERE id=?", (d["lead_id"],))
    offer = offers.get(d["offer_id"])
    if not (row and lead and offer):
        return {"ok": False, "error": "The soldier, lead or offer is gone."}
    rl = db.one("SELECT * FROM radar_leads WHERE id=?", (d["radar_lead_id"],)) if d["radar_lead_id"] else None
    angle = angle if angle in ANGLES else d["angle"]
    msg = compose(row, lead, rl, offer, pipeline.mailbox_for(row["mailbox_id"]) or {}, kind=d["kind"], angle=angle)
    db.update("army_drafts", did, {"subject": msg["subject"], "body": msg["body"], "writer": msg["writer"],
                                   "angle": angle, "status": "pending"})
    return {"ok": True, "subject": msg["subject"], "body": msg["body"], "writer": msg["writer"]}


# ------------------------------------------------------------------ the worker
def tick(force=False):
    """Called by the app's worker: send approved drafts, then let soldiers take their turns."""
    s = settings()
    now = _now()
    last = int(hunt.kv_get("army_last_tick", "0") or 0)
    if not force and now - last < int(s.get("tick_minutes") or 5) * 60:
        return {"ok": True, "action": "wait"}
    if not TICK_LOCK.acquire(blocking=False):
        return {"ok": True, "action": "busy"}
    try:
        hunt.kv_set("army_last_tick", now)
        result = {"ok": True, "sent": 0, "drafted": 0, "notes": []}
        ps = pipeline.settings()
        in_window, why = pipeline.within_window(ps)
        # 1 approved drafts go out one at a time, inside the window and the gap
        if in_window and ps.get("sending", {}).get("enabled", True):
            for d in db.rows("SELECT * FROM army_drafts WHERE status='approved' ORDER BY id LIMIT 20"):
                row = soldier(d["soldier_id"])
                box = pipeline.mailbox_for(row["mailbox_id"]) if row else None
                if not box or row["state"] in ("paused", "benched"):
                    continue
                if pipeline.sent_today(box["id"]) >= int(box.get("daily_limit") or 30):
                    continue
                ready, _ = pipeline.gap_ready(box, ps)
                if not ready:
                    continue
                with pipeline.SEND_LOCK:
                    res = send_draft(d["id"])
                if res.get("ok"):
                    result["sent"] += 1
                    break
        elif not in_window:
            result["notes"].append(why)
        # 2 soldiers on their own
        if not s.get("auto"):
            result["notes"].append("Autonomy is off - soldiers only move when you press Turn.")
            return result
        rows = db.rows("SELECT * FROM soldiers WHERE state IN ('training','active') AND offer_id>0 ORDER BY id")
        if not rows:
            return result
        start = int(hunt.kv_get("army_rr", "0") or 0)
        rows = rows[start % len(rows):] + rows[:start % len(rows)]
        hunt.kv_set("army_rr", start + 1)
        budget = int(s.get("drafts_per_tick") or 3) if s.get("review_first", True) else 1
        for row in rows:
            if budget <= 0:
                break
            if not s.get("review_first", True) and not in_window:
                break
            res = turn(row["id"])
            if res.get("ok"):
                budget -= 1
                if res.get("drafted"):
                    result["drafted"] += 1
                else:
                    result["sent"] += 1
        return result
    except Exception as exc:
        config.log("army tick failed: %s\n%s" % (exc, traceback.format_exc()))
        return {"ok": False, "error": str(exc)}
    finally:
        TICK_LOCK.release()


# ------------------------------------------------------------------ replies
def _word_in(text, word):
    return bool(re.search(r"(?<![a-z0-9áäčďéěíĺľňóôŕšťúůýž])" + re.escape(word) +
                          r"(?![a-z0-9áäčďéěíĺľňóôŕšťúůýž])", text))


def classify(text, hint=""):
    """One of positive / not_now / unsub / bounce / auto_reply, or '' for a plain reply."""
    if hint == "bounce":
        return "bounce"
    low = (text or "").lower()
    for cls in ("unsub", "bounce", "auto_reply", "not_now", "positive"):
        if any(stem in low for stem in REPLY_STEMS[cls]):
            return cls
        if any(_word_in(low, w) for w in REPLY_WORDS.get(cls, [])):
            return cls
    return ""


def _soldier_for_lead(lead_id, mailbox_id):
    if lead_id:
        ev = db.one("SELECT soldier_id, angle, offer_id FROM army_events WHERE lead_id=? AND kind IN ('send','followup') "
                    "ORDER BY id DESC LIMIT 1", (lead_id,))
        if ev and ev["soldier_id"]:
            return soldier(ev["soldier_id"]), ev.get("angle") or ""
    if mailbox_id:
        return db.one("SELECT * FROM soldiers WHERE mailbox_id=?", (mailbox_id,)), ""
    return None, ""


def apply_reply(reply, cls):
    now = _now()
    lead = db.one("SELECT * FROM leads WHERE id=?", (reply["lead_id"],)) if reply.get("lead_id") else None
    row, angle = _soldier_for_lead(reply.get("lead_id"), reply.get("mailbox_id"))
    sid = row["id"] if row else 0
    offer_id = row["offer_id"] if row else 0
    db.update("replies", reply["id"], {"class": cls or "reply", "classified_ts": now, "soldier_id": sid})
    lid = lead["id"] if lead else 0
    if cls == "positive":
        if lead:
            db.update("leads", lid, {"status": "interested", "updated_ts": now})
        if sid:
            event(sid, "positive", lead_id=lid, offer_id=offer_id, angle=angle, detail=reply.get("from_email", ""))
            add_xp(sid, XP_POSITIVE, "positive reply", lead_id=lid)
        _bandit_bump(offer_id, angle, "positive")
        check_mission()
    elif cls == "not_now":
        if lead:
            db.update("leads", lid, {"status": "replied", "updated_ts": now,
                                     "notes": ((lead.get("notes") or "") + "\nnot now - try again after " +
                                               time.strftime("%Y-%m-%d", time.localtime(now + NOT_NOW_DAYS * 86400))).strip()[:2000]})
        if sid:
            event(sid, "not_now", lead_id=lid, offer_id=offer_id, angle=angle, detail=reply.get("from_email", ""))
            add_xp(sid, XP_REPLY, "not now", lead_id=lid)
        _bandit_bump(offer_id, angle, "reply")
    elif cls == "unsub":
        addr = reply.get("from_email") or (lead or {}).get("email", "")
        if addr:
            suppress("email", addr, "unsubscribed")
        if lead:
            db.update("leads", lid, {"status": "unsubscribed", "updated_ts": now})
        if sid:
            event(sid, "unsub", lead_id=lid, offer_id=offer_id, angle=angle, detail=addr)
    elif cls == "bounce":
        addr = (lead or {}).get("email") or reply.get("from_email", "")
        if addr:
            suppress("email", addr, "bounced")
        if lead:
            db.update("leads", lid, {"status": "bounced", "updated_ts": now})
        if sid:
            event(sid, "bounce", lead_id=lid, offer_id=offer_id, detail=addr)
            check_bench(sid)
    elif cls == "auto_reply":
        # Not a person: undo the 'replied' the inbox sync set, so a follow-up can still go out.
        if lead and lead.get("status") == "replied":
            db.update("leads", lid, {"status": "contacted", "replied_ts": 0, "updated_ts": now})
        if sid:
            event(sid, "auto_reply", lead_id=lid, offer_id=offer_id, detail=reply.get("from_email", ""))
    else:
        if sid:
            event(sid, "reply", lead_id=lid, offer_id=offer_id, angle=angle, detail=reply.get("from_email", ""))
            add_xp(sid, XP_REPLY, "reply", lead_id=lid)
        _bandit_bump(offer_id, angle, "reply")
    return cls or "reply"


def sort_replies(limit=40, fetch_snippets=True):
    """Classify every reply that has no class yet. Called after each inbox sync."""
    rows = db.rows("SELECT * FROM replies WHERE COALESCE(class,'')='' ORDER BY id LIMIT ?", (limit,))
    counts = {}
    fetched = 0
    for reply in rows:
        if fetch_snippets and not reply.get("snippet") and not reply.get("is_bounce") and fetched < 10 \
                and reply.get("message_id") and reply.get("mailbox_id"):
            box = pipeline.mailbox_for(reply["mailbox_id"])
            if box and (box.get("imap_host") or "").strip():
                try:
                    snippet = mail.fetch_snippet(box, reply["message_id"])
                except Exception:
                    snippet = ""
                fetched += 1
                if snippet:
                    reply["snippet"] = snippet
                    db.update("replies", reply["id"], {"snippet": snippet})
        text = (reply.get("subject") or "") + "\n" + (reply.get("snippet") or "")
        cls = classify(text, "bounce" if reply.get("is_bounce") else "")
        final = apply_reply(reply, cls)
        counts[final] = counts.get(final, 0) + 1
    return {"ok": True, "sorted": len(rows), "counts": counts}


def set_reply_class(reply_id, cls):
    reply = db.one("SELECT * FROM replies WHERE id=?", (reply_id,))
    if not reply:
        return {"ok": False, "error": "That reply is gone."}
    if cls not in REPLY_CLASSES + ("reply",):
        return {"ok": False, "error": "Unknown class."}
    final = apply_reply(reply, "" if cls == "reply" else cls)
    return {"ok": True, "class": final}


# ------------------------------------------------------------------ mission and the board
def mission():
    start = _week_start()
    positives = db.rows("SELECT soldier_id, COUNT(*) AS n FROM army_events WHERE kind='positive' AND ts>=? "
                        "GROUP BY soldier_id ORDER BY n DESC", (start,))
    total = sum(r["n"] for r in positives)
    leader = positives[0] if positives else None
    leader_row = soldier(leader["soldier_id"]) if leader else None
    week_key = "army_mission_%d" % start
    return {"title": "Three positive replies this week", "target": MISSION_TARGET, "progress": total,
            "xp": MISSION_XP, "done": total >= MISSION_TARGET, "paid": bool(hunt.kv_get(week_key, "")),
            "leader": leader_row["name"] if leader_row else "", "leader_count": leader["n"] if leader else 0,
            "week_start": start}


def check_mission():
    m = mission()
    key = "army_mission_%d" % m["week_start"]
    if m["done"] and not hunt.kv_get(key, ""):
        hunt.kv_set(key, "1")
        leader = db.one("SELECT soldier_id FROM army_events WHERE kind='positive' AND ts>=? GROUP BY soldier_id "
                        "ORDER BY COUNT(*) DESC LIMIT 1", (m["week_start"],))
        if leader and leader["soldier_id"]:
            add_xp(leader["soldier_id"], MISSION_XP, "weekly mission")
            db.event("army", "Weekly mission done - %d XP to %s" % (MISSION_XP, m["leader"]))


def leaderboard():
    start = _week_start()
    rows = db.rows("SELECT s.id, s.name, s.xp, "
                   "SUM(CASE WHEN e.kind IN ('send','followup') THEN 1 ELSE 0 END) AS sent, "
                   "SUM(CASE WHEN e.kind IN ('reply','not_now','positive') THEN 1 ELSE 0 END) AS replies, "
                   "SUM(CASE WHEN e.kind='positive' THEN 1 ELSE 0 END) AS positives, "
                   "COALESCE(SUM(e.xp), 0) AS week_xp "
                   "FROM soldiers s LEFT JOIN army_events e ON e.soldier_id=s.id AND e.ts>=? "
                   "GROUP BY s.id ORDER BY week_xp DESC, positives DESC, s.xp DESC", (start,))
    for r in rows:
        r["rank"] = rank_for(r["xp"])["name"]
    return rows


def state():
    rows = db.rows("SELECT s.*, o.name AS offer_name, m.name AS mailbox_name, m.from_email, m.daily_limit "
                   "FROM soldiers s LEFT JOIN offers o ON o.id=s.offer_id LEFT JOIN mailboxes m ON m.id=s.mailbox_id "
                   "ORDER BY s.id")
    for r in rows:
        en = energy(r)
        r["energy"] = en
        r["rank"] = en["rank"]
        r["health"] = en["health"]
        r["totals"] = db.one("SELECT SUM(CASE WHEN kind IN ('send','followup') THEN 1 ELSE 0 END) AS sent, "
                             "SUM(CASE WHEN kind IN ('reply','not_now','positive') THEN 1 ELSE 0 END) AS replies, "
                             "SUM(CASE WHEN kind='positive' THEN 1 ELSE 0 END) AS positives "
                             "FROM army_events WHERE soldier_id=?", (r["id"],))
        r["territory"] = db.scalar("SELECT COUNT(*) FROM radar_leads WHERE offer_id=? AND status='approved' "
                                   "AND (soldier_id=0 OR soldier_id=?)", (r["offer_id"], r["id"]))
        r["next_angle"] = r["angle"] if r.get("angle_locked") else ""
    boxes = [{"id": b["id"], "name": b["name"], "from_email": b["from_email"], "daily_limit": b["daily_limit"],
              "active": b["active"]} for b in db.rows("SELECT * FROM mailboxes ORDER BY id")]
    used = {r["mailbox_id"] for r in rows}
    offer_rows = []
    for o in offers.list_offers():
        offer_rows.append({"id": o["id"], "name": o["name"], "active": o["active"],
                           "affiliate_url": o.get("affiliate_url", ""), "category": o.get("category", ""),
                           "territory": db.scalar("SELECT COUNT(*) FROM radar_leads WHERE offer_id=? AND status='approved'",
                                                  (o["id"],)),
                           "soldiers": sum(1 for r in rows if r["offer_id"] == o["id"])})
    events = db.rows("SELECT e.*, s.name AS soldier_name FROM army_events e LEFT JOIN soldiers s ON s.id=e.soldier_id "
                     "WHERE e.kind<>'xp' ORDER BY e.id DESC LIMIT 40")
    return {"ok": True, "settings": settings(), "soldiers": rows, "mailboxes": boxes,
            "free_mailboxes": [b for b in boxes if b["id"] not in used and b["active"]],
            "offers": offer_rows, "ranks": RANKS, "angles": list(ANGLES), "angle_help": ANGLE_HELP,
            "mission": mission(), "leaderboard": leaderboard(), "doctrine": doctrine(),
            "bandit_min": BANDIT_MIN, "drafts_pending": db.scalar("SELECT COUNT(*) FROM army_drafts WHERE status='pending'"),
            "drafts_approved": db.scalar("SELECT COUNT(*) FROM army_drafts WHERE status='approved'"),
            "events": events, "writer": writer_status(), "states": list(STATES),
            "reply_classes": list(REPLY_CLASSES),
            "unsorted_replies": db.scalar("SELECT COUNT(*) FROM replies WHERE COALESCE(class,'')=''")}
