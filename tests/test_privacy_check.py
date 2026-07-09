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

    def test_scan_text_detects_env_style_secret_with_prefix(self):
        text = "OPENAI_" + "API_" + "KEY=" + "abcdefghijklmnopqrstuvwxyz123456\n"
        findings = privacy_check.scan_text(".env", text)

        self.assertEqual(1, len(findings))
        self.assertEqual("secret assignment", findings[0].label)

    def test_scan_text_detects_url_query_secret(self):
        text = "callback=https://example.com/cb?access_" + "token=" + "abcdefghijklmnop12345678\n"
        findings = privacy_check.scan_text("sample.txt", text)

        self.assertEqual(1, len(findings))
        self.assertEqual("URL query secret", findings[0].label)

    def test_scan_text_detects_account_id(self):
        text = "tenant_" + "id=" + "123456789012\n"
        findings = privacy_check.scan_text("sample.txt", text)

        self.assertEqual(1, len(findings))
        self.assertEqual("account id", findings[0].label)

    def test_scan_text_detects_address(self):
        text = "住所: " + "東京" + "都" + "渋谷" + "区" + "神南" + "1-2-3\n"
        findings = privacy_check.scan_text("sample.txt", text)

        self.assertEqual(1, len(findings))
        self.assertEqual("Japanese address", findings[0].label)

    def test_scan_text_detects_long_random_string_in_publish_profile(self):
        random_value = (
            "A1b2C3d4E5f6G7h8"
            "I9j0K1l2M3n4O5p6"
            "Q7r8S9t0U1v2W3x4"
        )
        commit_findings = privacy_check.scan_text("sample.txt", random_value)
        publish_findings = privacy_check.scan_text("sample.txt", random_value, profile=privacy_check.PROFILE_PUBLISH)

        self.assertEqual([], commit_findings)
        self.assertEqual(1, len(publish_findings))
        self.assertEqual("long random string", publish_findings[0].label)

    def test_scan_text_ignores_allowlisted_false_positive_label(self):
        text = "住所: サンプル県テスト市1-2-3  # privacy-check: allow test fixture\n"
        findings = privacy_check.scan_text("sample.txt", text)

        self.assertEqual([], findings)

    def test_scan_text_does_not_allowlist_secret_assignment(self):
        text = "API_" + "KEY=abcdefghijklmnopqrstuvwxyz  # privacy-check: allow test fixture\n"
        findings = privacy_check.scan_text("sample.txt", text)

        self.assertEqual(1, len(findings))
        self.assertEqual("secret assignment", findings[0].label)

    def test_scan_path_detects_personal_data_directory(self):
        findings = privacy_check.scan_path("conversations/2026/07/raw.md")

        self.assertEqual(1, len(findings))
        self.assertEqual("personal data path", findings[0].label)

    def test_scan_path_detects_imports_directory(self):
        findings = privacy_check.scan_path("imports/chatgpt_export/messages.json")

        self.assertEqual(1, len(findings))
        self.assertEqual("personal data path", findings[0].label)

    def test_scan_path_detects_logs_directory(self):
        findings = privacy_check.scan_path("logs/chat_gui_jobs/example.json")

        self.assertEqual(1, len(findings))
        self.assertEqual("personal data path", findings[0].label)

    def test_scan_path_allows_gitkeep_placeholders(self):
        findings = privacy_check.scan_path("memory/.gitkeep")

        self.assertEqual([], findings)

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
