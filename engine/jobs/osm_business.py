"""Local businesses from OpenStreetMap, per offer, split the way each list is used next.

The scraper is `_CLEAN_2026-11-02/osm_icp_scrape.py` (Overpass API, free, no key). It writes three
files per offer:

    <OFFER>_1_WITH_EMAIL_<n>.csv      an address in OSM        -> import straight away
    <OFFER>_2_WEBSITE_NO_EMAIL_<n>.csv a site, no address      -> the site scraper's turn
    <OFFER>_3_PHONE_ONLY_<n>.csv       a phone, no site         -> a call list

The job runs it, imports the first file, and reports the size of all three. Raw answers are cached
by the scraper, so a second run the same day costs nothing.
"""
import os

from .. import external
from .base import Job, Context

LIMIT = 5000


def run(ctx):
    folder = external.root("clean")
    try:
        external.require(folder, "osm_icp_scrape.py")
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    offer = str(ctx.param("offer") or "ALL").strip().upper()
    args = ["--offer", offer]
    area = str(ctx.param("area") or "").strip().upper()
    if area:
        args += ["--area", area]
    state = str(ctx.param("state") or "").strip()
    if state:
        args += ["--state", state]
    city = str(ctx.param("city") or "").strip()
    if city:
        args += ["--city", city]
    out_dir = str(ctx.param("out_dir") or "").strip()
    if out_dir:
        args += ["--out-dir", out_dir]

    ctx.log("osm_icp_scrape.py %s" % " ".join(args))
    result = external.run(folder, "osm_icp_scrape.py", args,
                          timeout=ctx.int_param("timeout", 3600))
    for line in result["tail"].splitlines()[-20:]:
        ctx.log(line)
    if not result["ok"]:
        return {"ok": False, "error": "the OSM scraper failed (exit %s)" % result["code"],
                "seconds": result["seconds"]}

    search_dir = os.path.join(folder, out_dir) if out_dir else folder
    with_email = external.newest(search_dir, "*_1_WITH_EMAIL_*.csv")
    website = external.newest(search_dir, "*_2_WEBSITE_NO_EMAIL_*.csv")
    phone = external.newest(search_dir, "*_3_PHONE_ONLY_*.csv")
    imported = 0
    if with_email:
        answer = external.import_csv(with_email, "osm",
                                     limit=ctx.int_param("import_limit", LIMIT))
        imported = int(answer.get("imported") or 0)
        if not answer.get("ok"):
            ctx.warn("the OSM file could not be imported: %s" % answer.get("error"))

    counts = {"with_email": external.row_count(with_email),
              "website_no_email": external.row_count(website),
              "phone_only": external.row_count(phone)}
    ctx.add_signal("osm_batch", title="%s businesses from OpenStreetMap" % offer,
                   source="osm", source_url=with_email,
                   payload={"offer": offer, "area": area, "counts": counts, "imported": imported})
    ctx.log("with email %d, website only %d, phone only %d | imported %d" % (
        counts["with_email"], counts["website_no_email"], counts["phone_only"], imported))
    return ctx.summary(ok=True, imported=imported, **counts)


JOBS = [
    Job(key="osm_business",
        name="OpenStreetMap business scraper",
        group="Domains",
        description="Local businesses per offer from OpenStreetMap (free, Overpass API). Splits "
                    "them into with-email, website-only and phone-only files, imports the ones "
                    "with an address, and is cached and resumable through the scraper's own cache.",
        run=run,
        schedule="weekly mon 08:30",
        needs_network=True,
        params={"area": "SK", "offer": "ALL", "state": "", "city": "", "out_dir": "",
                "import_limit": LIMIT, "timeout": 3600}),
]
