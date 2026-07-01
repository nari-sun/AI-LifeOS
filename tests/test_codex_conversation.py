import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "codex_conversation.py"


class CodexConversationTests(unittest.TestCase):
    def make_live_file(self, root: Path) -> Path:
        live_dir = root / "inbox" / "live"
        live_dir.mkdir(parents=True, exist_ok=True)
        path = live_dir / "2026-07-01_223000.jsonl"
        records = [
            {
                "role": "user",
                "timestamp": "2026-07-01T22:30:00+09:00",
                "content": "再開したい",
            },
            {
                "role": "assistant",
                "timestamp": "2026-07-01T22:30:05+09:00",
                "content": "再開候補です。",
            },
        ]
        path.write_text(
            "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
            encoding="utf-8",
        )
        return path

    def test_resume_command_can_select_number(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.make_live_file(root)

            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--root",
                    str(root),
                    "--resume-days",
                    "9999",
                ],
                input="/resume\n1\n/exit\n",
                text=True,
                encoding="utf-8",
                capture_output=True,
                timeout=10,
            )

            self.assertEqual("", completed.stderr)
            self.assertEqual(0, completed.returncode)
            self.assertIn("2026-07-01_223000.jsonl", completed.stdout)


if __name__ == "__main__":
    unittest.main()
