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
import re
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Callable

from codex_cli_options import add_codex_model_options
from build_answer_context import (
    AnswerContext,
    MemoryContextReference,
    RetrievalHealth,
    build_answer_context,
)
from finalize_live_chat import FinalizeLiveChatResult, finalize_live_chat
from live_session import ROOT, LiveMessage, LiveSession, create_live_message, create_live_session
from personalization_settings import (
    SessionPersonalization,
    load_personalization_settings,
    load_session_personalization,
    update_session_personalization,
)
from session_store import ResumeSession, list_resumable_sessions, load_resume_session


if not sys.stdout.isatty() and hasattr(sys.stdout, "reconfigure"):
    # Keep redirected CLI output compatible with callers that explicitly read it as UTF-8.
    sys.stdout.reconfigure(encoding="utf-8")


DEBUG_LOG_ENV = "AI_LIFEOS_DEBUG_LOG"
DEFAULT_CHAT_CODEX_MODEL = "gpt-5.6-luna"
DEFAULT_CHAT_CODEX_REASONING_EFFORT = "medium"
DEFAULT_CHAT_CODEX_SERVICE_TIER: str | None = None
DEFAULT_CHAT_CODEX_FAST_MODE = False
MEMORY_MCP_SERVER_NAME = "ai_lifeos_memory"
MEMORY_MCP_TOOLS = (
    "search_past_chats",
    "open_conversation",
    "get_personal_memory",
    "get_index_health",
)
MCP_SERVER_NAME_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
MCP_LIST_TIMEOUT_SECONDS = 10
APP_SERVER_INTERRUPT_TIMEOUT_SECONDS = 5.0
CODEX_TOOL_ISOLATION_CONFIG = (
    "web_search=\"disabled\"",
    "allow_login_shell=false",
    "shell_environment_policy.inherit=\"none\"",
    "shell_environment_policy.ignore_default_excludes=false",
    "features.shell_tool=false",
    "features.shell_snapshot=false",
    "features.apps=false",
    "features.plugins=false",
    "features.remote_plugin=false",
    "features.multi_agent=false",
    "features.goals=false",
    "features.browser_use=false",
    "features.browser_use_external=false",
    "features.browser_use_full_cdp_access=false",
    "features.computer_use=false",
    "features.in_app_browser=false",
    "features.image_generation=false",
    "features.code_mode_host=false",
    "features.hooks=false",
    "features.skill_mcp_dependency_install=false",
    "features.tool_call_mcp_elicitation=false",
    "features.request_permissions_tool=false",
)


@dataclass(frozen=True)
class AssistantReplyResult:
    reply: str
    memory_context: AnswerContext | None
    memory_candidates: tuple[MemoryContextReference, ...] = ()
    memory_opened: tuple[MemoryContextReference, ...] = ()


@dataclass(frozen=True)
class MemoryMCPTrace:
    """Separate search candidates from source content actually opened for the model."""

    candidates: tuple[MemoryContextReference, ...] = ()
    opened: tuple[MemoryContextReference, ...] = ()


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
        help=argparse.SUPPRESS,
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
        help="Codex service tier for chat replies. The default leaves the tier unspecified.",
    )
    parser.add_argument(
        "--chat-codex-fast-mode",
        dest="chat_codex_fast_mode",
        action="store_true",
        help="Pass features.fast_mode=true for chat replies.",
    )
    parser.add_argument(
        "--no-chat-codex-fast-mode",
        dest="chat_codex_fast_mode",
        action="store_false",
        help=argparse.SUPPRESS,
    )
    parser.set_defaults(chat_codex_fast_mode=DEFAULT_CHAT_CODEX_FAST_MODE)
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
        help="Disable both injected AI-LifeOS memory context and read-only Memory MCP for chat replies.",
    )
    parser.add_argument(
        "--no-memory-mcp",
        action="store_true",
        help="Do not expose the read-only AI-LifeOS memory MCP tools to Codex.",
    )
    parser.add_argument(
        "--temporary",
        action="store_true",
        help="Keep only the live log; disable memory retrieval and exclude this session from archive/memory processing.",
    )
    parser.add_argument(
        "--project-scope",
        help="Constrain this session's memory retrieval to one project scope.",
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


def _load_session_personalization_fail_closed(
    root: Path | str,
    session_file: Path | str,
) -> SessionPersonalization:
    """Load effective controls without exposing memory when private metadata is invalid."""

    try:
        return load_session_personalization(root=root, session_file=session_file)
    except (OSError, ValueError) as exc:
        _debug_log(
            root,
            f"personalization.load_failed type={type(exc).__name__}",
        )
        return SessionPersonalization(
            temporary=True,
            temporary_locked=True,
            exclude_from_memory=True,
            memory_enabled=False,
            past_chat_search_enabled=False,
            project_scope=None,
            explicitly_configured=False,
        )


def _sanitized_argv_for_debug(argv: list[str] | tuple[str, ...]) -> list[str]:
    """Redact private project-scope values before writing process arguments to logs."""

    sanitized: list[str] = []
    redact_next = False
    for raw_value in argv:
        value = str(raw_value)
        if redact_next:
            sanitized.append("<redacted>")
            redact_next = False
            continue
        if value == "--project-scope":
            sanitized.append(value)
            redact_next = True
            continue
        if value.startswith("--project-scope="):
            sanitized.append("--project-scope=<redacted>")
            continue
        sanitized.append(value)
    return sanitized


def build_codex_chat_prompt(
    messages: list[LiveMessage],
    max_context_messages: int = 20,
    memory_context: str = "",
    memory_tools_enabled: bool = False,
    personal_memory_tool_enabled: bool = True,
    project_scope: str | None = None,
) -> str:
    recent_messages = messages[-max(max_context_messages, 1) :]
    transcript_lines = []
    for message in recent_messages:
        label = "User" if message.role == "user" else "Assistant"
        transcript_lines.extend([f"{label}:", message.content, ""])

    lines = [
        "You are the AI-LifeOS conversation assistant.",
        "Reply conversationally to the latest user message.",
        "Do not edit files, run shell commands, commit changes, or update memory/journal. "
        "The explicitly listed read-only AI-LifeOS MCP calls are allowed.",
        "The application has already saved the user message to the live JSONL log.",
        "Use the transcript and any read-only memory context below, then return only the assistant reply.",
        "",
    ]
    if memory_context.strip() or memory_tools_enabled:
        lines.extend(
            [
                "Memory-grounding rules:",
                "- Treat the memory context as evidence, not as a suggestion.",
                "- When answering about the user's past conversations, opinions, experiences, preferences, "
                "or decisions, state only claims supported by that context.",
                "- Do not fill gaps with general knowledge, plausible inference, or invented recollections.",
                "- Do not reverse, soften, or strengthen a stored claim. Preserve the meaning of the source.",
                "- For a question asking what the user previously said, thought, or felt, give a concise "
                "source-grounded summary. Mention the source date or title naturally when it helps establish "
                "the basis of the answer.",
                "- If the context does not support a specific answer, say that the stored records do not "
                "confirm it. Do not guess.",
            ]
        )
        if memory_tools_enabled:
            lines.extend(
                [
                    "- Read-only AI-LifeOS memory MCP tools are available under ai_lifeos_memory.",
                    "- For an explicit request to recall a past conversation, opinion, experience, preference, "
                    "or decision, call search_past_chats when the supplied context is missing or insufficient.",
                    "- A zero-result first search is not proof that no record exists. Retry with short entity names, "
                    "distinctive people/character names, or another faithful query variant, then inspect promising "
                    "sources with open_conversation.",
                    "- Use get_index_health when stale or incomplete indexing may explain missing results.",
                    "- Never use these tools to write, organize, or delete stored data.",
                ]
            )
            if personal_memory_tool_enabled:
                lines.append("- Use get_personal_memory only for stable preferences or projects.")
            if project_scope:
                lines.append(
                    f"- The active project scope is {project_scope!r}. Pass that scope to memory tools and do not "
                    "use results from unrelated projects. If no scoped evidence exists, say so instead of widening it."
                )
        if memory_context.strip():
            lines.extend(
                [
                    "",
                    "Memory Context:",
                    "",
                    memory_context.strip(),
                ]
            )
        lines.append("")
    lines.extend(["Transcript:", "", *transcript_lines])
    return "\n".join(lines).rstrip()


def _memory_mcp_config_options(
    root: Path | str,
    *,
    include_personal_memory: bool = True,
    project_scope: str | None = None,
    exclude_live_session: Path | str | None = None,
) -> list[str]:
    """Return per-process Codex config overrides for the local read-only MCP server."""

    resolved_root = Path(root).resolve()
    server_script = Path(__file__).resolve().with_name("memory_mcp_server.py")
    server_args = [str(server_script), "--root", str(resolved_root)]
    if project_scope:
        server_args.extend(["--project-scope", project_scope])
    if exclude_live_session is not None:
        server_args.extend(["--exclude-live-session", str(exclude_live_session)])
    prefix = f"mcp_servers.{MEMORY_MCP_SERVER_NAME}"
    enabled_tools = _enabled_memory_mcp_tools(include_personal_memory)
    values = (
        f"{prefix}.command={json.dumps(sys.executable, ensure_ascii=False)}",
        f"{prefix}.args={json.dumps(server_args, ensure_ascii=False)}",
        f"{prefix}.enabled=true",
        # A missing scoped-memory server must fail the answer instead of silently
        # widening the model to ambient tools or producing an ungrounded reply.
        f"{prefix}.required=true",
        f"{prefix}.startup_timeout_sec=10.0",
        f"{prefix}.tool_timeout_sec=30.0",
        f"{prefix}.enabled_tools={json.dumps(enabled_tools, ensure_ascii=False)}",
    )
    return _codex_config_options(values)


def _enabled_memory_mcp_tools(include_personal_memory: bool) -> list[str]:
    return [
        tool
        for tool in MEMORY_MCP_TOOLS
        if include_personal_memory or tool != "get_personal_memory"
    ]


def _codex_config_options(values: tuple[str, ...] | list[str]) -> list[str]:
    options: list[str] = []
    for value in values:
        options.extend(["-c", value])
    return options


def _codex_tool_isolation_options(configured_mcp_names: tuple[str, ...]) -> list[str]:
    """Disable every resolved ambient tool surface before enabling the scoped server."""

    names = tuple(dict.fromkeys(configured_mcp_names))
    values = [*CODEX_TOOL_ISOLATION_CONFIG]
    values.extend(f"mcp_servers.{name}.enabled=false" for name in names)
    return _codex_config_options(values)


def _list_configured_mcp_server_names(
    codex_command: str,
    root: Path | str,
    run_command=subprocess.run,
) -> tuple[str, ...]:
    """Read only the resolved MCP names and fail closed on ambiguous inventory."""

    # Remove plugin/app-derived servers before listing. Creating a standalone
    # `mcp_servers.<plugin-tool>` override after plugins are disabled would form
    # an incomplete transport table on current Codex builds.
    command = [
        codex_command,
        "mcp",
        "list",
        "--json",
        "-c",
        "features.plugins=false",
        "-c",
        "features.apps=false",
        "-c",
        "features.remote_plugin=false",
    ]
    try:
        completed = run_command(
            command,
            cwd=Path(root),
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=MCP_LIST_TIMEOUT_SECONDS,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("Codex MCP inventory could not be verified safely.") from exc
    if completed.returncode != 0:
        raise RuntimeError("Codex MCP inventory could not be verified safely.")

    output = getattr(completed, "stdout", "") or ""
    if not isinstance(output, str) or len(output) > 1_000_000:
        raise RuntimeError("Codex MCP inventory could not be verified safely.")
    try:
        payload = json.loads(output.lstrip("\ufeff"))
    except (json.JSONDecodeError, TypeError) as exc:
        raise RuntimeError("Codex MCP inventory could not be verified safely.") from exc
    if not isinstance(payload, list) or len(payload) > 256:
        raise RuntimeError("Codex MCP inventory could not be verified safely.")

    names: list[str] = []
    for item in payload:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise RuntimeError("Codex MCP inventory could not be verified safely.")
        name = item["name"]
        if not MCP_SERVER_NAME_PATTERN.fullmatch(name):
            raise RuntimeError("Codex MCP inventory contains an unsupported server name.")
        if name not in names:
            names.append(name)
    return tuple(names)


def _effective_memory_mcp_enabled(include_memory_context: bool, enable_memory_mcp: bool | None) -> bool:
    # Legacy callers used include_memory_context=False as the complete memory-off
    # switch. A caller can opt into the newer MCP-only mode explicitly with True.
    return include_memory_context if enable_memory_mcp is None else bool(enable_memory_mcp)


def _validate_app_server_mcp_inventory(
    result: dict,
    *,
    memory_mcp_enabled: bool,
    include_personal_memory: bool,
) -> None:
    """Ensure the app-server exposed no tool server except the scoped memory MCP."""

    data = result.get("data")
    next_cursor = result.get("nextCursor")
    if not isinstance(data, list) or (next_cursor is not None and next_cursor != ""):
        raise AppServerStreamingUnavailable("Codex app-server MCP isolation could not be verified.")
    expected_exposed_names = {MEMORY_MCP_SERVER_NAME} if memory_mcp_enabled else set()
    inventory_names: set[str] = set()
    exposed_names: set[str] = set()
    for item in data:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise AppServerStreamingUnavailable("Codex app-server MCP isolation could not be verified.")
        name = item["name"]
        if not MCP_SERVER_NAME_PATTERN.fullmatch(name) or name in inventory_names:
            raise AppServerStreamingUnavailable("Codex app-server MCP isolation could not be verified.")
        inventory_names.add(name)
        tools = item.get("tools", {})
        resources = item.get("resources", [])
        templates = item.get("resourceTemplates", [])
        if not isinstance(tools, dict) or not isinstance(resources, list) or not isinstance(templates, list):
            raise AppServerStreamingUnavailable("Codex app-server MCP isolation could not be verified.")
        if tools or resources or templates:
            exposed_names.add(name)
        if name == MEMORY_MCP_SERVER_NAME and memory_mcp_enabled:
            if set(tools) != set(_enabled_memory_mcp_tools(include_personal_memory)):
                raise AppServerStreamingUnavailable("AI-LifeOS Memory MCP did not expose the expected read-only tools.")
            if resources or templates:
                raise AppServerStreamingUnavailable("AI-LifeOS Memory MCP exposed unexpected resources.")
    if exposed_names != expected_exposed_names:
        raise AppServerStreamingUnavailable("Codex app-server exposed an unexpected MCP server.")


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
    *,
    enable_memory_mcp: bool | None = None,
    include_core_memory: bool = True,
    include_past_chats: bool = True,
    project_scope: str | None = None,
    exclude_live_session: Path | str | None = None,
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
        enable_memory_mcp=enable_memory_mcp,
        include_core_memory=include_core_memory,
        include_past_chats=include_past_chats,
        project_scope=project_scope,
        exclude_live_session=exclude_live_session,
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
    *,
    enable_memory_mcp: bool | None = None,
    include_core_memory: bool = True,
    include_past_chats: bool = True,
    project_scope: str | None = None,
    exclude_live_session: Path | str | None = None,
) -> AssistantReplyResult:
    root = Path(root)
    memory_mcp_enabled = _effective_memory_mcp_enabled(include_memory_context, enable_memory_mcp)
    configured_mcp_names = _list_configured_mcp_server_names(codex_command, root)
    _debug_log(root, f"assistant_reply.start messages={len(messages)} sandbox={sandbox}")
    memory_context = ""
    memory_context_result: AnswerContext | None = None
    if include_memory_context:
        recent_user_messages = _recent_user_contents(messages)
        latest_user = recent_user_messages[-1] if recent_user_messages else ""
        memory_context_result = build_answer_context(
            root=root,
            question=latest_user,
            recent_user_messages=recent_user_messages,
            include_core_memory=include_core_memory,
            include_past_chats=include_past_chats,
            project_scope=project_scope,
            exclude_live_session=exclude_live_session,
        )
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
        memory_tools_enabled=memory_mcp_enabled,
        personal_memory_tool_enabled=include_core_memory,
        project_scope=project_scope,
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        output_file = Path(temp_dir) / "assistant_reply.md"
        command = [
            codex_command,
            "--ask-for-approval",
            approval,
            "exec",
            "--ignore-rules",
            "--ephemeral",
        ]
        add_codex_model_options(
            command,
            model=model,
            reasoning_effort=reasoning_effort,
            service_tier=service_tier,
            fast_mode=fast_mode,
        )
        command.extend(_codex_tool_isolation_options(configured_mcp_names))
        if memory_mcp_enabled:
            command.extend(
                _memory_mcp_config_options(
                    root,
                    include_personal_memory=include_core_memory,
                    project_scope=project_scope,
                    exclude_live_session=exclude_live_session,
                )
            )
        command.extend(
            [
                "-C",
                str(root),
                "--sandbox",
                sandbox,
                "--color",
                "never",
                "--json",
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
            # stdout is JSONL and can contain complete MCP results. Never surface
            # either stream through a user-visible exception.
            raise RuntimeError(f"Codex CLI failed with exit code {completed.returncode}.")

        if output_file.exists():
            reply = output_file.read_text(encoding="utf-8").strip()
        else:
            reply = (getattr(completed, "stdout", "") or "").strip()

    if not reply:
        _debug_log(root, "assistant_reply.error empty_reply")
        raise RuntimeError("Codex CLI completed but returned an empty assistant reply.")

    mcp_trace = (
        _memory_trace_from_exec_jsonl(getattr(completed, "stdout", "") or "")
        if memory_mcp_enabled
        else MemoryMCPTrace()
    )
    memory_context_result = _merge_memory_tool_references(memory_context_result, mcp_trace.opened)
    _debug_log(
        root,
        "assistant_reply.success "
        f"chars={len(reply)} mcp_candidates={len(mcp_trace.candidates)} mcp_opened={len(mcp_trace.opened)}",
    )
    return AssistantReplyResult(
        reply=reply,
        memory_context=memory_context_result,
        memory_candidates=mcp_trace.candidates,
        memory_opened=mcp_trace.opened,
    )


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
    *,
    enable_memory_mcp: bool | None = None,
    include_core_memory: bool = True,
    include_past_chats: bool = True,
    project_scope: str | None = None,
    exclude_live_session: Path | str | None = None,
) -> AssistantReplyResult:
    """Generate a reply through app-server and expose only agent-message deltas.

    The authoritative completed agent message is returned to the caller. Deltas are
    transient UI data and are never persisted by this function.
    """
    root = Path(root)
    started_at = time.perf_counter()
    memory_mcp_enabled = _effective_memory_mcp_enabled(include_memory_context, enable_memory_mcp)
    try:
        configured_mcp_names = _list_configured_mcp_server_names(codex_command, root)
    except RuntimeError as exc:
        raise AppServerStreamingUnavailable("Codex app-server MCP isolation could not be verified.") from exc

    command = [codex_command, "app-server", "--stdio"]
    if fast_mode is not None:
        command.extend(["-c", f"features.fast_mode={'true' if fast_mode else 'false'}"])
    command.extend(_codex_tool_isolation_options(configured_mcp_names))
    if memory_mcp_enabled:
        command.extend(
            _memory_mcp_config_options(
                root,
                include_personal_memory=include_core_memory,
                project_scope=project_scope,
                exclude_live_session=exclude_live_session,
            )
        )
    popen_options: dict[str, object] = {
        "cwd": root,
        "stdin": subprocess.PIPE,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "bufsize": 1,
    }
    if os.name == "nt":
        # Keep the cmd wrapper and native codex.exe in one addressable process
        # group; `_terminate_process` then kills the exact tree on fallback.
        popen_options["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    try:
        process = popen(command, **popen_options)
    except (FileNotFoundError, OSError) as exc:
        raise AppServerStreamingUnavailable("Codex app-serverを起動できませんでした。") from exc

    if process.stdin is None or process.stdout is None:
        _terminate_process(process)
        raise AppServerStreamingUnavailable("Codex app-serverの標準入出力を開けませんでした。")

    events: queue.Queue[dict | None] = queue.Queue()

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
            # Drain without retaining output: Codex/MCP diagnostics can contain
            # local paths or tool payloads and are not needed for user errors.
            for _ in process.stderr:
                pass

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
                    raise AppServerStreamingUnavailable("Codex app-server exited before responding.")
                if event.get("id") == request_id:
                    if "error" in event:
                        raise AppServerStreamingUnavailable("Codex app-server request failed.")
                    return event.get("result") or {}
                deferred.append(event)
        finally:
            for event in deferred:
                events.put(event)
        raise AppServerStreamingUnavailable("Codex app-serverの初期化がタイムアウトしました。")

    try:
        # Process startup progresses while the read-only memory context is built.
        # This removes an otherwise serial wait without changing the prompt or data flow.
        memory_context = ""
        memory_context_result: AnswerContext | None = None
        memory_started_at = time.perf_counter()
        if include_memory_context:
            recent_user_messages = _recent_user_contents(messages)
            memory_context_result = build_answer_context(
                root=root,
                question=recent_user_messages[-1] if recent_user_messages else "",
                recent_user_messages=recent_user_messages,
                include_core_memory=include_core_memory,
                include_past_chats=include_past_chats,
                project_scope=project_scope,
                exclude_live_session=exclude_live_session,
            )
            memory_context = memory_context_result.text
            _debug_log(
                root,
                "assistant_reply.streaming_memory_context "
                f"enabled={memory_context_result.used_memory} "
                f"score={memory_context_result.score}/{memory_context_result.threshold} "
                f"references={len(memory_context_result.references)} results={len(memory_context_result.results)}",
            )
        memory_elapsed_ms = round((time.perf_counter() - memory_started_at) * 1000)
        prompt = build_codex_chat_prompt(
            messages,
            max_context_messages=max_context_messages,
            memory_context=memory_context,
            memory_tools_enabled=memory_mcp_enabled,
            personal_memory_tool_enabled=include_core_memory,
            project_scope=project_scope,
        )

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
        send(
            {
                "id": 2,
                "method": "mcpServerStatus/list",
                "params": {"detail": "toolsAndAuthOnly", "limit": 1_000},
            }
        )
        inventory_result = wait_response(2, timeout_seconds=30.0)
        _validate_app_server_mcp_inventory(
            inventory_result,
            memory_mcp_enabled=memory_mcp_enabled,
            include_personal_memory=include_core_memory,
        )
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
        send({"id": 3, "method": "thread/start", "params": thread_params})
        thread_result = wait_response(3)
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
        turn_started_at = time.perf_counter()
        send({"id": 4, "method": "turn/start", "params": turn_params})
        turn_result = wait_response(4, timeout_seconds=30.0)
        turn_id = str((turn_result.get("turn") or {}).get("id") or "")
        if not turn_id:
            raise AppServerStreamingUnavailable("Codex app-serverがturn idを返しませんでした。")

        final_reply = ""
        mcp_candidates: list[MemoryContextReference] = []
        mcp_opened: list[MemoryContextReference] = []
        interrupt_sent = False
        interrupt_deadline: float | None = None
        first_delta_logged = False
        while True:
            if is_cancelled and is_cancelled() and not interrupt_sent:
                send(
                    {
                        "id": 5,
                        "method": "turn/interrupt",
                        "params": {"threadId": thread_id, "turnId": turn_id},
                    }
                )
                interrupt_sent = True
                interrupt_deadline = time.monotonic() + APP_SERVER_INTERRUPT_TIMEOUT_SECONDS
            if interrupt_deadline is not None and time.monotonic() >= interrupt_deadline:
                raise InterruptedError("返答生成の停止期限を超えたためCodex processを終了しました。")
            try:
                event = events.get(timeout=0.1)
            except queue.Empty:
                if process.poll() is not None:
                    raise RuntimeError("Codex app-serverが返答生成中に終了しました。")
                continue
            if event is None:
                raise RuntimeError("Codex app-server exited during reply generation.")
            method = event.get("method")
            params = event.get("params") or {}
            if method == "item/agentMessage/delta":
                delta = str(params.get("delta") or "")
                if delta and not interrupt_sent:
                    if not first_delta_logged:
                        first_delta_logged = True
                        _debug_log(
                            root,
                            "assistant_reply.streaming_first_delta "
                            f"total_ms={round((time.perf_counter() - started_at) * 1000)} "
                            f"turn_ms={round((time.perf_counter() - turn_started_at) * 1000)} "
                            f"memory_ms={memory_elapsed_ms}",
                        )
                    on_delta(delta)
            elif method == "item/completed":
                item = params.get("item") or {}
                if item.get("type") == "agentMessage":
                    final_reply = str(item.get("text") or "")
                else:
                    trace = _memory_trace_from_mcp_item(item)
                    mcp_candidates.extend(trace.candidates)
                    mcp_opened.extend(trace.opened)
            elif method == "turn/completed":
                turn = params.get("turn") or {}
                status = turn.get("status")
                if status == "interrupted" or interrupt_sent:
                    raise InterruptedError("返答生成を停止しました。")
                if status != "completed":
                    raise RuntimeError(f"Codex turn failed with status {status or 'unknown'}.")
                if not final_reply:
                    for item in turn.get("items") or []:
                        if item.get("type") == "agentMessage":
                            final_reply = str(item.get("text") or "")
                        else:
                            trace = _memory_trace_from_mcp_item(item)
                            mcp_candidates.extend(trace.candidates)
                            mcp_opened.extend(trace.opened)
                final_reply = final_reply.strip()
                if not final_reply:
                    raise RuntimeError("Codex app-server completed but returned an empty assistant reply.")
                _debug_log(
                    root,
                    "assistant_reply.streaming_success "
                    f"total_ms={round((time.perf_counter() - started_at) * 1000)} "
                    f"memory_ms={memory_elapsed_ms} chars={len(final_reply)}",
                )
                merged_context = _merge_memory_tool_references(
                    memory_context_result,
                    _dedupe_memory_references(mcp_opened),
                )
                return AssistantReplyResult(
                    reply=final_reply,
                    memory_context=merged_context,
                    memory_candidates=_dedupe_memory_references(mcp_candidates),
                    memory_opened=_dedupe_memory_references(mcp_opened),
                )
    finally:
        _terminate_process(process)


def _memory_trace_from_exec_jsonl(output: str) -> MemoryMCPTrace:
    candidates: list[MemoryContextReference] = []
    opened: list[MemoryContextReference] = []
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        item = event.get("item")
        if isinstance(item, dict):
            trace = _memory_trace_from_mcp_item(item)
            candidates.extend(trace.candidates)
            opened.extend(trace.opened)
    return MemoryMCPTrace(
        candidates=_dedupe_memory_references(candidates),
        opened=_dedupe_memory_references(opened),
    )


def _memory_references_from_exec_jsonl(output: str) -> tuple[MemoryContextReference, ...]:
    """Backward-compatible view containing only opened evidence, not search candidates."""

    return _memory_trace_from_exec_jsonl(output).opened


def _memory_trace_from_mcp_item(item: dict) -> MemoryMCPTrace:
    item_type = str(item.get("type") or "")
    if item_type not in {"mcp_tool_call", "mcpToolCall"}:
        return MemoryMCPTrace()
    if str(item.get("server") or "") != MEMORY_MCP_SERVER_NAME:
        return MemoryMCPTrace()
    if str(item.get("status") or "") not in {"completed", "success", "succeeded"}:
        return MemoryMCPTrace()

    result = item.get("result")
    if not isinstance(result, dict):
        return MemoryMCPTrace()
    data = result.get("structured_content")
    if not isinstance(data, dict):
        data = result.get("structuredContent")
    if not isinstance(data, dict):
        return MemoryMCPTrace()

    tool = str(item.get("tool") or "")
    references: list[MemoryContextReference] = []
    if tool == "search_past_chats":
        for value in data.get("results") or []:
            if not isinstance(value, dict):
                continue
            source = value.get("source") or {}
            if not isinstance(source, dict):
                continue
            reference = _memory_reference(
                path=source.get("path"),
                document_type=source.get("document_type"),
                title=source.get("title"),
                date=source.get("date"),
                snippet=value.get("excerpt"),
                score=value.get("score"),
                speaker_role=source.get("role"),
                message_number=source.get("message_number"),
            )
            if reference:
                references.append(reference)
    elif tool == "open_conversation":
        source = data.get("source") or {}
        if isinstance(source, dict):
            messages = data.get("messages")
            if isinstance(messages, list) and messages:
                for message in messages[:10]:
                    if not isinstance(message, dict):
                        continue
                    reference = _memory_reference(
                        path=source.get("path"),
                        document_type=source.get("document_type"),
                        title=source.get("title"),
                        date=source.get("date"),
                        snippet=message.get("text"),
                        score=0,
                        speaker_role=message.get("role"),
                        message_number=message.get("message_number"),
                    )
                    if reference:
                        references.append(reference)
            else:
                reference = _memory_reference(
                    path=source.get("path"),
                    document_type=source.get("document_type"),
                    title=source.get("title"),
                    date=source.get("date"),
                    snippet=data.get("content"),
                    score=0,
                )
                if reference:
                    references.append(reference)
    elif tool == "get_personal_memory":
        for source in data.get("sources") or []:
            if not isinstance(source, dict):
                continue
            reference = _memory_reference(
                path=source.get("path"),
                document_type=source.get("document_type"),
                title=source.get("title"),
                date=(source.get("metadata") or {}).get("source_date")
                if isinstance(source.get("metadata"), dict)
                else None,
                snippet=source.get("content"),
                score=0,
            )
            if reference:
                references.append(reference)
    deduped = _dedupe_memory_references(references)
    if tool == "search_past_chats":
        return MemoryMCPTrace(candidates=deduped)
    return MemoryMCPTrace(opened=deduped)


def _memory_references_from_mcp_item(item: dict) -> tuple[MemoryContextReference, ...]:
    """Backward-compatible view containing only content explicitly opened by a tool."""

    return _memory_trace_from_mcp_item(item).opened


def _memory_reference(
    *,
    path: object,
    document_type: object,
    title: object,
    date: object,
    snippet: object,
    score: object,
    speaker_role: object = None,
    message_number: object = None,
) -> MemoryContextReference | None:
    normalized_path = str(path or "").strip().replace("\\", "/")
    if not normalized_path:
        return None
    normalized_snippet = " ".join(str(snippet or "").split())[:800]
    try:
        normalized_score = int(score or 0)
    except (TypeError, ValueError):
        normalized_score = 0
    try:
        normalized_number = int(message_number) if message_number is not None else None
    except (TypeError, ValueError):
        normalized_number = None
    normalized_role = str(speaker_role or "").strip() or None
    return MemoryContextReference(
        path=normalized_path,
        document_type=str(document_type or "memory_mcp"),
        title=str(title or Path(normalized_path).stem),
        date=str(date) if date else None,
        snippet=normalized_snippet,
        score=normalized_score,
        speaker_role=normalized_role,
        message_number=normalized_number,
    )


def _dedupe_memory_references(
    references: list[MemoryContextReference] | tuple[MemoryContextReference, ...],
) -> tuple[MemoryContextReference, ...]:
    deduped: list[MemoryContextReference] = []
    seen: set[tuple[str, str, str | None, int | None]] = set()
    for reference in references:
        key = (
            reference.path.replace("\\", "/").lower(),
            reference.document_type,
            reference.speaker_role,
            reference.message_number,
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(reference)
        if len(deduped) >= 20:
            break
    return tuple(deduped)


def _merge_memory_tool_references(
    context: AnswerContext | None,
    tool_references: tuple[MemoryContextReference, ...],
) -> AnswerContext | None:
    if not tool_references:
        return context
    existing = context.references if context else ()
    references = _dedupe_memory_references([*existing, *tool_references])
    mcp_only = [reference for reference in references if reference not in existing]
    core_hits = sum(reference.document_type in {"memory", "memory_item"} for reference in mcp_only)
    past_hits = len(mcp_only) - core_hits
    if context is None:
        return AnswerContext(
            should_use_memory=True,
            text="Read-only AI-LifeOS Memory MCP evidence was used during answer generation.",
            results=(),
            references=references,
            reasons=("agentic-memory-mcp",),
            retrieval_modes=("mcp",),
            retrieval_health=RetrievalHealth(
                retrieval_depth="agentic",
                core_enabled=False,
                past_chats_enabled=True,
                core_reference_count=core_hits,
                past_chat_hit_count=past_hits,
            ),
        )
    health = replace(
        context.retrieval_health,
        retrieval_depth="agentic",
        core_reference_count=context.retrieval_health.core_reference_count + core_hits,
        past_chat_hit_count=context.retrieval_health.past_chat_hit_count + past_hits,
    )
    return replace(
        context,
        should_use_memory=True,
        text=context.text or "Read-only AI-LifeOS Memory MCP evidence was used during answer generation.",
        references=references,
        reasons=tuple(dict.fromkeys((*context.reasons, "agentic-memory-mcp"))),
        retrieval_modes=tuple(dict.fromkeys((*context.retrieval_modes, "mcp"))),
        retrieval_health=health,
    )


def _terminate_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return

    pid = getattr(process, "pid", None)
    if os.name == "nt" and isinstance(pid, int) and pid > 0:
        try:
            completed = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
            if completed.returncode == 0:
                process.wait(timeout=5)
                return
        except (OSError, subprocess.SubprocessError):
            pass

    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)


def _latest_user_content(messages: list[LiveMessage]) -> str:
    recent = _recent_user_contents(messages, limit=1)
    return recent[-1] if recent else ""


def _recent_user_contents(messages: list[LiveMessage], limit: int = 2) -> tuple[str, ...]:
    contents = [message.content for message in messages if message.role == "user" and message.content.strip()]
    return tuple(contents[-max(limit, 1) :])


def _display_path(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _format_memory_context_status(context: AnswerContext | None) -> str:
    if context is None or not context.used_memory:
        if context is None:
            return "記憶参照: なし"
        return (
            f"記憶参照: なし (depth score {context.score}; "
            f"deep-search基準 {context.threshold})"
        )

    paths = [reference.path for reference in context.references]
    preview = ", ".join(paths[:3])
    if len(paths) > 3:
        preview += f", 他{len(paths) - 3}件"
    return (
        f"記憶参照: あり ({len(paths)}件, depth score {context.score}; "
        f"deep-search基準 {context.threshold})"
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


def _load_resume_messages(root: Path, session_ref: str) -> tuple[LiveSession, list[LiveMessage]]:
    summary, records = load_resume_session(root=root, session_ref=session_ref)
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


def _resume_candidates(root: Path) -> list[ResumeSession]:
    return list_resumable_sessions(root=root, limit=10)


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
    *,
    exclude_from_memory: bool = False,
) -> tuple[bool, str, FinalizeLiveChatResult | None]:
    root = Path(root)
    _debug_log(
        root,
        "finish_session.start "
        f"messages={len(messages)} has_new={has_new_messages} finalize={finalize_on_exit} "
        f"process={process_on_exit} commit={commit_on_exit} exclude={exclude_from_memory} session={session.path}",
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

    if exclude_from_memory:
        if progress:
            progress(100, "Saved temporary live log without memory processing.")
        _debug_log(root, "finish_session.memory_excluded")
        return (
            True,
            f"Saved {len(messages)} messages as a temporary live log; skipped archive and memory processing.",
            None,
        )

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
    *,
    exclude_from_memory: bool = False,
) -> tuple[bool, str, int]:
    root = Path(root)
    _debug_log(
        root,
        "finish_session_for_exit.start "
        f"messages={len(messages)} has_new={has_new_messages} finalize={finalize_on_exit} "
        f"process={process_on_exit} commit={commit_on_exit} exclude={exclude_from_memory} progress={show_progress}",
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
                exclude_from_memory=exclude_from_memory,
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
    _debug_log(root, f"main.start argv={_sanitized_argv_for_debug(sys.argv[1:])}")

    try:
        if args.resume:
            session, messages = _load_resume_messages(root=root, session_ref=args.resume)
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

    try:
        if not args.resume:
            defaults = load_personalization_settings(root)
            overrides: dict[str, object] = {
                "temporary": bool(args.temporary),
                "memory_enabled": defaults.memory_enabled,
                "past_chat_search_enabled": defaults.past_chat_search_enabled,
                "project_scope": args.project_scope if args.project_scope is not None else defaults.project_scope,
            }
            update_session_personalization(root, session.path, **overrides)
        elif args.temporary or args.project_scope is not None:
            # Resumed sessions keep their snapshot unless the user supplied an
            # explicit per-session override on this invocation.
            overrides = {}
            if args.temporary:
                overrides["temporary"] = True
            if args.project_scope is not None:
                overrides["project_scope"] = args.project_scope
            update_session_personalization(root, session.path, **overrides)
    except (OSError, ValueError) as exc:
        _debug_log(root, f"main.personalization_error type={type(exc).__name__}")
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
                personalization = _load_session_personalization_fail_closed(root, session.path)
                saved, status, finish_exit_code = finish_session_for_exit(
                    root=root,
                    session=session,
                    messages=messages,
                    has_new_messages=has_new_messages,
                    finalize_on_exit=not args.no_finalize_on_exit,
                    process_on_exit=not args.no_process_on_exit and not args.no_ai,
                    commit_on_exit=args.commit_on_exit,
                    exclude_from_memory=personalization.exclude_from_memory,
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
                resume_candidates = _resume_candidates(root=root)
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
                personalization = _load_session_personalization_fail_closed(root, session.path)
                reply_result = generate_assistant_reply_with_context(
                    root=root,
                    messages=messages,
                    codex_command=args.codex_command,
                    sandbox=args.codex_sandbox,
                    approval=args.codex_approval,
                    model=args.chat_codex_model,
                    reasoning_effort=args.chat_codex_reasoning_effort,
                    service_tier=args.chat_codex_service_tier,
                    fast_mode=args.chat_codex_fast_mode,
                    max_context_messages=args.max_context_messages,
                    include_memory_context=(
                        not args.no_memory_context
                        and (personalization.memory_enabled or personalization.past_chat_search_enabled)
                    ),
                    enable_memory_mcp=(
                        not args.no_memory_context
                        and not args.no_memory_mcp
                        and personalization.past_chat_search_enabled
                    ),
                    include_core_memory=personalization.memory_enabled,
                    include_past_chats=personalization.past_chat_search_enabled,
                    project_scope=personalization.project_scope,
                    exclude_live_session=session.path,
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
        personalization = _load_session_personalization_fail_closed(root, session.path)
        saved, status, finish_exit_code = finish_session_for_exit(
            root=root,
            session=session,
            messages=messages,
            has_new_messages=has_new_messages,
            finalize_on_exit=not args.no_finalize_on_exit,
            process_on_exit=not args.no_process_on_exit and not args.no_ai,
            commit_on_exit=args.commit_on_exit,
            exclude_from_memory=personalization.exclude_from_memory,
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
