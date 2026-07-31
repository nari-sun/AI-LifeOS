import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import notion_integration  # noqa: E402


PAGE_URL = "https://www.notion.so/Product-spec-11111111111141118111111111111111"
ROW_URL = "https://www.notion.so/Row-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
QUERY_SOURCE_URL = "https://www.notion.so/Tasks-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


class NotionIntegrationTests(unittest.TestCase):
    def test_process_config_uses_mcp_remote_oauth_bridge_and_read_only_allowlist(self):
        with mock.patch.object(
            notion_integration,
            "notion_mcp_auth_dir",
            return_value=Path("C:/Users/example/.mcp-auth/ai-lifeos-notion"),
        ):
            values = notion_integration.notion_mcp_config_values()

        self.assertTrue(any(".command=" in value and "npx" in value for value in values))
        args = next(value for value in values if value.startswith("mcp_servers.ai_lifeos_notion.args="))
        self.assertIn("mcp-remote@0.1.38", args)
        self.assertIn("https://mcp.notion.com/mcp", args)
        self.assertIn('"http-only"', args)
        self.assertTrue(any("MCP_REMOTE_CONFIG_DIR" in value for value in values))
        self.assertFalse(any(".url=" in value for value in values))
        self.assertFalse(any(".auth=" in value for value in values))
        self.assertIn("mcp_servers.ai_lifeos_notion.required=true", values)
        enabled = next(
            value for value in values if value.startswith("mcp_servers.ai_lifeos_notion.enabled_tools=")
        )
        self.assertIn('"fetch"', enabled)
        self.assertIn('"notion-fetch"', enabled)
        self.assertIn('"search"', enabled)
        self.assertIn('"notion-search"', enabled)
        self.assertNotIn("create", enabled)
        self.assertNotIn("update", enabled)
        self.assertNotIn("comment", enabled)

    def test_inventory_requires_fetch_and_search_and_rejects_write_tools_and_resources(self):
        accepted = {
            "name": notion_integration.NOTION_MCP_SERVER_NAME,
            "tools": {
                "fetch": {},
                "search": {},
                "notion-query-data-sources": {},
            },
            "resources": [],
            "resourceTemplates": [],
        }
        self.assertEqual(
            ("fetch", "search", "notion-query-data-sources"),
            notion_integration.validate_notion_inventory_item(accepted),
        )
        self.assertEqual(
            ("notion-fetch", "notion-search"),
            notion_integration.validate_notion_inventory_item(
                {
                    "name": notion_integration.NOTION_MCP_SERVER_NAME,
                    "tools": {"notion-fetch": {}, "notion-search": {}},
                }
            ),
        )

        for tools in (
            {"notion-query-data-sources": {}},
            {"search": {}},
            {"fetch": {}},
            {"fetch": {}, "search": {}, "notion-create-pages": {}},
        ):
            with self.subTest(tools=tools):
                with self.assertRaises(notion_integration.NotionIntegrationError):
                    notion_integration.validate_notion_inventory_item(
                        {"name": notion_integration.NOTION_MCP_SERVER_NAME, "tools": tools}
                    )

        with self.assertRaises(notion_integration.NotionIntegrationError):
            notion_integration.validate_notion_inventory_item(
                {
                    "name": notion_integration.NOTION_MCP_SERVER_NAME,
                    "tools": {"fetch": {}, "search": {}},
                    "resources": [{"uri": "private://body"}],
                }
            )

    def test_fetch_trace_extracts_safe_metadata_without_retaining_response_body(self):
        private_body = "PRIVATE_NOTION_BODY"
        trace = notion_integration.notion_trace_from_mcp_item(
            {
                "type": "mcp_tool_call",
                "server": notion_integration.NOTION_MCP_SERVER_NAME,
                "tool": "fetch",
                "status": "completed",
                "arguments": {"id": PAGE_URL},
                "result": {
                    "structured_content": {
                        "metadata": {
                            "object": "page",
                            "id": "11111111-1111-4111-8111-111111111111",
                            "title": "Product spec",
                            "url": PAGE_URL,
                        },
                        "text": private_body,
                    }
                },
            }
        )

        self.assertTrue(trace.attempted)
        self.assertEqual(1, trace.successful_calls)
        self.assertEqual("Product spec", trace.sources[0].title)
        self.assertNotIn(private_body, repr(trace))

    def test_workspace_search_trace_keeps_only_notion_metadata_and_requires_safe_mode(self):
        private_highlight = "PRIVATE_SEARCH_HIGHLIGHT"
        result = {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "results": [
                                {
                                    "type": "page",
                                    "title": "Product spec",
                                    "url": PAGE_URL,
                                    "highlight": private_highlight,
                                },
                                {
                                    "type": "page",
                                    "title": "Raw id result",
                                    "url": "22222222-2222-4222-8222-222222222222",
                                    "highlight": "PRIVATE_RAW_ID_HIGHLIGHT",
                                },
                                {
                                    "type": "slack",
                                    "title": "External result",
                                    "url": "https://example.slack.com/private-result",
                                    "highlight": "PRIVATE_CONNECTED_SOURCE",
                                },
                            ]
                        }
                    ),
                }
            ]
        }
        safe = notion_integration.notion_trace_from_mcp_item(
            {
                "type": "mcp_tool_call",
                "server": notion_integration.NOTION_MCP_SERVER_NAME,
                "tool": "search",
                "status": "completed",
                "arguments": {
                    "query": "product",
                    "query_type": "internal",
                    "content_search_mode": "workspace_search",
                },
                "result": result,
            }
        )
        unsafe = notion_integration.notion_trace_from_mcp_item(
            {
                "type": "mcp_tool_call",
                "server": notion_integration.NOTION_MCP_SERVER_NAME,
                "tool": "notion-search",
                "status": "completed",
                "arguments": {"query": "product", "content_search_mode": "ai_search"},
                "result": result,
            }
        )

        self.assertEqual(1, safe.successful_calls)
        self.assertEqual(2, len(safe.sources))
        self.assertEqual(PAGE_URL, safe.sources[0].url)
        self.assertEqual("22222222222242228222222222222222", safe.sources[1].id)
        self.assertEqual("", safe.sources[1].url)
        self.assertNotIn("PRIVATE_", repr(safe))
        self.assertEqual(1, unsafe.search_scope_violations)
        self.assertEqual(0, unsafe.successful_calls)
        self.assertEqual((), unsafe.sources)

    def test_database_query_aggregates_rows_into_one_database_source(self):
        query = notion_integration.notion_trace_from_mcp_item(
            {
                "type": "mcpToolCall",
                "server": notion_integration.NOTION_MCP_SERVER_NAME,
                "tool": "notion-query-data-sources",
                "status": "completed",
                "arguments": {"data_source_urls": [QUERY_SOURCE_URL]},
                "result": {
                    "structuredContent": {
                        "rows": [
                            {"title": "First", "url": ROW_URL, "body": "PRIVATE_FIRST_ROW"},
                            {
                                "title": "Second",
                                "url": "https://www.notion.so/Row-cccccccccccccccccccccccccccccccc",
                                "body": "PRIVATE_SECOND_ROW",
                            },
                        ]
                    }
                },
            }
        )
        row_fetch = notion_integration.notion_trace_from_mcp_item(
            {
                "type": "mcp_tool_call",
                "server": notion_integration.NOTION_MCP_SERVER_NAME,
                "tool": "fetch",
                "status": "completed",
                "arguments": {"id": ROW_URL},
                "result": {"structured_content": {"text": "PRIVATE_FETCHED_ROW"}},
            }
        )

        context = notion_integration.notion_context_from_traces(
            [query, row_fetch],
            requested=True,
        )

        self.assertTrue(context.used)
        self.assertEqual(1, len(context.sources))
        self.assertEqual("database", context.sources[0].object_type)
        self.assertEqual(2, context.sources[0].row_count)
        self.assertNotIn("PRIVATE_", repr(context))

    def test_failed_call_never_uses_stale_or_server_error_body(self):
        trace = notion_integration.notion_trace_from_mcp_item(
            {
                "type": "mcp_tool_call",
                "server": notion_integration.NOTION_MCP_SERVER_NAME,
                "tool": "fetch",
                "status": "failed",
                "error": "PRIVATE_SERVER_ERROR_BODY",
            }
        )
        context = notion_integration.notion_context_from_traces([trace], requested=True)

        self.assertFalse(context.used)
        self.assertEqual("error", context.status)
        self.assertEqual((), context.sources)
        self.assertNotIn("PRIVATE_SERVER_ERROR_BODY", context.error)

    def test_requested_but_unused_is_distinct_from_failure(self):
        context = notion_integration.notion_context_from_traces([], requested=True)

        self.assertFalse(context.used)
        self.assertEqual("not_used", context.status)
        self.assertIsNone(context.fetched_at)

    def test_exec_jsonl_ignores_other_servers_and_keeps_no_response_body(self):
        lines = [
            json.dumps(
                {
                    "item": {
                        "type": "mcp_tool_call",
                        "server": "other",
                        "tool": "fetch",
                        "status": "completed",
                        "result": {"structured_content": {"text": "OTHER_PRIVATE_BODY"}},
                    }
                }
            ),
            json.dumps(
                {
                    "item": {
                        "type": "mcp_tool_call",
                        "server": notion_integration.NOTION_MCP_SERVER_NAME,
                        "tool": "fetch",
                        "status": "completed",
                        "arguments": {"id": PAGE_URL},
                        "result": {"structured_content": {"text": "NOTION_PRIVATE_BODY"}},
                    }
                }
            ),
        ]

        context = notion_integration.notion_context_from_exec_jsonl("\n".join(lines), requested=True)

        self.assertTrue(context.used)
        self.assertEqual(1, len(context.sources))
        self.assertNotIn("PRIVATE_BODY", repr(context))

    def test_connection_view_reports_only_fixed_endpoint_and_verified_tools(self):
        inventory = {
            "name": notion_integration.NOTION_MCP_SERVER_NAME,
            "tools": {"fetch": {}, "search": {}},
            "resources": [],
            "resourceTemplates": [],
            "authStatus": "oauth",
            "_workspace_name": "Workspace A",
            "_user_name": "User A",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch.object(notion_integration, "_probe_notion_inventory", return_value=inventory):
                result = notion_integration.get_notion_connection_view(temp_dir, refresh=True)

        self.assertTrue(result["connection"]["connected"])
        self.assertEqual(["fetch", "search"], result["connection"]["tools"])
        self.assertEqual("Workspace A", result["connection"]["workspace_name"])
        self.assertEqual("User A", result["connection"]["user_name"])
        self.assertEqual("https://mcp.notion.com/mcp", result["endpoint"])
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("token", serialized.lower())
        self.assertNotIn("credential_value", serialized)

    def test_connection_identity_keeps_names_only(self):
        result = {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "self": {
                                "workspace": {"id": "private-workspace-id", "name": "Workspace A"},
                                "user": {
                                    "id": "private-user-id",
                                    "name": "User A",
                                    "email": "PRIVATE_EMAIL_VALUE",
                                },
                                "current_tool_access": {"fetch": "available"},
                            }
                        }
                    ),
                }
            ]
        }

        identity = notion_integration._connection_identity(result)

        self.assertEqual(("Workspace A", "User A"), identity)
        self.assertNotIn("private", repr(identity))

    def test_connection_failure_is_sanitized(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch.object(
                notion_integration,
                "_probe_notion_inventory",
                side_effect=RuntimeError("PRIVATE_OAUTH_SERVER_BODY"),
            ):
                result = notion_integration.get_notion_connection_view(temp_dir, refresh=True)

        self.assertEqual("connection_error", result["connection"]["status"])
        self.assertNotIn("PRIVATE_OAUTH_SERVER_BODY", result["connection"]["error"])

    def test_login_wrapper_runs_pinned_mcp_remote_client_without_token(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            auth_dir = Path(temp_dir) / ".mcp-auth" / "ai-lifeos-notion"
            with (
                mock.patch.object(notion_integration, "notion_mcp_auth_dir", return_value=auth_dir),
                mock.patch.object(notion_integration.subprocess, "call", return_value=0) as call,
            ):
                exit_code = notion_integration.main(["login", "--root", temp_dir, "--npx-command", "npx.cmd"])

        self.assertEqual(0, exit_code)
        command = call.call_args.args[0]
        self.assertEqual(["npx.cmd", "-y", "-p", "mcp-remote@0.1.38"], command[:4])
        self.assertIn("mcp-remote-client", command)
        self.assertIn("https://mcp.notion.com/mcp", command)
        self.assertEqual(str(auth_dir), call.call_args.kwargs["env"]["MCP_REMOTE_CONFIG_DIR"])
        self.assertFalse(any("token" in value.lower() for value in command))

    def test_logout_removes_only_dedicated_mcp_remote_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            auth_dir = home / ".mcp-auth" / "ai-lifeos-notion"
            auth_dir.mkdir(parents=True)
            (auth_dir / "credential.json").write_text("PRIVATE", encoding="utf-8")
            sibling = home / ".mcp-auth" / "other-server"
            sibling.mkdir()
            with (
                mock.patch.object(notion_integration.Path, "home", return_value=home),
                mock.patch.object(notion_integration, "notion_mcp_auth_dir", return_value=auth_dir),
                mock.patch("builtins.print"),
            ):
                exit_code = notion_integration.main(["logout"])

            self.assertEqual(0, exit_code)
            self.assertFalse(auth_dir.exists())
            self.assertTrue(sibling.exists())


if __name__ == "__main__":
    unittest.main()
