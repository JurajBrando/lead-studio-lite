"""Live progress for the long work a request starts, read back by the UI.

An import runs in a background thread and reports how far it is; the browser polls
`/api/import/progress` and draws the bar. Nothing is written to SQLite on every tick - the
registry lives in memory, keyed by a run id, and finished runs are dropped after 15 minutes.
"""
import math
import threading
import time

_LOCK = threading.Lock()
_RUNS = {}
KEEP_SECONDS = 900


def start(label=""):
    with _LOCK:
        run_id = "run-%d-%04d" % (int(time.time() * 1000), len(_RUNS) % 10000)
        _RUNS[run_id] = {"id": run_id, "label": label, "total": 0.0, "done": 0.0, "rows": 0,
                         "phase": "starting", "started": time.time(), "updated": time.time(),
                         "result": None, "error": "", "finished": False}
        prune_locked()
        return run_id


def update(run_id, done=None, total=None, phase=None, rows=None):
    with _LOCK:
        run = _RUNS.get(run_id)
        if not run or run["finished"]:
            return
        if done is not None:
            run["done"] = max(0.0, float(done))
        if total is not None:
            run["total"] = max(0.0, float(total))
        if phase:
            run["phase"] = str(phase)
        if rows is not None:
            run["rows"] = int(rows)
        run["updated"] = time.time()


def finish(run_id, result=None, error=""):
    with _LOCK:
        run = _RUNS.get(run_id)
        if not run:
            return
        run["finished"] = True
        run["result"] = result
        run["error"] = str(error or "")
        run["phase"] = "failed" if error else "done"
        run["updated"] = time.time()
        if not error:
            if not run["total"]:
                run["total"] = run["done"] or 1      # nothing measured: a finished run is done
            run["done"] = run["total"]


def snapshot(run_id):
    with _LOCK:
        run = _RUNS.get(str(run_id or ""))
        if not run:
            return {"ok": False, "error": "That run is no longer tracked."}
        end = run["updated"] if run["finished"] else time.time()
        elapsed = max(0.001, end - run["started"])
        total, done = run["total"], run["done"]
        percent = int(min(100, round(done * 100.0 / total))) if total else 0
        rate = done / elapsed if done else 0.0
        eta = int(math.ceil((total - done) / rate)) if (total and done and rate) else 0
        return {"ok": True, "id": run["id"], "label": run["label"], "phase": run["phase"],
                "total": total, "done": done, "rows": run["rows"], "percent": percent,
                "elapsed": int(elapsed), "rate": round(rate, 2), "eta": eta,
                "finished": run["finished"], "error": run["error"], "result": run["result"]}


def prune_locked():
    cutoff = time.time() - KEEP_SECONDS
    for key in [key for key, run in _RUNS.items()
                if run["finished"] and run["updated"] < cutoff]:
        _RUNS.pop(key, None)


def prune():
    with _LOCK:
        prune_locked()


def forget(run_id):
    with _LOCK:
        _RUNS.pop(str(run_id or ""), None)
