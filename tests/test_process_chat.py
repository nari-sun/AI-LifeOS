import importlib.util
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "process_chat.py"
SPEC = importlib.util.spec_from_file_location("process_chat", MODULE_PATH)
process_chat = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = process_chat
SPEC.loader.exec_module(process_chat)


class ProcessChatTests(unittest.TestCase):
    def make_root(self, temp_dir: str) -> Path:
        root = Path(temp_dir)
        (root / "inbox").mkdir()
        (root / "prompts").mkdir()
        (root / "prompts" / "codex_phase2_prompt.md").write_text(
            "対象:\n{RAW_FILE}\n",
            encoding="utf-8",
        )
        return root

    def test_process_chat_creates_raw_file_task_and_clears_inbox(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            source = root / "inbox" / "chat.txt"
            source.write_text("# Chat Session\n\nHello from ChatGPT.", encoding="utf-8")

            result = process_chat.process_chat(
                root=root,
                imported_at=datetime(2026, 6, 28, 18, 30, 45),
            )

            raw_file = root / "conversations" / "2026" / "06" / "2026-06-28_183045" / "raw.md"
            task_file = root / "tasks" / "latest_codex_task.md"
            raw_path = str(raw_file.relative_to(root))

            self.assertEqual(raw_file, result.raw_file)
            self.assertEqual(task_file, result.task_file)
            self.assertEqual("", source.read_text(encoding="utf-8"))
            self.assertTrue(raw_file.exists())
            self.assertTrue(task_file.exists())

            raw_text = raw_file.read_text(encoding="utf-8")
            self.assertIn("# Chat Log", raw_text)
            self.assertIn("Date: 2026-06-28", raw_text)
            self.assertIn("Time: 18:30:45", raw_text)
            self.assertIn("Hello from ChatGPT.", raw_text)

            self.assertEqual(f"対象:\n{raw_path}\n", task_file.read_text(encoding="utf-8"))
            self.assertEqual(task_file.read_text(encoding="utf-8"), result.prompt)

    def test_date_option_uses_requested_date_and_keep_inbox_preserves_source(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            source = root / "inbox" / "chat.txt"
            source.write_text("Same conversation.", encoding="utf-8")

            result = process_chat.process_chat(
                root=root,
                imported_at=datetime(2026, 6, 28, 18, 30, 45),
                date_text="2026-06-27",
                keep_inbox=True,
            )

            self.assertEqual(
                root / "conversations" / "2026" / "06" / "2026-06-27_183045" / "raw.md",
                result.raw_file,
            )
            self.assertEqual("Same conversation.", source.read_text(encoding="utf-8"))
            self.assertIn("Date: 2026-06-27", result.raw_file.read_text(encoding="utf-8"))

    def test_empty_inbox_fails_without_creating_conversation_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            (root / "inbox" / "chat.txt").write_text("", encoding="utf-8")

            with self.assertRaises(ValueError):
                process_chat.process_chat(
                    root=root,
                    imported_at=datetime(2026, 6, 28, 18, 30, 45),
                )

            self.assertFalse((root / "conversations").exists())
            self.assertFalse((root / "tasks").exists())

    def test_invalid_date_text_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            (root / "inbox" / "chat.txt").write_text("Hello.", encoding="utf-8")

            with self.assertRaises(ValueError):
                process_chat.process_chat(root=root, date_text="2026/06/28")

    def test_missing_prompt_template_fails_without_clearing_inbox(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "inbox").mkdir()
            source = root / "inbox" / "chat.txt"
            source.write_text("Hello.", encoding="utf-8")

            with self.assertRaises(FileNotFoundError):
                process_chat.process_chat(
                    root=root,
                    imported_at=datetime(2026, 6, 28, 18, 30, 45),
                )

            self.assertEqual("Hello.", source.read_text(encoding="utf-8"))
            self.assertFalse((root / "conversations").exists())
            self.assertFalse((root / "tasks").exists())

    def test_run_codex_task_invokes_codex_exec_with_prompt(self):
        calls = []

        def fake_run(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            result = process_chat.run_codex_task(
                root=root,
                prompt="整理して",
                run_command=fake_run,
            )

        self.assertEqual(0, result.returncode)
        self.assertEqual(
            [
                "codex.cmd",
                "--ask-for-approval",
                "never",
                "exec",
                "-C",
                str(root),
                "--sandbox",
                "workspace-write",
                "-",
            ],
            calls[0][0],
        )
        self.assertEqual("整理して", calls[0][1]["input"])

    def test_prepare_memory_targets_creates_memory_and_journal_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            process_chat.prepare_memory_targets(
                root=root,
                target_at=datetime(2026, 7, 2, 19, 48, 18),
            )

            self.assertEqual(
                "# Long-Term Memory\n\n",
                (root / "memory" / "long_term.md").read_text(encoding="utf-8"),
            )
            self.assertTrue((root / "journal" / "2026" / "07").is_dir())

    def test_commit_changes_stages_expected_paths_and_commits(self):
        calls = []

        def fake_run(command, **kwargs):
            calls.append(command)
            if command == ["git", "diff", "--cached", "--quiet"]:
                return subprocess.CompletedProcess(command, 1)
            return subprocess.CompletedProcess(command, 0)

        with tempfile.TemporaryDirectory() as temp_dir:
            result = process_chat.commit_changes(
                root=Path(temp_dir),
                message="Process chat session 2026-06-28",
                run_command=fake_run,
            )

        self.assertTrue(result.committed)
        self.assertEqual(
            ["git", "add", "--", "conversations", "journal", "memory", "inbox", "tasks"],
            calls[0],
        )
        self.assertEqual([sys.executable, "scripts/privacy_check.py", "--staged"], calls[1])
        self.assertEqual(["git", "diff", "--cached", "--quiet"], calls[2])
        self.assertEqual(["git", "commit", "-m", "Process chat session 2026-06-28"], calls[3])

    def test_commit_changes_skips_commit_when_no_staged_changes(self):
        calls = []

        def fake_run(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0)

        with tempfile.TemporaryDirectory() as temp_dir:
            result = process_chat.commit_changes(
                root=Path(temp_dir),
                message="Process chat session 2026-06-28",
                run_command=fake_run,
            )

        self.assertFalse(result.committed)
        self.assertEqual(
            [
                ["git", "add", "--", "conversations", "journal", "memory", "inbox", "tasks"],
                [sys.executable, "scripts/privacy_check.py", "--staged"],
                ["git", "diff", "--cached", "--quiet"],
            ],
            calls,
        )

    def test_process_chat_session_can_run_codex_and_commit(self):
        calls = []

        def fake_run(command, **kwargs):
            calls.append(command)
            if command == ["git", "diff", "--cached", "--quiet"]:
                return subprocess.CompletedProcess(command, 1)
            return subprocess.CompletedProcess(command, 0)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            (root / "inbox" / "chat.txt").write_text("Hello.", encoding="utf-8")

            result = process_chat.process_chat_session(
                root=root,
                imported_at=datetime(2026, 6, 28, 18, 30, 45),
                run_codex=True,
                commit=True,
                run_command=fake_run,
            )

        self.assertIsNotNone(result.codex)
        self.assertIsNotNone(result.git)
        self.assertTrue(result.git.committed)
        self.assertEqual("codex.cmd", calls[0][0])
        self.assertEqual(["git", "add", "--", "conversations", "journal", "memory", "inbox", "tasks"], calls[1])

    def test_commit_changes_stops_when_privacy_check_fails(self):
        calls = []

        def fake_run(command, **kwargs):
            calls.append(command)
            if command == [sys.executable, "scripts/privacy_check.py", "--staged"]:
                return subprocess.CompletedProcess(command, 1)
            return subprocess.CompletedProcess(command, 0)

        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(RuntimeError):
                process_chat.commit_changes(
                    root=Path(temp_dir),
                    message="Process chat session 2026-06-28",
                    run_command=fake_run,
                )

        self.assertEqual(
            [
                ["git", "add", "--", "conversations", "journal", "memory", "inbox", "tasks"],
                [sys.executable, "scripts/privacy_check.py", "--staged"],
            ],
            calls,
        )

    def test_process_chat_session_does_not_commit_when_codex_fails(self):
        calls = []

        def fake_run(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 1)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            (root / "inbox" / "chat.txt").write_text("Hello.", encoding="utf-8")

            with self.assertRaises(RuntimeError):
                process_chat.process_chat_session(
                    root=root,
                    imported_at=datetime(2026, 6, 28, 18, 30, 45),
                    run_codex=True,
                    commit=True,
                    run_command=fake_run,
                )

        self.assertEqual(1, len(calls))
        self.assertEqual("codex.cmd", calls[0][0])


if __name__ == "__main__":
    unittest.main()
