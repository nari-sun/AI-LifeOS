import argparse
import json
import os
import re
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from codex_conversation import generate_assistant_reply
from finalize_live_chat import finalize_live_chat
from live_session import ROOT, LiveMessage, LiveSession, create_live_message, create_live_session
from session_store import cleanup_expired_sessions, get_session_organization, list_resumable_sessions, load_resume_session, save_session

GUI_LOG_ENV = "AI_LIFEOS_GUI_LOG"
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


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


def handle_send_message(payload: dict[str, Any]) -> dict[str, Any]:
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

    user_message = create_live_message("user", content)
    session.append_message(user_message.role, user_message.content, user_message.timestamp)
    messages.append(user_message)
    _save_session_metadata(root=root, session_file=session_file, status="saved")

    assistant_message = None
    error = None
    cancelled = False
    if not bool(payload.get("no_ai", False)):
        try:
            run_command = _cancelable_run_command(root=root, cancel_file=cancel_file) if cancel_file else subprocess.run
            reply = generate_assistant_reply(
                root=root,
                messages=messages,
                codex_command=str(payload.get("codex_command") or "codex.cmd"),
                sandbox=str(payload.get("codex_sandbox") or "read-only"),
                approval=str(payload.get("codex_approval") or "never"),
                max_context_messages=int(payload.get("max_context_messages") or 20),
                run_command=run_command,
            )
            if cancel_file and cancel_file.exists():
                raise AssistantGenerationCancelled("返答生成を停止しました。")
            saved_assistant = create_live_message("assistant", reply)
            session.append_message(saved_assistant.role, saved_assistant.content, saved_assistant.timestamp)
            messages.append(saved_assistant)
            assistant_message = saved_assistant
            _save_session_metadata(root=root, session_file=session_file, status="saved")
        except AssistantGenerationCancelled as exc:
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
    _gui_log(root, f"cleanup_expired.start days={retention_days}")
    results = cleanup_expired_sessions(
        root=root,
        retention_days=retention_days,
        delete=True,
        auto_finalize=True,
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


COMMANDS = {
    "start-session": handle_start_session,
    "send-message": handle_send_message,
    "cancel-message": handle_cancel_message,
    "save-session": handle_save_session,
    "list-resumable": handle_list_resumable,
    "resume-session": handle_resume_session,
    "finalize-session": handle_finalize_session,
    "cleanup-expired": handle_cleanup_expired,
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
        result = COMMANDS[args.command](payload)
        _gui_log(root, f"command.success name={args.command} result={_result_log_summary(result)}")
        _write_json({"ok": True, **result})
        return 0
    except Exception as exc:
        _gui_log(root, f"command.error name={args.command} {_format_exception(exc)}")
        _gui_log(root, "command.traceback\n" + traceback.format_exc())
        _write_json({"ok": False, "error": str(exc), "type": type(exc).__name__})
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
