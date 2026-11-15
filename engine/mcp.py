"""Model Context Protocol (MCP) server for Lead Studio Lite.

Speaks JSON-RPC 2.0 over stdio: one JSON object per line on stdin, one JSON
object per line on stdout, and nothing but protocol traffic on stdout -
diagnostics go to sys.stderr.

Tool calls are dispatched straight to the handler functions that already exist
in engine.server, so no HTTP round trip and no extra port is involved.

The protocol logic lives in the pure function handle_request(request,
call_tool=None), which maps a parsed request dict to a response dict (or None
for notifications). main() is only the transport loop around it, which keeps
the protocol testable without touching stdin/stdout.
"""
import contextlib
import inspect
import json
import sys

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "lead-studio-lite"

_NO_RESPONSE = object()


# --------------------------------------------------------------- errors
class _ToolError(Exception):
    """A tool could not run - a required argument was missing or malformed."""


class _BadParams(Exception):
    """The JSON-RPC params were malformed (mapped to -32602)."""


class _UnknownMethod(Exception):
    """The JSON-RPC method is not implemented (mapped to -32601)."""


# ---------------------------------------------------------------- schemas
def _prop(kind, description, **extra):
    prop = {"type": kind, "description": description}
    prop.update(extra)
    return prop


def _schema(properties, required):
    return {"type": "object", "properties": properties, "required": list(required)}


_LEAD_IDS = {"type": "array", "items": {"type": "integer"}, "minItems": 1,
             "description": "Lead ids (the integer id field) to act on."}

TOOLS = [
    {"name": "stats",
     "description": "Return the dashboard counters, settings, mailboxes, sequences and "
                    "worker status.",
     "inputSchema": _schema({}, [])},
    {"name": "lead_search",
     "description": "Search leads by free-text query and/or pipeline status, returning one "
                    "page of results.",
     "inputSchema": _schema({
         "query": _prop("string", "Free text matched against email, name, company and domain."),
         "status": _prop("string", "Only return leads in this pipeline status."),
         "page": _prop("integer", "1-based page number (default 1).", minimum=1),
         "per": _prop("integer", "Page size, 10 to 200 (default 50).", minimum=10, maximum=200),
     }, [])},
    {"name": "lead_create",
     "description": "Create a lead from an email address plus any optional profile fields.",
     "inputSchema": _schema({
         "email": _prop("string", "The lead's email address; this is the unique identifier."),
         "first_name": _prop("string", "First name."),
         "last_name": _prop("string", "Last name."),
         "company": _prop("string", "Company name."),
         "title": _prop("string", "Job title."),
         "domain": _prop("string", "Company domain; defaults to the email domain."),
         "city": _prop("string", "City."),
         "country": _prop("string", "Country."),
         "industry": _prop("string", "Industry."),
         "notes": _prop("string", "Free-text notes about the lead."),
     }, ["email"])},
    {"name": "lead_set_status",
     "description": "Set the pipeline status of one or more leads by id.",
     "inputSchema": _schema({
         "ids": _LEAD_IDS,
         "status": _prop("string", "The new pipeline status.",
                         enum=["new", "contacted", "replied", "interested", "won", "lost",
                               "bounced", "unsubscribed"]),
     }, ["ids", "status"])},
    {"name": "lead_delete",
     "description": "Permanently delete one or more leads by id.",
     "inputSchema": _schema({"ids": _LEAD_IDS}, ["ids"])},
    {"name": "lead_import_csv",
     "description": "Import leads from CSV text that contains an email column.",
     "inputSchema": _schema({
         "csv": _prop("string", "The full CSV document as text, with a header row."),
         "name": _prop("string", "File name to record for the import (default upload.csv)."),
         "update_existing": _prop("boolean",
                                  "Update fields of leads that already exist instead of "
                                  "skipping them."),
     }, ["csv"])},
    {"name": "sequence_list",
     "description": "List the email sequences with their steps and enrollment counts.",
     "inputSchema": _schema({}, [])},
    {"name": "enroll_leads",
     "description": "Enroll leads into a sequence so its steps get sent on schedule.",
     "inputSchema": _schema({
         "sequence_id": _prop("integer", "Id of the sequence to enroll into."),
         "lead_ids": {"type": "array", "items": {"type": "integer"}, "minItems": 1,
                      "description": "Ids of the leads to enroll."},
         "mailbox_id": _prop("integer", "Optional id of the sending mailbox to use."),
     }, ["sequence_id", "lead_ids"])},
    {"name": "enrollment_status",
     "description": "List sequence enrollments, optionally filtered by enrollment status.",
     "inputSchema": _schema({
         "status": _prop("string",
                         "Only return enrollments in this status, e.g. running or paused."),
     }, [])},
    {"name": "job_list",
     "description": "List the automation jobs with their schedules, parameters and last run.",
     "inputSchema": _schema({}, [])},
    {"name": "job_run",
     "description": "Run one automation job immediately by its key.",
     "inputSchema": _schema({
         "key": _prop("string", "The job key as shown by job_list."),
     }, ["key"])},
    {"name": "signals_search",
     "description": "Search buying signals, optionally filtered by signal kind.",
     "inputSchema": _schema({
         "kind": _prop("string", "Only return signals of this kind."),
         "limit": _prop("integer", "Maximum number of signals, 1 to 500 (default 200).",
                        minimum=1, maximum=500),
     }, [])},
    {"name": "domains_search",
     "description": "Search tracked domains by name or domain, with their MX and "
                    "enrichment status.",
     "inputSchema": _schema({
         "query": _prop("string", "Free text matched against the domain and company name."),
         "limit": _prop("integer", "Maximum number of domains, 1 to 500 (default 200).",
                        minimum=1, maximum=500),
     }, [])},
    {"name": "inbox_recent",
     "description": "Return the most recent inbound replies together with the unread count.",
     "inputSchema": _schema({
         "limit": _prop("integer", "Maximum number of replies, 1 to 300 (default 100).",
                        minimum=1, maximum=300),
     }, [])},
]

TOOL_NAMES = frozenset(tool["name"] for tool in TOOLS)


# --------------------------------------------------------------- protocol
TOOLS.extend([
    {"name": "offers_list",
     "description": "List the offers you resell, with their affiliate links, matching keywords "
                    "and how many leads match each one.",
     "inputSchema": _schema({}, [])},
    {"name": "offer_match",
     "description": "Return the leads that best match one offer, scored by keyword overlap.",
     "inputSchema": _schema({
         "offer_id": _prop("integer", "The offer id from offers_list.", minimum=1),
         "limit": _prop("integer", "How many matches to return (default 25).", minimum=1,
                        maximum=200),
     }, ["offer_id"])},
    {"name": "offer_pitch",
     "description": "Draft the pitch email for one offer and one lead, with the affiliate link "
                    "appended. Uses your template, or your default LLM when ai is true.",
     "inputSchema": _schema({
         "offer_id": _prop("integer", "The offer id from offers_list.", minimum=1),
         "lead_id": _prop("integer", "The lead to write to.", minimum=1),
         "ai": _prop("boolean", "Rewrite with the default LLM provider instead of the template."),
     }, ["offer_id", "lead_id"])},
    {"name": "llm_providers",
     "description": "List the configured LLM providers and which one is the default.",
     "inputSchema": _schema({}, [])},
])


def _error(req_id, code, message):
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _version():
    """engine.config.VERSION, read lazily so importing this module stays cheap."""
    try:
        from . import config
        return str(config.VERSION)
    except Exception:                                  # noqa: BLE001
        return "0.0.0"


def handle_request(request, call_tool=None):
    """Turn one parsed JSON-RPC request into a response dict, or None.

    Pure: no stdin/stdout, no globals are mutated. `call_tool` is an optional
    callable for tests; the real tool dispatcher is used when it is omitted.
    """
    if not isinstance(request, dict):
        return _error(None, -32600, "Invalid Request: expected a JSON object.")
    has_id = "id" in request
    req_id = request.get("id")
    method = request.get("method")
    if not isinstance(method, str) or not method.strip():
        return _error(req_id, -32600, "Invalid Request: missing method.") if has_id else None
    try:
        result = _dispatch(method.strip(), request.get("params"), call_tool)
    except _UnknownMethod as exc:
        return _error(req_id, -32601, str(exc)) if has_id else None
    except _BadParams as exc:
        return _error(req_id, -32602, str(exc)) if has_id else None
    except Exception as exc:                           # noqa: BLE001 - never kill the loop
        return _error(req_id, -32603, "Internal error: %s" % exc) if has_id else None
    if result is _NO_RESPONSE or not has_id:
        return None
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _dispatch(method, params, call_tool):
    if method == "initialize":
        return {"protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": _version()}}
    if method in ("notifications/initialized", "notifications/cancelled"):
        return _NO_RESPONSE
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        return _call_tool(params, call_tool)
    if method == "resources/list":
        return {"resources": []}
    if method == "resources/templates/list":
        return {"resourceTemplates": []}
    if method == "prompts/list":
        return {"prompts": []}
    raise _UnknownMethod("Method not found: %s" % method)


def _call_tool(params, call_tool):
    if not isinstance(params, dict):
        raise _BadParams("params must be an object with name and arguments.")
    name = params.get("name")
    if not isinstance(name, str) or not name.strip():
        raise _BadParams("params.name must be a non-empty string.")
    name = name.strip()
    arguments = params.get("arguments")
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise _BadParams("params.arguments must be a JSON object.")
    if name not in TOOL_NAMES:
        return _tool_result({"ok": False, "error": "Unknown tool: %s" % name}, is_error=True)
    runner = call_tool if call_tool is not None else _run_tool
    try:
        result = _invoke(runner, name, arguments)
    except _ToolError as exc:
        return _tool_result({"ok": False, "error": str(exc)}, is_error=True)
    return _tool_result(result)


def _invoke(runner, name, arguments):
    """Call the dispatcher with (name, arguments), or just name if it takes one."""
    try:
        params = list(inspect.signature(runner).parameters.values())
    except (TypeError, ValueError):
        return runner(name, arguments)
    positional = [p for p in params
                  if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    varargs = any(p.kind is inspect.Parameter.VAR_POSITIONAL for p in params)
    if varargs or len(positional) >= 2:
        return runner(name, arguments)
    return runner(name)


def _tool_result(result, is_error=None):
    if is_error is None:
        is_error = isinstance(result, dict) and result.get("ok") is False
    text = json.dumps(result, ensure_ascii=False, default=str)
    return {"content": [{"type": "text", "text": text}], "isError": bool(is_error)}


# ---------------------------------------------------------------- tools
def _required(args, key):
    value = args.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise _ToolError("Missing required argument: %s" % key)
    return value


def _optional_string(args, key):
    value = args.get(key)
    return None if value is None else str(value)


def _optional_int(args, key):
    value = args.get(key)
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise _ToolError("Argument %s must be an integer." % key) from None


def _required_int(args, key):
    value = _required(args, key)
    try:
        return int(value)
    except (TypeError, ValueError):
        raise _ToolError("Argument %s must be an integer." % key) from None


def _int_list(args, key):
    value = _required(args, key)
    if not isinstance(value, (list, tuple)):
        raise _ToolError("Argument %s must be a list of integers." % key)
    if not value:
        raise _ToolError("Argument %s must not be empty." % key)
    try:
        return [int(item) for item in value]
    except (TypeError, ValueError):
        raise _ToolError("Argument %s must be a list of integers." % key) from None


def _compact(payload):
    """Drop keys whose value is None so handlers keep their own defaults."""
    return {k: v for k, v in payload.items() if v is not None}


def _run_tool(name, arguments):
    """The real dispatcher: calls engine.server / engine.scheduler directly.

    Imports happen here, lazily, so a heavy or broken dependency of the web
    server can never break tools/list.
    """
    args = arguments or {}

    if name == "job_list":
        from . import scheduler
        return scheduler.list_jobs()
    if name == "job_run":
        from . import scheduler
        return scheduler.run_job(str(_required(args, "key")), trigger="mcp")

    from . import server as web

    if name == "offers_list":
        return web.r_offers()
    if name == "offer_match":
        return web.r_offer_matches({"offer_id": _required_int(args, "offer_id"),
                                    "limit": _optional_int(args, "limit")})
    if name == "offer_pitch":
        return web.r_offer_pitch({"offer_id": _required_int(args, "offer_id"),
                                  "lead_id": _required_int(args, "lead_id"),
                                  "ai": bool(args.get("ai"))})
    if name == "llm_providers":
        return web.r_llm_providers()

    if name == "stats":
        return web.r_state()
    if name == "lead_search":
        return web.r_leads(_compact({
            "q": _optional_string(args, "query"),
            "status": _optional_string(args, "status"),
            "page": _optional_int(args, "page"),
            "per": _optional_int(args, "per"),
        }))
    if name == "lead_create":
        payload = {"email": str(_required(args, "email"))}
        for key in ("first_name", "last_name", "company", "title", "domain", "city",
                    "country", "industry", "notes"):
            value = _optional_string(args, key)
            if value is not None:
                payload[key] = value
        return web.r_lead_save(payload)
    if name == "lead_set_status":
        return web.r_lead_status({"ids": _int_list(args, "ids"),
                                  "status": str(_required(args, "status"))})
    if name == "lead_delete":
        return web.r_lead_delete({"ids": _int_list(args, "ids")})
    if name == "lead_import_csv":
        return web.r_lead_import({
            "name": _optional_string(args, "name") or "upload.csv",
            "text": str(_required(args, "csv")),
            "update_existing": bool(args.get("update_existing")),
        })
    if name == "sequence_list":
        state = web.r_state()
        return (state.get("sequences") or []) if isinstance(state, dict) else []
    if name == "enroll_leads":
        payload = {"sequence_id": _required_int(args, "sequence_id"),
                   "leads": _int_list(args, "lead_ids")}
        mailbox_id = _optional_int(args, "mailbox_id")
        if mailbox_id is not None:
            payload["mailbox_id"] = mailbox_id
        return web.r_enroll(payload)
    if name == "enrollment_status":
        return web.r_enrollments(_compact({"status": _optional_string(args, "status")}))
    if name == "signals_search":
        return web.r_signals(_compact({
            "kind": _optional_string(args, "kind"),
            "limit": _optional_int(args, "limit"),
        }))
    if name == "domains_search":
        return web.r_domains(_compact({
            "q": _optional_string(args, "query"),
            "limit": _optional_int(args, "limit"),
        }))
    if name == "inbox_recent":
        return web.r_inbox(_compact({"limit": _optional_int(args, "limit")}))

    raise _ToolError("Unknown tool: %s" % name)


# ------------------------------------------------------------- transport
def main():
    """Run the stdio transport until stdin closes; returns 0 on a clean EOF."""
    from . import db
    db.init_db()
    out = sys.stdout
    err = sys.stderr or sys.__stderr__
    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except ValueError as exc:
                response = _error(None, -32700, "Parse error: %s" % exc)
            else:
                # A stray print() inside a handler must never corrupt the
                # protocol stream, so stdout points at stderr while tools run.
                if err is None:
                    response = handle_request(request)
                else:
                    with contextlib.redirect_stdout(err):
                        response = handle_request(request)
            if response is None:
                continue
            out.write(json.dumps(response) + "\n")
            out.flush()
    except (KeyboardInterrupt, BrokenPipeError):
        return 0
    except OSError:
        return 0
    return 0
