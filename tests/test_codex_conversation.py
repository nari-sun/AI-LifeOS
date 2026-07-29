import json
import io
import inspect
import os
import queue
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
SCRIPT = ROOT / "scripts" / "codex_conversation.py"

import codex_conversation  # noqa: E402
from live_session import create_live_message  # noqa: E402


def memory_mcp_inventory(include_personal_memory=True):
    tools = {
        name: {}
        for name in codex_conversation.MEMORY_MCP_TOOLS
        if include_personal_memory or name != "get_personal_memory"
    }
    return [{"name": codex_conversation.MEMORY_MCP_SERVER_NAME, "tools": tools}]


def notion_mcp_inventory(tools=None):
    return [
        {
            "name": codex_conversation.NOTION_MCP_SERVER_NAME,
            "tools": {name: {} for name in (tools or ("fetch",))},
            "resources": [],
            "resourceTemplates": [],
        }
    ]


class FakeAppServerOutput:
    def __init__(self):
        self.lines = queue.Queue()

    def __iter__(self):
        return self

    def __next__(self):
        value = self.lines.get(timeout=2)
        if value is None:
            raise StopIteration
        return json.dumps(value) + "\n"

    def push(self, value):
        self.lines.put(value)

    def close(self):
        self.lines.put(None)


class FakeAppServerStdin:
    def __init__(self, process, complete_status="completed"):
        self.process = process
        self.complete_status = complete_status

    def write(self, raw):
        request = json.loads(raw)
        self.process.requests.append(request)
        method = request.get("method")
        if method == "initialize":
            self.process.stdout.push({"id": request["id"], "result": {}})
        elif method == "mcpServerStatus/list":
            self.process.stdout.push(
                {
                    "id": request["id"],
                    "result": {"data": self.process.mcp_inventory, "nextCursor": None},
                }
            )
        elif method == "thread/start":
            self.process.stdout.push({"id": request["id"], "result": {"thread": {"id": "thread-1"}}})
        elif method == "turn/start":
            self.process.stdout.push({"id": request["id"], "result": {"turn": {"id": "turn-1"}}})
            self.process.stdout.push(
                {
                    "method": "item/agentMessage/delta",
                    "params": {"threadId": "thread-1", "turnId": "turn-1", "itemId": "item-1", "delta": "途中"},
                }
            )
            if self.complete_status == "completed":
                for item in self.process.mcp_items:
                    self.process.stdout.push(
                        {
                            "method": "item/completed",
                            "params": {"threadId": "thread-1", "turnId": "turn-1", "item": item},
                        }
                    )
                self.process.stdout.push(
                    {
                        "method": "item/completed",
                        "params": {
                            "threadId": "thread-1",
                            "turnId": "turn-1",
                            "item": {"id": "item-1", "type": "agentMessage", "text": "確定返答"},
                        },
                    }
                )
                self.process.stdout.push(
                    {
                        "method": "turn/completed",
                        "params": {"threadId": "thread-1", "turn": {"id": "turn-1", "status": "completed", "items": []}},
                    }
                )
        elif method == "turn/interrupt":
            self.process.stdout.push({"id": request["id"], "result": {}})
            self.process.stdout.push(
                {
                    "method": "turn/completed",
                    "params": {"threadId": "thread-1", "turn": {"id": "turn-1", "status": "interrupted", "items": []}},
                }
            )
        return len(raw)

    def flush(self):
        return None


class FakeAppServerProcess:
    def __init__(self, complete_status="completed", mcp_inventory=None, mcp_items=None):
        self.requests = []
        self.mcp_inventory = list(mcp_inventory or [])
        self.mcp_items = list(mcp_items or [])
        self.stdout = FakeAppServerOutput()
        self.stderr = io.StringIO("")
        self.stdin = FakeAppServerStdin(self, complete_status=complete_status)
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 0
        self.stdout.close()

    def wait(self, timeout=None):
        self.returncode = 0
        return 0

    def kill(self):
        self.returncode = -9
        self.stdout.close()


class CodexConversationTests(unittest.TestCase):
    def setUp(self):
        self.original_mcp_inventory_reader = codex_conversation._list_configured_mcp_server_names
        patcher = mock.patch.object(codex_conversation, "_list_configured_mcp_server_names", return_value=())
        self.mock_mcp_inventory_reader = patcher.start()
        self.addCleanup(patcher.stop)

    def make_live_file(self, root: Path) -> Path:
        live_dir = root / "inbox" / "live"
        live_dir.mkdir(parents=True, exist_ok=True)
        path = live_dir / "2026-07-01_223000.jsonl"
        records = [
            {
                "role": "user",
                "timestamp": "2026-07-01T22:30:00+09:00",
                "content": "再開したい",
            },
            {
                "role": "assistant",
                "timestamp": "2026-07-01T22:30:05+09:00",
                "content": "再開候補です。",
            },
        ]
        path.write_text(
            "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
            encoding="utf-8",
        )
        return path

    def test_resume_command_can_select_number(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.make_live_file(root)

            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--root",
                    str(root),
                ],
                input="/resume\n1\n/exit\n",
                text=True,
                encoding="utf-8",
                capture_output=True,
                timeout=10,
            )

            self.assertEqual("", completed.stderr)
            self.assertEqual(0, completed.returncode)
            self.assertIn("2026-07-01_223000.jsonl", completed.stdout)

    def test_no_ai_mode_saves_user_message_without_codex_call(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--root",
                    str(root),
                    "--no-ai",
                    "--no-finalize-on-exit",
                ],
                input="Hello live chat.\n/exit\n",
                text=True,
                encoding="utf-8",
                capture_output=True,
                timeout=10,
            )

            self.assertEqual("", completed.stderr)
            self.assertEqual(0, completed.returncode)

            files = list((root / "inbox" / "live").glob("*.jsonl"))
            self.assertEqual(1, len(files))
            records = [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines()]
            self.assertEqual(1, len(records))
            self.assertEqual("user", records[0]["role"])
            self.assertEqual("Hello live chat.", records[0]["content"])

    def test_temporary_cli_session_keeps_live_log_and_skips_archive(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--root",
                    str(root),
                    "--temporary",
                    "--no-ai",
                ],
                input="temporary note\n/exit\n",
                text=True,
                encoding="utf-8",
                capture_output=True,
                timeout=10,
            )

            self.assertEqual(0, completed.returncode)
            live_files = list((root / "inbox" / "live").glob("*.jsonl"))
            self.assertEqual(1, len(live_files))
            metadata = json.loads(live_files[0].with_suffix(".session.json").read_text(encoding="utf-8"))
            self.assertTrue(metadata["personalization"]["temporary"])
            self.assertTrue(metadata["personalization"]["exclude_from_memory"])
            self.assertFalse((root / "conversations").exists())

    def test_new_cli_session_snapshots_global_personalization_but_resume_preserves_it(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            memory = root / "memory"
            memory.mkdir()
            settings_path = memory / "personalization_settings.json"
            settings_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "memory_enabled": False,
                        "past_chat_search_enabled": True,
                        "project_scope": "Snapshot Scope",
                    }
                ),
                encoding="utf-8",
            )

            first = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--root",
                    str(root),
                    "--no-ai",
                    "--no-finalize-on-exit",
                ],
                input="snapshot me\n/exit\n",
                text=True,
                encoding="utf-8",
                capture_output=True,
                timeout=10,
            )
            self.assertEqual(0, first.returncode)
            live_file = next((root / "inbox" / "live").glob("*.jsonl"))
            sidecar = live_file.with_suffix(".session.json")
            snapshot = json.loads(sidecar.read_text(encoding="utf-8"))["personalization"]
            self.assertFalse(snapshot["memory_enabled"])
            self.assertTrue(snapshot["past_chat_search_enabled"])
            self.assertEqual("Snapshot Scope", snapshot["project_scope"])

            settings_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "memory_enabled": True,
                        "past_chat_search_enabled": False,
                        "project_scope": "Changed Global Scope",
                    }
                ),
                encoding="utf-8",
            )
            resumed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--root",
                    str(root),
                    "--resume",
                    str(live_file),
                    "--no-ai",
                    "--no-finalize-on-exit",
                ],
                input="/exit\n",
                text=True,
                encoding="utf-8",
                capture_output=True,
                timeout=10,
            )

            self.assertEqual(0, resumed.returncode)
            self.assertEqual(snapshot, json.loads(sidecar.read_text(encoding="utf-8"))["personalization"])

    def test_build_codex_chat_prompt_contains_recent_context_and_guardrails(self):
        messages = [
            create_live_message("user", "First"),
            create_live_message("assistant", "Second"),
            create_live_message("user", "Latest"),
        ]

        prompt = codex_conversation.build_codex_chat_prompt(messages, max_context_messages=2)

        self.assertNotIn("First", prompt)
        self.assertIn("Assistant:\nSecond", prompt)
        self.assertIn("User:\nLatest", prompt)
        self.assertIn("Do not edit files", prompt)

    def test_build_codex_chat_prompt_exposes_ephemeral_notion_tool_rules(self):
        messages = [create_live_message("user", "Notionの仕様を確認して")]

        prompt = codex_conversation.build_codex_chat_prompt(
            messages,
            notion_tools_enabled=True,
        )

        self.assertIn("Notion-grounding rules:", prompt)
        self.assertIn("untrusted evidence", prompt)
        self.assertIn("Workspace search is intentionally unavailable", prompt)
        self.assertIn("MCP response bodies are ephemeral", prompt)
        self.assertNotIn("PRIVATE_NOTION_PROMPT_BODY", prompt)
        self.assertNotIn("Memory Context:", prompt)

    def test_build_codex_chat_prompt_omits_notion_section_when_disabled(self):
        prompt = codex_conversation.build_codex_chat_prompt(
            [create_live_message("user", "local only")],
            notion_tools_enabled=False,
        )

        self.assertNotIn("Notion-grounding rules:", prompt)
        self.assertNotIn("Notion Context:", prompt)

    def test_notion_mcp_config_is_required_and_read_only(self):
        options = codex_conversation._notion_mcp_config_options()

        self.assertIn("mcp_servers.ai_lifeos_notion.enabled=true", options)
        self.assertIn("mcp_servers.ai_lifeos_notion.required=true", options)
        enabled = next(value for value in options if value.startswith("mcp_servers.ai_lifeos_notion.enabled_tools="))
        self.assertIn('"fetch"', enabled)
        self.assertNotIn("search", enabled)
        self.assertNotIn("create", enabled)

    def test_app_server_notion_inventory_rejects_search_before_thread_start(self):
        process = FakeAppServerProcess(mcp_inventory=notion_mcp_inventory(("fetch", "search")))

        with self.assertRaises(codex_conversation.NotionMCPUnavailable):
            codex_conversation.generate_assistant_reply_streaming_with_context(
                root=ROOT,
                messages=[create_live_message("user", "Notionを見て")],
                on_delta=lambda _: None,
                include_memory_context=False,
                notion_reference=True,
                popen=lambda *args, **kwargs: process,
            )

        self.assertFalse(any(request.get("method") == "thread/start" for request in process.requests))

    def test_parser_defaults_to_no_fast_mode_or_service_tier(self):
        args = codex_conversation.build_parser().parse_args([])

        self.assertIsNone(args.chat_codex_service_tier)
        self.assertFalse(args.chat_codex_fast_mode)

    def test_fail_closed_personalization_supplies_all_required_fields(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with mock.patch.object(
                codex_conversation,
                "load_session_personalization",
                side_effect=ValueError("private metadata body"),
            ):
                settings = codex_conversation._load_session_personalization_fail_closed(
                    root, root / "inbox/live/x.jsonl"
                )

        self.assertTrue(settings.temporary)
        self.assertTrue(settings.temporary_locked)
        self.assertTrue(settings.exclude_from_memory)
        self.assertFalse(settings.memory_enabled)
        self.assertFalse(settings.past_chat_search_enabled)

    def test_debug_argv_redacts_project_scope_forms(self):
        sanitized = codex_conversation._sanitized_argv_for_debug(
            ["--root", "C:/repo", "--project-scope", "Secret Project", "--project-scope=Other Secret"]
        )

        self.assertEqual(
            ["--root", "C:/repo", "--project-scope", "<redacted>", "--project-scope=<redacted>"],
            sanitized,
        )
        self.assertNotIn("Secret Project", repr(sanitized))
        self.assertNotIn("Other Secret", repr(sanitized))

    def test_memory_mcp_config_binds_scope_and_requires_startup(self):
        options = codex_conversation._memory_mcp_config_options(
            ROOT,
            include_personal_memory=False,
            project_scope="Private Project",
        )
        args_value = next(value for value in options if value.startswith("mcp_servers.ai_lifeos_memory.args="))

        server_args = json.loads(args_value.split("=", 1)[1])
        self.assertEqual("--project-scope", server_args[-2])
        self.assertEqual("Private Project", server_args[-1])
        self.assertIn("mcp_servers.ai_lifeos_memory.required=true", options)
        enabled_tools = next(
            value for value in options if value.startswith("mcp_servers.ai_lifeos_memory.enabled_tools=")
        )
        self.assertNotIn("get_personal_memory", enabled_tools)

    def test_tool_isolation_disables_resolved_ambient_servers(self):
        options = codex_conversation._codex_tool_isolation_options(
            ("github", "node_repl", codex_conversation.MEMORY_MCP_SERVER_NAME)
        )

        self.assertIn("mcp_servers.github.enabled=false", options)
        self.assertIn("mcp_servers.node_repl.enabled=false", options)
        self.assertIn("mcp_servers.ai_lifeos_memory.enabled=false", options)
        self.assertIn("features.shell_tool=false", options)
        self.assertIn("features.apps=false", options)
        self.assertIn('web_search="disabled"', options)

    def test_mcp_inventory_reader_accepts_only_safe_names(self):
        calls = []

        def fake_run(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="\ufeff" + json.dumps([{"name": "github"}, {"name": "node_repl"}]),
                stderr="ignored",
            )

        names = self.original_mcp_inventory_reader("codex.cmd", ROOT, run_command=fake_run)

        self.assertEqual(("github", "node_repl"), names)
        self.assertEqual(
            [
                "codex.cmd",
                "mcp",
                "list",
                "--json",
                "-c",
                "features.plugins=false",
                "-c",
                "features.apps=false",
                "-c",
                "features.remote_plugin=false",
            ],
            calls[0][0],
        )
        self.assertEqual(10, calls[0][1]["timeout"])

    def test_mcp_inventory_reader_fails_closed_on_unsafe_name(self):
        def fake_run(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, stdout='[{"name":"bad.name"}]', stderr="")

        with self.assertRaisesRegex(RuntimeError, "unsupported server name"):
            self.original_mcp_inventory_reader("codex.cmd", ROOT, run_command=fake_run)

    def test_build_codex_chat_prompt_requires_grounded_memory_recall(self):
        memory_context = """AI-LifeOS memory context (read-only).

## Conversation Matches
- Date: 2099-06-07
  Source: conversations/2099/06/2099-06-07_080910/summary.md
  Snippet: SYNTHETIC_OPINION_MARKER — ユーザーは架空人物アルファの合成上の判断を指摘した。"""

        prompt = codex_conversation.build_codex_chat_prompt(
            [create_live_message("user", "前に話した感想を覚えてる？")],
            memory_context=memory_context,
        )

        self.assertIn("Memory-grounding rules:", prompt)
        self.assertIn("state only claims supported by that context", prompt)
        self.assertIn("Do not fill gaps with general knowledge", prompt)
        self.assertIn("Do not reverse, soften, or strengthen a stored claim", prompt)
        self.assertIn("say that the stored records do not confirm it", prompt)
        self.assertIn(
            "SYNTHETIC_OPINION_MARKER — ユーザーは架空人物アルファの合成上の判断を指摘した",
            prompt,
        )

    def test_build_codex_chat_prompt_instructs_agentic_memory_retry(self):
        prompt = codex_conversation.build_codex_chat_prompt(
            [create_live_message("user", "前に話した映画の感想を教えて")],
            memory_tools_enabled=True,
        )

        self.assertIn("search_past_chats", prompt)
        self.assertIn("zero-result first search is not proof", prompt)
        self.assertIn("open_conversation", prompt)
        self.assertIn("Do not describe the search index as broken", prompt)
        self.assertIn("If the reported status is fresh or ready", prompt)
        self.assertIn("Never use these tools to write", prompt)

    def test_full_archive_review_prompt_requires_paged_coverage(self):
        prompt = codex_conversation.build_codex_chat_prompt(
            [create_live_message("user", "過去の会話を全部見てから答えて")],
            memory_tools_enabled=True,
            full_archive_review=True,
        )

        self.assertIn("Full archive review is explicitly required", prompt)
        self.assertIn("list_past_chat_sources", prompt)
        self.assertIn("read_past_chat_page", prompt)
        self.assertIn("every listed source was fully read", prompt)
        self.assertTrue(codex_conversation._requests_full_archive_review("過去の会話を全部見て"))
        self.assertTrue(codex_conversation._requests_full_archive_review("review all past chats"))
        self.assertFalse(codex_conversation._requests_full_archive_review("前の会話を一つ探して"))

    def test_archive_review_status_requires_every_source_to_be_paged_to_eof(self):
        complete = codex_conversation.MemoryMCPTrace(
            archive_total_source_count=2,
            archive_listed_paths=(
                "conversations/2099/01/a/raw.md",
                "conversations/2099/01/b/raw.md",
            ),
            archive_listing_complete=True,
            archive_read_pages=(
                ("conversations/2099/01/a/raw.md", 0, 10, 10),
                ("conversations/2099/01/b/raw.md", 0, 4, 9),
                ("conversations/2099/01/b/raw.md", 4, 9, 9),
            ),
        )
        incomplete = codex_conversation.MemoryMCPTrace(
            archive_total_source_count=2,
            archive_listed_paths=complete.archive_listed_paths,
            archive_listing_complete=True,
            archive_read_pages=(("conversations/2099/01/a/raw.md", 0, 10, 10),),
        )

        complete_status = codex_conversation._archive_review_status(complete)
        incomplete_status = codex_conversation._archive_review_status(incomplete)
        self.assertTrue(complete_status.complete)
        self.assertEqual(2, complete_status.reviewed_source_count)
        self.assertFalse(incomplete_status.complete)
        self.assertEqual(1, incomplete_status.reviewed_source_count)

    def test_archive_review_trace_extracts_mcp_inventory_and_pages(self):
        source_path = "conversations/2099/01/2099-01-02_030405/raw.md"
        inventory_item = {
            "type": "mcpToolCall",
            "server": codex_conversation.MEMORY_MCP_SERVER_NAME,
            "tool": "list_past_chat_sources",
            "status": "completed",
            "result": {
                "structuredContent": {
                    "total_source_count": 1,
                    "unreadable_source_count": 0,
                    "next_cursor": None,
                    "sources": [{"path": source_path}],
                }
            },
        }
        page_item = {
            "type": "mcp_tool_call",
            "server": codex_conversation.MEMORY_MCP_SERVER_NAME,
            "tool": "read_past_chat_page",
            "status": "completed",
            "result": {
                "structured_content": {
                    "source": {
                        "path": source_path,
                        "document_type": "raw",
                        "title": "Synthetic",
                        "date": "2099-01-02",
                    },
                    "cursor": 0,
                    "total_chars": 12,
                    "next_cursor": None,
                    "content": "SYNTHETIC",
                }
            },
        }

        trace = codex_conversation._merge_memory_mcp_traces(
            [
                codex_conversation._memory_trace_from_mcp_item(inventory_item),
                codex_conversation._memory_trace_from_mcp_item(page_item),
            ]
        )

        self.assertTrue(codex_conversation._archive_review_status(trace).complete)

    def test_generate_assistant_reply_reads_codex_output_file(self):
        calls = []

        def fake_run(command, **kwargs):
            calls.append((command, kwargs))
            output_path = Path(command[command.index("--output-last-message") + 1])
            output_path.write_text("Fake assistant reply.\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout="progress", stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            reply = codex_conversation.generate_assistant_reply(
                root=root,
                messages=[create_live_message("user", "Hello")],
                run_command=fake_run,
            )

        command = calls[0][0]
        self.assertEqual("Fake assistant reply.", reply)
        self.assertEqual("codex.cmd", command[0])
        self.assertEqual("gpt-5.6-luna", command[command.index("--model") + 1])
        self.assertIn('model_reasoning_effort="medium"', command)
        self.assertNotIn('service_tier="fast"', command)
        self.assertIn("features.fast_mode=false", command)
        self.assertIn("--json", command)
        # Keep the resolved server definitions so per-server `enabled=false`
        # overrides remain valid transports; every ambient server is disabled
        # explicitly before the scoped Memory MCP is redefined below.
        self.assertNotIn("--ignore-user-config", command)
        self.assertIn("--ignore-rules", command)
        self.assertIn("--ephemeral", command)
        self.assertIn("features.shell_tool=false", command)
        self.assertIn('web_search="disabled"', command)
        self.assertIn("mcp_servers.ai_lifeos_memory.enabled=true", command)
        self.assertIn("mcp_servers.ai_lifeos_memory.required=true", command)
        self.assertTrue(any("memory_mcp_server.py" in value for value in command))
        self.assertIn(
            'mcp_servers.ai_lifeos_memory.enabled_tools=["search_past_chats", "open_conversation", '
            '"list_past_chat_sources", "read_past_chat_page", "get_personal_memory", "get_index_health"]',
            command,
        )
        self.assertIn("--sandbox", command)
        self.assertEqual("read-only", command[command.index("--sandbox") + 1])
        self.assertIn("User:\nHello", calls[0][1]["input"])

    def test_generate_assistant_reply_notion_on_uses_process_scoped_mcp_and_returns_only_metadata(self):
        calls = []
        private_body = "PRIVATE_NOTION_RESPONSE_BODY"

        def fake_run(command, **kwargs):
            calls.append((command, kwargs))
            output_path = Path(command[command.index("--output-last-message") + 1])
            output_path.write_text("Notionに基づく回答", encoding="utf-8")
            event = {
                "type": "item.completed",
                "item": {
                    "type": "mcp_tool_call",
                    "server": "ai_lifeos_notion",
                    "tool": "fetch",
                    "status": "completed",
                    "arguments": {"id": "https://www.notion.so/Product-spec-11111111111141118111111111111111"},
                    "result": {
                        "structured_content": {
                            "metadata": {
                                "object": "page",
                                "id": "11111111-1111-4111-8111-111111111111",
                                "title": "Product spec",
                                "url": "https://www.notion.so/Product-spec-11111111111141118111111111111111",
                            },
                            "text": private_body,
                        }
                    },
                },
            }
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(event), stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            result = codex_conversation.generate_assistant_reply_with_context(
                root=Path(temp_dir),
                messages=[create_live_message("user", "このNotion pageを確認して")],
                include_memory_context=False,
                notion_reference=True,
                run_command=fake_run,
            )

        command, kwargs = calls[0]
        self.assertIn("mcp_servers.ai_lifeos_notion.enabled=true", command)
        enabled = next(value for value in command if value.startswith("mcp_servers.ai_lifeos_notion.enabled_tools="))
        self.assertNotIn("search", enabled)
        self.assertIn("Notion-grounding rules:", kwargs["input"])
        self.assertNotIn(private_body, kwargs["input"])
        self.assertTrue(result.notion_context.used)
        serialized = json.dumps(result.notion_context, default=lambda value: value.__dict__)
        self.assertNotIn(private_body, serialized)

    def test_full_archive_request_replaces_an_unverified_reply_with_incomplete_status(self):
        self.mock_mcp_inventory_reader.return_value = (codex_conversation.MEMORY_MCP_SERVER_NAME,)

        def fake_run(command, **kwargs):
            output_path = Path(command[command.index("--output-last-message") + 1])
            output_path.write_text("全履歴を確認しました。", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            result = codex_conversation.generate_assistant_reply_with_context(
                root=Path(temp_dir),
                messages=[create_live_message("user", "過去の会話を全部見てから答えて")],
                run_command=fake_run,
            )

        self.assertIn("全件確認を完了できませんでした", result.reply)
        self.assertNotIn("全履歴を確認しました", result.reply)

    def test_forced_full_archive_review_does_not_depend_on_message_wording(self):
        self.mock_mcp_inventory_reader.return_value = (codex_conversation.MEMORY_MCP_SERVER_NAME,)
        prompts = []

        def fake_run(command, **kwargs):
            prompts.append(kwargs["input"])
            output_path = Path(command[command.index("--output-last-message") + 1])
            output_path.write_text("回答です。", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            result = codex_conversation.generate_assistant_reply_with_context(
                root=Path(temp_dir),
                messages=[create_live_message("user", "傾向を教えて")],
                force_full_archive_review=True,
                run_command=fake_run,
            )

        self.assertIn("Full archive review is explicitly required", prompts[0])
        self.assertIn("全件確認を完了できませんでした", result.reply)

    def test_generate_assistant_reply_can_disable_memory_mcp(self):
        calls = []
        self.mock_mcp_inventory_reader.return_value = (codex_conversation.MEMORY_MCP_SERVER_NAME,)

        def fake_run(command, **kwargs):
            calls.append((command, kwargs))
            output_path = Path(command[command.index("--output-last-message") + 1])
            output_path.write_text("reply", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            codex_conversation.generate_assistant_reply(
                root=Path(temp_dir),
                messages=[create_live_message("user", "Hello")],
                include_memory_context=False,
                run_command=fake_run,
            )

        command, kwargs = calls[0]
        self.assertIn("mcp_servers.ai_lifeos_memory.enabled=false", command)
        self.assertNotIn("mcp_servers.ai_lifeos_memory.enabled=true", command)
        self.assertNotIn("search_past_chats", kwargs["input"])

    def test_generate_assistant_reply_preserves_legacy_positional_runner_slot(self):
        calls = []
        self.mock_mcp_inventory_reader.return_value = (codex_conversation.MEMORY_MCP_SERVER_NAME,)

        def fake_run(command, **kwargs):
            calls.append(command)
            output_path = Path(command[command.index("--output-last-message") + 1])
            output_path.write_text("legacy reply", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            reply = codex_conversation.generate_assistant_reply(
                Path(temp_dir),
                [create_live_message("user", "Hello")],
                "codex.cmd",
                "read-only",
                "never",
                None,
                None,
                None,
                False,
                20,
                False,
                fake_run,
            )

        self.assertEqual("legacy reply", reply)
        self.assertEqual(1, len(calls))
        self.assertIn("mcp_servers.ai_lifeos_memory.enabled=false", calls[0])

    def test_nonzero_exec_error_does_not_expose_stdout_or_stderr(self):
        def fake_run(command, **kwargs):
            return subprocess.CompletedProcess(
                command,
                7,
                stdout='{"item":{"result":{"structured_content":{"content":"SECRET_MCP_BODY"}}}}',
                stderr="SECRET_STDERR_BODY",
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(RuntimeError) as raised:
                codex_conversation.generate_assistant_reply_with_context(
                    root=Path(temp_dir),
                    messages=[create_live_message("user", "Hello")],
                    run_command=fake_run,
                )

        message = str(raised.exception)
        self.assertEqual("Codex CLI failed with exit code 7.", message)
        self.assertNotIn("SECRET_MCP_BODY", message)
        self.assertNotIn("SECRET_STDERR_BODY", message)

    def test_core_memory_toggle_removes_personal_memory_mcp_tool(self):
        calls = []

        def fake_run(command, **kwargs):
            calls.append((command, kwargs))
            output_path = Path(command[command.index("--output-last-message") + 1])
            output_path.write_text("reply", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            codex_conversation.generate_assistant_reply(
                root=Path(temp_dir),
                messages=[create_live_message("user", "前の会話")],
                include_memory_context=False,
                enable_memory_mcp=True,
                include_core_memory=False,
                include_past_chats=True,
                run_command=fake_run,
            )

        command, kwargs = calls[0]
        enabled_tools = next(
            value
            for value in command
            if value.startswith("mcp_servers.ai_lifeos_memory.enabled_tools=")
        )
        self.assertIn("search_past_chats", enabled_tools)
        self.assertNotIn("get_personal_memory", enabled_tools)
        self.assertNotIn("Use get_personal_memory", kwargs["input"])

    def test_search_candidates_do_not_overstate_memory_as_used(self):
        def fake_run(command, **kwargs):
            output_path = Path(command[command.index("--output-last-message") + 1])
            output_path.write_text("source-grounded reply", encoding="utf-8")
            event = {
                "type": "item.completed",
                "item": {
                    "id": "item-1",
                    "type": "mcp_tool_call",
                    "server": "ai_lifeos_memory",
                    "tool": "search_past_chats",
                    "status": "completed",
                    "result": {
                        "structured_content": {
                            "results": [
                                {
                                    "score": 91,
                                    "excerpt": "作品についてのユーザー発言",
                                    "source": {
                                        "path": "conversations/2099/06/2099-06-07_080910/raw.md",
                                        "document_type": "raw_chunk",
                                        "title": "Example / user message 3",
                                        "date": "2099-06-07",
                                        "role": "user",
                                        "message_number": 3,
                                    },
                                }
                            ]
                        }
                    },
                },
            }
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(event), stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            result = codex_conversation.generate_assistant_reply_with_context(
                root=Path(temp_dir),
                messages=[create_live_message("user", "前の感想")],
                include_memory_context=False,
                enable_memory_mcp=True,
                run_command=fake_run,
            )

        self.assertEqual("source-grounded reply", result.reply)
        self.assertIsNone(result.memory_context)
        self.assertEqual(1, len(result.memory_candidates))
        reference = result.memory_candidates[0]
        self.assertEqual("conversations/2099/06/2099-06-07_080910/raw.md", reference.path)
        self.assertEqual("user", reference.speaker_role)
        self.assertEqual(3, reference.message_number)

    def test_opened_mcp_source_is_reported_as_used_primary_evidence(self):
        def fake_run(command, **kwargs):
            output_path = Path(command[command.index("--output-last-message") + 1])
            output_path.write_text("grounded reply", encoding="utf-8")
            event = {
                "type": "item.completed",
                "item": {
                    "type": "mcp_tool_call",
                    "server": "ai_lifeos_memory",
                    "tool": "open_conversation",
                    "status": "completed",
                    "result": {
                        "structured_content": {
                            "source": {
                                "path": "conversations/2099/06/2099-06-07_080910/raw.md",
                                "document_type": "raw",
                                "title": "Example",
                                "date": "2099-06-07",
                            },
                            "messages": [
                                {"role": "user", "message_number": 3, "text": "primary evidence"}
                            ],
                        }
                    },
                },
            }
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(event), stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            result = codex_conversation.generate_assistant_reply_with_context(
                root=Path(temp_dir),
                messages=[create_live_message("user", "recall it")],
                include_memory_context=False,
                enable_memory_mcp=True,
                run_command=fake_run,
            )

        self.assertIsNotNone(result.memory_context)
        self.assertTrue(result.memory_context.used_memory)
        self.assertEqual(1, len(result.memory_context.references))
        self.assertEqual((), result.memory_candidates)
        self.assertEqual(1, len(result.memory_opened))
        self.assertEqual("primary evidence", result.memory_context.references[0].snippet)

    def test_active_live_session_is_forwarded_to_static_and_mcp_retrieval(self):
        captured = {}
        calls = []
        original_build = codex_conversation.build_answer_context

        def fake_build(**kwargs):
            captured.update(kwargs)
            return codex_conversation.AnswerContext(should_use_memory=False, text="", results=())

        def fake_run(command, **kwargs):
            calls.append(command)
            output_path = Path(command[command.index("--output-last-message") + 1])
            output_path.write_text("reply", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            active = root / "inbox" / "live" / "2099-01-02_030405.jsonl"
            active.parent.mkdir(parents=True)
            codex_conversation.build_answer_context = fake_build
            try:
                codex_conversation.generate_assistant_reply_with_context(
                    root=root,
                    messages=[create_live_message("user", "remember")],
                    include_memory_context=True,
                    enable_memory_mcp=True,
                    exclude_live_session=active,
                    run_command=fake_run,
                )
            finally:
                codex_conversation.build_answer_context = original_build

        self.assertEqual(active, captured["exclude_live_session"])
        args_value = next(
            value for value in calls[0] if value.startswith("mcp_servers.ai_lifeos_memory.args=")
        )
        server_args = json.loads(args_value.split("=", 1)[1])
        index = server_args.index("--exclude-live-session")
        self.assertEqual(str(active), server_args[index + 1])

    def test_extracts_camel_case_app_server_mcp_result(self):
        references = codex_conversation._memory_references_from_mcp_item(
            {
                "type": "mcpToolCall",
                "server": "ai_lifeos_memory",
                "tool": "get_personal_memory",
                "status": "completed",
                "result": {
                    "structuredContent": {
                        "sources": [
                            {
                                "path": "memory/preferences.md",
                                "document_type": "memory",
                                "title": "Preferences",
                                "content": "SYNTHETIC_PREFERENCE_TOKEN",
                            }
                        ]
                    }
                },
            }
        )

        self.assertEqual(1, len(references))
        self.assertEqual("memory/preferences.md", references[0].path)
        self.assertIn("SYNTHETIC_PREFERENCE_TOKEN", references[0].snippet)

    def test_streaming_app_server_emits_only_agent_delta_and_returns_completed_text(self):
        process = FakeAppServerProcess()
        deltas = []
        commands = []
        self.mock_mcp_inventory_reader.return_value = (codex_conversation.MEMORY_MCP_SERVER_NAME,)

        def fake_popen(command, **kwargs):
            commands.append(command)
            return process

        result = codex_conversation.generate_assistant_reply_streaming_with_context(
            root=ROOT,
            messages=[create_live_message("user", "Hello")],
            on_delta=deltas.append,
            include_memory_context=False,
            popen=fake_popen,
        )

        self.assertEqual(["途中"], deltas)
        self.assertEqual("確定返答", result.reply)
        thread_start = next(request for request in process.requests if request.get("method") == "thread/start")
        self.assertEqual("read-only", thread_start["params"]["sandbox"])
        self.assertEqual("never", thread_start["params"]["approvalPolicy"])
        self.assertEqual("gpt-5.6-luna", thread_start["params"]["model"])
        turn_start = next(request for request in process.requests if request.get("method") == "turn/start")
        self.assertEqual("gpt-5.6-luna", turn_start["params"]["model"])
        self.assertEqual("medium", turn_start["params"]["effort"])
        self.assertNotIn("serviceTier", thread_start["params"])
        self.assertNotIn("serviceTier", turn_start["params"])
        self.assertIn("features.shell_tool=false", commands[0])
        self.assertIn("mcp_servers.ai_lifeos_memory.enabled=false", commands[0])
        self.assertNotIn("mcp_servers.ai_lifeos_memory.enabled=true", commands[0])
        self.assertTrue(any(request.get("method") == "mcpServerStatus/list" for request in process.requests))

    def test_streaming_notion_on_validates_inventory_and_collects_source_metadata(self):
        notion_item = {
            "type": "mcpToolCall",
            "server": "ai_lifeos_notion",
            "tool": "notion-query-data-sources",
            "status": "completed",
            "arguments": {
                "data_source_urls": [
                    "https://www.notion.so/Tasks-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
                ]
            },
            "result": {
                "structuredContent": {
                    "rows": [
                        {
                            "title": "PRIVATE_ROW_TITLE",
                            "url": "https://www.notion.so/Row-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                            "body": "PRIVATE_ROW_BODY",
                        }
                    ]
                }
            },
        }
        process = FakeAppServerProcess(
            mcp_inventory=notion_mcp_inventory(),
            mcp_items=[notion_item],
        )

        result = codex_conversation.generate_assistant_reply_streaming_with_context(
            root=ROOT,
            messages=[create_live_message("user", "Tasks databaseを確認して")],
            on_delta=lambda _: None,
            include_memory_context=False,
            notion_reference=True,
            popen=lambda *args, **kwargs: process,
        )

        self.assertTrue(result.notion_context.used)
        self.assertEqual(1, len(result.notion_context.sources))
        self.assertEqual("database", result.notion_context.sources[0].object_type)
        self.assertNotIn("PRIVATE_ROW_BODY", repr(result.notion_context))

    def test_streaming_full_archive_request_replaces_unverified_reply(self):
        process = FakeAppServerProcess(mcp_inventory=memory_mcp_inventory())

        result = codex_conversation.generate_assistant_reply_streaming_with_context(
            root=ROOT,
            messages=[create_live_message("user", "傾向を教えて")],
            on_delta=lambda _: None,
            force_full_archive_review=True,
            popen=lambda *args, **kwargs: process,
        )

        self.assertIn("全件確認を完了できませんでした", result.reply)

    def test_streaming_starts_app_server_before_building_memory_context(self):
        process = FakeAppServerProcess(mcp_inventory=memory_mcp_inventory())
        call_order = []
        original_build = codex_conversation.build_answer_context

        def fake_build(**kwargs):
            call_order.append("memory")
            return codex_conversation.AnswerContext(should_use_memory=False, text="", results=())

        def fake_popen(*args, **kwargs):
            call_order.append("popen")
            return process

        codex_conversation.build_answer_context = fake_build
        try:
            codex_conversation.generate_assistant_reply_streaming_with_context(
                root=ROOT,
                messages=[create_live_message("user", "Hello")],
                on_delta=lambda _: None,
                popen=fake_popen,
            )
        finally:
            codex_conversation.build_answer_context = original_build

        self.assertLess(call_order.index("popen"), call_order.index("memory"))

    def test_streaming_fails_closed_when_memory_mcp_is_missing(self):
        process = FakeAppServerProcess(mcp_inventory=[])

        with self.assertRaisesRegex(
            codex_conversation.AppServerStreamingUnavailable,
            "unexpected MCP server",
        ):
            codex_conversation.generate_assistant_reply_streaming_with_context(
                root=ROOT,
                messages=[create_live_message("user", "Hello")],
                on_delta=lambda _: None,
                include_memory_context=True,
                popen=lambda *args, **kwargs: process,
            )

        self.assertFalse(any(request.get("method") == "thread/start" for request in process.requests))

    def test_streaming_fails_closed_when_ambient_mcp_remains_enabled(self):
        process = FakeAppServerProcess(mcp_inventory=[{"name": "github", "tools": {"search": {}}}])

        with self.assertRaisesRegex(
            codex_conversation.AppServerStreamingUnavailable,
            "unexpected MCP server",
        ):
            codex_conversation.generate_assistant_reply_streaming_with_context(
                root=ROOT,
                messages=[create_live_message("user", "Hello")],
                on_delta=lambda _: None,
                include_memory_context=False,
                popen=lambda *args, **kwargs: process,
            )

        self.assertFalse(any(request.get("method") == "thread/start" for request in process.requests))

    def test_memory_context_receives_only_the_two_latest_user_messages(self):
        captured = {}
        original_build = codex_conversation.build_answer_context

        def fake_build(**kwargs):
            captured.update(kwargs)
            return codex_conversation.AnswerContext(should_use_memory=False, text="", results=())

        def fake_run(command, **kwargs):
            output_path = Path(command[command.index("--output-last-message") + 1])
            output_path.write_text("reply", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        messages = [
            create_live_message("user", "古い質問"),
            create_live_message("assistant", "古い返答"),
            create_live_message("user", "直前の質問"),
            create_live_message("assistant", "直前の返答"),
            create_live_message("user", "じゃあスマホは？"),
        ]
        codex_conversation.build_answer_context = fake_build
        try:
            codex_conversation.generate_assistant_reply_with_context(
                root=ROOT,
                messages=messages,
                run_command=fake_run,
            )
        finally:
            codex_conversation.build_answer_context = original_build

        self.assertEqual("じゃあスマホは？", captured["question"])
        self.assertEqual(("直前の質問", "じゃあスマホは？"), captured["recent_user_messages"])

    def test_memory_context_receives_personalization_filters(self):
        captured = {}
        original_build = codex_conversation.build_answer_context

        def fake_build(**kwargs):
            captured.update(kwargs)
            return codex_conversation.AnswerContext(should_use_memory=False, text="", results=())

        def fake_run(command, **kwargs):
            output_path = Path(command[command.index("--output-last-message") + 1])
            output_path.write_text("reply", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        codex_conversation.build_answer_context = fake_build
        try:
            codex_conversation.generate_assistant_reply_with_context(
                root=ROOT,
                messages=[create_live_message("user", "前の話")],
                include_core_memory=False,
                include_past_chats=True,
                project_scope="AI-LifeOS",
                run_command=fake_run,
            )
        finally:
            codex_conversation.build_answer_context = original_build

        self.assertFalse(captured["include_core_memory"])
        self.assertTrue(captured["include_past_chats"])
        self.assertEqual("AI-LifeOS", captured["project_scope"])

    def test_streaming_app_server_interrupt_suppresses_delta_and_raises(self):
        process = FakeAppServerProcess(complete_status="interrupted")
        deltas = []

        with self.assertRaises(InterruptedError):
            codex_conversation.generate_assistant_reply_streaming_with_context(
                root=ROOT,
                messages=[create_live_message("user", "Hello")],
                on_delta=deltas.append,
                is_cancelled=lambda: True,
                include_memory_context=False,
                popen=lambda *args, **kwargs: process,
            )

        self.assertEqual([], deltas)
        interrupt = next(request for request in process.requests if request.get("method") == "turn/interrupt")
        self.assertEqual("thread-1", interrupt["params"]["threadId"])
        self.assertEqual("turn-1", interrupt["params"]["turnId"])

    def test_streaming_interrupt_timeout_forces_process_cleanup(self):
        class SilentInterruptStdin(FakeAppServerStdin):
            def write(self, raw):
                request = json.loads(raw)
                if request.get("method") == "turn/interrupt":
                    self.process.requests.append(request)
                    return len(raw)
                return super().write(raw)

        process = FakeAppServerProcess(complete_status="pending")
        process.stdin = SilentInterruptStdin(process, complete_status="pending")

        with mock.patch.object(codex_conversation, "APP_SERVER_INTERRUPT_TIMEOUT_SECONDS", 0.01):
            with self.assertRaisesRegex(InterruptedError, "停止期限"):
                codex_conversation.generate_assistant_reply_streaming_with_context(
                    root=ROOT,
                    messages=[create_live_message("user", "Hello")],
                    on_delta=lambda _: None,
                    is_cancelled=lambda: True,
                    include_memory_context=False,
                    popen=lambda *args, **kwargs: process,
                )

        self.assertTrue(any(request.get("method") == "turn/interrupt" for request in process.requests))
        self.assertIsNotNone(process.returncode)

    def test_windows_app_server_cleanup_kills_exact_process_tree(self):
        class FakeProcessTree:
            pid = 424242

            def __init__(self):
                self.waited = False
                self.terminated = False

            def poll(self):
                return None

            def wait(self, timeout=None):
                self.waited = True
                return 0

            def terminate(self):
                self.terminated = True

            def kill(self):
                raise AssertionError("taskkill success should not fall through")

        process = FakeProcessTree()
        completed = subprocess.CompletedProcess([], 0)
        with mock.patch.object(codex_conversation.os, "name", "nt"), mock.patch.object(
            codex_conversation.subprocess,
            "run",
            return_value=completed,
        ) as run:
            codex_conversation._terminate_process(process)

        self.assertEqual(
            ["taskkill", "/PID", "424242", "/T", "/F"],
            run.call_args.args[0],
        )
        self.assertTrue(process.waited)
        self.assertFalse(process.terminated)

    def test_generate_assistant_reply_includes_memory_context_for_private_question(self):
        calls = []

        def fake_run(command, **kwargs):
            calls.append((command, kwargs))
            output_path = Path(command[command.index("--output-last-message") + 1])
            output_path.write_text("好みに合わせた返答です。\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "memory").mkdir()
            (root / "memory" / "long_term.md").write_text("# Long-Term Memory\n\n- ユーザーはAI-LifeOSを作っている。\n", encoding="utf-8")
            (root / "memory" / "preferences.md").write_text(
                "# Preferences\n\n- SYNTHETIC_PREFERENCE_TOKEN\n",
                encoding="utf-8",
            )

            reply = codex_conversation.generate_assistant_reply(
                root=root,
                messages=[create_live_message("user", "俺の好みに合う店は？")],
                run_command=fake_run,
            )

        self.assertEqual("好みに合わせた返答です。", reply)
        prompt = calls[0][1]["input"]
        self.assertIn("AI-LifeOS memory context", prompt)
        self.assertIn("SYNTHETIC_PREFERENCE_TOKEN", prompt)

    def test_generate_assistant_reply_with_context_returns_reference_metadata(self):
        def fake_run(command, **kwargs):
            output_path = Path(command[command.index("--output-last-message") + 1])
            output_path.write_text("参照しました。\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "memory").mkdir()
            (root / "memory" / "long_term.md").write_text("# Long-Term Memory\n\n- ユーザーはAI-LifeOSを作っている。\n", encoding="utf-8")
            (root / "memory" / "preferences.md").write_text(
                "# Preferences\n\n- SYNTHETIC_PREFERENCE_TOKEN\n",
                encoding="utf-8",
            )

            result = codex_conversation.generate_assistant_reply_with_context(
                root=root,
                messages=[create_live_message("user", "俺の好みに合う店は？")],
                run_command=fake_run,
            )

        self.assertEqual("参照しました。", result.reply)
        self.assertIsNotNone(result.memory_context)
        self.assertTrue(result.memory_context.used_memory)
        self.assertTrue(any(reference.path == "memory/preferences.md" for reference in result.memory_context.references))

    def test_finish_session_finalizes_and_processes_new_messages(self):
        calls = []
        progress_events = []

        def fake_run(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "inbox" / "live").mkdir(parents=True)
            (root / "prompts").mkdir()
            (root / "prompts" / "codex_phase2_prompt.md").write_text(
                "Target:\n{RAW_FILE}\n",
                encoding="utf-8",
            )
            session = codex_conversation.create_live_session(root=root)
            messages = [create_live_message("user", "Remember this after exit.")]
            session.write_messages(messages)

            saved, status, result = codex_conversation.finish_session(
                root=root,
                session=session,
                messages=messages,
                has_new_messages=True,
                progress=lambda percent, message: progress_events.append((percent, message)),
                run_command=fake_run,
            )

            self.assertTrue(saved)
            self.assertIsNotNone(result)
            self.assertIn("Updated summary/journal/memory.", status)
            self.assertTrue(result.raw_file.exists())
            self.assertEqual("codex.cmd", calls[0][0])
            self.assertEqual("gpt-5.6-terra", calls[0][calls[0].index("--model") + 1])
            self.assertIn('model_reasoning_effort="medium"', calls[0])
            self.assertEqual("workspace-write", calls[0][calls[0].index("--sandbox") + 1])
            self.assertIn((5, "Saving live log..."), progress_events)
            self.assertIn((70, "Updating summary, journal, and memory..."), progress_events)
            self.assertEqual((100, "Exit processing complete."), progress_events[-1])

    def test_new_options_follow_legacy_injection_parameters(self):
        reply_params = list(inspect.signature(codex_conversation.generate_assistant_reply).parameters)
        streaming_params = list(
            inspect.signature(codex_conversation.generate_assistant_reply_streaming_with_context).parameters
        )
        finish_params = list(inspect.signature(codex_conversation.finish_session).parameters)
        finish_exit_params = list(inspect.signature(codex_conversation.finish_session_for_exit).parameters)

        self.assertLess(reply_params.index("run_command"), reply_params.index("enable_memory_mcp"))
        self.assertLess(streaming_params.index("popen"), streaming_params.index("enable_memory_mcp"))
        self.assertLess(finish_params.index("run_command"), finish_params.index("exclude_from_memory"))
        self.assertLess(finish_exit_params.index("run_command"), finish_exit_params.index("exclude_from_memory"))

    def test_finish_session_skips_finalize_when_disabled(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session = codex_conversation.create_live_session(root=root)
            messages = [create_live_message("user", "Save only.")]

            saved, status, result = codex_conversation.finish_session(
                root=root,
                session=session,
                messages=messages,
                has_new_messages=True,
                finalize_on_exit=False,
            )

            self.assertTrue(saved)
            self.assertIsNone(result)
            self.assertIn("Saved 1 messages", status)
            self.assertTrue(session.path.exists())

    def test_finish_session_excluded_from_memory_keeps_only_live_log(self):
        calls = []

        def fake_run(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session = codex_conversation.create_live_session(root=root)
            messages = [create_live_message("user", "一時的な会話")]

            saved, status, result = codex_conversation.finish_session(
                root=root,
                session=session,
                messages=messages,
                has_new_messages=True,
                exclude_from_memory=True,
                run_command=fake_run,
            )

            self.assertTrue(saved)
            self.assertIsNone(result)
            self.assertIn("temporary live log", status)
            self.assertTrue(session.path.exists())
            self.assertFalse((root / "conversations").exists())
            self.assertEqual([], calls)

    def test_finish_session_for_exit_handles_interrupt_during_finalize(self):
        def interrupted_run(command, **kwargs):
            raise KeyboardInterrupt

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            marker = root / "exit-ok.marker"
            old_marker = os.environ.get("AI_LIFEOS_EXIT_MARKER")
            os.environ["AI_LIFEOS_EXIT_MARKER"] = str(marker)
            (root / "inbox" / "live").mkdir(parents=True)
            (root / "prompts").mkdir()
            (root / "prompts" / "codex_phase2_prompt.md").write_text(
                "Target:\n{RAW_FILE}\n",
                encoding="utf-8",
            )
            session = codex_conversation.create_live_session(root=root)
            messages = [create_live_message("user", "Interrupt during exit.")]

            try:
                saved, status, exit_code = codex_conversation.finish_session_for_exit(
                    root=root,
                    session=session,
                    messages=messages,
                    has_new_messages=True,
                    run_command=interrupted_run,
                )
            finally:
                if old_marker is None:
                    os.environ.pop("AI_LIFEOS_EXIT_MARKER", None)
                else:
                    os.environ["AI_LIFEOS_EXIT_MARKER"] = old_marker

            self.assertTrue(saved)
            self.assertEqual(0, exit_code)
            self.assertIn("interrupted", status)
            self.assertTrue(session.path.exists())
            self.assertTrue(marker.exists())
            records = [json.loads(line) for line in session.path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual("Interrupt during exit.", records[0]["content"])


if __name__ == "__main__":
    unittest.main()
