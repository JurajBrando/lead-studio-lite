## [1.22.0] - 2026-11-15

A light design, the affiliate link on HYROS, and campaigns as a table.

### Added

- **Light design.** A toggle at the bottom of the sidebar switches the whole app between the dark
  design and a light one (`html[data-theme="light"]` swaps the palette, including the sidebar, the
  tables, the inputs and the code blocks). The choice is remembered in `localStorage`, so a reload
  keeps it. The light theme is one palette block in `ui/styles.css`, not a second stylesheet.
- **Campaigns table.** The Sequences page is now a table like a sending platform: a toolbar with
  **All Folders / All Tags / All Statuses**, a search box and *Settings*, then one row per
  campaign — name, status, date, senders and steps, and the numbers
  **prospects · Reached · opened · clicked · resp. · inter. · opp. · 24h**, with *Enroll / Open /
  Delete* at the end. Opened, clicked and replied are shown as a percentage of Reached, opp. as won
  over prospects, and 24h counts the sends of the last day.
- `/api/state` now carries those per-campaign metrics (nine aggregates over `enrollments`, `sends`,
  `replies` and `leads`) plus a derived status: running, paused, draft or finished.

### Changed

- The **Hyros Enterprise** button uses the HYROS affiliate link from the Toolbox (offers table)
  instead of a plain link, marked `rel="noopener sponsored"` with a one-line disclosure under the
  callout. Falls back to the plain page when no HYROS offer has a link.

### Tests

416 (unchanged count; the API shape tests still pass with the extra fields).

## [1.21.0] - 2026-11-15

Hyros, built on the published API instead of guesses.

### Added

- **Two documented Hyros calls** on the Ads → HYROS Ad Tracking tab: *Ad accounts*
  (`GET /api/v1.0/ad-accounts`, the IDs the attribution report needs) and *Attribution · 30 days*
  (`GET /api/v1.0/attribution?attributionModel=last_click&…&fields=sales,revenue,cost,roas`), both
  rendered as tables with the raw JSON beside them. `engine/ads.py` builds the window itself.
- **The official Hyros MCP server**: one button registers `https://mcp.hyros.com/mcp` as an MCP
  connection with the same API key as a bearer token, so the tools can be listed and run from
  Settings → MCP connections.
- Links to [api-docs.hyros.com](https://api-docs.hyros.com/), [docs.hyros.com](https://docs.hyros.com/)
  and the Enterprise page on the HYROS AIR tab, plus `ad-accounts` in the collections it can pull.

### Fixed

- **The Hyros base URL is `https://api.hyros.com/v1`, not `/v1`-less.** The docs' OpenAPI omits a
  `servers:` block and some third-party notes drop the `/v1`, but probed live the real path
  `/v1/api/v1.0/leads` answers `401 Api key not valid` while `/api/v1.0/leads` is a Tomcat `404`.
  The client now uses the path that exists, and a test pins it.

### Tests

416 (was 412): the documented ad-accounts and attribution URLs with their query, the `result`
envelope, and the base URL that answers.

## [1.20.0] - 2026-11-15

An Ads tab at the bottom, and HYROS AIR as the paid service it is.

### Added

- **Ads** in the sidebar, under Social — one page, a sub-tab per platform:
  - **Metricool Ads**: the ad-account pane from Social, reused.
  - **HYROS Ad Tracking**: `https://api.hyros.com/v1` with the `API-Key` header.
  - **PLAI**: its API access is per account, so the base URL is yours to paste.
  - **DataFast**: `https://datafa.st/api` with a `Bearer` token.
  Each API tab keeps its own key, base URL and optional path in `data/settings.json`, offers
  *Test* and *Pull*, and shows the raw JSON that came back. No endpoint is guessed.
- **HYROS AIR Sales Agent** in the sidebar, below Ads: a **client** for the paid Hyros add-on.
  The page states that AIR is sold by Hyros and runs there, and reads the leads, sales and calls
  behind your HYROS API key (the same key as the Ad Tracking tab) with pull buttons and a table.
  The paths come from the Hyros API and are editable if they change.
- `/api/ads`, `/api/ads/save|test|pull`, `/api/hyros`, `/api/hyros/pull|paths`; `engine/ads.py`
  and `engine/hyros_air.py`.

### Removed

- The local "sales agent" that briefly lived in this branch: it answered with your own LLM
  provider (OpenRouter while developing), which is not HYROS AIR and must not be presented as it.
  The tab now reads the real service instead.

### Tests

412 (was 394): the four platforms, a key that is stored but never returned, the HYROS `API-Key`
header versus the DataFast bearer, HTTP errors, a platform with no base URL, and the HYROS AIR
client refusing to pull without a key or with an unknown collection.

## [1.19.0] - 2026-11-15

What you download lands in the app: senders become mailboxes, campaigns become sequences.

### Added

- **Manyreach senders → Mailboxes.** One mailbox per sender, with its SMTP and IMAP host, port,
  username, daily limit and signature, marked `source='manyreach'`. The API never returns a
  password, so those mailboxes are created **inactive** — visible and editable, but skipped by the
  send queue and the rotation — until you paste the password; a sender Manyreach reports as
  disconnected is never activated.
- **Manyreach campaigns → Sequences.** Each campaign becomes a `[MR] <name>` sequence with one step
  holding its subject and its body, with the HTML flattened to text.
- The import runs automatically after a sync (`POST /api/manyreach/sync`, and `/api/mcp/pull` for
  the MCP path); `POST /api/manyreach/import` re-runs it for data already downloaded. The card
  reports how many mailboxes and sequences came from Manyreach and how many still need a password.
- **Spintax.** `{|Hi|Hello|}` is resolved to one option when the message is merged, so imported
  Manyreach subjects and bodies no longer go out with the braces intact.

### Tests

394 (was 390): a sender becoming a mailbox with its servers, an inactive mailbox without a password,
a disconnected sender, a campaign becoming a one-step sequence with text rather than HTML, a second
import adding nothing, an existing address left untouched, and spintax both ways.

## [1.18.0] - 2026-11-15

Daily moved to the top, and wired to what you sell.

### Changed

- **Daily is the first item in the sidebar**, above Dashboard, and it opens on the findings feed —
  Automation, Brands and Match are the tabs beside it — instead of on the job list.
- Every finding is **scored against your offers**: the offer keywords are looked up in the company,
  title, kind, domain and payload, giving a fit percentage, the offer that fits and the words that
  matched. A select targets one offer or all of them; *Only findings that match* hides the rest;
  the list is sorted best fit first.

### Added

- **Email** on a finding: the message is written from that offer's pitch (merge fields, affiliate
  link and disclosure), shown in a modal with the recipient, the sending mailbox and its remaining
  daily quota, and sent from a real mailbox — recorded as a send, and the lead flips to contacted.
- **Campaign** on a finding: it becomes a lead and enters the chosen sequence at step one.
- **To lead** keeps the simple path: the finding becomes a lead with the finding attached as a note.
- `engine/daily.py` — `score`, `feed`, `promote`, `draft`, `to_campaign`, `contact` — plus
  `/api/signals/draft`, `/api/signals/campaign` and `/api/signals/contact`. `/api/signals` now
  returns the fit, the matching offer and the offers to choose from.

### Tests

384 (was 373): a matching and a non-matching score, feed ordering and the `min_fit` filter,
promote with and without a domain, the draft written from the offer pitch, enrolment into a
sequence, a real contact recorded as a send, and a mailbox at its cap refusing.

## [1.17.0] - 2026-11-15

Several inboxes, really: the queue spreads over them and the inbox merges them.

### Added

- **The inbox is one list for every mailbox.** `/api/inbox` joins the mailbox a reply came from,
  returns per-mailbox totals and unread counts, and takes `mailbox_id` to filter. The Inbox page
  gained a mailbox select (All mailboxes, then each with its unread count and total), a mailbox
  chip on every row, and an empty state that points at *Mailboxes* when none is connected yet.
- **The send queue rotates.** A send now goes to the mailbox with room under its daily limit that
  has been quiet longest; a mailbox at its cap is skipped instead of stalling the queue for an
  hour, and only when every mailbox is full does the tick report that.

### Changed

- README repositioned: source leads, enrich them, work several inboxes as one - instead of "a
  small SDR app". The old "bad fit: anyone wanting built-in data enrichment / teams needing shared
  inboxes" no longer describes the product.

### Tests

373 (was 361): the inbox merging two mailboxes, per-mailbox totals and unread, the filter, the
newest-first order, the account-wide unread count, and the rotation (quietest first, a capped
mailbox skipped, an explicit mailbox never swapped).

## [1.16.0] - 2026-11-15

Manyreach through its REST API v2, next to the MCP connection.

### Added

- **Manyreach API key** (`X-API-Key`) on the MCP card: paste it, and *Sync campaigns & senders*
  pulls the **whole account** — `GET /api/v2/campaigns` and `/api/v2/senders` with
  `limit=1000` a page, paging on `pagination.totalItems`. On the account this was built against
  that is **100 campaigns and 876 senders** in two requests, where the MCP download stops at the
  first 30 of each.
- The rows land in `mcp_items` through the same mapping the MCP download uses, so the card shows
  one list whichever path filled it, and a failed sync leaves the previous snapshot alone.
- A key that Manyreach refuses is not kept: saving validates it against `/api/v2/account` first
  and rolls back otherwise.
- `GET /api/manyreach/status`, `POST /api/manyreach/key`, `POST /api/manyreach/sync`.

### Tests

361 (was 353): key validation and rollback, the "no key, no request" case, paging that stops on
the reported total, an empty page, a failed page, a full sync storing both kinds, and a failed
sync that keeps the old snapshot.

## [1.15.0] - 2026-11-15

Downloading from an MCP server, and connecting any other sender platform.

### Added

- **Download** on a connected server (Settings → MCP connections). It runs the datasets the
  connection declares and stores them in `mcp_items`, newest snapshot only. For Manyreach that is
  **campaigns** (`campaign/get`) and **senders** (`sender/get`) — 30 of each on the account this
  was tested against — shown as two lists in the card.
- **Add another MCP server**: a name, an MCP URL and an optional token. With a token the calls go
  out as `Authorization: Bearer` directly (no OAuth); without one the browser sign-in is used.
  Instantly (`https://mcp.instantly.ai/mcp/<key>`), lemlist and Apify (OAuth) work that way.
  Smartlead publishes SSE only, which this client does not speak, and the card says so.
- `POST /api/mcp/pull`, `GET /api/mcp/items`, `POST /api/mcp/custom`.

### Changed

- Every generic call now carries the `intent` object Manyreach requires
  (`{objective, step}` — a business objective, never personal data), which its `read`/`create`/
  `update`/`delete`/`act` tools validate before doing anything.

### Tests

353 (was 345): the JSON body behind the header line, the campaign/sender field mapping, a download
that stores and then replaces its snapshot, a failed download, and custom servers with a token, a
bad URL and a reserved name.

## [1.14.0] - 2026-11-15

The app as an MCP **client**: a connection to Manyreach, and a generic way to add the next one.

### Added

- **Settings → MCP connections.** A remote MCP server is a name, a URL and the OAuth details
  discovered from it; `engine/mcp_client.py` walks `/.well-known/oauth-protected-resource`, then
  the authorization server's metadata, registers the app with dynamic client registration (public
  client, PKCE S256, loopback redirect), and stores the tokens in `data/settings.json`.
- **Manyreach** (`https://mcp.manyreach.com/mcp`) is the first connection. *Connect* opens its
  authorization page — the Manyreach API key is typed there, never in the app or the chat — and the
  loopback callback finishes the flow.
- **Tools and a runner**: *Tools* lists what the server exposes with each argument schema
  pre-filled as a JSON skeleton; *Run* sends `tools/call` and prints the raw answer, so a tool the
  app does not wrap is still reachable.
- `GET /api/mcp/status`, `POST /api/mcp/start`, `/api/mcp/tools`, `/api/mcp/call`,
  `/api/mcp/disconnect`, and `GET /oauth/mcp/<name>/callback`.

### Changed

- `/api/state` now reports the MCP connections, so the Settings card needs no extra request.

### Tests

345 (was 332): discovery through the protected-resource document, dynamic registration and the
PKCE URL, the token exchange, a stale state, refresh, disconnect, JSON and SSE answers, and a tool
call that returns text and one that returns an error.

## [1.13.1] - 2026-11-15

Ollama Cloud stopped being free on 9 November 2026 (free accounts moved to pay-as-you-go
credits), so the app stops advertising it as a free option.

### Changed

- The Ollama preset is labelled **"Ollama (local; the cloud is paid since Nov 2026)"** and still
  points at `http://127.0.0.1:11434/v1`, which is what actually runs without an account. The
  Settings card says the same.
- The 34 Ollama Cloud keys registered during testing were removed from the provider list. The
  free OpenRouter model (`inclusionai/ling-3.2-flash:free`) stays as the default, and the pool
  mechanism stays for any endpoint that ever has more than one key.

## [1.13.0] - 2026-11-15

A provider pool: many keys on one endpoint, and one of them failing is no longer fatal.

### Added

- **Fallback across keys.** Providers that share a base URL are treated as one pool. A call starts
  at the next key in turn, so the free quota is spread instead of burning the first key, and when
  a key answers `402` (no credits), `429`, `401`, `403`, a `5xx` or the network fails, the next key
  gets the request. A malformed request is not retried (it would fail everywhere), keys that
  failed in the last half hour go to the back of the queue, and at most six are tried per call so
  a dead pool fails in seconds.
- `llm.test()` tests exactly one key (`pool=False`), so a green tick means that key works.
- The Settings card shows the pool size and explains the behaviour; `llm.status()` reports
  `pooled` and `pool_base`.

### Fixed

- HTTP 402 now reads as what it is - *the key is valid but the account has no usage credits* -
  instead of a generic provider error.

### Tests

332 (was 325): which statuses are worth another key, a dead key falling through to the next one,
rotation across a pool, the message for a pool of dead keys, a 400 not being retried, and Test
staying on a single key.

## [1.12.0] - 2026-11-15

Lead filters: country, job title, industries (any of, or must-have), company size, location,
technologies and source.

### Added

- **A filter panel on the Leads page** (the *Filters* button): country and source from the data
  itself, company size from six buckets, and free text for job title, location, industries,
  must-have industries and technologies. Filters apply as you type, the button shows how many are
  active, and *Clear filters* resets everything.
- **`company_size` and `technologies` on a lead**: imported from `size`, `employees`, `headcount`
  and `technologies`, `tech_stack`, `tech`; company size is folded into `1-10`, `11-50`,
  `51-200`, `201-500`, `501-1000`, `1000+` so "120" and "51-200" mean the same thing. Both are in
  the lead form, the table, the search and every export.
- **Country normalisation**: about twenty alias groups (`US`, `USA`, `united states`,
  `United States of America` → United States; `UK`, `England` → United Kingdom; `Slovensko` →
  Slovakia …). Applied on import and on save, and compared through a SQL function at query time,
  so rows imported before this change are grouped and filtered correctly without being rewritten.
- **Sorting** by newest, company, country, job title or company size.
- The table gained Industry and Size columns, and the header total plus the filter badge update
  in place, so typing in a filter never re-renders the panel under the cursor.

### Tests

325 (was 307): the size buckets, every filter, must-have being AND, the sort orders, the facet
lists, country folding (including the query against existing variants), and an import filling
size and technologies.

## [1.11.1] - 2026-11-15

Every lead's domain now reaches the catalogue.

### Added

- **Import lead domains** (*Domains* tab, `POST /api/domain/sync`): one pass copies the distinct
  domain of every lead into `domains` — including the leads that have no email and only a domain
  (49,130 of them in the current database, 80,691 domains in total, in about five seconds).
  Running it again adds nothing.
- The same sync runs after every import (uploaded file or path on disk), and saving a lead puts
  its domain in the catalogue straight away, so the tab never misses a site you already know.

### Tests

307 (was 302): a lead with only a domain, a lead with only an email, the sync being idempotent,
`ensure_domain` normalising a URL and refusing a second insert, and the endpoint.

## [1.11.0] - 2026-11-15

A **Domains** tab: a catalogue of domains, three open-data feeds, Common Crawl enrichment and
direct scraping.

### Added

- **Domains tab**, next to Leads: the catalogue as its own thing, with a search, a source filter,
  per-domain Common Crawl pages and last-crawl, MX, and how many leads sit on the domain.
- **Three Hugging Face sources** (`engine/domain_sources.py`), streamed where they live and
  never downloaded whole:
  * `open-index/ccrawl-domains` - 370M domains from the current CC web graph, ranked by PageRank
    and harmonic centrality (ODC-BY).
  * `humbleworth/registered-domains` - ~180M registered domains, one per line (no licence
    declared).
  * `xfox25/ct-daily-domains` - one Certificate Transparency file per day, ~42M names (CC-BY-4.0).

  Only the domains the catalogue does not have are inserted, so a 370M-row list costs nothing
  until you actually use it.
- **`/api/domain/list`, `/api/domain/pull`, `/api/domain/enrich`, `/api/domain/scrape`.**
- **Per-domain Common Crawl check** from the table: up to three domains per click, 15 s each, a
  timeout or a 504 reported as an error instead of hanging the page. The daily `commoncrawl`
  job remains the way to check many.
- **Scrape**: reads the live site (robots-aware, up to four pages), lists the addresses it finds,
  and *Add as lead* creates the lead without leaving the page.

### Changed

- `domains.txt` is no longer published on Hugging Face - the catalogue is pulled from the three
  open datasets instead. The Bulk data dialog and `tools/upload_bulk_data.py` now carry only
  `rec_results.duckdb`.

### Tests

302 (was 289): the source registry, crawl/day selection, the store (de-duplication, URL to
domain), the pull counters and the error path, and the three new endpoints.

## [1.10.3] - 2026-11-15

Bulk data on Hugging Face, and Common Crawl in both directions.

### Added

- **Bulk data** (Leads -> *Bulk data*): download links for the two files that are too big for
  this folder, published as the public dataset `JurajBrando/lead-studio-data` —
  `rec_results.duckdb` (the scraped contact table) and `domains.txt` (the raw domain list).
  The links come from `/api/state`, so they change in one place.
- **`cc_hf` job — Common Crawl host index from Hugging Face.** Reads a shard of
  `commoncrawl/host-index-testing-v2`, streamed with range reads, takes each host's registered
  domain and adds the ones the `domains` table does not know yet (2000 per run, 200k rows
  scanned), keeping the fetch statistics in `data`. Weekly, Sunday 04:30. The Hub mirror lags
  the official index by months, so it only adds: the existing daily `commoncrawl` job still
  checks every domain against the current crawl.

### Changed

- `engine/hf_data.py` is the one place that knows the dataset id and the file URLs.

### Tests

289 (was 280): the links, the registered-domain and SURT fallbacks, the store (de-duplication,
stats, a second run adding nothing), and the job's missing-library message plus a run against a
fake shard.

## [1.10.2] - 2026-11-15

Metricool through the project's affiliate link, and the legal notice that goes with it.

### Changed

- **Every link that leaves for Metricool goes through the affiliate link**
  (`https://my.mtr.cool/NEMGNE`): *Get Metricool* in the connection bar, every *Connect* under
  Planner -> Connect social networks, the Meta/Google/TikTok *Open* and *Connect* buttons, and the
  ads empty state. All of them are `rel="noopener sponsored"`.
- **The connection bar reads differently**: *Get Metricool* (the affiliate link, for someone who
  needs an account) and *I have an account - connect* (the OAuth flow), instead of one button that
  could mean either.
- **A legal notice** under the connection bar, a *Legal notice* card at the bottom of Settings, a
  line in the sidebar, and the same text in the README: not affiliated with Metricool, the links
  are affiliate links, Metricool's terms and privacy policy apply, MIT licence and no warranty,
  and you are responsible for what you send and import.

### Tests

280 (was 278): the affiliate URL and the disclosure in the Metricool status, and the legal block
in `/api/state`.

## [1.10.1] - 2026-11-15

A **Browse** button on the import dialog's "File on this computer" tab.

### Added

- The page cannot see a real path from a file input, and a 2 GB Parquet cannot be uploaded,
  so *Browse* asks the server to open the operating system's own file window
  (`engine/picker.py`: tkinter in a child process, so the dialog gets its own main thread and
  its own process; the chosen path is written to a temp file, so the windowed build with no
  stdout works too). `POST /api/dialog/pick` returns the path, the field fills in and the file
  is checked immediately.
- `leadstudio.py --pick-file --out <file>` is the child entry point (used by the packaged exe
  as well).

### Tests

278 (was 271): the child command, the answer file, cancelling, a failing dialog, a build
without tkinter, and the endpoint turning a dialog error into a normal error.

## [1.10.0] - 2026-11-15

Contacts without an email address, and importing the multi-gigabyte files people actually have.

### Changed

- **An email is no longer required.** The leads table used to make the address `NOT NULL UNIQUE`,
  so exactly one contact could exist without one. It is now optional, guarded by a partial unique
  index (`UNIQUE WHERE email <> ''`), and a row is identified by an address, a company, a domain,
  a LinkedIn URL or a name. A file of companies with a description imports cleanly; those rows
  simply cannot be enrolled in a sequence (the queue skips them, and so does "verify").
- **A `description` field on the lead** (the company description), mapped from `description`,
  `about`, `summary`, `text300`, `bio` and friends, shown in the table where the email would be,
  editable in the lead form, included in search and in every export.
- **Re-importing fills the gap**: `update_existing` now matches an email-less row by company (and
  name) as well as by address, so a company file and a people file of the same accounts merge
  instead of duplicating.
- **Importing a file on this computer**, not just an upload: *Leads -> Import -> File on this
  computer*. It streams Parquet (pyarrow batches) and DuckDB/SQLite (cursor + `LIMIT`), so a
  10 GB file with 62 million rows is fine; you pick the table, an optional SQL `WHERE` (DuckDB and
  SQLite only), a row cap, and whether to keep only rows with an address. `POST /api/lead/inspect`
  reports tables, columns and row counts without reading the data.
- **Faster inserts**: one transaction per 500 rows instead of one commit per lead.

### Fixed

- A company name is no longer duplicated into the contact column when a row has no address.
- Domains are normalised on import: `https://www.Example.com/about` becomes `example.com`, and a
  column holding `"a@x.com, b@y.com"` takes the first valid address.

### Tests

271 (was 257): the table rebuild (children preserved, two empty addresses allowed, duplicate
address still refused, backup written), email-less imports, the `only_email` switch, company
de-duplication and promotion, Parquet and DuckDB path imports with a filter, the filter refusing a
second statement, and the "a row needs something to identify it" rule.

## [1.9.0] - 2026-11-15

The Social tab rebuilt: a five-field video composer, then the Metricool workspace it feeds.

### Changed

- **The lyric-video maker is gone.** The song/lyrics/timing/album/translation demo was never the
  point of this tab. The composer now takes exactly five things: a **title**, an **audio track**,
  one **picture or video**, a **description**, and the output **format** and **size**. A picture
  becomes a still cover with the title and description drawn on it, held for the length of the
  audio; a source video is scaled and cropped to the format and gets the audio as its track.
- **The renderer lost its dependency stack.** No whisper, no numpy, no rapidfuzz: an ffmpeg and
  Pillow are all it needs, so a render starts immediately instead of installing a few hundred MB
  first. Four formats (16:9, 9:16, 1:1, 4:5) and three sizes (1080p, 720p, 480p).
- **The Social tab is now five panes**: **Create**, **Inbox**, **Planner**, **Ads** and
  **Reporting**. The copy and the feature set follow Metricool's own product: a unified inbox with
  reply, notes, assignment and response-time numbers; a visual content calendar with the best time
  to publish and the network connections; the Meta, Google and TikTok ad panels; and the report,
  dashboard, Studio, Looker Studio and Hashtag Tracker cards.
- **The Metricool connection is now the top of the page**, not a card at the bottom, and it reports
  what the account actually exposes: the tab asks for `tools/list` and matches a tool to each
  section, so a section that cannot be answered says so instead of showing an empty box.

### Added

- `GET /api/social/workspace` - the tool list, which sections they cover, and the MCP/app URLs.
- `GET /api/social/section?name=inbox|planner|ads|reports` - runs the tool behind a section and
  returns its answer (plus the tool and the arguments that were sent).
- `POST /api/social/reply` - answers a conversation through whichever reply tool exists.
- **Sample data toggle** on every pane, labelled as sample, so the layout can be read without a
  Metricool account. Nothing in it is passed off as real.
- Internal notes and conversation assignment are kept in the app (they are never sent anywhere).

### Removed

- The lyrics editor, `.timing.csv` re-renders, album videos with chapters, the singing-language
  and listening-model pickers, the per-language labels and the translation-through-LLM path.
- `POST /api/social/lyrics`, and the `timing`/`lyrics` fields of the render spec.

### Tests

257 (was 241): the new spec validation (formats, sizes, even dimensions, file safety), a real
ffmpeg render of a picture plus a tone, the cover-frame drawing, the section-to-tool mapping, the
tool cache, the new endpoints, and the old lyrics endpoint staying gone.

## [1.8.0] - 2026-11-15

The Social tab: lyric videos, and Metricool through MCP.

### Added

- **Social tab** with the lyric-video maker from the original Lead Studio: drop songs, one
  background picture and the lyrics; get 16:9 (YouTube) or 9:16 (Shorts, Reels, TikTok) videos
  with the sung line and its translation, chorus labels, a title/album header, an album video
  with YouTube chapters, and a `.timing.csv` per video so a line's timing can be fixed by hand
  and re-rendered in seconds instead of minutes. Renders run as a child process: the app stays
  usable, the log streams into the page, and Cancel stops ffmpeg.
- **Metricool over its MCP server** (`https://ai.metricool.com/mcp`). Metricool's REST API needs
  the Advanced plan, but its MCP server works on Free, so this is the connection that exists for
  the plan actually in use: OAuth with dynamic client registration and PKCE, tokens stored next
  to the mailbox credentials and refreshed automatically.
- **Tool discovery instead of hard-coded calls**: the tab asks Metricool which tools the account
  exposes, offers a generic runner for any of them, and adds two helpers - *Analytics* (network +
  date range) and *Schedule in Metricool* (caption, when, networks, media URL) - that map the
  friendly fields onto whatever the tool's schema calls them and show the answer plus the
  arguments they sent.
- **Finished videos are reachable through the tracking base url** (`/t/v/<name>.mp4`), which is
  what lets Metricool fetch the file without a separate upload.
- **`--video-maker --spec <file>`** mode in `leadstudio.py`, so a render is a child process in the
  packaged exe too. See [docs/SOCIAL.md](docs/SOCIAL.md).

### Changed

- The renderer installs `faster-whisper` only when it has to listen to a song, and inside a
  frozen build it explains the situation instead of calling `pip` (which would just start
  another copy of the app). Re-rendering from a timing file needs no whisper at all.
- The video spec accepts `work_dir`, so scratch files stay in `data/video/work`.

### Tests

241 (was 211): video spec validation and file safety, the task runner, MCP response parsing
(JSON and server-sent events), schema-based argument mapping, tool discovery, token storage and
refresh, and the Social HTTP endpoints including the public video path.

## [1.7.3] - 2026-11-15

Correctness pass over the sending path, the data layer and the local server. Everything here
was found by reading the code and is covered by a test that failed before the fix.

### Fixed

- **Editing a lead no longer wipes it.** The edit form sends only the fields it shows, and the
  server used to write `""` into every other column: phone, country, industry, LinkedIn,
  website, tags and source disappeared on save. The server now updates only the fields it
  receives, and the form shows all of them. Changing the email resets its verification, and
  the status list now includes `bounced` and `unsubscribed` (saving a bounced lead used to
  quietly set it back to `new`).
- **A manual status change stops the sequence.** Marking a lead `replied`, `interested`, `won`
  or `lost` left the enrollment running, so follow-ups kept going out to someone who had
  already answered or signed. `replied` also sets `replied_ts`, which is what the queue checks.
- **Tracking and unsubscribe links work through a public URL.** The open pixel, click redirect,
  unsubscribe page and offer pages were rejected as "not local" whenever `tracking.base_url`
  was a tunnel or domain — the only way they are ever used.
- **No step is ever sent twice.** A send is claimed in the database *before* the SMTP call (the
  old code wrote the row afterwards), a lock stops the worker and the "Send next now" button
  from picking the same enrollment, and a connection that drops mid-send pauses the enrollment
  instead of resending a message that may already have been delivered.
- **A failed send no longer hammers the server.** It is retried after 15 minutes, then 30, then
  the enrollment pauses with the error. Before, the same attempt was repeated every ~20 seconds
  forever, adding a failed row each time.
- **Unsubscribe answers are honoured.** A reply whose subject is `unsubscribe` (the address in
  `List-Unsubscribe`, Gmail's one-click, or a person typing it) suppresses the address and
  stops the sequence instead of being filed as a sales reply.
- **`List-Unsubscribe-Post`** is advertised only when there is an https unsubscribe URL that
  accepts a POST (RFC 8058). It used to be promised with a mailto that cannot answer it.
- **CSV export** starts with a UTF-8 BOM (Windows Excel read accented names as mojibake) and
  neutralises cells beginning with `=`, `+`, `-` or `@` (formula injection from a scraped file).
- **Deleting a lead** also deletes its `sends` and `replies` rows. The full message body and the
  reply snippet used to stay in the database forever.
- **A corrupt `settings.json`** is kept as `settings.json.broken-<timestamp>` instead of being
  silently overwritten with defaults (which lost the company name, sending rules and API keys).
  Valid JSON that is not an object no longer crashes every request.
- **Settings refuse an impossible sending window**: equal start and end hours, hours outside
  0–23, negative or non-numeric delays. Such a window silently never sends.
- **The OAuth result pages** escape their message and are served with a strict CSP. A crafted
  `/oauth/callback?error=...` link could run script on the app's own origin and read the session
  token.
- **Bad or oversized `Content-Length`** gets a JSON error instead of a dropped connection, and a
  request body is capped at 64 MB.
- **Jitter** now actually randomises the gap between sends; the setting was read and ignored.
- **A job left `running=1`** by a crash (the tray's Quit exits hard) is cleared at start-up, so
  it is scheduled again.
- **The "app is not responding" Reload button** works again: it used an inline handler that the
  page's own CSP blocks.
- **Bulk actions** (delete, enroll, verify) no longer act on leads hidden by a search, a status
  filter or a page change.
- **Editing an offer** no longer destroys a custom pitch body — the textarea was filled with the
  literal text `{{offer_link}}`.
- **Build:** `BUILD_EXE.bat` and the release workflow now pass `--noconsole` (the documented
  build path produced a console window) and no longer exclude Pillow, which the tray icon needs.
- **Indexes** on `sends(token)`, `sends(status, sent_ts)` and `leads(verify_status)`: every
  tracking hit and dashboard load scanned the whole table, including message bodies.

### Tests

205 tests (was 179). New coverage: partial lead updates, status→sequence interaction, public
tracking routes, OAuth escaping and headers, settings validation, `Content-Length` handling,
Excel-safe export, jitter, concurrent ticks, retry back-off and give-up, ambiguous send
failures, inbound unsubscribe, lead-deletion cleanup, broken-settings recovery, stuck job flags
and the `List-Unsubscribe` headers.

## [1.7.2] - 2026-11-15

No console, and a tray icon.

### Added

- **System tray icon** (pystray + Pillow, optional): double-click to open the app, right-click to
  quit. This is how you reach the app once there is no console window.
- **Console-free launch**: `START_LEAD_STUDIO.bat` now starts `pythonw` (no console), and the exe is
  built with `--noconsole`. A windowed build has no stdout, so the app redirects it to a sink at
  start-up instead of crashing on the first print.
- `START_CONSOLE.bat` kept for debugging: same app, console visible, errors readable.
- `--no-tray` to run without the icon (headless/CI).

### Fixed

- `pystray.MenuSeparator` does not exist in pystray 0.19.x; the separator uses `Menu.SEPARATOR`.

