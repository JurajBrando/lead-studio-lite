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

