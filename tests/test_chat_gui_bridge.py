import base64
import importlib.util
import io
import json
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import chat_gui_bridge  # noqa: E402
import build_answer_context  # noqa: E402
import codex_conversation  # noqa: E402
from live_session import create_live_session  # noqa: E402


class ChatGuiBridgeTests(unittest.TestCase):
    def test_start_session_returns_live_jsonl_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            result = chat_gui_bridge.handle_start_session({"root": str(root)})

            self.assertEqual(result["messages"], [])
            self.assertTrue(result["session"]["jsonl_file"].startswith("inbox"))
            self.assertTrue((root / result["session"]["jsonl_file"]).parent.exists())

    def test_send_message_saves_user_message_without_ai(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            result = chat_gui_bridge.handle_send_message(
                {
                    "root": str(root),
                    "content": "hello",
                    "no_ai": True,
                }
            )

            session_path = root / result["session"]["jsonl_file"]
            records = [json.loads(line) for line in session_path.read_text(encoding="utf-8").splitlines()]

            self.assertEqual(records[0]["role"], "user")
            self.assertEqual(records[0]["content"], "hello")
            self.assertIsNone(result["assistant"])
            self.assertTrue(session_path.with_suffix(".session.json").exists())
            log_text = (root / "logs" / "chat_gui_bridge.log").read_text(encoding="utf-8")
            self.assertIn("send_message.done", log_text)
            self.assertNotIn("hello", log_text)

    def test_send_message_accepts_new_session_path_from_start_session(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            started = chat_gui_bridge.handle_start_session({"root": str(root)})
            session_file = started["session"]["jsonl_file"]

            result = chat_gui_bridge.handle_send_message(
                {
                    "root": str(root),
                    "session_file": session_file,
                    "content": "first message",
                    "no_ai": True,
                }
            )

            session_path = root / result["session"]["jsonl_file"]
            self.assertTrue(session_path.exists())
            records = [json.loads(line) for line in session_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(records[0]["content"], "first message")

    def test_streaming_send_emits_deltas_and_saves_only_completed_reply(self):
        original = chat_gui_bridge.generate_assistant_reply_streaming_with_context

        def fake_stream(root, messages, on_delta, **kwargs):
            on_delta("途中")
            on_delta("返答")
            return codex_conversation.AssistantReplyResult(reply="確定返答", memory_context=None)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            deltas = []
            chat_gui_bridge.generate_assistant_reply_streaming_with_context = fake_stream
            try:
                result = chat_gui_bridge.handle_send_message(
                    {"root": str(root), "content": "hello", "request_id": "stream-test"},
                    on_delta=deltas.append,
                )
            finally:
                chat_gui_bridge.generate_assistant_reply_streaming_with_context = original

            session_path = root / result["session"]["jsonl_file"]
            records = [json.loads(line) for line in session_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(["途中", "返答"], deltas)
            self.assertEqual(["user", "assistant"], [record["role"] for record in records])
            self.assertEqual("確定返答", records[1]["content"])

    def test_streaming_cancel_discards_partial_reply(self):
        original = chat_gui_bridge.generate_assistant_reply_streaming_with_context

        def fake_stream(root, messages, on_delta, **kwargs):
            on_delta("保存しない途中返答")
            raise InterruptedError("返答生成を停止しました。")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            deltas = []
            chat_gui_bridge.generate_assistant_reply_streaming_with_context = fake_stream
            try:
                result = chat_gui_bridge.handle_send_message(
                    {"root": str(root), "content": "hello", "request_id": "cancel-test"},
                    on_delta=deltas.append,
                )
            finally:
                chat_gui_bridge.generate_assistant_reply_streaming_with_context = original

            session_path = root / result["session"]["jsonl_file"]
            records = [json.loads(line) for line in session_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(["保存しない途中返答"], deltas)
            self.assertTrue(result["cancelled"])
            self.assertEqual(["user"], [record["role"] for record in records])

    def test_streaming_unavailable_falls_back_without_duplicate_user_message(self):
        original_stream = chat_gui_bridge.generate_assistant_reply_streaming_with_context
        original_generate = chat_gui_bridge.generate_assistant_reply_with_context

        def unavailable(**kwargs):
            raise codex_conversation.AppServerStreamingUnavailable("unsupported")

        def fake_generate(**kwargs):
            return codex_conversation.AssistantReplyResult(reply="fallback", memory_context=None)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            chat_gui_bridge.generate_assistant_reply_streaming_with_context = unavailable
            chat_gui_bridge.generate_assistant_reply_with_context = fake_generate
            try:
                result = chat_gui_bridge.handle_send_message(
                    {"root": str(root), "content": "hello", "request_id": "fallback-test"},
                    on_delta=lambda _: None,
                )
            finally:
                chat_gui_bridge.generate_assistant_reply_streaming_with_context = original_stream
                chat_gui_bridge.generate_assistant_reply_with_context = original_generate

            session_path = root / result["session"]["jsonl_file"]
            records = [json.loads(line) for line in session_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(["user", "assistant"], [record["role"] for record in records])
            self.assertEqual("fallback", records[1]["content"])

    def test_send_message_returns_memory_context_metadata(self):
        original_generate = chat_gui_bridge.generate_assistant_reply_with_context

        def fake_generate(root, messages, **kwargs):
            context = build_answer_context.build_answer_context(
                root=root,
                question=messages[-1].content,
                use_index=False,
            )
            return codex_conversation.AssistantReplyResult(reply="記憶を参照した返答です。", memory_context=context)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            memory = root / "memory"
            memory.mkdir()
            (memory / "long_term.md").write_text("# Long-Term Memory\n\n- ユーザーはAI-LifeOSを作っている。\n", encoding="utf-8")
            (memory / "preferences.md").write_text("# Preferences\n\n- ユーザーは静かな店を好む。\n", encoding="utf-8")

            chat_gui_bridge.generate_assistant_reply_with_context = fake_generate
            try:
                result = chat_gui_bridge.handle_send_message(
                    {
                        "root": str(root),
                        "content": "俺の好みに合う店は？",
                    }
                )
            finally:
                chat_gui_bridge.generate_assistant_reply_with_context = original_generate

            self.assertEqual("assistant", result["assistant"]["role"])
            self.assertTrue(result["memory_context"]["used"])
            self.assertGreaterEqual(result["memory_context"]["reference_count"], 1)
            paths = {reference["path"] for reference in result["memory_context"]["references"]}
            self.assertIn("memory/preferences.md", paths)

    def test_send_message_uses_attachment_context_without_saving_body(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            attachment_text = "PRIVATE_ATTACHMENT_BODY"

            result = chat_gui_bridge.handle_send_message(
                {
                    "root": str(root),
                    "content": "添付を確認して",
                    "no_ai": True,
                    "attachments": [
                        {
                            "name": "notes.md",
                            "extension": "md",
                            "size_bytes": len(attachment_text.encode("utf-8")),
                            "text": attachment_text,
                        }
                    ],
                }
            )

            session_path = root / result["session"]["jsonl_file"]
            records = [json.loads(line) for line in session_path.read_text(encoding="utf-8").splitlines()]

            self.assertEqual("extracted", result["attachments"][0]["status"])
            self.assertIn("[Attachments]", records[0]["content"])
            self.assertIn("notes.md", records[0]["content"])
            self.assertNotIn(attachment_text, records[0]["content"])

    def test_send_message_returns_pdf_attachment_error_when_extraction_unavailable_or_invalid(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            result = chat_gui_bridge.handle_send_message(
                {
                    "root": str(root),
                    "content": "PDFを確認して",
                    "no_ai": True,
                    "attachments": [
                        {
                            "name": "sample.pdf",
                            "extension": "pdf",
                            "size_bytes": 12,
                            "data_base64": "bm90IGEgcGRm",
                        }
                    ],
                }
            )

            attachment = result["attachments"][0]
            self.assertEqual("error", attachment["status"])
            self.assertTrue(attachment["error"])

    @unittest.skipUnless(importlib.util.find_spec("openpyxl"), "openpyxl is not installed")
    def test_xlsx_attachment_extracts_formula_without_saving_body(self):
        from openpyxl import Workbook

        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Summary"
        sheet.append(["Item", "Amount"])
        sheet.append(["Coffee", "=SUM(200,250)"])
        hidden = workbook.create_sheet("Private")
        hidden["A1"] = "HIDDEN_VALUE"
        hidden.sheet_state = "hidden"
        output = io.BytesIO()
        workbook.save(output)
        workbook.close()
        raw = output.getvalue()
        encoded = base64.b64encode(raw).decode("ascii")

        report = chat_gui_bridge._normalize_attachment(
            {
                "name": "budget.xlsx",
                "size_bytes": len(raw),
                "data_base64": encoded,
            }
        )

        self.assertEqual("extracted", report["status"])
        self.assertIn("# Sheet: Summary", report["_text"])
        self.assertIn("Coffee\t=SUM(200,250)", report["_text"])
        self.assertNotIn("HIDDEN_VALUE", report["_text"])

        captured_prompt = {}
        original_generate = chat_gui_bridge.generate_assistant_reply_with_context

        def fake_generate(root, messages, **_kwargs):
            del root
            captured_prompt["latest"] = messages[-1].content
            return codex_conversation.AssistantReplyResult(reply="確認しました。", memory_context=None)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            chat_gui_bridge.generate_assistant_reply_with_context = fake_generate
            try:
                result = chat_gui_bridge.handle_send_message(
                    {
                        "root": str(root),
                        "content": "Excelを確認して",
                        "attachments": [
                            {
                                "name": "budget.xlsx",
                                "size_bytes": len(raw),
                                "data_base64": encoded,
                            }
                        ],
                    }
                )
            finally:
                chat_gui_bridge.generate_assistant_reply_with_context = original_generate
            session_path = root / result["session"]["jsonl_file"]
            saved = session_path.read_text(encoding="utf-8")

        self.assertIn("Coffee\t=SUM(200,250)", captured_prompt["latest"])
        self.assertIn("budget.xlsx", saved)
        self.assertNotIn("Coffee", saved)
        self.assertNotIn("=SUM(200,250)", saved)

    def test_xlsx_attachment_reports_extraction_limits(self):
        class FakeSheet:
            title = "Large"
            sheet_state = "visible"
            max_row = 1
            max_column = 1

            def iter_rows(self, **_kwargs):
                return iter([("x" * (chat_gui_bridge.MAX_ATTACHMENT_TEXT_CHARS + 100),)])

        class FakeWorkbook:
            worksheets = [FakeSheet()]

            def close(self):
                return None

        original_loader = chat_gui_bridge._load_xlsx_workbook
        chat_gui_bridge._load_xlsx_workbook = lambda _raw: FakeWorkbook()
        try:
            report = chat_gui_bridge._normalize_attachment(
                {
                    "name": "large.xlsx",
                    "size_bytes": 4,
                    "data_base64": base64.b64encode(b"xlsx").decode("ascii"),
                }
            )
        finally:
            chat_gui_bridge._load_xlsx_workbook = original_loader

        self.assertEqual("extracted", report["status"])
        self.assertTrue(report["truncated"])
        self.assertEqual(chat_gui_bridge.MAX_ATTACHMENT_TEXT_CHARS, report["extracted_chars"])

    def test_xlsx_attachment_returns_clear_dependency_error(self):
        original_loader = chat_gui_bridge._load_xlsx_workbook
        chat_gui_bridge._load_xlsx_workbook = lambda _raw: (_ for _ in ()).throw(
            RuntimeError("Excel抽出には openpyxl が必要です。")
        )
        try:
            report = chat_gui_bridge._normalize_attachment(
                {
                    "name": "missing.xlsx",
                    "size_bytes": 4,
                    "data_base64": base64.b64encode(b"xlsx").decode("ascii"),
                }
            )
        finally:
            chat_gui_bridge._load_xlsx_workbook = original_loader

        self.assertEqual("error", report["status"])
        self.assertIn("openpyxl", report["error"])

    @unittest.skipUnless(importlib.util.find_spec("openpyxl"), "openpyxl is not installed")
    def test_xlsx_attachment_returns_error_for_invalid_workbook(self):
        report = chat_gui_bridge._normalize_attachment(
            {
                "name": "broken.xlsx",
                "size_bytes": 12,
                "data_base64": base64.b64encode(b"not an xlsx").decode("ascii"),
            }
        )

        self.assertEqual("error", report["status"])
        self.assertIn("Excelファイルを開けませんでした", report["error"])

    def test_resume_session_returns_messages(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            now = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)
            session = create_live_session(root=root, started_at=now)
            session.append_message("user", "resume me", now)
            session.append_message("assistant", "ok", now + timedelta(seconds=2))

            result = chat_gui_bridge.handle_resume_session(
                {
                    "root": str(root),
                    "session_ref": session.path.stem,
                    "retention_days": 10,
                }
            )

            self.assertEqual(result["session"]["session_id"], session.path.stem)
            self.assertEqual([message["role"] for message in result["messages"]], ["user", "assistant"])

    def test_cleanup_expired_is_dry_run_by_default(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
            session = create_live_session(root=root, started_at=old)
            session.append_message("user", "old message", old)

            result = chat_gui_bridge.handle_cleanup_expired(
                {
                    "root": str(root),
                    "retention_days": 10,
                }
            )

            self.assertTrue(session.path.exists())
            self.assertEqual(1, len(result["results"]))
            self.assertNotEqual("削除済み", result["results"][0]["status"])

    def test_finalize_job_runs_in_background_and_returns_status(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            prompt_dir = root / "prompts"
            prompt_dir.mkdir()
            (prompt_dir / "codex_phase2_prompt.md").write_text("Process {RAW_FILE}", encoding="utf-8")
            session = create_live_session(root=root, started_at=datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc))
            session.append_message("user", "finalize me", session.started_at)

            started = chat_gui_bridge.handle_start_finalize_job(
                {
                    "root": str(root),
                    "session_file": str(session.path),
                    "run_codex": False,
                }
            )
            job_id = started["job"]["job_id"]

            status = None
            for _ in range(50):
                status = chat_gui_bridge.handle_get_finalize_job({"root": str(root), "job_id": job_id})["job"]
                if status["status"] in {"succeeded", "failed", "cancelled"}:
                    break
                time.sleep(0.1)

            self.assertIsNotNone(status)
            self.assertEqual("succeeded", status["status"])
            self.assertTrue((root / status["result"]["raw_file"]).exists())
            for process in chat_gui_bridge.BACKGROUND_PROCESSES:
                process.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
