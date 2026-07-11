import argparse
import traceback
import shutil
import subprocess
import sys
import tempfile
import threading
import textwrap
import time
import os
import json
import queue
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from codex_cli_options import add_codex_model_options
from build_answer_context import AnswerContext, build_answer_context
from finalize_live_chat import FinalizeLiveChatResult, finalize_live_chat
from live_session import ROOT, LiveMessage, LiveSession, create_live_message, create_live_session
from session_store import ResumeSession, list_resumable_sessions, load_resume_session

DEBUG_LOG_ENV = "AI_LIFEOS_DEBUG_LOG"
DEFAULT_CHAT_CODEX_MODEL = "gpt-5.4-mini"
DEFAULT_CHAT_CODEX_REASONING_EFFORT = "medium"
DEFAULT_CHAT_CODEX_SERVICE_TIER = "fast"
DEFAULT_CHAT_CODEX_FAST_MODE = True


@dataclass(frozen=True)
class AssistantReplyResult:
    reply: str
    memory_context: AnswerContext | None


class AppServerStreamingUnavailable(RuntimeError):
    """Raised when the installed Codex CLI cannot provide app-server streaming."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Start an AI-LifeOS live conversation session.")
    parser.add_argument("--root", default=ROOT, help="AI-LifeOS root directory.")
    parser.add_argument(
        "--resume",
        nargs="?",
        const="latest",
        help="Load the latest resumable session, or a specific session id/path.",
    )
    parser.add_argument(
        "--resume-days",
        type=int,
        default=10,
        help="Only sessions whose last user input is within this many days can be resumed.",
    )
    parser.add_argument("--codex-command", default="codex.cmd", help="Codex CLI command.")
    parser.add_argument("--chat-codex-model", default=DEFAULT_CHAT_CODEX_MODEL, help="Codex model for chat replies.")
    parser.add_argument(
        "--chat-codex-reasoning-effort",
        default=DEFAULT_CHAT_CODEX_REASONING_EFFORT,
        choices=("minimal", "low", "medium", "high", "xhigh"),
        help="Codex reasoning effort for chat replies.",
    )
    parser.add_argument(
        "--chat-codex-service-tier",
        default=DEFAULT_CHAT_CODEX_SERVICE_TIER,
        help="Codex service tier for chat replies. The default requests Fast mode.",
    )
    parser.add_argument(
        "--no-chat-codex-fast-mode",
        action="store_true",
        help="Do not pass features.fast_mode=true for chat replies.",
    )
    parser.add_argument(
        "--codex-sandbox",
        default="read-only",
        choices=("read-only", "workspace-write", "danger-full-access"),
        help="Sandbox mode for chat replies. read-only is the default.",
    )
    parser.add_argument(
        "--codex-approval",
        default="never",
        choices=("untrusted", "on-request", "never"),
        help="Approval policy for Codex exec.",
    )
    parser.add_argument(
        "--max-context-messages",
        type=int,
        default=20,
        help="Maximum recent messages to pass to Codex on each turn.",
    )
    parser.add_argument(
        "--no-memory-context",
        action="store_true",
        help="Do not include AI-LifeOS memory search context in chat replies.",
    )
    parser.add_argument(
        "--no-ai",
        action="store_true",
        help="Save user messages without calling Codex. Useful for offline logging tests.",
    )
    parser.add_argument(
        "--no-finalize-on-exit",
        action="store_true",
        help="Do not convert the live JSONL into raw.md when the session exits.",
    )
    parser.add_argument(
        "--no-process-on-exit",
        action="store_true",
        help="Finalize raw.md on exit, but do not run the Phase2.5 summary/journal/memory task.",
    )
    parser.add_argument(
        "--commit-on-exit",
        action="store_true",
        help="After finalizing and processing on exit, commit only public project file changes.",
    )
    parser.add_argument(
        "--no-exit-progress",
        action="store_true",
        help="Do not show the spinner/progress line during exit processing.",
    )
    return parser


def _debug_log_path(root: Path | str | None = None) -> Path:
    override = os.environ.get(DEBUG_LOG_ENV)
    if override:
        return Path(override)

    base = Path(root) if root is not None else ROOT
    return base / "logs" / "codex_conversation_debug.log"


def _debug_log(root: Path | str | None, message: str) -> None:
    try:
        path = _debug_log_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        with path.open("a", encoding="utf-8") as file:
            file.write(f"{timestamp} pid={os.getpid()} {message}\n")
    except OSError:
        pass


def build_codex_chat_prompt(
    messages: list[LiveMessage],
    max_context_messages: int = 20,
    memory_context: str = "",
) -> str:
    recent_messages = messages[-max(max_context_messages, 1) :]
    transcript_lines = []
    for message in recent_messages:
        label = "User" if message.role == "user" else "Assistant"
        transcript_lines.extend([f"{label}:", message.content, ""])

    lines = [
        "You are the AI-LifeOS conversation assistant.",
        "Reply conversationally to the latest user message.",
        "Do not edit files, run commands, commit changes, or update memory/journal.",
        "The application has already saved the user message to the live JSONL log.",
        "Use the transcript and any read-only memory context below, then return only the assistant reply.",
        "",
    ]
    if memory_context.strip():
        lines.extend(["Memory Context:", "", memory_context.strip(), ""])
    lines.extend(["Transcript:", "", *transcript_lines])
    return "\n".join(lines).rstrip()


def generate_assistant_reply(
    root: Path | str,
    messages: list[LiveMessage],
    codex_command: str = "codex.cmd",
    sandbox: str = "read-only",
    approval: str = "never",
    model: str | None = DEFAULT_CHAT_CODEX_MODEL,
    reasoning_effort: str | None = DEFAULT_CHAT_CODEX_REASONING_EFFORT,
    service_tier: str | None = DEFAULT_CHAT_CODEX_SERVICE_TIER,
    fast_mode: bool | None = DEFAULT_CHAT_CODEX_FAST_MODE,
    max_context_messages: int = 20,
    include_memory_context: bool = True,
    run_command=subprocess.run,
) -> str:
    return generate_assistant_reply_with_context(
        root=root,
        messages=messages,
        codex_command=codex_command,
        sandbox=sandbox,
        approval=approval,
        model=model,
        reasoning_effort=reasoning_effort,
        service_tier=service_tier,
        fast_mode=fast_mode,
        max_context_messages=max_context_messages,
        include_memory_context=include_memory_context,
        run_command=run_command,
    ).reply


def generate_assistant_reply_with_context(
    root: Path | str,
    messages: list[LiveMessage],
    codex_command: str = "codex.cmd",
    sandbox: str = "read-only",
    approval: str = "never",
    model: str | None = DEFAULT_CHAT_CODEX_MODEL,
    reasoning_effort: str | None = DEFAULT_CHAT_CODEX_REASONING_EFFORT,
    service_tier: str | None = DEFAULT_CHAT_CODEX_SERVICE_TIER,
    fast_mode: bool | None = DEFAULT_CHAT_CODEX_FAST_MODE,
    max_context_messages: int = 20,
    include_memory_context: bool = True,
    run_command=subprocess.run,
) -> AssistantReplyResult:
    root = Path(root)
    _debug_log(root, f"assistant_reply.start messages={len(messages)} sandbox={sandbox}")
    memory_context = ""
    memory_context_result: AnswerContext | None = None
    if include_memory_context:
        latest_user = _latest_user_content(messages)
        memory_context_result = build_answer_context(root=root, question=latest_user)
        memory_context = memory_context_result.text
        _debug_log(
            root,
            "assistant_reply.memory_context "
            f"enabled={memory_context_result.used_memory} "
            f"score={memory_context_result.score}/{memory_context_result.threshold} "
            f"references={len(memory_context_result.references)} results={len(memory_context_result.results)}",
        )
    prompt = build_codex_chat_prompt(
        messages,
        max_context_messages=max_context_messages,
        memory_context=memory_context,
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        output_file = Path(temp_dir) / "assistant_reply.md"
        command = [
            codex_command,
            "--ask-for-approval",
            approval,
            "exec",
        ]
        add_codex_model_options(
            command,
            model=model,
            reasoning_effort=reasoning_effort,
            service_tier=service_tier,
            fast_mode=fast_mode,
        )
        command.extend(
            [
                "-C",
                str(root),
                "--sandbox",
                sandbox,
                "--color",
                "never",
                "--output-last-message",
                str(output_file),
                "-",
            ]
        )

        try:
            completed = run_command(
                command,
                cwd=root,
                input=prompt,
                text=True,
                encoding="utf-8",
                capture_output=True,
            )
        except FileNotFoundError as exc:
            _debug_log(root, "assistant_reply.error codex_not_found")
            raise RuntimeError("Codex CLI was not found. On Windows, use codex.cmd.") from exc

        _debug_log(root, f"assistant_reply.codex_exit returncode={completed.returncode}")
        if completed.returncode != 0:
            detail = "\n".join(
                part.strip()
                for part in (getattr(completed, "stderr", ""), getattr(completed, "stdout", ""))
                if part and part.strip()
            )
            raise RuntimeError(f"Codex CLI failed with exit code {completed.returncode}.\n{detail}".strip())

        if output_file.exists():
            reply = output_file.read_text(encoding="utf-8").strip()
        else:
            reply = (getattr(completed, "stdout", "") or "").strip()

    if not reply:
        _debug_log(root, "assistant_reply.error empty_reply")
        raise RuntimeError("Codex CLI completed but returned an empty assistant reply.")

    _debug_log(root, f"assistant_reply.success chars={len(reply)}")
    return AssistantReplyResult(reply=reply, memory_context=memory_context_result)


def generate_assistant_reply_streaming_with_context(
    root: Path | str,
    messages: list[LiveMessage],
    on_delta: Callable[[str], None],
    is_cancelled: Callable[[], bool] | None = None,
    codex_command: str = "codex.cmd",
    sandbox: str = "read-only",
    approval: str = "never",
    model: str | None = DEFAULT_CHAT_CODEX_MODEL,
    reasoning_effort: str | None = DEFAULT_CHAT_CODEX_REASONING_EFFORT,
    service_tier: str | None = DEFAULT_CHAT_CODEX_SERVICE_TIER,
    fast_mode: bool | None = DEFAULT_CHAT_CODEX_FAST_MODE,
    max_context_messages: int = 20,
    include_memory_context: bool = True,
    popen=subprocess.Popen,
) -> AssistantReplyResult:
    """Generate a reply through app-server and expose only agent-message deltas.

    The authoritative completed agent message is returned to the caller. Deltas are
    transient UI data and are never persisted by this function.
    """
    root = Path(root)
    memory_context = ""
    memory_context_result: AnswerContext | None = None
    if include_memory_context:
        memory_context_result = build_answer_context(root=root, question=_latest_user_content(messages))
        memory_context = memory_context_result.text
    prompt = build_codex_chat_prompt(
        messages,
        max_context_messages=max_context_messages,
        memory_context=memory_context,
    )

    command = [codex_command, "app-server", "--stdio"]
    if fast_mode is not None:
        command.extend(["-c", f"features.fast_mode={'true' if fast_mode else 'false'}"])
    try:
        process = popen(
            command,
            cwd=root,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    except (FileNotFoundError, OSError) as exc:
        raise AppServerStreamingUnavailable("Codex app-serverを起動できませんでした。") from exc

    if process.stdin is None or process.stdout is None:
        _terminate_process(process)
        raise AppServerStreamingUnavailable("Codex app-serverの標準入出力を開けませんでした。")

    events: queue.Queue[dict | None] = queue.Queue()
    stderr_lines: list[str] = []

    def read_stdout() -> None:
        try:
            for raw_line in process.stdout:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    events.put(value)
        finally:
            events.put(None)

    def read_stderr() -> None:
        if process.stderr is not None:
            stderr_lines.extend(line.rstrip() for line in process.stderr)

    threading.Thread(target=read_stdout, daemon=True).start()
    threading.Thread(target=read_stderr, daemon=True).start()

    def send(value: dict) -> None:
        try:
            process.stdin.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
            process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise AppServerStreamingUnavailable("Codex app-serverとの接続が終了しました。") from exc

    def wait_response(request_id: int, timeout_seconds: float = 15.0) -> dict:
        deadline = time.monotonic() + timeout_seconds
        deferred: list[dict] = []
        try:
            while time.monotonic() < deadline:
                try:
                    event = events.get(timeout=0.1)
                except queue.Empty:
                    continue
                if event is None:
                    detail = "\n".join(stderr_lines[-10:]).strip()
                    raise AppServerStreamingUnavailable(
                        f"Codex app-serverが応答前に終了しました。{(' ' + detail) if detail else ''}"
                    )
                if event.get("id") == request_id:
                    if "error" in event:
                        raise AppServerStreamingUnavailable(f"Codex app-server request failed: {event['error']}")
                    return event.get("result") or {}
                deferred.append(event)
        finally:
            for event in deferred:
                events.put(event)
        raise AppServerStreamingUnavailable("Codex app-serverの初期化がタイムアウトしました。")

    try:
        send(
            {
                "id": 1,
                "method": "initialize",
                "params": {
                    "clientInfo": {"name": "ai-lifeos", "version": "0.1.0"},
                    "capabilities": {"experimentalApi": True},
                },
            }
        )
        wait_response(1)
        send({"method": "initialized", "params": {}})
        thread_params: dict[str, object] = {
            "cwd": str(root.resolve()),
            "approvalPolicy": approval,
            "sandbox": sandbox,
            "ephemeral": True,
        }
        if model:
            thread_params["model"] = model
        if service_tier:
            thread_params["serviceTier"] = service_tier
        send({"id": 2, "method": "thread/start", "params": thread_params})
        thread_result = wait_response(2)
        thread_id = str((thread_result.get("thread") or {}).get("id") or "")
        if not thread_id:
            raise AppServerStreamingUnavailable("Codex app-serverがthread idを返しませんでした。")

        turn_params: dict[str, object] = {
            "threadId": thread_id,
            "input": [{"type": "text", "text": prompt}],
        }
        if model:
            turn_params["model"] = model
        if reasoning_effort:
            turn_params["effort"] = reasoning_effort
        if service_tier:
            turn_params["serviceTier"] = service_tier
        send({"id": 3, "method": "turn/start", "params": turn_params})
        turn_result = wait_response(3, timeout_seconds=30.0)
        turn_id = str((turn_result.get("turn") or {}).get("id") or "")
        if not turn_id:
            raise AppServerStreamingUnavailable("Codex app-serverがturn idを返しませんでした。")

        final_reply = ""
        interrupt_sent = False
        while True:
            if is_cancelled and is_cancelled() and not interrupt_sent:
                send(
                    {
                        "id": 4,
                        "method": "turn/interrupt",
                        "params": {"threadId": thread_id, "turnId": turn_id},
                    }
                )
                interrupt_sent = True
            try:
                event = events.get(timeout=0.1)
            except queue.Empty:
                if process.poll() is not None:
                    raise RuntimeError("Codex app-serverが返答生成中に終了しました。")
                continue
            if event is None:
                detail = "\n".join(stderr_lines[-10:]).strip()
                raise RuntimeError(f"Codex app-serverが返答生成中に終了しました。\n{detail}".strip())
            method = event.get("method")
            params = event.get("params") or {}
            if method == "item/agentMessage/delta":
                delta = str(params.get("delta") or "")
                if delta and not interrupt_sent:
                    on_delta(delta)
            elif method == "item/completed":
                item = params.get("item") or {}
                if item.get("type") == "agentMessage":
                    final_reply = str(item.get("text") or "")
            elif method == "turn/completed":
                turn = params.get("turn") or {}
                status = turn.get("status")
                if status == "interrupted" or interrupt_sent:
                    raise InterruptedError("返答生成を停止しました。")
                if status != "completed":
                    error = turn.get("error") or {}
                    raise RuntimeError(str(error.get("message") or f"Codex turn failed: {status}"))
                if not final_reply:
                    for item in turn.get("items") or []:
                        if item.get("type") == "agentMessage":
                            final_reply = str(item.get("text") or "")
                final_reply = final_reply.strip()
                if not final_reply:
                    raise RuntimeError("Codex app-server completed but returned an empty assistant reply.")
                return AssistantReplyResult(reply=final_reply, memory_context=memory_context_result)
    finally:
        _terminate_process(process)


def _terminate_process(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)


def _latest_user_content(messages: list[LiveMessage]) -> str:
    for message in reversed(messages):
        if message.role == "user":
            return message.content
    return ""


def _display_path(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _format_memory_context_status(context: AnswerContext | None) -> str:
    if context is None or not context.used_memory:
        if context is None:
            return "記憶参照: なし"
        return f"記憶参照: なし (score {context.score}/{context.threshold})"

    paths = [reference.path for reference in context.references]
    preview = ", ".join(paths[:3])
    if len(paths) > 3:
        preview += f", 他{len(paths) - 3}件"
    return (
        f"記憶参照: あり ({len(paths)}件, score {context.score}/{context.threshold})"
        f"\n参照元: {preview}"
    )


def _terminal_size() -> tuple[int, int]:
    size = shutil.get_terminal_size(fallback=(100, 30))
    return max(size.columns, 40), max(size.lines, 16)


def _clear_screen() -> None:
    print("\033[2J\033[H", end="")


def _wrap_message(role: str, content: str, width: int) -> list[str]:
    label = "You" if role == "user" else "Assistant"
    prefix = f"{label}: "
    indent = " " * len(prefix)
    wrapped_lines: list[str] = []

    for raw_line in content.splitlines() or [""]:
        available_width = max(width - len(prefix), 10)
        parts = textwrap.wrap(
            raw_line,
            width=available_width,
            break_long_words=True,
            replace_whitespace=False,
        ) or [""]

        for index, part in enumerate(parts):
            wrapped_lines.append(f"{prefix if index == 0 else indent}{part}")

    return wrapped_lines


def _render_screen(
    messages: list[LiveMessage],
    session_display_path: str,
    status: str = "",
) -> None:
    width, height = _terminal_size()
    rule = "-" * width
    header = [
        "AI-LifeOS - Codex Conversation",
        f"Log: {session_display_path}",
        "Enter: send    /resume: list    /resume <id|latest>: load    /exit: quit",
        rule,
    ]
    footer = [
        rule,
        *status.splitlines(),
    ]
    message_height = max(height - len(header) - len(footer) - 1, 4)

    message_lines: list[str] = []
    if messages:
        for message in messages:
            message_lines.extend(_wrap_message(message.role, message.content, width))
            message_lines.append("")
    else:
        message_lines.append("(No messages yet.)")

    visible_messages = message_lines[-message_height:]
    blank_count = max(message_height - len(visible_messages), 0)

    _clear_screen()
    for line in header:
        print(line[:width])

    for _ in range(blank_count):
        print()

    for line in visible_messages:
        print(line[:width])

    for line in footer:
        print(line[:width])


def _load_resume_messages(root: Path, session_ref: str, retention_days: int) -> tuple[LiveSession, list[LiveMessage]]:
    summary, records = load_resume_session(root=root, session_ref=session_ref, retention_days=retention_days)
    messages = [
        LiveMessage(
            role=record["role"],
            content=record["content"],
            timestamp=record["timestamp"],
        )
        for record in records
    ]

    return LiveSession(path=summary.jsonl_file, started_at=summary.started_at), messages


def _format_resume_list(sessions: list[ResumeSession]) -> str:
    if not sessions:
        return "No resumable sessions."

    lines = ["Enter a number to resume, or /cancel to cancel."]
    for index, session in enumerate(sessions[:10], start=1):
        lines.append(
            f"{index}. {session.session_id} | {session.last_user_at.isoformat(timespec='seconds')} | "
            f"{session.message_count} messages | {session.title}"
        )

    return "\n".join(lines)


def _resume_candidates(root: Path, retention_days: int) -> list[ResumeSession]:
    sessions = list_resumable_sessions(root=root, retention_days=retention_days)
    return sessions[:10]


def _cursor_selection_available() -> bool:
    if not sys.stdin.isatty():
        return False

    try:
        import msvcrt  # noqa: F401
    except ImportError:
        return False

    return True


def _render_resume_menu(candidates: list[ResumeSession], selected_index: int) -> None:
    width, _ = _terminal_size()
    rule = "-" * width

    _clear_screen()
    print("AI-LifeOS Resume")
    print("Up/Down: move    Enter: resume    Esc/q: cancel")
    print(rule)

    for index, session in enumerate(candidates):
        marker = ">" if index == selected_index else " "
        line = (
            f"{marker} {index + 1}. {session.session_id} | "
            f"{session.last_user_at.isoformat(timespec='seconds')} | "
            f"{session.message_count} messages | {session.title}"
        )
        print(line[:width])


def _read_resume_menu_key() -> str:
    import msvcrt

    key = msvcrt.getwch()

    if key == "\x03":
        raise KeyboardInterrupt
    if key in ("\x00", "\xe0"):
        key = msvcrt.getwch()
        if key == "H":
            return "up"
        if key == "P":
            return "down"
        return "unknown"
    if key in ("\r", "\n"):
        return "enter"
    if key == "\x1b" or key.lower() == "q":
        return "cancel"
    if key.isdigit():
        return f"digit:{key}"

    return "unknown"


def _select_resume_candidate_with_cursor(candidates: list[ResumeSession]) -> ResumeSession | None:
    selected_index = 0

    while True:
        _render_resume_menu(candidates, selected_index)
        action = _read_resume_menu_key()

        if action == "up":
            selected_index = (selected_index - 1) % len(candidates)
            continue
        if action == "down":
            selected_index = (selected_index + 1) % len(candidates)
            continue
        if action == "enter":
            return candidates[selected_index]
        if action == "cancel":
            return None
        if action.startswith("digit:"):
            digit = int(action.split(":", maxsplit=1)[1])
            if 1 <= digit <= min(len(candidates), 9):
                return candidates[digit - 1]


def _save_messages(session: LiveSession, messages: list[LiveMessage]) -> None:
    if messages:
        session.write_messages(messages)


def _write_exit_marker() -> None:
    marker = os.environ.get("AI_LIFEOS_EXIT_MARKER")
    if not marker:
        _debug_log(None, "exit_marker.skip no_env")
        return

    try:
        path = Path(marker)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("ok\n", encoding="utf-8")
        _debug_log(None, f"exit_marker.wrote path={path}")
    except OSError:
        _debug_log(None, f"exit_marker.error path={marker}")


class ExitProgress:
    def __init__(self, enabled: bool | None = None) -> None:
        self.enabled = sys.stdout.isatty() if enabled is None else enabled
        self._percent = 0
        self._message = ""
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "ExitProgress":
        if self.enabled:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.stop()

    def update(self, percent: int, message: str) -> None:
        with self._lock:
            self._percent = max(0, min(percent, 100))
            self._message = message

        if not self.enabled:
            return

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)
        if self.enabled:
            print("\r" + " " * 100 + "\r", end="", flush=True)

    def _run(self) -> None:
        frames = "|/-\\"
        index = 0
        while not self._stop.is_set():
            with self._lock:
                percent = self._percent
                message = self._message or "Working..."
            frame = frames[index % len(frames)]
            print(f"\r{frame} {percent:3d}% {message}", end="", flush=True)
            index += 1
            time.sleep(0.12)


def finish_session(
    root: Path | str,
    session: LiveSession,
    messages: list[LiveMessage],
    has_new_messages: bool,
    finalize_on_exit: bool = True,
    process_on_exit: bool = True,
    commit_on_exit: bool = False,
    codex_command: str = "codex.cmd",
    codex_approval: str = "never",
    progress: Callable[[int, str], None] | None = None,
    run_command=subprocess.run,
) -> tuple[bool, str, FinalizeLiveChatResult | None]:
    root = Path(root)
    _debug_log(
        root,
        "finish_session.start "
        f"messages={len(messages)} has_new={has_new_messages} finalize={finalize_on_exit} "
        f"process={process_on_exit} commit={commit_on_exit} session={session.path}",
    )
    if progress:
        progress(5, "Saving live log...")
    _save_messages(session, messages)
    _debug_log(root, f"finish_session.saved_live exists={session.path.exists()} path={session.path}")
    if not messages:
        if progress:
            progress(100, "No messages to save.")
        _debug_log(root, "finish_session.no_messages")
        return False, "Exited without messages.", None

    if not finalize_on_exit:
        if progress:
            progress(100, "Saved live log.")
        _debug_log(root, "finish_session.finalize_disabled")
        return True, f"Saved {len(messages)} messages and exited.", None

    if not has_new_messages:
        if progress:
            progress(100, "Saved live log.")
        _debug_log(root, "finish_session.no_new_messages")
        return True, f"Saved {len(messages)} messages. No new messages, skipped finalize.", None

    _debug_log(root, "finish_session.finalize_start")
    result = finalize_live_chat(
        root=root,
        session_file=session.path,
        run_codex=process_on_exit,
        commit=commit_on_exit,
        force=True,
        codex_command=codex_command,
        codex_sandbox="workspace-write",
        codex_approval=codex_approval,
        progress=progress,
        run_command=run_command,
    )
    _debug_log(root, f"finish_session.finalize_done raw={result.raw_file} codex={bool(result.codex)} git={bool(result.git)}")

    parts = [
        f"Saved {len(messages)} messages.",
        f"Finalized: {result.raw_file.name}",
    ]
    if result.codex:
        parts.append("Updated summary/journal/memory.")
    if result.git:
        parts.append("Committed public project changes." if result.git.committed else "No Git changes to commit.")

    _debug_log(root, "finish_session.success")
    return True, " ".join(parts), result


def finish_session_for_exit(
    root: Path | str,
    session: LiveSession,
    messages: list[LiveMessage],
    has_new_messages: bool,
    finalize_on_exit: bool = True,
    process_on_exit: bool = True,
    commit_on_exit: bool = False,
    codex_command: str = "codex.cmd",
    codex_approval: str = "never",
    show_progress: bool = True,
    run_command=subprocess.run,
) -> tuple[bool, str, int]:
    root = Path(root)
    _debug_log(
        root,
        "finish_session_for_exit.start "
        f"messages={len(messages)} has_new={has_new_messages} finalize={finalize_on_exit} "
        f"process={process_on_exit} commit={commit_on_exit} progress={show_progress}",
    )
    try:
        with ExitProgress(enabled=show_progress and sys.stdout.isatty()) as progress:
            saved, status, _ = finish_session(
                root=root,
                session=session,
                messages=messages,
                has_new_messages=has_new_messages,
                finalize_on_exit=finalize_on_exit,
                process_on_exit=process_on_exit,
                commit_on_exit=commit_on_exit,
                codex_command=codex_command,
                codex_approval=codex_approval,
                progress=progress.update,
                run_command=run_command,
            )
        _debug_log(root, f"finish_session_for_exit.success saved={saved} status={status!r}")
        return saved, status, 0
    except KeyboardInterrupt:
        _save_messages(session, messages)
        saved = bool(messages)
        _write_exit_marker()
        _debug_log(root, "finish_session_for_exit.keyboard_interrupt")
        return saved, "Saved live log, but exit processing was interrupted.", 0
    except (FileNotFoundError, FileExistsError, RuntimeError, ValueError) as exc:
        _save_messages(session, messages)
        saved = bool(messages)
        _write_exit_marker()
        _debug_log(root, f"finish_session_for_exit.error type={type(exc).__name__} message={exc}")
        return saved, f"Saved live log, but finalize failed: {exc}", 0


def main() -> int:
    args = build_parser().parse_args()
    root = Path(args.root)
    _debug_log(root, f"main.start argv={sys.argv[1:]}")

    try:
        if args.resume:
            session, messages = _load_resume_messages(root=root, session_ref=args.resume, retention_days=args.resume_days)
            status = f"Resumed session: {session.path.name}"
            _debug_log(root, f"main.session_resumed path={session.path} messages={len(messages)}")
        else:
            session = create_live_session(root=root)
            messages: list[LiveMessage] = []
            status = "Started a new live session."
            _debug_log(root, f"main.session_created path={session.path}")
    except (FileNotFoundError, ValueError) as exc:
        _debug_log(root, f"main.session_error type={type(exc).__name__} message={exc}")
        print(f"ERROR: {exc}")
        return 1

    session_display_path = _display_path(session.path, root)
    saved = False
    has_new_messages = False
    resume_candidates: list[ResumeSession] = []
    exit_code = 0

    _render_screen(messages, session_display_path, status)

    try:
        while True:
            message = input("You > ")
            normalized = message.strip().lower()

            if normalized == "/exit":
                _debug_log(root, "main.command_exit")
                saved, status, finish_exit_code = finish_session_for_exit(
                    root=root,
                    session=session,
                    messages=messages,
                    has_new_messages=has_new_messages,
                    finalize_on_exit=not args.no_finalize_on_exit,
                    process_on_exit=not args.no_process_on_exit and not args.no_ai,
                    commit_on_exit=args.commit_on_exit,
                    codex_command=args.codex_command,
                    codex_approval=args.codex_approval,
                    show_progress=not args.no_exit_progress,
                )
                if finish_exit_code:
                    exit_code = finish_exit_code
                _render_screen(messages, session_display_path, status)
                break

            if normalized == "/resume":
                _debug_log(root, "main.command_resume_menu")
                resume_candidates = _resume_candidates(root=root, retention_days=args.resume_days)
                if not resume_candidates:
                    status = "No resumable sessions."
                    _render_screen(messages, session_display_path, status)
                    continue

                if _cursor_selection_available():
                    selected = _select_resume_candidate_with_cursor(resume_candidates)
                    if selected is None:
                        resume_candidates = []
                        status = "Resume canceled."
                        _render_screen(messages, session_display_path, status)
                        continue

                    _save_messages(session, messages)
                    try:
                        session, messages = _load_resume_messages(
                            root=root,
                            session_ref=selected.session_id,
                            retention_days=args.resume_days,
                        )
                        session_display_path = _display_path(session.path, root)
                        status = f"Resumed session: {session.path.name}"
                        has_new_messages = False
                    except (FileNotFoundError, ValueError) as exc:
                        status = f"Could not resume: {exc}"
                    resume_candidates = []
                    _render_screen(messages, session_display_path, status)
                    continue

                status = _format_resume_list(resume_candidates)
                _render_screen(messages, session_display_path, status)
                continue

            if resume_candidates and normalized == "/cancel":
                _debug_log(root, "main.command_resume_cancel")
                resume_candidates = []
                status = "Resume canceled."
                _render_screen(messages, session_display_path, status)
                continue

            if resume_candidates and message.strip().isdigit():
                selected_index = int(message.strip()) - 1
                if not 0 <= selected_index < len(resume_candidates):
                    status = f"Enter a number from 1 to {len(resume_candidates)}, or /cancel."
                    _render_screen(messages, session_display_path, status)
                    continue

                _save_messages(session, messages)
                selected = resume_candidates[selected_index]
                try:
                    session, messages = _load_resume_messages(
                        root=root,
                        session_ref=selected.session_id,
                        retention_days=args.resume_days,
                    )
                    session_display_path = _display_path(session.path, root)
                    status = f"Resumed session: {session.path.name}"
                    resume_candidates = []
                    has_new_messages = False
                except (FileNotFoundError, ValueError) as exc:
                    status = f"Could not resume: {exc}"
                _render_screen(messages, session_display_path, status)
                continue

            if normalized.startswith("/resume "):
                session_ref = message.strip().split(maxsplit=1)[1]
                _debug_log(root, f"main.command_resume_ref ref={session_ref}")
                _save_messages(session, messages)
                try:
                    session, messages = _load_resume_messages(
                        root=root,
                        session_ref=session_ref,
                        retention_days=args.resume_days,
                    )
                    session_display_path = _display_path(session.path, root)
                    status = f"Resumed session: {session.path.name}"
                    resume_candidates = []
                    has_new_messages = False
                except (FileNotFoundError, ValueError) as exc:
                    status = f"Could not resume: {exc}"
                _render_screen(messages, session_display_path, status)
                continue

            if resume_candidates:
                status = f"Enter a number from 1 to {len(resume_candidates)}, or /cancel."
                _render_screen(messages, session_display_path, status)
                continue

            if not message.strip():
                status = "Empty messages are not saved."
                _render_screen(messages, session_display_path, status)
                continue

            messages.append(create_live_message("user", message))
            has_new_messages = True
            session.write_messages(messages)
            saved = True
            resume_candidates = []
            _debug_log(root, f"main.user_saved messages={len(messages)} path={session.path}")

            if args.no_ai:
                status = "Saved user message. --no-ai is active."
                _render_screen(messages, session_display_path, status)
                continue

            status = "Waiting for Codex reply..."
            _render_screen(messages, session_display_path, status)

            try:
                reply_result = generate_assistant_reply_with_context(
                    root=root,
                    messages=messages,
                    codex_command=args.codex_command,
                    sandbox=args.codex_sandbox,
                    approval=args.codex_approval,
                    model=args.chat_codex_model,
                    reasoning_effort=args.chat_codex_reasoning_effort,
                    service_tier=args.chat_codex_service_tier,
                    fast_mode=not args.no_chat_codex_fast_mode,
                    max_context_messages=args.max_context_messages,
                    include_memory_context=not args.no_memory_context,
                )
                reply = reply_result.reply
            except RuntimeError as exc:
                _debug_log(root, f"main.assistant_reply_failed type={type(exc).__name__} message={exc}")
                status = f"Codex reply failed: {exc}"
                _render_screen(messages, session_display_path, status)
                continue

            messages.append(create_live_message("assistant", reply))
            session.write_messages(messages)
            _debug_log(root, f"main.assistant_saved messages={len(messages)} path={session.path}")
            status = "Saved assistant reply.\n" + _format_memory_context_status(reply_result.memory_context)
            _render_screen(messages, session_display_path, status)
    except (KeyboardInterrupt, EOFError):
        _debug_log(root, f"main.input_interrupted type={sys.exc_info()[0].__name__ if sys.exc_info()[0] else 'unknown'}")
        saved, status, finish_exit_code = finish_session_for_exit(
            root=root,
            session=session,
            messages=messages,
            has_new_messages=has_new_messages,
            finalize_on_exit=not args.no_finalize_on_exit,
            process_on_exit=not args.no_process_on_exit and not args.no_ai,
            commit_on_exit=args.commit_on_exit,
            codex_command=args.codex_command,
            codex_approval=args.codex_approval,
            show_progress=not args.no_exit_progress,
        )
        if finish_exit_code:
            exit_code = finish_exit_code
        _render_screen(messages, session_display_path, status)

    if saved:
        print(f"Log: {session_display_path}")
    else:
        print("No log file was written.")
    _write_exit_marker()
    _debug_log(root, f"main.exit code={exit_code} saved={saved} status={status!r}")
    return exit_code


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrupted. Live log was saved if a session had messages.")
        _debug_log(None, "top_level.keyboard_interrupt")
        _write_exit_marker()
        raise SystemExit(0)
    except Exception:
        _debug_log(None, "top_level.unhandled_exception\n" + traceback.format_exc())
        raise
