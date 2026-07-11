import argparse
import base64
import binascii
import json
import os
import re
import subprocess
import sys
import traceback
from datetime import date, datetime, time as datetime_time
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from build_answer_context import AnswerContext, MemoryContextReference
from codex_conversation import (
    AppServerStreamingUnavailable,
    generate_assistant_reply_streaming_with_context,
    generate_assistant_reply_with_context,
)
from finalize_live_chat import finalize_live_chat
from live_session import ROOT, LiveMessage, LiveSession, create_live_message, create_live_session
from local_data_report import build_local_data_report
from session_store import cleanup_expired_sessions, get_session_organization, list_resumable_sessions, load_resume_session, save_session

GUI_LOG_ENV = "AI_LIFEOS_GUI_LOG"
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
MAX_ATTACHMENTS = 3
MAX_ATTACHMENT_BYTES = 1024 * 1024
MAX_ATTACHMENT_TEXT_CHARS = 12_000
MAX_XLSX_SHEETS = 20
MAX_XLSX_ROWS_PER_SHEET = 200
MAX_XLSX_COLUMNS_PER_SHEET = 50
ALLOWED_ATTACHMENT_EXTENSIONS = {".txt", ".md", ".pdf", ".xlsx"}
TEXT_ATTACHMENT_EXTENSIONS = {".txt", ".md"}
JOB_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
LOCAL_DATA_FOLDERS = {"conversations", "journal", "memory", "inbox", "tasks", "imports", "logs"}
BACKGROUND_PROCESSES: list[subprocess.Popen[str]] = []


class AssistantGenerationCancelled(RuntimeError):
    pass


def handle_start_session(payload: dict[str, Any]) -> dict[str, Any]:
    root = _payload_root(payload)
    session = create_live_session(root=root)
    _gui_log(root, f"start_session.created session={session.path.stem}")
    return {
        "session": _serialize_session_file(session.path, root),
        "messages": [],
    }


def handle_send_message(
    payload: dict[str, Any],
    on_delta: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    root = _payload_root(payload)
    content = str(payload.get("content", "")).strip()
    if not content:
        raise ValueError("送信するメッセージが空です。")

    session_file = _resolve_or_create_session(root=root, value=payload.get("session_file"))
    request_id = _optional_request_id(payload.get("request_id"))
    cancel_file = _cancel_file_path(root, request_id) if request_id else None
    if cancel_file:
        _clear_cancel_file(cancel_file)
    _gui_log(
        root,
        f"send_message.start session={session_file.stem} request_id={request_id or '-'} no_ai={bool(payload.get('no_ai', False))}",
    )

    session = LiveSession(path=session_file, started_at=_session_started_at(session_file))
    messages = _read_live_messages(session_file)
    attachments = _normalize_attachments(payload.get("attachments"))
    attachment_metadata = _format_attachment_metadata_for_saved_message(attachments)
    saved_user_content = content + attachment_metadata
    prompt_user_content = _format_user_content_for_generation(content, attachments)

    user_message = create_live_message("user", saved_user_content)
    session.append_message(user_message.role, user_message.content, user_message.timestamp)
    messages.append(user_message)
    _save_session_metadata(root=root, session_file=session_file, status="saved")

    assistant_message = None
    memory_context = None
    error = None
    cancelled = False
    if not bool(payload.get("no_ai", False)):
        try:
            run_command = _cancelable_run_command(root=root, cancel_file=cancel_file) if cancel_file else subprocess.run
            generation_messages = _messages_for_generation(messages, user_message, prompt_user_content)
            generation_options = {
                "root": root,
                "messages": generation_messages,
                "codex_command": str(payload.get("codex_command") or "codex.cmd"),
                "sandbox": str(payload.get("codex_sandbox") or "read-only"),
                "approval": str(payload.get("codex_approval") or "never"),
                "max_context_messages": int(payload.get("max_context_messages") or 20),
            }
            if on_delta is None:
                reply_result = generate_assistant_reply_with_context(
                    **generation_options,
                    run_command=run_command,
                )
            else:
                try:
                    reply_result = generate_assistant_reply_streaming_with_context(
                        **generation_options,
                        on_delta=on_delta,
                        is_cancelled=(lambda: bool(cancel_file and cancel_file.exists())),
                    )
                except AppServerStreamingUnavailable as exc:
                    _gui_log(
                        root,
                        "send_message.streaming_fallback "
                        f"session={session_file.stem} request_id={request_id or '-'} message={_safe_log_text(str(exc))}",
                    )
                    reply_result = generate_assistant_reply_with_context(
                        **generation_options,
                        run_command=run_command,
                    )
            reply = reply_result.reply
            memory_context = reply_result.memory_context
            if cancel_file and cancel_file.exists():
                raise AssistantGenerationCancelled("返答生成を停止しました。")
            saved_assistant = create_live_message("assistant", reply)
            session.append_message(saved_assistant.role, saved_assistant.content, saved_assistant.timestamp)
            messages.append(saved_assistant)
            assistant_message = saved_assistant
            _save_session_metadata(root=root, session_file=session_file, status="saved")
        except (AssistantGenerationCancelled, InterruptedError) as exc:
            cancelled = True
            _gui_log(
                root,
                f"send_message.cancelled session={session_file.stem} request_id={request_id or '-'} message={_safe_log_text(str(exc))}",
            )
        except Exception as exc:  # Keep the already-saved user message visible to the GUI.
            error = f"{type(exc).__name__}: {exc}"
            _gui_log(root, f"send_message.assistant_error session={session_file.stem} {_format_exception(exc)}")
        finally:
            if cancel_file:
                _clear_cancel_file(cancel_file)

    _gui_log(
        root,
        f"send_message.done session={session_file.stem} messages={len(messages)} assistant_saved={assistant_message is not None} cancelled={cancelled}",
    )
    return {
        "session": _serialize_session_file(session_file, root),
        "messages": [_serialize_message(message) for message in messages],
        "assistant": _serialize_message(assistant_message) if assistant_message else None,
        "memory_context": _serialize_memory_context(memory_context),
        "attachments": [_serialize_attachment_report(attachment) for attachment in attachments],
        "error": error,
        "cancelled": cancelled,
    }


def handle_cancel_message(payload: dict[str, Any]) -> dict[str, Any]:
    root = _payload_root(payload)
    request_id = _required_request_id(payload.get("request_id"))
    cancel_file = _cancel_file_path(root, request_id)
    cancel_file.parent.mkdir(parents=True, exist_ok=True)
    cancel_file.write_text(datetime.now().astimezone().isoformat(timespec="seconds"), encoding="utf-8")
    _gui_log(root, f"cancel_message.requested request_id={request_id}")
    return {
        "request_id": request_id,
        "cancelled": True,
    }


def handle_save_session(payload: dict[str, Any]) -> dict[str, Any]:
    root = _payload_root(payload)
    session_file = _resolve_existing_session(root=root, value=payload.get("session_file"))
    _gui_log(root, f"save_session.start session={session_file.stem}")
    saved = save_session(
        root=root,
        session_file=session_file,
        title=_optional_text(payload.get("title")),
        status="saved",
    )
    _gui_log(root, f"save_session.done session={saved.session_id} messages={saved.message_count}")
    return {"saved": _serialize_saved_session(saved, root)}


def handle_list_resumable(payload: dict[str, Any]) -> dict[str, Any]:
    root = _payload_root(payload)
    retention_days = int(payload.get("retention_days") or 10)
    sessions = list_resumable_sessions(root=root, retention_days=retention_days)
    _gui_log(root, f"list_resumable.done days={retention_days} count={len(sessions)}")
    return {"sessions": [_serialize_resume_session(session, root) for session in sessions]}


def handle_resume_session(payload: dict[str, Any]) -> dict[str, Any]:
    root = _payload_root(payload)
    session_ref = str(payload.get("session_ref") or "latest")
    retention_days = int(payload.get("retention_days") or 10)
    _gui_log(root, f"resume_session.start ref={_safe_log_text(session_ref)} days={retention_days}")
    summary, records = load_resume_session(root=root, session_ref=session_ref, retention_days=retention_days)
    messages = [_message_from_record(record) for record in records]
    _gui_log(root, f"resume_session.done session={summary.session_id} messages={len(messages)}")
    return {
        "session": _serialize_session_file(summary.jsonl_file, root),
        "summary": _serialize_resume_session(summary, root),
        "messages": [_serialize_message(message) for message in messages],
    }


def handle_finalize_session(payload: dict[str, Any]) -> dict[str, Any]:
    root = _payload_root(payload)
    session_file = _resolve_existing_session(root=root, value=payload.get("session_file"))
    run_codex = bool(payload.get("run_codex", True))
    _gui_log(root, f"finalize_session.start session={session_file.stem} run_codex={run_codex}")
    result = finalize_live_chat(
        root=root,
        session_file=session_file,
        run_codex=run_codex,
        commit=False,
        force=True,
        codex_command=str(payload.get("codex_command") or "codex.cmd"),
        codex_sandbox="workspace-write",
        codex_approval=str(payload.get("codex_approval") or "never"),
    )
    _gui_log(root, f"finalize_session.done session={session_file.stem} raw={_display_path(result.raw_file, root)}")
    return {
        "session": _serialize_session_file(result.jsonl_file, root),
        "jsonl_file": _display_path(result.jsonl_file, root),
        "raw_file": _display_path(result.raw_file, root),
        "task_file": _display_path(result.task_file, root),
        "imported_at": result.imported_at.isoformat(timespec="seconds"),
        "codex_updated": bool(result.codex),
        "git_committed": bool(result.git and result.git.committed),
        "organization": result.organization,
    }


def handle_cleanup_expired(payload: dict[str, Any]) -> dict[str, Any]:
    root = _payload_root(payload)
    retention_days = int(payload.get("retention_days") or 10)
    delete = bool(payload.get("delete", False))
    auto_finalize = bool(payload.get("auto_finalize", False))
    _gui_log(root, f"cleanup_expired.start days={retention_days} delete={delete} auto_finalize={auto_finalize}")
    results = cleanup_expired_sessions(
        root=root,
        retention_days=retention_days,
        delete=delete,
        auto_finalize=auto_finalize,
    )
    _gui_log(root, f"cleanup_expired.done count={len(results)}")
    return {
        "results": [
            {
                "session_id": result.session_id,
                "status": result.status,
                "deleted_paths": [_display_path(path, root) for path in result.deleted_paths],
                "raw_file": _display_path(result.raw_file, root) if result.raw_file else None,
                "error": result.error,
            }
            for result in results
        ],
    }


def handle_local_data_report(payload: dict[str, Any]) -> dict[str, Any]:
    root = _payload_root(payload)
    _gui_log(root, "local_data_report.start")
    report = build_local_data_report(root=root)
    _gui_log(root, "local_data_report.done")
    return {"report": report}


def handle_open_local_data_folder(payload: dict[str, Any]) -> dict[str, Any]:
    root = _payload_root(payload)
    folder = str(payload.get("folder") or "").strip()
    if folder not in LOCAL_DATA_FOLDERS:
        raise ValueError("開けるフォルダはローカルデータ管理対象に限定されています。")

    path = (root / folder).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("フォルダはAI-LifeOSルート内を指定してください。") from exc
    if not path.exists() or not path.is_dir():
        raise FileNotFoundError(f"フォルダが見つかりません: {folder}")

    _open_folder(path)
    _gui_log(root, f"open_local_data_folder.done folder={folder}")
    return {"folder": folder, "path": _display_path(path, root)}


def handle_start_finalize_job(payload: dict[str, Any]) -> dict[str, Any]:
    root = _payload_root(payload)
    session_file = _resolve_existing_session(root=root, value=payload.get("session_file"))
    job_id = _new_job_id()
    log_path = _job_log_path(root, job_id)
    cancel_file = _job_cancel_file(root, job_id)
    _write_job_status(
        root,
        job_id,
        {
            "job_id": job_id,
            "name": "finalize-session",
            "status": "queued",
            "stage": "queued",
            "message": "整理ジョブを開始待ちです。",
            "error": None,
            "percent": 0,
            "session_file": _display_path(session_file, root),
            "log_path": _display_path(log_path, root),
            "cancel_file": _display_path(cancel_file, root),
            "created_at": _now_iso(),
            "started_at": None,
            "finished_at": None,
            "result": None,
        },
    )

    worker_payload = {
        "root": str(root),
        "job_id": job_id,
        "session_file": _display_path(session_file, root),
        "run_codex": bool(payload.get("run_codex", True)),
        "codex_command": str(payload.get("codex_command") or "codex.cmd"),
        "codex_approval": str(payload.get("codex_approval") or "never"),
    }
    _spawn_finalize_worker(root=root, payload=worker_payload, log_path=log_path)
    _gui_log(root, f"finalize_job.started job_id={job_id} session={session_file.stem}")
    return {"job": _read_job_status(root, job_id)}


def handle_get_finalize_job(payload: dict[str, Any]) -> dict[str, Any]:
    root = _payload_root(payload)
    job_id = _required_job_id(payload.get("job_id"))
    return {"job": _read_job_status(root, job_id)}


def handle_cancel_finalize_job(payload: dict[str, Any]) -> dict[str, Any]:
    root = _payload_root(payload)
    job_id = _required_job_id(payload.get("job_id"))
    status = _read_job_status(root, job_id)
    if status.get("status") not in {"succeeded", "failed", "cancelled"}:
        cancel_file = _job_cancel_file(root, job_id)
        cancel_file.parent.mkdir(parents=True, exist_ok=True)
        cancel_file.write_text(_now_iso(), encoding="utf-8")
        status = _write_job_status(
            root,
            job_id,
            {
                "status": status.get("status", "running"),
                "message": "キャンセル要求を送信しました。",
                "cancel_requested_at": _now_iso(),
            },
        )
        _gui_log(root, f"finalize_job.cancel_requested job_id={job_id}")
    return {"job": status}


def handle_run_finalize_job(payload: dict[str, Any]) -> dict[str, Any]:
    root = _payload_root(payload)
    job_id = _required_job_id(payload.get("job_id"))
    _run_finalize_job(root=root, payload=payload, job_id=job_id)
    return {"job": _read_job_status(root, job_id)}


COMMANDS = {
    "start-session": handle_start_session,
    "send-message": handle_send_message,
    "send-message-stream": handle_send_message,
    "cancel-message": handle_cancel_message,
    "save-session": handle_save_session,
    "list-resumable": handle_list_resumable,
    "resume-session": handle_resume_session,
    "finalize-session": handle_finalize_session,
    "cleanup-expired": handle_cleanup_expired,
    "local-data-report": handle_local_data_report,
    "open-local-data-folder": handle_open_local_data_folder,
    "start-finalize-job": handle_start_finalize_job,
    "get-finalize-job": handle_get_finalize_job,
    "cancel-finalize-job": handle_cancel_finalize_job,
    "run-finalize-job": handle_run_finalize_job,
}


def main() -> int:
    _configure_stdio()
    parser = argparse.ArgumentParser(description="Bridge between the Tauri GUI and AI-LifeOS Python workflows.")
    parser.add_argument("command", choices=sorted(COMMANDS))
    args = parser.parse_args()

    payload: dict[str, Any] = {}
    root = ROOT
    try:
        payload = _read_payload()
        root = _payload_root(payload)
        _gui_log(root, f"command.start name={args.command} payload={_payload_log_summary(payload)}")
        if args.command == "send-message-stream":
            result = handle_send_message(payload, on_delta=_write_stream_delta)
            _write_json({"type": "result", "data": {"ok": True, **result}})
            return 0
        result = COMMANDS[args.command](payload)
        _gui_log(root, f"command.success name={args.command} result={_result_log_summary(result)}")
        _write_json({"ok": True, **result})
        return 0
    except Exception as exc:
        _gui_log(root, f"command.error name={args.command} {_format_exception(exc)}")
        _gui_log(root, "command.traceback\n" + traceback.format_exc())
        error_result = {"ok": False, "error": str(exc), "type": type(exc).__name__}
        if args.command == "send-message-stream":
            _write_json({"type": "result", "data": error_result})
        else:
            _write_json(error_result)
        return 1


def _configure_stdio() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def _read_payload() -> dict[str, Any]:
    raw = sys.stdin.read().lstrip("\ufeff")
    if not raw.strip():
        return {}

    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("JSON payload must be an object.")
    return value


def _write_json(value: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
    sys.stdout.write("\n")
    sys.stdout.flush()


def _write_stream_delta(delta: str) -> None:
    _write_json({"type": "delta", "delta": delta})


def _gui_log_path(root: Path | str | None = None) -> Path:
    override = os.environ.get(GUI_LOG_ENV)
    if override:
        return Path(override)

    base = Path(root) if root is not None else ROOT
    return base / "logs" / "chat_gui_bridge.log"


def _gui_log(root: Path | str | None, message: str) -> None:
    try:
        path = _gui_log_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        with path.open("a", encoding="utf-8") as file:
            file.write(f"{timestamp} pid={os.getpid()} {message}\n")
    except OSError:
        pass


def _format_exception(exc: Exception) -> str:
    return f"type={type(exc).__name__} message={_safe_log_text(str(exc))}"


def _safe_log_text(value: str, max_length: int = 1000) -> str:
    text = value.replace("\r", "\\r").replace("\n", "\\n")
    if len(text) > max_length:
        return text[:max_length] + "...[truncated]"
    return text


def _payload_log_summary(payload: dict[str, Any]) -> str:
    parts = []
    if payload.get("session_file"):
        parts.append(f"session_file={Path(str(payload['session_file'])).name}")
    if payload.get("session_ref"):
        parts.append(f"session_ref={_safe_log_text(str(payload['session_ref']))}")
    if payload.get("request_id"):
        parts.append(f"request_id={_safe_log_text(str(payload['request_id']))}")
    if "content" in payload:
        parts.append(f"content_chars={len(str(payload.get('content') or ''))}")
    if "no_ai" in payload:
        parts.append(f"no_ai={bool(payload.get('no_ai'))}")
    if payload.get("retention_days"):
        parts.append(f"retention_days={payload['retention_days']}")
    return ",".join(parts) if parts else "-"


def _result_log_summary(result: dict[str, Any]) -> str:
    if "session" in result and isinstance(result["session"], dict):
        session = result["session"]
        return f"session={session.get('session_id', '-')}"
    if "sessions" in result and isinstance(result["sessions"], list):
        return f"sessions={len(result['sessions'])}"
    if "request_id" in result:
        return f"request_id={result['request_id']}"
    if "raw_file" in result:
        return f"raw_file={result['raw_file']}"
    return "-"


def _open_folder(path: Path) -> None:
    if os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
        return

    command = ["open", str(path)] if sys.platform == "darwin" else ["xdg-open", str(path)]
    subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _new_job_id() -> str:
    return uuid4().hex


def _required_job_id(value: Any) -> str:
    text = str(value or "").strip()
    if not JOB_ID_PATTERN.fullmatch(text):
        raise ValueError("job_id が不正です。")
    return text


def _job_dir(root: Path) -> Path:
    return root / "logs" / "chat_gui_jobs"


def _job_status_path(root: Path, job_id: str) -> Path:
    return _job_dir(root) / f"{job_id}.json"


def _job_log_path(root: Path, job_id: str) -> Path:
    return _job_dir(root) / f"{job_id}.log"


def _job_cancel_file(root: Path, job_id: str) -> Path:
    return _job_dir(root) / f"{job_id}.cancel"


def _read_job_status(root: Path, job_id: str) -> dict[str, Any]:
    path = _job_status_path(root, job_id)
    if not path.exists():
        raise FileNotFoundError(f"ジョブが見つかりません: {job_id}")

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"ジョブ状態ファイルが壊れています: {job_id}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"ジョブ状態ファイルが不正です: {job_id}")
    return value


def _write_job_status(root: Path, job_id: str, update: dict[str, Any]) -> dict[str, Any]:
    path = _job_status_path(root, job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    current: dict[str, Any] = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            current = loaded if isinstance(loaded, dict) else {}
        except json.JSONDecodeError:
            current = {}
    current.update(update)
    temp_path = path.with_suffix(".json.tmp")
    temp_path.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp_path.replace(path)
    return current


def _spawn_finalize_worker(root: Path, payload: dict[str, Any], log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = log_path.open("a", encoding="utf-8")
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "run-finalize-job"],
            cwd=root,
            env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
            stdin=subprocess.PIPE,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            creationflags=creationflags,
        )
        assert process.stdin is not None
        process.stdin.write(json.dumps(payload, ensure_ascii=False))
        process.stdin.close()
        BACKGROUND_PROCESSES.append(process)
        log_handle.close()
    except Exception:
        log_handle.close()
        raise


def _run_finalize_job(root: Path, payload: dict[str, Any], job_id: str) -> None:
    session_file = _resolve_existing_session(root=root, value=payload.get("session_file"))
    cancel_file = _job_cancel_file(root, job_id)
    _write_job_status(
        root,
        job_id,
        {
            "status": "running",
            "stage": "starting",
            "message": "整理処理を開始しました。",
            "percent": 5,
            "started_at": _now_iso(),
        },
    )

    def progress(percent: int, message: str) -> None:
        if cancel_file.exists():
            raise AssistantGenerationCancelled("整理ジョブをキャンセルしました。")
        _write_job_status(
            root,
            job_id,
            {
                "status": "running",
                "stage": _stage_from_progress_message(message),
                "message": message,
                "percent": percent,
            },
        )

    try:
        run_command = _cancelable_run_command(root=root, cancel_file=cancel_file)
        result = finalize_live_chat(
            root=root,
            session_file=session_file,
            run_codex=bool(payload.get("run_codex", True)),
            commit=False,
            force=True,
            codex_command=str(payload.get("codex_command") or "codex.cmd"),
            codex_sandbox="workspace-write",
            codex_approval=str(payload.get("codex_approval") or "never"),
            progress=progress,
            run_command=run_command,
        )
        _write_job_status(
            root,
            job_id,
            {
                "status": "succeeded",
                "stage": "done",
                "message": "整理処理が完了しました。",
                "percent": 100,
                "finished_at": _now_iso(),
                "result": {
                    "ok": True,
                    "session": _serialize_session_file(result.jsonl_file, root),
                    "jsonl_file": _display_path(result.jsonl_file, root),
                    "raw_file": _display_path(result.raw_file, root),
                    "task_file": _display_path(result.task_file, root),
                    "imported_at": result.imported_at.isoformat(timespec="seconds"),
                    "codex_updated": bool(result.codex),
                    "git_committed": bool(result.git and result.git.committed),
                    "organization": result.organization,
                },
            },
        )
    except AssistantGenerationCancelled as exc:
        _write_job_status(
            root,
            job_id,
            {
                "status": "cancelled",
                "stage": "cancelled",
                "message": str(exc),
                "finished_at": _now_iso(),
            },
        )
    except Exception as exc:
        _write_job_status(
            root,
            job_id,
            {
                "status": "failed",
                "stage": "failed",
                "message": "整理処理に失敗しました。",
                "error": f"{type(exc).__name__}: {exc}",
                "finished_at": _now_iso(),
            },
        )
        raise


def _stage_from_progress_message(message: str) -> str:
    lowered = message.lower()
    if "raw" in lowered:
        return "raw"
    if "memory" in lowered or "journal" in lowered or "summary" in lowered:
        return "memory"
    if "index" in lowered:
        return "index"
    return "running"


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _payload_root(payload: dict[str, Any]) -> Path:
    root = payload.get("root")
    return Path(root) if root else ROOT


def _optional_request_id(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return _sanitize_request_id(text)


def _required_request_id(value: Any) -> str:
    request_id = _optional_request_id(value)
    if not request_id:
        raise ValueError("request_id is required.")
    return request_id


def _sanitize_request_id(value: str) -> str:
    if len(value) > 128:
        raise ValueError("request_id is too long.")
    if not REQUEST_ID_PATTERN.fullmatch(value):
        raise ValueError("request_id contains invalid characters.")
    return value


def _cancel_file_path(root: Path, request_id: str) -> Path:
    return root / "logs" / "chat_gui_cancel" / f"{request_id}.cancel"


def _clear_cancel_file(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def _cancelable_run_command(root: Path, cancel_file: Path) -> Callable[..., subprocess.CompletedProcess[str]]:
    def run_command(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if cancel_file.exists():
            raise AssistantGenerationCancelled("返答生成を停止しました。")

        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

        process = subprocess.Popen(
            command,
            cwd=kwargs.get("cwd", root),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=bool(kwargs.get("text", False)),
            encoding=kwargs.get("encoding"),
            creationflags=creationflags,
        )

        input_text = kwargs.get("input")
        try:
            try:
                stdout, stderr = process.communicate(input=input_text, timeout=0.25)
            except subprocess.TimeoutExpired:
                while True:
                    if cancel_file.exists():
                        _terminate_process_tree(process)
                        _drain_cancelled_process(process)
                        raise AssistantGenerationCancelled("返答生成を停止しました。")
                    try:
                        stdout, stderr = process.communicate(timeout=0.25)
                        break
                    except subprocess.TimeoutExpired:
                        continue
        except FileNotFoundError:
            raise

        if cancel_file.exists():
            raise AssistantGenerationCancelled("返答生成を停止しました。")

        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)

    return run_command


def _terminate_process_tree(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return

    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            return
        except OSError:
            pass

    process.kill()


def _drain_cancelled_process(process: subprocess.Popen[Any]) -> None:
    try:
        process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()


def _resolve_or_create_session(root: Path, value: Any) -> Path:
    if value:
        path = _resolve_session_path(root=root, value=value, must_exist=False)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    return create_live_session(root=root).path


def _resolve_existing_session(root: Path, value: Any) -> Path:
    return _resolve_session_path(root=root, value=value, must_exist=True)


def _resolve_session_path(root: Path, value: Any, must_exist: bool) -> Path:
    if not value:
        raise ValueError("session_file is required.")

    path = Path(str(value))
    if not path.is_absolute():
        path = root / path

    path = path.resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("session_file must be inside the AI-LifeOS root.") from exc

    if path.suffix != ".jsonl":
        raise ValueError("session_file must be a .jsonl file.")
    if must_exist and not path.exists():
        raise FileNotFoundError(f"session_file not found: {path}")

    return path


def _read_live_messages(path: Path) -> list[LiveMessage]:
    if not path.exists():
        return []

    messages: list[LiveMessage] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSONL at line {line_number}: {path}") from exc
        messages.append(_message_from_record(record))
    return messages


def _message_from_record(record: dict[str, Any]) -> LiveMessage:
    return LiveMessage(
        role=str(record["role"]),
        content=str(record["content"]),
        timestamp=_parse_datetime(str(record["timestamp"])),
    )


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _session_started_at(path: Path) -> datetime:
    messages = _read_live_messages(path)
    if messages:
        return messages[0].timestamp
    return datetime.now().astimezone()


def _save_session_metadata(root: Path, session_file: Path, status: str) -> None:
    try:
        save_session(root=root, session_file=session_file, status=status)
    except Exception as exc:
        _gui_log(root, f"save_session_metadata.error session={session_file.stem} {_format_exception(exc)}")


def _serialize_session_file(path: Path, root: Path) -> dict[str, Any]:
    return {
        "session_id": path.stem,
        "jsonl_file": _display_path(path, root),
        "organization": _serialize_organization(root=root, session_file=path),
    }


def _serialize_message(message: LiveMessage) -> dict[str, str]:
    return {
        "role": message.role,
        "content": message.content,
        "timestamp": message.timestamp.isoformat(timespec="seconds"),
    }


def _serialize_memory_context(context: AnswerContext | None) -> dict[str, Any]:
    if context is None:
        return {
            "used": False,
            "should_use": False,
            "score": 0,
            "threshold": 0,
            "reasons": [],
            "reference_count": 0,
            "references": [],
        }

    return {
        "used": context.used_memory,
        "should_use": context.should_use_memory,
        "score": context.score,
        "threshold": context.threshold,
        "reasons": list(context.reasons),
        "reference_count": len(context.references),
        "references": [_serialize_memory_reference(reference) for reference in context.references],
    }


def _serialize_memory_reference(reference: MemoryContextReference) -> dict[str, Any]:
    return {
        "path": reference.path,
        "document_type": reference.document_type,
        "title": reference.title,
        "date": reference.date,
        "snippet": reference.snippet,
        "score": reference.score,
    }


def _normalize_attachments(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("attachments must be a list.")
    if len(value) > MAX_ATTACHMENTS:
        raise ValueError(f"添付ファイルは1回の送信で最大{MAX_ATTACHMENTS}件までです。")

    reports = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("attachment must be an object.")
        reports.append(_normalize_attachment(item))
    return reports


def _normalize_attachment(item: dict[str, Any]) -> dict[str, Any]:
    file_name = _safe_attachment_name(str(item.get("name") or item.get("file_name") or "attachment"))
    extension = Path(file_name).suffix.lower()
    size_bytes = _optional_int_value(item.get("size_bytes")) or 0
    report: dict[str, Any] = {
        "file_name": file_name,
        "extension": extension.lstrip(".") or "unknown",
        "size_bytes": size_bytes,
        "status": "error",
        "error": None,
        "extracted_chars": 0,
        "truncated": bool(item.get("truncated", False)),
        "_text": "",
    }

    if extension not in ALLOWED_ATTACHMENT_EXTENSIONS:
        report["error"] = "未対応のファイル形式です。"
        return report
    if size_bytes > MAX_ATTACHMENT_BYTES:
        report["error"] = f"ファイルサイズが上限({MAX_ATTACHMENT_BYTES} bytes)を超えています。"
        return report

    extractor_truncated = False
    try:
        if extension in TEXT_ATTACHMENT_EXTENSIONS:
            text = _attachment_text(item)
        elif extension == ".pdf":
            text = _extract_pdf_text_from_attachment(item)
        elif extension == ".xlsx":
            text, extractor_truncated = _extract_xlsx_text_from_attachment(item)
        else:
            text = ""
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        return report

    text = _normalize_attachment_text(text)
    truncated = bool(report["truncated"]) or extractor_truncated or len(text) > MAX_ATTACHMENT_TEXT_CHARS
    if truncated:
        text = text[:MAX_ATTACHMENT_TEXT_CHARS]

    if not text.strip():
        report["error"] = "抽出できる本文がありません。"
        return report

    report.update(
        {
            "status": "extracted",
            "error": None,
            "extracted_chars": len(text),
            "truncated": truncated,
            "_text": text,
        }
    )
    return report


def _attachment_text(item: dict[str, Any]) -> str:
    text = item.get("text")
    if isinstance(text, str):
        return text

    raw = _attachment_bytes(item)
    return raw.decode("utf-8", errors="replace")


def _attachment_bytes(item: dict[str, Any]) -> bytes:
    data_base64 = item.get("data_base64")
    if not isinstance(data_base64, str) or not data_base64:
        raise ValueError("添付ファイル本文がありません。")
    try:
        data = base64.b64decode(data_base64, validate=True)
    except binascii.Error as exc:
        raise ValueError("添付ファイルのbase64が不正です。") from exc
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise ValueError(f"ファイルサイズが上限({MAX_ATTACHMENT_BYTES} bytes)を超えています。")
    return data


def _extract_pdf_text_from_attachment(item: dict[str, Any]) -> str:
    raw = _attachment_bytes(item)
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("PDF抽出には pypdf が必要です。") from exc

    import io

    reader = PdfReader(io.BytesIO(raw))
    texts = []
    for page in reader.pages[:20]:
        texts.append(page.extract_text() or "")
    return "\n\n".join(texts)


def _extract_xlsx_text_from_attachment(item: dict[str, Any]) -> tuple[str, bool]:
    raw = _attachment_bytes(item)
    workbook = _load_xlsx_workbook(raw)
    lines: list[str] = []
    extracted_length = 0
    truncated = False

    try:
        visible_sheets = [sheet for sheet in workbook.worksheets if sheet.sheet_state == "visible"]
        if len(visible_sheets) > MAX_XLSX_SHEETS:
            truncated = True

        for sheet in visible_sheets[:MAX_XLSX_SHEETS]:
            max_row = min(sheet.max_row or 1, MAX_XLSX_ROWS_PER_SHEET)
            max_column = min(sheet.max_column or 1, MAX_XLSX_COLUMNS_PER_SHEET)
            if (sheet.max_row or 0) > max_row or (sheet.max_column or 0) > max_column:
                truncated = True

            sheet_lines: list[str] = []
            for row_number, row in enumerate(
                sheet.iter_rows(
                    min_row=1,
                    max_row=max_row,
                    min_col=1,
                    max_col=max_column,
                    values_only=True,
                ),
                start=1,
            ):
                values = [_xlsx_cell_text(value) for value in row]
                while values and not values[-1]:
                    values.pop()
                if not values:
                    continue

                row_text = f"Row {row_number}: " + "\t".join(values)
                remaining = MAX_ATTACHMENT_TEXT_CHARS + 1 - extracted_length
                if len(row_text) > remaining:
                    row_text = row_text[:remaining]
                    truncated = True
                sheet_lines.append(row_text)
                extracted_length += len(row_text) + 1
                if extracted_length > MAX_ATTACHMENT_TEXT_CHARS:
                    truncated = True
                    break

            if sheet_lines:
                header = f"# Sheet: {_xlsx_cell_text(sheet.title)}"
                lines.extend([header, *sheet_lines, ""])
                extracted_length += len(header) + 2
            if extracted_length > MAX_ATTACHMENT_TEXT_CHARS:
                break
    finally:
        workbook.close()

    return "\n".join(lines).strip(), truncated


def _load_xlsx_workbook(raw: bytes):
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise RuntimeError("Excel抽出には openpyxl が必要です。") from exc

    import io

    try:
        return load_workbook(io.BytesIO(raw), read_only=True, data_only=False, keep_links=False)
    except Exception as exc:
        raise ValueError(f"Excelファイルを開けませんでした: {exc}") from exc


def _xlsx_cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, (date, datetime_time)):
        return value.isoformat()
    return re.sub(r"[\r\n\t]+", " ", str(value)).strip()


def _normalize_attachment_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def _safe_attachment_name(value: str) -> str:
    name = Path(value.replace("\\", "/")).name.strip() or "attachment"
    name = re.sub(r"[\x00-\x1f<>:\"/\\|?*]+", "_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name:
        name = "attachment"
    if len(name) > 120:
        suffix = Path(name).suffix
        stem_limit = max(1, 120 - len(suffix) - 3)
        name = f"{Path(name).stem[:stem_limit]}...{suffix}"
    return name


def _format_attachment_metadata_for_saved_message(attachments: list[dict[str, Any]]) -> str:
    if not attachments:
        return ""

    lines = ["", "", "[Attachments]"]
    for attachment in attachments:
        parts = [
            f"name={attachment['file_name']}",
            f"type={attachment['extension']}",
            f"status={attachment['status']}",
            f"chars={attachment['extracted_chars']}",
            f"truncated={'yes' if attachment['truncated'] else 'no'}",
        ]
        if attachment.get("error"):
            parts.append(f"error={_safe_log_text(str(attachment['error']), max_length=160)}")
        lines.append("- " + "; ".join(parts))
    return "\n".join(lines)


def _format_user_content_for_generation(content: str, attachments: list[dict[str, Any]]) -> str:
    context = _attachment_context_text(attachments)
    if not context:
        return content
    return "\n\n".join(
        [
            content,
            "---",
            "添付ファイルの一時コンテキストです。本文はlive JSONLには保存せず、この回答生成だけに使います。",
            context,
        ]
    )


def _attachment_context_text(attachments: list[dict[str, Any]]) -> str:
    sections = []
    for attachment in attachments:
        if attachment.get("status") != "extracted":
            continue
        sections.extend(
            [
                f"## {attachment['file_name']}",
                f"type: {attachment['extension']}, chars: {attachment['extracted_chars']}, truncated: {attachment['truncated']}",
                "",
                str(attachment.get("_text") or ""),
            ]
        )
    return "\n".join(sections).strip()


def _messages_for_generation(
    messages: list[LiveMessage],
    saved_user_message: LiveMessage,
    prompt_user_content: str,
) -> list[LiveMessage]:
    if prompt_user_content == saved_user_message.content:
        return messages

    generation_messages = list(messages)
    generation_messages[-1] = LiveMessage(
        role=saved_user_message.role,
        content=prompt_user_content,
        timestamp=saved_user_message.timestamp,
    )
    return generation_messages


def _serialize_attachment_report(attachment: dict[str, Any]) -> dict[str, Any]:
    return {
        "file_name": attachment["file_name"],
        "extension": attachment["extension"],
        "size_bytes": attachment["size_bytes"],
        "status": attachment["status"],
        "error": attachment["error"],
        "extracted_chars": attachment["extracted_chars"],
        "truncated": attachment["truncated"],
    }


def _optional_int_value(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _serialize_saved_session(session: Any, root: Path) -> dict[str, Any]:
    return {
        "session_id": session.session_id,
        "status": session.status,
        "title": session.title,
        "jsonl_file": _display_path(session.jsonl_file, root),
        "metadata_file": _display_path(session.metadata_file, root),
        "message_count": session.message_count,
        "started_at": session.started_at.isoformat(timespec="seconds"),
        "updated_at": session.updated_at.isoformat(timespec="seconds"),
        "saved_at": session.saved_at.isoformat(timespec="seconds"),
    }


def _serialize_resume_session(session: Any, root: Path) -> dict[str, Any]:
    return {
        "session_id": session.session_id,
        "title": session.title,
        "jsonl_file": _display_path(session.jsonl_file, root),
        "message_count": session.message_count,
        "started_at": session.started_at.isoformat(timespec="seconds"),
        "updated_at": session.updated_at.isoformat(timespec="seconds"),
        "last_user_at": session.last_user_at.isoformat(timespec="seconds"),
        "organization": _serialize_organization(root=root, session_file=session.jsonl_file),
    }


def _serialize_organization(root: Path, session_file: Path) -> dict[str, Any]:
    if not session_file.exists():
        return {
            "status": "empty",
            "label": "未開始",
            "can_organize": False,
            "is_organized": False,
            "next_stage": None,
            "failed_stage": None,
            "last_error": None,
            "raw_file": None,
            "task_file": None,
            "current_message_count": 0,
            "current_updated_at": None,
            "organized_message_count": 0,
            "organized_updated_at": None,
            "stages": {
                "raw": {"name": "raw", "label": "raw.md作成", "status": "pending"},
                "memory": {"name": "memory", "label": "記憶整理", "status": "pending"},
                "index": {"name": "index", "label": "検索index更新", "status": "pending"},
            },
        }

    return get_session_organization(root=root, session_file=session_file)


def _display_path(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


if __name__ == "__main__":
    raise SystemExit(main())
