"""Local businesses from OpenStreetMap, for whatever the user sells.

Native version: `engine.osm` turns a free-text offer into OSM tags, geocodes the place with
Nominatim, asks Overpass, stores the rows in `osm_business` (cached, so the same search is never
paid twice) and imports the ones that carry an email - or a website the contact finder can work on.

Each run is one search; the user can type any trade or business type, and an unknown term is
matched against the name/craft/shop tags rather than refused.
"""
from .. import osm
from .base import Job, Context

LIMIT = 200


def run(ctx):
    text = str(ctx.param("text") or ctx.param("offer") or "").strip()
    if not text:
        return {"ok": False, "error": "Type what to look for, e.g. 'roofers in Slovakia'."}
    area = str(ctx.param("area") or "").strip()
    try:
        found = osm.search(text, area=area, limit=ctx.int_param("limit", LIMIT))
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    if not found.get("ok"):
        return {"ok": False, "error": found.get("error") or "OpenStreetMap did not answer."}
    ctx.log("%s in %s: %d businesses, %d with an email, %d website only, %d phone only" % (
        found["label"], found["place"], found["total"], found["with_email"],
        found["website_only"], found["phone_only"]))
    imported = 0
    if bool(ctx.param("import", True)):
        answer = osm.import_ready(category=found["category"], area=found["area"],
                                  limit=ctx.int_param("import_limit", 5000))
        imported = int(answer.get("imported") or 0)
        if not answer.get("ok"):
            ctx.warn("the OSM rows could not be imported: %s" % answer.get("error"))
    ctx.add_signal("osm_batch", title="%s in %s" % (found["label"], found["place"]),
                   source="osm", payload={"total": found["total"], "imported": imported,
                                          "tags": found["tags"]})
    return ctx.summary(ok=True, found=found["total"], with_email=found["with_email"],
                       website_only=found["website_only"], phone_only=found["phone_only"],
                       imported=imported)


JOBS = [
    Job(key="osm_business",
        name="OpenStreetMap business scraper",
        group="Domains",
        description="Local businesses per offer from OpenStreetMap (free, no key). Type any trade "
                    "or business type and a place; the rows are cached, split into with-email, "
                    "website-only and phone-only, and the ones with a contact become leads.",
        run=run,
        schedule="weekly mon 08:30",
        needs_network=True,
        params={"text": "roofers in Slovakia", "area": "", "limit": LIMIT, "import": True,
                "import_limit": 5000}),
]
