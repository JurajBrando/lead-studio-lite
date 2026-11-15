"""The project's bulk data on Hugging Face.

Two files are too big for the repository and too useful to leave on one machine: the
scraped contact table and the raw domain list. They are published as a public dataset;
the app links to them, and the Common Crawl job reads the CC host index from the Hub as
well.

Nothing here uploads anything - the upload was a one-off command with a write token.
"""
from urllib.parse import quote

DATASET = "JurajBrando/lead-studio-data"

FILES = (
    {"name": "rec_results.duckdb", "label": "Scraped contact table",
     "note": "25.2M rows: domain, company (biz_name), description, best_email, country. "
             "Open it in DuckDB or import a slice through Leads -> Import."},
    {"name": "domains.txt", "label": "Domain list",
     "note": "One domain per line - the raw list the monthly Common Crawl sweep draws from."},
)


def dataset_url(dataset=DATASET):
    return "https://huggingface.co/datasets/" + dataset


def file_url(name, dataset=DATASET):
    return "%s/resolve/main/%s" % (dataset_url(dataset), quote(name))


def info():
    return {"dataset": DATASET, "url": dataset_url(),
            "files": [dict(entry, url=file_url(entry["name"])) for entry in FILES]}
