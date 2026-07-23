import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
VALID_ROLES = {"user", "assistant"}
VALID_STATUSES = {"saved", "raw_created", "raw_failed", "memory_failed", "index_failed", "finalized"}
DEFAULT_RESUME_LIST_LIMIT = 50
ORGANIZE_STAGE_LABELS = {
    "raw": "raw.md作成",
    "memory": "記憶整理",
    "index": "検索index更新",
}


@dataclass(frozen=True)
class SavedSession:
    session_id: str
    status: str
    title: str
    jsonl_file: Path
    metadata_file: Path
    message_count: int
    started_at: datetime
    updated_at: datetime
    saved_at: datetime


@dataclass(frozen=True)
class ResumeSession:
    session_id: str
    title: str
    jsonl_file: Path
    message_count: int
    started_at: datetime
    updated_at: datetime
    last_user_at: datetime


@dataclass(frozen=True)
class ExpiredSessionCleanupResult:
    """Compatibility result for callers that previously requested cleanup.

    Expired sessions are now retained permanently; ``deleted_paths`` is always
    empty and the status is always ``保持``.
    """

    session_id: str
    status: str
    deleted_paths: tuple[Path, ...]


def save_session(
    root: Path | str = ROOT,
    session_file: Path | str | None = None,
    title: str | None = None,
    status: str = "saved",
    saved_at: datetime | None = None,
    organize_update: dict[str, Any] | None = None,
) -> SavedSession:
    root = Path(root)
    if status not in VALID_STATUSES:
        raise ValueError("status が不正です。")

    jsonl_file = _resolve_session_file(root=root, session_file=session_file)
    records = _read_jsonl_messages(jsonl_file)
    if not records:
        raise ValueError("保存するメッセージがありません。")

    now = saved_at or datetime.now().astimezone()
    session_id = jsonl_file.stem
    session_title = _normalize_title(title) or _derive_title(records, session_id)
    started_at = records[0]["timestamp"]
    updated_at = records[-1]["timestamp"]
    metadata_file = jsonl_file.with_suffix(".session.json")
    # This is a mutating path.  If an existing sidecar is unreadable, treating
    # it as an empty object could silently erase temporary/exclusion controls.
    existing_metadata = _read_metadata_file(metadata_file, strict=True)
    organize = _merge_organize_state(
        existing_metadata=existing_metadata,
        update=organize_update,
        current_message_count=len(records),
        current_updated_at=updated_at,
        now=now,
    )

    # Session-scoped extensions (for example personalization/privacy controls)
    # belong to the live session and must survive routine save/finalize updates.
    # Canonical session fields below remain authoritative.
    metadata = {
        **existing_metadata,
        "version": 1,
        "session_id": session_id,
        "status": status,
        "title": session_title,
        "jsonl_file": _relative_path(jsonl_file, root),
        "message_count": len(records),
        "started_at": started_at.isoformat(timespec="seconds"),
        "updated_at": updated_at.isoformat(timespec="seconds"),
        "saved_at": now.isoformat(timespec="seconds"),
        "finalized_message_count": organize["processed_message_count"] if organize["index_updated"] else None,
        "finalized_updated_at": organize["processed_updated_at"] if organize["index_updated"] else None,
        "organize": organize,
    }

    metadata_file.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    return SavedSession(
        session_id=session_id,
        status=status,
        title=session_title,
        jsonl_file=jsonl_file,
        metadata_file=metadata_file,
        message_count=len(records),
        started_at=started_at,
        updated_at=updated_at,
        saved_at=now,
    )


def list_saved_sessions(root: Path | str = ROOT) -> list[SavedSession]:
    root = Path(root)
    live_dir = root / "inbox" / "live"
    if not live_dir.exists():
        return []

    sessions = []
    for metadata_file in sorted(live_dir.glob("*.session.json")):
        sessions.append(_load_saved_session(root=root, metadata_file=metadata_file))

    return sorted(sessions, key=lambda session: session.saved_at, reverse=True)


def list_resumable_sessions(
    root: Path | str = ROOT,
    limit: int | None = DEFAULT_RESUME_LIST_LIMIT,
) -> list[ResumeSession]:
    root = Path(root)
    sessions = []

    for jsonl_file in _live_session_files(root):
        summary = _summarize_resume_session(jsonl_file)
        if summary:
            sessions.append(summary)

    sessions.sort(key=lambda session: session.last_user_at, reverse=True)
    if limit is None:
        return sessions
    return sessions[: max(limit, 1)]


def load_resume_session(
    root: Path | str = ROOT,
    session_ref: str = "latest",
) -> tuple[ResumeSession, list[dict[str, Any]]]:
    root = Path(root)

    if session_ref == "latest":
        sessions = list_resumable_sessions(root=root)
        if not sessions:
            raise FileNotFoundError("再開できるセッションがありません。")

        jsonl_file = sessions[0].jsonl_file
    else:
        jsonl_file = _resolve_session_ref(root=root, session_ref=session_ref)

    records = _read_jsonl_messages(jsonl_file)
    summary = _build_resume_summary(jsonl_file=jsonl_file, records=records)

    return summary, records


def list_expired_sessions(
    root: Path | str = ROOT,
    retention_days: int = 10,
    now: datetime | None = None,
) -> list[ResumeSession]:
    root = Path(root)
    cutoff = _retention_cutoff(retention_days=retention_days, now=now)
    sessions = []

    for jsonl_file in _live_session_files(root):
        summary = _summarize_resume_session(jsonl_file)
        if summary and not _is_at_or_after(summary.last_user_at, cutoff):
            sessions.append(summary)

    return sorted(sessions, key=lambda session: session.last_user_at)


def prune_expired_sessions(
    root: Path | str = ROOT,
    retention_days: int = 10,
    now: datetime | None = None,
) -> list[Path]:
    """List sessions older than the reference window without deleting them."""
    return [session.jsonl_file for session in list_expired_sessions(root=root, retention_days=retention_days, now=now)]


def cleanup_expired_sessions(
    root: Path | str = ROOT,
    retention_days: int = 10,
    now: datetime | None = None,
    delete: bool = False,
    auto_finalize: bool = False,
) -> list[ExpiredSessionCleanupResult]:
    """Retain expired sessions for backward-compatible callers.

    The parameters are accepted only so older local callers fail safely after
    the retention-policy change.  No file is finalized, moved, or deleted.
    """
    del delete, auto_finalize
    return [
        ExpiredSessionCleanupResult(
            session_id=session.session_id,
            status="保持",
            deleted_paths=(),
        )
        for session in list_expired_sessions(root=root, retention_days=retention_days, now=now)
    ]


def get_session_organization(root: Path | str = ROOT, session_file: Path | str | None = None) -> dict[str, Any]:
    root = Path(root)
    jsonl_file = _resolve_session_file(root=root, session_file=session_file)
    records = _read_jsonl_messages(jsonl_file)
    current_message_count = len(records)
    current_updated_at = records[-1]["timestamp"].isoformat(timespec="seconds") if records else None
    metadata = _read_metadata_file(jsonl_file.with_suffix(".session.json"))
    organize = _normalize_organize_state(metadata)

    raw_matches_current = bool(
        organize["raw_created"]
        and organize["raw_message_count"] == current_message_count
        and organize["raw_updated_at"] == current_updated_at
    )
    memory_matches_current = bool(organize["memory_processed"] and raw_matches_current)
    index_matches_current = bool(
        organize["index_updated"]
        and organize["processed_message_count"] == current_message_count
        and organize["processed_updated_at"] == current_updated_at
    )

    failed_stage = organize["failed_stage"]
    if failed_stage and not raw_matches_current and failed_stage != "raw":
        failed_stage = None
    if failed_stage == "index" and not memory_matches_current:
        failed_stage = None

    is_organized = bool(current_message_count > 0 and index_matches_current and not failed_stage)
    if current_message_count == 0:
        status = "empty"
        label = "未開始"
        can_organize = False
        next_stage = None
    elif is_organized:
        status = "organized"
        label = "整理済み"
        can_organize = False
        next_stage = None
    elif failed_stage == "raw":
        status = "raw_failed"
        label = "raw.md作成失敗"
        can_organize = True
        next_stage = "raw"
    elif failed_stage == "memory":
        status = "memory_failed"
        label = "記憶整理失敗"
        can_organize = True
        next_stage = "memory"
    elif failed_stage == "index":
        status = "index_failed"
        label = "index更新失敗"
        can_organize = True
        next_stage = "index"
    elif raw_matches_current and not organize["memory_processed"]:
        status = "raw_created"
        label = "raw.md作成済み"
        can_organize = True
        next_stage = "memory"
    elif organize["index_updated"] and organize["processed_message_count"]:
        status = "unorganized_new"
        label = "未整理の新規会話あり"
        can_organize = True
        next_stage = "raw"
    else:
        status = "unorganized"
        label = "未整理"
        can_organize = True
        next_stage = "raw"

    return {
        "status": status,
        "label": label,
        "can_organize": can_organize,
        "is_organized": is_organized,
        "next_stage": next_stage,
        "failed_stage": failed_stage,
        "last_error": organize["last_error"],
        "raw_file": organize["raw_file"],
        "task_file": organize["task_file"],
        "current_message_count": current_message_count,
        "current_updated_at": current_updated_at,
        "organized_message_count": organize["processed_message_count"],
        "organized_updated_at": organize["processed_updated_at"],
        "stages": {
            "raw": _stage_status("raw", done=raw_matches_current or is_organized, failed=failed_stage == "raw"),
            "memory": _stage_status("memory", done=memory_matches_current or is_organized, failed=failed_stage == "memory"),
            "index": _stage_status("index", done=is_organized, failed=failed_stage == "index"),
        },
    }


def _read_metadata_file(metadata_file: Path, *, strict: bool = False) -> dict[str, Any]:
    if not metadata_file.exists():
        return {}

    try:
        data = json.loads(metadata_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        if strict:
            raise ValueError(f"既存のセッション情報が壊れているため上書きできません: {metadata_file.name}") from exc
        return {}

    if isinstance(data, dict):
        return data
    if strict:
        raise ValueError(f"既存のセッション情報が不正なため上書きできません: {metadata_file.name}")
    return {}


def _merge_organize_state(
    existing_metadata: dict[str, Any],
    update: dict[str, Any] | None,
    current_message_count: int,
    current_updated_at: datetime,
    now: datetime,
) -> dict[str, Any]:
    organize = _normalize_organize_state(existing_metadata)
    if not update:
        return organize

    merged = {**organize, **update}
    merged["updated_at"] = now.isoformat(timespec="seconds")

    if merged.get("index_updated"):
        merged["processed_message_count"] = int(merged.get("processed_message_count") or current_message_count)
        merged["processed_updated_at"] = str(
            merged.get("processed_updated_at") or current_updated_at.isoformat(timespec="seconds")
        )

    return _normalize_organize_state({"organize": merged})


def _normalize_organize_state(metadata: dict[str, Any]) -> dict[str, Any]:
    raw = metadata.get("organize")
    state = raw if isinstance(raw, dict) else {}

    normalized = {
        "raw_created": bool(state.get("raw_created", False)),
        "memory_processed": bool(state.get("memory_processed", False)),
        "index_updated": bool(state.get("index_updated", False)),
        "failed_stage": _optional_stage(state.get("failed_stage")),
        "last_error": _optional_string(state.get("last_error")),
        "raw_file": _optional_string(state.get("raw_file")),
        "task_file": _optional_string(state.get("task_file")),
        "raw_message_count": _optional_int(state.get("raw_message_count")),
        "raw_updated_at": _optional_string(state.get("raw_updated_at")),
        "processed_message_count": _optional_int(state.get("processed_message_count")) or 0,
        "processed_updated_at": _optional_string(state.get("processed_updated_at")),
        "completed_at": _optional_string(state.get("completed_at")),
        "updated_at": _optional_string(state.get("updated_at")),
    }

    if not state and metadata.get("status") == "finalized":
        message_count = _optional_int(metadata.get("message_count")) or 0
        updated_at = _optional_string(metadata.get("updated_at"))
        normalized.update(
            {
                "raw_created": True,
                "memory_processed": True,
                "index_updated": True,
                "raw_message_count": message_count,
                "raw_updated_at": updated_at,
                "processed_message_count": message_count,
                "processed_updated_at": updated_at,
                "completed_at": _optional_string(metadata.get("saved_at")),
                "updated_at": _optional_string(metadata.get("saved_at")),
            }
        )

    if normalized["failed_stage"] is not None:
        normalized["index_updated"] = False
        if normalized["failed_stage"] in {"raw", "memory"}:
            normalized["memory_processed"] = normalized["failed_stage"] != "raw" and normalized["memory_processed"]

    return normalized


def _stage_status(name: str, done: bool, failed: bool) -> dict[str, str]:
    if failed:
        status = "failed"
    elif done:
        status = "done"
    else:
        status = "pending"

    return {
        "name": name,
        "label": ORGANIZE_STAGE_LABELS[name],
        "status": status,
    }


def _optional_stage(value: Any) -> str | None:
    text = _optional_string(value)
    return text if text in ORGANIZE_STAGE_LABELS else None


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _resolve_session_file(root: Path, session_file: Path | str | None) -> Path:
    if session_file is None:
        return _latest_live_session_file(root)

    path = Path(session_file)
    if not path.is_absolute():
        path = root / path
    path = path.resolve()
    root_resolved = root.resolve()

    try:
        path.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError("セッションファイルはAI-LifeOSルート内を指定してください。") from exc

    if not path.exists():
        raise FileNotFoundError(f"セッションJSONLが見つかりません: {path}")
    if path.suffix != ".jsonl":
        raise ValueError("セッションファイルは .jsonl を指定してください。")

    return path


def _latest_live_session_file(root: Path) -> Path:
    files = _live_session_files(root)
    if not files:
        raise FileNotFoundError("inbox/live に保存対象のJSONLがありません。")

    return max(files, key=lambda path: (path.stat().st_mtime, path.name))


def _live_session_files(root: Path) -> list[Path]:
    live_dir = root / "inbox" / "live"
    return [path for path in live_dir.glob("*.jsonl") if path.is_file()] if live_dir.exists() else []


def _resolve_session_ref(root: Path, session_ref: str) -> Path:
    ref_path = Path(session_ref)
    if ref_path.suffix == ".jsonl" or ref_path.is_absolute() or "\\" in session_ref or "/" in session_ref:
        return _resolve_session_file(root=root, session_file=ref_path)

    path = root / "inbox" / "live" / f"{session_ref}.jsonl"
    if path.exists():
        return path

    raise FileNotFoundError(f"セッションが見つかりません: {session_ref}")


def _read_jsonl_messages(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue

        try:
            raw_record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path} の {line_number} 行目がJSONとして読めません。") from exc

        records.append(_validate_message_record(raw_record, path, line_number))

    return records


def _validate_message_record(record: Any, path: Path, line_number: int) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise ValueError(f"{path} の {line_number} 行目はJSON objectである必要があります。")

    role = record.get("role")
    timestamp_text = record.get("timestamp")
    content = record.get("content")

    if role not in VALID_ROLES:
        raise ValueError(f"{path} の {line_number} 行目の role が不正です。")
    if not isinstance(timestamp_text, str):
        raise ValueError(f"{path} の {line_number} 行目の timestamp が不正です。")
    if not isinstance(content, str):
        raise ValueError(f"{path} の {line_number} 行目の content が不正です。")

    try:
        timestamp = datetime.fromisoformat(timestamp_text)
    except ValueError as exc:
        raise ValueError(f"{path} の {line_number} 行目の timestamp がISO形式ではありません。") from exc

    return {
        "role": role,
        "timestamp": timestamp,
        "content": content,
    }


def _load_saved_session(root: Path, metadata_file: Path) -> SavedSession:
    data = json.loads(metadata_file.read_text(encoding="utf-8"))
    jsonl_file = root / data["jsonl_file"]

    return SavedSession(
        session_id=data["session_id"],
        status=data["status"],
        title=data["title"],
        jsonl_file=jsonl_file,
        metadata_file=metadata_file,
        message_count=int(data["message_count"]),
        started_at=datetime.fromisoformat(data["started_at"]),
        updated_at=datetime.fromisoformat(data["updated_at"]),
        saved_at=datetime.fromisoformat(data["saved_at"]),
    )


def _summarize_resume_session(jsonl_file: Path) -> ResumeSession | None:
    records = _read_jsonl_messages(jsonl_file)
    if not records:
        return None

    return _build_resume_summary(jsonl_file=jsonl_file, records=records)


def _build_resume_summary(jsonl_file: Path, records: list[dict[str, Any]]) -> ResumeSession:
    if not records:
        raise ValueError("再開するメッセージがありません。")

    last_user_at = _last_user_timestamp(records)
    if last_user_at is None:
        raise ValueError(f"{jsonl_file} にuser入力がないため再開できません。")

    return ResumeSession(
        session_id=jsonl_file.stem,
        title=_derive_title(records, jsonl_file.stem),
        jsonl_file=jsonl_file,
        message_count=len(records),
        started_at=records[0]["timestamp"],
        updated_at=records[-1]["timestamp"],
        last_user_at=last_user_at,
    )


def _last_user_timestamp(records: list[dict[str, Any]]) -> datetime | None:
    for record in reversed(records):
        if record["role"] == "user":
            return record["timestamp"]

    return None


def _retention_cutoff(retention_days: int, now: datetime | None = None) -> datetime:
    if retention_days < 0:
        raise ValueError("retention_days は0以上を指定してください。")

    current = now or datetime.now().astimezone()
    return current - timedelta(days=retention_days)


def _is_at_or_after(value: datetime, cutoff: datetime) -> bool:
    if (value.tzinfo is None) != (cutoff.tzinfo is None):
        value = value.replace(tzinfo=None)
        cutoff = cutoff.replace(tzinfo=None)

    return value >= cutoff


def _normalize_title(title: str | None) -> str | None:
    if title is None:
        return None

    normalized = " ".join(title.split())
    return normalized or None


def _derive_title(records: list[dict[str, Any]], session_id: str) -> str:
    for record in records:
        if record["role"] == "user":
            content = " ".join(record["content"].split())
            if content:
                return _truncate_title(content)

    return f"Session {session_id}"


def _truncate_title(text: str, limit: int = 40) -> str:
    if len(text) <= limit:
        return text

    return text[: limit - 3] + "..."


def _relative_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Save or list AI-LifeOS live conversation sessions.")
    parser.add_argument("--root", default=ROOT, help="AI-LifeOSのルートディレクトリ")

    subparsers = parser.add_subparsers(dest="command", required=True)

    save_parser = subparsers.add_parser("save", help="live JSONLを保存済みセッションとして記録する")
    save_parser.add_argument("--file", help="保存する inbox/live/*.jsonl。未指定なら最新を使う")
    save_parser.add_argument("--title", help="保存セッションのタイトル")

    subparsers.add_parser("list", help="保存済みセッションを一覧表示する")

    resume_parser = subparsers.add_parser("resume-list", help="再開できるliveセッションを一覧表示する")
    resume_parser.add_argument("--days", type=int, help=argparse.SUPPRESS)
    resume_parser.add_argument("--limit", type=int, default=DEFAULT_RESUME_LIST_LIMIT, help="表示する最新セッション数")

    prune_parser = subparsers.add_parser("prune", help="指定日数を超えたliveセッションを一覧する（削除しない）")
    prune_parser.add_argument("--days", type=int, default=10, help="最後のuser入力から何日を超えたものを表示するか")

    return parser


def main() -> int:
    args = build_parser().parse_args()
    root = Path(args.root)

    try:
        if args.command == "save":
            session = save_session(root=root, session_file=args.file, title=args.title)
            print("セッションを保存しました。")
            print(_relative_path(session.metadata_file, root))
            return 0

        if args.command == "list":
            sessions = list_saved_sessions(root=root)
            if not sessions:
                print("保存済みセッションはありません。")
                return 0

            for session in sessions:
                print(
                    f"{session.session_id}\t{session.status}\t"
                    f"{session.message_count} messages\t{session.title}"
                )
            return 0

        if args.command == "resume-list":
            sessions = list_resumable_sessions(root=root, limit=args.limit)
            if not sessions:
                print("再開できるセッションはありません。")
                return 0

            for session in sessions:
                print(
                    f"{session.session_id}\tlast-user={session.last_user_at.isoformat(timespec='seconds')}\t"
                    f"{session.message_count} messages\t{session.title}"
                )
            return 0

        if args.command == "prune":
            targets = prune_expired_sessions(
                root=root,
                retention_days=args.days,
            )
            if not targets:
                print(f"最後のuser入力から{args.days}日を超えたセッションはありません。")
                return 0

            for target in targets:
                print(f"{args.days}日超: {_relative_path(target, root)}")
            return 0
    except (FileNotFoundError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}")
        return 1

    print("ERROR: 不明なコマンドです。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
