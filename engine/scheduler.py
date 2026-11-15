"""Scheduling and running of the automation jobs.

The schedule is deliberately simple and local-time based:

    "daily 06:30"          every day at 06:30
    "weekly mon 07:30"     Mondays at 07:30
    ""                     manual only

Everything about a job lives in the `jobs` table, so toggles and parameter edits
survive restarts. Each run writes a row to `runs` with its log, which is what the
Automation page shows.
"""
import json
import re
import threading
import time
import traceback

from . import config, db, net
from . import jobs as registry
from .jobs.base import Context

WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
_lock = threading.Lock()
_thread = None
_stop = threading.Event()


def parse_schedule(schedule):
    """'daily 06:30' -> ('daily', None, 6, 30); 'weekly mon 07:30' -> ('weekly', 0, 7, 30)."""
    text_value = (schedule or "").strip().lower()
    if not text_value:
        return None
    parts = text_value.split()
    freq = parts[0]
    clock = parts[-1]
    try:
        hour, minute = [int(x) for x in clock.split(":")[:2]]
    except (ValueError, IndexError):
        return None
    if not (0 <= hour < 24 and 0 <= minute < 60):
        return None
    if freq == "daily" and len(parts) == 2:
        return ("daily", None, hour, minute)
    if freq == "weekly" and len(parts) == 3:
        day = WEEKDAYS.get(parts[1][:3])
        return ("weekly", None, hour, minute) if day is None else ("weekly", day, hour, minute)
    return None


def next_run_ts(schedule, base=None):
    """Next local-time occurrence strictly after `base` (default: now)."""
    parsed = parse_schedule(schedule)
    if not parsed:
        return 0
    freq, weekday, hour, minute = parsed
    now = time.localtime(base or time.time())
    candidate = time.mktime((now.tm_year, now.tm_mon, now.tm_mday, hour, minute, 0, 0, 0, -1))
    if candidate <= (base or time.time()):
        candidate += 86400
    if freq == "weekly":
        target = weekday
        for _ in range(8):
            if time.localtime(candidate).tm_wday == target:
                break
            candidate += 86400
    return int(candidate)


def sync_registry():
    """Create rows for new jobs; keep the user's enabled flag and parameters."""
    for job in registry.all_jobs():
        row = db.one("SELECT * FROM jobs WHERE key=?", (job.key,))
        defaults = json.dumps(job.params, ensure_ascii=False)
        if not row:
            db.insert("jobs", {"key": job.key, "name": job.name, "grp": job.group,
                               "description": job.description, "schedule": job.schedule,
                               "enabled": 1, "params": defaults,
                               "next_run_ts": next_run_ts(job.schedule)})
            continue
        schedule = row.get("schedule") or job.schedule
        try:
            current = json.loads(row.get("params") or "{}")
        except ValueError:
            current = {}
        merged = dict(job.params)
        merged.update(current or {})
        db.execute("UPDATE jobs SET name=?, grp=?, description=?, schedule=?, params=?, next_run_ts=? "
                   "WHERE key=?",
                   (job.name, job.group, job.description, schedule,
                    json.dumps(merged, ensure_ascii=False),
                    int(row.get("next_run_ts") or 0) or next_run_ts(schedule), job.key))


GROUP_ORDER = ("Domains", "Signals", "Data", "AI")

# The chain inside a group: a domain arrives, its mail server is checked, Common Crawl says
# whether the site is real, then the contact finder looks for an address. The clock times are
# the user's; this is only the order the page shows them in.
CHAIN_ORDER = {
    "new_domains": 10, "domain_mx": 20, "commoncrawl": 30, "cc_hf": 35, "cc_bulk": 38,
    "contact_finder": 40, "cc_contacts": 45,
    "phishing_watch": 10, "hiring_signals": 20, "bankruptcy_watch": 30, "brand_watch": 40,
    "ads_transparency": 50,
    "merge_files": 10,
    "ai_enrich": 10,
}


def _group_rank(group):
    try:
        return GROUP_ORDER.index(group or "")
    except ValueError:
        return len(GROUP_ORDER)


def _schedule_rank(schedule):
    """`daily 06:00` / `weekly sun 04:30` -> (weekly, minutes), so a day runs in clock order."""
    text = (schedule or "").lower().strip()
    match = re.search(r"(\d{1,2}):(\d{2})", text)
    minutes = int(match.group(1)) * 60 + int(match.group(2)) if match else 0
    return (1 if text.startswith("weekly") else 0, minutes)


def list_jobs():
    sync_registry()
    rows = sorted(db.rows("SELECT * FROM jobs"),
                  key=lambda row: (_group_rank(row.get("grp")),
                                   CHAIN_ORDER.get(row.get("key"), 90),
                                   _schedule_rank(row.get("schedule")),
                                   row.get("name") or ""))
    out = []
    for row in rows:
        job = registry.by_key(row["key"])
        try:
            params = json.loads(row.get("params") or "{}")
        except ValueError:
            params = {}
        out.append({**row, "params": params, "params_default": job.params if job else {},
                    "needs_network": job.needs_network if job else True,
                    "available": job is not None,
                    "last_run": (db.one("SELECT * FROM runs WHERE job_key=? ORDER BY id DESC LIMIT 1",
                                        (row["key"],)) or {})})
    return out


def run_job(key, trigger="manual", params=None, log_sink=None):
    job = registry.by_key(key)
    if not job:
        return {"ok": False, "error": "Unknown job: %s" % key}
    row = db.one("SELECT * FROM jobs WHERE key=?", (key,))
    if not row:
        sync_registry()
        row = db.one("SELECT * FROM jobs WHERE key=?", (key,))
    if row and int(row.get("running") or 0):
        return {"ok": False, "error": "That job is already running."}

    try:
        stored = json.loads((row or {}).get("params") or "{}")
    except ValueError:
        stored = {}
    merged = dict(stored)
    merged.update(params or {})

    db.execute("UPDATE jobs SET running=1 WHERE key=?", (key,))
    started = db.now()
    ctx = Context(job, merged, log_sink=log_sink)
    status, summary, error = "ok", {}, ""
    try:
        result = job.run(ctx) or {}
        if result.get("ok") is False:
            status, error = "error", str(result.get("error") or "job reported failure")
        summary = result
    except Exception as exc:                          # noqa: BLE001
        status, error = "error", str(exc)
        ctx.log("FAILED: %s" % exc)
        ctx.log(traceback.format_exc()[-1500:])
        config.log("job %s failed: %s\n%s" % (key, exc, traceback.format_exc()))
    finished = db.now()

    try:
        summary_text = json.dumps(summary, ensure_ascii=False, default=str)[:1000]
    except (TypeError, ValueError):
        summary_text = str(summary)[:1000]
    log_text = "\n".join(ctx.logs)[-20000:]
    db.insert("runs", {"job_key": key, "started_ts": started, "finished_ts": finished,
                       "status": status, "summary": summary_text, "log": log_text})
    schedule = (row or {}).get("schedule") or job.schedule
    db.execute("UPDATE jobs SET running=0, last_run_ts=?, last_status=?, last_summary=?, next_run_ts=? "
               "WHERE key=?",
               (finished, status, summary_text, next_run_ts(schedule) if schedule else 0, key))
    db.event("job", "%s %s (%s)" % (key, status, summary_text[:120]))
    return {"ok": status == "ok", "status": status, "error": error, "summary": summary,
            "log": log_text, "job": key, "trigger": trigger,
            "duration": finished - started}


def run_logs(key=None, limit=50):
    if key:
        return db.rows("SELECT * FROM runs WHERE job_key=? ORDER BY id DESC LIMIT ?", (key, limit))
    return db.rows("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))


def due_keys(now=None):
    ts = int(now or time.time())
    rows = db.rows("SELECT key FROM jobs WHERE enabled=1 AND schedule<>'' AND next_run_ts>0 "
                   "AND next_run_ts<=? AND running=0 ORDER BY next_run_ts", (ts,))
    return [r["key"] for r in rows]


def _loop():
    while not _stop.is_set():
        try:
            for key in due_keys():
                run_job(key, trigger="schedule")
        except Exception as exc:                      # noqa: BLE001
            config.log("scheduler loop: %s" % exc)
        _stop.wait(20)


def start():
    """Create the job rows and start the background scheduler once."""
    global _thread
    if _thread and _thread.is_alive():
        return _thread
    db.init_db()
    sync_registry()
    # A crash (or a hard "Quit" from the tray, which exits the process) leaves running=1
    # behind, and due_keys would then never pick that job again. Nothing can be running
    # before this point, so clear the flags.
    stuck = db.scalar("SELECT COUNT(*) FROM jobs WHERE running<>0")
    if stuck:
        db.execute("UPDATE jobs SET running=0 WHERE running<>0")
        config.log("scheduler: cleared %d stuck running flag(s)" % stuck)
    _thread = threading.Thread(target=_loop, name="ls-scheduler", daemon=True)
    _thread.start()
    return _thread


def stop():
    _stop.set()
