"""The generic MCP connection: discovery, registration, PKCE, tokens, and JSON-RPC."""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

if not os.environ.get("LS_DATA"):
    os.environ["LS_DATA"] = tempfile.mkdtemp(prefix="ls_mcp_client_")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine import mcp_client  # noqa: E402

META = {"issuer": "https://as.test",
        "authorization_endpoint": "https://as.test/authorize",
        "token_endpoint": "https://as.test/token",
        "registration_endpoint": "https://as.test/register",
        "scopes_supported": ["mcp"]}


def wipe():
    mcp_client._PENDING.clear()
    mcp_client._META.clear()
    mcp_client._save("manyreach", {})


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        wipe()

    def test_metadata_walks_the_protected_resource_to_the_auth_server(self):
        calls = []

        def fake(url, *args, **kwargs):
            calls.append(url)
            if "oauth-protected-resource" in url:
                return 200, {}, json.dumps({"authorization_servers": ["https://as.test"]})
            return 200, {}, json.dumps(META)

        with mock.patch.object(mcp_client, "_request", side_effect=fake):
            data = mcp_client.metadata("manyreach")
        self.assertEqual(data["token_endpoint"], "https://as.test/token")
        self.assertEqual(calls[0], "https://mcp.manyreach.com/.well-known/oauth-protected-resource")
        self.assertEqual(calls[1], "https://as.test/.well-known/oauth-authorization-server")

    def test_a_server_without_metadata_is_a_clear_error(self):
        with mock.patch.object(mcp_client, "_request", return_value=(404, {}, "")):
            with self.assertRaises(mcp_client.McpError) as ctx:
                mcp_client.metadata("manyreach")
        self.assertIn("OAuth metadata", str(ctx.exception))

    def test_an_unknown_connection_is_refused(self):
        with self.assertRaises(mcp_client.McpError):
            mcp_client.connection("nope")


class OAuthTests(unittest.TestCase):
    def setUp(self):
        wipe()

    def test_begin_registers_the_app_and_builds_a_pkce_url(self):
        registered = []

        def fake(url, data=None, **kwargs):
            registered.append(url)
            return 201, {}, json.dumps({"client_id": "client-123"})

        with mock.patch.object(mcp_client, "metadata", return_value=META), \
                mock.patch.object(mcp_client, "_request", side_effect=fake):
            answer = mcp_client.begin("manyreach", 8899)

        self.assertTrue(answer["url"].startswith("https://as.test/authorize?"))
        self.assertIn("code_challenge_method=S256", answer["url"])
        self.assertIn("client_id=client-123", answer["url"])
        self.assertIn("redirect_uri=http%3A%2F%2F127.0.0.1%3A8899%2Foauth%2Fmcp%2Fmanyreach%2Fcallback",
                      answer["url"])
        self.assertIn("https://as.test/register", registered)
        self.assertTrue(mcp_client.status("manyreach")["client_id"])

    def test_finish_stores_the_tokens(self):
        with mock.patch.object(mcp_client, "metadata", return_value=META), \
                mock.patch.object(mcp_client, "_request",
                                  return_value=(201, {}, json.dumps({"client_id": "cid"}))):
            answer = mcp_client.begin("manyreach", 8899)

        with mock.patch.object(mcp_client, "metadata", return_value=META), \
                mock.patch.object(mcp_client, "_request",
                                  return_value=(200, {}, json.dumps({
                                      "access_token": "tok-1", "refresh_token": "ref-1",
                                      "expires_in": 3600}))):
            result = mcp_client.finish("manyreach", {"state": answer["state"], "code": "abc"})

        self.assertTrue(result["ok"], result)
        status = mcp_client.status("manyreach")
        self.assertTrue(status["connected"])
        self.assertFalse(status["expired"])

    def test_a_reused_or_unknown_state_is_refused(self):
        result = mcp_client.finish("manyreach", {"state": "nope", "code": "abc"})
        self.assertFalse(result["ok"])
        self.assertIn("expired", result["error"])

    def test_an_expired_token_is_refreshed(self):
        mcp_client._save("manyreach", {"client_id": "cid", "access_token": "old",
                                       "refresh_token": "ref", "expires_at": 1})
        with mock.patch.object(mcp_client, "metadata", return_value=META), \
                mock.patch.object(mcp_client, "_request",
                                  return_value=(200, {}, json.dumps({
                                      "access_token": "new", "expires_in": 3600}))):
            token = mcp_client.access_token("manyreach")
        self.assertEqual(token, "new")
        self.assertEqual(mcp_client._saved("manyreach")["access_token"], "new")

    def test_disconnect_forgets_the_tokens(self):
        mcp_client._save("manyreach", {"client_id": "cid", "access_token": "tok"})
        mcp_client.disconnect("manyreach")
        self.assertFalse(mcp_client.status("manyreach")["connected"])


class RpcTests(unittest.TestCase):
    def test_json_answers(self):
        raw = json.dumps({"jsonrpc": "2.0", "id": 4, "result": {"tools": [{"name": "a"}]}})
        obj = mcp_client._parse_rpc(raw, "application/json", 4)
        self.assertEqual(obj["result"]["tools"][0]["name"], "a")

    def test_server_sent_events(self):
        raw = ('event: message\n'
               'data: {"jsonrpc":"2.0","id":9,"result":{"ok":true}}\n\n')
        obj = mcp_client._parse_rpc(raw, "text/event-stream", 9)
        self.assertTrue(obj["result"]["ok"])

    def test_an_event_stream_without_our_id_is_an_error(self):
        with self.assertRaises(mcp_client.McpError):
            mcp_client._parse_rpc('data: {"jsonrpc":"2.0","id":1,"result":{}}\n\n',
                                  "text/event-stream", 9)

    def test_a_client_calls_a_tool_and_returns_its_text(self):
        def fake(url, payload=None, headers=None, **kwargs):
            rid = (payload or {}).get("id")
            if (payload or {}).get("method") == "initialize":
                return 200, {"content-type": "application/json", "mcp-session-id": "s-1"}, \
                    json.dumps({"jsonrpc": "2.0", "id": rid, "result": {"protocolVersion": "x"}})
            if (payload or {}).get("method") == "notifications/initialized":
                return 202, {}, ""
            return 200, {"content-type": "application/json"}, json.dumps({
                "jsonrpc": "2.0", "id": rid,
                "result": {"content": [{"type": "text", "text": "campaign list"}],
                           "isError": False}})

        mcp_client._save("manyreach", {"client_id": "cid", "access_token": "tok", "expires_at": 0})
        with mock.patch.object(mcp_client, "_request", side_effect=fake):
            answer = mcp_client.call("manyreach", "list_campaigns", {"status": "active"})
        self.assertTrue(answer["ok"])
        self.assertEqual(answer["text"], "campaign list")

    def test_a_tool_error_is_reported(self):
        def fake(url, payload=None, headers=None, **kwargs):
            rid = (payload or {}).get("id")
            if (payload or {}).get("method") == "initialize":
                return 200, {"content-type": "application/json"}, \
                    json.dumps({"jsonrpc": "2.0", "id": rid, "result": {}})
            if (payload or {}).get("method") == "notifications/initialized":
                return 202, {}, ""
            return 200, {"content-type": "application/json"}, json.dumps({
                "jsonrpc": "2.0", "id": rid,
                "result": {"content": [{"type": "text", "text": "no such campaign"}],
                           "isError": True}})

        mcp_client._save("manyreach", {"client_id": "cid", "access_token": "tok", "expires_at": 0})
        with mock.patch.object(mcp_client, "_request", side_effect=fake):
            answer = mcp_client.call("manyreach", "get_campaign", {})
        self.assertFalse(answer["ok"])
        self.assertIn("no such campaign", answer["text"])


class DownloadTests(unittest.TestCase):
    def setUp(self):
        from engine import db
        db.init_db()
        db.execute("DELETE FROM mcp_items WHERE connection='manyreach'")

    def tearDown(self):
        from engine import db
        db.execute("DELETE FROM mcp_items WHERE connection='manyreach'")

    def test_a_header_line_before_the_json_is_tolerated(self):
        data = mcp_client._json_from_text(
            "response_id: x | seq: 1 | status: ok\n\n{\"items\": [{\"campaignId\": 1}]}")
        self.assertEqual(data["items"][0]["campaignId"], 1)

    def test_no_json_is_a_clear_error(self):
        with self.assertRaises(mcp_client.McpError):
            mcp_client._json_from_text("no json here")

    def test_campaigns_and_senders_map_to_id_label_status(self):
        self.assertEqual(mcp_client._item_row("campaigns",
                                              {"campaignId": 7, "name": "C", "status": "Running"}),
                         ("7", "C", "Running"))
        self.assertEqual(mcp_client._item_row("senders", {"senderId": 3, "email": "a@b.c",
                                                          "warmup": True}),
                         ("3", "a@b.c", "warmup"))

    def test_pull_stores_rows_and_replaces_them_next_time(self):
        answers = {
            "campaign/get": {"items": [{"campaignId": 1, "name": "One", "status": "Draft"},
                                       {"campaignId": 2, "name": "Two", "status": "Running"}]},
            "sender/get": {"items": [{"senderId": 9, "email": "s@x.com", "warmup": True}]},
        }

        def fake(name, tool, arguments):
            return {"ok": True, "text": json.dumps(answers[arguments["name"]])}

        with mock.patch.object(mcp_client, "call", side_effect=fake):
            answer = mcp_client.pull("manyreach")
        self.assertTrue(answer["ok"], answer)
        self.assertEqual([row["count"] for row in answer["results"]], [2, 1])
        self.assertEqual(len(mcp_client.items("manyreach")), 3)

        with mock.patch.object(mcp_client, "call", side_effect=fake):
            mcp_client.pull("manyreach", "campaigns")
        self.assertEqual(len(mcp_client.items("manyreach", "campaigns")), 2)

    def test_a_failed_download_is_reported(self):
        with mock.patch.object(mcp_client, "call",
                               return_value={"ok": False, "text": "no credits"}):
            answer = mcp_client.pull("manyreach", "campaigns")
        self.assertFalse(answer["ok"])
        self.assertIn("no credits", answer["results"][0]["error"])


class CustomConnectionTests(unittest.TestCase):
    def tearDown(self):
        from engine import config
        settings = config.load_settings()
        settings.pop("mcp_custom", None)
        settings.pop("mcp_connections", None)
        config.save_settings(settings)

    def test_a_server_can_be_added_with_a_token(self):
        result = mcp_client.save_custom({"name": "Instantly", "url": "https://mcp.instantly.ai/mcp/key",
                                         "token": "tok-123"})
        self.assertTrue(result["ok"], result)
        entry = mcp_client.connection("instantly")
        self.assertEqual(entry["url"], "https://mcp.instantly.ai/mcp/key")
        status = mcp_client.status("instantly")
        self.assertTrue(status["connected"])

    def test_a_bad_url_is_refused(self):
        self.assertFalse(mcp_client.save_custom({"name": "X", "url": "not-a-url"})["ok"])

    def test_a_built_in_name_cannot_be_replaced(self):
        self.assertFalse(mcp_client.save_custom({"name": "manyreach",
                                                 "url": "https://x.test/mcp"})["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
