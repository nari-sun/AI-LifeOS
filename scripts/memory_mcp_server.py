"""Read-only MCP server for AI-LifeOS personal memory.

The server intentionally has no dependency on the Python MCP SDK.  It speaks
newline-delimited JSON-RPC over stdio so it can be launched directly by Codex
CLI.  Standard output is reserved exclusively for MCP messages.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from contextlib import closing
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, TextIO

from memory_index import (
    ROOT,
    SUPPORTED_MEMORY_FILES,
    MemorySearchResult,
    default_index_path,
    inspect_index_health,
    search_memory_with_profile,
)
from memory_items import read_memory_item
from personalization_settings import (
    MAX_PROJECT_SCOPE_CHARS,
    capture_session_personalization,
    load_session_personalization,
    validate_session_file,
    validate_project_scope,
)


SERVER_NAME = "ai-lifeos-memory"
SERVER_VERSION = "0.1.0"
CURRENT_PROTOCOL_VERSION = "2025-11-25"
SUPPORTED_PROTOCOL_VERSIONS = {
    CURRENT_PROTOCOL_VERSION,
    "2025-06-18",
    "2025-03-26",
    "2024-11-05",
}

DEFAULT_SEARCH_LIMIT = 8
MAX_SEARCH_LIMIT = 20
MAX_SEARCH_CANDIDATES = 1000
DEFAULT_CONTENT_CHARS = 12_000
MIN_CONTENT_CHARS = 200
MAX_CONTENT_CHARS = 50_000
MAX_QUERY_CHARS = 2_000
MAX_PATH_CHARS = 500
MAX_SOURCE_BYTES = 20 * 1024 * 1024

MESSAGE_PATTERN = re.compile(
    r"^## (?P<role>User|Assistant)[ \t]*\r?\n"
    r"(?:[ \t]*\r?\n)*"
    r"(?:Timestamp:[ \t]*(?P<timestamp>[^\r\n]+)\r?\n(?:[ \t]*\r?\n)*)?"
    r"(?P<content>.*?)(?=^## (?:User|Assistant)[ \t]*\r?$|\Z)",
    re.MULTILINE | re.DOTALL,
)
REFERENCE_FRAGMENT_PATTERN = re.compile(r"^message-(?P<number>\d+)(?:-(?:user|assistant))?$", re.IGNORECASE)

READ_ONLY_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}

TOOLS: tuple[dict[str, Any], ...] = (
    {
        "name": "search_past_chats",
        "title": "Search past AI-LifeOS chats",
        "description": (
            "Search finalized conversations and unorganized live chat messages without modifying local data. "
            "For the user's own opinions or claims, set role='user'. Repeat with alternative concrete terms "
            "when the first query is insufficient, then open a returned reference for primary evidence."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1, "description": "A concrete search query."},
                "role": {
                    "type": "string",
                    "enum": ["any", "user", "assistant"],
                    "default": "any",
                    "description": "Filter message-level evidence by speaker role.",
                },
                "scope": {
                    "type": "string",
                    "enum": ["all", "finalized", "messages", "summaries", "live"],
                    "default": "all",
                    "description": "Limit which local chat sources are searched.",
                },
                "path": {
                    "type": "string",
                    "description": "Optional project-relative path under conversations or inbox/live.",
                },
                "project_scope": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": MAX_PROJECT_SCOPE_CHARS,
                    "description": (
                        "Optional exact substring scope. Results must contain it in source path, title, "
                        "metadata, or message content; no global fallback is performed. When the server "
                        "has an active project scope, this value may only repeat that scope."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_SEARCH_LIMIT,
                    "default": DEFAULT_SEARCH_LIMIT,
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        "annotations": READ_ONLY_ANNOTATIONS,
    },
    {
        "name": "open_conversation",
        "title": "Open conversation evidence",
        "description": (
            "Read a finalized raw/summary conversation or live JSONL reference. Pass a reference returned by "
            "search_past_chats; around_message selects a bounded chronological message window."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "reference": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Relative path, optionally followed by #message-N-role.",
                },
                "around_message": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Optional message number to center the returned window on.",
                },
                "max_chars": {
                    "type": "integer",
                    "minimum": MIN_CONTENT_CHARS,
                    "maximum": MAX_CONTENT_CHARS,
                    "default": DEFAULT_CONTENT_CHARS,
                },
            },
            "required": ["reference"],
            "additionalProperties": False,
        },
        "annotations": READ_ONLY_ANNOTATIONS,
    },
    {
        "name": "get_personal_memory",
        "title": "Read personal memory",
        "description": (
            "Read bounded content from AI-LifeOS long-term memory, preferences, projects, or structured items. "
            "This never creates or updates memory files."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "enum": ["all", "long_term", "preferences", "projects", "items"],
                    "default": "all",
                },
                "max_chars": {
                    "type": "integer",
                    "minimum": MIN_CONTENT_CHARS,
                    "maximum": MAX_CONTENT_CHARS,
                    "default": DEFAULT_CONTENT_CHARS,
                },
                "project_scope": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": MAX_PROJECT_SCOPE_CHARS,
                    "description": (
                        "Optional exact substring scope applied to memory path, title, metadata, and content. "
                        "No global fallback is performed. When the server has an active project scope, this "
                        "value may only repeat that scope."
                    ),
                },
            },
            "additionalProperties": False,
        },
        "annotations": READ_ONLY_ANNOTATIONS,
    },
    {
        "name": "get_index_health",
        "title": "Inspect memory index health",
        "description": (
            "Inspect the rebuildable SQLite search index and source timestamps without rebuilding or changing it."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": READ_ONLY_ANNOTATIONS,
    },
)


class InvalidToolArguments(ValueError):
    """Safe validation error that can be returned without exposing input text."""


class ToolExecutionError(RuntimeError):
    """Expected tool failure with a user-safe message."""


class MemoryTools:
    """Read-only implementations of the four MCP tools."""

    def __init__(
        self,
        root: Path | str = ROOT,
        project_scope: str | None = None,
        exclude_live_session: Path | str | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        self._active_project_scope = _configured_project_scope(project_scope)
        self._excluded_live_session = _configured_excluded_live_session(
            self.root,
            exclude_live_session,
        )

    @property
    def active_project_scope(self) -> str | None:
        """Return the immutable project boundary configured for this server run."""

        return self._active_project_scope

    @property
    def excluded_live_session(self) -> Path | None:
        """Return the immutable active-session exclusion for this server run."""

        return self._excluded_live_session

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        handlers = {
            "search_past_chats": self.search_past_chats,
            "open_conversation": self.open_conversation,
            "get_personal_memory": self.get_personal_memory,
            "get_index_health": self.get_index_health,
        }
        handler = handlers.get(name)
        if handler is None:
            raise InvalidToolArguments("Unknown tool.")
        return handler(arguments)

    def search_past_chats(self, arguments: dict[str, Any]) -> dict[str, Any]:
        _reject_extra_arguments(
            arguments,
            {"query", "role", "scope", "path", "project_scope", "limit"},
        )
        query = _required_string(arguments, "query", maximum=MAX_QUERY_CHARS)
        role = _enum_argument(arguments, "role", {"any", "user", "assistant"}, "any")
        scope = _enum_argument(
            arguments,
            "scope",
            {"all", "finalized", "messages", "summaries", "live"},
            "all",
        )
        limit = _bounded_int(arguments, "limit", DEFAULT_SEARCH_LIMIT, 1, MAX_SEARCH_LIMIT)
        project_scope = self._effective_project_scope(arguments)
        path_filter = arguments.get("path")
        normalized_path: str | None = None
        if path_filter is not None:
            normalized_path = self._validate_search_path(path_filter, scope)

        include_finalized = scope in {"all", "finalized", "messages", "summaries"}
        include_live = scope in {"all", "messages", "live"}
        if normalized_path:
            include_finalized = include_finalized and normalized_path.startswith("conversations/")
            include_live = include_live and normalized_path.startswith("inbox/live/")

        health = self._index_health()
        use_index = bool(health["usable"] and not health["stale"])
        results: list[dict[str, Any]] = []
        search_sources: list[str] = []

        if include_finalized:
            if scope == "summaries":
                document_types = ("summary",)
            elif role != "any":
                document_types = ("raw_chunk",)
            elif scope == "messages":
                document_types = ("raw_chunk", "raw")
            else:
                document_types = ("raw_chunk", "summary", "raw")

            candidate_limit = min(limit * 8, MAX_SEARCH_CANDIDATES)
            indexed, profile = search_memory_with_profile(
                root=self.root,
                query=query,
                limit=candidate_limit,
                document_types=document_types,
                path=normalized_path,
                scope=project_scope,
                use_index=use_index,
                speaker_role=None if role == "any" else role,
            )
            search_sources.append(profile.source)
            for result in indexed:
                results.append(self._search_result(result))

        if include_live:
            live_path = normalized_path if normalized_path and normalized_path.startswith("inbox/live/") else None
            results.extend(
                self._search_live(
                    query=query,
                    role=role,
                    path_filter=live_path,
                    project_scope=project_scope,
                )
            )
            search_sources.append("live-jsonl")

        results.sort(
            key=lambda item: (int(item["score"]), str(item["source"].get("date") or ""), item["reference"]),
            reverse=True,
        )
        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for result in results:
            reference = str(result["reference"])
            if reference in seen:
                continue
            seen.add(reference)
            deduped.append(result)
            if len(deduped) >= limit:
                break

        return {
            "read_only": True,
            "query": query,
            "filters": {
                "role": role,
                "scope": scope,
                "path": normalized_path,
                "project_scope": project_scope,
            },
            "search_source": "+".join(dict.fromkeys(search_sources)) or "none",
            "index_status": health["status"],
            "result_count": len(deduped),
            "results": deduped,
        }

    def open_conversation(self, arguments: dict[str, Any]) -> dict[str, Any]:
        _reject_extra_arguments(arguments, {"reference", "around_message", "max_chars"})
        reference = _required_string(arguments, "reference", maximum=MAX_PATH_CHARS)
        max_chars = _bounded_int(
            arguments,
            "max_chars",
            DEFAULT_CONTENT_CHARS,
            MIN_CONTENT_CHARS,
            MAX_CONTENT_CHARS,
        )
        around_message = arguments.get("around_message")
        if around_message is not None:
            around_message = _bounded_int(arguments, "around_message", 1, 1, 1_000_000)

        path_text, fragment = _split_reference(reference)
        path, relative = self._resolve_readable_source(path_text)
        fragment_message = _fragment_message_number(fragment)
        anchor = around_message if around_message is not None else fragment_message

        if relative.startswith("inbox/live/"):
            if self._session_excluded_from_memory(path):
                raise ToolExecutionError("This live conversation is excluded from memory retrieval.")
            records = self._read_live_records(path)
            if not records:
                raise ToolExecutionError("The live conversation has no readable messages.")
            if self.active_project_scope:
                scope_mode = self._live_project_scope_mode(path, self.active_project_scope)
                if scope_mode == "none":
                    raise ToolExecutionError("The conversation is outside the active project scope.")
                if scope_mode == "messages":
                    records = [
                        record
                        for record in records
                        if _project_scope_matches(self.active_project_scope, record["text"])
                    ]
                    if anchor is not None and not any(
                        record["message_number"] == anchor for record in records
                    ):
                        raise ToolExecutionError("The requested message is outside the active project scope.")
                    if not records:
                        raise ToolExecutionError("The conversation is outside the active project scope.")
            messages, truncated = _select_messages(records, anchor=anchor, max_chars=max_chars)
            return {
                "read_only": True,
                "source": {
                    "reference": reference,
                    "path": relative,
                    "document_type": "live",
                    "title": f"Live session {path.stem}",
                    "date": _first_message_date(records),
                },
                "selection": _selection_metadata(messages, anchor, max_chars, truncated, len(records)),
                "messages": messages,
            }

        text = _read_utf8_source(path)
        header = _markdown_header(text)
        title = _field_value(header, "Session") or _field_value(header, "Title") or _first_heading(header) or path.stem
        date = _field_value(header, "Date")
        document_type = "raw" if path.name == "raw.md" else "summary"
        parsed_messages = _parse_markdown_messages(text)
        if self.active_project_scope:
            header_matches = self._finalized_header_matches_project_scope(
                relative,
                title,
                text,
                self.active_project_scope,
            )
            if parsed_messages and not header_matches:
                parsed_messages = [
                    message
                    for message in parsed_messages
                    if _project_scope_matches(self.active_project_scope, message["text"])
                ]
                if anchor is not None and not any(
                    message["message_number"] == anchor for message in parsed_messages
                ):
                    raise ToolExecutionError("The requested message is outside the active project scope.")
                if not parsed_messages:
                    raise ToolExecutionError("The conversation is outside the active project scope.")
            elif not parsed_messages and not header_matches:
                text = _project_scoped_memory_content(text, self.active_project_scope)
                if not text:
                    raise ToolExecutionError("The conversation is outside the active project scope.")
        source = {
            "reference": reference,
            "path": relative,
            "document_type": document_type,
            "title": title,
            "date": date,
        }
        if parsed_messages:
            messages, truncated = _select_messages(parsed_messages, anchor=anchor, max_chars=max_chars)
            return {
                "read_only": True,
                "source": source,
                "selection": _selection_metadata(messages, anchor, max_chars, truncated, len(parsed_messages)),
                "messages": messages,
            }
        if anchor is not None:
            raise ToolExecutionError("This source has no message-level structure.")
        content, truncated = _truncate_text(text, max_chars)
        return {
            "read_only": True,
            "source": source,
            "selection": {
                "around_message": None,
                "max_chars": max_chars,
                "truncated": truncated,
                "total_messages": 0,
            },
            "content": content,
        }

    def get_personal_memory(self, arguments: dict[str, Any]) -> dict[str, Any]:
        _reject_extra_arguments(arguments, {"scope", "project_scope", "max_chars"})
        scope = _enum_argument(
            arguments,
            "scope",
            {"all", "long_term", "preferences", "projects", "items"},
            "all",
        )
        max_chars = _bounded_int(
            arguments,
            "max_chars",
            DEFAULT_CONTENT_CHARS,
            MIN_CONTENT_CHARS,
            MAX_CONTENT_CHARS,
        )
        project_scope = self._effective_project_scope(arguments)
        memory_root = (self.root / "memory").resolve()
        if not _is_within(memory_root, self.root):
            raise ToolExecutionError("The memory directory is outside the configured root.")

        filenames = {
            "long_term": "long_term.md",
            "preferences": "preferences.md",
            "projects": "projects.md",
        }
        paths: list[Path] = []
        if scope == "all":
            for name in ("preferences.md", "long_term.md", "projects.md"):
                candidate = memory_root / name
                if candidate.is_file():
                    paths.append(candidate)
            items_dir = memory_root / "items"
            if items_dir.is_dir():
                paths.extend(sorted(path for path in items_dir.glob("*.md") if path.is_file()))
        elif scope == "items":
            items_dir = memory_root / "items"
            if items_dir.is_dir():
                paths.extend(sorted(path for path in items_dir.glob("*.md") if path.is_file()))
        else:
            candidate = memory_root / filenames[scope]
            if candidate.is_file():
                paths.append(candidate)

        sources: list[dict[str, Any]] = []
        remaining = max_chars
        omitted_sources = 0
        read_error_count = 0
        for path in paths:
            resolved = path.resolve()
            if not _is_within(resolved, memory_root):
                read_error_count += 1
                continue
            if remaining <= 0:
                omitted_sources += 1
                continue
            try:
                if resolved.parent.name == "items":
                    item = read_memory_item(resolved)
                    raw_content = item.content
                    metadata: dict[str, Any] = {
                        "category": item.category,
                        "category_label": item.category_label,
                        "status": item.status,
                        "source": item.source,
                        "source_date": item.source_date,
                        "confidence": item.confidence,
                        "tags": list(item.tags),
                    }
                    document_type = "memory_item"
                    title = item.category_label
                else:
                    raw_content = _read_utf8_source(resolved)
                    metadata = {}
                    document_type = "memory"
                    title = _first_heading(raw_content) or resolved.stem
            except (OSError, UnicodeError, ValueError, ToolExecutionError):
                read_error_count += 1
                continue

            if project_scope:
                if document_type == "memory_item":
                    if not _project_scope_matches(
                        project_scope,
                        _relative_path(resolved, self.root),
                        title,
                        metadata,
                        raw_content,
                    ):
                        continue
                else:
                    raw_content = _project_scoped_memory_content(raw_content, project_scope)
                    if not raw_content:
                        continue

            content, truncated = _truncate_text(raw_content, remaining)
            remaining -= len(content)
            sources.append(
                {
                    "path": _relative_path(resolved, self.root),
                    "document_type": document_type,
                    "title": title,
                    "metadata": metadata,
                    "content": content,
                    "truncated": truncated,
                }
            )
            if truncated:
                omitted_sources += len(paths) - len(sources)
                break

        return {
            "read_only": True,
            "scope": scope,
            "project_scope": project_scope,
            "max_chars": max_chars,
            "returned_chars": sum(len(source["content"]) for source in sources),
            "source_count": len(sources),
            "omitted_source_count": omitted_sources,
            "read_error_count": read_error_count,
            "sources": sources,
        }

    def get_index_health(self, arguments: dict[str, Any]) -> dict[str, Any]:
        _reject_extra_arguments(arguments, set())
        return self._index_health()

    def _effective_project_scope(self, arguments: dict[str, Any]) -> str | None:
        requested = _tool_project_scope(arguments)
        active = self.active_project_scope
        if active is None:
            return requested
        if requested is not None and _normalize_scope(requested) != _normalize_scope(active):
            raise InvalidToolArguments("project_scope is fixed by the server configuration.")
        return active

    def _search_result(self, result: MemorySearchResult) -> dict[str, Any]:
        path = _relative_path(result.path.resolve(), self.root)
        reference = path
        if result.message_number is not None:
            suffix = f"-{result.speaker_role}" if result.speaker_role else ""
            reference = f"{path}#message-{result.message_number:03d}{suffix}"
        return {
            "reference": reference,
            "score": result.score,
            "excerpt": result.snippet,
            "source": {
                "path": path,
                "document_type": result.document_type,
                "title": result.title,
                "date": result.date,
                "tags": list(result.tags),
                "role": result.speaker_role,
                "message_number": result.message_number,
            },
        }

    def _search_live(
        self,
        query: str,
        role: str,
        path_filter: str | None,
        project_scope: str | None,
    ) -> list[dict[str, Any]]:
        live_dir = (self.root / "inbox" / "live").resolve()
        if not live_dir.is_dir() or not _is_within(live_dir, self.root):
            return []
        terms = _query_terms(query)
        if not terms:
            return []
        results: list[dict[str, Any]] = []
        for path in sorted(live_dir.glob("*.jsonl")):
            resolved = path.resolve()
            if not _is_within(resolved, live_dir) or not self._is_unorganized_live(resolved):
                continue
            relative = _relative_path(resolved, self.root)
            if path_filter and path_filter.lower() not in relative.lower():
                continue
            records = self._read_live_records(resolved)
            scope_mode = self._live_project_scope_mode(resolved, project_scope) if project_scope else "session"
            if scope_mode == "none":
                continue
            for record in records:
                if role != "any" and record["role"] != role:
                    continue
                if scope_mode == "messages" and not _project_scope_matches(
                    project_scope,
                    record["text"],
                ):
                    continue
                score = _match_score(record["text"], terms)
                if score <= 0:
                    continue
                reference = f"{relative}#message-{record['message_number']:03d}-{record['role']}"
                results.append(
                    {
                        "reference": reference,
                        "score": score,
                        "excerpt": _excerpt(record["text"], terms, 2_200),
                        "source": {
                            "path": relative,
                            "document_type": "live_message",
                            "title": f"Live session {path.stem} / {record['role']} message {record['message_number']}",
                            "date": _timestamp_date(record.get("timestamp")),
                            "tags": [],
                            "role": record["role"],
                            "message_number": record["message_number"],
                        },
                    }
                )
        return results

    def _finalized_header_matches_project_scope(
        self,
        relative: str,
        title: str,
        content: str,
        project_scope: str,
    ) -> bool:
        header = _markdown_header(content)
        stored_scope = _field_value(header, "Project Scope")
        if stored_scope and _normalize_scope(stored_scope):
            return _normalize_scope(stored_scope) == _normalize_scope(project_scope)
        metadata = {
            "session": _field_value(header, "Session"),
            "title": _field_value(header, "Title"),
            "date": _field_value(header, "Date"),
            "tags": _field_value(header, "Tags"),
            "project_scope": _field_value(header, "Project Scope"),
        }
        return _project_scope_matches(
            project_scope,
            relative,
            title,
            metadata,
            header,
        )

    def _live_project_scope_mode(
        self,
        path: Path,
        project_scope: str,
    ) -> str:
        relative = _relative_path(path.resolve(), self.root)
        session_metadata = self._live_session_scope_metadata(path)
        stored_scope = session_metadata.get("project_scope")
        if isinstance(stored_scope, str) and stored_scope.strip():
            return (
                "session"
                if _normalize_scope(project_scope) == _normalize_scope(stored_scope)
                else "none"
            )
        if _project_scope_matches(
            project_scope,
            relative,
            f"Live session {path.stem}",
            session_metadata.get("title"),
            session_metadata.get("session_id"),
        ):
            return "session"
        return "messages"

    def _live_session_scope_metadata(self, path: Path) -> dict[str, Any]:
        try:
            raw = capture_session_personalization(self.root, path)
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            return {}
        personalization = raw or {}
        metadata_path = path.with_suffix(".session.json")
        title: str | None = None
        session_id: str | None = None
        try:
            if metadata_path.is_file() and metadata_path.stat().st_size <= 1_000_000:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
                if isinstance(metadata, dict):
                    title = metadata.get("title") if isinstance(metadata.get("title"), str) else None
                    session_id = (
                        metadata.get("session_id") if isinstance(metadata.get("session_id"), str) else None
                    )
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {}
        return {
            "project_scope": personalization.get("project_scope"),
            "title": title,
            "session_id": session_id,
        }

    def _read_live_records(self, path: Path) -> list[dict[str, Any]]:
        if path.stat().st_size > MAX_SOURCE_BYTES:
            raise ToolExecutionError("The conversation source is too large to read safely.")
        records: list[dict[str, Any]] = []
        try:
            with path.open("r", encoding="utf-8-sig") as handle:
                for line in handle:
                    if len(line) > MAX_SOURCE_BYTES:
                        continue
                    try:
                        value = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(value, dict):
                        continue
                    item_role = value.get("role")
                    content = value.get("content")
                    timestamp = value.get("timestamp")
                    if item_role not in {"user", "assistant"} or not isinstance(content, str):
                        continue
                    records.append(
                        {
                            "message_number": len(records) + 1,
                            "role": item_role,
                            "timestamp": timestamp if isinstance(timestamp, str) else None,
                            "text": content,
                        }
                    )
        except (OSError, UnicodeError) as exc:
            raise ToolExecutionError("The live conversation could not be read.") from exc
        return records

    def _is_unorganized_live(self, path: Path) -> bool:
        if self._session_excluded_from_memory(path):
            return False
        metadata_path = path.with_suffix(".session.json")
        if not metadata_path.is_file():
            return True
        if not _is_within(metadata_path.resolve(), (self.root / "inbox" / "live").resolve()):
            return False
        try:
            if metadata_path.stat().st_size > 1_000_000:
                return True
            metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return True
        organize = metadata.get("organize") if isinstance(metadata, dict) else None
        return not isinstance(organize, dict) or not bool(organize.get("index_updated"))

    def _session_excluded_from_memory(self, path: Path) -> bool:
        """Apply temporary-chat exclusion fail-closed and without logging metadata."""

        if self.excluded_live_session is not None and path.resolve() == self.excluded_live_session:
            return True
        metadata_path = path.with_suffix(".session.json")
        try:
            if metadata_path.is_file() and metadata_path.stat().st_size > 1_000_000:
                return True
            personalization = load_session_personalization(self.root, path)
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            return True
        return personalization.temporary or personalization.exclude_from_memory

    def _validate_search_path(self, value: Any, scope: str) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > MAX_PATH_CHARS:
            raise InvalidToolArguments("path must be a non-empty relative path.")
        normalized = value.strip().replace("\\", "/")
        if "#" in normalized:
            raise InvalidToolArguments("path must not contain a reference fragment.")
        if not normalized.startswith(("conversations/", "inbox/live/")):
            prefix = "inbox/live" if scope == "live" else "conversations"
            normalized = f"{prefix}/{normalized}"
        target, relative = self._resolve_allowed_relative(normalized)
        del target
        return relative

    def _resolve_readable_source(self, path_text: str) -> tuple[Path, str]:
        path, relative = self._resolve_allowed_relative(path_text)
        if not path.is_file():
            raise ToolExecutionError("The requested conversation source does not exist.")
        if relative.startswith("conversations/") and path.name not in {"raw.md", "summary.md"}:
            raise InvalidToolArguments("Only raw.md and summary.md conversation sources can be opened.")
        if relative.startswith("inbox/live/") and path.suffix.lower() != ".jsonl":
            raise InvalidToolArguments("Only live JSONL conversation sources can be opened.")
        return path, relative

    def _resolve_allowed_relative(self, value: str) -> tuple[Path, str]:
        normalized = _validate_relative_path_text(value)
        pure = PurePosixPath(normalized)
        if len(pure.parts) < 2 or pure.parts[0] not in {"conversations", "inbox"}:
            raise InvalidToolArguments("The path is outside the allowed conversation directories.")
        if pure.parts[0] == "inbox" and (len(pure.parts) < 3 or pure.parts[1] != "live"):
            raise InvalidToolArguments("The path is outside the allowed conversation directories.")
        path = (self.root / Path(*pure.parts)).resolve()
        allowed_root = self.root / ("conversations" if pure.parts[0] == "conversations" else "inbox/live")
        if not _is_within(path, allowed_root.resolve()):
            raise InvalidToolArguments("The path is outside the allowed conversation directories.")
        return path, PurePosixPath(*path.relative_to(self.root).parts).as_posix()

    def _index_health(self) -> dict[str, Any]:
        db_path = default_index_path(self.root).resolve()
        relative_db = _relative_path(db_path, self.root)
        shared_health = inspect_index_health(self.root, db_path)
        shared_status = "ready" if shared_health.status == "fresh" else shared_health.status
        source_paths, source_scan_errors = self._source_paths()
        source_stats: list[tuple[float, str]] = []
        for path in source_paths:
            try:
                source_stats.append((path.stat().st_mtime, _relative_path(path, self.root)))
            except OSError:
                source_scan_errors += 1
        newest_source = max(source_stats, default=None)

        base: dict[str, Any] = {
            "read_only": True,
            "status": shared_status,
            "usable": shared_health.status == "fresh",
            "stale": shared_health.needs_markdown_fallback,
            "stale_reasons": list(shared_health.reasons),
            "search_strategy": "sqlite" if shared_health.status == "fresh" else "markdown",
            "index": {
                "path": relative_db,
                "exists": db_path.is_file(),
                "size_bytes": 0,
                "modified_at": None,
                "schema_compatible": False,
                "document_count": 0,
                "raw_chunk_count": 0,
                "last_built_at": None,
                "fts5_available": False,
            },
            "sources": {
                "file_count": len(source_paths),
                "newest_modified_at": _iso_timestamp(newest_source[0]) if newest_source else None,
                "source_scan_error_count": source_scan_errors,
                "missing_from_index_count": len(source_paths),
                "orphaned_in_index_count": 0,
            },
            "unorganized_live_file_count": self._unorganized_live_count(),
        }
        if not db_path.is_file():
            return base

        try:
            stat = db_path.stat()
            base["index"]["size_bytes"] = stat.st_size
            base["index"]["modified_at"] = _iso_timestamp(stat.st_mtime)
            uri = f"{db_path.as_uri()}?mode=ro"
            with closing(sqlite3.connect(uri, uri=True)) as connection:
                connection.execute("PRAGMA query_only = ON")
                tables = {
                    str(row[0])
                    for row in connection.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")
                }
                if "documents" not in tables:
                    raise sqlite3.DatabaseError("missing documents table")
                columns = {
                    str(row[1]) for row in connection.execute("PRAGMA table_info(documents)")
                }
                required_columns = {"document_type", "path", "updated_at", "content"}
                schema_compatible = required_columns.issubset(columns) and {
                    "speaker_role",
                    "message_number",
                }.issubset(columns)
                base["index"]["schema_compatible"] = (
                    schema_compatible and shared_health.status in {"fresh", "stale"}
                )
                base["index"]["document_count"] = int(
                    connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
                )
                if "document_type" in columns:
                    base["index"]["raw_chunk_count"] = int(
                        connection.execute(
                            "SELECT COUNT(*) FROM documents WHERE document_type = ?", ("raw_chunk",)
                        ).fetchone()[0]
                    )
                if "updated_at" in columns:
                    base["index"]["last_built_at"] = connection.execute(
                        "SELECT MAX(updated_at) FROM documents"
                    ).fetchone()[0]
                base["index"]["fts5_available"] = "documents_fts" in tables
                indexed_paths = {
                    str(row[0]).replace("\\", "/")
                    for row in connection.execute("SELECT DISTINCT path FROM documents")
                    if isinstance(row[0], str)
                }
        except (OSError, sqlite3.Error):
            base["status"] = "unreadable"
            base["usable"] = False
            base["stale"] = True
            base["stale_reasons"] = ["index-unreadable"]
            base["search_strategy"] = "markdown"
            return base

        source_names = {relative for _, relative in source_stats}
        missing = source_names - indexed_paths
        orphaned = indexed_paths - source_names
        base["sources"]["missing_from_index_count"] = len(missing)
        base["sources"]["orphaned_in_index_count"] = len(orphaned)
        reasons = list(shared_health.reasons)
        if not base["index"]["schema_compatible"]:
            reasons.append("legacy-schema")
        if source_scan_errors:
            reasons.append("source-scan-errors")

        reasons = list(dict.fromkeys(reasons))
        if source_scan_errors and shared_status == "ready":
            shared_status = "stale"
        base["status"] = shared_status
        base["usable"] = shared_status == "ready"
        base["stale"] = shared_status != "ready"
        base["stale_reasons"] = reasons
        base["search_strategy"] = "sqlite" if shared_status == "ready" else "markdown"
        return base

    def _source_paths(self) -> tuple[list[Path], int]:
        paths: list[Path] = []
        errors = 0
        try:
            conversations = self.root / "conversations"
            if conversations.is_dir():
                paths.extend(
                    path.resolve()
                    for path in conversations.rglob("*.md")
                    if path.name in {"raw.md", "summary.md"} and path.is_file()
                )
            journal = self.root / "journal"
            if journal.is_dir():
                paths.extend(path.resolve() for path in journal.rglob("*.md") if path.is_file())
            memory = self.root / "memory"
            if memory.is_dir():
                paths.extend(
                    path.resolve()
                    for path in memory.glob("*.md")
                    if path.name in SUPPORTED_MEMORY_FILES and path.is_file()
                )
                items = memory / "items"
                if items.is_dir():
                    paths.extend(path.resolve() for path in items.glob("*.md") if path.is_file())
        except OSError:
            errors += 1
        safe = sorted({path for path in paths if _is_within(path, self.root)})
        return safe, errors

    def _unorganized_live_count(self) -> int:
        live_dir = self.root / "inbox" / "live"
        if not live_dir.is_dir() or not _is_within(live_dir.resolve(), self.root):
            return 0
        count = 0
        try:
            for path in live_dir.glob("*.jsonl"):
                if path.is_file() and _is_within(path.resolve(), live_dir.resolve()) and self._is_unorganized_live(path):
                    count += 1
        except OSError:
            return count
        return count


class MCPServer:
    """Minimal MCP lifecycle and tools JSON-RPC dispatcher."""

    def __init__(self, tools: MemoryTools) -> None:
        self.tools = tools
        self.initialize_seen = False

    def handle(self, message: Any) -> dict[str, Any] | None:
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return _rpc_error(None, -32600, "Invalid Request")
        method = message.get("method")
        has_id = "id" in message
        request_id = message.get("id")
        if not isinstance(method, str):
            return _rpc_error(request_id if has_id else None, -32600, "Invalid Request")

        if not has_id:
            if method == "notifications/initialized":
                self.initialize_seen = True
            return None

        if method == "initialize":
            params = message.get("params")
            if not isinstance(params, dict) or not isinstance(params.get("protocolVersion"), str):
                return _rpc_error(request_id, -32602, "Invalid initialize parameters")
            requested = params["protocolVersion"]
            protocol_version = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else CURRENT_PROTOCOL_VERSION
            self.initialize_seen = True
            return _rpc_result(
                request_id,
                {
                    "protocolVersion": protocol_version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {
                        "name": SERVER_NAME,
                        "title": "AI-LifeOS Read-only Memory",
                        "version": SERVER_VERSION,
                    },
                    "instructions": (
                        "Read-only local AI-LifeOS memory. For the user's views, search with role=user and open "
                        "the source before answering. Assistant messages are not evidence of the user's beliefs. "
                        "If a search is empty, repeat search_past_chats with concrete names, people, or phrases. "
                        "A server-configured project scope is enforced by every content tool and cannot be changed "
                        "by tool arguments. The active live session, when configured, is excluded from every "
                        "search, direct open, and health count. "
                        "No tool writes, rebuilds, deletes, or sends data to an external service."
                    ),
                },
            )
        if method == "ping":
            return _rpc_result(request_id, {})
        if not self.initialize_seen:
            return _rpc_error(request_id, -32002, "Server not initialized")
        if method == "tools/list":
            return _rpc_result(request_id, {"tools": list(TOOLS)})
        if method == "tools/call":
            params = message.get("params")
            if not isinstance(params, dict) or not isinstance(params.get("name"), str):
                return _rpc_error(request_id, -32602, "Invalid tool call parameters")
            arguments = params.get("arguments", {})
            if not isinstance(arguments, dict):
                return _rpc_error(request_id, -32602, "Tool arguments must be an object")
            name = params["name"]
            if name not in {tool["name"] for tool in TOOLS}:
                return _rpc_error(request_id, -32602, "Unknown tool")
            try:
                data = self.tools.call(name, arguments)
            except InvalidToolArguments as exc:
                return _rpc_error(request_id, -32602, str(exc))
            except ToolExecutionError as exc:
                return _rpc_result(request_id, _tool_error(str(exc)))
            except Exception as exc:  # Keep private content and raw exception text out of stderr/results.
                _diagnostic("tool_failure", name, type(exc).__name__)
                return _rpc_result(request_id, _tool_error("The read-only tool could not complete the request."))
            return _rpc_result(request_id, _tool_success(data))
        return _rpc_error(request_id, -32601, "Method not found")


def run_stdio(
    root: Path | str = ROOT,
    instream: TextIO | None = None,
    outstream: TextIO | None = None,
    *,
    project_scope: str | None = None,
    exclude_live_session: Path | str | None = None,
) -> int:
    """Run a persistent newline-delimited MCP session until stdin closes."""

    input_stream = instream or sys.stdin
    output_stream = outstream or sys.stdout
    server = MCPServer(
        MemoryTools(
            root,
            project_scope=project_scope,
            exclude_live_session=exclude_live_session,
        )
    )
    for line in input_stream:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            response = _rpc_error(None, -32700, "Parse error")
        else:
            response = server.handle(message)
        if response is not None:
            output_stream.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
            output_stream.flush()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the read-only AI-LifeOS Memory MCP server over newline-delimited stdio JSON-RPC."
    )
    parser.add_argument("--root", default=ROOT, help="AI-LifeOS project root. Defaults to this script's project.")
    parser.add_argument(
        "--project-scope",
        help=(
            "Fix this server run to one project scope. Content tools cannot omit, broaden, or replace it."
        ),
    )
    parser.add_argument(
        "--exclude-live-session",
        help=(
            "Exclude one active inbox/live JSONL from search, direct open, and health counts for this run."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {SERVER_VERSION}")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    if not root.is_dir():
        parser.error("--root must point to an existing directory")
    try:
        project_scope = _configured_project_scope(args.project_scope)
    except ValueError:
        parser.error(
            f"--project-scope must be a non-empty value of at most {MAX_PROJECT_SCOPE_CHARS} characters "
            "without control characters"
        )
    try:
        exclude_live_session = _configured_excluded_live_session(root, args.exclude_live_session)
    except ValueError:
        parser.error("--exclude-live-session must be an exact .jsonl path directly under root/inbox/live")
    _configure_stdio()
    return run_stdio(
        root=root,
        project_scope=project_scope,
        exclude_live_session=exclude_live_session,
    )


def _configure_stdio() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def _rpc_result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _rpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _tool_success(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, indent=2)}],
        "structuredContent": data,
        "isError": False,
    }


def _tool_error(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "isError": True}


def _diagnostic(event: str, tool: str, error_type: str) -> None:
    # Never include arguments, queries, snippets, file contents, or exception text.
    print(f"{SERVER_NAME}: {event} tool={tool} error={error_type}", file=sys.stderr, flush=True)


def _reject_extra_arguments(arguments: dict[str, Any], allowed: set[str]) -> None:
    if set(arguments) - allowed:
        raise InvalidToolArguments("Unexpected tool argument.")


def _required_string(arguments: dict[str, Any], name: str, maximum: int) -> str:
    value = arguments.get(name)
    if not isinstance(value, str) or not value.strip():
        raise InvalidToolArguments(f"{name} must be a non-empty string.")
    if len(value) > maximum:
        raise InvalidToolArguments(f"{name} is too long.")
    return value.strip()


def _optional_string(arguments: dict[str, Any], name: str, maximum: int) -> str | None:
    if name not in arguments:
        return None
    return _required_string(arguments, name, maximum)


def _configured_project_scope(value: str | None) -> str | None:
    try:
        normalized = validate_project_scope(value)
    except ValueError as exc:
        raise ValueError("Invalid project scope.") from exc
    if value is not None and normalized is None:
        raise ValueError("Invalid project scope.")
    return normalized


def _configured_excluded_live_session(
    root: Path,
    value: Path | str | None,
) -> Path | None:
    if value is None:
        return None
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise ValueError("Invalid excluded live session.")
    try:
        return validate_session_file(root, value)
    except ValueError as exc:
        raise ValueError("Invalid excluded live session.") from exc


def _tool_project_scope(arguments: dict[str, Any]) -> str | None:
    value = _optional_string(arguments, "project_scope", maximum=MAX_PROJECT_SCOPE_CHARS)
    if value is None:
        return None
    try:
        normalized = validate_project_scope(value)
    except ValueError as exc:
        raise InvalidToolArguments("project_scope is invalid.") from exc
    if normalized is None:
        raise InvalidToolArguments("project_scope is invalid.")
    return normalized


def _enum_argument(arguments: dict[str, Any], name: str, allowed: set[str], default: str) -> str:
    value = arguments.get(name, default)
    if not isinstance(value, str) or value not in allowed:
        raise InvalidToolArguments(f"{name} has an unsupported value.")
    return value


def _bounded_int(arguments: dict[str, Any], name: str, default: int, minimum: int, maximum: int) -> int:
    value = arguments.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise InvalidToolArguments(f"{name} is outside the allowed range.")
    return value


def _validate_relative_path_text(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_PATH_CHARS:
        raise InvalidToolArguments("A non-empty relative path is required.")
    normalized = value.strip().replace("\\", "/")
    if "\x00" in normalized or normalized.startswith(("/", "//")) or re.match(r"^[A-Za-z]:", normalized):
        raise InvalidToolArguments("Absolute paths are not allowed.")
    raw_parts = normalized.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise InvalidToolArguments("Path traversal is not allowed.")
    return PurePosixPath(*raw_parts).as_posix()


def _split_reference(reference: str) -> tuple[str, str | None]:
    path, separator, fragment = reference.partition("#")
    if separator and (not fragment or "#" in fragment):
        raise InvalidToolArguments("The reference fragment is invalid.")
    return path, fragment or None


def _fragment_message_number(fragment: str | None) -> int | None:
    if fragment is None:
        return None
    match = REFERENCE_FRAGMENT_PATTERN.fullmatch(fragment)
    if not match:
        raise InvalidToolArguments("The reference fragment is invalid.")
    number = int(match.group("number"))
    if number < 1:
        raise InvalidToolArguments("The reference message number is invalid.")
    return number


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _relative_path(path: Path, root: Path) -> str:
    try:
        relative = path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ToolExecutionError("A source path is outside the configured root.") from exc
    return PurePosixPath(*relative.parts).as_posix()


def _read_utf8_source(path: Path) -> str:
    try:
        if path.stat().st_size > MAX_SOURCE_BYTES:
            raise ToolExecutionError("The conversation source is too large to read safely.")
        return path.read_text(encoding="utf-8-sig")
    except ToolExecutionError:
        raise
    except (OSError, UnicodeError) as exc:
        raise ToolExecutionError("The conversation source could not be read as UTF-8.") from exc


def _field_value(content: str, name: str) -> str | None:
    match = re.search(rf"^{re.escape(name)}:\s*(.+)$", content, re.MULTILINE)
    return match.group(1).strip() if match else None


def _markdown_header(content: str) -> str:
    match = re.search(
        r"^## (?:User|Assistant)[ \t]*$",
        content,
        re.MULTILINE | re.IGNORECASE,
    )
    return content[: match.start()] if match else content


def _first_heading(content: str) -> str | None:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or None
    return None


def _parse_markdown_messages(content: str) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for message_number, match in enumerate(MESSAGE_PATTERN.finditer(content), start=1):
        text = match.group("content").strip()
        if not text:
            continue
        messages.append(
            {
                "message_number": message_number,
                "role": match.group("role").lower(),
                "timestamp": match.group("timestamp").strip() if match.group("timestamp") else None,
                "text": text,
            }
        )
    return messages


def _select_messages(
    records: list[dict[str, Any]], anchor: int | None, max_chars: int
) -> tuple[list[dict[str, Any]], bool]:
    if anchor is not None and not any(record["message_number"] == anchor for record in records):
        raise ToolExecutionError("The requested message number does not exist in this conversation.")
    if anchor is None:
        candidate_indices = list(range(len(records)))
    else:
        anchor_index = next(index for index, record in enumerate(records) if record["message_number"] == anchor)
        candidate_indices = [anchor_index]
        radius = 1
        while len(candidate_indices) < len(records):
            before = anchor_index - radius
            after = anchor_index + radius
            if before >= 0:
                candidate_indices.append(before)
            if after < len(records):
                candidate_indices.append(after)
            radius += 1

    selected_indices: list[int] = []
    remaining = max_chars
    truncated = False
    for index in candidate_indices:
        text = str(records[index]["text"])
        if remaining <= 0:
            truncated = True
            break
        if len(text) <= remaining:
            selected_indices.append(index)
            remaining -= len(text)
            continue
        if not selected_indices:
            selected_indices.append(index)
            truncated = True
        else:
            truncated = True
        break

    selected_indices.sort()
    messages: list[dict[str, Any]] = []
    remaining = max_chars
    for index in selected_indices:
        record = records[index]
        text, text_truncated = _truncate_text(str(record["text"]), remaining)
        remaining -= len(text)
        messages.append(
            {
                "message_number": int(record["message_number"]),
                "role": str(record["role"]),
                "timestamp": record.get("timestamp"),
                "text": text,
                "truncated": text_truncated,
            }
        )
        truncated = truncated or text_truncated
    if len(messages) < len(records):
        truncated = True
    return messages, truncated


def _selection_metadata(
    messages: list[dict[str, Any]], anchor: int | None, max_chars: int, truncated: bool, total: int
) -> dict[str, Any]:
    return {
        "around_message": anchor,
        "first_message": messages[0]["message_number"] if messages else None,
        "last_message": messages[-1]["message_number"] if messages else None,
        "returned_messages": len(messages),
        "total_messages": total,
        "max_chars": max_chars,
        "truncated": truncated,
    }


def _truncate_text(text: str, maximum: int) -> tuple[str, bool]:
    if len(text) <= maximum:
        return text, False
    if maximum <= 1:
        return text[:maximum], True
    return text[: maximum - 1].rstrip() + "…", True


def _project_scoped_memory_content(content: str, project_scope: str) -> str:
    """Return matching Markdown sections or isolated factual lines only."""

    lines = content.splitlines()
    selected: set[int] = set()
    normalized_scope = _normalize_scope(project_scope)
    for index, line in enumerate(lines):
        if normalized_scope not in _normalize_scope(line):
            continue
        heading = re.match(r"^(#{1,6})\s+", line)
        if not heading:
            selected.add(index)
            continue
        level = len(heading.group(1))
        selected.add(index)
        for following in range(index + 1, len(lines)):
            next_heading = re.match(r"^(#{1,6})\s+", lines[following])
            if next_heading and len(next_heading.group(1)) <= level:
                break
            selected.add(following)
    return "\n".join(lines[index] for index in sorted(selected)).strip()


def _query_terms(query: str) -> tuple[str, ...]:
    parts = [
        part
        for part in re.split(r"[\s、。,.!?！？「」『』（）()\[\]【】]+", query.strip())
        if part
    ]
    expanded: list[str] = []
    for part in parts:
        split_parts = re.split(r"(?:の|は|を|に|が|で|と|や|も|へ|から|まで|です|ます)", part)
        expanded.extend(value for value in split_parts if len(value) >= 2)
    terms = expanded or [part for part in parts if len(part) >= 2]
    return tuple(dict.fromkeys(term.lower() for term in terms))


def _match_score(content: str, terms: tuple[str, ...]) -> int:
    lowered = content.lower()
    return sum(lowered.count(term) for term in terms)


def _excerpt(content: str, terms: tuple[str, ...], width: int) -> str:
    normalized = " ".join(line.strip() for line in content.splitlines() if line.strip())
    if len(normalized) <= width:
        return normalized
    lowered = normalized.lower()
    positions = [lowered.find(term) for term in terms if lowered.find(term) >= 0]
    start = max((min(positions) if positions else 0) - width // 4, 0)
    end = min(start + width, len(normalized))
    excerpt = normalized[start:end].strip()
    return ("…" if start else "") + excerpt + ("…" if end < len(normalized) else "")


def _project_scope_matches(project_scope: str, *values: Any) -> bool:
    needle = _normalize_scope(project_scope)
    for value in values:
        text = _scope_value_text(value)
        if needle in _normalize_scope(text):
            return True
    return False


def _scope_value_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return "\n".join(_scope_value_text(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return "\n".join(_scope_value_text(item) for item in value)
    return "" if value is None else str(value)


def _normalize_scope(value: str) -> str:
    return "".join(value.casefold().split())


def _timestamp_date(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value).date().isoformat()
    except ValueError:
        match = re.match(r"\d{4}-\d{2}-\d{2}", value)
        return match.group(0) if match else None


def _first_message_date(records: list[dict[str, Any]]) -> str | None:
    for record in records:
        date = _timestamp_date(record.get("timestamp"))
        if date:
            return date
    return None


def _iso_timestamp(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).astimezone().isoformat(timespec="seconds")


if __name__ == "__main__":
    raise SystemExit(main())
