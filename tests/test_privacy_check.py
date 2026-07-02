import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "privacy_check.py"
SPEC = importlib.util.spec_from_file_location("privacy_check", MODULE_PATH)
privacy_check = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = privacy_check
SPEC.loader.exec_module(privacy_check)


class PrivacyCheckTests(unittest.TestCase):
    def test_scan_text_detects_secret_assignment(self):
        text = "API_" + "KEY=abcdefghijklmnopqrstuvwxyz\n"
        findings = privacy_check.scan_text("sample.txt", text)

        self.assertEqual(1, len(findings))
        self.assertEqual("secret assignment", findings[0].label)
        self.assertEqual("sample.txt", findings[0].path)
        self.assertEqual(1, findings[0].line_number)

    def test_scan_text_detects_email(self):
        text = "contact: " + "user" + "@example.com\n"
        findings = privacy_check.scan_text("sample.txt", text)

        self.assertEqual(1, len(findings))
        self.assertEqual("email address", findings[0].label)
        self.assertIn("***@", findings[0].match)

    def test_scan_text_allows_clean_text(self):
        findings = privacy_check.scan_text("sample.txt", "Phase2.65 session resume workflow\n")

        self.assertEqual([], findings)


if __name__ == "__main__":
    unittest.main()
