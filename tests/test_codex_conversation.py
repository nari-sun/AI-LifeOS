import json
import io
import os
import queue
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
SCRIPT = ROOT / "scripts" / "codex_conversation.py"

import codex_conversation  # noqa: E402
from live_session import create_live_message  # noqa: E402


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
    def __init__(self, complete_status="completed"):
        self.requests = []
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
                    "--resume-days",
                    "9999",
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

    def test_parser_defaults_to_no_fast_mode_or_service_tier(self):
        args = codex_conversation.build_parser().parse_args([])

        self.assertIsNone(args.chat_codex_service_tier)
        self.assertFalse(args.chat_codex_fast_mode)

    def test_build_codex_chat_prompt_requires_grounded_memory_recall(self):
        memory_context = """AI-LifeOS memory context (read-only).

## Conversation Matches
- Date: 2026-07-11
  Source: conversations/2026/07/example/summary.md
  Snippet: ユーザーは千昭の計画性のなさを指摘した。"""

        prompt = codex_conversation.build_codex_chat_prompt(
            [create_live_message("user", "前に話した感想を覚えてる？")],
            memory_context=memory_context,
        )

        self.assertIn("Memory-grounding rules:", prompt)
        self.assertIn("state only claims supported by that context", prompt)
        self.assertIn("Do not fill gaps with general knowledge", prompt)
        self.assertIn("Do not reverse, soften, or strengthen a stored claim", prompt)
        self.assertIn("say that the stored records do not confirm it", prompt)
        self.assertIn("ユーザーは千昭の計画性のなさを指摘した", prompt)

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
        self.assertIn("--sandbox", command)
        self.assertEqual("read-only", command[command.index("--sandbox") + 1])
        self.assertIn("User:\nHello", calls[0][1]["input"])

    def test_streaming_app_server_emits_only_agent_delta_and_returns_completed_text(self):
        process = FakeAppServerProcess()
        deltas = []

        result = codex_conversation.generate_assistant_reply_streaming_with_context(
            root=ROOT,
            messages=[create_live_message("user", "Hello")],
            on_delta=deltas.append,
            include_memory_context=False,
            popen=lambda *args, **kwargs: process,
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
            (root / "memory" / "preferences.md").write_text("# Preferences\n\n- ユーザーは静かな店を好む。\n", encoding="utf-8")

            reply = codex_conversation.generate_assistant_reply(
                root=root,
                messages=[create_live_message("user", "俺の好みに合う店は？")],
                run_command=fake_run,
            )

        self.assertEqual("好みに合わせた返答です。", reply)
        prompt = calls[0][1]["input"]
        self.assertIn("AI-LifeOS memory context", prompt)
        self.assertIn("静かな店を好む", prompt)

    def test_generate_assistant_reply_with_context_returns_reference_metadata(self):
        def fake_run(command, **kwargs):
            output_path = Path(command[command.index("--output-last-message") + 1])
            output_path.write_text("参照しました。\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "memory").mkdir()
            (root / "memory" / "long_term.md").write_text("# Long-Term Memory\n\n- ユーザーはAI-LifeOSを作っている。\n", encoding="utf-8")
            (root / "memory" / "preferences.md").write_text("# Preferences\n\n- ユーザーは静かな店を好む。\n", encoding="utf-8")

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
