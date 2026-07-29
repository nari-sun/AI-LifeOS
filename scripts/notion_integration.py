import argparse
import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlparse


NOTION_MCP_SERVER_NAME = "ai_lifeos_notion"
NOTION_MCP_URL = "https://mcp.notion.com/mcp"
NOTION_MCP_REMOTE_PACKAGE = "mcp-remote@0.1.38"
NOTION_MCP_AUTH_DIR_NAME = "ai-lifeos-notion"
NOTION_MCP_READ_TOOLS = (
    "fetch",
    "notion-fetch",
    "notion-query-data-sources",
    "notion-query-database-view",
)
NOTION_MCP_FETCH_TOOLS = ("fetch", "notion-fetch")
MCP_SERVER_NAME_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
MCP_LIST_TIMEOUT_SECONDS = 10
MCP_PROBE_TIMEOUT_SECONDS = 30.0
_SUCCESS_STATUSES = {"completed", "success", "succeeded"}
_NOTION_URL_PATTERN = re.compile(r"https://(?:www\.)?notion\.(?:so|com)/[^\s\"'<>]+", re.IGNORECASE)
_NOTION_ID_PATTERN = re.compile(r"(?<![0-9a-f])([0-9a-f]{32}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})(?![0-9a-f])", re.IGNORECASE)


class NotionIntegrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class NotionSource:
    id: str
    object_type: str
    title: str
    url: str
    row_count: int = 0
    representative_titles: tuple[str, ...] = ()


@dataclass(frozen=True)
class NotionContextResult:
    requested: bool
    used: bool
    status: str
    fetched_at: str | None
    sources: tuple[NotionSource, ...] = ()
    error: str | None = None


@dataclass(frozen=True)
class NotionMCPTrace:
    attempted: bool = False
    successful_calls: int = 0
    failed_calls: int = 0
    sources: tuple[NotionSource, ...] = ()
    row_urls: tuple[str, ...] = ()


def notion_mcp_auth_dir() -> Path:
    return Path.home() / ".mcp-auth" / NOTION_MCP_AUTH_DIR_NAME


def notion_mcp_config_values() -> tuple[str, ...]:
    prefix = f"mcp_servers.{NOTION_MCP_SERVER_NAME}"
    npx_command = "npx.cmd" if os.name == "nt" else "npx"
    args = [
        "-y",
        NOTION_MCP_REMOTE_PACKAGE,
        NOTION_MCP_URL,
        "--transport",
        "http-only",
        "--auth-timeout",
        "180",
        "--silent",
    ]
    return (
        f"{prefix}.command={json.dumps(npx_command)}",
        f"{prefix}.args={json.dumps(args)}",
        f"{prefix}.env.MCP_REMOTE_CONFIG_DIR={json.dumps(str(notion_mcp_auth_dir()))}",
        f"{prefix}.enabled=true",
        f"{prefix}.required=true",
        f"{prefix}.startup_timeout_sec=45.0",
        f"{prefix}.tool_timeout_sec=45.0",
        f"{prefix}.enabled_tools={json.dumps(list(NOTION_MCP_READ_TOOLS))}",
        f'{prefix}.default_tools_approval_mode="approve"',
    )


def validate_notion_inventory_item(item: dict[str, Any]) -> tuple[str, ...]:
    if not isinstance(item, dict) or item.get("name") != NOTION_MCP_SERVER_NAME:
        raise NotionIntegrationError("Notion MCP server inventory is missing.")
    tools = item.get("tools")
    if not isinstance(tools, dict):
        raise NotionIntegrationError("Notion MCP tool inventory is unavailable.")
    names = tuple(tools)
    if not set(NOTION_MCP_FETCH_TOOLS).intersection(names):
        raise NotionIntegrationError("Notion MCP did not expose the required fetch tool.")
    unexpected = set(names).difference(NOTION_MCP_READ_TOOLS)
    if unexpected:
        raise NotionIntegrationError("Notion MCP exposed a tool outside the read-only allowlist.")
    resources = item.get("resources", [])
    templates = item.get("resourceTemplates", item.get("resource_templates", []))
    if resources or templates:
        raise NotionIntegrationError("Notion MCP exposed unsupported resources.")
    return names


def notion_context_from_exec_jsonl(output: str, *, requested: bool) -> NotionContextResult | None:
    if not requested:
        return None
    traces: list[NotionMCPTrace] = []
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        item = event.get("item")
        if isinstance(item, dict):
            traces.append(notion_trace_from_mcp_item(item))
    return notion_context_from_traces(traces, requested=True)


def notion_context_from_traces(
    traces: Iterable[NotionMCPTrace],
    *,
    requested: bool,
) -> NotionContextResult | None:
    if not requested:
        return None
    merged = merge_notion_traces(traces)
    now = _now_iso()
    if not merged.attempted:
        return NotionContextResult(
            requested=True,
            used=False,
            status="not_used",
            fetched_at=None,
            error="Notion MCPは公開されましたが、この回答では読み取りtoolを使用しませんでした。",
        )
    used = merged.successful_calls > 0
    if merged.failed_calls and used:
        return NotionContextResult(
            requested=True,
            used=True,
            status="partial",
            fetched_at=now,
            sources=merged.sources,
            error="一部のNotion読み取りtool callが失敗しました。失敗した本文は利用していません。",
        )
    if merged.failed_calls:
        return NotionContextResult(
            requested=True,
            used=False,
            status="error",
            fetched_at=None,
            error="Notionを参照できませんでした。認証、接続、timeout、rate limitを確認してください。",
        )
    return NotionContextResult(
        requested=True,
        used=used,
        status="ok" if used else "not_used",
        fetched_at=now if used else None,
        sources=merged.sources,
        error=None if used else "Notionの読み取り結果は利用されませんでした。",
    )


def notion_error_context(message: str | None = None) -> NotionContextResult:
    return NotionContextResult(
        requested=True,
        used=False,
        status="error",
        fetched_at=None,
        error=message or "Notion MCPへ接続できませんでした。OAuth認証と接続状態を確認してください。",
    )


def notion_trace_from_mcp_item(item: dict[str, Any]) -> NotionMCPTrace:
    item_type = str(item.get("type") or "")
    if item_type not in {"mcp_tool_call", "mcpToolCall"}:
        return NotionMCPTrace()
    if str(item.get("server") or "") != NOTION_MCP_SERVER_NAME:
        return NotionMCPTrace()

    tool = str(item.get("tool") or "")
    if tool not in NOTION_MCP_READ_TOOLS:
        return NotionMCPTrace(attempted=True, failed_calls=1)
    success = str(item.get("status") or "") in _SUCCESS_STATUSES
    if not success:
        return NotionMCPTrace(attempted=True, failed_calls=1)

    arguments = _tool_arguments(item)
    result = item.get("result")
    if tool in {"notion-query-data-sources", "notion-query-database-view"}:
        sources = _query_sources(arguments, result)
        row_urls = _result_row_urls(result)
    else:
        sources = _fetch_sources(arguments, result)
        row_urls = ()
    return NotionMCPTrace(
        attempted=True,
        successful_calls=1,
        sources=sources,
        row_urls=row_urls,
    )


def merge_notion_traces(traces: Iterable[NotionMCPTrace]) -> NotionMCPTrace:
    values = tuple(traces)
    attempted = any(trace.attempted for trace in values)
    successful_calls = sum(trace.successful_calls for trace in values)
    failed_calls = sum(trace.failed_calls for trace in values)
    row_urls = {_normalize_url(url) for trace in values for url in trace.row_urls if _normalize_url(url)}
    all_sources = [source for trace in values for source in trace.sources]

    databases: list[NotionSource] = []
    pages: list[NotionSource] = []
    for source in all_sources:
        if source.object_type in {"database", "data_source"}:
            databases.append(source)
        elif _normalize_url(source.url) not in row_urls:
            pages.append(source)
    sources = _dedupe_sources([*databases, *pages])
    return NotionMCPTrace(
        attempted=attempted,
        successful_calls=successful_calls,
        failed_calls=failed_calls,
        sources=sources,
        row_urls=tuple(sorted(row_urls)),
    )


def get_notion_connection_view(
    root: Path | str,
    *,
    refresh: bool = False,
    codex_command: str = "codex.cmd",
    run_command: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    popen: Callable[..., subprocess.Popen[str]] = subprocess.Popen,
) -> dict[str, Any]:
    root = Path(root)
    connection: dict[str, Any] = {
        "configured": True,
        "connected": False,
        "status": "not_checked",
        "auth_status": None,
        "workspace_name": None,
        "user_name": None,
        "tools": [],
        "error": None,
    }
    if refresh:
        try:
            item = _probe_notion_inventory(root, codex_command, run_command=run_command, popen=popen)
            tools = validate_notion_inventory_item(item)
            connection.update(
                {
                    "connected": True,
                    "status": "connected",
                    "auth_status": item.get("authStatus", item.get("auth_status"))
                    or "mcp_remote_oauth",
                    "workspace_name": item.get("_workspace_name"),
                    "user_name": item.get("_user_name"),
                    "tools": list(tools),
                }
            )
        except Exception:
            connection.update(
                {
                    "status": "connection_error",
                    "error": "Notion MCPへ接続できませんでした。`python scripts\\notion_integration.py login`でOAuth認証し、再確認してください。",
                }
            )
    return {
        "ok": True,
        "server_name": NOTION_MCP_SERVER_NAME,
        "endpoint": NOTION_MCP_URL,
        "connection": connection,
        "commands": {
            "login": "python scripts\\notion_integration.py login",
            "logout": "python scripts\\notion_integration.py logout",
        },
        "storage_policy": {
            "oauth_credential": "mcp_remote_user_profile",
            "mcp_response": "ephemeral_only",
            "source_metadata": "response_only",
            "assistant_reply": "saved_in_live_conversation",
        },
    }


def _probe_notion_inventory(
    root: Path,
    codex_command: str,
    *,
    run_command: Callable[..., subprocess.CompletedProcess[str]],
    popen: Callable[..., subprocess.Popen[str]],
) -> dict[str, Any]:
    names = _configured_mcp_names(root, codex_command, run_command=run_command)
    command = [codex_command, "app-server", "--stdio"]
    for value in (
        "features.plugins=false",
        "features.apps=false",
        "features.remote_plugin=false",
        "features.shell_tool=false",
        'web_search="disabled"',
        *(f"mcp_servers.{name}.enabled=false" for name in names),
        *notion_mcp_config_values(),
    ):
        command.extend(["-c", value])
    options: dict[str, Any] = {
        "cwd": root,
        "stdin": subprocess.PIPE,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.DEVNULL,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "bufsize": 1,
    }
    if os.name == "nt":
        options["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    process = popen(command, **options)
    if process.stdin is None or process.stdout is None:
        _terminate_process(process)
        raise NotionIntegrationError("Codex app-server stdio is unavailable.")
    events: queue.Queue[dict[str, Any] | None] = queue.Queue()

    def read_stdout() -> None:
        try:
            for raw_line in process.stdout:
                try:
                    value = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    events.put(value)
        finally:
            events.put(None)

    threading.Thread(target=read_stdout, daemon=True).start()

    def send(value: dict[str, Any]) -> None:
        process.stdin.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
        process.stdin.flush()

    try:
        send(
            {
                "id": 1,
                "method": "initialize",
                "params": {
                    "clientInfo": {"name": "ai-lifeos-notion-probe", "version": "0.1.0"},
                    "capabilities": {"experimentalApi": True},
                },
            }
        )
        _wait_response(events, process, 1, MCP_PROBE_TIMEOUT_SECONDS)
        send({"method": "initialized", "params": {}})
        send(
            {
                "id": 2,
                "method": "mcpServerStatus/list",
                "params": {"detail": "toolsAndAuthOnly", "limit": 1_000},
            }
        )
        result = _wait_response(events, process, 2, MCP_PROBE_TIMEOUT_SECONDS)
        data = result.get("data")
        if not isinstance(data, list) or result.get("nextCursor") not in {None, ""}:
            raise NotionIntegrationError("Notion MCP inventory is incomplete.")
        items = [item for item in data if isinstance(item, dict) and item.get("name") == NOTION_MCP_SERVER_NAME]
        if len(items) != 1:
            raise NotionIntegrationError("Notion MCP inventory is ambiguous.")
        tool_names = validate_notion_inventory_item(items[0])
        fetch_tool = next(name for name in NOTION_MCP_FETCH_TOOLS if name in tool_names)

        send(
            {
                "id": 3,
                "method": "thread/start",
                "params": {
                    "cwd": str(root.resolve()),
                    "approvalPolicy": "never",
                    "sandbox": "read-only",
                    "ephemeral": True,
                },
            }
        )
        thread_result = _wait_response(events, process, 3, MCP_PROBE_TIMEOUT_SECONDS)
        thread_id = str((thread_result.get("thread") or {}).get("id") or "")
        if not thread_id:
            raise NotionIntegrationError("Codex app-server did not create a probe thread.")
        send(
            {
                "id": 4,
                "method": "mcpServer/tool/call",
                "params": {
                    "threadId": thread_id,
                    "server": NOTION_MCP_SERVER_NAME,
                    "tool": fetch_tool,
                    "arguments": {"id": "self"},
                },
            }
        )
        identity_result = _wait_response(events, process, 4, MCP_PROBE_TIMEOUT_SECONDS)
        if identity_result.get("isError") is True:
            raise NotionIntegrationError("Notion MCP identity probe failed.")
        workspace_name, user_name = _connection_identity(identity_result)
        items[0]["_workspace_name"] = workspace_name
        items[0]["_user_name"] = user_name
    finally:
        _terminate_process(process)
    return items[0]


def _configured_mcp_names(
    root: Path,
    codex_command: str,
    *,
    run_command: Callable[..., subprocess.CompletedProcess[str]],
) -> tuple[str, ...]:
    completed = run_command(
        [
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
        ],
        cwd=root,
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=MCP_LIST_TIMEOUT_SECONDS,
    )
    if completed.returncode != 0:
        raise NotionIntegrationError("Codex MCP inventory could not be read.")
    try:
        payload = json.loads((completed.stdout or "").lstrip("\ufeff"))
    except json.JSONDecodeError as exc:
        raise NotionIntegrationError("Codex MCP inventory was malformed.") from exc
    if not isinstance(payload, list) or len(payload) > 256:
        raise NotionIntegrationError("Codex MCP inventory was malformed.")
    names: list[str] = []
    for item in payload:
        name = item.get("name") if isinstance(item, dict) else None
        if not isinstance(name, str) or not MCP_SERVER_NAME_PATTERN.fullmatch(name):
            raise NotionIntegrationError("Codex MCP inventory was unsafe.")
        if name not in names:
            names.append(name)
    return tuple(names)


def _wait_response(
    events: queue.Queue[dict[str, Any] | None],
    process: subprocess.Popen[str],
    request_id: int,
    timeout_seconds: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            event = events.get(timeout=0.1)
        except queue.Empty:
            if process.poll() is not None:
                break
            continue
        if event is None:
            break
        if event.get("id") != request_id:
            continue
        if "error" in event:
            raise NotionIntegrationError("Codex app-server request failed.")
        result = event.get("result")
        return result if isinstance(result, dict) else {}
    raise NotionIntegrationError("Codex app-server request timed out.")


def _terminate_process(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            pass


def _tool_arguments(item: dict[str, Any]) -> dict[str, Any]:
    for key in ("arguments", "input", "args"):
        value = item.get(key)
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
    return {}


def _connection_identity(result: Any) -> tuple[str | None, str | None]:
    workspace_name: str | None = None
    user_name: str | None = None

    def bounded_name(value: Any) -> str | None:
        text = str(value or "").strip()
        return text[:200] or None

    def visit(value: Any, depth: int = 0) -> None:
        nonlocal workspace_name, user_name
        if depth > 7 or (workspace_name and user_name):
            return
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith("{") and len(stripped) <= 100_000:
                try:
                    visit(json.loads(stripped), depth + 1)
                except json.JSONDecodeError:
                    pass
            return
        if isinstance(value, list):
            for item in value[:50]:
                visit(item, depth + 1)
            return
        if not isinstance(value, dict):
            return
        self_value = value.get("self")
        if isinstance(self_value, dict):
            workspace = self_value.get("workspace")
            user = self_value.get("user")
            if isinstance(workspace, dict):
                workspace_name = workspace_name or bounded_name(workspace.get("name"))
            else:
                workspace_name = workspace_name or bounded_name(self_value.get("workspace_name"))
            if isinstance(user, dict):
                user_name = user_name or bounded_name(user.get("name"))
            else:
                user_name = user_name or bounded_name(self_value.get("user_name"))
        for key in ("structured_content", "structuredContent", "content", "text", "result"):
            if key in value:
                visit(value[key], depth + 1)

    visit(result)
    return workspace_name, user_name


def _query_sources(arguments: dict[str, Any], result: Any) -> tuple[NotionSource, ...]:
    urls = _notion_urls(arguments)
    metadata = _safe_metadata_objects(result)
    sources: list[NotionSource] = []
    for value in metadata:
        object_type = _object_type(value)
        if object_type not in {"database", "data_source"}:
            continue
        source = _source_from_mapping(value, default_type=object_type)
        if source:
            sources.append(source)
    for url in urls:
        sources.append(
            NotionSource(
                id=_id_from_value(url),
                object_type="data_source" if "collection://" in url else "database",
                title=_title_from_url(url, "Notion database"),
                url=url if url.startswith("https://") else "",
                row_count=len(_result_row_urls(result)),
            )
        )
    return _dedupe_sources(sources)


def _fetch_sources(arguments: dict[str, Any], result: Any) -> tuple[NotionSource, ...]:
    metadata = _safe_metadata_objects(result)
    sources: list[NotionSource] = []
    for value in metadata:
        object_type = _object_type(value)
        if object_type not in {"page", "database", "data_source"}:
            continue
        source = _source_from_mapping(value, default_type=object_type)
        if source:
            sources.append(source)
    if sources:
        return _dedupe_sources(sources)
    target = arguments.get("id", arguments.get("url", arguments.get("page_id", "")))
    target_text = str(target or "").strip()
    if not target_text or target_text == "self":
        return ()
    url = target_text if target_text.startswith("https://") and _is_safe_notion_url(target_text) else ""
    return (
        NotionSource(
            id=_id_from_value(target_text),
            object_type="data_source" if target_text.startswith("collection://") else "page",
            title=_title_from_url(url, "Notion source"),
            url=url,
        ),
    )


def _safe_metadata_objects(result: Any) -> tuple[dict[str, Any], ...]:
    values: list[dict[str, Any]] = []

    def visit(value: Any, depth: int = 0) -> None:
        if depth > 5:
            return
        if isinstance(value, dict):
            keys = set(value)
            if keys.intersection({"url", "href"}) and keys.intersection({"type", "object", "object_type", "title", "name", "id"}):
                values.append(value)
            for key in ("structured_content", "structuredContent", "metadata", "source", "sources", "database", "data_source", "parent"):
                if key in value:
                    visit(value[key], depth + 1)
        elif isinstance(value, list):
            for item in value[:200]:
                visit(item, depth + 1)

    visit(result)
    return tuple(values)


def _source_from_mapping(value: dict[str, Any], *, default_type: str) -> NotionSource | None:
    raw_url = str(value.get("url", value.get("href", "")) or "").strip()
    url = raw_url if _is_safe_notion_url(raw_url) else ""
    raw_id = value.get("id", value.get("page_id", value.get("database_id", value.get("data_source_id", ""))))
    source_id = _id_from_value(raw_id or url)
    if not source_id and not url:
        return None
    title = str(value.get("title", value.get("name", "")) or "").strip()
    return NotionSource(
        id=source_id,
        object_type=default_type,
        title=title[:300] or _title_from_url(url, f"Notion {default_type}"),
        url=url,
    )


def _result_row_urls(result: Any) -> tuple[str, ...]:
    urls = _notion_urls(result)
    return tuple(url for url in urls if _looks_like_page_url(url))


def _notion_urls(value: Any) -> tuple[str, ...]:
    found: list[str] = []

    def visit(item: Any, depth: int = 0) -> None:
        if depth > 8:
            return
        if isinstance(item, str):
            for match in _NOTION_URL_PATTERN.findall(item):
                url = match.rstrip(".,;:)]}")
                if _is_safe_notion_url(url):
                    found.append(url)
            if item.startswith("collection://"):
                found.append(item)
        elif isinstance(item, dict):
            for child in item.values():
                visit(child, depth + 1)
        elif isinstance(item, list):
            for child in item[:500]:
                visit(child, depth + 1)

    visit(value)
    return tuple(dict.fromkeys(found))


def _object_type(value: dict[str, Any]) -> str:
    raw = str(value.get("object_type", value.get("object", value.get("type", ""))) or "").lower()
    if raw in {"data-source", "datasource", "collection"}:
        return "data_source"
    if raw in {"page", "database", "data_source"}:
        return raw
    return ""


def _dedupe_sources(values: Iterable[NotionSource]) -> tuple[NotionSource, ...]:
    deduped: list[NotionSource] = []
    positions: dict[tuple[str, str], int] = {}
    for source in values:
        identity = _normalize_url(source.url) or source.id.lower()
        if not identity:
            continue
        key = (source.object_type, identity)
        if key not in positions:
            positions[key] = len(deduped)
            deduped.append(source)
            continue
        index = positions[key]
        current = deduped[index]
        deduped[index] = NotionSource(
            id=current.id or source.id,
            object_type=current.object_type,
            title=current.title if current.title and not current.title.startswith("Notion ") else source.title,
            url=current.url or source.url,
            row_count=max(current.row_count, source.row_count),
            representative_titles=tuple(dict.fromkeys((*current.representative_titles, *source.representative_titles)))[:5],
        )
    return tuple(deduped)


def _is_safe_notion_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    return parsed.scheme == "https" and (parsed.hostname or "").lower() in {
        "notion.so",
        "www.notion.so",
        "notion.com",
        "www.notion.com",
    }


def _looks_like_page_url(value: str) -> bool:
    return _is_safe_notion_url(value) and bool(_NOTION_ID_PATTERN.search(value))


def _normalize_url(value: str) -> str:
    return str(value or "").strip().rstrip("/").lower()


def _id_from_value(value: Any) -> str:
    text = str(value or "")
    match = _NOTION_ID_PATTERN.search(text)
    return match.group(1).replace("-", "").lower() if match else text[:200]


def _title_from_url(url: str, fallback: str) -> str:
    if not url:
        return fallback
    slug = urlparse(url).path.rstrip("/").split("/")[-1]
    slug = re.sub(r"[-_ ]?[0-9a-f]{32}$", "", slug, flags=re.IGNORECASE).replace("-", " ").strip()
    return slug[:300] or fallback


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect the read-only official Notion MCP connection.")
    parser.add_argument("action", nargs="?", choices=("status", "login", "logout"), default="status")
    parser.add_argument("--root", default=Path(__file__).resolve().parents[1])
    parser.add_argument("--refresh", action="store_true", help="Start Codex app-server and verify the live tool inventory.")
    parser.add_argument("--codex-command", default="codex.cmd" if os.name == "nt" else "codex")
    parser.add_argument("--npx-command", default="npx.cmd" if os.name == "nt" else "npx")
    return parser


def _mcp_remote_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["MCP_REMOTE_CONFIG_DIR"] = str(notion_mcp_auth_dir())
    return environment


def _mcp_remote_login_command(npx_command: str) -> list[str]:
    return [
        npx_command,
        "-y",
        "-p",
        NOTION_MCP_REMOTE_PACKAGE,
        "mcp-remote-client",
        NOTION_MCP_URL,
    ]


def clear_mcp_remote_credentials() -> bool:
    home = Path.home().resolve()
    parent = (home / ".mcp-auth").resolve()
    target = notion_mcp_auth_dir().resolve()
    if target.parent != parent or target.name != NOTION_MCP_AUTH_DIR_NAME:
        raise NotionIntegrationError("Refusing to remove an unexpected MCP credential directory.")
    if not target.exists():
        return False
    shutil.rmtree(target)
    return True


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.action == "login":
        return subprocess.call(
            _mcp_remote_login_command(args.npx_command),
            cwd=Path(args.root),
            env=_mcp_remote_environment(),
        )
    if args.action == "logout":
        removed = clear_mcp_remote_credentials()
        print(json.dumps({"ok": True, "credentials_removed": removed}, ensure_ascii=False))
        return 0
    result = get_notion_connection_view(
        args.root,
        refresh=args.refresh,
        codex_command=args.codex_command,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["connection"]["status"] != "connection_error" else 1


if __name__ == "__main__":
    raise SystemExit(main())
