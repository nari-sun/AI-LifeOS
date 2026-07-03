import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import chat_gui_bridge  # noqa: E402
from live_session import create_live_session  # noqa: E402


class ChatGuiBridgeTests(unittest.TestCase):
    def test_start_session_returns_live_jsonl_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            result = chat_gui_bridge.handle_start_session({"root": str(root)})

            self.assertEqual(result["messages"], [])
            self.assertTrue(result["session"]["jsonl_file"].startswith("inbox"))
            self.assertTrue((root / result["session"]["jsonl_file"]).parent.exists())

    def test_send_message_saves_user_message_without_ai(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            result = chat_gui_bridge.handle_send_message(
                {
                    "root": str(root),
                    "content": "hello",
                    "no_ai": True,
                }
            )

            session_path = root / result["session"]["jsonl_file"]
            records = [json.loads(line) for line in session_path.read_text(encoding="utf-8").splitlines()]

            self.assertEqual(records[0]["role"], "user")
            self.assertEqual(records[0]["content"], "hello")
            self.assertIsNone(result["assistant"])
            self.assertTrue(session_path.with_suffix(".session.json").exists())
            log_text = (root / "logs" / "chat_gui_bridge.log").read_text(encoding="utf-8")
            self.assertIn("send_message.done", log_text)
            self.assertNotIn("hello", log_text)

    def test_send_message_accepts_new_session_path_from_start_session(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            started = chat_gui_bridge.handle_start_session({"root": str(root)})
            session_file = started["session"]["jsonl_file"]

            result = chat_gui_bridge.handle_send_message(
                {
                    "root": str(root),
                    "session_file": session_file,
                    "content": "first message",
                    "no_ai": True,
                }
            )

            session_path = root / result["session"]["jsonl_file"]
            self.assertTrue(session_path.exists())
            records = [json.loads(line) for line in session_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(records[0]["content"], "first message")

    def test_resume_session_returns_messages(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            now = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)
            session = create_live_session(root=root, started_at=now)
            session.append_message("user", "resume me", now)
            session.append_message("assistant", "ok", now + timedelta(seconds=2))

            result = chat_gui_bridge.handle_resume_session(
                {
                    "root": str(root),
                    "session_ref": session.path.stem,
                    "retention_days": 10,
                }
            )

            self.assertEqual(result["session"]["session_id"], session.path.stem)
            self.assertEqual([message["role"] for message in result["messages"]], ["user", "assistant"])


if __name__ == "__main__":
    unittest.main()
