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
            "# Chat Log\n\nDate: 2026-07-05\n\nラーメンの好みについて話した。\n",
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

    def test_build_answer_context_skips_general_question(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)

            context = build_answer_context.build_answer_context(
                root=root,
                question="SQLiteとは？",
                use_index=False,
            )

            self.assertFalse(context.should_use_memory)
            self.assertEqual("", context.text)

    def test_memory_need_scoring_combines_weak_signals(self):
        personal = build_answer_context.assess_memory_need("俺におすすめの本は？")
        generic = build_answer_context.assess_memory_need("おすすめの本は？")

        self.assertTrue(personal.should_use_memory)
        self.assertIn("self-plus-personal-topic", personal.reasons)
        self.assertFalse(generic.should_use_memory)

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
