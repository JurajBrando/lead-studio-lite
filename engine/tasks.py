"""Background work with a live log.

The video renderer runs for minutes, so it runs as a child process (a crash in ffmpeg must
not take the app down) and its output is collected here for the UI to poll.

Nothing is persisted: a task lives as long as the app runs. That is deliberate - the
result is a file on disk, not a record worth keeping.
"""
import itertools
import os
import subprocess
import sys
import threading
import time

_LOCK = threading.Lock()
_TASKS = {}
_IDS = itertools.count(1)
MAX_LOG_LINES = 4000
KEEP_FINISHED = 20


class Task:
    def __init__(self, task_id, name, argv):
        self.id = task_id
        self.name = name
        self.argv = argv
        self.status = "running"
        self.started = int(time.time())
        self.finished = 0
        self.returncode = None
        self.error = ""
        self.log = []
        self.proc = None

    def add(self, line):
        line = line.rstrip("\r\n")
        if not line:
            return
        self.log.append(line)
        if len(self.log) > MAX_LOG_LINES:
            del self.log[:len(self.log) - MAX_LOG_LINES]

    def public(self, tail=0):
        with _LOCK:
            log = self.log[-tail:] if tail else list(self.log)
        return {"id": self.id, "name": self.name, "status": self.status,
                "started": self.started, "finished": self.finished,
                "returncode": self.returncode, "error": self.error, "log": log,
                "seconds": (self.finished or int(time.time())) - self.started}


def _prune():
    done = [t for t in _TASKS.values() if t.status != "running"]
    if len(done) <= KEEP_FINISHED:
        return
    done.sort(key=lambda t: t.finished)
    for task in done[:len(done) - KEEP_FINISHED]:
        _TASKS.pop(task.id, None)


def _pump(task):
    stdout = task.proc.stdout
    try:
        for line in stdout:
            task.add(line)
    except (OSError, ValueError):
        pass
    finally:
        try:
            code = task.proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            task.proc.kill()
            code = task.proc.wait()
        with _LOCK:
            task.returncode = code
            task.finished = int(time.time())
            task.status = "ok" if code == 0 else "failed"
            if code != 0:
                tail = [ln for ln in task.log[-6:] if ln.strip()]
                task.error = tail[-1] if tail else "The job exited with code %d." % code
        if code == 0:
            task.add("Done.")
        _prune()


def start(name, argv, cwd=None, env=None):
    """Run argv as a child process. Returns the task id."""
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    full_env = dict(os.environ)
    full_env.update(env or {})
    full_env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.Popen(list(argv), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
                            errors="replace", bufsize=1, cwd=cwd, env=full_env,
                            creationflags=flags)
    with _LOCK:
        task = Task(str(next(_IDS)), name, list(argv))
        task.proc = proc
        _TASKS[task.id] = task
    threading.Thread(target=_pump, args=(task,), daemon=True,
                     name="ls-task-%s" % task.id).start()
    return task.id


def get(task_id):
    task = _TASKS.get(str(task_id))
    return task.public() if task else None


def recent(limit=10, tail=20):
    with _LOCK:
        items = sorted(_TASKS.values(), key=lambda t: t.started, reverse=True)[:limit]
    return [t.public(tail=tail) for t in items]


def cancel(task_id):
    task = _TASKS.get(str(task_id))
    if not task or task.status != "running" or not task.proc:
        return False
    try:
        task.proc.terminate()
    except OSError:
        return False
    task.status = "cancelled"
    task.add("Cancelled.")
    return True


def running(tail=20):
    return [t.public(tail=tail) for t in _TASKS.values() if t.status == "running"]
