# Lead Studio Lite

**Your own outbound desk: 25.2M contacts to start from, every mailbox in one place.**

Stop renting lead lists. Lead Studio Lite is a local-first sales development app for Windows that
runs the whole outbound loop — sourcing, enrichment, sequencing, sending and answering — on data
you own. Every lead, mailbox and reply lives in a SQLite file on your computer.

- **Start from 25.2M contacts** — the scraped contact table `rec_results.duckdb`
  (domain, company, description, best email, country) is published as the public dataset
  [Jurajbrando/lead-studio-data](https://huggingface.co/datasets/Jurajbrando/lead-studio-data).
  Open it in DuckDB, or import a slice straight into the app through *Leads -> Import*.
- **New domains keep arriving** — the weekly Common Crawl job reads the host index from the Hub
  as well, so fresh domains land in the Domains tab without any work from you.
- **Work the list, not just build it** — segment by country, industry, size and tech, verify
  every address, send multi-step sequences from as many mailboxes as you own, and read every
  reply and bounce in **one unified inbox** that stops the sequence and flips the lead.

No account, no subscription, no cloud — your list never leaves your computer.

**Python 3.9+ · MIT · ~15,000 lines · no runtime dependencies**

![Lead Studio Lite - your own outbound desk](docs/social-preview.png)

---

## Why this exists

Most outbound tools sell one slice each: a data vendor for leads, a verifier for addresses, a
sending platform for mailboxes, a separate inbox for replies — and each one keeps a copy of your
data. Lead Studio Lite is the whole loop in one folder, with the data in a file you own.

**Good fit:** freelancers, consultants, small agencies, founders doing their own outbound — anyone
running a handful of mailboxes and a few hundred to a few thousand emails a month who wants to
build the list as well as work it.
**Bad fit:** high-volume senders (10k+ a month from one mailbox), teams needing role-based shared
inbox permissions, or anyone looking for a managed data vendor.

## Features

- **Import anything** — CSV, TSV, semicolon files, Excel `.xlsx`, Parquet, DuckDB and SQLite.
  Column names are auto-mapped (`Email`, `E-mail`, `Company`, `Job Title`, `Description` …),
  duplicates skipped, invalid addresses reported. **An email is optional**: a company with a
  description, a domain or a name is a contact too, and the rows that have no address simply
  cannot be enrolled in a sequence. Big files (the multi-gigabyte kind) are imported from a path
  on disk - type it or press **Browse** for the system file window - streamed row by row with
  **no size limit**, resumable, with an optional SQL filter for DuckDB.
- **Bulk data on Hugging Face** — the scraped contact table (`rec_results.duckdb`, 25.2M rows)
  is too big for a repository, so it is published as the public dataset
  [Jurajbrando/lead-studio-data](https://huggingface.co/datasets/Jurajbrando/lead-studio-data)
  and linked from *Leads → Bulk data*. Re-publish it with `python tools/upload_bulk_data.py`
  (needs a write token).
- **Domains tab** — a catalogue of domains separate from leads. Pull a slice from three open
  datasets on Hugging Face — Common Crawl web graph (370M, ranked), registered domains (~180M),
  daily Certificate Transparency — enrich any of them against the live Common Crawl index, or
  scrape the site for contact details and add what it finds as a lead. The weekly Common Crawl
  job also feeds it from the CC host index, and **Import lead domains** pulls the domain of every
  lead you already have — with an email or with only a domain.
- **Segment the list** — filter leads by **country, job title, industries** (any of, or must-have
  all of), **company size, location, technologies and source**, then sort by company, country,
  job title or size. Country names are normalised on import and at query time, so `US`, `USA` and
  `United States of America` are one country.
- **Verify before you send** — syntax, typo domains, disposable providers, role accounts (`info@`),
  MX records (`dnspython` → `nslookup` fallback), optional live SMTP probe.
- **Sequences** — unlimited steps, per-step delays, merge fields with fallbacks
  (`{{first_name|there}}`), automatic stop on reply.
- **Many mailboxes, one queue, one inbox** — every active mailbox is in the rotation: a send goes
  to the mailbox with room under its daily cap that has been quiet longest, and one at its limit is
  skipped instead of stalling the queue. Replies and bounces from all of them are collected into
  one list, filterable per mailbox, each row showing which mailbox it came from.
- **Sends from your inbox** — SMTP with STARTTLS/SSL, sending window, gap + jitter between messages,
  weekday-only mode, per-mailbox daily cap.
- **Reads your replies** — IMAP polling on every mailbox marks replies and bounces, stops
  sequences, updates the lead, and shows them all in the unified inbox.
- **Local pipeline** — new → contacted → replied → interested → won / lost, plus unsubscribe and
  bounce suppression.
- **Open/click tracking** and **unsubscribe links** (optional; off unless you set a public URL).
- **Daily automation** — scheduled jobs that keep the database fresh: new domains, MX verification,
  US bankruptcy filings, weekly Google Ads Transparency, hiring signals, Common Crawl footprint,
  phishing/fraud watch, and a merge job that folds dropped CSV files into your leads.
- **Contact crawler** — turns a domain into a real lead: reads the site (robots-aware), follows the
  contact/about/team pages, de-obfuscates addresses and ranks them.
- **Optional connectors** — Hunter.io (addresses behind a domain), any OpenAI-compatible AI endpoint
  (segment + angle), Serper (company → website). Off until you add a key.
- **Offers & affiliate links** — add the tools you resell (accounting, ads, social, referrals) with
  their link, keywords and pitch template; leads are matched to the right offer and the email is
  drafted with your link in it. Per-offer connections (link / API key / MCP / webhook), generated
  offer pages with an affiliate disclosure, and a hard-coded, read-only Upgrades shelf of 29
  affiliate tools that survives a database reset.
  See [docs/OFFERS.md](docs/OFFERS.md).
- **Bring your own LLM** — OpenAI, Anthropic (Claude), DeepSeek, Ollama or any OpenAI-compatible
  endpoint, managed as a list with a default and a test button. Providers that share a base URL
  form a **pool**: calls rotate through the keys and fall back to the next one when a key is out
  of quota.
- **Brand watch** — monitor any number of business domains for lookalikes, typosquats and scam
  clones: registered lookalike names, new registrations and phishing listings, with a per-brand case
  file and CSV export. See [docs/BRAND_WATCH.md](docs/BRAND_WATCH.md).
- **Ads hub** — a page with a sub-tab per platform: Metricool Ads (the ad accounts it already
  reads), HYROS Ad Tracking (`API-Key`), PLAI and DataFast (`Bearer`). Each keeps its own key and
  base URL, has a *Test* and a raw *Pull*. **HYROS AIR Sales Agent** gets its own tab as the client
  for Hyros' paid add-on: it reads the leads, sales and calls behind your HYROS key and states
  plainly that AIR itself runs at Hyros.
- **MCP server** — `leadstudio_mcp.py` lets Claude Desktop or any MCP client search leads, import
  CSVs, run jobs and read signals. See [docs/MCP.md](docs/MCP.md).
- **MCP connections** — the app is an MCP *client* too: Settings → *MCP connections* connects a
  remote server over OAuth (dynamic client registration + PKCE) or with a token, lists its tools,
  and **downloads** what it declares — Manyreach's campaigns and senders land in `mcp_items` and
  are shown in the card. Any other MCP server can be added with a URL and an optional token.
  See [docs/MCP.md](docs/MCP.md#the-other-direction-mcp-servers-this-app-calls).
- **Manyreach REST API** — the same card takes a Manyreach API key (`X-API-Key`, 1000 rows a
  page) and syncs the whole account: every campaign and every sender, not the first page. The
  campaign and sender data can then be **pushed into the app itself** — each sender becomes a
  mailbox (with its SMTP/IMAP host, port, daily limit and signature, inactive until you add the
  password) and each campaign becomes a one-step sequence marked `[MR]`.
- **Social tab** — make a short video from a title, an audio track and one picture (or a source
  video), then plan, schedule and measure it through Metricool: a unified inbox, a content
  calendar, the Meta/Google/TikTok ad panels and the reporting cards. See [docs/SOCIAL.md](docs/SOCIAL.md).
- **Demo mode** — 24 sample leads and two sequences to explore before touching real data.

| Leads | Sequences |
| --- | --- |
| ![Leads](docs/screenshots/leads.png) | ![Sequences](docs/screenshots/sequences.png) |
| ![Unified inbox](docs/screenshots/inbox.png) | ![Mailboxes](docs/screenshots/mailboxes.png) |

## Download and run

### Option A — from this repository

```bash
git clone https://github.com/<your-account>/lead-studio-lite.git
cd lead-studio-lite
```

Double-click **`START_LEAD_STUDIO.bat`** (or run `python leadstudio.py`). Lead Studio opens in
**its own window** (pywebview + WebView2); if pywebview is not installed it opens in your browser at
`http://127.0.0.1:8791` instead. `--browser` and `--window` force either one, `--no-browser` keeps it
headless. Keep the console window open; closing it stops the app.

Nothing to install: the app is standard-library Python only. Optional extras: `pywebview` (own
window), `openpyxl` (Excel import), `pyarrow`/`duckdb` (Parquet and DuckDB import/export).

### Option B — the standalone `.exe`

Every tagged release attaches `LeadStudio.exe` (and a zip with this README, the docs and the
executable). No Python needed — put it in its own folder and run it; it creates a `data` folder next
to itself.

> Windows SmartScreen may warn about an unsigned executable: *More info → Run anyway*.

### Option C — build it yourself

`BUILD_EXE.bat` runs PyInstaller and produces `dist\LeadStudio.exe`. Requires
`pip install pyinstaller`.

## First run (5 minutes)

1. **Connect a mailbox** — *Mailboxes → Add mailbox*. SMTP for sending, IMAP for replies. Use an
   **app password**, not your account password. Press *Test connection*, then *Send test email*.
2. **Import leads** — *Leads → Import*. Drop a CSV (or Parquet/DuckDB/Excel), or point the
   *File on this computer* tab at a big file and cap the rows. An email column helps but is not
   required; `samples/leads-sample.csv` shows the columns it recognises.
3. **Verify** — one button; red addresses never reach your sequence.
4. **Build a sequence** — day 0 intro, day 3 follow-up, day 7 close. Two or three short emails beat
   one long one.
5. **Enroll** — the queue sends one email at a time, inside your window, under your daily cap.

Full walkthrough, provider settings (Gmail, Outlook, Zoho, Seznam, Websupport), sequence copy tips
and a troubleshooting table: **[docs/GETTING_STARTED.md](docs/GETTING_STARTED.md)**.

## Daily automation

The **Automation** page runs enrichment jobs on a schedule while the app is open. Each one pulls a
public source and writes what it finds into the `domains` and `signals` tables — nothing is emailed
by a job.

| Job | Default | Source |
| --- | --- | --- |
| New domains | daily 06:00 | smet.cz NRD feed (CC BY 4.0) |
| Domain MX verification | daily 04:30 | DNS |
| Bankruptcy & insolvency watch | daily 07:00 | SEC EDGAR 8-K Item 1.03 (UK Gazette / omni as options) |
| Google Ads Transparency | weekly Mon 07:30 | HuggingFace `najeh-halawani/google-ads-transparency` |
| Hiring signals | daily 06:45 | HuggingFace `cleanfeeds/tech-jobs-daily` |
| Common Crawl footprint | daily 03:00 | index.commoncrawl.org |
| Common Crawl host index | weekly Sun 04:30 | HuggingFace `commoncrawl/host-index-testing-v2` |
| Phishing / fraud watch | daily 05:30 | Phishing.Database |
| Merge files into leads | daily 02:00 | your `data/imports/` folder |
| Brand watch | daily 08:00 | DNS + new-domain feed + phishing lists |

Signals are evidence, not contacts, and the **Daily** page (first item in the sidebar) is where they
become work. Every finding is scored against the offers you sell — the offer keywords are looked up
in the company, title, kind, domain and payload — so the list arrives sorted by *fit* with the offer
that matches named, and *Only findings that match* hides the rest. From a finding you can:

- **Email** — the message is written from that offer's pitch (merge fields, affiliate link and
  disclosure), you pick the mailbox, and it goes out inside that mailbox's daily quota; the lead is
  created and flips to contacted.
- **Campaign** — the finding becomes a lead and enters a sequence at step one.
- **To lead** — just the contact record, with the finding attached as a note.

A bankruptcy signal additionally flags existing leads so you never pitch a company that just filed.

Schedules, parameters and on/off switches live on the Automation page. Details, licences and how to
write your own job: **[docs/AUTOMATION.md](docs/AUTOMATION.md)**.

![Settings](docs/screenshots/settings.png)

## What it does not do

- No email warm-up and no deliverability guarantee. A fresh mailbox that sends 100 cold emails on
  day one lands in spam; start at 10–20 a day and raise it slowly.
- No lead scraping, enrichment or buying data.
- No multi-user, no syncing, no cloud backup.
- Does not bypass provider limits — Gmail and Outlook cap daily sending and will lock the account.

Cold email is regulated (GDPR, CAN-SPAM, PECR and local equivalents). The app gives you unsubscribe
links and a suppression list; the legal basis, the list's provenance and the content are yours.
Read [docs/PRIVACY.md](docs/PRIVACY.md) before sending to real people.

## How it works

```
leadstudio.py            entry point, CLI flags
engine/config.py         paths, defaults, settings file
engine/db.py             SQLite schema (leads, sequences, sends, replies, suppressions)
engine/mail.py           SMTP send, IMAP read, template merge, HTML rendering
engine/verify.py         email verification (no required third-party package)
engine/importer.py       CSV/Excel import, column mapping, CSV export
engine/pipeline.py       the sending queue, reply matching, statistics
engine/scheduler.py      job scheduling and run history
engine/net.py            the HTTP layer used by the jobs (gzip, retries, parsers)
engine/jobs/             one module per automation job
engine/server.py         local HTTP API, static files, tracking pixel/redirect
engine/video.py          the Social composer: uploads, the render spec, the task
engine/video_maker.py    the renderer itself (Pillow cover frame + ffmpeg), a child process
engine/metricool.py      the Metricool MCP connection, tools and the section mapping
engine/demo.py           demo dataset
ui/                      single-page interface — plain HTML/CSS/JS, no build step
tests/                   end-to-end tests (fake SMTP server, temp database)
```

The server binds to `127.0.0.1` only and requires a per-run session token on every write, so a
random website you have open cannot poke your local app.

Your data lives in `data/` (git-ignored): `leadstudio.db`, `settings.json` with mailbox credentials
in plain text on your machine, and `logs/app.log`. Delete the folder to remove every trace.

## Development

```bash
python leadstudio.py                  # start
python leadstudio.py --no-browser     # don't open a browser
python leadstudio.py --seed-demo      # start with demo data
python leadstudio.py --port 9000      # different port
python -m unittest discover -s tests -v
```

The test suite spins up a fake SMTP server and a throwaway database — no real email leaves the
machine, and the job tests never touch the network. CI runs it on Windows for Python 3.9 and 3.12
(`.github/workflows/ci.yml`).

## Releases

`git tag v1.0.0 && git push --tags` triggers `.github/workflows/release.yml`, which runs the tests,
builds `LeadStudio.exe` with PyInstaller, and publishes a GitHub Release with:

- `LeadStudio.exe` — standalone Windows executable
- `LeadStudioLite-<tag>-windows.zip` — executable + README + docs
- `LeadStudioLite-<tag>-source.zip` — the source tree

To build the same zip locally: `python tools/package_release.py`.

Step-by-step instructions for GitHub, your own site, Gumroad and other download pages:
**[docs/PUBLISHING.md](docs/PUBLISHING.md)**.

## Repo layout extras

- `samples/leads-sample.csv` — example import file
- `docs/` — getting started, automation, brand watch, offers, connectors, MCP, privacy, publishing
- `marketing/index.html` — optional one-page product site (self-contained, no external assets)

## Contributing

Issues and pull requests are welcome. Keep the app dependency-free: if a feature needs a package,
put it behind an optional import with a graceful fallback. Run the test suite before opening a PR.

## Legal notice

Lead Studio Lite is an independent project. It is **not affiliated with, endorsed by or sponsored
by Metricool** or any other service it connects to.

The Metricool links in this app — the *Get Metricool* button and every *Connect* / *Open* link in
the Social tab — are **affiliate links** (`https://my.mtr.cool/NEMGNE`). If you open a Metricool
account through one, this project may earn a commission at no extra cost to you. Metricool's own
terms and privacy policy apply to your account.

The app is provided as is, under the MIT licence, without warranty. You are responsible for the
messages you send and for the data you import — cold email is regulated (GDPR, CAN-SPAM, PECR and
local equivalents). See [docs/PRIVACY.md](docs/PRIVACY.md).

## License

MIT — see [LICENSE](LICENSE). Use it, rebrand it, give it away.
