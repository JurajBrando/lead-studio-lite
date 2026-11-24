"""Job registry.

Each module exposes `JOBS = [Job(...)]`. Modules are imported defensively so a
broken or missing job file never stops the app from starting.
"""
import importlib

from .base import Context, Job  # noqa: F401  (re-exported for job modules)

MODULES = ("nrd", "mx", "bankruptcy", "merge", "consolidate", "ads", "hiring", "cc", "cc_hf", "cc_bulk", "cc_contacts", "phishing",
           "contacts", "ai_enrich", "brand_watch", "qualify",
           "pipeline_run", "osm_business", "hot_list", "osint", "leadscm", "domain_bulk",
           "lead_clean", "mailboxes")

_cache = None


def all_jobs():
    global _cache
    if _cache is not None:
        return _cache
    jobs = []
    for name in MODULES:
        try:
            module = importlib.import_module("engine.jobs." + name)
        except Exception as exc:                     # noqa: BLE001
            print("  [jobs] module %s unavailable: %s" % (name, exc))
            continue
        for job in getattr(module, "JOBS", []):
            jobs.append(job)
    _cache = jobs
    return jobs


def by_key(key):
    return next((j for j in all_jobs() if j.key == key), None)
