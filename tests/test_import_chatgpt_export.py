import json
import sys
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import import_chatgpt_export  # noqa: E402


def conversation(
    source_id="conv-1",
    title="Planning notes",
    created=1767225600,
    current_node="assistant-node",
):
    return {
        "id": source_id,
        "title": title,
        "create_time": created,
        "update_time": created + 60,
        "current_node": current_node,
        "mapping": {
            "root": {"id": "root", "parent": None, "message": None},
            "user-node": {
                "id": "user-node",
                "parent": "root",
                "message": {
                    "author": {"role": "user"},
                    "create_time": created + 1,
                    "content": {"content_type": "text", "parts": ["First line", "Second line"]},
                },
            },
            "old-branch": {
                "id": "old-branch",
                "parent": "user-node",
                "message": {
                    "author": {"role": "assistant"},
                    "create_time": created + 2,
                    "content": {"content_type": "text", "parts": ["Unused branch"]},
                },
            },
            "assistant-node": {
                "id": "assistant-node",
                "parent": "user-node",
                "message": {
                    "author": {"role": "assistant"},
                    "create_time": created + 3,
                    "content": {"content_type": "text", "parts": ["Selected answer"]},
                },
            },
        },
    }


class ImportChatGPTExportTests(unittest.TestCase):
    def write_export(self, root: Path, items):
        path = root / "conversations.json"
        path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
        return path

    def test_detects_folder_and_uses_active_branch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            nested = root / "export"
            nested.mkdir()
            self.write_export(nested, [conversation()])

            source = import_chatgpt_export.load_export(root)

            self.assertEqual(1, len(source.conversations))
            self.assertEqual(["First line\n\nSecond line", "Selected answer"], [m.content for m in source.conversations[0].messages])
            self.assertNotIn("Unused branch", [m.content for m in source.conversations[0].messages])

    def test_detects_zip_without_extracting(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            archive_path = root / "chatgpt-export.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("nested/conversations.json", json.dumps([conversation()]))

            source = import_chatgpt_export.load_export(archive_path)

            self.assertEqual("chatgpt-export.zip/nested/conversations.json", source.display_name)
            self.assertEqual("conv-1", source.conversations[0].source_id)
            self.assertEqual([], list(root.glob("nested/*")))

    def test_detects_split_conversation_files_in_folder_in_sequence_order(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            export = Path(temp_dir) / "export"
            export.mkdir()
            (export / "conversations-001.json").write_text(
                json.dumps([conversation("second")]), encoding="utf-8"
            )
            (export / "conversations-000.json").write_text(
                json.dumps([conversation("first")]), encoding="utf-8"
            )

            source = import_chatgpt_export.load_export(export)

            self.assertEqual(["first", "second"], [item.source_id for item in source.conversations])
            self.assertEqual(
                "export/conversations-000.json, conversations-001.json", source.display_name
            )

    def test_detects_split_conversation_files_in_zip_without_extracting(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            archive_path = root / "chatgpt-export.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("nested/conversations-001.json", json.dumps([conversation("second")]))
                archive.writestr("nested/conversations-000.json", json.dumps([conversation("first")]))

            source = import_chatgpt_export.load_export(archive_path)

            self.assertEqual(["first", "second"], [item.source_id for item in source.conversations])
            self.assertEqual(
                "chatgpt-export.zip/nested/conversations-000.json, nested/conversations-001.json",
                source.display_name,
            )
            self.assertEqual([], list(root.glob("nested/*")))

    def test_filters_by_period_title_and_id(self):
        items = [
            import_chatgpt_export._parse_conversation(conversation("a", "Alpha", 1767225600), 0),
            import_chatgpt_export._parse_conversation(conversation("b", "Beta project", 1769904000), 1),
        ]

        selected = import_chatgpt_export.select_conversations(
            items,
            from_date=datetime(2026, 2, 1, tzinfo=timezone.utc).date(),
            to_date=datetime(2026, 2, 1, tzinfo=timezone.utc).date(),
            title_query="PROJECT",
            source_ids=["b"],
        )

        self.assertEqual(["b"], [item.source_id for item in selected])

    def test_import_creates_raw_and_metadata_then_skips_duplicate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "conversations"
            item = import_chatgpt_export._parse_conversation(conversation(), 0)
            now = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)

            first = import_chatgpt_export.import_conversations(
                [item], output, "export.zip/conversations.json", imported_at=now
            )
            second = import_chatgpt_export.import_conversations(
                [item], output, "export.zip/conversations.json", imported_at=now
            )

            self.assertFalse(first[0].duplicate)
            self.assertTrue(second[0].duplicate)
            raw_file = first[0].raw_file
            self.assertIsNotNone(raw_file)
            raw_text = raw_file.read_text(encoding="utf-8")
            self.assertIn("Source: ChatGPT export", raw_text)
            self.assertIn("## User", raw_text)
            self.assertIn("Selected answer", raw_text)
            metadata = json.loads((raw_file.parent / "import_metadata.json").read_text(encoding="utf-8"))
            self.assertEqual("conv-1", metadata["source_conversation_id"])
            self.assertEqual("not_requested", metadata["memory_processing"])
            self.assertEqual(2, metadata["message_count"])
            self.assertEqual(1, len(list(output.rglob("raw.md"))))

    def test_same_second_conversations_get_separate_standard_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "conversations"
            first = import_chatgpt_export._parse_conversation(conversation("one"), 0)
            second = import_chatgpt_export._parse_conversation(conversation("two"), 1)

            results = import_chatgpt_export.import_conversations(
                [first, second], output, "conversations.json"
            )

            paths = [result.raw_file.parent.name for result in results]
            self.assertEqual(["2026-01-01_000000", "2026-01-01_000001"], paths)

    def test_multiline_title_does_not_break_raw_header(self):
        item = import_chatgpt_export._parse_conversation(
            conversation(title="First line\nSecond line"), 0
        )

        raw_text = import_chatgpt_export._format_raw(
            item, datetime(2026, 7, 11, tzinfo=timezone.utc)
        )

        self.assertIn("Title: First line Second line\n", raw_text)

    def test_cli_is_dry_run_by_default(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = self.write_export(root, [conversation()])
            output = root / "output"

            returncode = import_chatgpt_export.main(
                [str(source), "--conversations-dir", str(output)]
            )

            self.assertEqual(0, returncode)
            self.assertFalse(output.exists())

    def test_apply_requires_explicit_selection(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = self.write_export(root, [conversation()])
            with self.assertRaises(SystemExit) as raised:
                import_chatgpt_export.main(
                    [str(source), "--apply", "--conversations-dir", str(root / "output")]
                )
            self.assertEqual(2, raised.exception.code)

    def test_missing_id_gets_stable_content_hash(self):
        value = conversation(source_id=None)
        value.pop("id")

        first = import_chatgpt_export._parse_conversation(value, 0)
        second = import_chatgpt_export._parse_conversation(value, 0)

        self.assertTrue(first.source_id.startswith("sha256:"))
        self.assertEqual(first.source_id, second.source_id)

    def test_blank_id_gets_content_hash(self):
        value = conversation(source_id="   ")

        parsed = import_chatgpt_export._parse_conversation(value, 0)

        self.assertTrue(parsed.source_id.startswith("sha256:"))


if __name__ == "__main__":
    unittest.main()
