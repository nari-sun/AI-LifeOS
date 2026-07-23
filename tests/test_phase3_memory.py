import json
import os
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
        session = root / "conversations" / "2099" / "06" / "2099-06-15_101000"
        session.mkdir(parents=True)
        (session / "summary.md").write_text(
            "\n".join(
                [
                    "# Summary",
                    "",
                    "Date: 2099-06-15",
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
            "# Chat Log\n\nDate: 2099-06-15\n\nラーメンの好みについて話した。\n"
            "架空作品『星舟クロニクル』の青い羅針盤が印象に残ったという合成感想を述べた。\n",
            encoding="utf-8",
        )
        journal = root / "journal" / "2099" / "06"
        journal.mkdir(parents=True)
        (journal / "2099-06-15.md").write_text(
            "# 2099-06-15\n\n静かなラーメン店の候補について話した。\n",
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
                id="mem_20990615_001",
                category="study_status",
                category_label="学習状況",
                status="active",
                source="conversations/2099/06/2099-06-15_101000/raw.md",
                source_date="2099-06-15",
                confidence="explicit",
                tags=("security", "資格"),
                created_at="2099-06-15T10:10:00+09:00",
                updated_at="2099-06-15T10:10:00+09:00",
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
            self.assertEqual("2099-06-15", summary.date)

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

    def test_legacy_positional_use_index_argument_remains_compatible(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)

            results = memory_index.search_memory(
                root,
                "静かな",
                None,
                10,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                False,
            )

            self.assertTrue(results)
            self.assertTrue(any(result.document_type == "memory" for result in results))

    def test_speaker_role_is_filtered_before_ranking_in_markdown_and_sqlite(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            raw = root / "conversations" / "2099" / "06" / "2099-06-15_101000" / "raw.md"
            raw.write_text(
                "# Chat Log\n\nDate: 2099-06-15\n\n## Assistant\n\nneedle needle needle\n\n"
                "## User\n\nneedle user evidence\n",
                encoding="utf-8",
            )

            direct, direct_profile = memory_index.search_memory_with_profile(
                root=root,
                query="needle",
                document_types=("raw_chunk",),
                limit=1,
                use_index=False,
                speaker_role="user",
            )
            memory_index.rebuild_index(root)
            indexed, indexed_profile = memory_index.search_memory_with_profile(
                root=root,
                query="needle",
                document_types=("raw_chunk",),
                limit=1,
                use_index=True,
                speaker_role="user",
            )

            self.assertEqual(["user"], [result.speaker_role for result in direct])
            self.assertEqual(["user"], [result.speaker_role for result in indexed])
            self.assertIn("speaker_role", direct_profile.filters)
            self.assertIn("speaker_role", indexed_profile.filters)

    def test_scope_uses_raw_header_or_current_message_not_another_message(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            raw = root / "conversations" / "2099" / "06" / "2099-06-15_101000" / "raw.md"
            raw.write_text(
                "# Chat Log\n\nDate: 2099-06-15\n\n## User\n\nSession: Project Alpha only\n\n"
                "## User\n\nneedle from an unrelated message\n",
                encoding="utf-8",
            )

            for use_index in (False, True):
                if use_index:
                    memory_index.rebuild_index(root)
                results = memory_index.search_memory(
                    root=root,
                    query="needle",
                    document_types=("raw", "raw_chunk"),
                    scope="Project Alpha",
                    use_index=use_index,
                )
                self.assertEqual([], results)

            raw.write_text(
                "# Chat Log\n\nProject Scope: Project Alpha\n\n---\n\n"
                "## User\n\nneedle belongs to the scoped session\n",
                encoding="utf-8",
            )
            for use_index in (False, True):
                if use_index:
                    memory_index.rebuild_index(root)
                results = memory_index.search_memory(
                    root=root,
                    query="needle",
                    document_types=("raw_chunk",),
                    scope="Project Alpha",
                    use_index=use_index,
                )
                self.assertEqual(["user"], [result.speaker_role for result in results])

            raw.write_text(
                "# Chat Log\n\nProject Scope: Project Alpha Secret\n\n---\n\n"
                "## User\n\nneedle must not cross a prefix-related scope\n",
                encoding="utf-8",
            )
            for use_index in (False, True):
                if use_index:
                    memory_index.rebuild_index(root)
                results = memory_index.search_memory(
                    root=root,
                    query="needle",
                    document_types=("raw", "raw_chunk"),
                    scope="Project Alpha",
                    use_index=use_index,
                )
                self.assertEqual([], results)

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
                created_at="2099-06-15T12:00:00+09:00",
            )
            loaded = memory_items.load_categories(root)
            item = memory_items.read_memory_item(root / "memory" / "items" / "mem_20990615_001.md")

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
                created_at="2099-06-15T12:00:00+09:00",
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
            self.assertEqual(("study_status", "active", "conversations/2099/06/2099-06-15_101000/raw.md", "explicit"), row)

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
                date="2099-06-15",
                date_from="2099-06-01",
                date_to="2099-06-15",
                path="memory/items/mem_20990615",
            )
            direct, direct_profile = memory_index.search_memory_with_profile(
                root=root,
                query="",
                document_types=("memory_item",),
                tag="資格",
                category="study_status",
                status="active",
                date="2099-06-15",
                date_from="2099-06-01",
                date_to="2099-06-15",
                path="memory/items/mem_20990615",
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
                        'Preferences', NULL, '[]', '静かな店を好む', '2099-06-15T00:00:00+09:00'
                    );
                    """
                )

            results, profile = memory_index.search_memory_with_profile(
                root=root, query="静かな", use_index=True
            )

            self.assertTrue(any(result.document_type == "memory" for result in results))
            self.assertEqual("stale", profile.index_status)
            self.assertEqual("sqlite+markdown-fallback", profile.source)

    def test_legacy_index_detects_same_path_content_update(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            db_path = memory_index.rebuild_index(root)
            with closing(sqlite3.connect(db_path)) as connection:
                connection.execute("DROP TABLE indexed_sources")
                connection.commit()

            preferences = root / "memory" / "preferences.md"
            preferences.write_text(
                preferences.read_text(encoding="utf-8") + "\n- LEGACY_CHANGED_CONTENT\n",
                encoding="utf-8",
            )
            database_mtime = db_path.stat().st_mtime_ns
            os.utime(preferences, ns=(database_mtime + 1_000_000, database_mtime + 1_000_000))

            health = memory_index.inspect_index_health(root)
            results = memory_index.search_memory(root=root, query="LEGACY_CHANGED_CONTENT")

            self.assertEqual("stale", health.status)
            self.assertIn("changed-source", health.reasons)
            self.assertTrue(results)

    def test_legacy_index_never_uses_message_body_metadata_for_project_scope(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session = root / "conversations" / "2099" / "04" / "2099-04-02_101500"
            session.mkdir(parents=True)
            (session / "raw.md").write_text(
                "\n".join(
                    (
                        "# Chat Log",
                        "",
                        "Date: 2099-04-02",
                        "",
                        "## User",
                        "",
                        "Session: Project Alpha",
                        "This scope label belongs only to message one.",
                        "",
                        "## User",
                        "",
                        "LEGACY_SCOPE_LEAK_SENTINEL belongs to an unrelated message.",
                        "",
                    )
                ),
                encoding="utf-8",
            )
            db_path = memory_index.rebuild_index(root)
            with closing(sqlite3.connect(db_path)) as connection:
                # Reproduce metadata produced by the old full-raw parser: the
                # Session field in message 1 became the title of message 2.
                updated = connection.execute(
                    "UPDATE documents SET title = ? WHERE document_type = 'raw_chunk' "
                    "AND document_key LIKE '%#message-002-%'",
                    ("Project Alpha / User message 2",),
                )
                self.assertEqual(1, updated.rowcount)
                connection.execute("DROP TABLE indexed_sources")
                connection.commit()
            index_before = db_path.read_bytes()

            health = memory_index.inspect_index_health(root)
            results, profile = memory_index.search_memory_with_profile(
                root=root,
                query="LEGACY_SCOPE_LEAK_SENTINEL",
                document_types=("raw_chunk",),
                scope="Project Alpha",
                use_index=True,
            )

            self.assertEqual("legacy", health.status)
            self.assertTrue(health.needs_markdown_fallback)
            self.assertEqual([], results)
            self.assertEqual("legacy", profile.index_status)
            self.assertEqual("sqlite+markdown-fallback", profile.source)
            self.assertEqual(index_before, db_path.read_bytes())

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
                    source_date="2099-06-15",
                    confidence="explicit",
                    tags=("long_term",),
                    created_at="2099-06-15T10:00:00+09:00",
                    updated_at="2099-06-15T10:00:00+09:00",
                    content="- 海の見える場所を訪れたい。",
                ),
                memory_items.StructuredMemoryItem(
                    id="mem_home",
                    category="home_status",
                    category_label="家の状況",
                    status="active",
                    source="conversations/home/raw.md",
                    source_date="2099-06-15",
                    confidence="explicit",
                    tags=("home",),
                    created_at="2099-06-15T10:00:00+09:00",
                    updated_at="2099-06-15T10:00:00+09:00",
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

            self.assertTrue(build_answer_context.assess_memory_need(question).should_use_memory)
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
            journal_dir = root / "journal" / "2099" / "06"
            for day in (16, 17, 18):
                (journal_dir / f"2099-06-{day:02d}.md").write_text(
                    f"# 2099-06-{day:02d}\n\nNARROW_LIMIT_SENTINEL {day}\n",
                    encoding="utf-8",
                )

            question = "NARROW_LIMIT_SENTINELについて教えて"
            self.assertTrue(build_answer_context.assess_memory_need(question).should_use_memory)
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

    def test_temporary_and_excluded_live_logs_never_become_past_chat_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            live_dir = root / "inbox" / "live"
            live_dir.mkdir(parents=True)

            for session_name, personalization in (
                (
                    "temporary",
                    {"temporary": True, "exclude_from_memory": True},
                ),
                (
                    "excluded",
                    {"temporary": False, "exclude_from_memory": True},
                ),
            ):
                live_file = live_dir / f"{session_name}.jsonl"
                records = [
                    {
                        "role": "user",
                        "timestamp": "2026-07-15T10:10:10+09:00",
                        "content": f"PRIVATE_{session_name.upper()}_SENTINEL",
                    }
                ]
                live_file.write_text(
                    "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
                    encoding="utf-8",
                )
                live_file.with_suffix(".session.json").write_text(
                    json.dumps(
                        {"personalization": personalization, "organize": {"index_updated": False}},
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )

            context = build_answer_context.build_answer_context(
                root=root,
                question="前に PRIVATE_TEMPORARY_SENTINEL について何て言った？",
                use_index=False,
            )

            self.assertNotIn("PRIVATE_TEMPORARY_SENTINEL", context.text)
            self.assertNotIn("PRIVATE_EXCLUDED_SENTINEL", context.text)
            self.assertFalse(any(result.document_type == "live_message" for result in context.results))

    def test_live_project_scope_uses_sidecar_or_current_message_not_a_sibling(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            live_dir = root / "inbox" / "live"
            live_dir.mkdir(parents=True)
            live_file = live_dir / "2026-07-15_121212.jsonl"
            records = [
                {
                    "role": "user",
                    "timestamp": "2026-07-15T12:12:12+09:00",
                    "content": "Project Alpha belongs only to this message.",
                },
                {
                    "role": "assistant",
                    "timestamp": "2026-07-15T12:12:13+09:00",
                    "content": "LIVE_SCOPE_LEAK_SENTINEL",
                },
            ]
            live_file.write_text(
                "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
                encoding="utf-8",
            )

            unassigned = build_answer_context.build_answer_context(
                root=root,
                question="前に LIVE_SCOPE_LEAK_SENTINEL について何て答えた？",
                use_index=False,
                include_core_memory=False,
                project_scope="Project Alpha",
            )
            self.assertNotIn("LIVE_SCOPE_LEAK_SENTINEL", unassigned.text)

            live_file.with_suffix(".session.json").write_text(
                json.dumps(
                    {
                        "personalization": {
                            "temporary": False,
                            "exclude_from_memory": False,
                            "project_scope": "Project Alpha",
                        }
                    }
                ),
                encoding="utf-8",
            )
            assigned = build_answer_context.build_answer_context(
                root=root,
                question="前に LIVE_SCOPE_LEAK_SENTINEL について何て答えた？",
                use_index=False,
                include_core_memory=False,
                project_scope="Project Alpha",
            )
            self.assertIn("LIVE_SCOPE_LEAK_SENTINEL", assigned.text)

    def test_memory_need_scoring_combines_weak_signals(self):
        personal = build_answer_context.assess_memory_need("俺におすすめの本は？")
        generic = build_answer_context.assess_memory_need("おすすめの本は？")

        self.assertTrue(personal.should_use_memory)
        self.assertIn("self-plus-personal-topic", personal.reasons)
        self.assertTrue(generic.should_use_memory)
        self.assertLess(generic.score, build_answer_context.MEMORY_SCORE_THRESHOLD)

    def test_build_answer_context_uses_past_personal_impression(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)

            context = build_answer_context.build_answer_context(
                root=root,
                question="俺の架空作品『星舟クロニクル』の感想ってなんだっけ？",
                use_index=False,
            )

            self.assertTrue(context.should_use_memory)
            self.assertIn("past-conversation", context.reasons)
            self.assertIn("Conversation Matches", context.text)
            self.assertIn("青い羅針盤が印象に残った", context.text)

    def test_answer_context_includes_matching_raw_message_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session = root / "conversations" / "2099" / "03" / "2099-03-14_091526"
            session.mkdir(parents=True)
            (session / "raw.md").write_text(
                "\n".join(
                    (
                        "# Chat Log",
                        "",
                        "Date: 2099-03-14",
                        "Session: AI-LifeOS movie thoughts",
                        "",
                        "## User",
                        "",
                        "Timestamp: 2099-03-14T09:15:26+09:00",
                        "",
                        "PERSONAL_SENTINEL: memories accumulate instead of disappearing.",
                        "",
                        "## Assistant",
                        "",
                        "Timestamp: 2099-03-14T09:16:03+09:00",
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

    def test_query_variants_remove_request_wording_without_private_topic_dictionary(self):
        variants = memory_index.expand_query_variants(
            "俺の架空作品『星舟クロニクル』の感想教えて"
        )

        self.assertTrue(variants)
        self.assertTrue(all("教えて" not in variant for variant in variants))
        self.assertIn("星舟クロニクル", variants)
        self.assertFalse(any("リオナ" in variant or "ベルク" in variant for variant in variants))

    def test_local_semantic_backend_can_join_rank_fusion_without_external_dependency(self):
        class StubLocalBackend:
            def rank(self, query, documents, limit):
                del query, limit
                return [
                    document.document_key
                    for document in documents
                    if document.document_type == "summary"
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            results, profile = memory_index.search_memory_with_profile(
                root=root,
                query="NO_LEXICAL_MATCH",
                document_types=("summary",),
                use_index=False,
                semantic_backend=StubLocalBackend(),
            )

            self.assertEqual(1, len(results))
            self.assertEqual("summary", results[0].document_type)
            self.assertEqual("hybrid-local", profile.retrieval_mode)

    def test_stale_index_fallback_recovers_titleless_user_impression(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = memory_index.rebuild_index(root)
            index_before = db_path.read_bytes()

            correct_session = root / "conversations" / "2099" / "05" / "2099-05-17_120000"
            correct_session.mkdir(parents=True)
            (correct_session / "raw.md").write_text(
                "\n".join(
                    (
                        "# Chat Log",
                        "",
                        "Date: 2099-05-17",
                        "",
                        "## User",
                        "",
                        "リオナが青い羅針盤をベルクへ渡し忘れる、という合成場面が面白いと思った。",
                        "公開テスト専用の合成感想。SYNTHETIC_CORRECT_IMPRESSION_SENTINEL",
                        "",
                        "## Assistant",
                        "",
                        "公開テスト専用の合成応答。",
                        "",
                    )
                ),
                encoding="utf-8",
            )

            failed_session = root / "conversations" / "2099" / "05" / "2099-05-18_083000"
            failed_session.mkdir(parents=True)
            (failed_session / "raw.md").write_text(
                "\n".join(
                    (
                        "# Chat Log",
                        "",
                        "Date: 2099-05-18",
                        "Session: synthetic retrieval miss",
                        "",
                        "## User",
                        "",
                        "俺の架空作品『星舟クロニクル』のリオナについての感想教えて",
                        "",
                        "## Assistant",
                        "",
                        "具体的な感想は確認できない。WRONG_RETRIEVAL_SENTINEL",
                        "",
                    )
                ),
                encoding="utf-8",
            )

            health = memory_index.inspect_index_health(root)
            context = build_answer_context.build_answer_context(
                root=root,
                question="俺の架空作品『星舟クロニクル』のリオナについての感想教えて",
                use_index=True,
            )

            self.assertEqual("stale", health.status)
            self.assertEqual("stale", context.retrieval_health.index_status)
            self.assertTrue(context.retrieval_health.markdown_fallback_used)
            self.assertGreater(context.retrieval_health.past_chat_hit_count, 0)
            self.assertIn("SYNTHETIC_CORRECT_IMPRESSION_SENTINEL", context.text)
            self.assertNotIn("WRONG_RETRIEVAL_SENTINEL", context.text)
            raw_chunks = [item for item in context.results if item.document_type == "raw_chunk"]
            self.assertEqual(["user"], [item.speaker_role for item in raw_chunks])
            self.assertTrue(all(item.path == correct_session / "raw.md" for item in raw_chunks))
            self.assertEqual(index_before, db_path.read_bytes())

    def test_narrow_retrieval_reads_imported_user_raw_from_stale_markdown(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = memory_index.rebuild_index(root)
            index_before = db_path.read_bytes()
            session = root / "conversations" / "2099" / "05" / "2099-05-19_120000"
            session.mkdir(parents=True)
            long_tail = "静かな余韻が残った。" * 120
            (session / "raw.md").write_text(
                "\n".join(
                    (
                        "# Chat Log",
                        "",
                        "Date: 2099-05-19",
                        "Source: ChatGPT export",
                        "Title: Imported movie conversation",
                        "",
                        "## User",
                        "",
                        "星舟クロニクルは青い羅針盤の場面が好きだった。NARROW_IMPORTED_USER_EVIDENCE",
                        long_tail,
                        "",
                        "## Assistant",
                        "",
                        "NARROW_IMPORTED_ASSISTANT_MUST_NOT_APPEAR",
                        "",
                    )
                ),
                encoding="utf-8",
            )

            context = build_answer_context.build_answer_context(
                root=root,
                question="星舟クロニクルってどうだった？",
            )

            raw_chunks = [item for item in context.results if item.document_type == "raw_chunk"]
            self.assertEqual("narrow", context.retrieval_health.retrieval_depth)
            self.assertEqual("stale", context.retrieval_health.index_status)
            self.assertTrue(context.retrieval_health.markdown_fallback_used)
            self.assertEqual(["user"], [item.speaker_role for item in raw_chunks])
            self.assertEqual(1, len(raw_chunks))
            self.assertLessEqual(
                len(raw_chunks[0].snippet),
                build_answer_context.NARROW_RAW_SNIPPET_CHAR_LIMIT + 3,
            )
            self.assertIn("NARROW_IMPORTED_USER_EVIDENCE", context.text)
            self.assertNotIn("NARROW_IMPORTED_ASSISTANT_MUST_NOT_APPEAR", context.text)
            self.assertEqual(index_before, db_path.read_bytes())

    def test_narrow_raw_retrieval_preserves_scope_and_failed_request_exclusions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            def write_raw(name: str, scope: str, user_text: str, assistant_text: str) -> Path:
                session = root / "conversations" / "2099" / "05" / name
                session.mkdir(parents=True)
                path = session / "raw.md"
                path.write_text(
                    "\n".join(
                        (
                            "# Chat Log",
                            "",
                            "Date: 2099-05-20",
                            f"Project Scope: {scope}",
                            "",
                            "## User",
                            "",
                            user_text,
                            "",
                            "## Assistant",
                            "",
                            assistant_text,
                            "",
                        )
                    ),
                    encoding="utf-8",
                )
                return path

            expected_path = write_raw(
                "2099-05-20_090000",
                "Alpha",
                "NARROW_SCOPE_TOPIC GOOD_SCOPE_USER_EVIDENCE",
                "通常の応答。",
            )
            write_raw(
                "2099-05-20_100000",
                "Beta",
                "NARROW_SCOPE_TOPIC WRONG_SCOPE_MUST_NOT_APPEAR",
                "通常の応答。",
            )
            write_raw(
                "2099-05-20_110000",
                "Alpha",
                "NARROW_SCOPE_TOPIC FAILED_REQUEST_MUST_NOT_APPEAR",
                "具体的な感想は確認できない。",
            )

            context = build_answer_context.build_answer_context(
                root=root,
                question="NARROW_SCOPE_TOPICってどうだった？",
                project_scope="Alpha",
            )

            raw_chunks = [item for item in context.results if item.document_type == "raw_chunk"]
            self.assertEqual(1, len(raw_chunks))
            self.assertEqual(expected_path, raw_chunks[0].path)
            self.assertEqual("user", raw_chunks[0].speaker_role)
            self.assertIn("GOOD_SCOPE_USER_EVIDENCE", context.text)
            self.assertNotIn("WRONG_SCOPE_MUST_NOT_APPEAR", context.text)
            self.assertNotIn("FAILED_REQUEST_MUST_NOT_APPEAR", context.text)

    def test_core_and_past_chat_controls_are_independent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)

            core_only = build_answer_context.build_answer_context(
                root=root,
                question="俺のラーメンの好みは？",
                use_index=False,
                include_past_chats=False,
            )
            past_only = build_answer_context.build_answer_context(
                root=root,
                question="AI-LifeOSのPhase3で決めた方針は？",
                use_index=False,
                include_core_memory=False,
            )

            self.assertIn("Priority Memory", core_only.text)
            self.assertNotIn("Journal Matches", core_only.text)
            self.assertNotIn("Conversation Matches", core_only.text)
            self.assertNotIn("Priority Memory", past_only.text)
            self.assertNotIn("Structured Memory Matches", past_only.text)
            self.assertIn("Conversation Matches", past_only.text)
            self.assertTrue(core_only.retrieval_health.core_enabled)
            self.assertFalse(core_only.retrieval_health.past_chats_enabled)
            self.assertFalse(past_only.retrieval_health.core_enabled)
            self.assertTrue(past_only.retrieval_health.past_chats_enabled)

    def test_project_scope_never_broadens_to_an_unrelated_project(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)

            matching = build_answer_context.build_answer_context(
                root=root,
                question="Phase3で決めた方針は？",
                use_index=False,
                include_core_memory=False,
                project_scope="検索設計",
            )
            unrelated = build_answer_context.build_answer_context(
                root=root,
                question="Phase3で決めた方針は？",
                use_index=False,
                include_core_memory=False,
                project_scope="別プロジェクト",
            )

            self.assertIn("検索設計", matching.text)
            self.assertFalse(unrelated.used_memory)
            self.assertEqual("別プロジェクト", unrelated.retrieval_health.project_scope)

    def test_project_scope_does_not_expose_unrelated_core_memory_sections(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            (root / "memory" / "long_term.md").write_text(
                "\n".join(
                    (
                        "# Long-Term Memory",
                        "",
                        "- GLOBAL_PRIVATE_SENTINEL",
                        "",
                        "## Project Alpha",
                        "",
                        "- ALPHA_SCOPED_SENTINEL",
                        "",
                        "## Project Beta",
                        "",
                        "- BETA_PRIVATE_SENTINEL",
                        "",
                    )
                ),
                encoding="utf-8",
            )

            context = build_answer_context.build_answer_context(
                root=root,
                question="Project Alpha の進捗は？",
                use_index=False,
                include_past_chats=False,
                project_scope="Project Alpha",
            )

            self.assertIn("ALPHA_SCOPED_SENTINEL", context.text)
            self.assertNotIn("GLOBAL_PRIVATE_SENTINEL", context.text)
            self.assertNotIn("BETA_PRIVATE_SENTINEL", context.text)

    def test_projects_memory_is_available_when_core_is_on_and_past_chats_are_off(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            (root / "memory" / "projects.md").write_text(
                "# Projects\n\n- PROJECTS_CORE_SENTINEL\n",
                encoding="utf-8",
            )

            context = build_answer_context.build_answer_context(
                root=root,
                question="プロジェクトの状況は？",
                use_index=False,
                include_core_memory=True,
                include_past_chats=False,
            )

            self.assertIn("PROJECTS_CORE_SENTINEL", context.text)
            self.assertTrue(any(reference.path == "memory/projects.md" for reference in context.references))

    def test_reserved_all_scope_is_not_treated_as_unscoped(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            memory = root / "memory"
            memory.mkdir(parents=True)
            (memory / "long_term.md").write_text(
                "# Long-Term Memory\n\n- PRIVATE_WITHOUT_SCOPE_SENTINEL\n",
                encoding="utf-8",
            )

            context = build_answer_context.build_answer_context(
                root=root,
                question="覚えている？",
                use_index=False,
                include_past_chats=False,
                project_scope="all",
            )

            self.assertNotIn("PRIVATE_WITHOUT_SCOPE_SENTINEL", context.text)
            self.assertEqual("all", context.retrieval_health.project_scope)

    def test_project_scope_checks_full_document_outside_the_ranked_snippet(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session = root / "conversations" / "2026" / "07" / "2026-07-12_120000"
            session.mkdir(parents=True)
            (session / "raw.md").write_text(
                "\n".join(
                    (
                        "# Chat Log",
                        "",
                        "Date: 2026-07-12",
                        "Session: Neutral title",
                        "ProjectHiddenScope",
                        "X" * 600,
                        "",
                        "## User",
                        "",
                        "FULL_DOCUMENT_SCOPE_QUERY の方針を決めた。",
                        "",
                    )
                ),
                encoding="utf-8",
            )

            memory_index.rebuild_index(root)
            for use_index in (False, True):
                with self.subTest(use_index=use_index):
                    context = build_answer_context.build_answer_context(
                        root=root,
                        question="前に FULL_DOCUMENT_SCOPE_QUERY で決めた方針は？",
                        use_index=use_index,
                        include_core_memory=False,
                        project_scope="ProjectHiddenScope",
                    )

                    self.assertTrue(context.used_memory)
                    self.assertIn("FULL_DOCUMENT_SCOPE_QUERY", context.text)
                    self.assertTrue(
                        any(result.document_type == "raw_chunk" for result in context.results)
                    )

    def test_manifest_index_without_current_parser_version_falls_back_without_scope_leak(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            raw = root / "conversations" / "2099" / "08" / "2099-08-09_101112" / "raw.md"
            raw.parent.mkdir(parents=True)
            raw.write_text(
                "# Chat Log\n\n## User\n\nSession: Project Alpha only in message one\n\n"
                "## User\n\nLEGACY_SCOPE_LEAK_TOKEN from an unrelated message\n",
                encoding="utf-8",
            )
            db_path = memory_index.rebuild_index(root)
            with closing(sqlite3.connect(db_path)) as connection:
                connection.execute(
                    "UPDATE documents SET title = ? WHERE document_type = 'raw_chunk' AND message_number = 2",
                    ("Project Alpha unsafe legacy title",),
                )
                connection.execute(
                    "UPDATE index_metadata SET value = '0' WHERE key = 'raw_metadata_parser_version'"
                )
                connection.commit()
            before = db_path.read_bytes()

            health = memory_index.inspect_index_health(root)
            results, profile = memory_index.search_memory_with_profile(
                root=root,
                query="LEGACY_SCOPE_LEAK_TOKEN",
                document_types=("raw_chunk",),
                scope="Project Alpha",
                use_index=True,
            )

            self.assertEqual("legacy", health.status)
            self.assertIn("parser-version-mismatch", health.reasons)
            self.assertTrue(health.needs_markdown_fallback)
            self.assertEqual("sqlite+markdown-fallback", profile.source)
            self.assertEqual([], results)
            self.assertEqual(before, db_path.read_bytes())

    def test_failed_reply_excludes_only_its_request_and_keeps_earlier_user_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            raw = root / "conversations" / "2099" / "09" / "2099-09-10_111213" / "raw.md"
            raw.parent.mkdir(parents=True)
            raw.write_text(
                "\n".join(
                    (
                        "# Chat Log",
                        "",
                        "## User",
                        "",
                        "SYNTHETIC_TOPIC_TOKEN は余韻の設計が面白いと感じた。SAFE_PRIMARY_EVIDENCE",
                        "",
                        "## Assistant",
                        "",
                        "その感想を受け取った。",
                        "",
                        "## User",
                        "",
                        "俺の SYNTHETIC_TOPIC_TOKEN の感想を教えて",
                        "",
                        "## Assistant",
                        "",
                        "具体的な感想は確認できない。FAILED_REPLY_TOKEN",
                        "",
                    )
                ),
                encoding="utf-8",
            )

            context = build_answer_context.build_answer_context(
                root=root,
                question="俺の SYNTHETIC_TOPIC_TOKEN の感想を教えて",
                use_index=False,
                include_core_memory=False,
            )

            raw_chunks = [item for item in context.results if item.document_type == "raw_chunk"]
            self.assertEqual([1], [item.message_number for item in raw_chunks])
            self.assertEqual(["user"], [item.speaker_role for item in raw_chunks])
            self.assertIn("SAFE_PRIMARY_EVIDENCE", context.text)
            self.assertNotIn("FAILED_REPLY_TOKEN", context.text)

    def test_failed_live_reply_excludes_only_its_request_and_keeps_earlier_user_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            live = root / "inbox" / "live" / "2099-10-11_121314.jsonl"
            live.parent.mkdir(parents=True)
            records = (
                {"role": "user", "timestamp": "2099-10-11T12:13:14+09:00", "content": "LIVE_SAFE_TOPIC_TOKEN is my earlier view. LIVE_SAFE_PRIMARY"},
                {"role": "assistant", "timestamp": "2099-10-11T12:13:15+09:00", "content": "Acknowledged."},
                {"role": "user", "timestamp": "2099-10-11T12:13:16+09:00", "content": "前の LIVE_SAFE_TOPIC_TOKEN を教えて"},
                {"role": "assistant", "timestamp": "2099-10-11T12:13:17+09:00", "content": "具体的な感想は確認できない。LIVE_FAILED_REPLY"},
            )
            live.write_text(
                "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
                encoding="utf-8",
            )

            context = build_answer_context.build_answer_context(
                root=root,
                question="前に俺が LIVE_SAFE_TOPIC_TOKEN について何と言った？",
                use_index=False,
                include_core_memory=False,
            )

            live_results = [item for item in context.results if item.document_type == "live_message"]
            self.assertEqual([1], [item.message_number for item in live_results])
            self.assertIn("LIVE_SAFE_PRIMARY", context.text)
            self.assertNotIn("LIVE_FAILED_REPLY", context.text)

    def test_role_pairing_never_crosses_an_intervening_user_message(self):
        anchor = memory_index.MemorySearchResult(
            document_type="raw_chunk",
            path=Path("synthetic/raw.md"),
            title="Synthetic / User message 1",
            date="2099-11-12",
            tags=(),
            snippet="ANCHOR_USER",
            score=10,
            speaker_role="user",
            message_number=1,
        )
        related = [
            memory_index.MemorySearchResult(
                document_type="raw_chunk",
                path=anchor.path,
                title="Synthetic / User message 2",
                date=anchor.date,
                tags=(),
                snippet="INTERVENING_USER",
                score=0,
                speaker_role="user",
                message_number=2,
            ),
            memory_index.MemorySearchResult(
                document_type="raw_chunk",
                path=anchor.path,
                title="Synthetic / Assistant message 3",
                date=anchor.date,
                tags=(),
                snippet="UNRELATED_ASSISTANT",
                score=0,
                speaker_role="assistant",
                message_number=3,
            ),
        ]

        paired = build_answer_context._paired_raw_evidence(anchor, related, limit=2)

        self.assertEqual([1], [item.message_number for item in paired])

    def test_related_raw_chunks_reads_the_exact_neighbor_beyond_fifty_messages(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            raw = root / "conversations" / "2099" / "12" / "2099-12-13_141516" / "raw.md"
            raw.parent.mkdir(parents=True)
            parts = ["# Chat Log", ""]
            for number in range(1, 61):
                role = "User" if number % 2 == 1 else "Assistant"
                parts.extend((f"## {role}", "", f"SYNTHETIC_MESSAGE_{number}", ""))
            raw.write_text("\n".join(parts), encoding="utf-8")
            anchor = memory_index.MemorySearchResult(
                document_type="raw_chunk",
                path=raw,
                title="Synthetic / User message 55",
                date="2099-12-13",
                tags=(),
                snippet="SYNTHETIC_MESSAGE_55",
                score=10,
                speaker_role="user",
                message_number=55,
            )

            related = build_answer_context._related_raw_chunks(root, anchor, use_index=True)
            paired = build_answer_context._paired_raw_evidence(anchor, related, limit=2)

            self.assertEqual([54, 56], [item.message_number for item in related])
            self.assertEqual([55, 56], [item.message_number for item in paired])

    def test_active_live_session_is_excluded_from_past_chat_search(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            live_dir = root / "inbox" / "live"
            live_dir.mkdir(parents=True)
            older = live_dir / "2099-01-01_010101.jsonl"
            active = live_dir / "2099-01-02_020202.jsonl"
            older.write_text(
                json.dumps({"role": "user", "timestamp": "2099-01-01T01:01:01+09:00", "content": "ACTIVE_EXCLUSION_TOKEN older evidence"}) + "\n",
                encoding="utf-8",
            )
            active.write_text(
                json.dumps({"role": "user", "timestamp": "2099-01-02T02:02:02+09:00", "content": "ACTIVE_EXCLUSION_TOKEN ACTIVE_EXCLUSION_TOKEN current question"}) + "\n",
                encoding="utf-8",
            )

            context = build_answer_context.build_answer_context(
                root=root,
                question="前に俺が ACTIVE_EXCLUSION_TOKEN について何と言った？",
                use_index=False,
                include_core_memory=False,
                exclude_live_session=active,
            )

            live_results = [item for item in context.results if item.document_type == "live_message"]
            self.assertTrue(live_results)
            self.assertTrue(all(item.path == older for item in live_results))
            self.assertFalse(any(item.path == active for item in context.results))


if __name__ == "__main__":
    unittest.main()
