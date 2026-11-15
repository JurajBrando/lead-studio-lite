# Match and the dashboard chat

Two LLM-powered features that use the provider configured in Settings → LLM providers. Both degrade
gracefully: without a provider, matching still works on keywords and the chat tells you what to add.

## Match

**Match** answers the question the offer catalogue should not: *which of my leads actually fit what I
sell?*

1. Describe your business in a paragraph, or paste your website.
2. The app builds a profile from it:
   - with an LLM: industry, segment, a summary and 8–12 keywords describing **who needs it**,
   - without one: the significant words of your text.
3. Every lead is scored against those keywords (industry 4, tags 3, company 2, title 2, notes 1).
4. You get the fitting leads, best first, each with the words that matched.

Then **Pitch** with whichever offer you pick from the dropdown - that is where the affiliate link and
the disclosure come in.

Why it is separate from Offers: an offer is a product you resell; a match is a property of *your*
description against *your* database. Keeping them apart means one catalogue, many ways to search it.

## Dashboard chat

The assistant on the Dashboard is not a chat window bolted on the side - it can **call the same tools
the MCP server exposes**, so it can actually do things:

| It can | Ask it |
| --- | --- |
| Search and create leads | "Find dental leads and show the best five." |
| Import CSV | "Import these rows: …" |
| Change pipeline status | "Mark everyone who replied as interested." |
| Run automation jobs | "Run the bankruptcy job and summarise it." |
| Read signals and domains | "What did the phishing feed find this week?" |
| Work with offers | "Which offer has the most matching leads? Pitch the top three." |
| Read the inbox | "Any replies since yesterday?" |

It **cannot** delete anything and **cannot** send email; those stay manual on purpose. Heavy jobs
(Google Ads Transparency downloads ~250 MB) are supposed to be confirmed first - the system prompt
tells it to ask.

### How it works

The model is asked to answer with JSON only:

```json
{"tool": "lead_search", "arguments": {"query": "dental"}}
{"answer": "You have 4 dental leads; the three with a verified address are …"}
```

The app runs the tool through the MCP dispatcher, feeds the result back, and repeats up to five
steps. A reply that is not JSON is treated as the final answer, so a model that ignores the protocol
still produces something useful.

Every tool call is shown in the transcript ("used tool: lead_search"), so you can see exactly what
it did before you trust the answer.
