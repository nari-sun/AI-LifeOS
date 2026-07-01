import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "live_session.py"
SPEC = importlib.util.spec_from_file_location("live_session", MODULE_PATH)
live_session = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = live_session
SPEC.loader.exec_module(live_session)


class LiveSessionTests(unittest.TestCase):
    def test_create_live_session_prepares_jsonl_path_without_writing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            started_at = datetime(2026, 7, 1, 22, 30, 0, tzinfo=timezone(timedelta(hours=9)))

            session = live_session.create_live_session(
                root=Path(temp_dir),
                started_at=started_at,
            )

            expected = Path(temp_dir) / "inbox" / "live" / "2026-07-01_223000.jsonl"
            self.assertEqual(expected, session.path)
            self.assertFalse(session.path.exists())

    def test_append_message_writes_jsonl_record(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            started_at = datetime(2026, 7, 1, 22, 30, 0, tzinfo=timezone(timedelta(hours=9)))
            message_at = datetime(2026, 7, 1, 22, 31, 5, tzinfo=timezone(timedelta(hours=9)))
            session = live_session.create_live_session(root=Path(temp_dir), started_at=started_at)

            session.append_message("user", "こんにちは", timestamp=message_at)

            lines = session.path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(1, len(lines))
            self.assertEqual(
                {
                    "role": "user",
                    "timestamp": "2026-07-01T22:31:05+09:00",
                    "content": "こんにちは",
                },
                json.loads(lines[0]),
            )

    def test_write_messages_saves_records_at_once(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            started_at = datetime(2026, 7, 1, 22, 30, 0, tzinfo=timezone(timedelta(hours=9)))
            first_at = datetime(2026, 7, 1, 22, 31, 5, tzinfo=timezone(timedelta(hours=9)))
            second_at = datetime(2026, 7, 1, 22, 32, 10, tzinfo=timezone(timedelta(hours=9)))
            session = live_session.create_live_session(root=Path(temp_dir), started_at=started_at)

            session.write_messages(
                [
                    live_session.create_live_message("user", "こんにちは", timestamp=first_at),
                    live_session.create_live_message("user", "続きです", timestamp=second_at),
                ]
            )

            lines = session.path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(2, len(lines))
            self.assertEqual("こんにちは", json.loads(lines[0])["content"])
            self.assertEqual("続きです", json.loads(lines[1])["content"])

    def test_invalid_role_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            session = live_session.create_live_session(root=Path(temp_dir))

            with self.assertRaises(ValueError):
                session.append_message("system", "not allowed")

    def test_create_live_session_avoids_same_second_collision(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            started_at = datetime(2026, 7, 1, 22, 30, 0, tzinfo=timezone(timedelta(hours=9)))

            first = live_session.create_live_session(root=root, started_at=started_at)
            first.write_messages([])
            second = live_session.create_live_session(root=root, started_at=started_at)

            self.assertEqual(root / "inbox" / "live" / "2026-07-01_223000.jsonl", first.path)
            self.assertEqual(root / "inbox" / "live" / "2026-07-01_223000_01.jsonl", second.path)


if __name__ == "__main__":
    unittest.main()
