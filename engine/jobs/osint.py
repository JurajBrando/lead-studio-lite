"""The OSINT jobs: fake-shop radar, Meta Ad Library prospects, bare-domain profiling."""
from .. import dorks, osint
from .base import Job, Context


def _not_set_up(ctx, what):
    """A scheduled job with nothing to work on is skipped, not failed.

    These jobs ship switched on with empty parameters; raising here put a traceback in the
    log and a red "error" on the Automation page every day until someone filled them in.
    """
    note = "Skipped - not set up yet. Give this job %s under its parameters." % what
    ctx.log(note)
    return ctx.summary(ok=True, skipped=True, note=note)


def run_radar(ctx):
    source = str(ctx.param("source") or "").strip()
    brands = str(ctx.param("brands") or "").strip()
    if not source:
        return _not_set_up(ctx, "a domain feed in 'source' (a domains-monitor zip, a text file "
                                "of domains, or a URL)")
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


def run_dorks(ctx):
    if not str(ctx.param("brands") or "").strip():
        return _not_set_up(ctx, "at least one brand in 'brands'")
    answer = dorks.sweep_brands(str(ctx.param("brands") or ""),
                                own_domains=[d for d in str(ctx.param("own") or "").split(",") if d.strip()],
                                per_brand=ctx.int_param("per_brand", 5),
                                limit=ctx.int_param("limit", 200))
    for row in answer["candidates"][:10]:
        ctx.log("  %s <- %s" % (row["domain"], row["query"]))
    return ctx.summary(ok=answer["ok"], brands=answer["brands"],
                       candidates=len(answer["candidates"]), error=answer["error"])


def run_discover(ctx):
    brands = [" ".join(line.split()) for line in str(ctx.param("brands") or "").splitlines() if line.strip()]
    if not brands:
        return _not_set_up(ctx, "at least one brand in 'brands'")
    total, errors = 0, []
    for brand in brands[:5]:
        try:
            total += osint.discover(brand, limit=ctx.int_param("limit", 200))["count"]
        except ValueError as exc:
            errors.append(str(exc))
    ctx.log("%d brands, %d certificate candidates" % (len(brands), total))
    return ctx.summary(ok=bool(brands), candidates=total, error="; ".join(errors[:2]))


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
    Job(key="dork_sweep",
        name="Dork sweep (Serper)",
        group="Signals",
        description="Runs Google dorks for a brand - official store, shop now, outlet, "
                    "site:myshopify.com - through Serper and stores every other domain as a "
                    "fake-shop candidate.",
        run=run_dorks,
        schedule="weekly mon 09:30",
        needs_network=True,
        params={"brands": "", "own": "", "per_brand": 5, "limit": 200}),
    Job(key="fake_shop_discover",
        name="Certificate discovery (crt.sh)",
        group="Signals",
        description="Asks certificate transparency which domains ever got a certificate with the "
                    "brand in the name, before any scam feed lists them.",
        run=run_discover,
        schedule="daily 06:40",
        needs_network=True,
        params={"brands": "", "limit": 200}),
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
