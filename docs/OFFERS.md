# Toolbox — the recommended tools

The **Toolbox** tab is the distributor's shelf, not a sales tool for the person using the app.

The offers in it are the tools Lead Studio is built around — ad tracking, cold-email sending,
verification, proxies, SEO, CRMs, ERP, referrals. The person who downloads the app is the audience:
they read the list, pick what they need, and sign up with the provider. Some of those links are
affiliate links, so the person who built and distributed the app may earn a commission.

The downloader's own prospects are **never** involved. There is no "pitch this offer to a lead"
anywhere in the app: lead work happens in Leads, Sequences and Inbox; the Toolbox only recommends
software.

## What the downloader sees

- **Category tabs** — `all · tracking · outreach · crm · seo · medical …`, so a list of thirty is
  navigable instead of a wall of cards.
- **Each card** — what the tool does, its category, and three actions:
  - **Open** – a panel with the description, the sign-up link, and the disclosure,
  - **Connect** – how to hook this tool up (link, API key, MCP command or webhook),
  - **Details** – a full page at `/offer/<id>` they can read or share.
- **Nothing is required.** Every feature of Lead Studio works without a single tool from the shelf.

## Disclosure, addressed to the reader

The details page and the panel both carry the disclosure, written for the person reading it:

> **Affiliate disclosure.** {company} may receive a commission or other compensation from
> {partner} if you purchase through the links on this page. This is a **material connection**
> between us and {partner}, disclosed in accordance with the applicable endorsement and
> consumer-protection rules, including the U.S. FTC Endorsement Guides (**16 C.F.R. Part 255**) and
> the **EU Unfair Commercial Practices Directive** (Directive 2005/29/EC, as amended). The
> commission is paid by {partner} and is already included in the price you pay: it is **at no
> additional cost to you**, it does not increase the price, and you pay the same amount as when
> buying directly. Our recommendation is our own assessment; we act neither as {partner}'s agent
> nor as a party to your agreement with them.

`{company}` comes from Settings → *Company*, `{partner}` from the offer's link domain. A pipeline
makes no difference to the price the downloader pays — that sentence is the point of the whole
paragraph.

## Read-only by default

A distributed build ships with the shelf **locked**: the downloader sees the tools, the links and
the pages, and cannot edit or delete them. That keeps the links, the wording and the disclosure
exactly as you shipped them.

To manage your own copy, Settings → **Keep the Toolbox read-only** → untick. New offer, Import JSON
and Load starter pack then appear. Turn it back on before you package the build.

## The starter pack

`samples/offers-pack.json` holds 29 tools in 20 categories. Load it with **Load starter pack**
(unlocked), or from the command line:

```bash
python leadstudio.py --seed-offers
```

If you distribute this app, **the links in that file should be yours**. They carry the referral
codes of whoever built the pack; replacing them is the difference between recommending a tool and
earning from a recommendation. Replace them with your own before you publish the pack.

## Managing the shelf

| Field | Purpose |
| --- | --- |
| Name, category | What it is and where it sits in the tabs |
| Description | The one or two sentences the reader sees |
| Sign-up link | Affiliate or plain URL; `https://` is added automatically |
| Keywords | Kept for search and for the tool's own page |
| Commission | Your note, e.g. `20% recurring` (visible in the panel) |
| Connections | link / api_key / mcp / webhook — how the downloader hooks it up |

Secrets in connections are stored locally and masked in the UI (`saved:***ab`).

## What this is not

- Not a lead list, and not a pitch to the downloader's prospects — that was the earlier design and
  it was wrong.
- Not tracked attribution: the app does not know whether a click converted. Use the affiliate
  programme's dashboard for that.
- Not a price comparison: the description is what the vendor says about itself plus your note.

## Legal note

Recommending software you earn from is fine as long as the recommendation is genuine and the
material connection is disclosed clearly and conspicuously — on the page, before the link, in
plain words. The default wording does that; if you change it, keep the substance: commission, no
extra cost, and the fact that the recommendation is yours.
