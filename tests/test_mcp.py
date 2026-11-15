"""Tests for the MCP stdio server (engine/mcp.py).

Run:  python -m unittest discover -s tests -v

No network and no subprocess: the protocol layer is exercised through
handle_request() and the tools use a throwaway SQLite database.
"""
import json
import os
import sys
import tempfile
import unittest
import uuid

TMP = tempfile.mkdtemp(prefix="ls_mcp_")
os.environ["LS_DATA"] = TMP

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine import config, db, mcp  # noqa: E402


def request(method, params=None, req_id=1):
    out = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        out["params"] = params
    return out


def call_tool(name, arguments=None):
    params = {"name": name}
    if arguments is not None:
        params["arguments"] = arguments
    return mcp.handle_request(request("tools/call", params, req_id=7))


def payload(response):
    return json.loads(response["result"]["content"][0]["text"])


def setUpModule():
    db.init_db()


def tearDownModule():
    db.close()


class McpProtocolTests(unittest.TestCase):
    def test_initialize_returns_protocol_version_and_server_name(self):
        res = mcp.handle_request(request("initialize", {"protocolVersion": "2024-11-05"}))
        result = res["result"]
        self.assertEqual(result["protocolVersion"], "2024-11-05")
        self.assertEqual(result["serverInfo"]["name"], "lead-studio-lite")
        self.assertEqual(result["serverInfo"]["version"], config.VERSION)
        self.assertEqual(result["capabilities"], {"tools": {}})

    def test_tools_list_shape(self):
        res = mcp.handle_request(request("tools/list"))
        tools = res["result"]["tools"]
        self.assertGreaterEqual(len(tools), 14)
        names = set()
        for tool in tools:
            self.assertTrue(tool["name"], tool)
            self.assertIsInstance(tool["description"], str)
            self.assertTrue(tool["description"].strip(), tool)
            schema = tool["inputSchema"]
            self.assertEqual(schema["type"], "object", tool["name"])
            self.assertIn("properties", schema, tool["name"])
            self.assertIn("required", schema, tool["name"])
            names.add(tool["name"])
        expected = {"stats", "lead_search", "lead_create", "lead_set_status", "lead_delete",
                    "lead_import_csv", "sequence_list", "enroll_leads", "enrollment_status",
                    "job_list", "job_run", "signals_search", "domains_search", "inbox_recent"}
        self.assertTrue(expected.issubset(names), expected - names)

    def test_unknown_method_returns_method_not_found(self):
        res = mcp.handle_request(request("does/not/exist"))
        self.assertEqual(res["error"]["code"], -32601)
        self.assertIn("does/not/exist", res["error"]["message"])

    def test_notification_returns_none(self):
        self.assertIsNone(mcp.handle_request({"jsonrpc": "2.0",
                                              "method": "notifications/initialized"}))
        self.assertIsNone(mcp.handle_request({"jsonrpc": "2.0", "method": "ping"}))

    def test_ping_returns_empty_result(self):
        res = mcp.handle_request(request("ping"))
        self.assertEqual(res["result"], {})

    def test_empty_tool_resource_and_prompt_lists(self):
        resources = mcp.handle_request(request("resources/list"))
        self.assertEqual(resources["result"]["resources"], [])
        prompts = mcp.handle_request(request("prompts/list"))
        self.assertEqual(prompts["result"]["prompts"], [])

    def test_bad_params_returns_invalid_params(self):
        res = mcp.handle_request(request("tools/call", ["not", "an", "object"]))
        self.assertEqual(res["error"]["code"], -32602)

    def test_stats_tool_returns_json(self):
        res = call_tool("stats")
        self.assertFalse(res["result"]["isError"])
        data = payload(res)
        self.assertIsInstance(data, dict)
        self.assertIn("ok", data)
        self.assertTrue(data["ok"], data)

    def test_unknown_tool_is_an_error_result(self):
        res = call_tool("no_such_tool")
        self.assertTrue(res["result"]["isError"])
        self.assertIn("no_such_tool", payload(res)["error"])

    def test_lead_create_then_search_finds_the_address(self):
        email = "mcp-%s@example-test.com" % uuid.uuid4().hex[:8]
        created = call_tool("lead_create", {
            "email": email, "first_name": "Mia", "company": "MCP Test Co"})
        self.assertFalse(created["result"]["isError"], created)
        data = payload(created)
        self.assertTrue(data["ok"], data)
        self.assertGreater(data["id"], 0)

        found = call_tool("lead_search", {"query": email})
        self.assertFalse(found["result"]["isError"], found)
        leads = payload(found)
        self.assertTrue(leads["ok"], leads)
        self.assertIn(email, [lead["email"] for lead in leads["leads"]])


if __name__ == "__main__":
    unittest.main(verbosity=2)
