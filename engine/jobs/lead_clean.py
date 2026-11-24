"""The one pass over the whole lead list that removes what cannot be contacted."""
from .. import lead_clean
from .base import Job


def run_clean(ctx):
    return lead_clean.run(ctx, dry_run=bool(ctx.param("dry_run", False)),
                          batch=max(100, min(5000, ctx.int_param("batch", 1000))))


JOBS = [
    Job(key="leads_clean_all",
        name="Clean the list: private and empty leads",
        group="Data",
        description="Removes leads that can never start a conversation: a consumer mailbox "
                    "(gmail, yahoo, seznam ...) with no website or own domain behind it - a "
                    "company name, an industry or a job title is not enough - an address "
                    "that is not an address, and rows with no email, company, site or phone "
                    "at all. A gmail lead that names a website stays. Removed leads are "
                    "quarantined whole - sends, replies, enrollments and signals go with "
                    "them, like the Delete button - and can be allowed back. Preview first: "
                    "it changes nothing and reports the count.",
        run=run_clean,
        schedule="",
        needs_network=False,
        params={"dry_run": False, "batch": 1000}),
]
