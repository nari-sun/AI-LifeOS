import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import local_data_report  # noqa: E402


class LocalDataReportTests(unittest.TestCase):
    def test_build_report_counts_existing_personal_data_without_reading_content(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            conversation = root / "conversations" / "2026" / "07" / "2026-07-09_120000"
            conversation.mkdir(parents=True)
            (conversation / "raw.md").write_bytes(b"hello")
            (conversation / "summary.md").write_bytes(b"summary")
            journal = root / "journal" / "2026" / "07"
            journal.mkdir(parents=True)
            (journal / "2026-07-09.md").write_bytes(b"journal")
            memory = root / "memory"
            memory.mkdir()
            (memory / "long_term.md").write_bytes(b"memory")
            (memory / "search_index.sqlite3").write_bytes(b"sqlite")
            inbox = root / "inbox"
            inbox.mkdir()
            (inbox / ".gitkeep").write_bytes(b"placeholder")

            report = local_data_report.build_local_data_report(root)

            self.assertTrue(report["read_only"])
            self.assertEqual(2, report["directories"]["conversations"]["file_count"])
            self.assertEqual(12, report["directories"]["conversations"]["total_bytes"])
            self.assertEqual(1, report["directories"]["journal"]["file_count"])
            self.assertEqual(2, report["directories"]["memory"]["file_count"])
            self.assertEqual(0, report["directories"]["inbox"]["file_count"])
            self.assertTrue(report["search_index"]["exists"])
            self.assertEqual(6, report["search_index"]["size_bytes"])
            self.assertIn("conversations", report["directories"]["conversations"]["newest_file"])

    def test_missing_directories_are_reported_as_absent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            report = local_data_report.build_local_data_report(Path(temp_dir))

            self.assertFalse(report["directories"]["conversations"]["exists"])
            self.assertEqual(0, report["directories"]["conversations"]["file_count"])
            self.assertFalse(report["search_index"]["exists"])
            self.assertEqual(0, report["totals"]["file_count"])

    def test_json_cli_output_is_parseable_for_gui_bridge_use(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "logs").mkdir()
            (root / "logs" / "run.log").write_text("ok", encoding="utf-8")
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = local_data_report.main(["--root", str(root), "--json"])

            payload = json.loads(stdout.getvalue())
            self.assertEqual(0, exit_code)
            self.assertEqual(str(root), payload["root"])
            self.assertTrue(payload["directories"]["logs"]["exists"])
            self.assertEqual(1, payload["directories"]["logs"]["file_count"])
            self.assertEqual(2, payload["directories"]["logs"]["total_bytes"])


if __name__ == "__main__":
    unittest.main()
