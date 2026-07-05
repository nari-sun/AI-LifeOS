import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "session_store.py"
SPEC = importlib.util.spec_from_file_location("session_store", MODULE_PATH)
session_store = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = session_store
SPEC.loader.exec_module(session_store)


class SessionStoreTests(unittest.TestCase):
    def make_live_file(self, root: Path, name: str = "2026-07-01_223000.jsonl") -> Path:
        live_dir = root / "inbox" / "live"
        live_dir.mkdir(parents=True, exist_ok=True)
        path = live_dir / name
        records = [
            {
                "role": "user",
                "timestamp": "2026-07-01T22:30:00+09:00",
                "content": "セッション保存を追加したい",
            },
            {
                "role": "assistant",
                "timestamp": "2026-07-01T22:30:05+09:00",
                "content": "最小構成で保存します。",
            },
        ]
        path.write_text(
            "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
            encoding="utf-8",
        )
        return path

    def test_save_session_writes_metadata_for_latest_jsonl(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            live_file = self.make_live_file(root)
            saved_at = datetime(2026, 7, 1, 22, 31, 0, tzinfo=timezone(timedelta(hours=9)))

            result = session_store.save_session(root=root, saved_at=saved_at)

            metadata_file = live_file.with_suffix(".session.json")
            self.assertEqual(metadata_file, result.metadata_file)
            self.assertTrue(metadata_file.exists())

            metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
            self.assertEqual(1, metadata["version"])
            self.assertEqual("2026-07-01_223000", metadata["session_id"])
            self.assertEqual("saved", metadata["status"])
            self.assertEqual("セッション保存を追加したい", metadata["title"])
            self.assertEqual("inbox/live/2026-07-01_223000.jsonl", metadata["jsonl_file"])
            self.assertEqual(2, metadata["message_count"])
            self.assertEqual("2026-07-01T22:30:00+09:00", metadata["started_at"])
            self.assertEqual("2026-07-01T22:30:05+09:00", metadata["updated_at"])
            self.assertEqual("2026-07-01T22:31:00+09:00", metadata["saved_at"])
            self.assertIsNone(metadata["finalized_message_count"])
            self.assertFalse(metadata["organize"]["raw_created"])

    def test_save_session_uses_title_override(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.make_live_file(root)

            result = session_store.save_session(root=root, title=" Phase2.65 session save ")

            self.assertEqual("Phase2.65 session save", result.title)

    def test_save_session_rejects_empty_jsonl(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            live_dir = root / "inbox" / "live"
            live_dir.mkdir(parents=True)
            path = live_dir / "2026-07-01_223000.jsonl"
            path.write_text("", encoding="utf-8")

            with self.assertRaises(ValueError):
                session_store.save_session(root=root, session_file=path)

    def test_list_saved_sessions_returns_newest_first(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = self.make_live_file(root, "2026-07-01_223000.jsonl")
            second = self.make_live_file(root, "2026-07-01_224000.jsonl")

            session_store.save_session(
                root=root,
                session_file=first,
                saved_at=datetime(2026, 7, 1, 22, 31, 0, tzinfo=timezone(timedelta(hours=9))),
            )
            session_store.save_session(
                root=root,
                session_file=second,
                saved_at=datetime(2026, 7, 1, 22, 41, 0, tzinfo=timezone(timedelta(hours=9))),
                title="newer",
            )

            sessions = session_store.list_saved_sessions(root=root)

            self.assertEqual(["2026-07-01_224000", "2026-07-01_223000"], [s.session_id for s in sessions])
            self.assertEqual("newer", sessions[0].title)

    def test_list_resumable_sessions_keeps_last_user_within_retention(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.make_live_file(root, "recent.jsonl")
            old = self.make_live_file(root, "old.jsonl")
            old.write_text(
                json.dumps(
                    {
                        "role": "user",
                        "timestamp": "2026-06-20T22:30:00+09:00",
                        "content": "古い入力",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            now = datetime(2026, 7, 1, 22, 30, 0, tzinfo=timezone(timedelta(hours=9)))

            sessions = session_store.list_resumable_sessions(root=root, retention_days=10, now=now)

            self.assertEqual(["recent"], [session.session_id for session in sessions])

    def test_load_resume_session_loads_latest_resumable_messages(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.make_live_file(root, "2026-07-01_223000.jsonl")
            now = datetime(2026, 7, 1, 22, 30, 0, tzinfo=timezone(timedelta(hours=9)))

            summary, records = session_store.load_resume_session(root=root, session_ref="latest", now=now)

            self.assertEqual("2026-07-01_223000", summary.session_id)
            self.assertEqual(2, len(records))
            self.assertEqual("セッション保存を追加したい", records[0]["content"])

    def test_prune_expired_sessions_does_not_delete_unorganized_sessions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            live_file = self.make_live_file(root, "old.jsonl")
            metadata_file = live_file.with_suffix(".session.json")
            metadata_file.write_text("{}", encoding="utf-8")
            live_file.write_text(
                json.dumps(
                    {
                        "role": "user",
                        "timestamp": "2026-06-20T22:30:00+09:00",
                        "content": "古い入力",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            now = datetime(2026, 7, 1, 22, 30, 0, tzinfo=timezone(timedelta(hours=9)))

            dry_run_targets = session_store.prune_expired_sessions(root=root, retention_days=10, now=now)
            self.assertEqual([live_file, metadata_file], dry_run_targets)
            self.assertTrue(live_file.exists())
            self.assertTrue(metadata_file.exists())

            deleted_targets = session_store.prune_expired_sessions(
                root=root,
                retention_days=10,
                now=now,
                delete=True,
                auto_finalize=False,
            )

            self.assertEqual([], deleted_targets)
            self.assertTrue(live_file.exists())
            self.assertTrue(metadata_file.exists())

    def test_cleanup_expired_sessions_deletes_only_organized_artifacts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            live_file = self.make_live_file(root, "old.jsonl")
            live_file.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "role": "user",
                                "timestamp": "2026-06-20T22:30:00+09:00",
                                "content": "古い入力",
                            },
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            {
                                "role": "assistant",
                                "timestamp": "2026-06-20T22:30:05+09:00",
                                "content": "古い返答",
                            },
                            ensure_ascii=False,
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            raw_file = root / "conversations" / "2026" / "06" / "2026-06-20_223000" / "raw.md"
            raw_file.parent.mkdir(parents=True)
            raw_file.write_text("raw", encoding="utf-8")
            updated_at = "2026-06-20T22:30:05+09:00"
            session_store.save_session(
                root=root,
                session_file=live_file,
                status="finalized",
                organize_update={
                    "raw_created": True,
                    "memory_processed": True,
                    "index_updated": True,
                    "raw_file": "conversations/2026/06/2026-06-20_223000/raw.md",
                    "raw_message_count": 2,
                    "raw_updated_at": updated_at,
                    "processed_message_count": 2,
                    "processed_updated_at": updated_at,
                },
            )
            metadata_file = live_file.with_suffix(".session.json")
            now = datetime(2026, 7, 1, 22, 30, 0, tzinfo=timezone(timedelta(hours=9)))

            results = session_store.cleanup_expired_sessions(root=root, retention_days=10, now=now)

            self.assertEqual(1, len(results))
            self.assertEqual("削除済み", results[0].status)
            self.assertEqual((live_file, metadata_file, raw_file), results[0].deleted_paths)
            self.assertFalse(live_file.exists())
            self.assertFalse(metadata_file.exists())
            self.assertFalse(raw_file.exists())

    def test_cleanup_expired_sessions_keeps_failed_sessions_for_manual_resume(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            live_file = self.make_live_file(root, "old.jsonl")
            live_file.write_text(
                json.dumps(
                    {
                        "role": "user",
                        "timestamp": "2026-06-20T22:30:00+09:00",
                        "content": "古い入力",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            session_store.save_session(
                root=root,
                session_file=live_file,
                status="memory_failed",
                organize_update={
                    "raw_created": True,
                    "memory_processed": False,
                    "index_updated": False,
                    "failed_stage": "memory",
                    "last_error": "RuntimeError: failed",
                    "raw_message_count": 1,
                    "raw_updated_at": "2026-06-20T22:30:00+09:00",
                },
            )
            metadata_file = live_file.with_suffix(".session.json")
            now = datetime(2026, 7, 1, 22, 30, 0, tzinfo=timezone(timedelta(hours=9)))

            results = session_store.cleanup_expired_sessions(root=root, retention_days=10, now=now)

            self.assertEqual(1, len(results))
            self.assertEqual("整理失敗", results[0].status)
            self.assertEqual((), results[0].deleted_paths)
            self.assertTrue(live_file.exists())
            self.assertTrue(metadata_file.exists())

    def test_session_organization_switches_to_unorganized_when_messages_are_added(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            live_file = self.make_live_file(root)
            updated_at = "2026-07-01T22:30:05+09:00"
            session_store.save_session(
                root=root,
                session_file=live_file,
                status="finalized",
                organize_update={
                    "raw_created": True,
                    "memory_processed": True,
                    "index_updated": True,
                    "raw_message_count": 2,
                    "raw_updated_at": updated_at,
                    "processed_message_count": 2,
                    "processed_updated_at": updated_at,
                },
            )

            organized = session_store.get_session_organization(root=root, session_file=live_file)
            self.assertTrue(organized["is_organized"])
            self.assertFalse(organized["can_organize"])

            with live_file.open("a", encoding="utf-8", newline="\n") as file:
                file.write(
                    json.dumps(
                        {
                            "role": "user",
                            "timestamp": "2026-07-01T22:31:00+09:00",
                            "content": "追加の会話",
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
            session_store.save_session(root=root, session_file=live_file, status="saved")

            unorganized = session_store.get_session_organization(root=root, session_file=live_file)
            self.assertEqual("unorganized_new", unorganized["status"])
            self.assertTrue(unorganized["can_organize"])
            self.assertEqual(2, unorganized["organized_message_count"])


if __name__ == "__main__":
    unittest.main()
