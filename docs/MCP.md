# MCP server

Lead Studio Lite ships a **Model Context Protocol** server, so an AI client (Claude Desktop, or any
MCP-capable agent) can read and drive the app: search leads, import a CSV, run an enrichment job,
read the signal feed.

It speaks JSON-RPC over **stdio** — no port, no HTTP, no exposure to the network.

## Run it

```bash
python leadstudio_mcp.py
```

Nothing is printed to stdout except protocol messages; diagnostics go to stderr.

## Register it in Claude Desktop

Edit `%APPDATA%\Claude\claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "lead-studio-lite": {
      "command": "python",
      "args": ["C:/Users/you/LeadStudioLite/leadstudio_mcp.py"]
    }
  }
}
```

Restart Claude Desktop. The tools appear under the plug icon. Use an absolute path with forward
slashes (or doubled backslashes) — the client does not expand `~` or `%APPDATA%` inside `args`.

Any other MCP client works the same way: `command: python`, `args: [path/to/leadstudio_mcp.py]`.

### If you use the standalone `.exe` (no Python installed)

The executable can act as the MCP server too — add `--mcp`:

```json
{
  "mcpServers": {
    "lead-studio-lite": {
      "command": "C:/Users/you/LeadStudioLite/LeadStudio.exe",
      "args": ["--mcp"]
    }
  }
}
```

## Tools

| Tool | Arguments | What it does |
| --- | --- | --- |
| `stats` | — | Dashboard numbers: leads, sends, replies, signals, jobs. |
| `lead_search` | `query`, `status`, `page`, `per` | Search and page through leads. |
| `lead_create` | `email` (required), name/company/title/domain/city/country/industry/notes | Create one lead. |
| `lead_set_status` | `ids`, `status` | Move leads along the pipeline. |
| `lead_delete` | `ids` | Delete leads. |
| `lead_import_csv` | `csv` (required), `name`, `update_existing` | Import CSV text with the same auto-mapping as the UI. |
| `sequence_list` | — | Sequences with their steps and counts. |
| `enroll_leads` | `sequence_id`, `lead_ids`, `mailbox_id` | Put leads into a sequence. |
| `enrollment_status` | `status` | Who is running, paused or finished. |
| `job_list` | — | Automation jobs, schedules and last run. |
| `job_run` | `key` | Run one job now and return its summary. |
| `signals_search` | `kind`, `limit` | The signal feed (new domains, ads, hiring, filings, fraud). |
| `domains_search` | `query`, `limit` | The enriched domain table. |
| `inbox_recent` | `limit` | Recent replies and bounces. |

## Example prompts

> "Search my leads for anyone in logistics and tell me which ones replied."
> "Run the bankruptcy job, then list every bankruptcy signal from this week."
> "Import this CSV: … and put the new leads into the 'Cold outreach - 3 touches' sequence."
> "Which domains have no mail server? Don't enroll those."

## Safety

- The server only touches `data/` on this machine. It cannot send email: there is no send tool, and
  the sending queue still obeys the daily caps and the unsubscribe list.
- `job_run` executes a real job (network downloads included). Ask the client to confirm before
  running the heavier ones (Google Ads Transparency downloads ~250 MB).
- Treat the client's conversation as untrusted input: `lead_delete` exists, so keep backups of
  `data/leadstudio.db` if you let an agent drive unattended.

---

# The other direction: MCP servers this app calls

Lead Studio can also be an MCP **client**. Settings → *MCP connections* lists the remote servers
it knows how to talk to; today that is **Manyreach** (`https://mcp.manyreach.com/mcp`).

## Connecting

1. *Connect* → the app reads `/.well-known/oauth-protected-resource`, then the authorization
   server's metadata, then registers itself with dynamic client registration (public client,
   PKCE S256, loopback redirect).
2. Your browser opens the Manyreach authorization page — **paste your Manyreach API key there**,
   never into the chat.
3. The loopback redirect (`http://127.0.0.1:<port>/oauth/mcp/manyreach/callback`) stores the
   access and refresh tokens in `data/settings.json`, and refreshes them when they expire.

*Disconnect* throws the tokens away; Manyreach keeps the app authorised on its side until you
remove it there.

## Using it

*Tools* asks the server for `tools/list` and shows every tool with its argument schema filled in
as a JSON skeleton. *Run* sends `tools/call` and prints the raw answer — that is the escape hatch
for any tool the app does not wrap in its own UI.

`engine/mcp_client.py` is generic: a connection is an entry in `CONNECTIONS` (name, label, URL,
note), and nothing in the module is service-specific. Adding another remote MCP server is a
five-line dict entry plus, if you want to test it, a case in `tests/test_mcp_client.py`.

## What it does not do

- It never stores the API key itself — the key is typed into Manyreach's own page, and only the
  OAuth tokens come back.
- It does not proxy arbitrary JSON-RPC: only `initialize`, `tools/list`, `tools/call` and the
  `notifications/initialized` the protocol requires.
- Like every other connector, it only runs when you press something. Nothing is sent on a timer.

## API

| Endpoint | What it does |
| --- | --- |
| `GET /api/mcp/status` | The configured connections, connected/expired. |
| `POST /api/mcp/start` | Begin the OAuth flow; returns the URL to open. |
| `POST /api/mcp/tools` | `tools/list` for one connection. |
| `POST /api/mcp/call` | `tools/call` with a JSON arguments object. |
| `POST /api/mcp/disconnect` | Forget the tokens. |
