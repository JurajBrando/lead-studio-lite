"""Common Crawl: bulk domain match against the columnar index itself.

The per-domain footprint job asks the CDX endpoint once per domain - fine for a handful,
hopeless for thousands. This job reads the index shards (Hugging Face `open-index/ccrawl-urls`,
or a local copy of the same table) and matches a batch of domains per run.
"""
from .. import cc_index
from .base import Job, Context

LIMIT = 25


def run(ctx):
    limit = ctx.int_param("limit", LIMIT)
    local_dir = (ctx.param("path") or "").strip()
    source = (ctx.param("source") or "bucket").strip()
    crawl = (ctx.param("crawl") or "").strip()
    domains = cc_index.pending(limit)
    if not domains:
        return ctx.summary(ok=True, checked=0, found=0,
                           note="every domain already has an index lookup")
    ctx.log("matching %d domains against the columnar index" % len(domains))
    try:
        if local_dir:
            bands = local_bands(local_dir)
        else:
            bands = cc_index.load_bands(crawl=crawl, source=source)
        results = cc_index.match(domains, local_dir=local_dir, bands=bands, source=source)
    except Exception as exc:                     # noqa: BLE001 - surfaced as a job error
        return {"ok": False, "error": "The index could not be read: %s" % exc}
    found = sum(1 for entry in results.values() if entry.get("found"))
    written = cc_index.store(results)
    ctx.log("%d of %d domains found, %d rows written" % (found, len(domains), written))
    return ctx.summary(ok=True, checked=len(domains), found=found)


def local_bands(folder):
    """The band of every local shard, read from its footer (300 shards take ~0.3s)."""
    import glob
    import os

    import pyarrow.parquet as pq

    bands = []
    for path in sorted(glob.glob(os.path.join(folder, "*.parquet"))):
        parquet = pq.ParquetFile(path)
        index = parquet.schema_arrow.get_field_index("url_surtkey")
        stats = parquet.metadata.row_group(0).column(index).statistics
        bands.append({"path": path, "key_low": (stats.min or "")[:200] if stats else "",
                      "key_high": (stats.max or "")[:200] if stats else "",
                      "rows": parquet.metadata.num_rows})
    return bands


JOBS = [
    Job(key="cc_bulk",
        name="Common Crawl: bulk domain match (index)",
        group="Domains",
        description="Reads the Common Crawl columnar index itself - the shards published on "
                    "Hugging Face as open-index/ccrawl-urls, or a local copy with the same schema "
                    "- and matches a batch of domains per run. Each shard covers a narrow band of "
                    "the URL sort key, so only the shards that can hold the batch are read.",
        run=run,
        schedule="daily 03:30",
        needs_network=True,
        params={"limit": LIMIT, "path": "", "source": "bucket", "crawl": ""}),
]
