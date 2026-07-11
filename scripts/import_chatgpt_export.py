from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
VALID_ROLES = {"user", "assistant"}
METADATA_FILE = "import_metadata.json"


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


@dataclass(frozen=True)
class ExportSource:
    display_name: str
    conversations: tuple[ExportConversation, ...]


@dataclass(frozen=True)
class ImportResult:
    conversation: ExportConversation
    raw_file: Path | None
    duplicate: bool


def load_export(source: Path | str) -> ExportSource:
    source_path = Path(source)
    if not source_path.exists():
        raise FileNotFoundError(f"エクスポート元が見つかりません: {source_path}")

    if source_path.is_dir():
        matches = sorted(path for path in source_path.rglob("conversations.json") if path.is_file())
        if not matches:
            raise FileNotFoundError("フォルダ内に conversations.json が見つかりません。")
        if len(matches) > 1:
            raise ValueError("conversations.json が複数あります。対象ファイルを直接指定してください。")
        payload = _load_json_bytes(matches[0].read_bytes())
        display_name = f"{source_path.name}/{matches[0].relative_to(source_path).as_posix()}"
    elif source_path.suffix.lower() == ".zip":
        payload, member_name = _load_from_zip(source_path)
        display_name = f"{source_path.name}/{member_name}"
    else:
        if source_path.name.lower() != "conversations.json":
            raise ValueError("JSONファイルを直接指定する場合は conversations.json を指定してください。")
        payload = _load_json_bytes(source_path.read_bytes())
        display_name = source_path.name

    if not isinstance(payload, list):
        raise ValueError("conversations.json のルートは配列である必要があります。")

    conversations = tuple(_parse_conversation(item, index) for index, item in enumerate(payload))
    return ExportSource(display_name=display_name, conversations=conversations)


def _load_from_zip(source_path: Path) -> tuple[Any, str]:
    try:
        with zipfile.ZipFile(source_path) as archive:
            matches = [
                info
                for info in archive.infolist()
                if not info.is_dir() and Path(info.filename.replace("\\", "/")).name.lower() == "conversations.json"
            ]
            if not matches:
                raise FileNotFoundError("zip内に conversations.json が見つかりません。")
            if len(matches) > 1:
                raise ValueError("zip内に conversations.json が複数あります。")
            info = matches[0]
            return _load_json_bytes(archive.read(info)), info.filename.replace("\\", "/")
    except zipfile.BadZipFile as exc:
        raise ValueError("有効なzipファイルではありません。") from exc


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
    messages = tuple(_extract_messages(value))
    raw_id = value.get("id") or value.get("conversation_id")
    candidate_id = str(raw_id).strip() if raw_id else ""
    source_id = candidate_id or _derived_source_id(title, created_at, messages)
    return ExportConversation(
        source_id=source_id,
        title=title,
        created_at=created_at,
        updated_at=updated_at,
        messages=messages,
    )


def _extract_messages(conversation: dict[str, Any]) -> Iterable[ImportedMessage]:
    mapping = conversation.get("mapping")
    if not isinstance(mapping, dict):
        return ()

    nodes = _active_branch_nodes(mapping, conversation.get("current_node"))
    result: list[ImportedMessage] = []
    for node in nodes:
        message = node.get("message")
        if not isinstance(message, dict):
            continue
        author = message.get("author")
        role = author.get("role") if isinstance(author, dict) else None
        if role not in VALID_ROLES:
            continue
        content = _message_text(message.get("content"))
        if not content.strip():
            continue
        result.append(
            ImportedMessage(
                role=role,
                content=content.strip(),
                created_at=_timestamp(message.get("create_time")),
            )
        )
    return result


def _active_branch_nodes(mapping: dict[str, Any], current_node: Any) -> list[dict[str, Any]]:
    if isinstance(current_node, str) and current_node in mapping:
        nodes: list[dict[str, Any]] = []
        seen: set[str] = set()
        node_id: Any = current_node
        while isinstance(node_id, str) and node_id in mapping and node_id not in seen:
            seen.add(node_id)
            node = mapping[node_id]
            if not isinstance(node, dict):
                break
            nodes.append(node)
            node_id = node.get("parent")
        nodes.reverse()
        return nodes

    indexed_nodes = [(position, node) for position, node in enumerate(mapping.values()) if isinstance(node, dict)]
    return [
        node
        for _, node in sorted(
            indexed_nodes,
            key=lambda pair: (_message_sort_time(pair[1]), pair[0]),
        )
    ]


def _message_sort_time(node: dict[str, Any]) -> float:
    message = node.get("message")
    value = message.get("create_time") if isinstance(message, dict) else None
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("inf")


def _message_text(content: Any) -> str:
    if not isinstance(content, dict):
        return ""
    parts = content.get("parts")
    if isinstance(parts, list):
        return "\n\n".join(part for part in parts if isinstance(part, str))
    text = content.get("text")
    return text if isinstance(text, str) else ""


def _timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    except (ValueError, TypeError, OverflowError, OSError):
        return None


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
    root = Path(conversations_dir)
    source_ids: set[str] = set()
    if not root.exists():
        return source_ids
    for path in root.rglob(METADATA_FILE):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if isinstance(value, dict) and value.get("source") == "chatgpt_export":
            source_id = value.get("source_conversation_id")
            if isinstance(source_id, str):
                source_ids.add(source_id)
    return source_ids


def import_conversations(
    conversations: Iterable[ExportConversation],
    conversations_dir: Path | str,
    source_display_name: str,
    imported_at: datetime | None = None,
) -> list[ImportResult]:
    output_root = Path(conversations_dir)
    known_ids = find_imported_source_ids(output_root)
    now = imported_at or datetime.now(timezone.utc)
    results: list[ImportResult] = []

    for conversation in conversations:
        if conversation.source_id in known_ids:
            results.append(ImportResult(conversation=conversation, raw_file=None, duplicate=True))
            continue
        raw_file = _next_raw_path(output_root, conversation.created_at or now)
        metadata_file = raw_file.parent / METADATA_FILE
        raw_text = _format_raw(conversation, now)
        metadata = {
            "schema_version": 1,
            "source": "chatgpt_export",
            "source_file": source_display_name,
            "source_conversation_id": conversation.source_id,
            "title": conversation.title,
            "source_created_at": _iso(conversation.created_at),
            "source_updated_at": _iso(conversation.updated_at),
            "message_count": len(conversation.messages),
            "imported_at": _iso(now),
            "memory_processing": "not_requested",
        }
        raw_file.parent.mkdir(parents=True, exist_ok=False)
        try:
            raw_file.write_text(raw_text, encoding="utf-8")
            metadata_file.write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except Exception:
            raw_file.unlink(missing_ok=True)
            metadata_file.unlink(missing_ok=True)
            try:
                raw_file.parent.rmdir()
            except OSError:
                pass
            raise
        known_ids.add(conversation.source_id)
        results.append(ImportResult(conversation=conversation, raw_file=raw_file, duplicate=False))
    return results


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
    duplicate_ids: set[str],
) -> None:
    dates = [item.created_at.date() for item in selected if item.created_at]
    period = f"{min(dates)} .. {max(dates)}" if dates else "unknown"
    new_count = sum(item.source_id not in duplicate_ids for item in selected)
    print(f"Source: {source.display_name}")
    print(f"Export conversations: {len(source.conversations)}")
    print(f"Selected: {len(selected)} (new: {new_count}, duplicate: {len(selected) - new_count})")
    print(f"Period (UTC): {period}")
    for item in selected:
        created = item.created_at.date().isoformat() if item.created_at else "unknown"
        state = "duplicate" if item.source_id in duplicate_ids else "new"
        print(f"- [{item.source_id}] {created} | {item.title} | {len(item.messages)} messages | {state}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="ChatGPT exportのconversations.jsonを確認し、AI-LifeOS raw.mdへ安全に取り込みます。"
    )
    parser.add_argument("source", type=Path, help="exportフォルダ、zip、またはconversations.json")
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
        duplicates = find_imported_source_ids(args.conversations_dir)
        _print_preview(source, selected, duplicates)

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
        imported = [item for item in results if not item.duplicate]
        print(f"Imported: {len(imported)}; skipped duplicates: {len(results) - len(imported)}")
        for item in imported:
            print(f"- {item.raw_file}")
        print("summary / journal / memory / search index は更新していません。")
        return 0
    except (FileNotFoundError, OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
