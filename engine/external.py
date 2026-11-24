"""Running the older tooling from inside the app.

Three jobs the app does not implement itself because the scripts already exist and work:

  * `LEAD_STUDIO/daily_pipeline.py run`             the whole chain, with its own report
  * `_CLEAN_2026-11-02/osm_icp_scrape.py`           OpenStreetMap businesses per offer
  * `_CLEAN_2026-11-02/signal_monitor.py report`    today's hot list

They are outside this repository (the public one ships code only), so the folder is a setting
(`external.studio` / `external.clean`) with a sensible default, and a job whose folder is missing
says exactly which one instead of failing with a stack trace.
"""
import glob
import os
import re
import subprocess
import sys
import time

from . import config, db, importer

# The two tool folders sit next to this app's own folder (..\LEAD_STUDIO and
# ..\_CLEAN_2026-11-02). Worked out from where the app is, so no account name or machine
# path is written into the code; `external.studio` / `external.clean` in settings.json, or
# the LS_STUDIO_DIR / LS_CLEAN_DIR environment variables, point somewhere else.
_PARENT = os.path.dirname(config.APP_DIR)
DEFAULTS = {
    "studio": os.environ.get("LS_STUDIO_DIR") or os.path.join(_PARENT, "LEAD_STUDIO"),
    "clean": os.environ.get("LS_CLEAN_DIR") or os.path.join(_PARENT, "_CLEAN_2026-11-02"),
}
HANDOFF_RX = re.compile(r"^\s{2}(.+?)\s{2,}([A-Za-z]:\\[^()]+?)\s*"
                        r"(?:\((\d[\d,]*)\s+rows?\))?\s*$")


def settings_block():
    return dict(config.load_settings().get("external") or {})


def root(kind="studio"):
    block = settings_block()
    return os.path.abspath(str(block.get(kind) or DEFAULTS.get(kind) or ""))


def require(folder, *names):
    """The folder and the scripts it needs, or a message that names them."""
    if not folder or not os.path.isdir(folder):
        raise ValueError("That folder is not there: %s. Set external.%s in settings.json."
                         % (folder or "(empty)", "studio" if "daily_pipeline.py" in names else "clean"))
    missing = [name for name in names if not os.path.exists(os.path.join(folder, name))]
    if missing:
        raise ValueError("%s has no %s." % (folder, ", ".join(missing)))
    return folder


def run(folder, script, args=(), timeout=1800):
    """Run one script and keep the end of its output for the job log."""
    started = time.time()
    try:
        finished = subprocess.run([sys.executable, script] + list(args), cwd=folder,
                                  capture_output=True, text=True, timeout=timeout)
        code = finished.returncode
        output = (finished.stdout or "") + (finished.stderr or "")
    except subprocess.TimeoutExpired as exc:
        code = -1
        output = "timed out after %ss\n%s" % (timeout, (exc.stdout or "") if isinstance(exc.stdout, str) else "")
    tail = "\n".join([line for line in output.strip().splitlines() if line.strip()][-40:])
    return {"ok": code == 0, "code": code, "tail": tail,
            "seconds": round(time.time() - started, 1)}


def newest(folder, pattern):
    files = glob.glob(os.path.join(folder, pattern))
    return max(files, key=os.path.getmtime) if files else ""


def parse_handoffs(report):
    """[(label, path, rows)] from the pipeline's `handoffs` section."""
    found, in_section = [], False
    for line in (report or "").splitlines():
        if line.strip().lower().startswith("handoffs"):
            in_section = True
            continue
        if not in_section:
            continue
        if not line.strip():
            if found:
                break
            continue
        match = HANDOFF_RX.match(line)
        if match:
            rows = match.group(3)
            found.append((match.group(1).strip(), match.group(2).strip(),
                          int(rows.replace(",", "")) if rows else 0))
    return found


def import_csv(path, source, limit=20000, update_existing=False):
    """Import a CSV the older tooling produced, with the same importer the UI uses."""
    if not path or not os.path.exists(path):
        return {"ok": False, "error": "No file at %s" % path}
    return importer.import_path(path, limit=limit, source=source,
                                update_existing=update_existing)


def row_count(path):
    """Fast line count for a reported handoff (the files can be millions of rows)."""
    if not path or not os.path.exists(path):
        return 0
    try:
        with open(path, "rb") as handle:
            return max(0, sum(1 for _ in handle) - 1)
    except OSError:
        return 0
