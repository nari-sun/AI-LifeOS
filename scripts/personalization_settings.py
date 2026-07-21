from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SETTINGS_VERSION = 1
SETTINGS_RELATIVE_PATH = Path("memory") / "personalization_settings.json"
MAX_PROJECT_SCOPE_CHARS = 120
MAX_MEMORY_PREVIEW_CHARS = 12_000
MAX_ITEM_PREVIEW_CHARS = 4_000
MAX_STRUCTURED_ITEMS = 100
MEMORY_SECTION_FILES = (
    ("long_term", "長期メモリ", "long_term.md"),
    ("preferences", "好み", "preferences.md"),
    ("projects", "プロジェクト", "projects.md"),
)
_CONTROL_CHARACTER_PATTERN = re.compile(r"[\x00-\x1f\x7f]")
_UNSET = object()
_MEMORY_BEFORE_TEMPORARY_KEY = "memory_enabled_before_temporary"
_PAST_CHAT_BEFORE_TEMPORARY_KEY = "past_chat_search_enabled_before_temporary"


@dataclass(frozen=True)
class PersonalizationSettings:
    memory_enabled: bool = True
    past_chat_search_enabled: bool = True
    project_scope: str | None = None


@dataclass(frozen=True)
class SessionPersonalization:
    temporary: bool
    temporary_locked: bool
    exclude_from_memory: bool
    memory_enabled: bool
    past_chat_search_enabled: bool
    project_scope: str | None
    explicitly_configured: bool


def personalization_settings_path(root: Path | str = ROOT) -> Path:
    return Path(root).resolve() / SETTINGS_RELATIVE_PATH


def load_personalization_settings(root: Path | str = ROOT) -> PersonalizationSettings:
    """Load private settings without creating or modifying any file."""

    path = personalization_settings_path(root)
    if not path.exists():
        return PersonalizationSettings()

    data = _read_json_object(path, label="パーソナライズ設定")
    return PersonalizationSettings(
        memory_enabled=_stored_bool(data, "memory_enabled", True),
        past_chat_search_enabled=_stored_bool(data, "past_chat_search_enabled", True),
        project_scope=validate_project_scope(data.get("project_scope")),
    )


def update_personalization_settings(
    root: Path | str = ROOT,
    *,
    memory_enabled: Any = _UNSET,
    past_chat_search_enabled: Any = _UNSET,
    project_scope: Any = _UNSET,
) -> PersonalizationSettings:
    """Persist only fields explicitly supplied by a user-facing operation."""

    current = load_personalization_settings(root)
    updated = PersonalizationSettings(
        memory_enabled=(
            current.memory_enabled
            if memory_enabled is _UNSET
            else _required_bool(memory_enabled, "memory_enabled")
        ),
        past_chat_search_enabled=(
            current.past_chat_search_enabled
            if past_chat_search_enabled is _UNSET
            else _required_bool(past_chat_search_enabled, "past_chat_search_enabled")
        ),
        project_scope=(
            current.project_scope
            if project_scope is _UNSET
            else validate_project_scope(project_scope)
        ),
    )
    path = personalization_settings_path(root)
    _atomic_write_json(
        path,
        {
            "version": SETTINGS_VERSION,
            **asdict(updated),
        },
    )
    return updated


def load_session_personalization(
    root: Path | str,
    session_file: Path | str,
) -> SessionPersonalization:
    root_path = Path(root).resolve()
    jsonl_file = validate_session_file(root_path, session_file)
    global_settings = load_personalization_settings(root_path)
    record = capture_session_personalization(root_path, jsonl_file)
    configured = record is not None
    raw = record or {}
    temporary = _stored_bool(raw, "temporary", False)
    # Temporary mode defines the retention boundary for the whole session.  Once
    # a message or an organization stage exists, changing that boundary could
    # leave an already-created raw/index record behind, so lock the choice for
    # both temporary and normal sessions.
    temporary_locked = _session_has_started_or_been_organized(root_path, jsonl_file)

    memory_enabled = _stored_optional_bool(raw, "memory_enabled")
    past_chat_search_enabled = _stored_optional_bool(raw, "past_chat_search_enabled")
    has_scope_override = "project_scope" in raw
    scope = validate_project_scope(raw.get("project_scope")) if has_scope_override else global_settings.project_scope

    return SessionPersonalization(
        temporary=temporary,
        temporary_locked=temporary_locked,
        exclude_from_memory=temporary or _stored_bool(raw, "exclude_from_memory", False),
        memory_enabled=False if temporary else (
            global_settings.memory_enabled if memory_enabled is None else memory_enabled
        ),
        past_chat_search_enabled=False if temporary else (
            global_settings.past_chat_search_enabled
            if past_chat_search_enabled is None
            else past_chat_search_enabled
        ),
        project_scope=scope,
        explicitly_configured=configured,
    )


def update_session_personalization(
    root: Path | str,
    session_file: Path | str,
    *,
    temporary: Any = _UNSET,
    memory_enabled: Any = _UNSET,
    past_chat_search_enabled: Any = _UNSET,
    project_scope: Any = _UNSET,
) -> SessionPersonalization:
    root_path = Path(root).resolve()
    jsonl_file = validate_session_file(root_path, session_file)
    global_settings = load_personalization_settings(root_path)
    existing = capture_session_personalization(root_path, jsonl_file) or {}

    existing_temporary = _stored_bool(existing, "temporary", False)

    next_temporary = (
        existing_temporary
        if temporary is _UNSET
        else _required_bool(temporary, "temporary")
    )
    temporary_changed = next_temporary != existing_temporary
    if temporary_changed and _session_has_started_or_been_organized(root_path, jsonl_file):
        raise ValueError("発言保存後や整理開始後は一時チャット設定を変更できません。")
    requested_memory_enabled = (
        _stored_optional_bool(existing, "memory_enabled")
        if memory_enabled is _UNSET
        else _required_bool(memory_enabled, "memory_enabled")
    )
    requested_past_chat_enabled = (
        _stored_optional_bool(existing, "past_chat_search_enabled")
        if past_chat_search_enabled is _UNSET
        else _required_bool(past_chat_search_enabled, "past_chat_search_enabled")
    )
    if requested_memory_enabled is None:
        requested_memory_enabled = global_settings.memory_enabled
    if requested_past_chat_enabled is None:
        requested_past_chat_enabled = global_settings.past_chat_search_enabled

    memory_before_temporary = _stored_optional_bool(existing, _MEMORY_BEFORE_TEMPORARY_KEY)
    past_chat_before_temporary = _stored_optional_bool(existing, _PAST_CHAT_BEFORE_TEMPORARY_KEY)
    if existing_temporary:
        # Temporary mode exposes effective OFF values to the GUI, so a later
        # update sends those forced values back.  Keep the pre-temporary
        # snapshot instead of treating the forced OFF values as a user choice.
        memory_before_temporary = (
            global_settings.memory_enabled
            if memory_before_temporary is None
            else memory_before_temporary
        )
        past_chat_before_temporary = (
            global_settings.past_chat_search_enabled
            if past_chat_before_temporary is None
            else past_chat_before_temporary
        )
    else:
        memory_before_temporary = requested_memory_enabled
        past_chat_before_temporary = requested_past_chat_enabled

    if existing_temporary and not next_temporary:
        next_memory_enabled = memory_before_temporary
        next_past_chat_enabled = past_chat_before_temporary
    else:
        next_memory_enabled = requested_memory_enabled
        next_past_chat_enabled = requested_past_chat_enabled

    if project_scope is _UNSET:
        next_scope = (
            validate_project_scope(existing.get("project_scope"))
            if "project_scope" in existing
            else global_settings.project_scope
        )
    else:
        next_scope = validate_project_scope(project_scope)

    # Exclusion is sticky once the session has started.  Before the first
    # message, an explicit temporary -> normal change is still reversible and
    # cannot expose any already-saved conversation because none exists yet.
    reverting_empty_temporary = existing_temporary and not next_temporary and temporary_changed
    next_exclude_from_memory = (
        False
        if reverting_empty_temporary
        else _stored_bool(existing, "exclude_from_memory", False) or next_temporary
    )
    record = {
        "version": SETTINGS_VERSION,
        "temporary": next_temporary,
        "exclude_from_memory": next_exclude_from_memory,
        "memory_enabled": False if next_temporary else next_memory_enabled,
        "past_chat_search_enabled": False if next_temporary else next_past_chat_enabled,
        "project_scope": next_scope,
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    if next_temporary:
        record[_MEMORY_BEFORE_TEMPORARY_KEY] = memory_before_temporary
        record[_PAST_CHAT_BEFORE_TEMPORARY_KEY] = past_chat_before_temporary
    _merge_session_personalization(root_path, jsonl_file, record)
    return load_session_personalization(root_path, jsonl_file)


def _session_has_started_or_been_organized(root: Path, session_file: Path) -> bool:
    try:
        if session_file.exists() and session_file.stat().st_size > 0:
            return True
    except OSError:
        # If the state cannot be inspected, fail closed and keep the boundary
        # immutable rather than risk reclassifying a session.
        return True

    metadata_path = session_file.with_suffix(".session.json")
    if not metadata_path.exists():
        return False
    metadata = _read_json_object(metadata_path, label="セッション情報")
    status = str(metadata.get("status") or "").strip().lower()
    if status and status not in {"new", "active", "saved", "temporary"}:
        return True
    organization = metadata.get("organization")
    if not isinstance(organization, dict):
        return organization is not None
    return any(
        bool(organization.get(key))
        for key in (
            "raw_created",
            "memory_processed",
            "index_updated",
            "raw_file",
            "task_file",
            "raw_message_count",
            "processed_message_count",
        )
    )


def capture_session_personalization(
    root: Path | str,
    session_file: Path | str,
) -> dict[str, Any] | None:
    """Return the raw metadata record so callers can preserve it across rewrites."""

    root_path = Path(root).resolve()
    jsonl_file = validate_session_file(root_path, session_file)
    metadata_path = jsonl_file.with_suffix(".session.json")
    if not metadata_path.exists():
        return None
    metadata = _read_json_object(metadata_path, label="セッション情報")
    record = metadata.get("personalization")
    if record is None:
        return None
    if not isinstance(record, dict):
        raise ValueError("セッションの personalization 情報が不正です。")
    return dict(record)


def restore_session_personalization(
    root: Path | str,
    session_file: Path | str,
    record: dict[str, Any] | None,
) -> None:
    """Restore an unchanged record after another workflow rewrites session metadata."""

    if record is None:
        return
    root_path = Path(root).resolve()
    jsonl_file = validate_session_file(root_path, session_file)
    _merge_session_personalization(root_path, jsonl_file, dict(record))


def validate_session_file(root: Path | str, session_file: Path | str) -> Path:
    root_path = Path(root).resolve()
    live_dir = (root_path / "inbox" / "live").resolve()
    candidate = Path(session_file)
    if not candidate.is_absolute():
        candidate = root_path / candidate
    candidate = candidate.resolve(strict=False)
    try:
        candidate.relative_to(live_dir)
    except ValueError as exc:
        raise ValueError("session_file は inbox/live 内を指定してください。") from exc
    if candidate.parent != live_dir or candidate.suffix.lower() != ".jsonl":
        raise ValueError("session_file は inbox/live 直下の .jsonl を指定してください。")
    return candidate


def validate_project_scope(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("project_scope は文字列で指定してください。")
    normalized = " ".join(value.strip().split())
    if not normalized:
        return None
    if normalized.casefold() == "all":
        raise ValueError("project_scope に予約語 all は使用できません。全体スコープは空欄で指定してください。")
    if len(normalized) > MAX_PROJECT_SCOPE_CHARS:
        raise ValueError(f"project_scope は{MAX_PROJECT_SCOPE_CHARS}文字以内で指定してください。")
    if _CONTROL_CHARACTER_PATTERN.search(value):
        raise ValueError("project_scope に改行や制御文字は使用できません。")
    return normalized


def build_memory_summary(root: Path | str = ROOT) -> dict[str, Any]:
    """Build a bounded, read-only preview from fixed files below memory/."""

    root_path = Path(root).resolve()
    memory_dir = root_path / "memory"
    sections = [
        _preview_memory_file(
            root_path,
            key=key,
            label=label,
            path=memory_dir / file_name,
            max_chars=MAX_MEMORY_PREVIEW_CHARS,
        )
        for key, label, file_name in MEMORY_SECTION_FILES
    ]

    items_dir = memory_dir / "items"
    item_paths: list[Path] = []
    if items_dir.is_dir():
        for candidate in items_dir.glob("*.md"):
            try:
                resolved = candidate.resolve(strict=True)
                resolved.relative_to(items_dir.resolve())
            except (OSError, ValueError):
                continue
            if resolved.is_file():
                item_paths.append(resolved)
    item_paths.sort(key=lambda path: path.name.casefold())
    visible_paths = item_paths[:MAX_STRUCTURED_ITEMS]
    items = [
        _preview_memory_file(
            root_path,
            key=path.stem,
            label=_structured_item_title(path),
            path=path,
            max_chars=MAX_ITEM_PREVIEW_CHARS,
        )
        for path in visible_paths
    ]

    return {
        "read_only": True,
        "sections": sections,
        "structured_items": items,
        "structured_item_count": len(item_paths),
        "structured_items_truncated": len(item_paths) > len(visible_paths),
    }


def _preview_memory_file(
    root: Path,
    *,
    key: str,
    label: str,
    path: Path,
    max_chars: int,
) -> dict[str, Any]:
    expected_parent = (root / "memory").resolve()
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(expected_parent)
    except ValueError as exc:  # Defensive guard for future changes to the fixed file list.
        raise ValueError("memory 配下以外はプレビューできません。") from exc

    if not resolved.is_file():
        return {
            "key": key,
            "label": label,
            "path": _relative_display_path(resolved, root),
            "exists": False,
            "content": "",
            "character_count": 0,
            "truncated": False,
            "modified_at": None,
        }

    content = resolved.read_text(encoding="utf-8")
    stat = resolved.stat()
    return {
        "key": key,
        "label": label,
        "path": _relative_display_path(resolved, root),
        "exists": True,
        "content": content[:max_chars],
        "character_count": len(content),
        "truncated": len(content) > max_chars,
        "modified_at": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds"),
    }


def _structured_item_title(path: Path) -> str:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[:80]
    except OSError:
        return path.stem
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("# ") and stripped[2:].strip():
            return stripped[2:].strip()
        match = re.match(r"title\s*:\s*(.+)", stripped, flags=re.IGNORECASE)
        if match:
            title = match.group(1).strip().strip("\"'")
            if title:
                return title
    return path.stem


def _merge_session_personalization(root: Path, session_file: Path, record: dict[str, Any]) -> None:
    metadata_path = session_file.with_suffix(".session.json")
    metadata = _read_json_object(metadata_path, label="セッション情報") if metadata_path.exists() else {}
    metadata["personalization"] = record
    _atomic_write_json(metadata_path, metadata)


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    temporary_path = Path(handle.name)
    try:
        with handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def _read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label}のJSONが壊れています: {path.name}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{label}はJSON objectである必要があります。")
    return data


def _required_bool(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} は true または false で指定してください。")
    return value


def _stored_bool(data: dict[str, Any], name: str, default: bool) -> bool:
    value = data.get(name, default)
    return _required_bool(value, name)


def _stored_optional_bool(data: dict[str, Any], name: str) -> bool | None:
    if name not in data or data[name] is None:
        return None
    return _required_bool(data[name], name)


def _relative_display_path(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return path.name


def serialize_personalization(settings: PersonalizationSettings | SessionPersonalization) -> dict[str, Any]:
    return asdict(settings)


if __name__ == "__main__":
    print(json.dumps(serialize_personalization(load_personalization_settings()), ensure_ascii=False, indent=2))
