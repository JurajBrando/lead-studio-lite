## [Unreleased]

### Added

- **Radar: the Assistant read works on any website, and every hunt starts from the whole job
  history.** Page text now drops HTML comments, `<script>` / `<style>` / template blocks and
  tag fragments whose quoted attributes contain `>` - the leak that turned a shop's Alpine.js
  markup into "keywords" - so the words it reads are words the page shows: nav, consent,
  policy and code tokens are filtered, accents count once (`zaluzie` == `žalúzie`) and the
  business name comes from the title part that matches the domain instead of a slogan. A
  problem line is only read when the sentence states a buyer's pain: cookie, terms, privacy
  and thank-you text and "you can ..." promises are rejected, a real pain word is required,
  and an unknown category no longer splits a stray sentence into junk pains. A price under 10
  is ignored unless it is monthly, so "$5.00 shipping" is not the offer's price. Separately,
  `daily.signals_summary` keeps domains and search-engine words out of the words it counts,
  and the Assistant read, the offer card and every hunt all read that summary: the result
  shows a "From your job results" block, the Hunt card shows the counts it targets, and each
  run records them in `params.targeting`.
- **Radar: Hunt now targets from the Assistant's read.** The read writes a `targeting` block on
  the offer card - who buys, the use cases it clusters, the pains, the tools that create the
  problem, which signal kinds matter and the markets it names - and `radar.assistant_view`
  feeds it to every stage: the gate accepts a company whose own words match a use case, a
  pain or a need even when it misses the card's keywords, fit counts the use cases, the pain
  profile uses the read's needs and kind weights, and the reason line reports "their site
  talks about ...". Each run records what it targeted in `params.targeting`, the Hunt card
  shows it, and the first phase names it. Saving the card dialog no longer drops the read:
  `hunt.save_card` merges the form over the stored card instead of replacing it.
- **Radar: the Assistant reads one link into the whole Radar view** (`engine/radar_brief.py`,
  `POST /api/radar/assistant`). Paste a business link and it fetches the home page plus the
  first `/about`, `/pricing`, `/team` and `/contact` it finds, extracts what the business
  sells and to whom, lists its product keywords, searches the businesses that come up as
  "alternatives to" it, reads and scores those candidates, keeps the closest, finds the team
  named on the site and the mailboxes it publishes, merges the rivals the site itself names,
  matches the pains the offer answers, and scores six campaign ideas with a subject and a
  body for each. Every step is shown with its own line and what it actually found. Plain
  rules, no LLM: a field the site does not show stays empty. With an offer selected the read
  is merged onto that offer's card and fills only empty fields; without one it writes
  nothing. The Assistant also carries the outreach list (the hunt's company index, fit
  scores, ranking and the addresses it found) and drafts the first email from the top brief
  with the company's own opening line. The free-form workspace chat moved here from the
  Overview and says so when no LLM provider is set. The separate "Offers under management"
  card is gone: the offer picker and the Edit / Delete / New offer buttons above it already
  do that, and the delete explanation is now the button's tooltip.
- **Page text is decoded by the page, not by a fixed list** (`net.decode`): a site that
  declares `utf-8` but is served as legacy Windows-1250 (or the other way round) no longer
  comes back with the accented words mangled into replacement characters.
- **Domains tab: the catalogue against a downloaded live-websites list** (`engine/live_list.py`,
  job `domains_live_list_all`, `live_list` in the Domains stats). DNSniffer's Live Websites
  Dataset is a ZIP of CSV parts with one `Domain` column - about 285 million names, 7.3 GB
  unzipped. They do not fit in memory, and a Bloom filter that size takes close to an hour to
  fill, so the pass runs the other way round: the catalogue is read into a set and the file is
  streamed through it once. Exact, no index to build or keep in date, no key, no quota - ten to
  fifteen minutes for the full export, reading the file where it is. A catalogue domain the
  list does not contain is quarantined (junk rows keep their own reason), the leads table is
  never touched, a domain that already produced a lead is kept, and `Allow back` restores the
  quarantine. The tile starts from the file already downloaded on this computer and accepts
  any other ZIP, folder or CSV; `Preview` reads the whole file and removes nothing.
- **Domains tab: one "Run on every domain" card.** Cleaning the junk, the DNSniffer live
  check and the live-websites list were separate cards with their own descriptions; all seven
  catalogue passes now sit in one card as tiles with their options inline (DNS on/off for the
  cleaner, certificate and re-check for DNSniffer, the API key, the file path and preview for
  the list), and the four enrichment passes follow underneath.
- **Mailboxes: bulk setup from a senders sheet, a real login test and a real send**
  (`engine/mailboxes.py`, jobs `mailboxes_test_all` / `mailboxes_send_test`, routes
  `/api/mailbox/import` and `/api/mailbox/dns`). A Manyreach export carries no passwords, so
  the hundred mailboxes it created could never sign in. The new card on the Mailboxes page
  reads the provider sheet (email, password, smtp_host/port/security, imap_host/port,
  connect_now), fills the mailbox that already has the address instead of duplicating it,
  never blanks a saved password, maps `SSL/TLS` to SSL on 465 and STARTTLS on 587, and only
  brings `connect_now = yes` rows live unless every row is asked for. **Test every login**
  signs in over SMTP and IMAP and stores the result on the card; **Send a real test from
  each** puts one short message in each inbox, spaced out; **Check SPF / DKIM / DMARC**
  verifies the provider DNS sheet live - the SPF include, the three `szn*.domainkey` CNAMEs,
  DMARC, and old leftover records.
  The import can also put the sheet's password into every other `.eu` Seznam mailbox that
  has none and switch them on, since one Seznam account holds aliases on many of its sender
  domains; the login test then runs six at a time and pauses a box whose login the server
  rejects, so a dead alias never sits in the sending rotation.
- **Domains tab: check against DNSniffer's live index** (`engine/dnsniffer.py`, job
  `domains_dnsniffer_all`). Walks the catalogue against DNSniffer's index of about 380
  million domains seen in zone files and Certificate Transparency and quarantines the rows
  it does not list, in the same `domains_dropped` table as the junk cleaner - so a later
  import cannot add them back, and `Allow them back` restores them. With an API key
  (Settings > Connectors, or the field on the card) each check is one exact
  `GET /api/v1/domains/<name>`, and only a 404 counts as missing. Without a key the free
  public MCP tool `search_domains` is used: a substring search capped at 500 rows, so an
  answer the server truncates is counted as "unknown" and never removed. The optional
  certificate rule also removes an indexed domain that never showed a certificate; it is
  off by default, because the certificate column has gaps (DNSniffer's own domain has none).
  This is a "seen live" test, not an HTTP fetch. The leads table is never touched, a domain
  that already has a lead is kept, errors, timeouts and rate limits remove nothing, and a
  second press continues where the last run stopped. `Preview` checks a sample only.
- **MCP: a server that needs no sign-in can connect** (`engine/mcp_client.py`). `begin()`
  demanded OAuth metadata and stopped at the first 404, so DNSniffer - whose public tools
  take no token - could never be added; and a connection without a token still sent
  `Authorization: Bearer `, which the server saw as a bad credential. A server with no
  metadata is now probed without a token, and on success the connection is saved as open
  (no browser tab, no token written); the header is only sent when there is a token. Adds
  the `dnsniffer` preset (`https://dnsniffer.com/mcp`).
- **Leads tab: "Clean the list first"** (`engine/lead_clean.py`, job `leads_clean_all`).
  Removes what can never start a conversation: a consumer mailbox (gmail, yahoo, seznam and
  about seventy more) with no website or own domain behind it, an address that is not an
  address, and rows with no email, company, site or phone at all. A company name, an
  industry, a job title or a city alone does not make a business - a personal gmail in a
  government or fishery import goes - while a gmail lead that names a website stays, which
  is the ordinary owner-with-gmail case. `junk_reason` holds the rule; the walk
  pushes it into SQL and reads only candidate rows, in bounded id windows of 200k, so 65M
  leads are one pass rather than a Python dict per row and Cancel stays responsive.
  Removed leads are copied whole into
  `leads_dropped`, with the reason, and their sends, replies, enrollments and signals go
  with them exactly like the Delete button; `Allow them back` re-inserts them. `Preview`
  changes nothing and reports the count per rule.
- **Import contacts: "Only business contacts"** (`import_rows(..., business_only=True)`, and
  `business_only` on both import routes). While a file is read, a row the Leads cleaner would
  drop is counted in `not_business` instead of stored - a private mailbox with no website and
  no own domain, an address that is not an address, and a row that is nothing but a name. Off
  by default, so every existing import behaves as before; the checkbox sits next to "Only rows
  that have an email" and the result line reports how many were skipped.
- **Domains tab: "Clean the catalogue first"** (`engine/domain_clean.py`, job
  `domains_clean_all`). Before any pass walks the list, this removes the rows that are not
  domains at all - phone numbers and addresses in the domain column, wildcards, IP
  addresses, reserved and test names - and, with DNS on, names that answer NXDOMAIN. One
  A/AAAA lookup per domain, 64 in flight; a name that exists but has no address yet, a
  timeout and a refusing resolver all keep their row. Only `domains` is touched: the leads
  table is never changed, and removed domains are copied into `domains_dropped`, which
  every import and `ensure_domain` now skips, so a feed cannot silently add them back.
  `Preview` estimates the dead share on a sample and changes nothing; `Clean` runs the
  whole catalogue as a cancellable background job with a progress bar; `Allow them back`
  clears the quarantine. 3.6M rows at roughly 4 ms per lookup.
- **Qualification: buying signals per offer, no model calls** (`engine/qualify.py`,
  `engine/jobs/qualify.py`). The database holds millions of contacts and nothing until
  now decided which of them deserved an email - every source optimised for volume. The
  new job reads a lead's site once and checks it against 37 signals grouped into eight
  cohorts, one per offer actually sold: trades quoting by hand (QuoteIQ Pro, plus
  Ataraxis when the phone is the only way in), clinics with no online booking (Dermis,
  plus HYROS when they are already buying ads), ads running with no attribution (HYROS
  Tracking), agencies with nothing capturing their leads (HighLevel SaaS Pro, or
  Unlimited when they white-label, plus HYROS AIR when they advertise for themselves),
  US companies at referral scale (micro1), own-brand ecommerce also sold on
  marketplaces (Red Points) and manufacturers (Modulario). The offer in the CSV is the
  bundle to pitch, not just the cohort's name.

  No LLM and no API key is involved, so it costs one HTTP request per domain and can
  run over the whole database. A domain is read once however many contacts sit behind
  it. Parked, placeholder, contentless, free-host and unreachable sites are
  disqualified before anything is scored, and - unless `park_disqualified` is off -
  given `status='unqualified'`, which every existing filter, export and enrollment
  query already skips. Nothing is deleted; setting the status back puts the lead in
  play. The rest get a score, a cohort, a `q:` tag, a `qualified` signal and a row in
  `data/qualified/qualified_<date>.csv`.

  Two cohorts (`paid_ads`, `liveness`) only corroborate: they add to the score but
  never name the offer, because a pixel proves a budget, not a problem. Ties between
  the rest go to the cohort that identifies who the company is over the one that
  identifies what it does.

  Unlike `hot_list`, which needs a change between two crawls, this fires on the site's
  current state, so a lead imported five minutes ago can be qualified immediately.
  Documented in `docs/QUALIFY.md`; 32 offline tests in `tests/test_qualify.py`.
- `crawl.robots_allowed(url)` - a public wrapper over the per-run robots cache, so
  other modules can honour robots.txt without reaching into `crawl._ROBOTS`.

### Changed

- **Radar: Edit offer and the offer/buyer card are one dialog.** The always-open card is gone
  from the page; `Edit offer` opens it over Radar, filled with what the Assistant's read saved
  on the offer - kind of offer, buyer, problem, price, countries, industries, keywords, pains,
  already solved by, competitors and the score weights. `Save card` writes them back and
  closes; `Analyze with AI` and `Apply to card` work inside the dialog, and Hunt still saves
  the card before it starts.
- **Leads tab: the export buttons now say what they do.** `CSV`, `Parquet` and `DuckDB` on the
  Leads page read `Export CSV`, `Export Parquet` and `Export DuckDB`; the routes, the handlers
  and the hint that lists the import formats are unchanged.
- **Leads tab: Clean and OpenStreetMap are buttons now.** The two cards that used to fill
  the page sit behind `Clean the list` and `Find businesses (OSM)` in the header, and each
  opens in a dialog over the list. The quarantine count, the preview/clean/allow-back
  buttons, the job progress bar and the OSM search fields moved with them unchanged.

### Fixed

- **Radar: a problem line read off a page is the company's own sentence, not a quote or a menu.**
  Lifted text is now checked as a written sentence: a first-person customer review ("I do not
  feel them ... !!") and a row of menu labels run into the next sentence ("Prototype sharing
  permissions Share prototypes with ...") are no longer shown as what the business solves, and
  a sentence that opens with "And" / "But" / "So" is shown from its first real word. Typed
  offers keep the looser check, so an offer paragraph that lacks a closing period still reads.
  The same read no longer turns "Meet Eron" or "Lead Product" into people, and the rival list
  drops button labels ("Run Dev Mode", "VS Code"); page text that runs one paragraph into the
  next still yields its problem sentence.
- **Radar: a page that prints its headings twice no longer spoils the problem line.** It came
  out as a 140-character cut that began with the page's own heading repeated ("The problem we
  solve The problem we solve ...") and stopped mid-word. It now drops repeated sentences and
  headings, starts at the problem sentence, stops at the next section heading and is cut on a
  word. Generic filler ("only", "keywords", "trusted", "play") no longer reaches the keywords.
- **Radar: Edit offer showed empty fields and the next Save wiped them.** The editor
  filled its form from the short list the Dashboard sends (`id` and `name` only), so the
  description, the site link, the keywords and the pitch looked gone; saving then wrote those
  blanks over the row. It now loads the full record from `/api/offers`, and `offers.save`
  writes only the keys the caller sends, so a partial save cannot blank a column.
- **The Dashboard offer step never ticked.** It read `stats.offers`, which `pipeline.stats()`
  never returned; the count is there now, with `offers_active` beside it.

- **A mailbox with an empty password reported "SMTP works".** `mail.smtp_open` skipped
  `login()` when the password was blank and returned the open connection as a success, so
  the test said the mailbox was ready and the first real send failed. It now refuses with
  "No password is saved for ...". Saving a mailbox also copies one typed password into both
  SMTP and IMAP - before, Seznam answered IMAP with
  `[AUTHENTICATIONFAILED] Empty password` even though the same password was saved for SMTP.
- **CSV and Excel imports from a path have no size limit.** A CSV was read into memory whole
  and refused above 200 MB, so an 18 GB file could not be imported at all. Both formats now
  stream row by row from the file - a small sample at the start picks the encoding, the
  delimiter and the header - skip straight past already-imported rows when resuming, and use
  the file's byte position as the progress total. JSON is the only format still parsed whole,
  so its 200 MB guard stays, with a message that says to convert it.
- A file over 40 MB dropped on the import dialog now points to **File on this computer**
  instead of being read inside the page (a request body is 64 MB and base64 inflates it); that
  tab reads the file where it lies and has no size limit.

- **A `website` column is stored as `website` again.** It used to be folded into `domain` and
  silently lost whenever the file also had a `domain` column, which made a gmail lead with a
  site look site-less to the cleaner; the domain is now derived from the site instead.
- **The Leads tab's country filter list no longer takes four minutes on a big database.** It
  grouped with a Python function called once per row; SQLite now groups the raw column and the
  groups are normalised afterwards - a couple of seconds instead of minutes on 65.7M leads.

## [1.37.1] - 2026-11-24

### Changed

- **Leads > LinkedIn data opens the subscription page.** The leads.cm table (about 105 million
  contacts) is a paid service; this build does not carry the downloader, so the button opens the
  offer page and the LinkedIn endpoints answer with that link.

## [1.37.0] - 2026-11-17

A review pass over the whole app: the secrets the UI was handed, the jobs that failed every
day, the imports that started over after a crash, and the queries that made the dashboard
slow on a database with millions of leads.

### Security

- **`/api/state` handed out every key and token.** Only the Hunter, AI and Serper keys were
  stripped; the Manyreach API key, the MCP access and refresh tokens, the Metricool token, the
  Treg key and the ad/HYROS keys all went to the browser in full. Any field whose name ends in
  `key`, `token`, `secret`, `pass` or `pwd` is now blanked and replaced by a `has_<name>` flag,
  wherever it sits in the settings tree. Saving a blank secret still keeps the stored one, so
  the Settings forms work unchanged.
- **The read endpoints needed no session token.** Only the `Host` header stood between any
  program on this machine - or anything reaching the port through a tunnel that rewrites
  `Host` - and the whole lead list, the settings and the logs. Every `/api/...` GET and the
  four export links now require the token, the same as the write endpoints; the links the
  browser opens itself carry it as `?t=`.
- **`/t/c/<token>?u=` was an open redirect.** It forwarded to any URL without looking at the
  token - on a public tracking domain, a good name to borrow for phishing. The target now has
  to be a link that really is in the message that token belongs to.
- **`engine/external.py` had `C:\Users\you\...` hardcoded**, which `tools/check_release.py`
  refuses to publish. The two tool folders are worked out from where the app is installed;
  `external.studio` / `external.clean` in settings.json, or `LS_STUDIO_DIR` / `LS_CLEAN_DIR`,
  still point anywhere else.

### Fixed

- **Starting the app twice ran two send workers on one database.** The second copy got its own
  port, its own worker and its own scheduler, and the same prospect could be mailed twice. The
  first copy now takes an OS lock on `data/app.lock` and writes its port; a second start opens
  the copy that is running and exits. The lock is released by the system, so a crash never
  locks the app out. `LS_ALLOW_MULTI=1` overrides it.
- **Saving settings failed with `WinError 32`.** Nothing serialised the read-modify-write, every
  request reads `settings.json`, and all writers shared one `settings.json.tmp`. A failed save
  could lose a freshly refreshed OAuth token. Reads and writes are now serialised in-process,
  the temporary file is per-writer, and a replace blocked by another process is retried.
- **An import died on `database is locked`.** A long job can hold the write lock past the 30 s
  busy timeout. Writes now roll back and retry with a growing wait, which also keeps a failed
  statement from leaving an open transaction that locks everybody else out.
- **A crashed import started over from row 0.** The error path wrote the starting row back over
  the state, throwing away every checkpoint of the run that had just failed. The checkpoints
  are left alone now, and each one flushes the pending rows first, so it only ever covers rows
  that are actually stored - no gap on resume, no duplicates.
- **The in-file duplicate check could exhaust memory.** A streamed DuckDB or Parquet source
  reports no row count, so the "big file" switch never came on. The set of identity keys is now
  capped at two million; past that the unique email index catches repeats.
- **Bounces were dropped and acknowledgements were treated as bounces.** A delivery report was
  matched to a lead only through reply headers, which Exchange, Postfix and others do not send,
  so those addresses kept getting follow-ups. The report itself is now read for our Message-ID
  or for `Final-Recipient` / `X-Failed-Recipients`, and only a lead this app has actually mailed
  is accepted. A message from a `no-reply@` address is an automatic acknowledgement: it no
  longer counts as a bounce (which suppressed a working address for good) or as a reply.
- **Follow-ups were not threaded and could come from another mailbox.** Step 2 and 3 now go out
  of the mailbox that sent step 1, as a reply to the message before it, so the prospect sees one
  conversation with one sender and the answer lands in the inbox being watched for it. Step 1
  still spreads across every active mailbox.
- **The `site_profile` job failed every day** with `no such column: excluded.platforms`:
  `CREATE TABLE IF NOT EXISTS` leaves a table from an older version as it was, so `platforms`,
  `favicon_hash` and `rdap` were never added. They are added on start now, and `platforms` is
  actually written - it was missing from the insert as well.
- **`profile_new` profiled the same twenty domains forever.** It took bare-domain leads without
  skipping the ones already done, so the rest of the list was never reached. Domains already in
  `osint_domains` are skipped, and one that cannot be reached is recorded as tried.
- **`scam_radar` and `dork_sweep` failed daily.** Both ship switched on with empty parameters,
  and both raised - a traceback in the log and a red *error* on Automation every morning. With
  nothing to work on they now report *skipped, not set up yet* and say which parameter to fill
  in. Same for certificate discovery.
- **"Delete all data" left the import bookmarks behind**, so importing the same file again
  continued from the saved row and skipped everything before it.

### Performance

- **The dashboard asked for everything every 30 seconds.** `/api/state` ran eleven queries per
  sequence and one per mailbox - with 100 imported campaigns and 876 senders, over a thousand
  queries a refresh. It is five grouped queries for all sequences and one for all mailboxes now.
- **Counts over the big tables are remembered**, but only when they are slow to work out (over
  150 ms): lead totals for two minutes, the Leads country and source filter lists for ten. A
  small database is always counted fresh, and an import, a delete or a status change clears the
  memory at once.
- **The exports no longer build the whole file in memory.** CSV is streamed to the browser in
  batches of 5 000 rows; Parquet and DuckDB are written to a temporary file (through Arrow, or a
  bulk CSV load when pyarrow is missing - `executemany` into DuckDB took minutes) and streamed
  from there. Verified at 60 000 leads: quotes, commas and newlines survive the round trip.

### Added

- `engine/instance.py` - the single-instance lock described above.
- `db.cached()` / `db.cache_clear()` - remember an answer only if it was slow.
- `pipeline.sent_today_map()`, `pipeline.lead_counts()`, `pipeline.boxes_for()`,
  `pipeline.click_target()`, `mail.fetch_bounce_text()`.
- `importer.iter_export_csv()`, `importer.export_parquet_file()`,
  `importer.export_duckdb_file()`, `importer.iter_lead_batches()`.

### Notes

- A script of your own that reads the local API with GET now needs the `X-LS-Token` header
  (the token is in the `ls-token` meta tag of the page, and in `data/settings.json`).
- A follow-up waits if the mailbox that sent step 1 is at its daily cap; it does not switch
  sender to get around it.
- Counts on the dashboard can lag up to two minutes behind a change made outside the app's own
  import, delete and status actions.
- The Windows file lock, the `WinError 32` retry and bounce reading against a real IMAP server
  were not exercised on Windows - the test run was Linux with mocked mail.

## [1.36.3] - 2026-11-16

### Fixed

- **"Import from this file" did nothing.** `run()` read `$('#imp-restart').checked`, but the
  restart checkbox had never made it into the markup, so clicking the button threw
  `Cannot read properties of null` before any request was sent. The checkbox is in the path tab
  now, and every control is read defensively - a missing element can no longer kill the click.
- **No progress bar on the path tab.** The bar lived inside the upload drop zone, which is hidden
  while the path tab is open. It now sits under the two tabs, visible in both, and a click first
  shows `Starting the import of <file>...` in the result box.
- **The checkpoint never advanced.** `_save_state` used `db.update("import_state", key, ...)`,
  which writes `WHERE id=?` - and this table's primary key is a text `key`. The update failed
  silently inside the try/except, so a resumed run kept the old offset. It now updates by `key`
  explicitly.

### Verified on the real 8 GB file (25 203 723 rows)

```
run 1, killed after ~6 s    state: offset_rows 20 000, finished 0
run 2                       phase: "resuming at row 20000"
run 2, killed after ~6 s    state: offset_rows 35 000  (15 000 rows this run)
```

### Notes

- Checkpoints are every 5 000 rows; an interruption loses at most that much repeated work.
- The dialog's Filter is a SQL `WHERE` for DuckDB/SQLite only. `rec_results.duckdb` has no
  `best_email` column (its columns are kind, url, domain, group, status, title, description, ...),
  so leaving that filter in makes DuckDB refuse the query - clear it, or use a column that exists.
- Measured on that file: about 2 500-3 300 rows a second through the app, so 25.2M rows is roughly
  2.5-4 hours.

## [1.36.2] - 2026-11-16

### Fixed

- **The upload result box was not actually in the page.** 1.36.1 pointed the upload at
  `#imp-upload-result` and claimed the box was there, but the markup insertion had not matched, so
  the box did not exist: choosing a file still produced no visible progress and no summary. The
  box is now inside the *Upload a file* tab (verified in the built dialog markup: div, the
  `handleFile` lookup and the `startImport`/`watchImport` target all resolve). Uploading a file now
  shows `Reading <name> (KB)`, the bar, the live phase and the final imported/existing/invalid
  counts.

## [1.36.1] - 2026-11-16

### Fixed

- **Uploading a file looked like it did nothing.** The progress and the result of an upload were
  written into `#imp-result`, which lives inside the *File on this computer* tab - hidden while the
  *Upload a file* tab is open. The upload tab now has its own result box: choosing a file shows
  `Reading <name> (KB)...`, the progress bar fills, and the summary (imported, existing, invalid)
  appears where the user is looking. Verified through the same base64 path the browser uses:
  3 rows uploaded, 3 imported.
- **Three buttons on Leads that all opened the same dialog.** The header *Import* and the card's
  *Import from this computer* are gone; the Leads page has one **Import contacts** button (the
  empty-state *Import a file* stays, since it only shows when there are no leads).

### Also

- The dialog says JSON is accepted, matching what the importer reads.

## [1.36.0] - 2026-11-16

Every row, resume where it stopped, and the Contacts import the original app had.

### Changed

- **No more Max rows.** The field is gone from the import dialog and the path import default is
  `limit=0`, which now means *every row in the file* (the route still accepts an explicit number,
  capped at a billion, for anyone who wants a slice).
- **A path import continues where it stopped.** Progress is checkpointed every 5 000 rows in a new
  `import_state` table keyed by file + table + filter. If the run is interrupted - app closed,
  machine restarted, an error - running the same import again starts at the saved row instead of
  row one, and the progress line says `resuming at row N`. A new *Start this file from the
  beginning* checkbox clears the checkpoint. A finished file is never resumed; its rows are
  re-checked against the email index, so a rerun imports nothing twice.
- **JSON import.** `.json` files are read now (a list of records, or `{rows: [...]}` /
  `{data: [...]}` / `{contacts: [...]}`), alongside CSV, XLSX, Parquet, DuckDB and SQLite.

### Added

- **Contacts import panel on the Leads page** (the original Lead Studio's option): a card with
  **Import contacts**, **Import from this computer**, and drag-and-drop **anywhere on the page** -
  a dropped file opens the import dialog already pointed at it. The recognised file types are on
  the card.

### Tests

511 (was 504): zero limit imports every row, an interrupted run resumes at its offset (and reads
only the second half), a finished run starts over and imports nothing twice, a restart ignores the
saved offset, and the JSON reader (list, wrapped object, path kind).

## [1.35.0] - 2026-11-16

Treg is wired in - one token, 4,100+ tools, paid per call.

### Added

- **`engine/treg.py`**: the team token from Settings -> Connectors, `catalog_search()` over
  `GET /catalog/search`, `call()` over `POST /call/<endpoint-id>` with a JSON body and the
  `X-Treg-Token` header, and the two capabilities Lead Studio needs: `verify_email()` on
  `treg.people.email.verify` and `maps_search()` on `treg.google.serp.maps`. Every answer reports
  what it cost (`charged_micro` from `_treg`), so a free route and a paid one are distinguishable.
- **Settings -> Connectors**: a Treg team token field, saved like the other keys (`has_treg`).
- **OSINT tab**: a *Treg tools* card - verify up to 25 addresses at once, or find Google Maps
  businesses, with the places (name, address, phone, website, rating, review count) listed.
- Routes `/api/treg/status|search|verify|maps`.

### Measured (live, this machine)

- Token valid: team `brandojuraj-s-team`, scope `team`.
- `treg.people.email.verify`: answered through ContactOut on a **free** route (`charged_micro: 0`)
  and through Orbisearch on a paid one ($0.00124) - the card shows which.
- `treg.google.serp.maps` for "coffee Bratislava": 3 places, **$0.00023** per call, first result
  `Urban House`, Laurinska 14, +421 911 755 205, rating 4.7 from 14 279 reviews.

### Tests

504 (was 498): the missing token, the team claims decoded from the token, a verified address on a
free route, Maps places plus the charge, a 422 reported with its detail, and catalog search ids.

## [1.34.1] - 2026-11-16

### Fixed

- **`UNIQUE constraint failed: leads.email` on a big import.** Above two million rows the in-file
  duplicate set is skipped (memory), and a repeated address *inside the same 500-row batch* is not
  in the database yet when the row is checked - so both copies were queued and the batch insert
  died. Now a batch keeps the addresses it already holds (`pending_emails`), counts the repeat as a
  duplicate, and if a batch still collides it falls back to inserting row by row, counting the
  losers instead of aborting an import that may have been running for hours.

## [1.34.0] - 2026-11-16

A 62 million row file imports without a cap and without eating the machine's memory.

### Changed

- **The 500 000 row cap is gone.** The path import accepted a `limit` and then clamped it to
  500 000, which is why a 62.7M contact DuckDB stopped at "500 000 rows read". The ceiling is now
  100 000 000, so a Max rows value of 80 000 000 means what it says.
- **The in-file duplicate set is skipped above two million rows.** It kept every identity key in
  memory (~80 bytes each, gigabytes at 62M). Above the threshold the unique email index does the
  work instead, so a rerun of the same file still imports nothing twice; the summary carries
  `duplicate_check_skipped` so it is visible rather than silent.
- The Max rows input accepts up to 100 000 000.

### Notes for a 62M run

- Expected time: roughly **2.5–4 hours** at the measured ~6 700 rows/s (the progress bar shows the
  ETA; the import runs in the background, so the UI stays usable).
- Database growth: budget **30–50 GB** of free disk in `data/` for the leads table plus indexes.
- It is resumable in practice: rerunning the same file against the same database imports only the
  rows that were not stored yet, because the email unique index and `_find_existing` catch them.

## [1.33.2] - 2026-11-16

### Changed

- The **Red Points - brand protection** affiliate card is now the first thing under the OSINT
  KPIs, above the scam jobs, instead of buried below the radar cards.

## [1.33.1] - 2026-11-16

Only free sources, as asked.

### Removed

- **Shodan and SecurityTrails connectors.** Both need a paid key to answer, so they are gone:
  `shodan_favicon()`, `securitytrails_history()` and their key flags. The favicon hash stays -
  a pure-Python MurmurHash3 is computed locally and needs nobody's API - and `keys()` now reports
  only Serper, which the dork sweep reuses from the existing settings.

Everything else in the OSINT tab is free: OpenStreetMap/Overpass, crt.sh certificate transparency,
rdap.org WHOIS, the Wayback availability API, Phishing.Database, and the domains-monitor feed.

## [1.33.0] - 2026-11-16

Four ways to find a fake shop before it is famous, all in the OSINT tab.

### Added

- **Dork sweep through Serper** (`engine/dorks.py`): seven Google dork templates per brand -
  official store, shop now, outlet, `intitle:"brand" "buy online"`, free shipping,
  `site:myshopify.com`, official website - through the Serper key the app already has. Results are
  filtered (the brand must be mentioned, the brand's own domains are dropped) and stored as
  `fake_shop_candidate` signals with the query and the result as evidence. Job *Dork sweep
  (Serper)*, Signals, weekly Mon 09:30.
- **Favicon + shop platform fingerprint**: a pure-Python MurmurHash3 so the favicon hash matches
  Shodan's `http.favicon.hash` with no dependency, and recognition of Shopify, WooCommerce, Wix,
  BigCommerce, Squarespace, Magento, PrestaShop and WordPress. Both are saved on the profile, so
  "same favicon as a known fake" clusters across the domain catalogue for free.
- **Certificate discovery (crt.sh)**: asks certificate transparency which domains ever received a
  certificate with the brand token in the name - before any scam feed lists them. Free, no key.
  Job *Certificate discovery (crt.sh)*, Signals, daily 06:40.
- **RDAP enrichment**: registration date, expiry, registrar and nameservers for a suspect domain,
  free through rdap.org; `enrich()` combines it with the favicon hash and the platform.
- **Shodan and SecurityTrails connectors**: `shodan_favicon(hash)` finds every host serving the
  same favicon, `securitytrails_history(domain)` returns the A-record history. Both are ~40 lines,
  read their keys from the existing connector settings, and the OSINT tab shows whether each key
  is set.
- OSINT tab: a *Candidates* KPI, a **Dork sweep** card, a **Certificate discovery** card, and the
  key badges. Routes `/api/osint/dorks|discover|enrich`.

### Tests

492 (was 484): the MurmurHash vectors (`""`, `"foo"`), the Shodan-style favicon hash, platform
recognition, dork filtering (brand mentioned, own domain dropped, empty brand refused), and
certificate filtering with the brand's own domain removed.

## [1.32.1] - 2026-11-16

Two fixes in the OSINT tab, found by running its tests on a fresh database.

### Fixed

- **The radar could not save the brands.** They were written to the existing `brands` table, whose
  `domain` column is `NOT NULL UNIQUE` (it holds domains, not names), so a scan died with
  `IntegrityError: NOT NULL constraint failed: brands.domain`. Brands typed in the OSINT tab now
  live in their own `osint_brands` table; the existing `brands` table is still read as a fallback.
- **Red Points card was empty before the first offer sync.** `state()` returned `{}` when no brand
  offer row existed yet; it now falls back to the built-in Red Points affiliate link.

## [1.32.0] - 2026-11-16

A new tab for the Red Points side of the business, and three tools behind it.

### Added

- **OSINT tab** (sidebar, above Domains): KPIs for fake shops, lookalikes and profiled domains; a
  **Scam jobs** section that runs and shows the three watchers in place (*Fraud watch: phishing
  domains*, *Fake-shop radar*, *Brand watch: lookalikes & scam clones*) with their schedule,
  enabled toggle, **Run now** and **Last log** - the same jobs as the Daily page, not copies; the
  fake-shop radar form; and the **Red Points** affiliate card with its disclosure.
- **`engine/osint.py`**:
  - `scan()` - the fake-shop radar: reads a domains-monitor zip, txt, CSV or URL, matches domain
    labels against the protected brands (brand inside the domain, brand + shop/store/outlet word,
    brand on a shop TLD), stores every hit as a `fake_shop` signal.
  - `profile()` / `profile_new()` - what a bare domain is: title, description, visible text,
    emails, phones, a guess at the business, saved in `osint_domains` and back-filled into the
    lead's domain.
  - `meta_ads()` - advertisers in the public Meta Ad Library through its GraphQL endpoint (no
    key), walked, deduplicated and stored as `meta_advertiser` signals.
- **`engine/jobs/osint.py`**: *Fake-shop radar* (Signals 06:20), *Meta Ad Library prospects*
  (Signals Mon 09:00), *Profile bare domains* (Data 11:00).
- Routes `/api/osint/state|scan|profile|meta-ads`, and `osint` in the state payload.
- **Meta Ad Library card** lives on the **Ads** page, above the platform sub-tabs.
- **Identify a bare domain** card lives on the **Domains** page, under the OpenStreetMap card.
- `tests/test_osint.py`: brand matching (hit, shop TLD, own domain, unrelated), a txt feed scan
  that stores hits, refusal without brands, the Red Points link, the Ad Library walker, and a
  mocked page turned into a profile.

### Tests

484 (was 474), all passing.

## [1.31.0] - 2026-11-16

OpenStreetMap and the hot list are native now - no scripts outside the app.

### Added

- **`engine/osm.py`**: a free-text offer becomes OSM tags (a map of the usual trades, and an
  unknown term falls through to a `name~/craft~/shop~` search, so *any* business type works), the
  place is geocoded with Nominatim, and the Overpass API answers with the businesses. Rows go into
  the new `osm_business` table, so a repeated search costs nothing; the three lists are queries
  over it: with an email, website only, phone only. `import_ready()` turns the ones with a contact
  into leads (`source='osm'`), reusing the normal importer. Four Overpass mirrors, a second pass
  after a pause, and a clear "free service is busy, try again" message.
- **Domains page**: a *Find local businesses (OpenStreetMap)* card - what to look for, where (or
  `roofers in Slovakia`, `dentists, Bratislava`), how many, then **Find** (counts of the three
  lists) and **Import the ones with a contact**. ODbL credit is on the card.
- **`engine/hotlist.py`**: today's hot list built from the app's own signals, leads and offers -
  heat, offer, signal, reason, the first email question, address, name, title, email confidence,
  company, domain, evidence. Writes `data/hot/hot_<date>.csv` with the same columns as the list
  the user already reads. Signal kinds with no buying intent (`mx_missing`) are left out.
- **Daily page**: a *TODAY HOT LIST* card with a **Build today's hot list** button and the table.
- Routes `/api/osm/search`, `/api/osm/import`, `/api/osm/status`, `/api/hot/today`.
- The OSM job now runs the native search (`text`, `area`, `limit`, `import`); the hot-list job runs
  the native builder. The whole-pipeline job still calls `daily_pipeline.py`, which is an
  orchestrator of its own scripts.

### Measured

- "roofers in Slovakia": **21 businesses, 10 with an email, 6 website only, 1 phone only**;
  import turned **16 with a contact into 16 leads**.
- The hot list on the current data: 1,060 signals in two days, natives templates and offer
  matching working; the rows are WARM because the Signals jobs (brand watch, ads) had not filled
  the dropped-tool kinds yet.

### Tests

474 (was 463): offer parsing (trade map, comma form, unknown term, empty), domain extraction,
storage upsert (no duplicates), the three lists, import into leads, the hot list's heat/offer/
question/confidence, noise kinds excluded, and the CSV header.

## [1.30.0] - 2026-11-16

Three jobs that call the older tooling, instead of re-inventing it.

### Added

- **Run the whole pipeline** (Data, daily 07:15): runs `LEAD_STUDIO/daily_pipeline.py run` — new
  domains (smet.cz + WhoisExtractor) → Google Ads Transparency → fraud listings (Watchlist +
  Phishing.Database) → Red Points cases against the brand dataset → cross-check of new domains
  against the fraud lists. Stages are declared in `PIPELINE/config.json`, a failing stage does not
  stop the rest, and the job parses the report's `handoffs` section into signals so the files it
  names are visible in the app.
- **OpenStreetMap business scraper** (Domains, weekly Mon 08:30): runs
  `_CLEAN_2026-11-02/osm_icp_scrape.py` for an offer (area/state/city), then imports the
  `*_1_WITH_EMAIL_*.csv` it wrote and reports the size of the website-only and phone-only lists.
  The scraper's own `OSM_CACHE` makes it resumable.
- **TODAY HOT LIST** (Signals, daily 10:30): runs `_CLEAN_2026-11-02/signal_monitor.py` in `report`
  or `daily` mode, imports the addresses from `SIGNALS/HOT_TODAY/hot_<date>.csv` and records the
  file and its row count as a signal.
- `engine/external.py`: locating the two folders (settings `external.studio` / `external.clean`,
  with defaults), running a script and keeping its output tail, finding the newest file, parsing
  the pipeline report's handoffs, and importing a CSV through the same importer the UI uses. A
  missing folder is a named error, not a stack trace.

### Verified

- `daily_pipeline.py stages` lists the seven stages (nrd, fraud, radar, cases, cross, jobs off,
  ads); `osm_icp_scrape.py --list-categories` lists the OSM categories per offer; the newest hot
  list on disk is `hot_2026-11-14.csv` with **1,692 rows**, the same count the app's own page shows.

### Tests

463 (was 452): the four handoffs parsed from a real report with their row counts, a report without
that section, the newest-file pick, the row counter, a script's output tail, a failing script
reported rather than raised, and each job naming the folder it is missing.

## [1.29.0] - 2026-11-16

An import shows its progress, and it is fast enough for the bar to matter.

### Added

- **A progress bar on both imports** — the uploaded file and the file on disk. The import runs in
  a background thread, `POST /api/lead/import` and `/api/lead/import/path` return an `import_id`,
  and the modal polls `/api/import/progress` every half second: phase, percent, rows so far,
  done/total, rows a second, and **time left**. Finished runs return the summary, so the bar ends
  on the result instead of a spinner.
- `engine/progress.py`: an in-memory registry (no writes per tick), with percent, rate and an
  estimate rounded up so a nearly finished run shows a second rather than zero.
- `report(done, total, phase, rows)` callbacks through `import_data`, `import_path`,
  `iter_source_rows` and `import_rows`; a streamed Parquet file reports its batch progress against
  the real row count from the file metadata.

### Fixed

- **The import was scanning the whole table for every row.** Two index problems, both now fixed:
  - `idx_leads_email` is a *partial* unique index (`WHERE email <> ''`), and SQLite cannot use it
    for `WHERE email=?` because a parameter does not imply the predicate. A new
    `idx_leads_email_lookup` covers plain lookups: **56 ms → 0.1 ms per row** on 123k leads.
  - after `_rebuild_leads` the `idx_leads_*` indexes could be left on the renamed-away table, so
    `CREATE INDEX IF NOT EXISTS` skipped them for `leads` entirely. `init_db()` now drops those
    orphans and rebuilds them on the right table.
  - the company lookups take `INDEXED BY idx_leads_company_blank`, since otherwise the planner
    picked the email index for `email='' AND company=?`.

### Measured

- 2,000 rows: 125 s → **0.3 s**.
- 60,000 rows through the API: **~4,500 rows a second**, the bar stepping 42% → 100% with a 9 s
  estimate at the start, ending `done 100%` and `imported 60000`.

### Tests

452 (was 446): percent/rate/estimate, a finished run carrying its result, a failure reported,
an unknown id, a late update ignored, and a run with no known total.

## [1.28.0] - 2026-11-15

The official Common Crawl index in the Hugging Face bucket is now the default.

### Changed

- **`cc_index` reads Common Crawl's own index from the bucket**:
  `hf://buckets/commoncrawl/commoncrawl/cc-index/table/cc-main/warc/crawl=<crawl>/subset=warc/part-*.parquet`.
  The bucket currently carries seven crawls — `CC-MAIN-2026-17 … CC-MAIN-2026-43` — and the
  `CC-MAIN-2026-43` shards are **the same 300 files, same UUID (`6e44f0dc…`), as the local
  `M:\index` copy**, so the two are interchangeable.
- `source="bucket"` (default), `"mirror"` (`open-index/ccrawl-urls` on the Hub, kept) or a local
  folder via `path`. A `crawl` parameter picks the archive; empty means the newest.
- Both Domains jobs (bulk index match, contacts from the archive) default to `source=bucket` and
  accept `crawl`.

### Fixed

- **`locate()`** compared a bare surtkey (`com,example`) against full bounds
  (`com,blogspot,infidel753)/2006/…`), so a key that is a *prefix* of the band's first row was
  judged smaller than the band and skipped. The comparison now uses the domain part of each bound.
  This is the fix that makes a band lookup actually find the shard.

### Measured

- Listing the crawls takes ~2 s, the 300 shard names ~0.2 s, three footers ~1.7 s.
- Scanning one shard **from the bucket** is slow — 493 s for a full 6.6M-row pass — against ~25 s
  from the local `M:\index`. The bucket is for spot checks and for planning; a bulk run should
  point `path` at the local copy.

### Tests

446 (unchanged): the index tests run against a local shard, so they cover the same code either way.

## [1.27.0] - 2026-11-15

Batched archive fetching, and the Hugging Face bucket as the fast source.

### Added

- **`engine/cc_fetch.py`**: the bulk shape a 76-pages-a-second scraper uses — plan every
  homepage/contact page first, order the plan by WARC file, download in **parallel threads** over
  **keep-alive connections**, remember each record in a `cc_fetch` table (so a stop resumes), and
  export the rows as CSV.
- **Two WARC sources**: `bucket` — Hugging Face's `commoncrawl/commoncrawl` storage bucket
  (`huggingface.co/buckets/commoncrawl/commoncrawl/resolve/…`, public, answers `206` to byte
  ranges after a signed redirect) — and `http` — `data.commoncrawl.org`.
- **`cc_index.plan_pages()`**: reads each index shard **once** for a whole batch of domains,
  instead of once per domain.

### Fixed

- The contacts job fetched **one record at a time** whenever the plan spanned many WARC files: it
  created a thread pool per file group, and most groups held a single record. There is one pool
  for the whole plan now, with the file order kept.

### Measured from this machine

- `data.commoncrawl.org`: **1.5–2.4 ranges/s**, unchanged with 16 or 32 threads — it rate-limits
  per client.
- The Hugging Face bucket: **14.9/s at 32 threads, 21.6/s at 64**. Common Crawl's own guide puts
  HF-bucket-to-edge-client below 100 receipts/s, HF-to-HF at 1,500/s and S3-to-S3 at 3,500/s.

### Tests

446 (was 440): the bucket path and its signed redirect, one pool for the whole plan, a failed page
becoming a result rather than an exception, and the resume table.

## [1.26.0] - 2026-11-15

From the index to the page: contacts out of the Common Crawl archive.

### Added

- **`engine/cc_warc.py`**: one record, fetched by byte range. The index row gives
  `warc_filename`, `warc_record_offset` and `warc_record_length`; a single Range request to
  `data.commoncrawl.org` returns that record, which is gunzipped, split into WARC and HTTP
  headers, and decoded with the page's own charset. `contacts_of()` then extracts addresses
  (including Cloudflare-obfuscated ones) and phones with the same cleaners the live crawler uses.
- **`cc_index.pages(domain)`**: the archived pages of one domain, with their WARC coordinates —
  contact/about/impressum pages first, the homepage next, failures and redirects skipped.
- **New job "Contacts from the Common Crawl archive"** in the Domains group (daily 09:30): for
  domains that have an index match but no contact yet, it takes up to four pages, fetches each
  record, extracts the address and creates the lead. It works for sites that are parked, dead or
  refuse bots — exactly the tail the live contact finder leaves behind.
- The Domains stage now runs: new domains → MX → footprint → host index → **bulk index match** →
  live contact finder → **contacts from the archive**.

### Verified live

Found `https://infografia-pedrojimenez.blogspot.com/p/contacto.html` in the local 300-shard index
(2.6 s), fetched its 18 569-byte record from `data.commoncrawl.org` in 0.5 s, parsed it
(`200`, `text/html`) and extracted `pedjima@hotmail.com`.

### Tests

440 (was 431): a gzipped and an uncompressed record, a non-record, contact extraction and page
ranking, and the page lookup (domain filter, status filter, ordering, the WARC coordinates, an
absent domain).

## [1.25.0] - 2026-11-15

The Common Crawl index itself, matched locally or from Hugging Face.

### Added

- **`engine/cc_index.py`**: match domains against the Common Crawl **columnar URL index**
  (`url_surtkey`, `url_host_registered_domain`, `fetch_time`, `fetch_status`, `warc_*`) without
  keeping 170 GB on disk. Each shard covers a narrow band of the URL sort key, so the module caches
  every shard's band from its Parquet footer (300 shards take ~0.3 s from disk, one footer read
  each), finds the shard whose band can hold `com,example`, and reads only that shard's three
  useful columns. Bands are cached in the `cc_bands` table.
- Sources: **Hugging Face** `open-index/ccrawl-urls` (303 shards, 161.7 GB, `CC-MAIN-2026-25`) by
  default, or a **local folder** with the same schema (`path` parameter, e.g. an `M:\index` copy).
- **New job "Common Crawl: bulk domain match (index)"** in the Domains group (daily 03:30, 25
  domains a run): matches the domains that have no lookup yet and writes `cc_pages`,
  `cc_last_crawl` and `cc_checked_ts`. It sits after the per-domain footprint job, which stays the
  better choice for a handful of domains because it needs no shard read at all.

### Tests

431 (was 424): the reversed key, band location (including a band that starts with the key), the
aggregation of pages / last capture / 200s from a real Parquet shard, what is written to `domains`,
a domain that is not in the index still being marked checked, and the pending queue.

## [1.24.0] - 2026-11-15

The Daily page runs in the order the data actually flows.

### Changed

- **Four numbered stages**, in this order:
  1. **Domains** — new domains feed → MX verification → Common Crawl footprint → Common Crawl
     host index → contact finder (domains → emails).
  2. **Signals** — phishing, hiring, bankruptcy, brand watch, ad transparency.
  3. **Data** — merge files into leads.
  4. **AI** — segment + angle, last, once the data exists.
- `scheduler.list_jobs()` sorts by group, then by the chain inside the group (`CHAIN_ORDER`), then
  by clock time, so a domain arrives, its mail server is checked, Common Crawl says whether the
  site is real and the contact finder looks for an address, in that order. Each stage carries a
  one-line explanation.
- The groups changed from `Signals` / `Enrichment` to `Domains` / `Signals` / `Data` / `AI`;
  `sync_registry()` rewrites existing rows, so nothing has to be recreated. Schedules are untouched.

### Tests

424 (was 419): domains before signals before data before AI, the domain chain in order, the AI job
last, every job in a known group, and the AI job still describing itself as optional.

## [1.23.0] - 2026-11-15

The campaigns table shows the sending platform's own numbers.

### Added

- Manyreach campaigns keep their statistics on the sequence (`sequences.stats_json`): prospects,
  reached, replies, interested, conversions, bounce count, sender count, status and the campaign's
  own created date — matching the figures Manyreach shows.
- Opens and clicks are summed from `GET /api/v2/campaigns/{id}/stats` (its series are
  `{"count": [...]}` arrays with nulls), for campaigns that have sent anything, capped at 25 lookups
  per import.
- The table gained a totals row and prints a dash where a platform-sourced figure is missing rather
  than a misleading zero.

### Changed

- A re-import now refreshes the numbers of campaigns that are already there, and their date, without
  touching their steps.

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

