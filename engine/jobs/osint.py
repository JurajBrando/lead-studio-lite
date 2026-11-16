"""The OSINT jobs: fake-shop radar, Meta Ad Library prospects, bare-domain profiling."""
from .. import osint
from .base import Job, Context


def run_radar(ctx):
    source = str(ctx.param("source") or "").strip()
    brands = str(ctx.param("brands") or "").strip()
    answer = osint.scan(source, brands_text=brands, limit=ctx.int_param("limit", 5000))
    ctx.log("%d domains scanned against %d brands: %d fake-shop hits" % (
        answer["scanned"], answer["brands"], answer["hit_count"]))
    for hit in answer["hits"][:10]:
        ctx.log("  %s ~ %s (%s)" % (hit["domain"], hit["brand"], hit["why"]))
    return ctx.summary(ok=True, scanned=answer["scanned"], hits=answer["hit_count"])


def run_meta(ctx):
    answer = osint.meta_ads(str(ctx.param("keywords") or "shopify store"),
                            country=str(ctx.param("country") or ""),
                            limit=ctx.int_param("limit", 25))
    for row in answer["advertisers"][:10]:
        ctx.log("  %s %s" % (row["page"], row["page_url"]))
    return ctx.summary(ok=answer["ok"], found=answer["found"], error=answer["error"])


def run_profile(ctx):
    answer = osint.profile_new(limit=ctx.int_param("limit", 20))
    ctx.log("%d domains profiled" % answer["profiled"])
    return ctx.summary(ok=True, profiled=answer["profiled"])


JOBS = [
    Job(key="scam_radar",
        name="Fake-shop radar",
        group="Signals",
        description="Checks a domains-monitor feed (zip, txt or URL) against the brands you "
                    "protect and stores every fake shop it recognises.",
        run=run_radar,
        schedule="daily 06:20",
        needs_network=True,
        params={"source": "", "brands": "", "limit": 5000}),
    Job(key="meta_ads",
        name="Meta Ad Library prospects",
        group="Signals",
        description="Advertisers running Meta ads for your offer keywords - the HYROS ICP, free "
                    "and without a key.",
        run=run_meta,
        schedule="weekly mon 09:00",
        needs_network=True,
        params={"keywords": "shopify store, ecommerce, skincare, supplements", "country": "SK",
                "limit": 25}),
    Job(key="site_profile",
        name="Profile bare domains",
        group="Data",
        description="Visits leads that have a domain but no email, works out what the business "
                    "is, and keeps its title, description, emails and phones.",
        run=run_profile,
        schedule="daily 11:00",
        needs_network=True,
        params={"limit": 20}),
]
