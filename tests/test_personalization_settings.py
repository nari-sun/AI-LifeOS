import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import chat_gui_bridge  # noqa: E402
import build_answer_context  # noqa: E402
import personalization_settings  # noqa: E402
from live_session import create_live_session  # noqa: E402


class PersonalizationSettingsTests(unittest.TestCase):
    def test_defaults_are_read_without_creating_private_settings_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            settings = personalization_settings.load_personalization_settings(root)

            self.assertTrue(settings.memory_enabled)
            self.assertTrue(settings.past_chat_search_enabled)
            self.assertIsNone(settings.project_scope)
            self.assertFalse(personalization_settings.personalization_settings_path(root).exists())

    def test_explicit_update_writes_private_settings_and_normalizes_scope(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            settings = personalization_settings.update_personalization_settings(
                root,
                memory_enabled=False,
                past_chat_search_enabled=True,
                project_scope="  Phase3   改善  ",
            )

            self.assertFalse(settings.memory_enabled)
            self.assertEqual("Phase3 改善", settings.project_scope)
            path = root / "memory" / "personalization_settings.json"
            self.assertTrue(path.exists())
            stored = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(1, stored["version"])
            self.assertEqual("Phase3 改善", stored["project_scope"])

    def test_update_rejects_non_boolean_and_unsafe_scope(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with self.assertRaisesRegex(ValueError, "true または false"):
                personalization_settings.update_personalization_settings(root, memory_enabled="false")
            with self.assertRaisesRegex(ValueError, "改行や制御文字"):
                personalization_settings.update_personalization_settings(root, project_scope="project\nprivate")
            with self.assertRaisesRegex(ValueError, "120文字以内"):
                personalization_settings.update_personalization_settings(root, project_scope="x" * 121)
            with self.assertRaisesRegex(ValueError, "予約語 all"):
                personalization_settings.update_personalization_settings(root, project_scope="ALL")
            self.assertFalse(personalization_settings.personalization_settings_path(root).exists())

    def test_temporary_session_disables_retrieval_and_marks_metadata(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            live_dir = root / "inbox" / "live"
            live_dir.mkdir(parents=True)
            session_file = live_dir / "temporary.jsonl"

            effective = personalization_settings.update_session_personalization(
                root,
                session_file,
                temporary=True,
                memory_enabled=True,
                past_chat_search_enabled=True,
                project_scope="private-project",
            )

            self.assertTrue(effective.temporary)
            self.assertTrue(effective.exclude_from_memory)
            self.assertFalse(effective.memory_enabled)
            self.assertFalse(effective.past_chat_search_enabled)
            self.assertEqual("private-project", effective.project_scope)
            metadata = json.loads(session_file.with_suffix(".session.json").read_text(encoding="utf-8"))
            self.assertTrue(metadata["personalization"]["temporary"])
            self.assertTrue(metadata["personalization"]["exclude_from_memory"])

            session_file.write_text('{"role":"user","timestamp":"2026-07-21T12:00:00+09:00","content":"private"}\n', encoding="utf-8")
            locked = personalization_settings.load_session_personalization(root, session_file)
            self.assertTrue(locked.temporary_locked)
            with self.assertRaisesRegex(ValueError, "一時チャット設定を変更できません"):
                personalization_settings.update_session_personalization(root, session_file, temporary=False)

            after = personalization_settings.load_session_personalization(root, session_file)
            self.assertTrue(after.temporary)
            self.assertTrue(after.exclude_from_memory)
            self.assertFalse(after.memory_enabled)
            self.assertFalse(after.past_chat_search_enabled)

    def test_empty_temporary_session_can_return_to_normal_and_restore_retrieval_settings(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            live_dir = root / "inbox" / "live"
            live_dir.mkdir(parents=True)
            session_file = live_dir / "reversible.jsonl"
            session_file.touch()

            temporary = personalization_settings.update_session_personalization(
                root,
                session_file,
                temporary=True,
                memory_enabled=False,
                past_chat_search_enabled=True,
            )
            stored_temporary = personalization_settings.capture_session_personalization(root, session_file)

            self.assertTrue(temporary.temporary)
            self.assertTrue(temporary.exclude_from_memory)
            self.assertFalse(temporary.memory_enabled)
            self.assertFalse(temporary.past_chat_search_enabled)
            self.assertFalse(temporary.temporary_locked)
            self.assertFalse(stored_temporary["memory_enabled_before_temporary"])
            self.assertTrue(stored_temporary["past_chat_search_enabled_before_temporary"])

            restored = personalization_settings.update_session_personalization(
                root,
                session_file,
                temporary=False,
                # The GUI sends the effective forced-OFF values back when the
                # temporary checkbox is cleared.
                memory_enabled=False,
                past_chat_search_enabled=False,
            )
            stored_restored = personalization_settings.capture_session_personalization(root, session_file)

            self.assertFalse(restored.temporary)
            self.assertFalse(restored.exclude_from_memory)
            self.assertFalse(restored.memory_enabled)
            self.assertTrue(restored.past_chat_search_enabled)
            self.assertFalse(restored.temporary_locked)
            self.assertNotIn("memory_enabled_before_temporary", stored_restored)
            self.assertNotIn("past_chat_search_enabled_before_temporary", stored_restored)

    def test_temporary_cannot_be_enabled_after_first_message(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session = create_live_session(root=root)
            session.append_message("user", "already started")

            effective = personalization_settings.load_session_personalization(root, session.path)
            self.assertTrue(effective.temporary_locked)
            with self.assertRaisesRegex(ValueError, "一時チャット設定を変更できません"):
                personalization_settings.update_session_personalization(root, session.path, temporary=True)

            after = personalization_settings.load_session_personalization(root, session.path)
            self.assertFalse(after.temporary)
            self.assertFalse(after.exclude_from_memory)
            self.assertTrue(after.memory_enabled)
            self.assertTrue(after.past_chat_search_enabled)

    def test_existing_exclusion_is_sticky_when_other_settings_change(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            live_dir = root / "inbox" / "live"
            live_dir.mkdir(parents=True)
            session_file = live_dir / "excluded.jsonl"
            session_file.with_suffix(".session.json").write_text(
                json.dumps(
                    {
                        "status": "saved",
                        "personalization": {
                            "temporary": False,
                            "exclude_from_memory": True,
                            "memory_enabled": False,
                            "past_chat_search_enabled": False,
                            "project_scope": None,
                        },
                    }
                ),
                encoding="utf-8",
            )

            effective = personalization_settings.update_session_personalization(
                root,
                session_file,
                project_scope="LifeOS",
            )

            self.assertFalse(effective.temporary)
            self.assertTrue(effective.exclude_from_memory)
            stored = json.loads(session_file.with_suffix(".session.json").read_text(encoding="utf-8"))
            self.assertTrue(stored["personalization"]["exclude_from_memory"])

    def test_session_file_is_limited_to_inbox_live(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            outside = root / "conversations" / "private.jsonl"
            with self.assertRaisesRegex(ValueError, "inbox/live"):
                personalization_settings.update_session_personalization(root, outside, temporary=True)
            nested = root / "inbox" / "live" / "nested" / "private.jsonl"
            with self.assertRaisesRegex(ValueError, "直下"):
                personalization_settings.update_session_personalization(root, nested, temporary=True)

    def test_session_personalization_can_be_restored_after_metadata_rewrite(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session = create_live_session(root=root)
            session.append_message("user", "test")
            personalization_settings.update_session_personalization(
                root,
                session.path,
                temporary=False,
                project_scope="LifeOS",
            )
            captured = personalization_settings.capture_session_personalization(root, session.path)
            session.path.with_suffix(".session.json").write_text('{"status":"saved"}\n', encoding="utf-8")

            personalization_settings.restore_session_personalization(root, session.path, captured)

            metadata = json.loads(session.path.with_suffix(".session.json").read_text(encoding="utf-8"))
            self.assertEqual("saved", metadata["status"])
            self.assertEqual("LifeOS", metadata["personalization"]["project_scope"])

    def test_memory_summary_reads_only_fixed_sections_and_structured_items(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            memory = root / "memory"
            items = memory / "items"
            items.mkdir(parents=True)
            (memory / "long_term.md").write_text("# Long term\nimportant", encoding="utf-8")
            (memory / "preferences.md").write_text("x" * 12_100, encoding="utf-8")
            (memory / "unlisted-secret.md").write_text("do not include", encoding="utf-8")
            (items / "study.md").write_text("---\ntitle: Study plan\n---\n# Fallback", encoding="utf-8")

            summary = personalization_settings.build_memory_summary(root)

            self.assertTrue(summary["read_only"])
            self.assertEqual(["long_term", "preferences", "projects"], [item["key"] for item in summary["sections"]])
            self.assertEqual("# Long term\nimportant", summary["sections"][0]["content"])
            self.assertTrue(summary["sections"][1]["truncated"])
            self.assertFalse(summary["sections"][2]["exists"])
            self.assertEqual(1, summary["structured_item_count"])
            self.assertEqual("Study plan", summary["structured_items"][0]["label"])
            serialized = json.dumps(summary, ensure_ascii=False)
            self.assertNotIn("unlisted-secret", serialized)
            self.assertNotIn("do not include", serialized)


class PersonalizationBridgeTests(unittest.TestCase):
    def test_implicit_send_session_snapshots_global_defaults_once(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            personalization_settings.update_personalization_settings(
                root,
                memory_enabled=False,
                past_chat_search_enabled=True,
                project_scope="SyntheticScopeA",
            )

            sent = chat_gui_bridge.handle_send_message(
                {"root": str(root), "session_file": None, "content": "synthetic message", "no_ai": True}
            )
            session_file = sent["session"]["jsonl_file"]
            personalization_settings.update_personalization_settings(
                root,
                memory_enabled=True,
                past_chat_search_enabled=False,
                project_scope="SyntheticScopeB",
            )
            effective = personalization_settings.load_session_personalization(root, session_file)

            self.assertFalse(effective.memory_enabled)
            self.assertTrue(effective.past_chat_search_enabled)
            self.assertEqual("SyntheticScopeA", effective.project_scope)
            self.assertTrue(effective.explicitly_configured)

    def test_temporary_update_winning_before_first_append_controls_generation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            started = chat_gui_bridge.handle_start_session({"root": str(root)})
            session_file = started["session"]["jsonl_file"]

            def switch_to_temporary(_attachments):
                personalization_settings.update_session_personalization(
                    root,
                    session_file,
                    temporary=True,
                )
                return []

            reply_result = mock.Mock(
                reply="ok",
                memory_context=None,
                memory_candidates=(),
                memory_opened=(),
            )
            with mock.patch.object(
                chat_gui_bridge,
                "_normalize_attachments",
                side_effect=switch_to_temporary,
            ), mock.patch.object(
                chat_gui_bridge,
                "generate_assistant_reply_with_context",
                return_value=reply_result,
            ) as generate:
                sent = chat_gui_bridge.handle_send_message(
                    {"root": str(root), "session_file": session_file, "content": "synthetic question"}
                )

            options = generate.call_args.kwargs
            self.assertTrue(sent["session"]["personalization"]["temporary"])
            self.assertFalse(options["include_memory_context"])
            self.assertFalse(options["enable_memory_mcp"])
            self.assertFalse(options["include_core_memory"])
            self.assertFalse(options["include_past_chats"])
            self.assertTrue(str(options["exclude_live_session"]).endswith(".jsonl"))

    def test_bridge_keeps_mcp_candidates_and_opened_sources_separate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            started = chat_gui_bridge.handle_start_session({"root": str(root)})
            session_file = started["session"]["jsonl_file"]
            candidate = build_answer_context.MemoryContextReference(
                path="conversations/2099/01/candidate/raw.md",
                document_type="raw_chunk",
                title="Synthetic candidate",
                date="2099-01-01",
                snippet="SYNTHETIC_CANDIDATE_MARKER",
                score=12,
                speaker_role="user",
                message_number=1,
            )
            opened = build_answer_context.MemoryContextReference(
                path="conversations/2099/01/opened/raw.md",
                document_type="raw_chunk",
                title="Synthetic opened",
                date="2099-01-02",
                snippet="SYNTHETIC_OPENED_MARKER",
                score=0,
                speaker_role="user",
                message_number=2,
            )
            reply_result = mock.Mock(
                reply="ok",
                memory_context=None,
                memory_candidates=(candidate,),
                memory_opened=(opened,),
            )

            with mock.patch.object(
                chat_gui_bridge,
                "generate_assistant_reply_with_context",
                return_value=reply_result,
            ):
                sent = chat_gui_bridge.handle_send_message(
                    {"root": str(root), "session_file": session_file, "content": "synthetic question"}
                )

            self.assertEqual([candidate.path], [item["path"] for item in sent["memory_candidates"]])
            self.assertEqual([opened.path], [item["path"] for item in sent["memory_opened"]])

    def test_global_update_does_not_overwrite_current_session_overrides(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            started = chat_gui_bridge.handle_start_session({"root": str(root)})
            session_file = started["session"]["jsonl_file"]
            chat_gui_bridge.handle_update_personalization(
                {
                    "root": str(root),
                    "session_file": session_file,
                    "session": {
                        "temporary": False,
                        "memory_enabled": False,
                        "past_chat_search_enabled": True,
                        "project_scope": "SessionScope",
                    },
                }
            )

            chat_gui_bridge.handle_update_personalization(
                {
                    "root": str(root),
                    "session_file": session_file,
                    "settings": {
                        "memory_enabled": True,
                        "past_chat_search_enabled": False,
                        "project_scope": "GlobalScope",
                    },
                }
            )
            loaded = chat_gui_bridge.handle_get_personalization(
                {"root": str(root), "session_file": session_file}
            )

            self.assertEqual("GlobalScope", loaded["settings"]["project_scope"])
            self.assertFalse(loaded["session"]["memory_enabled"])
            self.assertTrue(loaded["session"]["past_chat_search_enabled"])
            self.assertEqual("SessionScope", loaded["session"]["project_scope"])

    def test_memory_context_serializes_bounded_retrieval_health_and_default(self):
        context = build_answer_context.AnswerContext(
            should_use_memory=True,
            text="",
            results=(),
            retrieval_health=build_answer_context.RetrievalHealth(
                index_status="stale",
                index_reasons=("index is older than source",),
                markdown_fallback_used=True,
                retrieval_depth="deep",
                query_variants=("x" * 300,),
                core_enabled=True,
                past_chats_enabled=False,
                core_reference_count=2,
                structured_memory_hit_count=3,
                past_chat_hit_count=0,
                project_scope="LifeOS",
            ),
        )

        serialized = chat_gui_bridge._serialize_memory_context(context)  # noqa: SLF001
        health = serialized["retrieval_health"]
        default = chat_gui_bridge._serialize_memory_context(None)["retrieval_health"]  # noqa: SLF001

        self.assertEqual("stale", health["index_status"])
        self.assertTrue(health["markdown_fallback_used"])
        self.assertEqual("deep", health["retrieval_depth"])
        self.assertEqual(2, health["core_reference_count"])
        self.assertEqual(3, health["structured_memory_hit_count"])
        self.assertEqual("LifeOS", health["project_scope"])
        self.assertLessEqual(len(health["query_variants"][0]), 174)
        self.assertEqual("disabled", default["index_status"])
        self.assertFalse(default["core_enabled"])
        self.assertFalse(default["past_chats_enabled"])

    def test_bridge_updates_settings_and_session_without_logging_scope_body(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            started = chat_gui_bridge.handle_start_session({"root": str(root)})
            scope = "PRIVATE_PROJECT_SCOPE_VALUE"

            result = chat_gui_bridge.handle_update_personalization(
                {
                    "root": str(root),
                    "session_file": started["session"]["jsonl_file"],
                    "settings": {
                        "memory_enabled": False,
                        "past_chat_search_enabled": True,
                        "project_scope": scope,
                    },
                    "session": {
                        "temporary": True,
                        "memory_enabled": False,
                        "past_chat_search_enabled": False,
                        "project_scope": scope,
                    },
                }
            )
            loaded = chat_gui_bridge.handle_get_personalization(
                {"root": str(root), "session_file": started["session"]["jsonl_file"]}
            )

            self.assertFalse(result["settings"]["memory_enabled"])
            self.assertTrue(loaded["session"]["temporary"])
            self.assertEqual(scope, loaded["session"]["project_scope"])
            payload_summary = chat_gui_bridge._payload_log_summary(  # noqa: SLF001
                {"session_file": started["session"]["jsonl_file"], "settings": {"project_scope": scope}}
            )
            self.assertNotIn(scope, payload_summary)
            log_text = (root / "logs" / "chat_gui_bridge.log").read_text(encoding="utf-8")
            self.assertNotIn(scope, log_text)

    def test_bridge_preserves_session_metadata_and_excludes_temporary_session_from_organization(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            started = chat_gui_bridge.handle_start_session({"root": str(root), "temporary": True})
            session_file = started["session"]["jsonl_file"]

            sent = chat_gui_bridge.handle_send_message(
                {"root": str(root), "session_file": session_file, "content": "private body", "no_ai": True}
            )

            self.assertTrue(sent["session"]["personalization"]["temporary"])
            self.assertEqual("temporary", sent["session"]["organization"]["status"])
            self.assertFalse(sent["session"]["organization"]["can_organize"])
            with self.assertRaisesRegex(ValueError, "一時チャット"):
                chat_gui_bridge.handle_start_finalize_job({"root": str(root), "session_file": session_file})
            self.assertEqual([], chat_gui_bridge._organize_session_targets(root))  # noqa: SLF001

    def test_bridge_can_clear_temporary_before_first_message_and_restore_session_overrides(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            started = chat_gui_bridge.handle_start_session({"root": str(root)})
            session_file = started["session"]["jsonl_file"]

            chat_gui_bridge.handle_update_personalization(
                {
                    "root": str(root),
                    "session_file": session_file,
                    "session": {
                        "temporary": False,
                        "memory_enabled": False,
                        "past_chat_search_enabled": True,
                        "project_scope": "LifeOS",
                    },
                }
            )
            temporary = chat_gui_bridge.handle_update_personalization(
                {
                    "root": str(root),
                    "session_file": session_file,
                    "session": {
                        "temporary": True,
                        "memory_enabled": False,
                        "past_chat_search_enabled": True,
                        "project_scope": "LifeOS",
                    },
                }
            )
            restored = chat_gui_bridge.handle_update_personalization(
                {
                    "root": str(root),
                    "session_file": session_file,
                    "session": {
                        "temporary": False,
                        "memory_enabled": False,
                        "past_chat_search_enabled": False,
                        "project_scope": "LifeOS",
                    },
                }
            )

            self.assertTrue(temporary["session"]["temporary"])
            self.assertTrue(temporary["session"]["exclude_from_memory"])
            self.assertFalse(restored["session"]["temporary"])
            self.assertFalse(restored["session"]["exclude_from_memory"])
            self.assertFalse(restored["session"]["memory_enabled"])
            self.assertTrue(restored["session"]["past_chat_search_enabled"])
            self.assertEqual("LifeOS", restored["session"]["project_scope"])
            self.assertNotEqual("temporary", restored["session_state"]["organization"]["status"])

    def test_bridge_memory_summary_is_read_only(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            memory = root / "memory"
            memory.mkdir()
            path = memory / "projects.md"
            path.write_text("# Project\nstatus", encoding="utf-8")

            result = chat_gui_bridge.handle_get_memory_summary({"root": str(root)})

            self.assertTrue(result["summary"]["read_only"])
            self.assertEqual("# Project\nstatus", result["summary"]["sections"][2]["content"])
            self.assertEqual("# Project\nstatus", path.read_text(encoding="utf-8"))

    def test_bridge_passes_all_memory_toggle_combinations_and_project_scope_to_generation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            started = chat_gui_bridge.handle_start_session({"root": str(root)})
            session_file = started["session"]["jsonl_file"]
            combinations = (
                (False, False, False, False),
                (True, False, True, False),
                (False, True, True, True),
                (True, True, True, True),
            )

            for index, (memory_enabled, past_chat_enabled, context_enabled, mcp_enabled) in enumerate(combinations):
                with self.subTest(memory_enabled=memory_enabled, past_chat_enabled=past_chat_enabled):
                    chat_gui_bridge.handle_update_personalization(
                        {
                            "root": str(root),
                            "session_file": session_file,
                            "session": {
                                "temporary": False,
                                "memory_enabled": memory_enabled,
                                "past_chat_search_enabled": past_chat_enabled,
                                "project_scope": "LifeOS",
                            },
                        }
                    )

                    reply_result = mock.Mock(reply="ok", memory_context=None)
                    with mock.patch.object(
                        chat_gui_bridge,
                        "generate_assistant_reply_with_context",
                        return_value=reply_result,
                    ) as generate:
                        chat_gui_bridge.handle_send_message(
                            {"root": str(root), "session_file": session_file, "content": f"question {index}"}
                        )

                    options = generate.call_args.kwargs
                    self.assertEqual(context_enabled, options["include_memory_context"])
                    self.assertEqual(mcp_enabled, options["enable_memory_mcp"])
                    self.assertEqual(memory_enabled, options["include_core_memory"])
                    self.assertEqual(past_chat_enabled, options["include_past_chats"])
                    self.assertEqual("LifeOS", options["project_scope"])
                    self.assertTrue(str(options["exclude_live_session"]).endswith(".jsonl"))


if __name__ == "__main__":
    unittest.main()
