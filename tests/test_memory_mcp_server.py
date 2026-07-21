import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import memory_index  # noqa: E402
import memory_items  # noqa: E402
import memory_mcp_server  # noqa: E402


class MemoryMCPServerTests(unittest.TestCase):
    def make_root(self, temp_dir: str) -> Path:
        root = Path(temp_dir)
        alpha = root / "conversations" / "2099" / "01" / "2099-01-02_030405"
        alpha.mkdir(parents=True)
        (alpha / "raw.md").write_text(
            "\n".join(
                (
                    "# Chat Log",
                    "",
                    "Date: 2099-01-02",
                    "Session: Project-Alpha SYNTHETIC_FIXTURE_SESSION",
                    "",
                    "## User",
                    "",
                    "Timestamp: 2099-01-02T03:04:05+09:00",
                    "",
                    "Project-Alpha ALPHA_USER_SEARCH_TOKEN synthetic fixture message.",
                    "",
                    "## Assistant",
                    "",
                    "Timestamp: 2099-01-02T03:04:06+09:00",
                    "",
                    "ALPHA_ASSISTANT_REPLY_TOKEN synthetic fixture response.",
                    "",
                    "## User",
                    "",
                    "ALPHA_SECOND_USER_TOKEN synthetic fixture message.",
                    "",
                )
            ),
            encoding="utf-8",
        )
        (alpha / "summary.md").write_text(
            "# Summary\n\nDate: 2099-01-02\nSession: Project-Alpha SYNTHETIC_FIXTURE_SESSION\n\n"
            "ALPHA_USER_SEARCH_TOKEN and ALPHA_SECOND_USER_TOKEN synthetic fixture summary.\n",
            encoding="utf-8",
        )

        beta = root / "conversations" / "2026" / "07" / "2026-07-12_100000"
        beta.mkdir(parents=True)
        (beta / "raw.md").write_text(
            "# Chat Log\n\nDate: 2026-07-12\nSession: Project-Beta 設計\n\n"
            "## User\n\nProject-Beta の検索設計について考えた。\n\n"
            "## Assistant\n\n設計案を回答した。\n",
            encoding="utf-8",
        )

        memory = root / "memory"
        memory.mkdir()
        (memory / "preferences.md").write_text(
            "# Preferences\n\n- Project-Alpha ALPHA_PREFERENCE_TOKEN synthetic fixture.\n",
            encoding="utf-8",
        )
        (memory / "long_term.md").write_text(
            "# Long-Term Memory\n\n- GLOBAL_MEMORY_TOKEN synthetic fixture.\n",
            encoding="utf-8",
        )
        (memory / "projects.md").write_text(
            "# Projects\n\n- Project-Beta BETA_PROJECT_TOKEN synthetic fixture.\n",
            encoding="utf-8",
        )
        items = memory / "items"
        items.mkdir()
        memory_items.create_memory_item(
            root,
            memory_items.StructuredMemoryItem(
                id="mem_fixture_alpha_001",
                category="project_status",
                category_label="Synthetic project status",
                status="active",
                source="conversations/2099/01/2099-01-02_030405/raw.md",
                source_date="2099-01-02",
                confidence="explicit",
                tags=("Project-Alpha",),
                created_at="2099-01-02T04:00:00+09:00",
                updated_at="2099-01-02T04:00:00+09:00",
                content="- Project-Alpha ALPHA_MEMORY_ITEM_TOKEN synthetic fixture.",
            ),
        )

        live = root / "inbox" / "live"
        live.mkdir(parents=True)
        (live / "2099-01-04_050607.jsonl").write_text(
            "\n".join(
                (
                    json.dumps(
                        {
                            "role": "user",
                            "timestamp": "2099-01-04T05:06:07+09:00",
                            "content": "Project-Alpha ALPHA_LIVE_SEARCH_TOKEN synthetic live fixture message.",
                        },
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        {
                            "role": "assistant",
                            "timestamp": "2099-01-04T05:06:08+09:00",
                            "content": "ALPHA_LIVE_ASSISTANT_REPLY_TOKEN synthetic live fixture response.",
                        },
                        ensure_ascii=False,
                    ),
                    "",
                )
            ),
            encoding="utf-8",
        )
        return root

    def test_tool_catalog_is_read_only_and_has_four_tools(self):
        names = {tool["name"] for tool in memory_mcp_server.TOOLS}

        self.assertEqual(
            {"search_past_chats", "open_conversation", "get_personal_memory", "get_index_health"},
            names,
        )
        for tool in memory_mcp_server.TOOLS:
            self.assertTrue(tool["annotations"]["readOnlyHint"])
            self.assertFalse(tool["annotations"]["destructiveHint"])
            self.assertFalse(tool["annotations"]["openWorldHint"])

    def test_search_filters_user_role_and_returns_openable_reference(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            tools = memory_mcp_server.MemoryTools(root)

            result = tools.search_past_chats(
                {
                    "query": "ALPHA_USER_SEARCH_TOKEN",
                    "role": "user",
                    "scope": "finalized",
                    "limit": 5,
                }
            )

            self.assertGreaterEqual(result["result_count"], 1)
            self.assertTrue(all(item["source"]["role"] == "user" for item in result["results"]))
            reference = result["results"][0]["reference"]
            self.assertIn("#message-", reference)
            opened = tools.open_conversation({"reference": reference, "max_chars": 500})
            self.assertTrue(any(message["role"] == "user" for message in opened["messages"]))
            self.assertIn("ALPHA_USER_SEARCH_TOKEN", json.dumps(opened, ensure_ascii=False))

    def test_search_includes_unorganized_live_jsonl(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            tools = memory_mcp_server.MemoryTools(root)

            result = tools.search_past_chats(
                {"query": "ALPHA_LIVE_SEARCH_TOKEN", "role": "user", "scope": "live"}
            )

            self.assertEqual(1, result["result_count"])
            self.assertEqual("live_message", result["results"][0]["source"]["document_type"])
            opened = tools.open_conversation(
                {"reference": result["results"][0]["reference"], "max_chars": 500}
            )
            self.assertEqual("live", opened["source"]["document_type"])
            self.assertEqual("user", opened["messages"][0]["role"])

    def test_temporary_and_memory_excluded_live_sessions_are_never_exposed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            live = root / "inbox" / "live"
            temporary = live / "temporary.jsonl"
            excluded = live / "excluded.jsonl"
            temporary.write_text(
                json.dumps(
                    {
                        "role": "user",
                        "timestamp": "2026-07-14T09:00:00+09:00",
                        "content": "TEMPORARY_SENTINEL must never be retrieved",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            excluded.write_text(
                json.dumps(
                    {
                        "role": "user",
                        "timestamp": "2026-07-14T10:00:00+09:00",
                        "content": "EXCLUDED_SENTINEL must never be retrieved",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            temporary.with_suffix(".session.json").write_text(
                json.dumps(
                    {
                        "personalization": {
                            "temporary": True,
                            "exclude_from_memory": True,
                        }
                    }
                ),
                encoding="utf-8",
            )
            excluded.with_suffix(".session.json").write_text(
                json.dumps(
                    {
                        "personalization": {
                            "temporary": False,
                            "exclude_from_memory": True,
                        }
                    }
                ),
                encoding="utf-8",
            )
            tools = memory_mcp_server.MemoryTools(root)

            temporary_result = tools.search_past_chats(
                {"query": "TEMPORARY_SENTINEL", "scope": "live"}
            )
            excluded_result = tools.search_past_chats(
                {"query": "EXCLUDED_SENTINEL", "scope": "live"}
            )
            health = tools.get_index_health({})

            self.assertEqual(0, temporary_result["result_count"])
            self.assertEqual(0, excluded_result["result_count"])
            self.assertEqual(1, health["unorganized_live_file_count"])
            with self.assertRaises(memory_mcp_server.ToolExecutionError):
                tools.open_conversation({"reference": "inbox/live/temporary.jsonl"})
            with self.assertRaises(memory_mcp_server.ToolExecutionError):
                tools.open_conversation({"reference": "inbox/live/excluded.jsonl"})

    def test_project_scope_is_strict_for_search_and_personal_memory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            tools = memory_mcp_server.MemoryTools(root)

            alpha = tools.search_past_chats(
                {
                    "query": "ALPHA_USER_SEARCH_TOKEN",
                    "scope": "all",
                    "project_scope": "Project-Alpha",
                    "limit": 10,
                }
            )
            no_match = tools.search_past_chats(
                {
                    "query": "設計",
                    "scope": "all",
                    "project_scope": "Project-Does-Not-Exist",
                    "limit": 10,
                }
            )
            memory = tools.get_personal_memory(
                {"scope": "all", "project_scope": "Project-Alpha", "max_chars": 2_000}
            )
            no_memory = tools.get_personal_memory(
                {"scope": "all", "project_scope": "Project-Does-Not-Exist", "max_chars": 2_000}
            )

            self.assertGreaterEqual(alpha["result_count"], 1)
            self.assertTrue(
                all("Project-Alpha" in json.dumps(item, ensure_ascii=False) for item in alpha["results"])
            )
            self.assertEqual(0, no_match["result_count"])
            self.assertGreaterEqual(memory["source_count"], 1)
            self.assertTrue(
                all("Project-Alpha" in json.dumps(item, ensure_ascii=False) for item in memory["sources"])
            )
            self.assertEqual(0, no_memory["source_count"])

    def test_active_project_scope_is_applied_when_omitted_and_cannot_be_replaced(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            tools = memory_mcp_server.MemoryTools(root, project_scope="  Project-Alpha  ")

            result = tools.search_past_chats(
                {"query": "ALPHA_SECOND_USER_TOKEN", "role": "user", "scope": "finalized"}
            )
            memory = tools.get_personal_memory({"scope": "all", "max_chars": 2_000})

            self.assertEqual("Project-Alpha", tools.active_project_scope)
            self.assertGreaterEqual(result["result_count"], 1)
            self.assertEqual("Project-Alpha", result["filters"]["project_scope"])
            self.assertTrue(
                all("Project-Alpha" in item["source"]["title"] for item in result["results"])
            )
            self.assertEqual("Project-Alpha", memory["project_scope"])
            self.assertNotIn("BETA_PROJECT_TOKEN", json.dumps(memory, ensure_ascii=False))

            alpha_reference = "conversations/2099/01/2099-01-02_030405/raw.md#message-2-user"
            opened = tools.open_conversation({"reference": alpha_reference, "max_chars": 500})
            self.assertTrue(
                any("ALPHA_SECOND_USER_TOKEN" in item["text"] for item in opened["messages"])
            )

            with self.assertRaises(memory_mcp_server.ToolExecutionError):
                tools.open_conversation(
                    {"reference": "conversations/2026/07/2026-07-12_100000/raw.md"}
                )
            with self.assertRaises(memory_mcp_server.InvalidToolArguments):
                tools.search_past_chats(
                    {"query": "設計", "project_scope": "Project-Beta"}
                )
            with self.assertRaises(memory_mcp_server.InvalidToolArguments):
                tools.get_personal_memory(
                    {"scope": "all", "project_scope": "Project-Beta"}
                )

    def test_scoped_core_memory_returns_only_matching_sections_and_lines(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            (root / "memory" / "long_term.md").write_text(
                "\n".join(
                    (
                        "# Long-Term Memory",
                        "",
                        "## Project-Alpha",
                        "- ALPHA_SECTION_SENTINEL",
                        "",
                        "## Project-Beta",
                        "- BETA_SECTION_SECRET",
                        "",
                    )
                ),
                encoding="utf-8",
            )
            (root / "memory" / "preferences.md").write_text(
                "# Preferences\n\n- Project-Alpha: ALPHA_LINE_SENTINEL\n- ADJACENT_GLOBAL_SECRET\n",
                encoding="utf-8",
            )
            tools = memory_mcp_server.MemoryTools(root, project_scope="Project-Alpha")

            result = tools.get_personal_memory({"scope": "all", "max_chars": 5_000})
            serialized = json.dumps(result, ensure_ascii=False)

            self.assertIn("ALPHA_SECTION_SENTINEL", serialized)
            self.assertIn("ALPHA_LINE_SENTINEL", serialized)
            self.assertNotIn("BETA_SECTION_SECRET", serialized)
            self.assertNotIn("ADJACENT_GLOBAL_SECRET", serialized)

    def test_live_session_project_scope_comes_from_immutable_sidecar_metadata(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            live = root / "inbox" / "live" / "metadata-scope.jsonl"
            live.write_text(
                json.dumps(
                    {
                        "role": "user",
                        "timestamp": "2026-07-15T09:00:00+09:00",
                        "content": "METADATA_ONLY_QUERY without a project name",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            live.with_suffix(".session.json").write_text(
                json.dumps(
                    {
                        "session_id": "metadata-scope",
                        "title": "A generic live title",
                        "personalization": {
                            "temporary": False,
                            "exclude_from_memory": False,
                            "project_scope": "Project-Alpha",
                        },
                    }
                ),
                encoding="utf-8",
            )

            alpha_tools = memory_mcp_server.MemoryTools(root, project_scope="Project-Alpha")
            beta_tools = memory_mcp_server.MemoryTools(root, project_scope="Project-Beta")
            alpha = alpha_tools.search_past_chats(
                {"query": "METADATA_ONLY_QUERY", "scope": "live", "role": "user"}
            )
            beta = beta_tools.search_past_chats(
                {"query": "METADATA_ONLY_QUERY", "scope": "live", "role": "user"}
            )

            self.assertEqual(1, alpha["result_count"])
            self.assertEqual(0, beta["result_count"])
            opened = alpha_tools.open_conversation(
                {"reference": alpha["results"][0]["reference"], "max_chars": 500}
            )
            self.assertIn("METADATA_ONLY_QUERY", opened["messages"][0]["text"])
            with self.assertRaises(memory_mcp_server.ToolExecutionError):
                beta_tools.open_conversation({"reference": "inbox/live/metadata-scope.jsonl"})

    def test_explicit_scope_assignment_does_not_use_prefix_matching(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            finalized = root / "conversations" / "2099" / "02" / "2099-02-03_040506"
            finalized.mkdir(parents=True)
            (finalized / "raw.md").write_text(
                "# Chat Log\n\nDate: 2099-02-03\nProject Scope: Project-Alpha-Secret\n\n"
                "## User\n\nPREFIX_FINALIZED_SECRET_TOKEN\n",
                encoding="utf-8",
            )
            live = root / "inbox" / "live" / "prefix-secret.jsonl"
            live.write_text(
                json.dumps(
                    {
                        "role": "user",
                        "timestamp": "2099-02-03T04:05:06+09:00",
                        "content": "PREFIX_LIVE_SECRET_TOKEN",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            live.with_suffix(".session.json").write_text(
                json.dumps(
                    {
                        "personalization": {
                            "temporary": False,
                            "exclude_from_memory": False,
                            "project_scope": "Project-Alpha-Secret",
                        }
                    }
                ),
                encoding="utf-8",
            )
            tools = memory_mcp_server.MemoryTools(root, project_scope="Project-Alpha")

            finalized_result = tools.search_past_chats(
                {"query": "PREFIX_FINALIZED_SECRET_TOKEN", "scope": "finalized", "role": "user"}
            )
            live_result = tools.search_past_chats(
                {"query": "PREFIX_LIVE_SECRET_TOKEN", "scope": "live", "role": "user"}
            )

            self.assertEqual(0, finalized_result["result_count"])
            self.assertEqual(0, live_result["result_count"])
            with self.assertRaises(memory_mcp_server.ToolExecutionError):
                tools.open_conversation(
                    {
                        "reference": (
                            "conversations/2099/02/2099-02-03_040506/raw.md#message-1-user"
                        )
                    }
                )
            with self.assertRaises(memory_mcp_server.ToolExecutionError):
                tools.open_conversation({"reference": "inbox/live/prefix-secret.jsonl#message-1-user"})

    def test_scope_is_filtered_before_candidate_limit_for_markdown_and_sqlite(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            alpha_raw = root / "conversations" / "2099" / "01" / "2099-01-02_030405" / "raw.md"
            beta_raw = root / "conversations" / "2026" / "07" / "2026-07-12_100000" / "raw.md"
            alpha_raw.write_text(
                alpha_raw.read_text(encoding="utf-8")
                + "\n## Assistant\n\nCAP_QUERY CAP_QUERY CAP_QUERY CAP_QUERY\n"
                + "\n## User\n\nCAP_QUERY\n",
                encoding="utf-8",
            )
            beta_raw.write_text(
                beta_raw.read_text(encoding="utf-8") + "\n## User\n\nCAP_QUERY CAP_QUERY CAP_QUERY\n",
                encoding="utf-8",
            )
            tools = memory_mcp_server.MemoryTools(root, project_scope="Project-Alpha")

            with mock.patch.object(memory_mcp_server, "MAX_SEARCH_CANDIDATES", 1):
                markdown = tools.search_past_chats(
                    {"query": "CAP_QUERY", "scope": "finalized", "role": "user", "limit": 1}
                )
                memory_index.rebuild_index(root)
                sqlite = tools.search_past_chats(
                    {"query": "CAP_QUERY", "scope": "finalized", "role": "user", "limit": 1}
                )

            self.assertEqual(1, markdown["result_count"])
            self.assertEqual(1, sqlite["result_count"])
            self.assertEqual("markdown", markdown["search_source"])
            self.assertEqual("sqlite", sqlite["search_source"])
            self.assertTrue(
                markdown["results"][0]["source"]["path"].startswith(
                    "conversations/2099/01/2099-01-02"
                )
            )
            self.assertTrue(
                sqlite["results"][0]["source"]["path"].startswith(
                    "conversations/2099/01/2099-01-02"
                )
            )
            self.assertEqual("user", markdown["results"][0]["source"]["role"])
            self.assertEqual("user", sqlite["results"][0]["source"]["role"])

    def test_message_level_scope_does_not_expose_sibling_finalized_or_live_messages(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            mixed = root / "conversations" / "2026" / "07" / "2026-07-16_120000"
            mixed.mkdir(parents=True)
            (mixed / "raw.md").write_text(
                "# Chat Log\n\nDate: 2026-07-16\nSession: Mixed discussion\n\n"
                "## User\n\nSession: Project-Alpha ALLOWED_FINALIZED_MESSAGE\n\n"
                "## User\n\nCROSS_SCOPE_FINALIZED_SECRET\n",
                encoding="utf-8",
            )
            live = root / "inbox" / "live" / "mixed-scope.jsonl"
            live.write_text(
                "\n".join(
                    (
                        json.dumps(
                            {"role": "user", "content": "Project-Alpha ALLOWED_LIVE_MESSAGE"}
                        ),
                        json.dumps(
                            {"role": "user", "content": "CROSS_SCOPE_LIVE_SECRET"}
                        ),
                        "",
                    )
                ),
                encoding="utf-8",
            )
            tools = memory_mcp_server.MemoryTools(root, project_scope="Project-Alpha")

            finalized_search = tools.search_past_chats(
                {"query": "CROSS_SCOPE_FINALIZED_SECRET", "scope": "finalized", "role": "user"}
            )
            live_search = tools.search_past_chats(
                {"query": "CROSS_SCOPE_LIVE_SECRET", "scope": "live", "role": "user"}
            )
            opened_finalized = tools.open_conversation(
                {"reference": "conversations/2026/07/2026-07-16_120000/raw.md", "max_chars": 1_000}
            )
            opened_live = tools.open_conversation(
                {"reference": "inbox/live/mixed-scope.jsonl", "max_chars": 1_000}
            )

            self.assertEqual(0, finalized_search["result_count"])
            self.assertEqual(0, live_search["result_count"])
            self.assertIn("ALLOWED_FINALIZED_MESSAGE", json.dumps(opened_finalized, ensure_ascii=False))
            self.assertNotIn("CROSS_SCOPE_FINALIZED_SECRET", json.dumps(opened_finalized, ensure_ascii=False))
            self.assertIn("ALLOWED_LIVE_MESSAGE", json.dumps(opened_live, ensure_ascii=False))
            self.assertNotIn("CROSS_SCOPE_LIVE_SECRET", json.dumps(opened_live, ensure_ascii=False))
            with self.assertRaises(memory_mcp_server.ToolExecutionError):
                tools.open_conversation(
                    {
                        "reference": (
                            "conversations/2026/07/2026-07-16_120000/raw.md#message-2-user"
                        )
                    }
                )
            with self.assertRaises(memory_mcp_server.ToolExecutionError):
                tools.open_conversation(
                    {"reference": "inbox/live/mixed-scope.jsonl#message-2-user"}
                )

    def test_path_traversal_and_absolute_paths_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            tools = memory_mcp_server.MemoryTools(self.make_root(temp_dir))

            with self.assertRaises(memory_mcp_server.InvalidToolArguments):
                tools.open_conversation({"reference": "conversations/../../outside.md"})
            with self.assertRaises(memory_mcp_server.InvalidToolArguments):
                tools.open_conversation({"reference": "C:/outside/raw.md"})
            with self.assertRaises(memory_mcp_server.InvalidToolArguments):
                tools.search_past_chats({"query": "設計", "path": "../outside"})

    def test_index_health_reports_missing_ready_and_stale_without_rebuilding(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            tools = memory_mcp_server.MemoryTools(root)

            missing = tools.get_index_health({})
            self.assertEqual("missing", missing["status"])
            self.assertFalse((root / "memory" / "search_index.sqlite3").exists())

            db_path = memory_index.rebuild_index(root)
            ready = tools.get_index_health({})
            self.assertEqual("ready", ready["status"])
            self.assertTrue(ready["index"]["schema_compatible"])
            self.assertEqual("sqlite", ready["search_strategy"])

            raw = root / "conversations" / "2099" / "01" / "2099-01-02_030405" / "raw.md"
            future = db_path.stat().st_mtime + 2
            os.utime(raw, (future, future))
            stale = tools.get_index_health({})
            self.assertEqual("stale", stale["status"])
            self.assertIn("changed-source", stale["stale_reasons"])
            self.assertEqual("markdown", stale["search_strategy"])

    def test_index_health_reports_versionless_manifest_as_legacy_markdown(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            db_path = memory_index.rebuild_index(root)
            with closing(sqlite3.connect(db_path)) as connection:
                connection.execute("DROP TABLE index_metadata")
                connection.commit()
            before = db_path.read_bytes()
            tools = memory_mcp_server.MemoryTools(root)

            health = tools.get_index_health({})
            search = tools.search_past_chats(
                {
                    "query": "ALPHA_USER_SEARCH_TOKEN",
                    "role": "user",
                    "scope": "finalized",
                }
            )

            self.assertEqual("legacy", health["status"])
            self.assertFalse(health["usable"])
            self.assertTrue(health["stale"])
            self.assertIn("index-version-metadata-missing", health["stale_reasons"])
            self.assertEqual("markdown", health["search_strategy"])
            self.assertEqual("markdown", search["search_source"])
            self.assertGreater(search["result_count"], 0)
            self.assertEqual(before, db_path.read_bytes())

    def test_active_live_session_is_immutable_exclusion_for_search_open_and_health(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            live = root / "inbox" / "live" / "2099-01-04_050607.jsonl"
            tools = memory_mcp_server.MemoryTools(root, exclude_live_session=live)

            health = tools.get_index_health({})
            result = tools.search_past_chats(
                {
                    "query": "ALPHA_LIVE_SEARCH_TOKEN",
                    "role": "user",
                    "scope": "live",
                }
            )

            self.assertEqual(0, result["result_count"])
            self.assertEqual(0, health["unorganized_live_file_count"])
            with self.assertRaises(memory_mcp_server.ToolExecutionError):
                tools.open_conversation(
                    {
                        "reference": "inbox/live/2099-01-04_050607.jsonl#message-1-user",
                    }
                )

    def test_excluded_live_session_must_be_direct_jsonl_under_live_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)

            with self.assertRaises(ValueError):
                memory_mcp_server.MemoryTools(root, exclude_live_session=root / "outside.jsonl")
            with self.assertRaises(ValueError):
                memory_mcp_server.MemoryTools(
                    root,
                    exclude_live_session=root / "inbox" / "live" / "nested" / "session.jsonl",
                )

    def test_all_tool_calls_leave_personal_files_unchanged(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            tools = memory_mcp_server.MemoryTools(root)
            before = self.snapshot(root)

            tools.search_past_chats({"query": "ALPHA_USER_SEARCH_TOKEN", "role": "user"})
            tools.open_conversation(
                {
                    "reference": "conversations/2099/01/2099-01-02_030405/raw.md#message-1-user",
                    "max_chars": 500,
                }
            )
            tools.get_personal_memory({"scope": "all", "max_chars": 1_000})
            tools.get_index_health({})

            self.assertEqual(before, self.snapshot(root))
            self.assertFalse((root / "memory" / "search_index.sqlite3").exists())

    def test_jsonrpc_stdio_supports_repeated_searches_in_one_session(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            messages = [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": memory_mcp_server.CURRENT_PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "1"},
                    },
                },
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "search_past_chats",
                        "arguments": {"query": "ALPHA_USER_SEARCH_TOKEN", "role": "user"},
                    },
                },
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {
                        "name": "search_past_chats",
                        "arguments": {"query": "ALPHA_SECOND_USER_TOKEN", "role": "user"},
                    },
                },
            ]
            completed = subprocess.run(
                [sys.executable, str(SCRIPTS / "memory_mcp_server.py"), "--root", str(root)],
                input="".join(json.dumps(message, ensure_ascii=False) + "\n" for message in messages),
                text=True,
                encoding="utf-8",
                capture_output=True,
                timeout=20,
                check=False,
            )

            self.assertEqual(0, completed.returncode, completed.stderr)
            responses = [json.loads(line) for line in completed.stdout.splitlines()]
            self.assertEqual([1, 2, 3, 4], [response["id"] for response in responses])
            self.assertEqual(memory_mcp_server.CURRENT_PROTOCOL_VERSION, responses[0]["result"]["protocolVersion"])
            self.assertEqual(4, len(responses[1]["result"]["tools"]))
            self.assertFalse(responses[2]["result"]["isError"])
            self.assertFalse(responses[3]["result"]["isError"])
            self.assertEqual("", completed.stderr)

    def test_cli_project_scope_is_immutable_for_search_and_direct_open(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            messages = [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": memory_mcp_server.CURRENT_PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "1"},
                    },
                },
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "search_past_chats",
                        "arguments": {"query": "ALPHA_SECOND_USER_TOKEN", "role": "user"},
                    },
                },
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "search_past_chats",
                        "arguments": {"query": "設計", "project_scope": "Project-Beta"},
                    },
                },
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {
                        "name": "open_conversation",
                        "arguments": {
                            "reference": "conversations/2026/07/2026-07-12_100000/raw.md"
                        },
                    },
                },
            ]
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "memory_mcp_server.py"),
                    "--root",
                    str(root),
                    "--project-scope",
                    "Project-Alpha",
                ],
                input="".join(json.dumps(message, ensure_ascii=False) + "\n" for message in messages),
                text=True,
                encoding="utf-8",
                capture_output=True,
                timeout=20,
                check=False,
            )

            self.assertEqual(0, completed.returncode, completed.stderr)
            responses = [json.loads(line) for line in completed.stdout.splitlines()]
            self.assertEqual([1, 2, 3, 4], [response["id"] for response in responses])
            search = responses[1]["result"]["structuredContent"]
            self.assertEqual("Project-Alpha", search["filters"]["project_scope"])
            self.assertGreaterEqual(search["result_count"], 1)
            self.assertEqual(-32602, responses[2]["error"]["code"])
            self.assertTrue(responses[3]["result"]["isError"])
            self.assertEqual("", completed.stderr)

    def test_run_stdio_returns_jsonrpc_parse_error_without_logging_input(self):
        output = io.StringIO()

        code = memory_mcp_server.run_stdio(
            root=ROOT,
            instream=io.StringIO("not-private-valid-json\n"),
            outstream=output,
        )

        self.assertEqual(0, code)
        response = json.loads(output.getvalue())
        self.assertEqual(-32700, response["error"]["code"])
        self.assertNotIn("not-private-valid-json", output.getvalue())

    def test_cli_help_smoke(self):
        completed = subprocess.run(
            [sys.executable, str(SCRIPTS / "memory_mcp_server.py"), "--help"],
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=20,
            check=False,
        )

        self.assertEqual(0, completed.returncode)
        self.assertIn("--root", completed.stdout)
        self.assertIn("--project-scope", completed.stdout)
        self.assertIn("read-only", completed.stdout.lower())

    @staticmethod
    def snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
        snapshot: dict[str, tuple[bytes, int]] = {}
        for path in root.rglob("*"):
            if path.is_file():
                snapshot[path.relative_to(root).as_posix()] = (path.read_bytes(), path.stat().st_mtime_ns)
        return snapshot


if __name__ == "__main__":
    unittest.main()
