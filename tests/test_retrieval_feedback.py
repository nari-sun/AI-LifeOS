import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import memory_index  # noqa: E402
import retrieval_feedback  # noqa: E402


class RetrievalFeedbackTests(unittest.TestCase):
    def make_root(self, temp_dir: str) -> Path:
        root = Path(temp_dir)
        memory = root / "memory"
        memory.mkdir()
        (memory / "long_term.md").write_text(
            "# Long-Term Memory\n\n- ユーザーのスマホはiPhone 17。\n",
            encoding="utf-8",
        )
        memory_index.rebuild_index(root)
        return root

    def test_confirmed_miss_stores_only_normalized_features_and_enables_soft_bonus(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            records = [
                {"role": "user", "content": "俺のスマホは？"},
                {"role": "assistant", "content": "保存記録では確認できません。"},
                {"role": "user", "content": "前に話したのが残ってるよ。"},
            ]

            recorded = retrieval_feedback.record_confirmed_retrieval_feedback(
                root=root,
                records=records,
                session_id="2026-07-15_120000",
            )

            feedback_path = root / retrieval_feedback.FEEDBACK_RELATIVE_PATH
            stored = feedback_path.read_text(encoding="utf-8")
            event = json.loads(stored)
            self.assertEqual(1, recorded)
            self.assertEqual("confirmed-retrieval-miss", event["outcome"])
            self.assertIn("short-question", event["features"])
            self.assertIn("self-reference", event["features"])
            self.assertIn("owned-device-question", event["features"])
            self.assertNotIn("俺のスマホ", stored)
            self.assertNotIn("iPhone 17", stored)
            self.assertEqual((1, ("learned-retrieval-pattern",)), retrieval_feedback.feedback_bonus(root, "おれのスマホは？"))

    def test_unconfirmed_correction_does_not_create_feedback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            records = [
                {"role": "user", "content": "俺のスマホは？"},
                {"role": "assistant", "content": "保存記録では確認できません。"},
                {"role": "user", "content": "前に話したのが残ってるよ。"},
            ]

            recorded = retrieval_feedback.record_confirmed_retrieval_feedback(
                root=root,
                records=records,
                session_id="2026-07-15_120000",
            )

            self.assertEqual(0, recorded)
            self.assertFalse((root / retrieval_feedback.FEEDBACK_RELATIVE_PATH).exists())


if __name__ == "__main__":
    unittest.main()
