from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
VALID_ROLES = {"user", "assistant"}
EXTRACTABLE_CONTENT_TYPES = {"", "text", "multimodal_text", "audio_transcription"}
HIDDEN_CONTENT_TYPES = {"thoughts", "reasoning_recap", "user_editable_context"}
METADATA_FILE = "import_metadata.json"
CONVERSATION_FILE_PATTERN = re.compile(r"^conversations(?:-(\d+))?\.json$", re.IGNORECASE)
IMPORT_METADATA_SCHEMA_VERSION = 2
CONTENT_FINGERPRINT_VERSION = 1
IMPORT_STATE_NEW = "new"
IMPORT_STATE_DUPLICATE = "duplicate"
IMPORT_STATE_UPDATED = "updated"
IMPORT_STATE_CONFLICT = "conflict"


@dataclass(frozen=True)
class ImportedMessage:
    role: str
    content: str
    created_at: datetime | None


@dataclass(frozen=True)
class ExportConversation:
    source_id: str
    title: str
    created_at: datetime | None
    updated_at: datetime | None
    messages: tuple[ImportedMessage, ...]
    source_message_count: int = 0
    skipped_message_count: int = 0
    non_text_message_count: int = 0
    attachment_count: int = 0
    non_text_part_count: int = 0
    audio_transcription_count: int = 0

    @property
    def empty_conversation(self) -> bool:
        return not self.messages


@dataclass(frozen=True)
class ExportSource:
    display_name: str
    conversations: tuple[ExportConversation, ...]


@dataclass(frozen=True)
class ImportResult:
    conversation: ExportConversation
    raw_file: Path | None
    duplicate: bool
    updated: bool = False


@dataclass(frozen=True)
class MessageContentExtraction:
    text: str
    source_part_count: int = 0
    non_text_part_count: int = 0
    attachment_count: int = 0
    audio_transcription_count: int = 0


@dataclass(frozen=True)
class MessageExtraction:
    messages: tuple[ImportedMessage, ...]
    source_message_count: int
    skipped_message_count: int
    non_text_message_count: int
    attachment_count: int
    non_text_part_count: int
    audio_transcription_count: int


@dataclass(frozen=True)
class ImportedRevision:
    metadata_file: Path
    raw_file: Path
    metadata: dict[str, Any]


def load_export(source: Path | str) -> ExportSource:
    source_path = Path(source)
    if not source_path.exists():
        raise FileNotFoundError(f"エクスポート元が見つかりません: {source_path}")

    if source_path.is_dir():
        matches = _find_conversation_files(source_path)
        if not matches:
            raise FileNotFoundError("フォルダ内に conversations.json または conversations-*.json が見つかりません。")
        file_names = [path.relative_to(source_path).as_posix() for path in matches]
        payload = _merge_conversation_payloads(
            (file_name, _load_json_bytes(path.read_bytes()))
            for file_name, path in zip(file_names, matches)
        )
        display_name = _source_display_name(source_path.name, file_names)
    elif source_path.suffix.lower() == ".zip":
        payload, member_names = _load_from_zip(source_path)
        display_name = _source_display_name(source_path.name, member_names)
    else:
        if not CONVERSATION_FILE_PATTERN.fullmatch(source_path.name):
            raise ValueError(
                "JSONファイルを直接指定する場合は conversations.json または conversations-*.json を指定してください。"
            )
        payload = _load_json_bytes(source_path.read_bytes())
        display_name = source_path.name

    if not isinstance(payload, list):
        raise ValueError("conversations.json のルートは配列である必要があります。")

    conversations = tuple(_parse_conversation(item, index) for index, item in enumerate(payload))
    return ExportSource(display_name=display_name, conversations=conversations)


def _find_conversation_files(source_path: Path) -> list[Path]:
    matches = [
        path
        for path in source_path.rglob("*")
        if path.is_file() and CONVERSATION_FILE_PATTERN.fullmatch(path.name)
    ]
    _require_single_conversation_location(
        path.relative_to(source_path).as_posix() for path in matches
    )
    return sorted(matches, key=lambda path: _conversation_file_sort_key(path.name))


def _load_from_zip(source_path: Path) -> tuple[list[Any], list[str]]:
    try:
        with zipfile.ZipFile(source_path) as archive:
            matches = [
                info
                for info in archive.infolist()
                if not info.is_dir()
                and CONVERSATION_FILE_PATTERN.fullmatch(
                    Path(info.filename.replace("\\", "/")).name
                )
            ]
            if not matches:
                raise FileNotFoundError("zip内に conversations.json または conversations-*.json が見つかりません。")
            member_names = [info.filename.replace("\\", "/") for info in matches]
            _require_single_conversation_location(member_names)
            ordered = sorted(matches, key=lambda info: _conversation_file_sort_key(info.filename))
            ordered_names = [info.filename.replace("\\", "/") for info in ordered]
            return (
                _merge_conversation_payloads(
                    (name, _load_json_bytes(archive.read(info)))
                    for name, info in zip(ordered_names, ordered)
                ),
                ordered_names,
            )
    except zipfile.BadZipFile as exc:
        raise ValueError("有効なzipファイルではありません。") from exc


def _require_single_conversation_location(file_names: Iterable[str]) -> None:
    locations = {Path(file_name).parent.as_posix() for file_name in file_names}
    if len(locations) > 1:
        raise ValueError(
            "会話データが複数のフォルダにあります。対象のエクスポートフォルダを直接指定してください。"
        )


def _conversation_file_sort_key(file_name: str) -> tuple[int, int, str]:
    match = CONVERSATION_FILE_PATTERN.fullmatch(Path(file_name).name)
    if match is None:
        raise ValueError(f"対応していない会話データファイルです: {file_name}")
    sequence = match.group(1)
    if sequence is None:
        return (0, 0, file_name.casefold())
    return (1, int(sequence), file_name.casefold())


def _source_display_name(source_name: str, file_names: list[str]) -> str:
    if len(file_names) == 1:
        return f"{source_name}/{file_names[0]}"
    return f"{source_name}/" + ", ".join(file_names)


def _merge_conversation_payloads(payloads: Iterable[tuple[str, Any]]) -> list[Any]:
    conversations: list[Any] = []
    for file_name, payload in payloads:
        if not isinstance(payload, list):
            raise ValueError(f"{file_name} のルートは配列である必要があります。")
        conversations.extend(payload)
    return conversations


def _load_json_bytes(data: bytes) -> Any:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("conversations.json はUTF-8として読めません。") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"conversations.json のJSONが不正です: {exc}") from exc


def _parse_conversation(value: Any, index: int) -> ExportConversation:
    if not isinstance(value, dict):
        raise ValueError(f"会話 {index + 1} がオブジェクトではありません。")

    title = str(value.get("title") or "Untitled").strip() or "Untitled"
    created_at = _timestamp(value.get("create_time"))
    updated_at = _timestamp(value.get("update_time"))
    extraction = _extract_messages_with_stats(value)
    messages = extraction.messages
    raw_id = value.get("id") or value.get("conversation_id")
    candidate_id = str(raw_id).strip() if raw_id else ""
    source_id = candidate_id or _derived_source_id(title, created_at, messages)
    return ExportConversation(
        source_id=source_id,
        title=title,
        created_at=created_at,
        updated_at=updated_at,
        messages=messages,
        source_message_count=extraction.source_message_count,
        skipped_message_count=extraction.skipped_message_count,
        non_text_message_count=extraction.non_text_message_count,
        attachment_count=extraction.attachment_count,
        non_text_part_count=extraction.non_text_part_count,
        audio_transcription_count=extraction.audio_transcription_count,
    )


def _extract_messages(conversation: dict[str, Any]) -> Iterable[ImportedMessage]:
    return _extract_messages_with_stats(conversation).messages


def _extract_messages_with_stats(conversation: dict[str, Any]) -> MessageExtraction:
    mapping = conversation.get("mapping")
    if not isinstance(mapping, dict):
        return MessageExtraction((), 0, 0, 0, 0, 0, 0)

    nodes = _active_branch_nodes(mapping, conversation.get("current_node"))
    result: list[ImportedMessage] = []
    source_message_count = 0
    non_text_message_count = 0
    attachment_count = 0
    non_text_part_count = 0
    audio_transcription_count = 0
    for node in nodes:
        message = node.get("message")
        if not isinstance(message, dict):
            continue
        source_message_count += 1
        content = _extract_message_content(message.get("content"))
        attachment_count += content.attachment_count
        non_text_part_count += content.non_text_part_count
        audio_transcription_count += content.audio_transcription_count
        author = message.get("author")
        role = author.get("role") if isinstance(author, dict) else None
        if role not in VALID_ROLES:
            continue
        if not content.text.strip():
            non_text_message_count += 1
            continue
        result.append(
            ImportedMessage(
                role=role,
                content=content.text.strip(),
                created_at=_timestamp(message.get("create_time")),
            )
        )
    return MessageExtraction(
        messages=tuple(result),
        source_message_count=source_message_count,
        skipped_message_count=source_message_count - len(result),
        non_text_message_count=non_text_message_count,
        attachment_count=attachment_count,
        non_text_part_count=non_text_part_count,
        audio_transcription_count=audio_transcription_count,
    )


def _active_branch_nodes(mapping: dict[str, Any], current_node: Any) -> list[dict[str, Any]]:
    if (
        isinstance(current_node, str)
        and current_node in mapping
        and isinstance(mapping[current_node], dict)
    ):
        return _branch_nodes_from_leaf(mapping, current_node)

    referenced_parents = {
        parent
        for node in mapping.values()
        if isinstance(node, dict)
        for parent in [node.get("parent")]
        if isinstance(parent, str)
    }
    leaf_candidates = [
        (position, node_id, node)
        for position, (node_id, node) in enumerate(mapping.items())
        if isinstance(node_id, str)
        and isinstance(node, dict)
        and node_id not in referenced_parents
    ]
    if leaf_candidates:
        _, leaf_id, _ = max(
            leaf_candidates,
            key=lambda candidate: (
                *_latest_message_sort_key(candidate[2]),
                candidate[0],
            ),
        )
        return _branch_nodes_from_leaf(mapping, leaf_id)

    indexed_nodes = [(position, node) for position, node in enumerate(mapping.values()) if isinstance(node, dict)]
    return [
        node
        for _, node in sorted(
            indexed_nodes,
            key=lambda pair: (_message_sort_time(pair[1]), pair[0]),
        )
    ]


def _branch_nodes_from_leaf(mapping: dict[str, Any], leaf_id: str) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    seen: set[str] = set()
    node_id: Any = leaf_id
    while isinstance(node_id, str) and node_id in mapping and node_id not in seen:
        seen.add(node_id)
        node = mapping[node_id]
        if not isinstance(node, dict):
            break
        nodes.append(node)
        node_id = node.get("parent")
    nodes.reverse()
    return nodes


def _latest_message_sort_key(node: dict[str, Any]) -> tuple[bool, float]:
    message = node.get("message")
    value = message.get("create_time") if isinstance(message, dict) else None
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return (False, 0.0)
    if timestamp != timestamp:  # NaN must not outrank a real message timestamp.
        return (False, 0.0)
    return (True, timestamp)


def _message_sort_time(node: dict[str, Any]) -> float:
    message = node.get("message")
    value = message.get("create_time") if isinstance(message, dict) else None
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("inf")


def _message_text(content: Any) -> str:
    return _extract_message_content(content).text


def _extract_message_content(content: Any) -> MessageContentExtraction:
    if not isinstance(content, dict):
        return MessageContentExtraction("")

    content_type = str(content.get("content_type") or "").strip().casefold()
    parts = content.get("parts")
    if (
        content_type in HIDDEN_CONTENT_TYPES
        or content_type not in EXTRACTABLE_CONTENT_TYPES
    ):
        part_count = len(parts) if isinstance(parts, list) else (1 if content else 0)
        attachment_count = 0
        if isinstance(parts, list):
            attachment_count = sum(
                1
                for part in parts
                if isinstance(part, dict)
                and _is_attachment_content_type(
                    str(part.get("content_type") or "").strip().casefold()
                )
            )
        elif _is_attachment_content_type(content_type):
            attachment_count = 1
        return MessageContentExtraction(
            text="",
            source_part_count=part_count,
            non_text_part_count=part_count,
            attachment_count=attachment_count,
        )

    if isinstance(parts, list):
        texts: list[str] = []
        non_text_part_count = 0
        attachment_count = 0
        audio_transcription_count = 0
        for part in parts:
            if isinstance(part, str):
                texts.append(part)
                continue
            if not isinstance(part, dict):
                non_text_part_count += 1
                continue

            content_type = str(part.get("content_type") or "").strip().casefold()
            if content_type == "audio_transcription":
                transcript = part.get("text")
                if isinstance(transcript, str) and transcript.strip():
                    texts.append(transcript)
                    audio_transcription_count += 1
                    continue
            non_text_part_count += 1
            if _is_attachment_content_type(content_type):
                attachment_count += 1
        return MessageContentExtraction(
            text="\n\n".join(texts),
            source_part_count=len(parts),
            non_text_part_count=non_text_part_count,
            attachment_count=attachment_count,
            audio_transcription_count=audio_transcription_count,
        )

    text = content.get("text")
    if isinstance(text, str):
        return MessageContentExtraction(
            text=text,
            source_part_count=1,
            audio_transcription_count=(
                1 if content_type == "audio_transcription" and text.strip() else 0
            ),
        )
    is_attachment = _is_attachment_content_type(content_type)
    return MessageContentExtraction(
        text="",
        source_part_count=1 if content else 0,
        non_text_part_count=1 if content else 0,
        attachment_count=1 if is_attachment else 0,
    )


def _is_attachment_content_type(content_type: str) -> bool:
    return bool(content_type and content_type.endswith("asset_pointer"))


def _timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def _parse_iso_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _derived_source_id(
    title: str,
    created_at: datetime | None,
    messages: tuple[ImportedMessage, ...],
) -> str:
    stable_value = {
        "title": title,
        "created_at": _iso(created_at),
        "messages": [
            {"role": item.role, "content": item.content, "created_at": _iso(item.created_at)}
            for item in messages
        ],
    }
    digest = hashlib.sha256(
        json.dumps(stable_value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return f"sha256:{digest}"


def select_conversations(
    conversations: Iterable[ExportConversation],
    from_date: date | None = None,
    to_date: date | None = None,
    title_query: str | None = None,
    source_ids: Iterable[str] = (),
) -> list[ExportConversation]:
    wanted_ids = set(source_ids)
    query = title_query.casefold() if title_query else None
    selected = []
    for conversation in conversations:
        created_date = conversation.created_at.date() if conversation.created_at else None
        if from_date and (created_date is None or created_date < from_date):
            continue
        if to_date and (created_date is None or created_date > to_date):
            continue
        if query and query not in conversation.title.casefold():
            continue
        if wanted_ids and conversation.source_id not in wanted_ids:
            continue
        selected.append(conversation)
    return selected


def find_imported_source_ids(conversations_dir: Path | str) -> set[str]:
    return set(_load_imported_revisions(conversations_dir))


def classify_import_states(
    conversations: Iterable[ExportConversation],
    conversations_dir: Path | str,
) -> dict[str, str]:
    """Classify incoming revisions without writing any personal data.

    ``updated`` means exactly one prior import exists and its export timestamp
    or extracted-content fingerprint changed. ``conflict`` is deliberately
    fail-closed: either multiple existing imports use the same source ID, or a
    single export payload contains different revisions under the same ID.
    """

    incoming = tuple(conversations)
    revisions = _load_imported_revisions(conversations_dir)
    incoming_keys: dict[str, set[tuple[str | None, str]]] = {}
    for conversation in incoming:
        incoming_keys.setdefault(conversation.source_id, set()).add(
            (_iso(conversation.updated_at), _conversation_fingerprint(conversation))
        )

    states: dict[str, str] = {}
    for conversation in incoming:
        source_id = conversation.source_id
        if len(incoming_keys[source_id]) > 1:
            states[source_id] = IMPORT_STATE_CONFLICT
            continue
        states[source_id] = _classify_import_state(
            conversation,
            revisions.get(source_id, ()),
        )
    return states


def _load_imported_revisions(
    conversations_dir: Path | str,
) -> dict[str, list[ImportedRevision]]:
    root = Path(conversations_dir)
    revisions: dict[str, list[ImportedRevision]] = {}
    if not root.exists():
        return revisions
    for path in root.rglob(METADATA_FILE):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if isinstance(value, dict) and value.get("source") == "chatgpt_export":
            source_id = value.get("source_conversation_id")
            if isinstance(source_id, str) and source_id.strip():
                normalized_id = source_id.strip()
                revisions.setdefault(normalized_id, []).append(
                    ImportedRevision(
                        metadata_file=path,
                        raw_file=path.parent / "raw.md",
                        metadata=value,
                    )
                )
    for values in revisions.values():
        values.sort(key=lambda item: item.metadata_file.as_posix())
    return revisions


def _classify_import_state(
    conversation: ExportConversation,
    revisions: Iterable[ImportedRevision],
) -> str:
    existing = tuple(revisions)
    if not existing:
        return IMPORT_STATE_NEW
    if len(existing) != 1:
        return IMPORT_STATE_CONFLICT
    revision = existing[0]
    if not revision.raw_file.is_file():
        return IMPORT_STATE_CONFLICT

    stored_updated_at = revision.metadata.get("source_updated_at")
    incoming_updated_at = _iso(conversation.updated_at)
    incoming_fingerprint = _conversation_fingerprint(conversation)
    stored_updated_datetime = _parse_iso_timestamp(stored_updated_at)
    if stored_updated_datetime is not None and (
        conversation.updated_at is None
        or conversation.updated_at < stored_updated_datetime
    ):
        stored_fingerprint = revision.metadata.get("content_fingerprint")
        # The same content from an older export is harmlessly redundant. A
        # different or unverifiable older revision must never roll local data
        # back automatically.
        return (
            IMPORT_STATE_DUPLICATE
            if isinstance(stored_fingerprint, str)
            and stored_fingerprint == incoming_fingerprint
            else IMPORT_STATE_CONFLICT
        )
    stored_fingerprint = revision.metadata.get("content_fingerprint")
    if isinstance(stored_fingerprint, str) and stored_fingerprint:
        unchanged = (
            stored_updated_at == incoming_updated_at
            and stored_fingerprint == incoming_fingerprint
        )
    else:
        # Version-1 metadata has no canonical fingerprint. Preserve the legacy
        # duplicate behavior unless ChatGPT reports a changed revision time or
        # the newer parser recovered messages/statistics the old parser could
        # not represent. In particular, audio_transcription dict parts were
        # previously dropped even though they contain first-party text.
        stored_message_count = _nonnegative_int(revision.metadata.get("message_count"))
        recovered_extraction = (
            stored_message_count != len(conversation.messages)
            or (
                conversation.audio_transcription_count > 0
                and "audio_transcription_count" not in revision.metadata
            )
            or (
                conversation.attachment_count > 0
                and "attachment_count" not in revision.metadata
            )
            or (
                conversation.skipped_message_count > 0
                and "skipped_message_count" not in revision.metadata
            )
        )
        unchanged = stored_updated_at == incoming_updated_at and not recovered_extraction
    return IMPORT_STATE_DUPLICATE if unchanged else IMPORT_STATE_UPDATED


def _conversation_fingerprint(conversation: ExportConversation) -> str:
    value = {
        "version": CONTENT_FINGERPRINT_VERSION,
        "title": conversation.title,
        "created_at": _iso(conversation.created_at),
        "messages": [
            {
                "role": message.role,
                "content": message.content,
                "created_at": _iso(message.created_at),
            }
            for message in conversation.messages
        ],
        "extraction": {
            "source_message_count": conversation.source_message_count,
            "skipped_message_count": conversation.skipped_message_count,
            "non_text_message_count": conversation.non_text_message_count,
            "attachment_count": conversation.attachment_count,
            "non_text_part_count": conversation.non_text_part_count,
            "audio_transcription_count": conversation.audio_transcription_count,
        },
    }
    digest = hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    return f"sha256:{digest}"


def import_conversations(
    conversations: Iterable[ExportConversation],
    conversations_dir: Path | str,
    source_display_name: str,
    imported_at: datetime | None = None,
) -> list[ImportResult]:
    incoming = tuple(conversations)
    output_root = Path(conversations_dir)
    revisions = _load_imported_revisions(output_root)
    states = classify_import_states(incoming, output_root)
    conflicts = sorted(
        source_id for source_id, state in states.items() if state == IMPORT_STATE_CONFLICT
    )
    if conflicts:
        raise ValueError(
            "安全に自動更新できないimport conflictがあります。"
            "既存raw/metadata、重複ID、またはrevision順を確認してください: "
            + ", ".join(conflicts)
        )

    now = imported_at or datetime.now(timezone.utc)
    results: list[ImportResult] = []

    for conversation in incoming:
        state = _classify_import_state(
            conversation,
            revisions.get(conversation.source_id, ()),
        )
        if state == IMPORT_STATE_DUPLICATE:
            results.append(
                ImportResult(
                    conversation=conversation,
                    raw_file=None,
                    duplicate=True,
                    updated=False,
                )
            )
            continue

        is_update = state == IMPORT_STATE_UPDATED
        previous_revision = (
            revisions[conversation.source_id][0]
            if is_update
            else None
        )
        raw_file = (
            previous_revision.raw_file
            if previous_revision is not None
            else _next_raw_path(output_root, conversation.created_at or now)
        )
        metadata_file = raw_file.parent / METADATA_FILE
        raw_text = _format_raw(conversation, now)
        metadata = _build_import_metadata(
            conversation=conversation,
            source_display_name=source_display_name,
            imported_at=now,
            previous=(previous_revision.metadata if previous_revision else None),
        )

        if previous_revision is not None:
            _backup_import_revision(previous_revision)
        else:
            raw_file.parent.mkdir(parents=True, exist_ok=False)
        try:
            _atomic_write_import_files(
                raw_file=raw_file,
                raw_text=raw_text,
                metadata_file=metadata_file,
                metadata=metadata,
            )
        except Exception:
            if previous_revision is None:
                raw_file.unlink(missing_ok=True)
                metadata_file.unlink(missing_ok=True)
                try:
                    raw_file.parent.rmdir()
                except OSError:
                    pass
            raise

        stored_revision = ImportedRevision(
            metadata_file=metadata_file,
            raw_file=raw_file,
            metadata=metadata,
        )
        revisions[conversation.source_id] = [stored_revision]
        results.append(
            ImportResult(
                conversation=conversation,
                raw_file=raw_file,
                duplicate=False,
                updated=is_update,
            )
        )
    return results


def _build_import_metadata(
    conversation: ExportConversation,
    source_display_name: str,
    imported_at: datetime,
    previous: dict[str, Any] | None,
) -> dict[str, Any]:
    metadata = dict(previous or {})
    previous_revision = (
        (_positive_int(metadata.get("revision")) or 1)
        if previous is not None
        else 0
    )
    first_imported_at = metadata.get("imported_at") if previous else None
    metadata.update(
        {
            "schema_version": IMPORT_METADATA_SCHEMA_VERSION,
            "source": "chatgpt_export",
            "source_file": source_display_name,
            "source_conversation_id": conversation.source_id,
            "title": conversation.title,
            "source_created_at": _iso(conversation.created_at),
            "source_updated_at": _iso(conversation.updated_at),
            "message_count": len(conversation.messages),
            "source_message_count": conversation.source_message_count,
            "skipped_message_count": conversation.skipped_message_count,
            "non_text_message_count": conversation.non_text_message_count,
            "attachment_count": conversation.attachment_count,
            "non_text_part_count": conversation.non_text_part_count,
            "audio_transcription_count": conversation.audio_transcription_count,
            "empty_conversation": conversation.empty_conversation,
            "content_fingerprint_version": CONTENT_FINGERPRINT_VERSION,
            "content_fingerprint": _conversation_fingerprint(conversation),
            "revision": previous_revision + 1,
            "imported_at": (
                first_imported_at
                if isinstance(first_imported_at, str) and first_imported_at
                else _iso(imported_at)
            ),
            "last_imported_at": _iso(imported_at),
            "memory_processing": "not_requested",
        }
    )
    return metadata


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _backup_import_revision(revision: ImportedRevision) -> None:
    """Keep the prior revision recoverable without making it searchable.

    Backup filenames intentionally do not use the exact names ``raw.md`` or
    ``summary.md`` because the memory index recursively discovers those names.
    """

    try:
        raw_bytes = revision.raw_file.read_bytes()
        metadata_bytes = revision.metadata_file.read_bytes()
    except OSError as exc:
        raise ValueError("既存importのrevision backupを作成できません。") from exc

    revision_number = _positive_int(revision.metadata.get("revision")) or 1
    backup_dir = revision.metadata_file.parent / "import_revisions"
    backup_raw = backup_dir / f"revision-{revision_number:04d}.raw.md"
    backup_metadata = backup_dir / f"revision-{revision_number:04d}.metadata.json"
    backup_dir.mkdir(parents=True, exist_ok=True)

    if backup_raw.is_file() and not backup_metadata.exists():
        try:
            if backup_raw.read_bytes() != raw_bytes:
                raise ValueError(
                    "作成途中のrevision backupが既存rawと一致しません。内容を確認してください。"
                )
        except OSError as exc:
            raise ValueError("作成途中のrevision backupを確認できません。") from exc
        _atomic_write_bytes(backup_metadata, metadata_bytes)
        return

    if backup_raw.exists() or backup_metadata.exists():
        try:
            same_committed_revision = (
                backup_raw.is_file()
                and backup_metadata.is_file()
                and backup_metadata.read_bytes() == metadata_bytes
            )
        except OSError as exc:
            raise ValueError("既存のrevision backupを確認できません。") from exc
        if same_committed_revision:
            # A previous attempt may have replaced raw.md and stopped before
            # replacing metadata. The unchanged metadata is the commit marker,
            # and its existing backup is sufficient to recover the old pair;
            # allow the pending revision to be retried.
            return
        raise ValueError(
            "同じrevision番号のbackupが既に存在します。自動上書きせず内容を確認してください。"
        )

    try:
        _atomic_write_bytes(backup_raw, raw_bytes)
        _atomic_write_bytes(backup_metadata, metadata_bytes)
    except Exception:
        backup_raw.unlink(missing_ok=True)
        backup_metadata.unlink(missing_ok=True)
        try:
            backup_dir.rmdir()
        except OSError:
            pass
        raise


def _atomic_write_import_files(
    raw_file: Path,
    raw_text: str,
    metadata_file: Path,
    metadata: dict[str, Any],
) -> None:
    raw_staged = _stage_bytes(raw_file, raw_text.encode("utf-8"))
    metadata_staged: Path | None = None
    try:
        metadata_staged = _stage_bytes(
            metadata_file,
            (json.dumps(metadata, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        )
        # Metadata is the commit marker and is replaced last. If a process dies
        # between these atomic file replacements, the old fingerprint remains,
        # so the next import retries instead of incorrectly reporting duplicate.
        os.replace(raw_staged, raw_file)
        raw_staged = None
        os.replace(metadata_staged, metadata_file)
        metadata_staged = None
    finally:
        if raw_staged is not None:
            raw_staged.unlink(missing_ok=True)
        if metadata_staged is not None:
            metadata_staged.unlink(missing_ok=True)


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    staged = _stage_bytes(path, data)
    try:
        os.replace(staged, path)
        staged = None
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)


def _stage_bytes(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="wb",
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        delete=False,
    )
    staged = Path(handle.name)
    try:
        with handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        staged.unlink(missing_ok=True)
        raise
    return staged


def _next_raw_path(output_root: Path, created_at: datetime) -> Path:
    candidate_time = created_at
    for offset in range(86400):
        value = candidate_time.timestamp() + offset
        current = datetime.fromtimestamp(value, tz=timezone.utc)
        session_dir = (
            output_root
            / current.strftime("%Y")
            / current.strftime("%m")
            / current.strftime("%Y-%m-%d_%H%M%S")
        )
        if not session_dir.exists():
            return session_dir / "raw.md"
    raise FileExistsError("保存先の空きセッション時刻を確保できませんでした。")


def _format_raw(conversation: ExportConversation, imported_at: datetime) -> str:
    session_time = conversation.created_at or imported_at
    lines = [
        "# Chat Log",
        "",
        f"Date: {session_time.strftime('%Y-%m-%d')}",
        f"Time: {session_time.strftime('%H:%M:%S')} UTC",
        "Source: ChatGPT export",
        f"Title: {_inline(conversation.title)}",
        f"Source Conversation ID: {_inline(conversation.source_id)}",
        f"Created At: {_iso(conversation.created_at) or 'unknown'}",
        f"Updated At: {_iso(conversation.updated_at) or 'unknown'}",
        "",
        "---",
        "",
    ]
    for message in conversation.messages:
        heading = "User" if message.role == "user" else "Assistant"
        lines.extend([f"## {heading}", ""])
        if message.created_at:
            lines.extend([f"Timestamp: {_iso(message.created_at)}", ""])
        lines.extend([message.content.rstrip(), ""])
    return "\n".join(lines).rstrip() + "\n"


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _inline(value: str) -> str:
    return " ".join(value.splitlines()).strip()


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("YYYY-MM-DD形式で指定してください。") from exc


def _print_preview(
    source: ExportSource,
    selected: list[ExportConversation],
    import_states: dict[str, str],
) -> None:
    dates = [item.created_at.date() for item in selected if item.created_at]
    period = f"{min(dates)} .. {max(dates)}" if dates else "unknown"
    state_counts = {
        state: sum(import_states.get(item.source_id) == state for item in selected)
        for state in (
            IMPORT_STATE_NEW,
            IMPORT_STATE_UPDATED,
            IMPORT_STATE_DUPLICATE,
            IMPORT_STATE_CONFLICT,
        )
    }
    print(f"Source: {source.display_name}")
    print(f"Export conversations: {len(source.conversations)}")
    print(
        f"Selected: {len(selected)} "
        f"(new: {state_counts[IMPORT_STATE_NEW]}, "
        f"updated: {state_counts[IMPORT_STATE_UPDATED]}, "
        f"duplicate: {state_counts[IMPORT_STATE_DUPLICATE]}, "
        f"conflict: {state_counts[IMPORT_STATE_CONFLICT]})"
    )
    print(f"Period (UTC): {period}")
    for item in selected:
        created = item.created_at.date().isoformat() if item.created_at else "unknown"
        state = import_states.get(item.source_id, IMPORT_STATE_NEW)
        extraction = (
            f"{len(item.messages)}/{item.source_message_count} messages"
            f"; skipped={item.skipped_message_count}"
            f"; non_text_messages={item.non_text_message_count}"
            f"; attachments={item.attachment_count}"
            f"; non_text_parts={item.non_text_part_count}"
            f"; audio_transcriptions={item.audio_transcription_count}"
        )
        if item.empty_conversation:
            extraction += "; empty=true"
        print(f"- [{item.source_id}] {created} | {item.title} | {extraction} | {state}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="ChatGPT exportの会話データを確認し、AI-LifeOS raw.mdへ安全に取り込みます。"
    )
    parser.add_argument(
        "source",
        type=Path,
        help="exportフォルダ、zip、conversations.json、またはconversations-*.json",
    )
    parser.add_argument("--from-date", type=_parse_date, help="作成日がこの日以降（UTC）")
    parser.add_argument("--to-date", type=_parse_date, help="作成日がこの日以前（UTC）")
    parser.add_argument("--title", help="タイトルの部分一致（大文字小文字を区別しない）")
    parser.add_argument("--id", action="append", default=[], dest="source_ids", help="会話ID（複数指定可）")
    parser.add_argument("--all", action="store_true", help="全件を取り込み対象として明示する")
    parser.add_argument("--apply", action="store_true", help="dry-runを解除してraw.mdを作成する")
    parser.add_argument(
        "--conversations-dir",
        type=Path,
        default=ROOT / "conversations",
        help=argparse.SUPPRESS,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.from_date and args.to_date and args.from_date > args.to_date:
        parser.error("--from-date は --to-date 以前にしてください。")

    try:
        source = load_export(args.source)
        selected = select_conversations(
            source.conversations,
            from_date=args.from_date,
            to_date=args.to_date,
            title_query=args.title,
            source_ids=args.source_ids,
        )
        import_states = classify_import_states(selected, args.conversations_dir)
        _print_preview(source, selected, import_states)

        if not args.apply:
            print("Dry-run only. 取り込む場合は対象を指定して --apply を追加してください。")
            return 0
        has_selector = args.all or args.from_date or args.to_date or args.title or args.source_ids
        if not has_selector:
            parser.error("--apply には --all、期間、--title、または --id による対象指定が必要です。")
        results = import_conversations(
            selected,
            conversations_dir=args.conversations_dir,
            source_display_name=source.display_name,
        )
        imported = [item for item in results if not item.duplicate and not item.updated]
        updated = [item for item in results if item.updated]
        duplicates = [item for item in results if item.duplicate]
        print(
            f"Imported: {len(imported)}; updated: {len(updated)}; "
            f"skipped duplicates: {len(duplicates)}"
        )
        for item in [*imported, *updated]:
            print(f"- {item.raw_file}")
        print("summary / journal / memory / search index は更新していません。")
        return 0
    except (FileNotFoundError, OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
