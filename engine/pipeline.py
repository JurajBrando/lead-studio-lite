"""The SDR engine: work out who is due, send, pace, and react to replies.

One process, one queue. Everything is idempotent so a crash mid-send never
double-sends: a send row is written before the SMTP call and re-used if it
already exists for that (enrollment, step).
"""
import email.utils
import random
import re
import secrets
import threading
import time

from . import config, db, mail

MAX_PER_TICK = 1          # the worker sends at most one email per tick
MAX_SEND_ATTEMPTS = 3     # then the enrollment is paused instead of hammering the server
SEND_RETRY_SECONDS = 900  # first retry after 15 minutes, second after 30
BOUNCE_RX = re.compile(r"(mailer-daemon|postmaster|mail delivery (subsystem|system))", re.I)
# A no-reply address is an automatic acknowledgement ("we received your message"), not a
# delivery failure and not an answer from a person. It used to count as a bounce, which
# suppressed a working address for good.
NOREPLY_RX = re.compile(r"(^|[<\s])(no-?reply|do-?not-?reply|donotreply)[^@\s]*@", re.I)
BOUNCE_RECIPIENT_RX = re.compile(
    r"(?:Final-Recipient|Original-Recipient|X-Failed-Recipients)\s*:\s*(?:rfc822\s*;)?\s*"
    r"<?([^\s<>;,]+@[^\s<>;,]+)>?", re.I)
ADDRESS_RX = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# Bounces that could not be matched to a lead are looked at once, not on every sync.
_BOUNCES_CHECKED = set()
# The address in List-Unsubscribe is a mailto with this exact subject, and people also just
# write "unsubscribe". Honour both instead of filing them as replies.
UNSUB_SUBJECT_RX = re.compile(r"^\s*(unsubscribe|opt[- ]?out|remove me|take me off)\b", re.I)

# Only one send may run at a time inside this process: the 20 s worker and the
# "Send next now" button both call tick(), and without this they could pick the same
# enrollment and mail the same person twice.
SEND_LOCK = threading.Lock()


# ------------------------------------------------------------------ helpers
def settings():
    return config.load_settings()


def active_mailboxes():
    return db.rows("SELECT * FROM mailboxes WHERE active=1 ORDER BY id")


def mailbox_for(box_id):
    if box_id:
        return db.one("SELECT * FROM mailboxes WHERE id=?", (box_id,))
    boxes = mailbox_candidates()
    return boxes[0] if boxes else None


def mailbox_candidates():
    """Every active mailbox, best next first.

    With more than one mailbox the queue spreads: a mailbox that still has room under its
    daily limit comes before one that does not, and among equals the one that has been quiet
    longest goes first. That is what keeps two or five inboxes working at once instead of
    everything going out of the first one until it caps.
    """
    boxes = active_mailboxes()
    if not boxes:
        return []
    def room(box):
        return int(box.get("daily_limit") or 30) - sent_today(box["id"])
    with_room = [box for box in boxes if room(box) > 0]
    pool = with_room or boxes
    return sorted(pool, key=lambda box: (int(box.get("last_send_ts") or 0), box["id"]))


def sent_today(box_id):
    day_start = int(time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1)))
    return db.scalar("SELECT COUNT(*) FROM sends WHERE mailbox_id=? AND status='sent' AND sent_ts>=?",
                     (box_id, day_start))


def sent_today_map():
    """{mailbox id: messages sent today} in one query - for pages that list every mailbox."""
    day_start = int(time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1)))
    return {row["mailbox_id"]: row["n"] for row in db.rows(
        "SELECT mailbox_id, COUNT(*) AS n FROM sends WHERE status='sent' AND sent_ts>=? "
        "GROUP BY mailbox_id", (day_start,))}


def _lead_counts():
    by_status = {r["status"]: r["n"] for r in db.rows("SELECT status, COUNT(*) n FROM leads GROUP BY status")}
    return {"by_status": by_status, "total": sum(by_status.values()),
            "unverified": db.scalar("SELECT COUNT(*) FROM leads WHERE verify_status='unverified'")}


def lead_counts():
    """Totals over the leads table. Three passes over millions of rows take seconds, and the
    dashboard asks every 30 s - `db.cached` keeps a slow answer for two minutes."""
    return db.cached("lead_counts", _lead_counts)


def within_window(s):
    sending = s.get("sending", {})
    lt = time.localtime()
    if sending.get("weekdays_only", True) and lt.tm_wday >= 5:
        return False, "It's the weekend - sending is paused until Monday."
    hour = lt.tm_hour
    start = int(sending.get("window_start", 8))
    end = int(sending.get("window_end", 18))
    if start <= end:
        ok = start <= hour < end
    else:                                   # window crosses midnight
        ok = hour >= start or hour < end
    if not ok:
        return False, "Outside the sending window (%02d:00-%02d:00)." % (start, end)
    return True, ""


def gap_ready(box, s):
    gap = int(s.get("sending", {}).get("gap_seconds", 45))
    jitter = max(0, int(s.get("sending", {}).get("jitter_seconds", 30)))
    last = int(box.get("last_send_ts") or 0)
    if not last:
        return True, 0
    wait = max(0, (last + gap) - int(time.time()))
    if wait <= 0:
        return True, 0
    # Randomise the last part of the gap: a machine-perfect cadence is one of the
    # patterns spam filters look for. Recomputed from last_send_ts every time, so the
    # extra never stacks up - the real wait stays within [gap, gap + jitter].
    if jitter:
        wait += random.randint(0, jitter)
    return False, wait


def previous_send(en):
    """The last message this enrollment actually delivered, or None."""
    if int(en.get("step_no") or 0) <= 0:
        return None
    return db.one("SELECT * FROM sends WHERE enrollment_id=? AND status='sent' "
                  "ORDER BY id DESC LIMIT 1", (en["id"],))


def boxes_for(en):
    """The mailboxes that may send this enrollment's next step, best first.

    A follow-up goes out of the mailbox that sent the step before it: the prospect gets one
    conversation with one sender, and the reply lands in the inbox that is being watched for
    it. Only the first step (or a follow-up whose mailbox was removed or switched off) takes
    part in the rotation.
    """
    explicit = en.get("mailbox_id") or 0
    if explicit:
        return [box for box in [mailbox_for(explicit)] if box]
    earlier = previous_send(en)
    if earlier and earlier.get("mailbox_id"):
        box = db.one("SELECT * FROM mailboxes WHERE id=? AND active=1", (earlier["mailbox_id"],))
        if box:
            return [box]
    return mailbox_candidates()


def next_step(sequence_id, after_no):
    return db.one("SELECT * FROM steps WHERE sequence_id=? AND step_no>? ORDER BY step_no LIMIT 1",
                  (sequence_id, after_no))


def step_by_no(sequence_id, no):
    return db.one("SELECT * FROM steps WHERE sequence_id=? AND step_no=?", (sequence_id, no))


def enroll(sequence_id, lead_ids, mailbox_id=0, start_now=True):
    """Enroll leads into a sequence. Skips duplicates, suppressed and already-replied leads."""
    seq = db.one("SELECT * FROM sequences WHERE id=?", (sequence_id,))
    if not seq:
        return {"ok": False, "error": "That sequence no longer exists."}
    first = step_by_no(sequence_id, 1)
    if not first:
        return {"ok": False, "error": "That sequence has no steps yet."}
    ts = db.now()
    added = skipped = 0
    for lead_id in lead_ids:
        lead = db.one("SELECT * FROM leads WHERE id=?", (lead_id,))
        if (not lead or not (lead["email"] or "").strip()
                or db.is_suppressed(lead["email"])
                or lead["status"] in ("unsubscribed", "bounced")):
            skipped += 1
            continue
        if db.one("SELECT id FROM enrollments WHERE sequence_id=? AND lead_id=? AND status='running'",
                  (sequence_id, lead_id)):
            skipped += 1
            continue
        existing = db.one("SELECT id FROM enrollments WHERE sequence_id=? AND lead_id=?", (sequence_id, lead_id))
        if existing:
            db.update("enrollments", existing["id"], {
                "status": "running", "step_no": 0, "started_ts": ts, "finished_ts": 0,
                "next_run_ts": ts + int(first["delay_days"] or 0) * 86400, "last_error": "",
                "mailbox_id": mailbox_id or 0})
            added += 1
            continue
        db.insert("enrollments", {
            "sequence_id": sequence_id, "lead_id": lead_id, "mailbox_id": mailbox_id or 0,
            "status": "running", "step_no": 0,
            "next_run_ts": ts + int(first["delay_days"] or 0) * 86400, "started_ts": ts,
        })
        added += 1
    db.event("enroll", "sequence %s: %d added, %d skipped" % (seq["name"], added, skipped))
    return {"ok": True, "added": added, "skipped": skipped}


def due_enrollments(limit=20):
    return db.rows(
        "SELECT e.*, l.email, l.status AS lead_status FROM enrollments e "
        "JOIN leads l ON l.id=e.lead_id "
        "WHERE e.status='running' AND e.next_run_ts<=? AND l.email<>'' "
        "ORDER BY e.next_run_ts LIMIT ?",
        (int(time.time()), limit))


# ------------------------------------------------------------------ sending
def _tracking_urls(s, token):
    base = (s.get("tracking", {}).get("base_url") or "").strip().rstrip("/")
    if not base or not s.get("tracking", {}).get("enabled", True):
        return None, None
    return ("%s/t/o/%s.gif" % (base, token)), ("%s/t/u/%s" % (base, token))


def send_for_enrollment(en, box=None):
    """Send the next step for one enrollment. Returns a result dict.

    `box` is the mailbox the caller already chose (and checked the cap and the gap for);
    without it the enrollment's own mailbox, or the rotation, decides.
    """
    s = settings()
    seq = db.one("SELECT * FROM sequences WHERE id=?", (en["sequence_id"],))
    lead = db.one("SELECT * FROM leads WHERE id=?", (en["lead_id"],))
    if not seq or not lead:
        db.update("enrollments", en["id"], {"status": "failed", "last_error": "missing sequence or lead"})
        return {"ok": False, "error": "missing data"}

    if db.is_suppressed(lead["email"]):
        db.update("enrollments", en["id"], {"status": "unsubscribed", "finished_ts": db.now()})
        return {"ok": False, "error": "lead is unsubscribed"}
    if lead["status"] in ("bounced", "unsubscribed"):
        db.update("enrollments", en["id"], {"status": lead["status"], "finished_ts": db.now()})
        return {"ok": False, "error": "lead is " + lead["status"]}
    if lead["replied_ts"]:
        db.update("enrollments", en["id"], {"status": "replied", "finished_ts": db.now()})
        return {"ok": False, "error": "lead already replied"}

    step = next_step(en["sequence_id"], int(en["step_no"] or 0))
    if not step:
        db.update("enrollments", en["id"], {"status": "finished", "finished_ts": db.now()})
        return {"ok": False, "error": "sequence finished"}

    if not box:
        options = boxes_for(en)
        box = options[0] if options else None
    if not box:
        db.update("enrollments", en["id"], {"status": "paused", "last_error": "no mailbox"})
        return {"ok": False, "error": "No sending mailbox is configured."}

    fields = mail.lead_fields(lead)
    # Sender-side merge fields come from Settings, never from the lead row.
    fields["sender_name"] = (s.get("sender_name") or box.get("from_name") or "").strip()
    fields["sender_email"] = (box.get("from_email") or "").strip()
    fields["my_company"] = (s.get("company_name") or "").strip()
    subject, miss_s = mail.merge(step["subject"], fields)
    body, miss_b = mail.merge(step["body"], fields)
    hard_missing = [f for f in (miss_s + miss_b) if f not in ("unsubscribe",)]
    if hard_missing:
        reason = "Missing data for merge field(s): %s" % ", ".join(sorted(set(hard_missing)))
        db.update("enrollments", en["id"], {"last_error": reason, "status": "paused"})
        db.event("skip", "enrollment %s: %s" % (en["id"], reason))
        return {"ok": False, "error": reason}

    token = secrets.token_hex(8)
    pixel, unsub = _tracking_urls(s, token)
    signature = (box.get("signature") or "").strip()
    if signature and signature not in body:
        body = body.rstrip() + "\n\n" + signature
    if unsub:
        body = body.rstrip() + "\n\n--\nUnsubscribe: %s" % unsub
    html = mail.plain_to_html(body, track_url=pixel)

    # One row per (enrollment, step), written *before* the SMTP call. If the app dies or a
    # send times out, the claim is already on disk, so the same step can never be sent a
    # second time - the old code wrote the row afterwards and could resent on restart.
    claim = db.one("SELECT * FROM sends WHERE enrollment_id=? AND step_id=? ORDER BY id DESC LIMIT 1",
                   (en["id"], step["id"]))
    if claim and claim["status"] == "sent":
        # Delivered, but the queue never advanced (crash between the two writes).
        return _advance_after_send(en, step, lead, box, subject, db.now())
    if claim and claim["status"] == "unknown":
        db.update("enrollments", en["id"], {
            "status": "paused",
            "last_error": "An earlier attempt may already have been delivered - check the Sent folder."})
        return {"ok": False, "error": "That step needs a manual check before it is sent again."}

    frm = (box.get("from_email") or "").strip()
    msg_id = email.utils.make_msgid(domain=frm.split("@")[-1] if "@" in frm else None)
    ts = db.now()
    attempts = int((claim or {}).get("attempts") or 0) + 1
    record = {"enrollment_id": en["id"], "step_id": step["id"], "lead_id": lead["id"],
              "mailbox_id": box["id"], "to_email": lead["email"], "subject": subject,
              "body": body, "message_id": msg_id, "token": token, "status": "sending",
              "sent_ts": ts, "error": "", "attempts": attempts}
    if claim:
        db.update("sends", claim["id"], record)
        send_id = claim["id"]
    else:
        send_id = db.insert("sends", record)

    # A follow-up answers the message before it, so mail clients show one conversation.
    earlier = previous_send(en)
    ok, detail = mail.smtp_send(box, lead["email"], subject, body, html,
                                unsub_mailto=(box.get("from_email") or ""),
                                message_id=msg_id, unsub_url=unsub or "",
                                in_reply_to=(earlier or {}).get("message_id") or "")
    ts = db.now()
    if ok:
        db.update("sends", send_id, {"status": "sent", "message_id": detail or msg_id,
                                     "sent_ts": ts})
        db.execute("UPDATE mailboxes SET last_send_ts=? WHERE id=?", (ts, box["id"]))
        # Never demote a lead that replied (or bounced) while this message was in flight.
        db.execute("UPDATE leads SET status=CASE WHEN status='new' THEN 'contacted' ELSE status END, "
                   "last_contact_ts=?, updated_ts=? WHERE id=?", (ts, ts, lead["id"]))
        return _advance_after_send(en, step, lead, box, subject, ts)

    # A failure during DATA may still have been delivered by the server: retrying would mail
    # the person twice, so pause and let the user look at the Sent folder instead.
    ambiguous = detail.startswith("Sending failed")
    db.update("sends", send_id, {"status": "unknown" if ambiguous else "failed",
                                 "error": detail[:500], "sent_ts": ts})
    if ambiguous:
        db.update("enrollments", en["id"], {
            "status": "paused",
            "last_error": "The connection dropped mid-send: %s" % detail[:200]})
        db.event("send_unknown", "%s: %s" % (lead["email"], detail[:160]))
        return {"ok": False, "error": detail}

    attempts = int((claim or {}).get("attempts") or 0) + 1
    if attempts >= MAX_SEND_ATTEMPTS:
        db.update("enrollments", en["id"], {
            "status": "paused",
            "last_error": "Gave up after %d attempts: %s" % (attempts, detail[:180])})
        db.event("send_paused", "%s: %s" % (lead["email"], detail[:160]))
    else:
        delay = SEND_RETRY_SECONDS * attempts
        db.update("enrollments", en["id"], {"last_error": detail[:300],
                                           "next_run_ts": ts + delay})
        db.event("send_fail", "%s: %s (retry in %d min)" % (lead["email"], detail[:160],
                                                            delay // 60))
    return {"ok": False, "error": detail}


def _advance_after_send(en, step, lead, box, subject, ts):
    """Move the enrollment to the next step (or finish it) and log the send."""
    nxt = next_step(en["sequence_id"], step["step_no"])
    if nxt:
        db.update("enrollments", en["id"], {
            "step_no": step["step_no"],
            "next_run_ts": ts + max(0, int(nxt["delay_days"] or 0)) * 86400,
            "last_error": ""})
    else:
        db.update("enrollments", en["id"], {"step_no": step["step_no"], "status": "finished",
                                            "finished_ts": ts, "last_error": ""})
    db.event("send", "%s -> %s (%s)" % (box["name"], lead["email"], subject[:60]))
    return {"ok": True, "to": lead["email"], "subject": subject, "mailbox": box["name"],
            "step": step["step_no"]}


def tick(force=False):
    """One worker pass, never overlapping another one. Returns what happened."""
    if not SEND_LOCK.acquire(blocking=False):
        return {"ok": True, "action": "busy",
                "reason": "A send is already in progress."}
    try:
        return _tick(force)
    finally:
        SEND_LOCK.release()


def _tick(force=False):
    s = settings()
    if not force and not s.get("sending", {}).get("enabled", True):
        return {"ok": True, "action": "idle", "reason": "Sending is switched off in Settings."}
    if not force:
        ok, why = within_window(s)
        if not ok:
            return {"ok": True, "action": "idle", "reason": why}
    due = due_enrollments(MAX_PER_TICK)
    if not due:
        return {"ok": True, "action": "idle", "reason": "Nothing is due right now."}
    en = due[0]
    candidates = boxes_for(en)
    if not candidates:
        return {"ok": False, "action": "blocked", "reason": "No active mailbox configured."}
    # Skip a mailbox that is at its cap instead of stalling the queue for an hour - the next
    # one gets the send.
    box = next((one for one in candidates
                if sent_today(one["id"]) < int(one.get("daily_limit") or 30)), None)
    if not box:
        db.update("enrollments", en["id"], {"next_run_ts": int(time.time()) + 3600})
        return {"ok": True, "action": "idle",
                "reason": "Every mailbox reached its daily limit (%d)." % len(candidates)}
    if not force:
        ready, wait = gap_ready(box, s)
        if not ready:
            return {"ok": True, "action": "idle", "reason": "Waiting %ss for the next send gap." % wait}
    res = send_for_enrollment(en, box=box)
    res["action"] = "sent" if res.get("ok") else "blocked"
    return res


# ------------------------------------------------------------- inbox sync
def sync_inbox(box_id=0, days=14):
    """Match new inbox mail against what we sent: replies, bounces, unsubscribes."""
    boxes = [mailbox_for(box_id)] if box_id else active_mailboxes()
    boxes = [b for b in boxes if b]
    result = {"ok": True, "mailboxes": [], "new_replies": 0, "new_bounces": 0,
              "new_unsubscribes": 0, "errors": []}
    for box in boxes:
        if not (box.get("imap_host") or "").strip():
            continue
        items, err = mail.fetch_recent(box, days=days)
        if err:
            result["errors"].append("%s: %s" % (box["name"], err))
            result["mailboxes"].append({"mailbox": box["name"], "error": err})
            continue
        seen = {r["message_id"] for r in db.rows("SELECT message_id FROM replies WHERE message_id<>''")}
        counters = {"replies": 0, "bounces": 0, "unsubscribes": 0}
        for m in items:
            if m["message_id"] and m["message_id"] in seen:
                continue
            lead = None
            for mid in m["in_reply_to"]:
                send = db.one("SELECT * FROM sends WHERE message_id=?", (mid,))
                if send:
                    lead = db.one("SELECT * FROM leads WHERE id=?", (send["lead_id"],))
                    break
            if not lead and m["address"]:
                lead = db.one("SELECT * FROM leads WHERE email=?", (m["address"],))
            is_bounce = bool(BOUNCE_RX.search(m["address"] or "") or
                             BOUNCE_RX.search(m["from"] or "") or
                             mail.AUTOREPLY_RX.search(m["subject"] or "") and "undeliver" in (m["subject"] or "").lower())
            if is_bounce and not lead:
                lead = _bounced_lead(box, m)
            if not lead:
                continue
            if not is_bounce and NOREPLY_RX.search(m["from"] or m["address"] or ""):
                continue                    # an automatic acknowledgement: not a reply
            ts = db.now()
            # An unsubscribe answer (the List-Unsubscribe mailto, Gmail's one-click, or a
            # plain "unsubscribe" reply) is not a sales reply: suppress, never re-enrol.
            if not is_bounce and lead and UNSUB_SUBJECT_RX.search(m["subject"] or ""):
                counters["unsubscribes"] += 1
                db.update("leads", lead["id"], {"status": "unsubscribed", "updated_ts": ts})
                db.add_suppression(lead["email"], "unsubscribe email")
                db.execute("UPDATE enrollments SET status='unsubscribed', finished_ts=? "
                           "WHERE lead_id=? AND status='running'", (ts, lead["id"]))
                db.event("unsubscribe", "%s asked to be removed by email" % lead["email"])
                continue
            reply_id = db.insert("replies", {
                "lead_id": lead["id"] if lead else 0, "mailbox_id": box["id"],
                "from_email": m["address"], "subject": m["subject"], "snippet": "",
                "message_id": m["message_id"], "received_ts": ts,
                "is_bounce": 1 if is_bounce else 0, "seen": 0,
            })
            if not is_bounce and counters["replies"] < 3 and m["message_id"]:
                snippet = mail.fetch_snippet(box, m["message_id"])
                if snippet:
                    db.update("replies", reply_id, {"snippet": snippet})
            if is_bounce:
                counters["bounces"] += 1
                if lead:
                    db.update("leads", lead["id"], {"status": "bounced", "updated_ts": ts})
                    db.add_suppression(lead["email"], "bounce")
                    db.execute("UPDATE enrollments SET status='bounced', finished_ts=? "
                               "WHERE lead_id=? AND status='running'", (ts, lead["id"]))
            else:
                counters["replies"] += 1
                db.update("leads", lead["id"], {"status": "replied", "replied_ts": ts, "updated_ts": ts})
                db.execute("UPDATE enrollments SET status='replied', finished_ts=? "
                           "WHERE lead_id=? AND status='running'", (ts, lead["id"]))
                db.event("reply", "%s replied: %s" % (lead["email"], m["subject"][:60]))
        result["new_replies"] += counters["replies"]
        result["new_bounces"] += counters["bounces"]
        result["new_unsubscribes"] += counters["unsubscribes"]
        result["mailboxes"].append({"mailbox": box["name"], "checked": len(items), **counters})
    if not boxes:
        result["errors"].append("No mailbox with IMAP configured yet.")
    return result


def _bounced_lead(box, m):
    """The lead a delivery report is about, when its headers did not say.

    Looks at the report itself: first for the Message-ID of something we sent, then for the
    address it names. Only a lead this app has actually mailed is accepted, so a stray bounce
    for somebody else's message can never suppress one of ours.
    """
    key = m.get("message_id") or ""
    if not key or key in _BOUNCES_CHECKED:
        return None
    if len(_BOUNCES_CHECKED) > 5000:
        _BOUNCES_CHECKED.clear()
    _BOUNCES_CHECKED.add(key)
    text = mail.fetch_bounce_text(box, key) or ""
    for mid in re.findall(r"<[^<>\s]+@[^<>\s]+>", text)[:40]:
        send = db.one("SELECT lead_id FROM sends WHERE message_id=?", (mid,))
        if send:
            lead = db.one("SELECT * FROM leads WHERE id=?", (send["lead_id"],))
            if lead:
                return lead
    own = (box.get("from_email") or "").strip().lower()
    named = list(m.get("failed_recipients") or []) + BOUNCE_RECIPIENT_RX.findall(text)
    seen = set()
    for address in (named or ADDRESS_RX.findall(text))[:40]:
        address = address.strip().strip(".").lower()
        if not address or address == own or address in seen:
            continue
        seen.add(address)
        lead = db.one("SELECT * FROM leads WHERE email=?", (address,))
        if lead and db.one("SELECT id FROM sends WHERE lead_id=? AND status='sent' LIMIT 1",
                           (lead["id"],)):
            return lead
    return None


def unsubscribe(token):
    send = db.one("SELECT * FROM sends WHERE token=?", (token,))
    if not send:
        return False
    lead = db.one("SELECT * FROM leads WHERE id=?", (send["lead_id"],))
    if not lead:
        return False
    ts = db.now()
    db.update("leads", lead["id"], {"status": "unsubscribed", "updated_ts": ts})
    db.add_suppression(lead["email"], "unsubscribe link")
    db.execute("UPDATE enrollments SET status='unsubscribed', finished_ts=? "
               "WHERE lead_id=? AND status='running'", (ts, lead["id"]))
    db.event("unsubscribe", lead["email"])
    return True


def record_open(token):
    send = db.one("SELECT * FROM sends WHERE token=?", (token,))
    if not send:
        return False
    ts = db.now()
    db.execute("UPDATE sends SET opens=opens+1, opened_ts=CASE WHEN opened_ts=0 THEN ? ELSE opened_ts END "
               "WHERE id=?", (ts, send["id"]))
    return True


def click_target(token, url):
    """`url` when it is a link inside the message `token` was sent with, otherwise ""."""
    url = str(url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return ""
    send = db.one("SELECT body FROM sends WHERE token=?", (str(token or ""),))
    if not send or url not in (send.get("body") or ""):
        return ""
    return url


def record_click(token):
    send = db.one("SELECT * FROM sends WHERE token=?", (token,))
    if not send:
        return False
    ts = db.now()
    db.execute("UPDATE sends SET clicks=clicks+1, clicked_ts=CASE WHEN clicked_ts=0 THEN ? ELSE clicked_ts END "
               "WHERE id=?", (ts, send["id"]))
    return True


# ---------------------------------------------------------------- reports
def stats():
    ts = int(time.time())
    day_start = int(time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1)))
    week_start = day_start - 6 * 86400
    counts = lead_counts()
    by_status = counts["by_status"]
    sent_map = sent_today_map()
    boxes = []
    for b in db.rows("SELECT * FROM mailboxes ORDER BY id"):
        boxes.append({"id": b["id"], "name": b["name"], "from_email": b["from_email"],
                      "daily_limit": b["daily_limit"], "sent_today": sent_map.get(b["id"], 0),
                      "active": b["active"]})
    return {
        "offers": db.scalar("SELECT COUNT(*) FROM offers"),
        "offers_active": db.scalar("SELECT COUNT(*) FROM offers WHERE active=1"),
        "leads_total": counts["total"],
        "leads_new": by_status.get("new", 0),
        "leads_contacted": by_status.get("contacted", 0),
        "leads_replied": by_status.get("replied", 0),
        "leads_won": by_status.get("won", 0),
        "by_status": by_status,
        "sends_total": db.scalar("SELECT COUNT(*) FROM sends WHERE status='sent'"),
        "sends_today": db.scalar("SELECT COUNT(*) FROM sends WHERE status='sent' AND sent_ts>=?", (day_start,)),
        "sends_week": db.scalar("SELECT COUNT(*) FROM sends WHERE status='sent' AND sent_ts>=?", (week_start,)),
        "replies_total": db.scalar("SELECT COUNT(*) FROM replies WHERE is_bounce=0"),
        "replies_week": db.scalar("SELECT COUNT(*) FROM replies WHERE is_bounce=0 AND received_ts>=?", (week_start,)),
        "bounces": db.scalar("SELECT COUNT(*) FROM replies WHERE is_bounce=1"),
        "opens": db.scalar("SELECT COUNT(*) FROM sends WHERE opened_ts>0"),
        "clicks": db.scalar("SELECT COUNT(*) FROM sends WHERE clicked_ts>0"),
        "running": db.scalar("SELECT COUNT(*) FROM enrollments WHERE status='running'"),
        "due_now": db.scalar("SELECT COUNT(*) FROM enrollments WHERE status='running' AND next_run_ts<=?", (ts,)),
        "suppressed": db.scalar("SELECT COUNT(*) FROM suppressions"),
        "mailboxes": boxes,
        "sequences": db.scalar("SELECT COUNT(*) FROM sequences"),
        "unverified": counts["unverified"],
    }
