"""Brand watch: scan monitored brands for lookalikes and scam clones.

Runs a rotating slice of the brand list, oldest-scanned first, so a list of ten
or ten thousand brands is all monitored eventually without hammering DNS in one
go. Findings land in the Signals feed as `lookalike_live`, `lookalike_nrd` and
`lookalike_listed`, grouped by the brand name so the Brands page can show a case
file per brand.
"""
from .. import brands, db
from .base import Job, Context


def run(ctx):
    per_run = ctx.int_param("brands_per_run", 25)
    candidates_n = ctx.int_param("candidates_per_brand", 60)
    min_score = ctx.int_param("min_score", 4)
    pause = float(ctx.param("pause", 0.15))
    include_nrd = bool(ctx.param("include_nrd", True))
    include_phishing = bool(ctx.param("include_phishing", True))

    rows = db.rows("SELECT * FROM brands WHERE active=1 ORDER BY last_scan_ts ASC, id ASC LIMIT ?",
                   (per_run,))
    if not rows:
        return ctx.summary(brands=0, note="No brands to watch yet. Add them on the Brands page.")

    total_checked = total_live = total_new = 0
    preview = []
    for brand in rows:
        before = db.scalar("SELECT COUNT(*) FROM signals WHERE company=? AND kind LIKE 'lookalike%'",
                           (brand["name"],))
        report = brands.scan_brand(brand, candidates_per_brand=candidates_n, min_score=min_score,
                                   pause=pause, include_nrd=include_nrd,
                                   include_phishing=include_phishing, ctx=ctx)
        after = db.scalar("SELECT COUNT(*) FROM signals WHERE company=? AND kind LIKE 'lookalike%'",
                          (brand["name"],))
        total_checked += report["checked"]
        total_live += report["live"]
        total_new += max(0, after - before)
        preview.extend(report["findings"][:6])
        ctx.log("%s: %d names checked, %d live, %d findings" % (
            brand["domain"], report["checked"], report["live"], report["count"]))

    return ctx.summary(brands=len(rows), checked=total_checked, live=total_live,
                       new_findings=total_new, findings=preview[:40])


JOBS = [Job(
    key="brand_watch",
    name="Brand watch: lookalikes & scam clones",
    group="Signals",
    description="For the brands you monitor: builds the lookalike names a fake shop would use, "
                "checks which are registered and accepting mail, and cross-checks the new-domain "
                "and phishing feeds. Rotates through the list, oldest scan first.",
    run=run,
    schedule="daily 08:00",
    params={"brands_per_run": 25, "candidates_per_brand": 60, "min_score": 4, "pause": 0.15,
            "include_nrd": True, "include_phishing": True},
)]
