import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
VALID_ROLES = {"user", "assistant"}


@dataclass(frozen=True)
class LiveMessage:
    role: str
    content: str
    timestamp: datetime


@dataclass
class LiveSession:
    path: Path
    started_at: datetime

    def append_message(
        self,
        role: str,
        content: str,
        timestamp: datetime | None = None,
    ) -> None:
        now = timestamp or datetime.now().astimezone()
        record = _build_record(role=role, content=content, timestamp=now)

        with self.path.open("a", encoding="utf-8", newline="\n") as file:
            _write_record(file, record)

    def write_messages(self, messages: Iterable[LiveMessage]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)

        with self.path.open("w", encoding="utf-8", newline="\n") as file:
            for message in messages:
                record = _build_record(
                    role=message.role,
                    content=message.content,
                    timestamp=message.timestamp,
                )
                _write_record(file, record)


def create_live_session(
    root: Path | str = ROOT,
    started_at: datetime | None = None,
) -> LiveSession:
    root = Path(root)
    now = started_at or datetime.now().astimezone()
    live_dir = root / "inbox" / "live"
    live_dir.mkdir(parents=True, exist_ok=True)

    base_name = now.strftime("%Y-%m-%d_%H%M%S")
    session_path = _unique_session_path(live_dir, base_name)

    return LiveSession(path=session_path, started_at=now)


def create_live_message(
    role: str,
    content: str,
    timestamp: datetime | None = None,
) -> LiveMessage:
    now = timestamp or datetime.now().astimezone()
    _validate_role(role)
    return LiveMessage(role=role, content=content, timestamp=now)


def _unique_session_path(live_dir: Path, base_name: str) -> Path:
    for index in range(100):
        suffix = "" if index == 0 else f"_{index:02d}"
        path = live_dir / f"{base_name}{suffix}.jsonl"

        if not path.exists():
            return path

    raise FileExistsError("同じ時刻のlive sessionファイルが多すぎます。")


def _validate_role(role: str) -> None:
    if role not in VALID_ROLES:
        raise ValueError("role は user または assistant を指定してください。")


def _build_record(role: str, content: str, timestamp: datetime) -> dict[str, str]:
    _validate_role(role)
    return {
        "role": role,
        "timestamp": timestamp.isoformat(timespec="seconds"),
        "content": content,
    }


def _write_record(file, record: dict[str, str]) -> None:
    file.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
    file.write("\n")
