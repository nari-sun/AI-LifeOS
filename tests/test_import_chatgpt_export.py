import io
import json
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock


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

    def test_missing_current_node_uses_latest_leaf_without_mixing_branches(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            value = conversation(current_node=None)
            value["mapping"]["old-branch"]["message"]["create_time"] = 1767225700
            value["mapping"]["assistant-node"]["message"]["create_time"] = 1767225800
            self.write_export(root, [value])

            source = import_chatgpt_export.load_export(root)

            self.assertEqual(
                ["First line\n\nSecond line", "Selected answer"],
                [message.content for message in source.conversations[0].messages],
            )
            self.assertNotIn(
                "Unused branch",
                [message.content for message in source.conversations[0].messages],
            )

    def test_invalid_current_node_breaks_leaf_timestamp_ties_by_mapping_order(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            value = conversation(current_node="missing-node")
            tied_time = 1767225800
            value["mapping"]["old-branch"]["message"]["create_time"] = tied_time
            value["mapping"]["assistant-node"]["message"]["create_time"] = tied_time
            self.write_export(root, [value])

            source = import_chatgpt_export.load_export(root)

            self.assertEqual(
                ["First line\n\nSecond line", "Selected answer"],
                [message.content for message in source.conversations[0].messages],
            )
            self.assertNotIn(
                "Unused branch",
                [message.content for message in source.conversations[0].messages],
            )

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
            self.assertEqual(2, metadata["source_message_count"])
            self.assertEqual(0, metadata["skipped_message_count"])
            self.assertEqual(1, metadata["revision"])
            self.assertTrue(metadata["content_fingerprint"].startswith("sha256:"))
            self.assertEqual(1, len(list(output.rglob("raw.md"))))

    def test_updated_export_replaces_current_files_and_keeps_revision_backup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "conversations"
            first_item = import_chatgpt_export._parse_conversation(conversation(), 0)
            first_at = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
            second_at = datetime(2026, 7, 12, 13, 0, tzinfo=timezone.utc)
            first = import_chatgpt_export.import_conversations(
                [first_item], output, "first-export.zip/conversations.json", imported_at=first_at
            )[0]
            raw_file = first.raw_file
            self.assertIsNotNone(raw_file)
            metadata_file = raw_file.parent / "import_metadata.json"
            old_raw = raw_file.read_bytes()
            old_metadata = metadata_file.read_bytes()

            revised_value = conversation()
            revised_value["update_time"] += 600
            revised_value["mapping"]["assistant-node"]["message"]["content"]["parts"] = [
                "Revised answer"
            ]
            revised = import_chatgpt_export._parse_conversation(revised_value, 0)
            self.assertEqual(
                import_chatgpt_export.IMPORT_STATE_UPDATED,
                import_chatgpt_export.classify_import_states([revised], output)["conv-1"],
            )

            result = import_chatgpt_export.import_conversations(
                [revised], output, "second-export.zip/conversations.json", imported_at=second_at
            )[0]

            self.assertTrue(result.updated)
            self.assertFalse(result.duplicate)
            self.assertEqual(raw_file, result.raw_file)
            self.assertIn("Revised answer", raw_file.read_text(encoding="utf-8"))
            backup_dir = raw_file.parent / "import_revisions"
            self.assertEqual(old_raw, (backup_dir / "revision-0001.raw.md").read_bytes())
            self.assertEqual(
                old_metadata,
                (backup_dir / "revision-0001.metadata.json").read_bytes(),
            )
            metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
            self.assertEqual(2, metadata["revision"])
            self.assertEqual("2026-07-11T12:00:00Z", metadata["imported_at"])
            self.assertEqual("2026-07-12T13:00:00Z", metadata["last_imported_at"])
            self.assertEqual("second-export.zip/conversations.json", metadata["source_file"])
            self.assertEqual(
                import_chatgpt_export.IMPORT_STATE_DUPLICATE,
                import_chatgpt_export.classify_import_states([revised], output)["conv-1"],
            )
            duplicate = import_chatgpt_export.import_conversations(
                [revised], output, "second-export.zip/conversations.json", imported_at=second_at
            )[0]
            self.assertTrue(duplicate.duplicate)
            self.assertFalse(duplicate.updated)

    def test_content_fingerprint_detects_change_when_update_time_is_unchanged(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "conversations"
            original = import_chatgpt_export._parse_conversation(conversation(), 0)
            import_chatgpt_export.import_conversations(
                [original], output, "export.zip/conversations.json"
            )
            changed_value = conversation()
            changed_value["mapping"]["assistant-node"]["message"]["content"]["parts"] = [
                "Changed without a new update timestamp"
            ]
            changed = import_chatgpt_export._parse_conversation(changed_value, 0)

            self.assertEqual(
                import_chatgpt_export.IMPORT_STATE_UPDATED,
                import_chatgpt_export.classify_import_states([changed], output)["conv-1"],
            )

    def test_newer_update_time_is_updated_even_when_content_is_unchanged(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "conversations"
            original = import_chatgpt_export._parse_conversation(conversation(), 0)
            import_chatgpt_export.import_conversations(
                [original], output, "first.zip/conversations.json"
            )
            revised_value = conversation()
            revised_value["update_time"] += 1
            revised = import_chatgpt_export._parse_conversation(revised_value, 0)

            self.assertEqual(
                import_chatgpt_export.IMPORT_STATE_UPDATED,
                import_chatgpt_export.classify_import_states([revised], output)["conv-1"],
            )

    def test_failed_metadata_commit_can_retry_using_existing_backup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "conversations"
            original = import_chatgpt_export._parse_conversation(conversation(), 0)
            first = import_chatgpt_export.import_conversations(
                [original], output, "first.zip/conversations.json"
            )[0]
            raw_file = first.raw_file
            self.assertIsNotNone(raw_file)
            metadata_file = raw_file.parent / "import_metadata.json"
            old_metadata = metadata_file.read_bytes()

            revised_value = conversation()
            revised_value["update_time"] += 1
            revised_value["mapping"]["assistant-node"]["message"]["content"]["parts"] = [
                "Retry-safe revision"
            ]
            revised = import_chatgpt_export._parse_conversation(revised_value, 0)
            real_replace = import_chatgpt_export.os.replace
            failed = False

            def fail_current_metadata_once(source, destination):
                nonlocal failed
                if Path(destination) == metadata_file and not failed:
                    failed = True
                    raise OSError("simulated metadata replacement failure")
                return real_replace(source, destination)

            with mock.patch.object(
                import_chatgpt_export.os,
                "replace",
                side_effect=fail_current_metadata_once,
            ):
                with self.assertRaises(OSError):
                    import_chatgpt_export.import_conversations(
                        [revised], output, "second.zip/conversations.json"
                    )

            self.assertEqual(old_metadata, metadata_file.read_bytes())
            self.assertIn("Retry-safe revision", raw_file.read_text(encoding="utf-8"))
            self.assertEqual(
                import_chatgpt_export.IMPORT_STATE_UPDATED,
                import_chatgpt_export.classify_import_states([revised], output)["conv-1"],
            )
            retried = import_chatgpt_export.import_conversations(
                [revised], output, "second.zip/conversations.json"
            )[0]
            self.assertTrue(retried.updated)
            self.assertEqual(2, json.loads(metadata_file.read_text(encoding="utf-8"))["revision"])

    def test_multiple_existing_imports_are_conflict_and_never_auto_updated(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "conversations"
            original = import_chatgpt_export._parse_conversation(conversation(), 0)
            first = import_chatgpt_export.import_conversations(
                [original], output, "first.zip/conversations.json"
            )[0]
            raw_file = first.raw_file
            self.assertIsNotNone(raw_file)
            duplicate_dir = output / "2099" / "01" / "2099-01-01_000000"
            duplicate_dir.mkdir(parents=True)
            (duplicate_dir / "raw.md").write_bytes(raw_file.read_bytes())
            (duplicate_dir / "import_metadata.json").write_bytes(
                (raw_file.parent / "import_metadata.json").read_bytes()
            )
            revised_value = conversation()
            revised_value["update_time"] += 1
            revised = import_chatgpt_export._parse_conversation(revised_value, 0)
            before = raw_file.read_bytes()

            self.assertEqual(
                import_chatgpt_export.IMPORT_STATE_CONFLICT,
                import_chatgpt_export.classify_import_states([revised], output)["conv-1"],
            )
            with self.assertRaisesRegex(ValueError, "import conflict"):
                import_chatgpt_export.import_conversations(
                    [revised], output, "second.zip/conversations.json"
                )
            self.assertEqual(before, raw_file.read_bytes())

    def test_missing_raw_is_conflict_in_preview_and_apply(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "conversations"
            original = import_chatgpt_export._parse_conversation(conversation(), 0)
            first = import_chatgpt_export.import_conversations(
                [original], output, "first.zip/conversations.json"
            )[0]
            raw_file = first.raw_file
            self.assertIsNotNone(raw_file)
            raw_file.unlink()

            self.assertEqual(
                import_chatgpt_export.IMPORT_STATE_CONFLICT,
                import_chatgpt_export.classify_import_states([original], output)["conv-1"],
            )
            with self.assertRaisesRegex(ValueError, "import conflict"):
                import_chatgpt_export.import_conversations(
                    [original], output, "second.zip/conversations.json"
                )
            self.assertFalse(raw_file.exists())

    def test_older_export_never_rolls_back_newer_content(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "conversations"
            newer_value = conversation()
            newer_value["update_time"] += 600
            newer_value["mapping"]["assistant-node"]["message"]["content"]["parts"] = [
                "Newest answer"
            ]
            newer = import_chatgpt_export._parse_conversation(newer_value, 0)
            import_chatgpt_export.import_conversations(
                [newer], output, "newer.zip/conversations.json"
            )

            older_same_value = json.loads(json.dumps(newer_value))
            older_same_value["update_time"] -= 300
            older_same = import_chatgpt_export._parse_conversation(older_same_value, 0)
            self.assertEqual(
                import_chatgpt_export.IMPORT_STATE_DUPLICATE,
                import_chatgpt_export.classify_import_states([older_same], output)["conv-1"],
            )

            older_different = import_chatgpt_export._parse_conversation(conversation(), 0)
            self.assertEqual(
                import_chatgpt_export.IMPORT_STATE_CONFLICT,
                import_chatgpt_export.classify_import_states([older_different], output)["conv-1"],
            )
            with self.assertRaisesRegex(ValueError, "import conflict"):
                import_chatgpt_export.import_conversations(
                    [older_different], output, "older.zip/conversations.json"
                )

    def test_audio_transcription_is_extracted_and_nontext_assets_are_counted(self):
        value = {
            "id": "audio-1",
            "title": "Audio import",
            "create_time": 1767225600,
            "update_time": 1767225660,
            "current_node": "assistant-node",
            "mapping": {
                "root": {"id": "root", "parent": None, "message": None},
                "user-node": {
                    "id": "user-node",
                    "parent": "root",
                    "message": {
                        "author": {"role": "user"},
                        "create_time": 1767225601,
                        "content": {
                            "content_type": "multimodal_text",
                            "parts": [
                                {"content_type": "audio_transcription", "text": "Spoken request"},
                                {"content_type": "image_asset_pointer", "asset_pointer": "hidden"},
                                {"content_type": "audio_transcription", "text": ""},
                                {"content_type": "unknown_structured_part", "text": "do not import"},
                            ],
                        },
                    },
                },
                "assistant-node": {
                    "id": "assistant-node",
                    "parent": "user-node",
                    "message": {
                        "author": {"role": "assistant"},
                        "create_time": 1767225602,
                        "content": {
                            "content_type": "multimodal_text",
                            "parts": [
                                {"content_type": "audio_asset_pointer", "asset_pointer": "hidden"},
                                {
                                    "content_type": "real_time_user_audio_video_asset_pointer",
                                    "asset_pointer": "hidden",
                                },
                            ],
                        },
                    },
                },
            },
        }
        parsed = import_chatgpt_export._parse_conversation(value, 0)

        self.assertEqual(["Spoken request"], [message.content for message in parsed.messages])
        self.assertEqual(2, parsed.source_message_count)
        self.assertEqual(1, parsed.skipped_message_count)
        self.assertEqual(1, parsed.non_text_message_count)
        self.assertEqual(3, parsed.attachment_count)
        self.assertEqual(5, parsed.non_text_part_count)
        self.assertEqual(1, parsed.audio_transcription_count)
        self.assertFalse(parsed.empty_conversation)

        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "conversations"
            result = import_chatgpt_export.import_conversations(
                [parsed], output, "audio-export.zip/conversations.json"
            )[0]
            metadata = json.loads(
                (result.raw_file.parent / "import_metadata.json").read_text(encoding="utf-8")
            )
            self.assertEqual(1, metadata["message_count"])
            self.assertEqual(2, metadata["source_message_count"])
            self.assertEqual(1, metadata["skipped_message_count"])
            self.assertEqual(3, metadata["attachment_count"])
            self.assertEqual(5, metadata["non_text_part_count"])
            self.assertEqual(1, metadata["audio_transcription_count"])
            self.assertFalse(metadata["empty_conversation"])

            preview = io.StringIO()
            states = import_chatgpt_export.classify_import_states([parsed], output)
            with redirect_stdout(preview):
                import_chatgpt_export._print_preview(
                    import_chatgpt_export.ExportSource("audio.zip", (parsed,)),
                    [parsed],
                    states,
                )
            preview_text = preview.getvalue()
            self.assertIn("attachments=3", preview_text)
            self.assertIn("non_text_parts=5", preview_text)
            self.assertIn("audio_transcriptions=1", preview_text)

    def test_hidden_and_unknown_content_types_are_never_extracted(self):
        for content_type in (
            "thoughts",
            "reasoning_recap",
            "user_editable_context",
            "future_internal_payload",
        ):
            with self.subTest(content_type=content_type):
                value = conversation(source_id=f"hidden-{content_type}")
                value["mapping"]["assistant-node"]["message"]["content"] = {
                    "content_type": content_type,
                    "parts": ["INTERNAL_TEXT_MUST_NOT_BE_IMPORTED"],
                }
                parsed = import_chatgpt_export._parse_conversation(value, 0)
                self.assertEqual(
                    ["First line\n\nSecond line"],
                    [message.content for message in parsed.messages],
                )
                self.assertNotIn(
                    "INTERNAL_TEXT_MUST_NOT_BE_IMPORTED",
                    import_chatgpt_export._format_raw(
                        parsed, datetime(2026, 7, 1, tzinfo=timezone.utc)
                    ),
                )
                self.assertEqual(1, parsed.skipped_message_count)
                self.assertEqual(1, parsed.non_text_message_count)
                self.assertEqual(1, parsed.non_text_part_count)

        self.assertEqual(
            "Visible text",
            import_chatgpt_export._message_text(
                {"content_type": "text", "parts": ["Visible text"]}
            ),
        )

    def test_image_only_conversation_is_visible_as_empty(self):
        value = conversation(source_id="empty-image", current_node="user-node")
        value["mapping"]["user-node"]["message"]["content"] = {
            "content_type": "multimodal_text",
            "parts": [{"content_type": "image_asset_pointer", "asset_pointer": "hidden"}],
        }
        parsed = import_chatgpt_export._parse_conversation(value, 0)

        self.assertTrue(parsed.empty_conversation)
        self.assertEqual(1, parsed.source_message_count)
        self.assertEqual(1, parsed.skipped_message_count)
        self.assertEqual(1, parsed.non_text_message_count)
        self.assertEqual(1, parsed.attachment_count)

        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "conversations"
            result = import_chatgpt_export.import_conversations(
                [parsed], output, "image-export.zip/conversations.json"
            )[0]
            metadata = json.loads(
                (result.raw_file.parent / "import_metadata.json").read_text(encoding="utf-8")
            )
            self.assertTrue(metadata["empty_conversation"])
            preview = io.StringIO()
            with redirect_stdout(preview):
                import_chatgpt_export._print_preview(
                    import_chatgpt_export.ExportSource("image.zip", (parsed,)),
                    [parsed],
                    import_chatgpt_export.classify_import_states([parsed], output),
                )
            self.assertIn("empty=true", preview.getvalue())

    def test_legacy_metadata_reimports_recovered_audio_transcription(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "conversations"
            session = output / "2026" / "01" / "2026-01-01_000000"
            session.mkdir(parents=True)
            value = conversation(source_id="legacy-audio", current_node="user-node")
            value["mapping"]["user-node"]["message"]["content"] = {
                "content_type": "multimodal_text",
                "parts": [{"content_type": "audio_transcription", "text": "Recovered speech"}],
            }
            incoming = import_chatgpt_export._parse_conversation(value, 0)
            legacy_empty = import_chatgpt_export.ExportConversation(
                source_id=incoming.source_id,
                title=incoming.title,
                created_at=incoming.created_at,
                updated_at=incoming.updated_at,
                messages=(),
            )
            (session / "raw.md").write_text(
                import_chatgpt_export._format_raw(
                    legacy_empty, datetime(2026, 7, 1, tzinfo=timezone.utc)
                ),
                encoding="utf-8",
            )
            (session / "import_metadata.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "source": "chatgpt_export",
                        "source_conversation_id": incoming.source_id,
                        "source_updated_at": import_chatgpt_export._iso(incoming.updated_at),
                        "message_count": 0,
                        "imported_at": "2026-07-01T00:00:00Z",
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(
                import_chatgpt_export.IMPORT_STATE_UPDATED,
                import_chatgpt_export.classify_import_states([incoming], output)[incoming.source_id],
            )
            result = import_chatgpt_export.import_conversations(
                [incoming], output, "new-export.zip/conversations.json"
            )[0]
            self.assertTrue(result.updated)
            self.assertIn("Recovered speech", (session / "raw.md").read_text(encoding="utf-8"))
            metadata = json.loads((session / "import_metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(1, metadata["audio_transcription_count"])
            self.assertEqual(1, metadata["message_count"])

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
