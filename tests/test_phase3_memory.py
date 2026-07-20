import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import build_answer_context  # noqa: E402
import memory_index  # noqa: E402
import memory_items  # noqa: E402
import search_benchmark  # noqa: E402


class Phase3MemoryTests(unittest.TestCase):
    def make_root(self, temp_dir: str) -> Path:
        root = Path(temp_dir)
        session = root / "conversations" / "2026" / "07" / "2026-07-05_101000"
        session.mkdir(parents=True)
        (session / "summary.md").write_text(
            "\n".join(
                [
                    "# Summary",
                    "",
                    "Date: 2026-07-05",
                    "Session: 検索設計",
                    "",
                    "## 概要",
                    "",
                    "AI-LifeOSの検索設計を決めた。",
                    "",
                    "## タグ",
                    "",
                    "- AI-LifeOS",
                    "- Phase3",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        (session / "raw.md").write_text(
            "# Chat Log\n\nDate: 2026-07-05\n\nラーメンの好みについて話した。\n"
            "『時をかける少女』を見て、時間を戻せるからこそ生まれる選択の重さが印象に残ったと感想を述べた。\n",
            encoding="utf-8",
        )
        journal = root / "journal" / "2026" / "07"
        journal.mkdir(parents=True)
        (journal / "2026-07-05.md").write_text(
            "# 2026-07-05\n\n静かなラーメン店の候補について話した。\n",
            encoding="utf-8",
        )
        memory = root / "memory"
        memory.mkdir()
        (memory / "long_term.md").write_text("# Long-Term Memory\n\n- ユーザーはAI-LifeOSを作っている。\n", encoding="utf-8")
        (memory / "preferences.md").write_text("# Preferences\n\n- ユーザーは静かな店を好む。\n", encoding="utf-8")
        items = memory / "items"
        items.mkdir()
        memory_items.create_memory_item(
            root,
            memory_items.StructuredMemoryItem(
                id="mem_20260705_001",
                category="study_status",
                category_label="学習状況",
                status="active",
                source="conversations/2026/07/2026-07-05_101000/raw.md",
                source_date="2026-07-05",
                confidence="explicit",
                tags=("security", "資格"),
                created_at="2026-07-05T10:10:00+09:00",
                updated_at="2026-07-05T10:10:00+09:00",
                content="- ユーザーは安全確保支援士の学習を始めた。",
            ),
        )
        return root

    def test_collect_documents_extracts_tags_and_memory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)

            documents = memory_index.collect_documents(root)

            paths = {document.document_key for document in documents}
            self.assertIn(str(Path("memory") / "preferences.md"), paths)
            summary = next(document for document in documents if document.path.name == "summary.md")
            self.assertEqual(("AI-LifeOS", "Phase3"), summary.tags)
            self.assertEqual("2026-07-05", summary.date)

    def test_rebuild_index_and_search_memory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)

            db_path = memory_index.rebuild_index(root)
            results = memory_index.search_memory(root=root, query="静かな", use_index=True)

            self.assertTrue(db_path.exists())
            self.assertTrue(any(result.document_type == "memory" for result in results))
            with closing(sqlite3.connect(db_path)) as connection:
                count = connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            self.assertGreaterEqual(count, 4)

    def test_structured_memory_round_trip_and_dynamic_category(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            config = root / "config"
            config.mkdir()
            (config / "memory_categories.example.json").write_text(
                '{"version":1,"categories":[{"name":"study_status","label":"学習状況","description":"学習"}]}',
                encoding="utf-8",
            )

            category = memory_items.add_category(
                root,
                name="health_status",
                label="健康状況",
                description="継続的な健康関連の状況",
                source="conversations/2026/07/example/raw.md",
                created_at="2026-07-05T12:00:00+09:00",
            )
            loaded = memory_items.load_categories(root)
            item = memory_items.read_memory_item(root / "memory" / "items" / "mem_20260705_001.md")

            self.assertEqual("health_status", category.name)
            self.assertIn("health_status", {value.name for value in loaded})
            self.assertEqual("study_status", item.category)
            self.assertEqual(("security", "資格"), item.tags)

    def test_duplicate_category_label_is_rejected_and_suggestion_is_pending(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            config = root / "config"
            config.mkdir()
            (config / "memory_categories.example.json").write_text(
                '{"version":1,"categories":[{"name":"home_status","label":"家の状況","description":"家"}]}',
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                memory_items.add_category(root, "house", "家 の 状況", "duplicate", "source/raw.md")
            suggestion = memory_items.propose_category(
                root,
                "house_candidate",
                "住居候補",
                "既存カテゴリとの境界が不明",
                "conversations/2026/07/example/raw.md",
                created_at="2026-07-05T12:00:00+09:00",
            )

            self.assertIn("Status: pending", suggestion.read_text(encoding="utf-8"))

    def test_index_filters_structured_memory_by_category_status_and_tag(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)

            memory_index.rebuild_index(root)
            results = memory_index.search_memory(
                root=root,
                query="",
                document_types=("memory_item",),
                category="study_status",
                status="active",
                tag="資格",
            )

            self.assertEqual(1, len(results))
            self.assertEqual("study_status", results[0].category)
            with closing(sqlite3.connect(memory_index.default_index_path(root))) as connection:
                row = connection.execute(
                    "SELECT category, status, source, confidence FROM documents WHERE document_type = 'memory_item'"
                ).fetchone()
            self.assertEqual(("study_status", "active", "conversations/2026/07/2026-07-05_101000/raw.md", "explicit"), row)

    def test_structured_filters_match_with_and_without_index(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            memory_index.rebuild_index(root)

            indexed = memory_index.search_memory(
                root=root, query="", category="study_status", status="active", use_index=True
            )
            direct = memory_index.search_memory(
                root=root, query="", category="study_status", status="active", use_index=False
            )

            self.assertEqual(
                {str(result.path.relative_to(root)) for result in indexed},
                {str(result.path.relative_to(root)) for result in direct},
            )

    def test_profile_reports_sql_pushdown_for_metadata_filters(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            memory_items.create_memory_item(
                root,
                memory_items.StructuredMemoryItem(
                    id="mem_20260706_001",
                    category="study_status",
                    category_label="学習状況",
                    status="active",
                    source="conversations/2026/07/other/raw.md",
                    source_date="2026-07-06",
                    confidence="explicit",
                    tags=("security", "資格"),
                    created_at="2026-07-06T10:10:00+09:00",
                    updated_at="2026-07-06T10:10:00+09:00",
                    content="- 別の日付の学習メモ。",
                ),
            )
            memory_index.rebuild_index(root)

            indexed, profile = memory_index.search_memory_with_profile(
                root=root,
                query="",
                document_types=("memory_item",),
                tag="資格",
                category="study_status",
                status="active",
                date="2026-07-05",
                date_from="2026-07-01",
                date_to="2026-07-05",
                path="memory/items/mem_20260705",
            )
            direct, direct_profile = memory_index.search_memory_with_profile(
                root=root,
                query="",
                document_types=("memory_item",),
                tag="資格",
                category="study_status",
                status="active",
                date="2026-07-05",
                date_from="2026-07-01",
                date_to="2026-07-05",
                path="memory/items/mem_20260705",
                use_index=False,
            )

            self.assertEqual(1, len(indexed))
            self.assertEqual(
                {str(result.path.relative_to(root)) for result in indexed},
                {str(result.path.relative_to(root)) for result in direct},
            )
            self.assertEqual("sqlite", profile.source)
            self.assertEqual(1, profile.candidate_count)
            self.assertEqual(
                ("document_type", "tag", "category", "status", "date", "date_from", "date_to", "path"),
                profile.filters,
            )
            self.assertEqual("markdown", direct_profile.source)
            self.assertGreaterEqual(profile.total_ms, profile.ranking_ms)

    def test_synthetic_search_benchmark_never_uses_personal_documents(self):
        result = search_benchmark.run_benchmark(
            document_count=20,
            query="長期検索",
            runs=1,
            compare_japanese=True,
        )

        self.assertEqual(20, result.document_count)
        self.assertEqual(2, result.candidate_count)
        self.assertEqual(2, result.result_count)
        self.assertTrue(result.japanese_comparison)
        baseline = next(
            item for item in result.japanese_comparison if item.method == "python_partial_match_rank"
        )
        self.assertTrue(baseline.available)
        self.assertEqual(2, baseline.matched_documents)

    def test_legacy_index_remains_searchable_before_rebuild(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            db_path = memory_index.default_index_path(root)
            with closing(sqlite3.connect(db_path)) as connection:
                connection.executescript(
                    """
                    CREATE TABLE documents (
                        id INTEGER PRIMARY KEY, document_key TEXT, document_type TEXT, path TEXT,
                        title TEXT, date TEXT, tags_json TEXT, content TEXT, updated_at TEXT
                    );
                    CREATE TABLE tags (document_id INTEGER, tag TEXT);
                    INSERT INTO documents VALUES (
                        1, 'memory/preferences.md', 'memory', 'memory/preferences.md',
                        'Preferences', NULL, '[]', '静かな店を好む', '2026-07-05T00:00:00+09:00'
                    );
                    """
                )

            results = memory_index.search_memory(root=root, query="静かな", use_index=True)

            self.assertEqual(1, len(results))
            self.assertIsNone(results[0].category)

    def test_build_answer_context_uses_private_memory_and_journal(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)

            context = build_answer_context.build_answer_context(
                root=root,
                question="俺のラーメンの好みは？",
                use_index=False,
            )

            self.assertTrue(context.should_use_memory)
            self.assertIn("memory/preferences.md", context.text)
            self.assertIn("静かな店を好む", context.text)
            self.assertIn("Journal Matches", context.text)

    def test_build_answer_context_uses_conversation_matches_for_project_question(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)

            context = build_answer_context.build_answer_context(
                root=root,
                question="AI-LifeOSのPhase3で決めた方針は？",
                use_index=False,
            )

            self.assertTrue(context.should_use_memory)
            self.assertIn("Conversation Matches", context.text)
            self.assertIn("検索設計", context.text)

    def test_build_answer_context_prioritizes_inferred_structured_category(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            config = root / "config"
            config.mkdir()
            source_config = ROOT / "config" / "memory_categories.example.json"
            (config / "memory_categories.example.json").write_text(
                source_config.read_text(encoding="utf-8"), encoding="utf-8"
            )

            context = build_answer_context.build_answer_context(
                root=root,
                question="安全確保支援士の学習状況を教えて",
                use_index=False,
            )

            self.assertTrue(context.should_use_memory)
            self.assertIn("Structured Memory Matches", context.text)
            self.assertIn("Category: study_status", context.text)
            self.assertIn("安全確保支援士の学習を始めた", context.text)

    def test_representative_category_questions_use_the_expected_items(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            config = root / "config"
            config.mkdir()
            (config / "memory_categories.example.json").write_text(
                (ROOT / "config" / "memory_categories.example.json").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            for item in (
                memory_items.StructuredMemoryItem(
                    id="mem_future",
                    category="future_wishlist",
                    category_label="いつかやりたいこと",
                    status="active",
                    source="conversations/future/raw.md",
                    source_date="2026-07-05",
                    confidence="explicit",
                    tags=("long_term",),
                    created_at="2026-07-05T10:00:00+09:00",
                    updated_at="2026-07-05T10:00:00+09:00",
                    content="- 海の見える場所を訪れたい。",
                ),
                memory_items.StructuredMemoryItem(
                    id="mem_home",
                    category="home_status",
                    category_label="家の状況",
                    status="active",
                    source="conversations/home/raw.md",
                    source_date="2026-07-05",
                    confidence="explicit",
                    tags=("home",),
                    created_at="2026-07-05T10:00:00+09:00",
                    updated_at="2026-07-05T10:00:00+09:00",
                    content="- 玄関の整理は未完了。",
                ),
            ):
                memory_items.create_memory_item(root, item)

            cases = (
                ("やりたいことリストを見せて", "future_wishlist", "海の見える場所"),
                ("家の状況を教えて", "home_status", "玄関の整理"),
                ("安全確保支援士の学習状況を教えて", "study_status", "安全確保支援士"),
            )
            for question, category, expected_text in cases:
                with self.subTest(question=question):
                    context = build_answer_context.build_answer_context(root, question, use_index=False)
                    self.assertTrue(context.should_use_memory)
                    self.assertIn(f"Category: {category}", context.text)
                    self.assertIn(expected_text, context.text)

    def test_build_answer_context_uses_capped_core_memory_and_narrow_search_for_general_question(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)

            context = build_answer_context.build_answer_context(
                root=root,
                question="SQLiteとは？",
                use_index=False,
            )

            self.assertTrue(context.should_use_memory)
            self.assertTrue(context.used_memory)
            self.assertEqual(("core", "narrow"), context.retrieval_modes)
            self.assertEqual((), context.results)
            self.assertIn("Priority Memory", context.text)

    def test_general_question_reads_at_most_two_narrow_memory_matches(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            question = "静かなラーメン店について教えて"

            self.assertFalse(build_answer_context.assess_memory_need(question).should_use_memory)
            context = build_answer_context.build_answer_context(
                root=root,
                question=question,
                use_index=False,
            )

            self.assertTrue(context.should_use_memory)
            self.assertEqual(("core", "narrow"), context.retrieval_modes)
            self.assertLessEqual(len(context.results), 2)
            self.assertIn("Narrow Memory Matches", context.text)
            self.assertIn("静かなラーメン店の候補", context.text)
            self.assertNotIn("Journal Matches", context.text)

    def test_narrow_search_caps_multiple_matching_documents_at_two(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            journal_dir = root / "journal" / "2026" / "07"
            for day in (6, 7, 8):
                (journal_dir / f"2026-07-{day:02d}.md").write_text(
                    f"# 2026-07-{day:02d}\n\nNARROW_LIMIT_SENTINEL {day}\n",
                    encoding="utf-8",
                )

            question = "NARROW_LIMIT_SENTINELについて教えて"
            self.assertFalse(build_answer_context.assess_memory_need(question).should_use_memory)
            context = build_answer_context.build_answer_context(root=root, question=question, use_index=False)

            self.assertEqual(("core", "narrow"), context.retrieval_modes)
            self.assertEqual(2, len(context.results))
            self.assertTrue(all(result.document_type == "journal" for result in context.results))

    def test_core_memory_is_capped_while_general_question_uses_narrow_search(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            (root / "memory" / "long_term.md").write_text("# Long-Term\n\n" + "A" * 1400, encoding="utf-8")

            context = build_answer_context.build_answer_context(
                root=root,
                question="SQLiteとは？",
                max_memory_chars=5000,
                use_index=False,
            )

            self.assertTrue(context.should_use_memory)
            self.assertIn("...[truncated]", context.text)
            self.assertNotIn("A" * 1100, context.text)
            self.assertEqual((), context.results)
            self.assertEqual(("core", "narrow"), context.retrieval_modes)

    def test_self_reference_variants_and_follow_up_use_memory_without_writing_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            (root / "memory" / "long_term.md").write_text(
                "# Long-Term Memory\n\n- ユーザーはiPhone 17を使用している。\n",
                encoding="utf-8",
            )
            before = {
                path.relative_to(root).as_posix(): path.read_bytes()
                for path in root.rglob("*")
                if path.is_file()
            }

            cases = (
                ("俺のスマホは？", (), "fallback"),
                ("おれのスマホは？", (), "fallback"),
                ("じゃあスマホは？", ("俺のスマホは？", "じゃあスマホは？"), "fallback"),
                ("前に話した俺の端末は？", (), "fallback"),
            )
            for question, recent_user_messages, expected_mode in cases:
                with self.subTest(question=question):
                    context = build_answer_context.build_answer_context(
                        root=root,
                        question=question,
                        recent_user_messages=recent_user_messages,
                        use_index=False,
                    )
                    self.assertTrue(context.used_memory)
                    self.assertIn("iPhone 17", context.text)
                    self.assertIn(expected_mode, context.retrieval_modes)

            after = {
                path.relative_to(root).as_posix(): path.read_bytes()
                for path in root.rglob("*")
                if path.is_file()
            }
            self.assertEqual(before, after)

    def test_past_fact_lookup_reads_unorganized_live_log_without_modifying_it(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            live_dir = root / "inbox" / "live"
            live_dir.mkdir(parents=True)
            live_file = live_dir / "2026-07-15_101010.jsonl"
            records = [
                {"role": "user", "timestamp": "2026-07-15T10:10:10+09:00", "content": "PAST_FACT_SENTINELを確認した。"},
                {"role": "assistant", "timestamp": "2026-07-15T10:10:11+09:00", "content": "LIVE_ANSWER_SENTINELとして回答した。"},
            ]
            live_file.write_text("\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n", encoding="utf-8")
            before = live_file.read_bytes()

            context = build_answer_context.build_answer_context(
                root=root,
                question="前に PAST_FACT_SENTINEL について何て答えた？",
                use_index=False,
            )

            self.assertIn("Unorganized Live Conversation Evidence", context.text)
            self.assertIn("PAST_FACT_SENTINEL", context.text)
            self.assertIn("LIVE_ANSWER_SENTINEL", context.text)
            self.assertTrue(any(result.document_type == "live_message" for result in context.results))
            self.assertEqual(before, live_file.read_bytes())

    def test_memory_need_scoring_combines_weak_signals(self):
        personal = build_answer_context.assess_memory_need("俺におすすめの本は？")
        generic = build_answer_context.assess_memory_need("おすすめの本は？")

        self.assertTrue(personal.should_use_memory)
        self.assertIn("self-plus-personal-topic", personal.reasons)
        self.assertFalse(generic.should_use_memory)

    def test_build_answer_context_uses_past_personal_impression(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)

            context = build_answer_context.build_answer_context(
                root=root,
                question="俺の時をかける少女の感想ってなんだっけ？",
                use_index=False,
            )

            self.assertTrue(context.should_use_memory)
            self.assertIn("past-conversation", context.reasons)
            self.assertIn("Conversation Matches", context.text)
            self.assertIn("選択の重さが印象に残った", context.text)

    def test_answer_context_includes_matching_raw_message_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session = root / "conversations" / "2026" / "07" / "2026-07-11_173611"
            session.mkdir(parents=True)
            (session / "raw.md").write_text(
                "\n".join(
                    (
                        "# Chat Log",
                        "",
                        "Date: 2026-07-11",
                        "Session: AI-LifeOS movie thoughts",
                        "",
                        "## User",
                        "",
                        "Timestamp: 2026-07-11T17:36:42+09:00",
                        "",
                        "PERSONAL_SENTINEL: memories accumulate instead of disappearing.",
                        "",
                        "## Assistant",
                        "",
                        "Timestamp: 2026-07-11T17:37:03+09:00",
                        "",
                        "That is a thoughtful reading.",
                        "",
                    )
                ),
                encoding="utf-8",
            )
            memory_index.rebuild_index(root)
            with closing(sqlite3.connect(memory_index.default_index_path(root))) as connection:
                connection.execute("DELETE FROM documents WHERE document_type = 'raw_chunk'")
                connection.commit()

            context = build_answer_context.build_answer_context(
                root=root,
                question="AI-LifeOS movie thoughts",
            )

            self.assertTrue(context.should_use_memory)
            self.assertIn("Raw Conversation Evidence", context.text)
            self.assertIn("PERSONAL_SENTINEL", context.text)
            self.assertTrue(any(result.document_type == "raw_chunk" for result in context.results))

    def test_role_aware_evidence_pairs_chatgpt_import_and_live_responses(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cases = (
                (
                    "chatgpt",
                    ("Source: ChatGPT export", "Title: Imported role evidence"),
                    "IMPORTED_USER_SENTINEL",
                    "IMPORTED_ASSISTANT_CONCLUSION",
                ),
                (
                    "live",
                    ("Session: Live role evidence",),
                    "LIVE_USER_SENTINEL",
                    "LIVE_ASSISTANT_CONCLUSION",
                ),
            )
            for index, (name, headers, user_marker, assistant_marker) in enumerate(cases, start=1):
                session = root / "conversations" / "2026" / "07" / f"2026-07-11_12000{index}"
                session.mkdir(parents=True)
                (session / "raw.md").write_text(
                    "\n".join(
                        (
                            "# Chat Log",
                            "",
                            "Date: 2026-07-11",
                            *headers,
                            "",
                            "## User",
                            "",
                            "Timestamp: 2026-07-11T12:00:00+09:00",
                            "",
                            f"AI-LifeOS {user_marker}",
                            "",
                            "## Assistant",
                            "",
                            "Timestamp: 2026-07-11T12:01:00+09:00",
                            "",
                            assistant_marker,
                            "",
                        )
                    ),
                    encoding="utf-8",
                )

            memory_index.rebuild_index(root)
            for name, _, user_marker, assistant_marker in cases:
                with self.subTest(source=name):
                    context = build_answer_context.build_answer_context(
                        root=root,
                        question=f"AI-LifeOS {user_marker}",
                    )

                    raw_chunks = [item for item in context.results if item.document_type == "raw_chunk"]
                    self.assertEqual(["user", "assistant"], [item.speaker_role for item in raw_chunks])
                    self.assertEqual([1, 2], [item.message_number for item in raw_chunks])
                    self.assertIn(user_marker, context.text)
                    self.assertIn(assistant_marker, context.text)
                    self.assertIn("Role: user", context.text)
                    self.assertIn("Role: assistant", context.text)
                    roles = [reference.speaker_role for reference in context.references if reference.document_type == "raw_chunk"]
                    self.assertEqual(["user", "assistant"], roles)
                    expected_title = "Imported role evidence" if name == "chatgpt" else "Live role evidence"
                    self.assertTrue(all(expected_title in item.title for item in raw_chunks))

    def test_role_aware_evidence_keeps_chatgpt_messages_without_timestamps(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session = root / "conversations" / "2026" / "07" / "2026-07-11_121500"
            session.mkdir(parents=True)
            (session / "raw.md").write_text(
                "\n".join(
                    (
                        "# Chat Log",
                        "",
                        "Date: 2026-07-11",
                        "Source: ChatGPT export",
                        "Title: Imported messages without timestamps",
                        "",
                        "## User",
                        "",
                        "AI-LifeOS NO_TIMESTAMP_USER_SENTINEL",
                        "",
                        "## Assistant",
                        "",
                        "NO_TIMESTAMP_ASSISTANT_CONCLUSION",
                        "",
                    )
                ),
                encoding="utf-8",
            )
            memory_index.rebuild_index(root)

            context = build_answer_context.build_answer_context(
                root=root,
                question="AI-LifeOS NO_TIMESTAMP_USER_SENTINEL",
            )

            raw_chunks = [item for item in context.results if item.document_type == "raw_chunk"]
            self.assertEqual(["user", "assistant"], [item.speaker_role for item in raw_chunks])
            self.assertEqual([1, 2], [item.message_number for item in raw_chunks])
            self.assertIn("NO_TIMESTAMP_USER_SENTINEL", context.text)
            self.assertIn("NO_TIMESTAMP_ASSISTANT_CONCLUSION", context.text)
            self.assertNotIn("Timestamp: None", context.text)

    def test_role_aware_evidence_respects_user_and_assistant_queries(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session = root / "conversations" / "2026" / "07" / "2026-07-11_130000"
            session.mkdir(parents=True)
            (session / "raw.md").write_text(
                "\n".join(
                    (
                        "# Chat Log",
                        "",
                        "Date: 2026-07-11",
                        "Session: Role-specific evidence",
                        "",
                        "## User",
                        "",
                        "Timestamp: 2026-07-11T13:00:00+09:00",
                        "",
                        "AI-LifeOS ROLE_USER_SENTINEL",
                        "",
                        "## Assistant",
                        "",
                        "Timestamp: 2026-07-11T13:01:00+09:00",
                        "",
                        "ROLE_ASSISTANT_CONCLUSION",
                        "",
                    )
                ),
                encoding="utf-8",
            )
            memory_index.rebuild_index(root)

            user_context = build_answer_context.build_answer_context(
                root=root,
                question="AI-LifeOS what did I say ROLE_USER_SENTINEL",
            )
            user_chunks = [item for item in user_context.results if item.document_type == "raw_chunk"]
            self.assertEqual(["user"], [item.speaker_role for item in user_chunks])
            self.assertIn("ROLE_USER_SENTINEL", user_context.text)
            self.assertNotIn("ROLE_ASSISTANT_CONCLUSION", user_context.text)

            assistant_context = build_answer_context.build_answer_context(
                root=root,
                question="AI-LifeOS assistant response ROLE_USER_SENTINEL",
            )
            assistant_chunks = [item for item in assistant_context.results if item.document_type == "raw_chunk"]
            self.assertEqual(["user", "assistant"], [item.speaker_role for item in assistant_chunks])
            self.assertIn("ROLE_ASSISTANT_CONCLUSION", assistant_context.text)

    def test_answer_context_records_reference_sources(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)

            context = build_answer_context.build_answer_context(
                root=root,
                question="AI-LifeOSのPhase3で決めた方針は？",
                use_index=False,
            )

            paths = {reference.path for reference in context.references}
            self.assertTrue(context.used_memory)
            self.assertIn("memory/long_term.md", paths)
            self.assertTrue(any(path.endswith("summary.md") for path in paths))


if __name__ == "__main__":
    unittest.main()
