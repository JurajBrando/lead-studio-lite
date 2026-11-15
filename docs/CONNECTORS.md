# Connectors (optional API keys)

Three services, all optional, all off until you paste a key in **Settings → Connectors**. Nothing in
the app breaks without them; the jobs that use them either skip or report what is missing.

Keys live in `data/settings.json` on your computer, like the mailbox passwords. The browser never
receives them back — Settings only knows whether one is saved.

---

## Hunter.io — find the address behind a domain

**What it buys you:** the free crawler reads the address a company publishes. Hunter finds the ones
it doesn't (older sites, forms only, personal addresses with confidence scores).

- Used by: the **contact finder** job (`hunter_fallback: true`) and by MCP automations.
- Get a key: [hunter.io](https://hunter.io/) → API → copy the key. There is a free tier.
- Field: **Hunter.io API key**.
- Called endpoints: `/v2/domain-search` and `/v2/email-verifier`.
- Cost control: the job asks for at most 5 addresses per domain and only when the crawl found none.

## AI — classify a company, write the angle

**What it buys you:** instead of an empty `industry` column, every lead gets a segment, a size guess,
a country and one sentence explaining why they might need help — written from what their homepage
actually says.

- Used by: the **AI enrichment** job.
- Any **OpenAI-compatible** endpoint works:

| Provider | Base URL | Model example |
| --- | --- | --- |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` |
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` |
| Ollama Cloud | `https://ollama.com/v1` | `gpt-oss:120b` |
| Local (Ollama) | `http://127.0.0.1:11434/v1` | `llama3.1` |

- Fields: **AI base URL**, **AI model**, **AI API key** (a local server usually accepts any
  non-empty string).
- The job sends the page title, meta description and up to 2,500 characters of visible text. No lead
  list, no email addresses, no mailbox credentials.
- It is deliberately conservative: it is told to answer `unknown` rather than guess, and the result
  is stored as *tags* plus a note, never as a fact you cannot see.

## Serper — company name to official website

**What it buys you:** you have a list of company names and no domains. Serper returns Google results;
the connector picks the first non-Wikipedia, non-LinkedIn domain.

- Used by: MCP tools and future jobs (the crawler needs a domain to start).
- Get a key: [serper.dev](https://serper.dev/) — free credits on signup.
- Field: **Serper API key**.

---

## How to add a key

1. **Settings → Connectors** (bottom half of the Settings page).
2. Paste the key. Leave a field blank to keep the one already saved.
3. **Save settings**. The badges at the top of the card turn green: `hunter on`, `ai on`, `serper on`.
4. Run the job from **Automation** to see it work.

To remove a key, replace it with a different one. To wipe them all, delete `data/settings.json`
(this also removes mailboxes and settings — export your leads first).

## What is *not* here, on purpose

The old Lead Studio had connectors for ManyReach, Postiz, OpenWA/WhatsApp, Agent Reach, Metabase,
OpenRefine, Langfuse and several scrapers. They are not in this edition because each one needs an
account, a private endpoint or a paid plan that a person downloading a free tool does not have, and
a connector nobody can configure is worse than no connector. If you want one back, the pattern is
small: add the key to `config.DEFAULTS["connectors"]`, a function in `engine/connectors.py`, and call
it from a job.
