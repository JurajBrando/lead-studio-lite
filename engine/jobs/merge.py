"""Merge files into leads.

Drop CSV / TSV / Excel files into data/imports/ and this job folds them into the
lead database: new addresses become leads, extra columns fill empty fields on
leads that already exist, and processed files are moved aside so nothing is
imported twice.

This is the "combine several files into one list" step that used to be a pile of
one-off scripts: same column auto-mapping as the manual import, but on a folder
and on a schedule.
"""
import os
import shutil

from .. import config, db, importer
from .base import Job, Context

EXTS = (".csv", ".tsv", ".txt", ".xlsx", ".xlsm")


def run(ctx):
    folder = ctx.param("folder") or os.path.join(config.DATA_DIR, "imports")
    folder = folder if os.path.isabs(folder) else os.path.join(config.APP_DIR, folder)
    done_dir = os.path.join(folder, "processed")
    os.makedirs(folder, exist_ok=True)
    os.makedirs(done_dir, exist_ok=True)

    pattern = ctx.param("pattern", "")
    files = []
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        if not os.path.isfile(path) or not name.lower().endswith(EXTS):
            continue
        if pattern and pattern.lower() not in name.lower():
            continue
        files.append(path)

    if not files:
        return ctx.summary(files=0, imported=0, updated=0,
                           note="Nothing to merge - drop CSV files into %s" % folder)

    import csv as _csv
    _csv.field_size_limit(10 ** 9)
    update_existing = bool(ctx.param("update_existing", True))
    mark_status = ctx.param("status", "new")
    totals = {"imported": 0, "updated": 0, "existing_email": 0, "invalid": 0, "duplicates": 0}
    errors = []
    per_file = []

    for path in files:
        name = os.path.basename(path)
        try:
            with open(path, "rb") as fh:
                raw = fh.read()
            result = importer.import_data(name, raw, source=ctx.param("source", "merge"),
                                          update_existing=update_existing, mark_status=mark_status)
        except Exception as e:                     # noqa: BLE001 - one bad file must not stop the rest
            errors.append("%s: %s" % (name, e))
            ctx.warn("could not import %s: %s" % (name, e))
            continue
        if not result.get("ok"):
            errors.append("%s: %s" % (name, result.get("error")))
            ctx.warn("skipped %s: %s" % (name, result.get("error")))
            continue
        for key in totals:
            totals[key] += result.get(key, 0)
        per_file.append({"file": name, "imported": result.get("imported", 0),
                         "updated": result.get("updated", 0),
                         "existing": result.get("existing_email", 0),
                         "invalid": result.get("invalid", 0)})
        ctx.stats["leads"] += result.get("imported", 0)
        try:
            shutil.move(path, os.path.join(done_dir, name))
        except OSError as e:
            ctx.warn("could not move %s: %s" % (name, e))

    ctx.log("merged %d file(s): %d new leads, %d updated" % (len(per_file), totals["imported"],
                                                             totals["updated"]))
    db.event("merge_job", "%d files, %d new, %d updated" % (len(per_file), totals["imported"],
                                                            totals["updated"]))
    return ctx.summary(files=len(per_file), folder=folder, errors=errors,
                       imported=totals["imported"], updated=totals["updated"],
                       existing=totals["existing_email"], invalid=totals["invalid"],
                       detail=per_file[:20])


JOBS = [Job(
    key="merge_files",
    name="Merge files into leads",
    group="Enrichment",
    description="Folds every CSV/TSV/Excel file dropped in data/imports into the lead database, "
                "filling empty fields on existing leads, then moves the files to processed/.",
    run=run,
    schedule="daily 02:00",
    params={"folder": "", "pattern": "", "update_existing": True, "status": "new", "source": "merge"},
    needs_network=False,
)]
