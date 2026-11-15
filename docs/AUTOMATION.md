# Daily automation

Lead Studio Lite can run a set of enrichment jobs on a schedule while the app is
open. Each job pulls one public source, writes what it finds into the `domains`
and `signals` tables, and shows up on the **Automation** and **Signals** pages.

Nothing is emailed by a job. Jobs only *find and enrich* - sending stays behind
the queue, the daily caps and the unsubscribe list you already configured.

---

## How scheduling works

| Field | Meaning |
| --- | --- |
| `daily 06:30` | every day at 06:30 local time |
| `weekly mon 07:30` | Mondays at 07:30 local time |
| empty | manual only - run it from the Automation page |

- Jobs only fire while the app is running. A closed app is a paused scheduler, not a missed one:
  on the next start, any job whose time has passed runs once.
- Jobs run one at a time, so two downloads never fight for the same file or database.
- Every run is recorded with its full log (Automation → *Last log*), and a failed run never
  stops the others.
- Change the schedule, the parameters, or switch a job off on the Automation page. These are
  stored in the database, so they survive restarts and upgrades.

## The jobs

| Job | Default | Source | What it does |
| --- | --- | --- | --- |
| **New domains: daily feed** | daily 06:00 | [smet.cz](https://smet.cz/) NRD feed, CC BY 4.0 | Today's newly registered domains, filtered by TLD and keywords, written to `domains` for the other jobs to enrich. |
| **Domain MX verification** | daily 04:30 | DNS | One MX lookup per domain, cached. Domains with no mail server are flagged `dead` so no address on them is ever emailed. |
| **Bankruptcy & insolvency watch** | daily 07:00 | SEC EDGAR 8-K Item 1.03 (default), UK Gazette, or omni filings | Companies that filed for bankruptcy or receivership. Matches debtors against your leads and flags them. Three providers: `sec` (structured JSON, no key), `gazette` (UK notices, Atom), `omni` (richest columns but bot-protected - often blocked for scripted clients). |
| **Google Ads Transparency** | weekly Mon 07:30 | HuggingFace `najeh-halawani/google-ads-transparency` | Last week's EU advertisers, filtered by your keywords, regions and minimum impressions. Companies spending on ads are companies with budget. |
| **Hiring signals** | daily 06:45 | HuggingFace `cleanfeeds/tech-jobs-daily` | The newest day of job postings; companies hiring the titles you care about become signals. |
| **Common Crawl: domain footprint** | daily 03:00 | [index.commoncrawl.org](https://index.commoncrawl.org/) | For each domain: how many pages Common Crawl has, and the last crawl date. A cheap "is this a real site" check without touching the site. |
| **Common Crawl: new domains from the host index** | weekly Sun 04:30 | HuggingFace `commoncrawl/host-index-testing-v2` | The other direction: reads a shard of the CC host index (streamed with range reads), takes each host's registered domain and adds the ones you do not have yet to `domains`, with their fetch statistics in `data`. The mirror lags the official index by months, which is why the daily footprint check still runs. |
| **Fraud watch: phishing domains** | daily 05:30 | [Phishing.Database](https://github.com/Phishing-Database/Phishing.Database) | New phishing domains, plus impersonation checks against your own leads' brands. |
| **Merge files into leads** | daily 02:00 | local folder | Folds every CSV/TSV/Excel in `data/imports/` into the lead database, fills empty fields on existing leads, then moves the files to `processed/`. |
| **Brand watch** | daily 08:00 | DNS + new-domain feed + phishing lists | Builds lookalikes of every monitored brand, checks which are registered and accepting mail, and cross-checks the new-domain and phishing feeds. Rotates through the brand list, oldest scan first. See [BRAND_WATCH.md](BRAND_WATCH.md). |

All of them are **off-switchable** and **parameter-editable** from the Automation page.
Common parameters: `limit` (how many rows per run), `days` (refresh window), `keywords`.

**A note on size:** the Google Ads Transparency job downloads the newest weekly aggregation, which is
roughly 250 MB across its two gzip parts — the first run takes a few minutes on a normal connection.
It is weekly for that reason. Everything else in the list is small (kilobytes to a few megabytes).

## Signals vs leads

A signal is evidence, not a contact. It has a company or domain, a kind, a source URL and a
payload - but usually no email address. That is deliberate: you decide when a signal is worth
a lead. Use **To lead** on any signal to create the lead (you supply the address) and the finding
is attached to the lead as a note.

Typical rhythms:

- **New domains** → MX job marks the dead ones → Common Crawl confirms the live ones → you look at
  the survivors and decide which to research.
- **Hiring / Ads** → companies with budget and a gap → match against your offer, then find the
  human and add them as a lead manually.
- **Bankruptcy** → you want this mostly for *removal*: if a prospect filed, they stop getting
  sales emails. The job also flags them on the lead so you never pitch a company in Chapter 11.
  SEC EDGAR is the reliable provider; the omni filings table has better columns (district, attorney,
  asset and liability bands) but its host blocks scripted clients from many networks, so it is an
  option rather than the default.
- **Phishing** → brand protection: a lookalike domain using a client's name is a conversation
  starter (and a warning worth sending them).

## Privacy, licences and good manners

- Every source is public. No scraping behind a login, no paid APIs, no personal data brokers.
- **smet.cz** requires attribution under CC BY 4.0. The app records the attribution in the run
  summary; keep it if you publish anything derived from that data.
- **HuggingFace datasets** are CC BY 4.0 / MIT as noted on their cards; credit the dataset authors
  if you surface their rows publicly.
- **Common Crawl** asks for a polite rate; the job sleeps `delay` seconds between domains.
- **Phishing.Database** is a public list; it is evidence of a domain being *reported*, not proof.
- **Bankruptcy filings** are public court records. Never email a debtor about their own filing -
  that is where the legal risk is, not in reading the record.
- The network layer sends an honest user agent. If you need SEC EDGAR or another API that asks for
  a contact address, put it in Settings → *Contact email* and it is appended to the user agent.

## Writing your own job

Create `engine/jobs/mine.py`:

```python
from .. import net
from .base import Job

def run(ctx):
    data = net.text("https://example.com/feed.csv")
    for row in net.csv_rows(data)[1][: ctx.int_param("limit", 100)]:
        ctx.add_signal("mine", domain=row.get("domain"), company=row.get("company"),
                       source="example", source_url="https://example.com/feed.csv", payload=row)
    return ctx.summary(rows=len(data))

JOBS = [Job(key="my_job", name="My job", group="Custom",
            description="What it does in one sentence.",
            run=run, schedule="daily 08:00", params={"limit": 100})]
```

Then add `"mine"` to `MODULES` in `engine/jobs/__init__.py`. The scheduler picks it up on the next
start: it creates the row, schedules it, and the Automation page can run it.
