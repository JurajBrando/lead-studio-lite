"""Chat with the app.

The dashboard chat is not a chatbot bolted on the side: it can call the same
tools the MCP server exposes, so "add these leads", "run the bankruptcy job" or
"what did the phishing feed find" actually happen.

To keep it provider-agnostic, the model is asked to answer with JSON:

    {"tool": "lead_search", "arguments": {"query": "dental"}}
    {"answer": "Here is what I found ..."}

A reply that is not JSON is treated as the answer, so a model that ignores the
protocol still produces something useful instead of an error.
"""
import json
import re

from . import llm

MAX_STEPS = 5
RESULT_CHARS = 2200

TOOLS = {
    "stats": "dashboard counters",
    "lead_search": "search leads (query, status, page, per)",
    "lead_create": "create one lead (email required)",
    "lead_set_status": "set pipeline status (ids, status)",
    "lead_import_csv": "import CSV text (csv, name)",
    "sequence_list": "list sequences",
    "enrollment_status": "list enrollments (status)",
    "job_list": "list automation jobs and schedules",
    "job_run": "run one job (key)",
    "signals_search": "read the signal feed (kind, limit)",
    "domains_search": "search enriched domains (query, limit)",
    "offers_list": "list offers with affiliate links and match counts",
    "offer_match": "leads that fit an offer (offer_id, limit)",
    "llm_providers": "configured LLM providers",
    "inbox_recent": "recent replies and bounces (limit)",
}

SYSTEM = """You operate Lead Studio Lite, a local sales-development app.

You can use these tools by replying with JSON and nothing else:
{{"tool": "<name>", "arguments": {{...}}}}

Tools:
{tools}

When you have the answer, reply with JSON: {{"answer": "<short answer for the user>"}}
Rules:
- Never invent data: call a tool and use its result.
- Ask before running job_run for the heavy jobs (ads_transparency downloads ~250 MB).
- You cannot delete anything; if asked, explain that it must be done in the UI.
- Keep answers short and concrete; include counts and a few example rows, not raw JSON.
"""


def _parse(text_value):
    if not text_value:
        return None
    match = re.search(r"\{.*\}", text_value, re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def run(message, history=None, max_steps=MAX_STEPS, provider_id=None):
    if not llm.status().get("configured"):
        return {"ok": False, "error": "No LLM provider configured. Add one in Settings -> LLM providers."}
    text_value = (message or "").strip()
    if not text_value:
        return {"ok": False, "error": "Type a message first."}

    messages = [{"role": "system",
                 "content": SYSTEM.format(tools="\n".join("- %s: %s" % (k, v)
                                                          for k, v in TOOLS.items()))}]
    for turn in (history or [])[-8:]:
        role = turn.get("role") if isinstance(turn, dict) else None
        content = turn.get("content") if isinstance(turn, dict) else None
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": str(content)[:4000]})
    messages.append({"role": "user", "content": text_value})

    steps = []
    for _ in range(max(1, min(8, max_steps))):
        try:
            reply = llm.chat(messages, provider_id=provider_id, max_tokens=900, temperature=0.2)
        except llm.LLMError as exc:
            return {"ok": False, "error": str(exc), "steps": steps}

        data = _parse(reply)
        if not data:
            return {"ok": True, "answer": reply.strip()[:4000], "steps": steps}

        if data.get("answer") is not None:
            return {"ok": True, "answer": str(data["answer"])[:4000], "steps": steps}

        tool = str(data.get("tool") or "").strip()
        arguments = data.get("arguments") if isinstance(data.get("arguments"), dict) else {}
        if tool not in TOOLS:
            messages.append({"role": "assistant", "content": reply[:2000]})
            messages.append({"role": "user", "content":
                             "That tool does not exist. Use one of: %s" % ", ".join(TOOLS)})
            continue

        try:
            from . import mcp
            result = mcp._run_tool(tool, arguments)
        except Exception as exc:                       # noqa: BLE001
            result = {"ok": False, "error": str(exc)}
        steps.append({"tool": tool, "arguments": arguments,
                      "ok": bool(result.get("ok", True)) if isinstance(result, dict) else True})
        try:
            rendered = json.dumps(result, ensure_ascii=False, default=str)[:RESULT_CHARS]
        except (TypeError, ValueError):
            rendered = str(result)[:RESULT_CHARS]
        messages.append({"role": "assistant", "content": reply[:2000]})
        messages.append({"role": "user", "content": "TOOL RESULT %s: %s" % (tool, rendered)})

    return {"ok": True, "steps": steps,
            "answer": "I stopped after %d tool calls without a final answer. Ask me to continue."
                      % len(steps)}
