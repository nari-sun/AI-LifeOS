import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
VALID_ROLES = {"user", "assistant"}
VALID_STATUSES = {"saved", "finalized"}


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


def save_session(
    root: Path | str = ROOT,
    session_file: Path | str | None = None,
    title: str | None = None,
    status: str = "saved",
    saved_at: datetime | None = None,
) -> SavedSession:
    root = Path(root)
    if status not in VALID_STATUSES:
        raise ValueError("status は saved または finalized を指定してください。")

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

    metadata = {
        "version": 1,
        "session_id": session_id,
        "status": status,
        "title": session_title,
        "jsonl_file": _relative_path(jsonl_file, root),
        "message_count": len(records),
        "started_at": started_at.isoformat(timespec="seconds"),
        "updated_at": updated_at.isoformat(timespec="seconds"),
        "saved_at": now.isoformat(timespec="seconds"),
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
    retention_days: int = 10,
    now: datetime | None = None,
) -> list[ResumeSession]:
    root = Path(root)
    cutoff = _retention_cutoff(retention_days=retention_days, now=now)
    sessions = []

    for jsonl_file in _live_session_files(root):
        summary = _summarize_resume_session(jsonl_file)
        if summary and _is_at_or_after(summary.last_user_at, cutoff):
            sessions.append(summary)

    return sorted(sessions, key=lambda session: session.last_user_at, reverse=True)


def load_resume_session(
    root: Path | str = ROOT,
    session_ref: str = "latest",
    retention_days: int = 10,
    now: datetime | None = None,
) -> tuple[ResumeSession, list[dict[str, Any]]]:
    root = Path(root)

    if session_ref == "latest":
        sessions = list_resumable_sessions(root=root, retention_days=retention_days, now=now)
        if not sessions:
            raise FileNotFoundError("再開できるセッションがありません。")

        jsonl_file = sessions[0].jsonl_file
    else:
        jsonl_file = _resolve_session_ref(root=root, session_ref=session_ref)

    records = _read_jsonl_messages(jsonl_file)
    summary = _build_resume_summary(jsonl_file=jsonl_file, records=records)
    cutoff = _retention_cutoff(retention_days=retention_days, now=now)

    if not _is_at_or_after(summary.last_user_at, cutoff):
        raise ValueError(f"このセッションは最後の入力から{retention_days}日を超えているため再開対象外です。")

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
    delete: bool = False,
) -> list[Path]:
    targets: list[Path] = []
    for session in list_expired_sessions(root=root, retention_days=retention_days, now=now):
        targets.append(session.jsonl_file)
        metadata_file = session.jsonl_file.with_suffix(".session.json")
        if metadata_file.exists():
            targets.append(metadata_file)

    if delete:
        for target in targets:
            target.unlink()

    return targets


def _resolve_session_file(root: Path, session_file: Path | str | None) -> Path:
    if session_file is None:
        return _latest_live_session_file(root)

    path = Path(session_file)
    if not path.is_absolute():
        path = root / path

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
    resume_parser.add_argument("--days", type=int, default=10, help="最後のuser入力から何日以内を残すか")

    prune_parser = subparsers.add_parser("prune", help="再開期限を過ぎたliveセッションを確認または削除する")
    prune_parser.add_argument("--days", type=int, default=10, help="最後のuser入力から何日以内を残すか")
    prune_parser.add_argument("--delete", action="store_true", help="期限切れセッションを実際に削除する")

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
            sessions = list_resumable_sessions(root=root, retention_days=args.days)
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
            targets = prune_expired_sessions(root=root, retention_days=args.days, delete=args.delete)
            if not targets:
                print("削除対象の期限切れセッションはありません。")
                return 0

            action = "削除しました" if args.delete else "削除対象です"
            for target in targets:
                print(f"{action}: {_relative_path(target, root)}")
            if not args.delete:
                print("実際に削除するには --delete を付けてください。")
            return 0
    except (FileNotFoundError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}")
        return 1

    print("ERROR: 不明なコマンドです。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
