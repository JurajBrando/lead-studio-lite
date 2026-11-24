"""The right-hand rail: what is running, and what happened today, on every page.

One request gathers it all; the UI polls it every few seconds. Everything here is either
in memory (running work) or an indexed query (today's sends, replies, army events), and the
one slow number - leads by status on a table with millions of rows - goes through the same
two-minute cache the dashboard already uses. "New leads today" never counts the big table:
it compares the highest lead id with the one remembered at the start of the day.
"""
import threading
import time

from . import daily, db, hunt, pipeline, progress, scheduler, tasks

_CACHE = {"ts": 0.0, "data": None}
_LOCK = threading.Lock()
TTL = 8


def _day_start():
    return int(time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1)))


def _running():
    out = []
    for job in scheduler.live():
        out.append({"kind": "job", "name": job["name"], "percent": job["percent"], "phase": job["phase"],
                    "eta": job["eta"], "elapsed": job["elapsed"], "key": job["key"],
                    "cancel": bool(job.get("cancel"))})
    run = dict(hunt.RUN)
    if run.get("running"):
        offer = db.one("SELECT o.name FROM hunts h JOIN offers o ON o.id=h.offer_id WHERE h.id=?",
                       (run.get("hunt_id") or 0,)) or {}
        out.append({"kind": "hunt", "name": "Radar hunt" + (": " + offer["name"] if offer.get("name") else ""),
                    "percent": int(round(100 * float(run.get("progress") or 0))), "phase": run.get("phase", ""),
                    "eta": 0, "elapsed": 0, "key": str(run.get("hunt_id") or "hunt"),
                    "cancel": bool(run.get("cancel"))})
    with progress._LOCK:                                  # imports in flight
        ids = [rid for rid, r in progress._RUNS.items() if not r.get("finished")]
    for rid in ids:
        snap = progress.snapshot(rid)
        if snap.get("ok"):
            out.append({"kind": "import", "name": "Import: " + (snap.get("label") or "file"),
                        "percent": snap.get("percent", 0), "phase": snap.get("phase", ""),
                        "eta": snap.get("eta", 0), "elapsed": snap.get("elapsed", 0),
                        "rows": snap.get("rows", 0), "key": rid,
                        "cancel": bool(snap.get("cancel"))})
    for task in tasks.running(tail=1):
        out.append({"kind": "task", "name": task.get("name") or "Background task", "percent": 0,
                    "phase": ((task.get("lines") or [""])[-1] or "")[:80], "eta": 0, "elapsed": 0,
                    "key": task.get("id")})
    return out


def _new_leads_today(day0):
    top = db.scalar("SELECT MAX(id) FROM leads", default=0) or 0
    key = "rail_leads_maxid_%d" % day0
    base = hunt.kv_get(key, "")
    if base == "":
        hunt.kv_set(key, top)
        return 0
    return max(0, top - int(base or 0))


def _hot(day0):
    hot = []
    for r in db.rows(
            "SELECT r.id, r.from_email, r.subject, r.snippet, r.received_ts, l.company, l.domain "
            "FROM replies r LEFT JOIN leads l ON l.id=r.lead_id WHERE r.class='positive' AND r.received_ts>=? "
            "ORDER BY r.id DESC LIMIT 6", (day0,)):
        hot.append({"kind": "positive", "title": r.get("company") or r.get("domain") or r["from_email"],
                    "detail": (r.get("snippet") or r.get("subject") or "")[:120], "ts": r["received_ts"],
                    "go": "inbox"})
    try:
        feed = daily.feed(limit=300, min_fit=2)
    except Exception:
        feed = []
    for row in feed:
        if int(row.get("ts") or 0) < day0:
            continue
        hot.append({"kind": "signal", "title": row.get("company") or row.get("domain") or "",
                    "detail": "%s · fits %s" % ((row.get("title") or row.get("kind") or "")[:70], row.get("offer") or ""),
                    "ts": row.get("ts"), "go": "signals"})
        if sum(1 for h in hot if h["kind"] == "signal") >= 5:
            break
    for h in db.rows("SELECT id, offer_id, rows FROM hunts WHERE finished_ts>=? AND status='done' ORDER BY id DESC LIMIT 3",
                     (day0,)):
        rows = hunt._j(h.get("rows"), [])
        best = [r for r in rows if r.get("pass") and r.get("email") and not r.get("approved") and not r.get("skipped")]
        for r in best[:3]:
            hot.append({"kind": "radar", "title": r.get("name") or r.get("domain"),
                        "detail": "rank %d · %s" % (int(r.get("rank") or 0), (r.get("why") or "")[:90]),
                        "ts": h["id"], "go": "radar"})
    return hot[:12]


def build():
    now = int(time.time())
    day0 = _day_start()
    sends_today = db.scalar("SELECT COUNT(*) FROM sends WHERE status='sent' AND sent_ts>=?", (day0,))
    replies_today = db.scalar("SELECT COUNT(*) FROM replies WHERE is_bounce=0 AND COALESCE(class,'') NOT IN "
                              "('bounce','auto_reply') AND received_ts>=?", (day0,))
    positives_today = db.scalar("SELECT COUNT(*) FROM replies WHERE class='positive' AND received_ts>=?", (day0,))
    bounces_today = db.scalar("SELECT COUNT(*) FROM replies WHERE (is_bounce=1 OR class='bounce') AND received_ts>=?",
                              (day0,))
    army_sends = db.scalar("SELECT COUNT(*) FROM army_events WHERE kind IN ('send','followup') AND ts>=?", (day0,))
    caps = 0
    for box in db.rows("SELECT daily_limit FROM mailboxes WHERE active=1"):
        caps += int(box["daily_limit"] or 0)

    counts = pipeline.lead_counts()
    by = counts.get("by_status") or {}
    soldiers = db.rows("SELECT state, COUNT(*) AS n FROM soldiers GROUP BY state")
    sold = {r["state"]: r["n"] for r in soldiers}
    try:
        from . import army
        mission = army.mission()
    except Exception:
        mission = {}
    offers = db.rows(
        "SELECT o.id, o.name, "
        "(SELECT COUNT(*) FROM radar_leads r WHERE r.offer_id=o.id AND r.status='approved') AS territory, "
        "(SELECT COUNT(*) FROM soldiers s WHERE s.offer_id=o.id) AS soldiers, "
        "(SELECT COUNT(*) FROM army_events e WHERE e.offer_id=o.id AND e.kind IN ('send','followup') AND e.ts>=?) AS sent_today, "
        "(SELECT COUNT(*) FROM army_events e WHERE e.offer_id=o.id AND e.kind='positive' AND e.ts>=?) AS positive_today "
        "FROM offers o WHERE o.active=1 ORDER BY sent_today DESC, territory DESC, o.id LIMIT 8", (day0, day0))
    recent = db.rows("SELECT job_key, status, finished_ts FROM runs ORDER BY id DESC LIMIT 4")
    return {
        "ok": True, "ts": now,
        "running": _running(),
        "recent_runs": recent,
        "today": {"sent": sends_today, "army_sent": army_sends, "capacity": caps, "replies": replies_today,
                  "positive": positives_today, "bounces": bounces_today,
                  "reply_rate": round(replies_today / float(sends_today), 3) if sends_today else 0.0,
                  "drafts": db.scalar("SELECT COUNT(*) FROM army_drafts WHERE status='pending'"),
                  "queued": db.scalar("SELECT COUNT(*) FROM army_drafts WHERE status='approved'"),
                  "unread": db.scalar("SELECT COUNT(*) FROM replies WHERE seen=0")},
        "leads": {"total": counts.get("total", 0), "new_today": _new_leads_today(day0),
                  "new": by.get("new", 0), "contacted": by.get("contacted", 0), "replied": by.get("replied", 0),
                  "interested": by.get("interested", 0), "won": by.get("won", 0),
                  "territory": db.scalar("SELECT COUNT(*) FROM radar_leads WHERE status='approved'"),
                  "signals_today": db.scalar("SELECT COUNT(*) FROM signals WHERE id>(SELECT COALESCE(MAX(id),0)-5000 "
                                             "FROM signals) AND ts>=?", (day0,))},
        "army": {"total": sum(sold.values()), "active": sold.get("active", 0), "training": sold.get("training", 0),
                 "paused": sold.get("paused", 0), "benched": sold.get("benched", 0),
                 "mission": {k: mission.get(k) for k in ("progress", "target", "leader", "done")}},
        "offers": offers,
        "hot": _hot(day0),
    }


def get():
    with _LOCK:
        if _CACHE["data"] and time.time() - _CACHE["ts"] < TTL:
            data = dict(_CACHE["data"])
            data["running"] = _running()              # running work is always live
            return data
    data = build()
    with _LOCK:
        _CACHE.update({"ts": time.time(), "data": data})
    return data
