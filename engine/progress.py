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
                         "result": None, "error": "", "finished": False, "lines": [],
                         "cancel": False,
                         "track": "read",
                         "tracks": {"read": {"done": 0.0, "total": 0.0, "rows": 0},
                                    "import": {"done": 0.0, "total": 0.0, "rows": 0}}}
        prune_locked()
        return run_id


def track_of(phase):
    """Which counter a phase belongs to. Reading and importing are separate work."""
    name = str(phase or "").lower()
    if name.startswith(("importing", "finishing")):
        return "import"
    return "read"


def update(run_id, done=None, total=None, phase=None, rows=None):
    """One phase's progress. Counters only ever move forward within their own track.

    The reader and the importer both report here - the reader counts rows pulled out of
    the file, the importer counts rows written - and because the reader is a generator the
    importer drains, the two interleave. Kept in one field they overwrote each other and
    the count appeared to jump backwards, so each gets its own track.
    """
    with _LOCK:
        run = _RUNS.get(run_id)
        if not run or run["finished"]:
            return
        if run.get("cancel"):
            return
        if phase:
            run["phase"] = str(phase)
            run["track"] = track_of(phase)
        slot = run["tracks"][run["track"]]
        if done is not None:
            slot["done"] = max(slot["done"], max(0.0, float(done)))
        if total is not None:
            slot["total"] = max(0.0, float(total))
        if rows is not None:
            slot["rows"] = max(slot["rows"], int(rows))
        # the legacy flat fields mirror the track in play, for anything still reading them
        run["done"], run["total"], run["rows"] = slot["done"], slot["total"], slot["rows"]
        run["updated"] = time.time()


def log(run_id, message):
    """One line for the UI's log window. Keeps the last 400."""
    with _LOCK:
        run = _RUNS.get(run_id)
        if not run:
            return
        run.setdefault("lines", []).append("%s  %s" % (time.strftime("%H:%M:%S"), message))
        if len(run["lines"]) > 400:
            del run["lines"][:100]
        run["updated"] = time.time()


def finish(run_id, result=None, error="", cancelled=False):
    with _LOCK:
        run = _RUNS.get(run_id)
        if not run:
            return
        run["finished"] = True
        run["result"] = result
        run["error"] = str(error or "")
        run["phase"] = "cancelled" if cancelled else ("failed" if error else "done")
        run["updated"] = time.time()
        if not error and not cancelled:
            slot = run["tracks"][run.get("track") or "read"]
            if not slot["total"]:
                slot["total"] = slot["done"] or 1    # nothing measured: a finished run is done
            slot["done"] = slot["total"]
            run["done"], run["total"] = slot["done"], slot["total"]


def request_cancel(run_id):
    """Ask a running import to stop where it next reports progress."""
    with _LOCK:
        run = _RUNS.get(str(run_id or ""))
        if not run or run["finished"]:
            return False
        run["cancel"] = True
        run["phase"] = "stopping"
        run["updated"] = time.time()
        return True


def is_cancelled(run_id):
    with _LOCK:
        run = _RUNS.get(str(run_id or ""))
        return bool(run and run.get("cancel"))


def snapshot(run_id):
    with _LOCK:
        run = _RUNS.get(str(run_id or ""))
        if not run:
            return {"ok": False, "error": "That run is no longer tracked."}
        end = run["updated"] if run["finished"] else time.time()
        elapsed = max(0.001, end - run["started"])
        tracks = run["tracks"]
        read_rows = int(tracks["read"]["rows"])
        imported_rows = int(tracks["import"]["rows"])
        slot = tracks[run.get("track") or "read"]
        total, done = slot["total"], slot["done"]
        percent = int(min(100, round(done * 100.0 / total))) if total else 0
        rate = done / elapsed if done else 0.0
        eta = int(math.ceil((total - done) / rate)) if (total and done and rate) else 0
        if not total and run.get("track") == "import":
            # A streamed source (CSV, Parquet) has no row total before its last line, so
            # the importer has nothing to divide by. The reader's byte position is the
            # only measure of how much of the file is left; total and done stay rows so
            # the UI does not print bytes as if they were contacts.
            read_slot = tracks["read"]
            if read_slot["total"]:
                percent = int(min(100, round(read_slot["done"] * 100.0 / read_slot["total"])))
                if read_slot["done"]:
                    read_rate = read_slot["done"] / elapsed
                    eta = int(math.ceil((read_slot["total"] - read_slot["done"]) / read_rate))
        # `rows` means one thing for the whole run: rows actually written. It stays 0
        # while the file is only being read (read_rows covers that), so it never drops.
        rows = imported_rows
        return {"ok": True, "id": run["id"], "label": run["label"], "phase": run["phase"],
                "total": total, "done": done, "rows": rows, "percent": percent,
                "track": run.get("track") or "read",
                "read_rows": read_rows, "imported_rows": imported_rows,
                "elapsed": int(elapsed), "rate": round(rate, 2), "eta": eta,
                "finished": run["finished"], "error": run["error"], "result": run["result"],
                "cancel": bool(run.get("cancel")),
                "lines": list(run.get("lines") or [])[-120:]}


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
