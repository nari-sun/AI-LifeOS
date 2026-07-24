"""Read-only Notion context adapter for AI-LifeOS.

The adapter deliberately keeps the Notion installation token in Windows
Credential Manager and keeps fetched page bodies in memory only.  The local
allowlist contains identifiers and display metadata, but never credentials or
retrieved page content.
"""

from __future__ import annotations

import argparse
import ctypes
import getpass
import json
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable


NOTION_API_BASE = "https://api.notion.com"
NOTION_API_VERSION = "2026-03-11"
NOTION_CREDENTIAL_TARGET = "AI-LifeOS/Notion"
NOTION_SETTINGS_RELATIVE_PATH = Path("config") / "notion_settings.json"
NOTION_OBJECT_TYPES = {"page", "data_source"}
NOTION_STATUS_VALUES = {"never", "ok", "partial", "error", "unavailable"}
NOTION_ID_PATTERN = re.compile(
    r"(?i)(?:https?://(?:www\.)?notion\.so/(?:[^/?#]+/)?(?:[A-Za-z0-9_-]+-)?)?"
    r"([0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
)
READ_ONLY_ENDPOINTS = (
    ("POST", re.compile(r"/v1/search\Z")),
    ("GET", re.compile(r"/v1/pages/[0-9a-f-]{36}\Z")),
    ("GET", re.compile(r"/v1/blocks/[0-9a-f-]{36}/children\Z")),
    ("GET", re.compile(r"/v1/data_sources/[0-9a-f-]{36}\Z")),
    ("POST", re.compile(r"/v1/data_sources/[0-9a-f-]{36}/query\Z")),
)
MAX_SETTINGS_TARGETS = 200
MAX_DISCOVERY_PAGES = 2
MAX_DISCOVERY_REQUESTS = MAX_DISCOVERY_PAGES * 2
MAX_HTTP_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_ERROR_CHARS = 300
MAX_PURPOSE_CHARS = 500
MAX_DISPLAY_NAME_CHARS = 200
SETTINGS_LOCK_TIMEOUT_SECONDS = 3.0
SETTINGS_LOCK_STALE_SECONDS = 30.0
MIN_CONTEXT_TARGET_CHARS = 200
NON_RECURSIVE_CHILD_BLOCK_TYPES = {"child_page", "child_database"}


class NotionIntegrationError(RuntimeError):
    """Safe, user-displayable Notion integration error."""


class NotionCredentialError(NotionIntegrationError):
    pass


class NotionApiError(NotionIntegrationError):
    def __init__(self, message: str, *, status: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass(frozen=True)
class NotionLimits:
    max_targets_per_request: int = 4
    max_database_rows: int = 20
    max_database_pages: int = 5
    max_chars_per_target: int = 6_000
    max_total_chars: int = 18_000
    max_block_depth: int = 2
    max_api_requests: int = 30
    timeout_seconds: float = 8.0
    total_timeout_seconds: float = 20.0


@dataclass(frozen=True)
class NotionTarget:
    id: str
    object_type: str
    enabled: bool = False
    display_name: str = ""
    purpose: str = ""
    last_fetched_at: str | None = None
    last_status: str = "never"
    last_error: str | None = None


@dataclass(frozen=True)
class NotionSettings:
    schema_version: int = 1
    targets: tuple[NotionTarget, ...] = ()
    limits: NotionLimits = NotionLimits()


@dataclass(frozen=True)
class NotionDiscoveredTarget:
    id: str
    object_type: str
    title: str
    url: str
    in_trash: bool


@dataclass(frozen=True)
class NotionSource:
    id: str
    object_type: str
    title: str
    url: str
    allowed_target_id: str
    allowed_target_title: str
    fetched_at: str


@dataclass(frozen=True)
class NotionContextResult:
    requested: bool
    used: bool
    status: str
    fetched_at: str | None
    sources: tuple[NotionSource, ...] = ()
    error: str | None = None
    context_text: str = ""


@dataclass(frozen=True)
class _FetchedTarget:
    text: str
    sources: tuple[NotionSource, ...]


def notion_settings_path(root: Path | str) -> Path:
    return Path(root).resolve() / NOTION_SETTINGS_RELATIVE_PATH


def _notion_settings_lock_path(root: Path | str) -> Path:
    return notion_settings_path(root).with_suffix(".lock")


@contextmanager
def _notion_settings_lock(root: Path | str):
    """Serialize the short read/merge/write section across bridge processes."""

    lock_path = _notion_settings_lock_path(root)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    owner = f"{os.getpid()}-{time.time_ns()}"
    deadline = time.monotonic() + SETTINGS_LOCK_TIMEOUT_SECONDS
    acquired = False
    while not acquired:
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                stale = time.time() - lock_path.stat().st_mtime > SETTINGS_LOCK_STALE_SECONDS
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise NotionIntegrationError("Notion設定の排他状態を確認できません。") from exc
            if stale:
                try:
                    lock_path.unlink()
                except FileNotFoundError:
                    pass
                except OSError:
                    pass
                continue
            if time.monotonic() >= deadline:
                raise NotionIntegrationError("Notion設定を更新中です。少し待ってから再実行してください。")
            time.sleep(0.05)
        except OSError as exc:
            raise NotionIntegrationError("Notion設定の排他を開始できません。") from exc
        else:
            try:
                os.write(descriptor, owner.encode("ascii"))
            finally:
                os.close(descriptor)
            acquired = True
    try:
        yield
    finally:
        try:
            if lock_path.read_text(encoding="ascii") == owner:
                lock_path.unlink(missing_ok=True)
        except (OSError, UnicodeError):
            pass


def normalize_notion_id(value: Any) -> str:
    text = str(value or "").strip()
    match = NOTION_ID_PATTERN.fullmatch(text) or NOTION_ID_PATTERN.search(text)
    if not match:
        raise ValueError("Notion target idはUUIDまたはNotion URLで指定してください。")
    raw = match.group(1).replace("-", "").lower()
    return f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"


def load_notion_settings(root: Path | str) -> NotionSettings:
    path = notion_settings_path(root)
    if not path.exists():
        return NotionSettings()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise NotionIntegrationError("Notion設定を読み取れません。設定ファイルを確認してください。") from exc
    if not isinstance(payload, dict) or payload.get("schema_version", 1) != 1:
        raise NotionIntegrationError("Notion設定のschema_versionに対応していません。")

    raw_targets = payload.get("targets", [])
    if not isinstance(raw_targets, list) or len(raw_targets) > MAX_SETTINGS_TARGETS:
        raise NotionIntegrationError("Notion設定のtargetsが不正です。")
    targets: list[NotionTarget] = []
    seen: set[str] = set()
    for raw in raw_targets:
        if not isinstance(raw, dict):
            raise NotionIntegrationError("Notion設定のtargetが不正です。")
        try:
            target = _target_from_mapping(raw)
        except (TypeError, ValueError) as exc:
            raise NotionIntegrationError("Notion設定のtargetが不正です。") from exc
        if target.id in seen:
            raise NotionIntegrationError("Notion設定に重複したtarget idがあります。")
        seen.add(target.id)
        targets.append(target)

    raw_limits = payload.get("limits", {})
    if not isinstance(raw_limits, dict):
        raise NotionIntegrationError("Notion設定のlimitsが不正です。")
    limits = _limits_from_mapping(raw_limits)
    return NotionSettings(targets=tuple(targets), limits=limits)


def save_notion_settings(root: Path | str, settings: NotionSettings) -> Path:
    with _notion_settings_lock(root):
        return _save_notion_settings_unlocked(root, settings)


def _save_notion_settings_unlocked(root: Path | str, settings: NotionSettings) -> Path:
    path = notion_settings_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "targets": [asdict(target) for target in settings.targets],
        "limits": asdict(settings.limits),
    }
    temporary = path.with_name(f"{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def get_notion_settings_view(
    root: Path | str,
    *,
    refresh: bool = False,
    client: "NotionClient | None" = None,
    _discovered_targets: Iterable[NotionDiscoveredTarget] | None = None,
) -> dict[str, Any]:
    settings = load_notion_settings(root)
    credential_present = False
    credential_error: str | None = None
    if client is None:
        try:
            credential_value = read_notion_token()
            credential_present = bool(credential_value)
            if credential_value:
                client = NotionClient(credential_value, timeout_seconds=settings.limits.timeout_seconds)
        except NotionCredentialError as exc:
            credential_error = str(exc)
    else:
        credential_present = True

    discovered: tuple[NotionDiscoveredTarget, ...] = ()
    connected = False
    status = "not_checked"
    connection_error = credential_error
    if not credential_present:
        status = "credential_missing" if credential_error is None else "credential_error"
        connection_error = connection_error or "Windows Credential ManagerにNotion tokenが登録されていません。"
    elif refresh:
        try:
            deadline = time.monotonic() + settings.limits.total_timeout_seconds
            discovered = (
                tuple(_discovered_targets)
                if _discovered_targets is not None
                else client.search_targets(deadline=deadline, max_requests=MAX_DISCOVERY_REQUESTS) if client else ()
            )
            connected = True
            status = "connected"
        except NotionIntegrationError as exc:
            status = "connection_error"
            connection_error = _safe_error(str(exc))
    else:
        status = "credential_ready"

    merged = _merge_settings_targets(settings.targets, discovered, refreshed=refresh and connected)
    return {
        "settings_file": str(NOTION_SETTINGS_RELATIVE_PATH).replace("\\", "/"),
        "connection": {
            "credential_present": credential_present,
            "connected": connected,
            "status": status,
            "error": connection_error,
            "api_version": NOTION_API_VERSION,
        },
        "targets": merged,
        "limits": asdict(settings.limits),
        "storage_policy": {
            "credential": "Windows Credential Manager",
            "allowlist": str(NOTION_SETTINGS_RELATIVE_PATH).replace("\\", "/"),
            "fetched_body": "ephemeral_only",
            "assistant_reply": "saved_in_live_conversation",
        },
    }


def update_notion_allowlist(
    root: Path | str,
    target_updates: Any,
    *,
    client: "NotionClient | None" = None,
) -> dict[str, Any]:
    if not isinstance(target_updates, list) or len(target_updates) > MAX_SETTINGS_TARGETS:
        raise ValueError("targetsは200件以内の配列で指定してください。")
    requires_discovery = False
    for raw in target_updates:
        if not isinstance(raw, dict):
            raise ValueError("targetが不正です。")
        enabled = raw.get("enabled", False)
        if not isinstance(enabled, bool):
            raise ValueError("enabledはtrueまたはfalseで指定してください。")
        requires_discovery = requires_discovery or enabled

    initial_settings = load_notion_settings(root)
    discovered_items: tuple[NotionDiscoveredTarget, ...] | None = None
    if requires_discovery:
        if client is None:
            credential_value = read_notion_token()
            if not credential_value:
                raise NotionCredentialError("Windows Credential ManagerにNotion tokenが登録されていません。")
            client = NotionClient(credential_value, timeout_seconds=initial_settings.limits.timeout_seconds)
        deadline = time.monotonic() + initial_settings.limits.total_timeout_seconds
        discovered_items = client.search_targets(deadline=deadline, max_requests=MAX_DISCOVERY_REQUESTS)
    discovered = {item.id: item for item in discovered_items or () if not item.in_trash}

    with _notion_settings_lock(root):
        settings = load_notion_settings(root)
        existing = {target.id: target for target in settings.targets}
        updated: list[NotionTarget] = []
        seen: set[str] = set()
        for raw in target_updates:
            target_id = normalize_notion_id(raw.get("id"))
            if target_id in seen:
                raise ValueError("target idが重複しています。")
            seen.add(target_id)
            enabled = raw.get("enabled", False)
            discovered_target = discovered.get(target_id)
            prior = existing.get(target_id)
            if enabled and discovered_target is None:
                raise ValueError("接続から現在参照できないtargetは有効化できません。")
            if discovered_target is None and prior is None:
                continue
            if prior is None and not enabled:
                # Discovery results are a picker, not local allowlist entries,
                # until the user explicitly enables one.
                continue
            object_type = discovered_target.object_type if discovered_target else prior.object_type
            requested_type = str(raw.get("object_type") or object_type)
            if requested_type != object_type:
                raise ValueError("targetのobject_typeが接続先と一致しません。")
            display_name = _bounded_text(
                raw.get("display_name") or (prior.display_name if prior else discovered_target.title),
                MAX_DISPLAY_NAME_CHARS,
                "display_name",
            )
            purpose = _bounded_text(raw.get("purpose") or "", MAX_PURPOSE_CHARS, "purpose")
            updated.append(
                NotionTarget(
                    id=target_id,
                    object_type=object_type,
                    enabled=enabled,
                    display_name=display_name,
                    purpose=purpose,
                    last_fetched_at=prior.last_fetched_at if prior else None,
                    last_status=prior.last_status if prior else "never",
                    last_error=prior.last_error if prior else None,
                )
            )
        saved = replace(settings, targets=tuple(updated))
        _save_notion_settings_unlocked(root, saved)
    return get_notion_settings_view(
        root,
        refresh=discovered_items is not None,
        client=client,
        _discovered_targets=discovered_items,
    )


def retrieve_notion_context(
    root: Path | str,
    question: str,
    *,
    client: "NotionClient | None" = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> NotionContextResult:
    now = _now_iso()
    try:
        settings = load_notion_settings(root)
    except NotionIntegrationError as exc:
        return NotionContextResult(
            requested=True,
            used=False,
            status="error",
            fetched_at=now,
            error=_safe_error(str(exc)),
        )
    enabled = [target for target in settings.targets if target.enabled]
    if not enabled:
        return NotionContextResult(
            requested=True,
            used=False,
            status="error",
            fetched_at=now,
            error="Notion設定で参照を許可したtargetがありません。",
        )
    attempted_ids = {target.id for target in enabled}
    successful_ids: set[str] = set()
    try:
        if client is None:
            credential_value = read_notion_token()
            if not credential_value:
                raise NotionCredentialError("Windows Credential ManagerにNotion tokenが登録されていません。")
            client = NotionClient(credential_value, timeout_seconds=settings.limits.timeout_seconds)
        selected = _select_targets(enabled, question, settings.limits.max_targets_per_request)
        attempted_ids = set()
        deadline = time.monotonic() + settings.limits.total_timeout_seconds
        errors: dict[str, str] = {}
        context_parts: list[str] = []
        sources: list[NotionSource] = []
        total_chars = 0
        for target in selected:
            if is_cancelled and is_cancelled():
                raise InterruptedError("Notion取得を停止しました。")
            separator_chars = 2 if context_parts else 0
            remaining = settings.limits.max_total_chars - total_chars - separator_chars
            if remaining < MIN_CONTEXT_TARGET_CHARS:
                break
            attempted_ids.add(target.id)
            try:
                value = client.fetch_target_context(
                    target,
                    question=question,
                    limits=replace(
                        settings.limits,
                        max_chars_per_target=min(settings.limits.max_chars_per_target, remaining),
                    ),
                    deadline=deadline,
                    is_cancelled=is_cancelled,
                )
                if value.text.strip():
                    text = value.text[:remaining]
                    context_parts.append(text)
                    total_chars += separator_chars + len(text)
                    sources.extend(value.sources)
                    successful_ids.add(target.id)
                else:
                    errors[target.id] = "本文を取得できませんでした。"
            except NotionIntegrationError as exc:
                errors[target.id] = _safe_error(str(exc))

        if context_parts:
            status = "partial" if errors else "ok"
            error = _format_target_errors(errors, selected) if errors else None
            result = NotionContextResult(
                requested=True,
                used=True,
                status=status,
                fetched_at=now,
                sources=_dedupe_sources(sources),
                error=error,
                context_text="\n\n".join(context_parts),
            )
        else:
            result = NotionContextResult(
                requested=True,
                used=False,
                status="error",
                fetched_at=now,
                error=_format_target_errors(errors, selected) or "Notion本文を取得できませんでした。",
            )
    except InterruptedError:
        raise
    except NotionIntegrationError as exc:
        result = NotionContextResult(
            requested=True,
            used=False,
            status="error",
            fetched_at=now,
            error=_safe_error(str(exc)),
        )
    except Exception:
        result = NotionContextResult(
            requested=True,
            used=False,
            status="error",
            fetched_at=now,
            error="Notion参照処理で予期しないエラーが発生しました。ローカル情報だけで回答します。",
        )

    _record_retrieval_status(root, attempted_ids, successful_ids, result)
    return result


class _RejectRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise urllib.error.HTTPError(req.full_url, code, "Notion redirectを拒否しました。", headers, fp)


class NotionClient:
    """Small REST client whose endpoint allowlist contains read operations only."""

    def __init__(
        self,
        credential_value: str,
        *,
        timeout_seconds: float = 8.0,
        opener: Callable[..., Any] | None = None,
    ):
        credential_value = str(credential_value or "").strip()
        if not credential_value:
            raise NotionCredentialError("Notion tokenが空です。")
        if any(ord(character) < 32 or ord(character) == 127 for character in credential_value):
            raise NotionCredentialError("Notion tokenの形式が不正です。")
        self._authorization_value = credential_value
        self.timeout_seconds = max(1.0, min(float(timeout_seconds), 30.0))
        self._opener = opener or urllib.request.build_opener(_RejectRedirectHandler()).open
        self._request_count = 0

    def search_targets(
        self,
        *,
        deadline: float | None = None,
        max_requests: int = MAX_DISCOVERY_REQUESTS,
    ) -> tuple[NotionDiscoveredTarget, ...]:
        results: list[NotionDiscoveredTarget] = []
        cursor: str | None = None
        for _ in range(MAX_DISCOVERY_PAGES):
            body: dict[str, Any] = {"page_size": 100}
            if cursor:
                body["start_cursor"] = cursor
            payload = self._request(
                "POST",
                "/v1/search",
                body=body,
                deadline=deadline,
                max_requests=max_requests,
            )
            raw_results = payload.get("results", [])
            if not isinstance(raw_results, list):
                raise NotionApiError("Notion search responseが不正です。")
            for raw in raw_results:
                if not isinstance(raw, dict):
                    continue
                object_type = str(raw.get("object") or "")
                if object_type not in NOTION_OBJECT_TYPES:
                    continue
                try:
                    target_id = normalize_notion_id(raw.get("id"))
                except ValueError:
                    continue
                results.append(
                    NotionDiscoveredTarget(
                        id=target_id,
                        object_type=object_type,
                        title=_object_title(raw) or "Untitled",
                        url=_notion_object_url(raw, target_id, object_type),
                        in_trash=bool(raw.get("in_trash", False)),
                    )
                )
            if not payload.get("has_more"):
                break
            next_cursor = payload.get("next_cursor")
            if not isinstance(next_cursor, str) or not next_cursor:
                break
            cursor = next_cursor
        unique: dict[str, NotionDiscoveredTarget] = {}
        for item in results:
            unique[item.id] = item
        ordered = sorted(unique.values(), key=lambda item: (item.in_trash, item.title.casefold(), item.id))
        return tuple(ordered[:MAX_SETTINGS_TARGETS])

    def fetch_target_context(
        self,
        target: NotionTarget,
        *,
        question: str,
        limits: NotionLimits,
        deadline: float,
        is_cancelled: Callable[[], bool] | None,
    ) -> _FetchedTarget:
        if target.object_type == "page":
            return self._fetch_page_target(target, limits=limits, deadline=deadline, is_cancelled=is_cancelled)
        if target.object_type == "data_source":
            return self._fetch_data_source_target(
                target,
                question=question,
                limits=limits,
                deadline=deadline,
                is_cancelled=is_cancelled,
            )
        raise NotionIntegrationError("未対応のNotion target typeです。")

    def _fetch_page_target(
        self,
        target: NotionTarget,
        *,
        limits: NotionLimits,
        deadline: float,
        is_cancelled: Callable[[], bool] | None,
    ) -> _FetchedTarget:
        page = self._request("GET", f"/v1/pages/{target.id}", deadline=deadline, max_requests=limits.max_api_requests)
        if page.get("object") != "page" or bool(page.get("in_trash", False)):
            raise NotionIntegrationError("許可targetは削除済み、またはページではありません。")
        fetched_at = _now_iso()
        title = _object_title(page) or target.display_name or "Untitled"
        url = _safe_notion_url(page.get("url"), target.id)
        properties = _page_properties_text(page.get("properties"))
        body = self._read_block_tree(
            target.id,
            limits=limits,
            deadline=deadline,
            is_cancelled=is_cancelled,
        )
        content = "\n".join(part for part in (properties, body) if part).strip()
        text = _format_context_entry(
            title=title,
            object_type="page",
            url=url,
            fetched_at=fetched_at,
            allowed_title=target.display_name or title,
            content=content[: limits.max_chars_per_target],
        )[: limits.max_chars_per_target]
        source = NotionSource(
            id=target.id,
            object_type="page",
            title=title,
            url=url,
            allowed_target_id=target.id,
            allowed_target_title=target.display_name or title,
            fetched_at=fetched_at,
        )
        return _FetchedTarget(text=text, sources=(source,))

    def _fetch_data_source_target(
        self,
        target: NotionTarget,
        *,
        question: str,
        limits: NotionLimits,
        deadline: float,
        is_cancelled: Callable[[], bool] | None,
    ) -> _FetchedTarget:
        data_source = self._request(
            "GET",
            f"/v1/data_sources/{target.id}",
            deadline=deadline,
            max_requests=limits.max_api_requests,
        )
        if data_source.get("object") != "data_source" or bool(data_source.get("in_trash", False)):
            raise NotionIntegrationError("許可targetは削除済み、またはdata sourceではありません。")
        data_source_title = _object_title(data_source) or target.display_name or "Untitled data source"
        body = {
            "page_size": limits.max_database_rows,
            "sorts": [{"timestamp": "last_edited_time", "direction": "descending"}],
            "result_type": "page",
        }
        query = self._request(
            "POST",
            f"/v1/data_sources/{target.id}/query",
            body=body,
            deadline=deadline,
            max_requests=limits.max_api_requests,
        )
        raw_rows = query.get("results", [])
        if not isinstance(raw_rows, list):
            raise NotionApiError("Notion data source responseが不正です。")
        rows = [item for item in raw_rows if isinstance(item, dict) and item.get("object") == "page"]
        ranked = sorted(
            rows,
            key=lambda page: (_text_match_score(question, f"{_object_title(page)} {_page_properties_text(page.get('properties'))}"), str(page.get("last_edited_time") or "")),
            reverse=True,
        )[: limits.max_database_pages]
        fetched_at = _now_iso()
        chunks: list[str] = []
        sources: list[NotionSource] = []
        remaining = limits.max_chars_per_target
        for page in ranked:
            if remaining <= 0:
                break
            if is_cancelled and is_cancelled():
                raise InterruptedError("Notion取得を停止しました。")
            if bool(page.get("in_trash", False)):
                continue
            try:
                page_id = normalize_notion_id(page.get("id"))
            except ValueError:
                continue
            title = _object_title(page) or "Untitled"
            properties = _page_properties_text(page.get("properties"))
            body_text = self._read_block_tree(
                page_id,
                limits=limits,
                deadline=deadline,
                is_cancelled=is_cancelled,
            )
            page_text = "\n".join(part for part in (properties, body_text) if part).strip()[:remaining]
            url = _safe_notion_url(page.get("url"), page_id)
            chunks.append(f"Row: {title}\nURL: {url}\n{page_text}".strip())
            remaining -= len(page_text)
            sources.append(
                NotionSource(
                    id=page_id,
                    object_type="page",
                    title=title,
                    url=url,
                    allowed_target_id=target.id,
                    allowed_target_title=target.display_name or data_source_title,
                    fetched_at=fetched_at,
                )
            )
        data_source_url = _notion_object_url(data_source, target.id, "data_source")
        content = "\n\n".join(chunks) if chunks else "No readable rows were returned."
        text = _format_context_entry(
            title=data_source_title,
            object_type="data_source",
            url=data_source_url,
            fetched_at=fetched_at,
            allowed_title=target.display_name or data_source_title,
            content=content,
        )[: limits.max_chars_per_target]
        if not sources:
            sources.append(
                NotionSource(
                    id=target.id,
                    object_type="data_source",
                    title=data_source_title,
                    url=data_source_url,
                    allowed_target_id=target.id,
                    allowed_target_title=target.display_name or data_source_title,
                    fetched_at=fetched_at,
                )
            )
        return _FetchedTarget(text=text, sources=tuple(sources))

    def _read_block_tree(
        self,
        block_id: str,
        *,
        limits: NotionLimits,
        deadline: float,
        is_cancelled: Callable[[], bool] | None,
        depth: int = 0,
    ) -> str:
        if depth > limits.max_block_depth:
            return ""
        lines: list[str] = []
        cursor: str | None = None
        while True:
            if is_cancelled and is_cancelled():
                raise InterruptedError("Notion取得を停止しました。")
            query = {"page_size": "100"}
            if cursor:
                query["start_cursor"] = cursor
            suffix = urllib.parse.urlencode(query)
            payload = self._request(
                "GET",
                f"/v1/blocks/{block_id}/children?{suffix}",
                deadline=deadline,
                max_requests=limits.max_api_requests,
            )
            blocks = payload.get("results", [])
            if not isinstance(blocks, list):
                raise NotionApiError("Notion block responseが不正です。")
            for block in blocks:
                if not isinstance(block, dict) or bool(block.get("in_trash", False)):
                    continue
                line = _block_text(block)
                if line:
                    lines.append(line)
                if (
                    block.get("has_children")
                    and block.get("type") not in NON_RECURSIVE_CHILD_BLOCK_TYPES
                    and depth < limits.max_block_depth
                ):
                    try:
                        child_id = normalize_notion_id(block.get("id"))
                    except ValueError:
                        continue
                    child = self._read_block_tree(
                        child_id,
                        limits=limits,
                        deadline=deadline,
                        is_cancelled=is_cancelled,
                        depth=depth + 1,
                    )
                    if child:
                        lines.append(child)
                if len("\n".join(lines)) >= limits.max_chars_per_target:
                    return "\n".join(lines)[: limits.max_chars_per_target]
            if not payload.get("has_more"):
                break
            next_cursor = payload.get("next_cursor")
            if not isinstance(next_cursor, str) or not next_cursor:
                break
            cursor = next_cursor
        return "\n".join(lines)[: limits.max_chars_per_target]

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        deadline: float | None = None,
        max_requests: int = 100,
        _rate_limit_retried: bool = False,
    ) -> dict[str, Any]:
        method = method.upper()
        parsed_path = urllib.parse.urlsplit(path).path
        if not any(allowed_method == method and pattern.fullmatch(parsed_path) for allowed_method, pattern in READ_ONLY_ENDPOINTS):
            raise NotionIntegrationError("Notionの読み取り専用境界に含まれないendpointは呼び出せません。")
        if self._request_count >= max_requests:
            raise NotionIntegrationError("Notion APIの取得件数上限に達しました。")
        if deadline is not None and time.monotonic() >= deadline:
            raise NotionIntegrationError("Notion取得が全体timeoutを超えました。")
        self._request_count += 1
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        request = urllib.request.Request(
            f"{NOTION_API_BASE}{path}",
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self._authorization_value}",
                "Notion-Version": NOTION_API_VERSION,
                "Content-Type": "application/json",
                "User-Agent": "AI-LifeOS-Notion-ReadOnly/1",
            },
        )
        remaining = self.timeout_seconds if deadline is None else max(0.1, min(self.timeout_seconds, deadline - time.monotonic()))
        try:
            with self._opener(request, timeout=remaining) as response:
                raw = response.read(MAX_HTTP_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            error_body = exc.read(MAX_ERROR_CHARS * 4)
            code, message = _parse_api_error(error_body)
            if exc.code == 429 and not _rate_limit_retried:
                retry_after = _retry_after_seconds(exc.headers.get("Retry-After"))
                if retry_after > 0 and retry_after <= 2 and (deadline is None or time.monotonic() + retry_after < deadline):
                    time.sleep(retry_after)
                    return self._request(
                        method,
                        path,
                        body=body,
                        deadline=deadline,
                        max_requests=max_requests,
                        _rate_limit_retried=True,
                    )
            raise NotionApiError(_http_error_message(exc.code, code, message), status=exc.code, code=code) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise NotionApiError("Notionへ接続できませんでした。接続とtimeout設定を確認してください。") from exc
        if len(raw) > MAX_HTTP_RESPONSE_BYTES:
            raise NotionApiError("Notion responseが上限を超えました。")
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise NotionApiError("Notion response JSONを読み取れません。") from exc
        if not isinstance(value, dict):
            raise NotionApiError("Notion responseが不正です。")
        return value


def read_notion_token() -> str | None:
    credential = _credential_read(NOTION_CREDENTIAL_TARGET)
    return credential.strip() if credential else None


def write_notion_token(credential_value: str) -> None:
    credential_value = str(credential_value or "").strip()
    if not credential_value:
        raise ValueError("Notion tokenが空です。")
    _credential_write(NOTION_CREDENTIAL_TARGET, credential_value)


def delete_notion_token() -> bool:
    return _credential_delete(NOTION_CREDENTIAL_TARGET)


def _credential_read(target: str) -> str | None:
    advapi32, credential_type = _credential_api()
    pointer = ctypes.POINTER(credential_type)()
    if not advapi32.CredReadW(target, 1, 0, ctypes.byref(pointer)):
        error = ctypes.get_last_error()
        if error == 1168:  # ERROR_NOT_FOUND
            return None
        raise NotionCredentialError("Windows Credential ManagerからNotion tokenを読み取れません。")
    try:
        credential = pointer.contents
        if not credential.CredentialBlob or credential.CredentialBlobSize == 0:
            return ""
        raw = ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize)
        try:
            return raw.decode("utf-16-le")
        except UnicodeDecodeError as exc:
            raise NotionCredentialError("Windows Credential ManagerのNotion token形式が不正です。") from exc
    finally:
        advapi32.CredFree(pointer)


def _credential_write(target: str, secret: str) -> None:
    advapi32, credential_type = _credential_api()
    blob = secret.encode("utf-16-le")
    buffer = ctypes.create_string_buffer(blob)
    credential = credential_type()
    credential.Type = 1  # CRED_TYPE_GENERIC
    credential.TargetName = target
    credential.CredentialBlobSize = len(blob)
    credential.CredentialBlob = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
    credential.Persist = 2  # CRED_PERSIST_LOCAL_MACHINE
    credential.UserName = "AI-LifeOS Notion read-only integration"
    if not advapi32.CredWriteW(ctypes.byref(credential), 0):
        raise NotionCredentialError("Windows Credential ManagerへNotion tokenを保存できません。")


def _credential_delete(target: str) -> bool:
    advapi32, _ = _credential_api()
    if advapi32.CredDeleteW(target, 1, 0):
        return True
    error = ctypes.get_last_error()
    if error == 1168:
        return False
    raise NotionCredentialError("Windows Credential ManagerからNotion tokenを削除できません。")


def _credential_api():
    if os.name != "nt":
        raise NotionCredentialError("Notion credentialはWindows Credential Managerで管理します。")

    from ctypes import wintypes

    class CREDENTIALW(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", wintypes.FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]

    advapi32 = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    advapi32.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    advapi32.CredReadW.restype = wintypes.BOOL
    advapi32.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIALW), wintypes.DWORD]
    advapi32.CredWriteW.restype = wintypes.BOOL
    advapi32.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    advapi32.CredDeleteW.restype = wintypes.BOOL
    advapi32.CredFree.argtypes = [ctypes.c_void_p]
    advapi32.CredFree.restype = None
    return advapi32, CREDENTIALW


def _target_from_mapping(raw: dict[str, Any]) -> NotionTarget:
    object_type = str(raw.get("object_type") or "")
    if object_type not in NOTION_OBJECT_TYPES:
        raise ValueError("object_type")
    enabled = raw.get("enabled", False)
    if not isinstance(enabled, bool):
        raise TypeError("enabled")
    last_status = str(raw.get("last_status") or "never")
    if last_status not in NOTION_STATUS_VALUES:
        raise ValueError("last_status")
    return NotionTarget(
        id=normalize_notion_id(raw.get("id")),
        object_type=object_type,
        enabled=enabled,
        display_name=_bounded_text(raw.get("display_name") or "", MAX_DISPLAY_NAME_CHARS, "display_name"),
        purpose=_bounded_text(raw.get("purpose") or "", MAX_PURPOSE_CHARS, "purpose"),
        last_fetched_at=_optional_iso_text(raw.get("last_fetched_at")),
        last_status=last_status,
        last_error=_optional_bounded_text(raw.get("last_error"), MAX_ERROR_CHARS),
    )


def _limits_from_mapping(raw: dict[str, Any]) -> NotionLimits:
    defaults = NotionLimits()
    return NotionLimits(
        max_targets_per_request=_bounded_int(raw.get("max_targets_per_request"), defaults.max_targets_per_request, 1, 10),
        max_database_rows=_bounded_int(raw.get("max_database_rows"), defaults.max_database_rows, 1, 100),
        max_database_pages=_bounded_int(raw.get("max_database_pages"), defaults.max_database_pages, 1, 20),
        max_chars_per_target=_bounded_int(raw.get("max_chars_per_target"), defaults.max_chars_per_target, 500, 20_000),
        max_total_chars=_bounded_int(raw.get("max_total_chars"), defaults.max_total_chars, 1_000, 50_000),
        max_block_depth=_bounded_int(raw.get("max_block_depth"), defaults.max_block_depth, 0, 5),
        max_api_requests=_bounded_int(raw.get("max_api_requests"), defaults.max_api_requests, 1, 100),
        timeout_seconds=_bounded_float(raw.get("timeout_seconds"), defaults.timeout_seconds, 1.0, 30.0),
        total_timeout_seconds=_bounded_float(raw.get("total_timeout_seconds"), defaults.total_timeout_seconds, 2.0, 60.0),
    )


def _merge_settings_targets(
    configured: Iterable[NotionTarget],
    discovered: Iterable[NotionDiscoveredTarget],
    *,
    refreshed: bool,
) -> list[dict[str, Any]]:
    configured_map = {item.id: item for item in configured}
    discovered_map = {item.id: item for item in discovered}
    ids = list(configured_map)
    ids.extend(item_id for item_id in discovered_map if item_id not in configured_map)
    ids = ids[:MAX_SETTINGS_TARGETS]
    merged = []
    for target_id in ids:
        saved = configured_map.get(target_id)
        remote = discovered_map.get(target_id)
        merged.append(
            {
                "id": target_id,
                "object_type": remote.object_type if remote else saved.object_type,
                "enabled": saved.enabled if saved else False,
                "display_name": saved.display_name if saved and saved.display_name else (remote.title if remote else ""),
                "notion_title": remote.title if remote else (saved.display_name if saved else ""),
                "purpose": saved.purpose if saved else "",
                "url": remote.url if remote else _safe_notion_url(None, target_id),
                "available": (remote is not None and not remote.in_trash) if refreshed else None,
                "in_trash": remote.in_trash if remote else False,
                "last_fetched_at": saved.last_fetched_at if saved else None,
                "last_status": saved.last_status if saved else "never",
                "last_error": saved.last_error if saved else None,
            }
        )
    return merged


def _select_targets(targets: list[NotionTarget], question: str, maximum: int) -> list[NotionTarget]:
    indexed = list(enumerate(targets))
    indexed.sort(
        key=lambda pair: (_text_match_score(question, f"{pair[1].display_name} {pair[1].purpose}"), -pair[0]),
        reverse=True,
    )
    return [target for _, target in indexed[:maximum]]


def _text_match_score(query: str, candidate: str) -> int:
    query_text = _normalize_search_text(query)
    candidate_text = _normalize_search_text(candidate)
    if not query_text or not candidate_text:
        return 0
    score = 10 if query_text in candidate_text else 0
    query_tokens = _search_tokens(query_text)
    candidate_tokens = _search_tokens(candidate_text)
    score += sum(3 for token in query_tokens if token in candidate_text)
    score += len(query_tokens & candidate_tokens)
    return score


def _search_tokens(value: str) -> set[str]:
    tokens = {item for item in re.findall(r"[a-z0-9_]{2,}|[一-龠々ぁ-んァ-ヶー]{2,}", value) if len(item) <= 40}
    compact = re.sub(r"\s+", "", value)
    if len(compact) <= 80:
        tokens.update(compact[index : index + 2] for index in range(max(0, len(compact) - 1)))
    return tokens


def _normalize_search_text(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def _record_retrieval_status(
    root: Path | str,
    attempted_ids: set[str],
    successful_ids: set[str],
    result: NotionContextResult,
) -> None:
    try:
        with _notion_settings_lock(root):
            latest = load_notion_settings(root)
            updated = []
            for target in latest.targets:
                if target.id not in attempted_ids:
                    updated.append(target)
                    continue
                target_status = "ok" if target.id in successful_ids else "error"
                updated.append(
                    replace(
                        target,
                        last_fetched_at=result.fetched_at,
                        last_status=target_status,
                        last_error=result.error if target_status != "ok" else None,
                    )
                )
            _save_notion_settings_unlocked(root, replace(latest, targets=tuple(updated)))
    except (OSError, NotionIntegrationError):
        pass


def _page_properties_text(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    lines = []
    for name, raw in list(value.items())[:30]:
        if not isinstance(raw, dict):
            continue
        text = _property_value_text(raw)
        if text:
            lines.append(f"{name}: {text}")
    return "\n".join(lines)


def _property_value_text(raw: dict[str, Any]) -> str:
    property_type = str(raw.get("type") or "")
    value = raw.get(property_type)
    if property_type in {"title", "rich_text"}:
        return _plain_rich_text(value)
    if property_type in {"number", "url", "email", "phone_number", "checkbox"}:
        return "" if value is None else str(value)
    if property_type in {"select", "status"} and isinstance(value, dict):
        return str(value.get("name") or "")
    if property_type == "multi_select" and isinstance(value, list):
        return ", ".join(str(item.get("name") or "") for item in value if isinstance(item, dict))
    if property_type == "date" and isinstance(value, dict):
        return " - ".join(str(value.get(key) or "") for key in ("start", "end") if value.get(key))
    if property_type == "formula" and isinstance(value, dict):
        formula_type = value.get("type")
        if not isinstance(formula_type, str):
            return ""
        formula_value = value.get(formula_type)
        return "" if formula_value is None else str(formula_value)
    if property_type == "unique_id" and isinstance(value, dict):
        return f"{value.get('prefix') or ''}{value.get('number') or ''}"
    return ""


def _block_text(block: dict[str, Any]) -> str:
    block_type = str(block.get("type") or "")
    value = block.get(block_type)
    if not isinstance(value, dict):
        return ""
    text = _plain_rich_text(value.get("rich_text"))
    if block_type == "to_do":
        return f"[{'x' if value.get('checked') else ' '}] {text}".strip()
    if block_type.startswith("heading_"):
        level = block_type.split("_")[-1]
        return f"{'#' * int(level)} {text}".strip() if level.isdigit() else text
    if block_type in {"bulleted_list_item", "numbered_list_item"}:
        return f"- {text}".strip()
    if block_type == "quote":
        return f"> {text}".strip()
    if block_type == "code":
        language = value.get("language") or ""
        return f"```{language}\n{text}\n```"
    if block_type == "equation":
        return str(value.get("expression") or "")
    if block_type in {"child_page", "child_database"}:
        return f"{block_type}: {value.get('title') or ''}".strip()
    if block_type in {"bookmark", "embed", "link_preview"}:
        return str(value.get("url") or "")
    if text:
        return text
    caption = _plain_rich_text(value.get("caption"))
    return caption


def _object_title(value: dict[str, Any]) -> str:
    title = _plain_rich_text(value.get("title"))
    if title:
        return title
    properties = value.get("properties")
    if isinstance(properties, dict):
        for raw in properties.values():
            if isinstance(raw, dict) and raw.get("type") == "title":
                title = _plain_rich_text(raw.get("title"))
                if title:
                    return title
    return ""


def _plain_rich_text(value: Any) -> str:
    if not isinstance(value, list):
        return ""
    parts = []
    for item in value[:100]:
        if not isinstance(item, dict):
            continue
        text = item.get("plain_text")
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts).strip()


def _format_context_entry(
    *,
    title: str,
    object_type: str,
    url: str,
    fetched_at: str,
    allowed_title: str,
    content: str,
) -> str:
    return (
        "[Allowed Notion source]\n"
        f"Allowed target: {allowed_title}\n"
        f"Title: {title}\n"
        f"Type: {object_type}\n"
        f"URL: {url}\n"
        f"Fetched at: {fetched_at}\n"
        "Content:\n"
        f"{content.strip()}"
    ).strip()


def _format_target_errors(errors: dict[str, str], selected: list[NotionTarget]) -> str | None:
    if not errors:
        return None
    names = {target.id: target.display_name or target.id for target in selected}
    parts = [f"{names.get(target_id, target_id)}: {message}" for target_id, message in list(errors.items())[:5]]
    return _safe_error(" / ".join(parts))


def _dedupe_sources(values: list[NotionSource]) -> tuple[NotionSource, ...]:
    result = []
    seen = set()
    for value in values:
        key = (value.id, value.allowed_target_id)
        if key not in seen:
            seen.add(key)
            result.append(value)
    return tuple(result[:20])


def _notion_object_url(value: dict[str, Any], target_id: str, object_type: str) -> str:
    direct = str(value.get("url") or "").strip()
    try:
        parsed = urllib.parse.urlsplit(direct)
    except ValueError:
        parsed = None
    if parsed and parsed.scheme == "https" and parsed.hostname and parsed.hostname.casefold() in {"notion.so", "www.notion.so"}:
        return direct[:2_000]
    if object_type == "data_source":
        parent = value.get("parent")
        if isinstance(parent, dict) and parent.get("database_id"):
            try:
                return _safe_notion_url(None, normalize_notion_id(parent["database_id"]))
            except ValueError:
                pass
    return _safe_notion_url(None, target_id)


def _safe_notion_url(value: Any, target_id: str) -> str:
    text = str(value or "").strip()
    try:
        parsed = urllib.parse.urlsplit(text)
    except ValueError:
        parsed = None
    if parsed and parsed.scheme == "https" and parsed.hostname and parsed.hostname.casefold() in {"notion.so", "www.notion.so"}:
        return text[:2_000]
    return f"https://www.notion.so/{target_id.replace('-', '')}"


def _parse_api_error(raw: bytes) -> tuple[str | None, str | None]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, None
    if not isinstance(value, dict):
        return None, None
    code = str(value.get("code") or "")[:80] or None
    message = _safe_error(str(value.get("message") or "")) or None
    return code, message


def _http_error_message(status: int, code: str | None, message: str | None) -> str:
    if status in {401, 403}:
        return "Notionの認証または読み取り権限を確認してください。"
    if status == 404:
        return "Notion targetが未共有、権限喪失、または削除済みです。"
    if status == 429:
        return "Notion APIのrate limitに達しました。時間をおいて再実行してください。"
    if status in {500, 502, 503, 504}:
        return "Notion側で一時的なエラーが発生しました。"
    # API response messages can echo private workspace values.  Keep only the
    # bounded machine-readable code in user-facing errors and logs.
    del message
    return f"Notion API error ({status}){': ' + code if code else ''}"


def _retry_after_seconds(value: Any) -> float:
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


def _bounded_text(value: Any, maximum: int, label: str) -> str:
    if not isinstance(value, str):
        raise TypeError(label)
    text = " ".join(value.split())
    if len(text) > maximum:
        raise ValueError(label)
    return text


def _optional_bounded_text(value: Any, maximum: int) -> str | None:
    if value is None:
        return None
    return _bounded_text(value, maximum, "text") or None


def _optional_iso_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("timestamp")
    datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        raise NotionIntegrationError("Notion limitsが不正です。")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise NotionIntegrationError("Notion limitsが不正です。") from exc
    if not math.isfinite(number) or number < minimum or number > maximum:
        raise NotionIntegrationError("Notion limitsが範囲外です。")
    return number


def _bounded_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    if value is None:
        return default
    if isinstance(value, bool):
        raise NotionIntegrationError("Notion limitsが不正です。")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise NotionIntegrationError("Notion limitsが不正です。") from exc
    if not math.isfinite(number) or number < minimum or number > maximum:
        raise NotionIntegrationError("Notion limitsが範囲外です。")
    return number


def _safe_error(value: str) -> str:
    return " ".join(str(value or "").split())[:MAX_ERROR_CHARS]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage the AI-LifeOS read-only Notion connection.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    credential = subparsers.add_parser("credential", help="Manage the token in Windows Credential Manager.")
    credential.add_argument("action", choices=("set", "status", "delete"))
    connection = subparsers.add_parser("connection", help="Check the Notion connection and list shared targets.")
    connection.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "credential":
        if args.action == "set":
            credential_value = getpass.getpass("Notion installation credential: ").strip()
            confirmation = getpass.getpass("Confirm credential: ").strip()
            if credential_value != confirmation:
                raise SystemExit("Tokenが一致しません。")
            write_notion_token(credential_value)
            print("Notion tokenをWindows Credential Managerへ保存しました。")
        elif args.action == "delete":
            removed = delete_notion_token()
            print("Notion tokenを削除しました。" if removed else "保存済みNotion tokenはありません。")
        else:
            print("registered" if read_notion_token() else "missing")
        return 0
    view = get_notion_settings_view(args.root, refresh=True)
    connection = view["connection"]
    print(f"status: {connection['status']}")
    if connection.get("error"):
        print(f"error: {connection['error']}")
    for target in view["targets"]:
        print(f"{target['object_type']}\t{target['id']}\t{target['notion_title']}")
    return 0 if connection["connected"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
