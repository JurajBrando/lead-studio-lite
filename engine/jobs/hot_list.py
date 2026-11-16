"""Today's hot list, built natively from this app's own signals, leads and offers.

The signals arrive from the Daily jobs (a tool disappeared, ads started, a lookalike domain was
registered, the site says it is hiring); this job turns them into the list the user reads:
heat, offer, signal, reason, the first email question, who to write to and how sure we are of the
address. It writes `data/hot/hot_<date>.csv` with the same columns as the older hot list.
"""
from .. import hotlist
from .base import Job, Context


def run(ctx):
    answer = hotlist.build(days=ctx.int_param("days", 2), limit=ctx.int_param("limit", 500))
    counts = answer["counts"]
    ctx.log("%d rows: %d hot, %d warm, %d with an email -> %s" % (
        counts["total"], counts["HOT"], counts["WARM"], counts["with_email"], answer["path"]))
    ctx.add_signal("hot_list", title="Today's hot list (%d)" % counts["total"],
                   source="hotlist", source_url=answer["path"], payload=counts)
    return ctx.summary(ok=True, rows=counts["total"], hot=counts["HOT"],
                       warm=counts["WARM"], with_email=counts["with_email"],
                       file=answer["path"])


JOBS = [
    Job(key="hot_list",
        name="TODAY HOT LIST",
        group="Signals",
        description="Builds today's hot list from the signals the other jobs collected - what "
                    "changed, why it matters, the first email question, the address and how sure "
                    "we are of it - and writes hot_<date>.csv.",
        run=run,
        schedule="daily 10:30",
        needs_network=False,
        params={"days": 2, "limit": 500}),
]
