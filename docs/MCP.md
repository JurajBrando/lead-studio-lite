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
