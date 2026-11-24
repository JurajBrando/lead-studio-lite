# Qualify: buying signals per offer, no model calls

Everything upstream of this job optimises for volume. Common Crawl, OSM, the contact
finder and bought lists all add rows; nothing decided whether a row deserved an email.
This job is that decision.

It reads a company's site **once**, as it is today, and answers three questions:

1. Is this a real, living business? -> the disqualifiers
2. Does it show a signal we sell to? -> 37 signals, in cohorts
3. Which offer does that signal point at? -> the cohort, refined into a bundle

No LLM, no API key, no paid data provider. One HTTP request per domain, so it is free
to run across the whole database.

## Why not the hot list?

`hot_list` fires on a **change** between two crawls (a tool disappeared, ads started).
That needs crawl history we do not have for a cold import of a million rows. This job
fires on a **state**, so a lead imported five minutes ago can be qualified immediately.
The two are complementary: qualify decides who is worth knowing, the hot list decides
when to write to them.

## Cohorts - one per offer

| Cohort | Offer | The situation | Signals |
|---|---|---|---|
| `home_service` | QuoteIQ Pro | A trade giving free estimates with no quoting software | 5 |
| `clinic_booking` | Dermis | Dental / derm / aesthetics, phone is the only way in | 4 |
| `agency_ops` | HighLevel | Agency with clients and ads, nothing capturing leads | 5 |
| `enterprise_data` | micro1 | US company, 30+, engineering or AI work | 4 |
| `industrial_ops` | Modulario | Production, ISO certification, shop-floor hiring | 3 |
| `brand_risk` | Red Points | Own-brand goods online, also on marketplaces | 4 |
| `tracking_gap` | HYROS Tracking | Ad pixels, no attribution tool | 4 |
| `paid_ads` | - | Corroboration only: a pixel proves a budget | 6 |
| `liveness` | - | Corroboration only: somebody still works here | 2 |

`paid_ads` and `liveness` add to the score but never name the offer. Ties between the
others go to the cohort that says **who the company is** over the one that says what it
does: a clinic running ads is a Dermis lead that also advertises, not an ads lead that
happens to be a clinic.

## The offer is a bundle, not a label

The cohort picks the product; what the site showed picks the version. This is what ends
up in the CSV, so the row tells you what to actually pitch:

| What the site showed | Offer written to the CSV |
|---|---|
| Trade, free estimates, no form or booking - phone only | `QuoteIQ Pro + Ataraxis` |
| Trade already running Jobber / ServiceTitan | `QuoteIQ Pro` (displacement) |
| Clinic, no booking | `Dermis` |
| Clinic, no booking, already buying ads | `Dermis + HYROS` |
| Agency advertising for itself | `HighLevel SaaS Pro + HYROS AIR` |
| Agency that white-labels or resells | `HighLevel Unlimited + HYROS AIR` |
| Ads, no attribution | `HYROS Tracking` |
| Already pays Triple Whale / Northbeam | `HYROS Tracking (displacement)` |

## Confirming what you sell

Sellbird's `signals --confirm`, stored in `data/settings.json`:

```python
from engine import qualify
qualify.confirm(["home_service", "clinic_booking", "tracking_gap", "enterprise_data"])
```

Unset means all cohorts. Signals outside the confirmed set are still recorded on the
domain, but they do not earn a lead a place in the outbox. The job's `cohorts`
parameter overrides the setting for one run - which is how you point a run at one
campaign's segment. `industrial_ops` is Modulario, your own B2B offer rather than an
affiliate one; leave it out of the confirmed set when you are working the affiliate
lists.

## Scoring

Each signal carries a weight. The score is the sum of hits inside the confirmed
cohorts; `liveness` can add at most 2 and never qualifies a lead on its own.

* **hot** - 8 or more
* **warm** - 4 to 7
* **cold** - below 4
* **disqualified** - a disqualifier hit, score 0, no scan

A cohort only gets to name the offer when it carries 4 points by itself. One stray
point is not a buying situation.

## Disqualifiers

These end the scan. They are the reason the database is full of contacts that could
never have replied.

* `parked` - parked, placeholder, "coming soon", default server page
* `thin` - under 400 characters of readable text
* `free_host` - a wixsite/weebly/blogspot subdomain
* `no_contact` - no email, no phone, no form
* `unreachable` - the site did not answer at all

## What a run writes

* `domains.score` and `domains.data.qualify` - verdict, score, cohort, offer, signals
* one `qualified` signal per hot/warm domain, so the Signals tab and Daily pick it up
* a tag on every lead behind that domain: `q:hot|warm|cold|disqualified`, plus
  `cohort:<key>` when there is one, and a `[qualify]` line in the notes
* `data/qualified/qualified_<date>.csv` - the sendable rows, hottest first, each with
  the offer to pitch and the signals that earned it

That CSV is the Manyreach import: filter by cohort, and the segment and the offer are
already decided.

## The gate

A disqualified lead gets `status='unqualified'`. That is simply not `new`, so every
existing filter, export and enrollment query skips it. **Nothing is deleted** - set the
status back and the lead is in play again. Turn it off with `park_disqualified: false`.

## Running it

Automation -> Signals -> *Qualify leads (buying signals)*. Daily at 05:30 by default.

| Parameter | Default | |
|---|---|---|
| `limit` | 200 | domains per run (not leads - a domain is read once) |
| `max_pages` | 2 | home page plus the most useful other page |
| `pause` | 0.6 | seconds between requests |
| `cohorts` | "" | override the confirmed cohorts for this run |
| `recheck_days` | 90 | how long a verdict stands before a re-read |
| `park_disqualified` | true | set `status='unqualified'` on the dead ones |
| `only_status` | new | which leads to consider |
| `respect_robots` | true | leave this on |

At 200 domains a run that is 6 000 domains a month per schedule; raise `limit` and run
it manually to work through a backlog. The cost is bandwidth, nothing else.

## Tuning

The Slovak and Czech markers (`vyroba`, `otvaracie hodiny`, `hladame`, `cenova ponuka`)
and the trade list are guesses at how the targets write. After a first run, read the
cold rows: a vertical that should have matched and did not is a missing marker, not a
missing signal. The marker tables at the top of `engine/qualify.py` are meant to be
edited.
