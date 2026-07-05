import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import finalize_live_chat  # noqa: E402


class FinalizeLiveChatTests(unittest.TestCase):
    def make_root(self, temp_dir: str) -> Path:
        root = Path(temp_dir)
        (root / "inbox" / "live").mkdir(parents=True)
        (root / "prompts").mkdir()
        (root / "prompts" / "codex_phase2_prompt.md").write_text(
            "Target:\n{RAW_FILE}\n",
            encoding="utf-8",
        )
        return root

    def make_live_file(self, root: Path) -> Path:
        path = root / "inbox" / "live" / "2026-07-01_223000.jsonl"
        records = [
            {
                "role": "user",
                "timestamp": "2026-07-01T22:30:00+09:00",
                "content": "Hello Phase2.6.",
            },
            {
                "role": "assistant",
                "timestamp": "2026-07-01T22:30:05+09:00",
                "content": "Phase2.6 reply.",
            },
        ]
        path.write_text(
            "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
            encoding="utf-8",
        )
        return path

    def test_finalize_live_chat_creates_raw_task_and_metadata(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            live_file = self.make_live_file(root)

            result = finalize_live_chat.finalize_live_chat(root=root, session_file=live_file)

            raw_file = root / "conversations" / "2026" / "07" / "2026-07-01_223000" / "raw.md"
            task_file = root / "tasks" / "latest_codex_task.md"
            metadata_file = live_file.with_suffix(".session.json")

            self.assertEqual(raw_file, result.raw_file)
            self.assertEqual(task_file, result.task_file)
            self.assertTrue(raw_file.exists())
            self.assertTrue(task_file.exists())
            self.assertTrue(metadata_file.exists())

            raw_text = raw_file.read_text(encoding="utf-8")
            self.assertIn("Source: AI-LifeOS live session", raw_text)
            self.assertIn("## User", raw_text)
            self.assertIn("Hello Phase2.6.", raw_text)
            self.assertIn("## Assistant", raw_text)
            self.assertIn("Phase2.6 reply.", raw_text)

            self.assertEqual(
                "Target:\nconversations\\2026\\07\\2026-07-01_223000\\raw.md\n",
                task_file.read_text(encoding="utf-8"),
            )
            self.assertEqual("finalized", json.loads(metadata_file.read_text(encoding="utf-8"))["status"])

    def test_finalize_live_chat_refuses_to_overwrite_raw_without_force(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            live_file = self.make_live_file(root)
            raw_file = root / "conversations" / "2026" / "07" / "2026-07-01_223000" / "raw.md"
            raw_file.parent.mkdir(parents=True)
            raw_file.write_text("existing", encoding="utf-8")

            with self.assertRaises(FileExistsError):
                finalize_live_chat.finalize_live_chat(root=root, session_file=live_file)

    def test_finalize_live_chat_can_run_codex_and_commit(self):
        calls = []

        def fake_run(command, **kwargs):
            calls.append((command, kwargs))
            if command == ["git", "diff", "--cached", "--quiet"]:
                return subprocess.CompletedProcess(command, 1)
            return subprocess.CompletedProcess(command, 0)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            live_file = self.make_live_file(root)

            result = finalize_live_chat.finalize_live_chat(
                root=root,
                session_file=live_file,
                run_codex=True,
                commit=True,
                write_session_metadata=False,
                run_command=fake_run,
            )

            self.assertIsNotNone(result.codex)
            self.assertIsNotNone(result.git)
            self.assertEqual("codex.cmd", calls[0][0][0])
            self.assertEqual("gpt-5.5", calls[0][0][calls[0][0].index("--model") + 1])
            self.assertIn('model_reasoning_effort="xhigh"', calls[0][0])
            self.assertTrue(calls[0][1]["capture_output"])
            self.assertTrue((root / "memory" / "long_term.md").exists())
            self.assertTrue((root / "memory" / "preferences.md").exists())
            self.assertTrue((root / "memory" / "projects.md").exists())
            self.assertTrue((root / "journal" / "2026" / "07").is_dir())
            self.assertEqual(["git", "add", "--", "conversations", "journal", "memory", "inbox", "tasks"], calls[1][0])
            self.assertEqual([sys.executable, "scripts/privacy_check.py", "--staged"], calls[2][0])

    def test_session_datetime_falls_back_to_first_message_timestamp(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            live_file = root / "inbox" / "live" / "custom.jsonl"
            timestamp = datetime(2026, 7, 2, 8, 15, 30, tzinfo=timezone(timedelta(hours=9)))
            live_file.write_text(
                json.dumps(
                    {
                        "role": "user",
                        "timestamp": timestamp.isoformat(timespec="seconds"),
                        "content": "Custom session.",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            result = finalize_live_chat.finalize_live_chat(root=root, session_file=live_file)

            self.assertEqual(
                root / "conversations" / "2026" / "07" / "2026-07-02_081530" / "raw.md",
                result.raw_file,
            )


if __name__ == "__main__":
    unittest.main()
