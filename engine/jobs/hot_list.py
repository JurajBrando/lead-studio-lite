"""Today's hot list: the companies whose site changed in a way worth a first email.

`_CLEAN_2026-11-02/signal_monitor.py report` writes `SIGNALS/HOT_TODAY/hot_<date>.csv` - heat,
offer, signal, reason, the first email question, the address, name, title, email confidence,
company, domain, evidence. This job runs that step, imports the addresses, and records the file as
a signal so the list is findable from the app.

`mode` decides how much runs before the report: `report` only greys the existing signals, `daily`
also scans the watched homepages and finds decision makers (the full morning chain).
"""
import os

from .. import external
from .base import Job, Context

LIMIT = 3000


def run(ctx):
    folder = external.root("clean")
    try:
        external.require(folder, "signal_monitor.py")
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    mode = str(ctx.param("mode") or "report").strip()
    if mode not in ("report", "daily", "scan", "people", "lookalikes"):
        return {"ok": False, "error": "Unknown mode: %s" % mode}

    ctx.log("signal_monitor.py %s" % mode)
    result = external.run(folder, "signal_monitor.py", [mode],
                          timeout=ctx.int_param("timeout", 5400))
    for line in result["tail"].splitlines()[-25:]:
        ctx.log(line)
    if not result["ok"]:
        return {"ok": False, "error": "the signal monitor failed (exit %s)" % result["code"],
                "seconds": result["seconds"]}

    hot_dir = os.path.join(folder, "SIGNALS", "HOT_TODAY")
    hot_file = external.newest(hot_dir, "hot_????-??-??.csv")
    rows = external.row_count(hot_file)
    imported = 0
    if hot_file and bool(ctx.param("import_leads", True)):
        answer = external.import_csv(hot_file, "hot-list",
                                     limit=ctx.int_param("import_limit", LIMIT))
        imported = int(answer.get("imported") or 0)
        if not answer.get("ok"):
            ctx.warn("the hot list could not be imported: %s" % answer.get("error"))

    if hot_file:
        ctx.add_signal("hot_list", title=os.path.basename(hot_file), source="signal_monitor",
                       source_url=hot_file, payload={"rows": rows, "imported": imported})
    ctx.log("%s: %d companies, %d imported" % (os.path.basename(hot_file) or "no file",
                                               rows, imported))
    return ctx.summary(ok=True, rows=rows, imported=imported, file=hot_file)


JOBS = [
    Job(key="hot_list",
        name="TODAY HOT LIST",
        group="Signals",
        description="Builds today's hot list - the companies whose site changed (a tool dropped, "
                    "ads started, hiring, a lookalike domain) and the first email question for "
                    "each - writes hot_<date>.csv and imports the addresses it carries.",
        run=run,
        schedule="daily 10:30",
        needs_network=True,
        params={"mode": "report", "import_leads": True, "import_limit": LIMIT,
                "timeout": 5400}),
]
