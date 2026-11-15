"""The whole chain, one run: new domains -> ads transparency -> fraud -> Red Points -> cross-check.

The stages and their order live in `LEAD_STUDIO/PIPELINE/config.json`, which `daily_pipeline.py`
reads; this job just runs it, keeps the log tail and turns the report's `handoffs` section into
signals, so the files it names are visible in the app.
"""
import os

from .. import external
from .base import Job, Context

TIMEOUT = 5400


def run(ctx):
    folder = external.root("studio")
    try:
        external.require(folder, "daily_pipeline.py")
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    args = ["run"]
    if bool(ctx.param("with_jobs", False)):
        args.append("--with-jobs")
    only = str(ctx.param("only") or "").strip()
    if only:
        args += ["--only", only]

    ctx.log("running daily_pipeline.py %s in %s" % (" ".join(args), folder))
    result = external.run(folder, "daily_pipeline.py", args,
                          timeout=ctx.int_param("timeout", TIMEOUT))
    for line in result["tail"].splitlines()[-25:]:
        ctx.log(line)

    report_path = external.newest(os.path.join(folder, "PIPELINE"), "report_*.txt")
    handoffs = []
    if report_path:
        try:
            with open(report_path, "r", encoding="utf-8", errors="replace") as handle:
                handoffs = external.parse_handoffs(handle.read())
        except OSError as exc:
            ctx.warn("could not read the report: %s" % exc)
    for label, path, rows in handoffs:
        ctx.add_signal("pipeline_handoff", title=label, source="pipeline",
                       source_url=path, payload={"rows": rows, "report": report_path})
        ctx.log("handoff %s -> %s (%s rows)" % (label, path, rows))

    return ctx.summary(ok=result["ok"], stages=len(handoffs), seconds=result["seconds"],
                       report=report_path, exit_code=result["code"],
                       note="" if result["ok"] else "the pipeline finished with an error; the "
                                                   "stages that ran are still in the report")


JOBS = [
    Job(key="pipeline_all",
        name="Run the whole pipeline",
        group="Data",
        description="One run of the chain in order - new domains (smet.cz + WhoisExtractor), "
                    "Google Ads Transparency, fraud listings, Red Points cases against the brand "
                    "dataset, then the cross-check of new domains against those lists. A failing "
                    "stage does not stop the rest, and the report names the handoff files.",
        run=run,
        schedule="daily 07:15",
        needs_network=True,
        params={"with_jobs": False, "only": "", "timeout": TIMEOUT}),
]
