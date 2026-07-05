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


if __name__ == "__main__":
    unittest.main()
