import argparse
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from process_chat import (
    DEFAULT_MEMORY_CODEX_MODEL,
    DEFAULT_MEMORY_CODEX_REASONING_EFFORT,
    PHASE2_PROMPT_TEMPLATE,
    CodexRunResult,
    GitCommitResult,
    commit_changes,
    prepare_memory_targets,
    run_codex_task,
)
from memory_index import rebuild_index
from session_store import get_session_organization, save_session

ROOT = Path(__file__).resolve().parents[1]
VALID_ROLES = {"user", "assistant"}


@dataclass(frozen=True)
class FinalizeLiveChatResult:
    jsonl_file: Path
    raw_file: Path
    task_file: Path
    prompt: str
    imported_at: datetime
    codex: CodexRunResult | None
    git: GitCommitResult | None
    organization: dict[str, Any]


def finalize_live_chat(
    root: Path | str = ROOT,
    session_file: Path | str | None = None,
    run_codex: bool = False,
    commit: bool = False,
    force: bool = False,
    write_session_metadata: bool = True,
    codex_command: str = "codex.cmd",
    codex_sandbox: str = "workspace-write",
    codex_approval: str = "never",
    codex_model: str | None = DEFAULT_MEMORY_CODEX_MODEL,
    codex_reasoning_effort: str | None = DEFAULT_MEMORY_CODEX_REASONING_EFFORT,
    progress: Callable[[int, str], None] | None = None,
    run_command=None,
) -> FinalizeLiveChatResult:
    root = Path(root)
    _emit_progress(progress, 15, "Reading live JSONL...")
    jsonl_file = _resolve_session_file(root=root, session_file=session_file)
    records = _read_live_records(jsonl_file)
    if not records:
        raise ValueError("live JSONL has no messages.")

    session_started_at = _session_datetime(jsonl_file=jsonl_file, records=records)
    raw_file = _raw_file_for(root=root, imported_at=session_started_at)
    task_file = root / "tasks" / "latest_codex_task.md"
    organization = get_session_organization(root=root, session_file=jsonl_file)
    raw_current = _stage_done_for_current(organization, "raw")
    memory_current = _stage_done_for_current(organization, "memory")
    index_current = _stage_done_for_current(organization, "index")
    records_to_write, message_offset = _records_for_next_raw(records=records, organization=organization)
    imported_at = records_to_write[0]["timestamp"] if records_to_write else session_started_at

    if index_current:
        _emit_progress(progress, 100, "Session is already organized.")
        prompt = _read_or_write_codex_task(root=root, raw_file=raw_file)
        return FinalizeLiveChatResult(
            jsonl_file=jsonl_file,
            raw_file=raw_file,
            task_file=task_file,
            prompt=prompt,
            imported_at=imported_at,
            codex=None,
            git=None,
            organization=organization,
        )

    if raw_current:
        _emit_progress(progress, 35, "raw.md already exists for the current messages.")
        prompt = _read_or_write_codex_task(root=root, raw_file=raw_file)
    else:
        if raw_file.exists() and not force:
            raise FileExistsError(f"raw.md already exists: {_relative_path(raw_file, root)}")

        try:
            _emit_progress(progress, 30, "Creating raw.md...")
            raw_file.parent.mkdir(parents=True, exist_ok=True)
            raw_file.write_text(
                _format_raw_markdown(
                    records=records_to_write,
                    jsonl_file=jsonl_file,
                    root=root,
                    session_id=jsonl_file.stem,
                    imported_at=imported_at,
                    message_offset=message_offset,
                    total_messages=len(records),
                ),
                encoding="utf-8",
            )

            _emit_progress(progress, 45, "Writing Codex task...")
            prompt = _write_codex_task(root=root, raw_file=raw_file)
            if write_session_metadata:
                _emit_progress(progress, 50, "Writing raw stage metadata...")
                save_session(
                    root=root,
                    session_file=jsonl_file,
                    status="raw_created",
                    organize_update={
                        "raw_created": True,
                        "memory_processed": False,
                        "index_updated": False,
                        "failed_stage": None,
                        "last_error": None,
                        "raw_file": _relative_path(raw_file, root),
                        "task_file": _relative_path(task_file, root),
                        "raw_message_count": len(records),
                        "raw_updated_at": records[-1]["timestamp"].isoformat(timespec="seconds"),
                    },
                )
        except Exception as exc:
            if write_session_metadata:
                _write_failure_metadata(
                    root=root,
                    jsonl_file=jsonl_file,
                    status="raw_failed",
                    stage="raw",
                    error=exc,
                    raw_file=raw_file,
                    task_file=task_file,
                )
            raise
        organization = get_session_organization(root=root, session_file=jsonl_file) if write_session_metadata else organization
        raw_current = True
        memory_current = False
        index_current = False

    command_runner = run_command or __import__("subprocess").run

    codex_result = None
    if run_codex:
        if not raw_current:
            raise RuntimeError("raw.md stage did not complete.")
        if not memory_current:
            try:
                _emit_progress(progress, 60, "Preparing journal and memory files...")
                prepare_memory_targets(root=root, target_at=imported_at)
                _emit_progress(progress, 70, "Updating summary, journal, and memory...")
                codex_result = run_codex_task(
                    root=root,
                    prompt=prompt,
                    codex_command=codex_command,
                    sandbox=codex_sandbox,
                    approval=codex_approval,
                    model=codex_model,
                    reasoning_effort=codex_reasoning_effort,
                    capture_output=True,
                    run_command=command_runner,
                )
                if write_session_metadata:
                    save_session(
                        root=root,
                        session_file=jsonl_file,
                        status="raw_created",
                        organize_update={
                            "memory_processed": True,
                            "failed_stage": None,
                            "last_error": None,
                        },
                    )
                _emit_progress(progress, 85, "Finished memory processing.")
            except Exception as exc:
                if write_session_metadata:
                    _write_failure_metadata(
                        root=root,
                        jsonl_file=jsonl_file,
                        status="memory_failed",
                        stage="memory",
                        error=exc,
                        raw_file=raw_file,
                        task_file=task_file,
                    )
                raise

        organization = get_session_organization(root=root, session_file=jsonl_file)
        index_current = _stage_done_for_current(organization, "index")
        if not index_current:
            try:
                _emit_progress(progress, 90, "Updating search index...")
                rebuild_index(root=root)
                if write_session_metadata:
                    save_session(
                        root=root,
                        session_file=jsonl_file,
                        status="finalized",
                        organize_update={
                            "index_updated": True,
                            "failed_stage": None,
                            "last_error": None,
                            "processed_message_count": len(records),
                            "processed_updated_at": records[-1]["timestamp"].isoformat(timespec="seconds"),
                            "completed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                        },
                    )
            except Exception as exc:
                if write_session_metadata:
                    _write_failure_metadata(
                        root=root,
                        jsonl_file=jsonl_file,
                        status="index_failed",
                        stage="index",
                        error=exc,
                        raw_file=raw_file,
                        task_file=task_file,
                    )
                raise
    elif write_session_metadata:
        save_session(
            root=root,
            session_file=jsonl_file,
            status="raw_created",
        )

    git_result = None
    if commit:
        _emit_progress(progress, 95, "Committing public project changes...")
        git_result = commit_changes(
            root=root,
            message=f"Finalize live chat {imported_at.strftime('%Y-%m-%d')}",
            run_command=command_runner,
        )

    organization = get_session_organization(root=root, session_file=jsonl_file)
    _emit_progress(progress, 100, "Exit processing complete.")
    return FinalizeLiveChatResult(
        jsonl_file=jsonl_file,
        raw_file=raw_file,
        task_file=task_file,
        prompt=prompt,
        imported_at=imported_at,
        codex=codex_result,
        git=git_result,
        organization=organization,
    )


def _emit_progress(progress: Callable[[int, str], None] | None, percent: int, message: str) -> None:
    if progress:
        progress(percent, message)


def _stage_done_for_current(organization: dict[str, Any], stage: str) -> bool:
    stages = organization.get("stages", {})
    value = stages.get(stage, {})
    return isinstance(value, dict) and value.get("status") == "done"


def _records_for_next_raw(
    records: list[dict[str, Any]],
    organization: dict[str, Any],
) -> tuple[list[dict[str, Any]], int]:
    processed_count = int(organization.get("organized_message_count") or 0)
    if (
        processed_count > 0
        and processed_count < len(records)
        and organization.get("failed_stage") is None
    ):
        return records[processed_count:], processed_count

    return records, 0


def _read_or_write_codex_task(root: Path, raw_file: Path) -> str:
    task_file = root / "tasks" / "latest_codex_task.md"
    if task_file.exists():
        text = task_file.read_text(encoding="utf-8")
        if str(raw_file.relative_to(root)) in text or raw_file.as_posix() in text:
            return text

    return _write_codex_task(root=root, raw_file=raw_file)


def _write_failure_metadata(
    root: Path,
    jsonl_file: Path,
    status: str,
    stage: str,
    error: Exception,
    raw_file: Path,
    task_file: Path,
) -> None:
    organize_update: dict[str, Any] = {
        "failed_stage": stage,
        "last_error": f"{type(error).__name__}: {error}",
        "raw_file": _relative_path(raw_file, root),
        "task_file": _relative_path(task_file, root),
    }
    if stage == "raw":
        organize_update.update(
            {
                "raw_created": False,
                "memory_processed": False,
                "index_updated": False,
            }
        )
    elif stage == "memory":
        organize_update.update(
            {
                "memory_processed": False,
                "index_updated": False,
            }
        )
    elif stage == "index":
        organize_update["index_updated"] = False

    save_session(
        root=root,
        session_file=jsonl_file,
        status=status,
        organize_update=organize_update,
    )


def _resolve_session_file(root: Path, session_file: Path | str | None) -> Path:
    if session_file is None:
        return _latest_live_session_file(root)

    path = Path(session_file)
    if not path.is_absolute():
        path = root / path

    if not path.exists():
        raise FileNotFoundError(f"live JSONL not found: {path}")
    if path.suffix != ".jsonl":
        raise ValueError("live session file must be a .jsonl file.")

    return path


def _latest_live_session_file(root: Path) -> Path:
    live_dir = root / "inbox" / "live"
    files = [path for path in live_dir.glob("*.jsonl") if path.is_file()] if live_dir.exists() else []
    if not files:
        raise FileNotFoundError("No live JSONL files found under inbox/live.")

    return max(files, key=lambda path: (path.stat().st_mtime, path.name))


def _read_live_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue

        try:
            raw_record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path} line {line_number} is not valid JSON.") from exc

        records.append(_validate_record(raw_record, path=path, line_number=line_number))

    return records


def _validate_record(record: Any, path: Path, line_number: int) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise ValueError(f"{path} line {line_number} must be a JSON object.")

    role = record.get("role")
    timestamp_text = record.get("timestamp")
    content = record.get("content")

    if role not in VALID_ROLES:
        raise ValueError(f"{path} line {line_number} has an invalid role.")
    if not isinstance(timestamp_text, str):
        raise ValueError(f"{path} line {line_number} has an invalid timestamp.")
    if not isinstance(content, str):
        raise ValueError(f"{path} line {line_number} has an invalid content value.")

    try:
        timestamp = datetime.fromisoformat(timestamp_text)
    except ValueError as exc:
        raise ValueError(f"{path} line {line_number} timestamp is not ISO formatted.") from exc

    return {
        "role": role,
        "timestamp": timestamp,
        "content": content,
    }


def _session_datetime(jsonl_file: Path, records: list[dict[str, Any]]) -> datetime:
    try:
        return datetime.strptime(jsonl_file.stem[:17], "%Y-%m-%d_%H%M%S").replace(
            tzinfo=records[0]["timestamp"].tzinfo
        )
    except ValueError:
        return records[0]["timestamp"]


def _raw_file_for(root: Path, imported_at: datetime) -> Path:
    date = imported_at.strftime("%Y-%m-%d")
    year = imported_at.strftime("%Y")
    month = imported_at.strftime("%m")
    time_str = imported_at.strftime("%H%M%S")

    return root / "conversations" / year / month / f"{date}_{time_str}" / "raw.md"


def _format_raw_markdown(
    records: list[dict[str, Any]],
    jsonl_file: Path,
    root: Path,
    session_id: str,
    imported_at: datetime,
    message_offset: int = 0,
    total_messages: int | None = None,
) -> str:
    date = imported_at.strftime("%Y-%m-%d")
    time_text = imported_at.strftime("%H:%M:%S")
    total = total_messages or len(records)
    lines = [
        "# Chat Log",
        "",
        f"Date: {date}",
        f"Time: {time_text}",
        "Source: AI-LifeOS live session",
        f"Session: {session_id}",
        f"Live JSONL: {_relative_path(jsonl_file, root)}",
        f"Message Range: {message_offset + 1}-{message_offset + len(records)} of {total}",
        "",
        "---",
        "",
    ]

    for record in records:
        heading = "User" if record["role"] == "user" else "Assistant"
        timestamp = record["timestamp"].isoformat(timespec="seconds")
        lines.extend(
            [
                f"## {heading}",
                "",
                f"Timestamp: {timestamp}",
                "",
                record["content"].rstrip(),
                "",
            ]
        )

    return "\n".join(lines).rstrip() + "\n"


def _write_codex_task(root: Path, raw_file: Path) -> str:
    # This is the only runtime prompt source for Phase2.5 memory processing.
    # See prompts/README.md before adding or editing prompt files.
    prompt_template = root / PHASE2_PROMPT_TEMPLATE
    template_text = prompt_template.read_text(encoding="utf-8")
    prompt = template_text.replace("{RAW_FILE}", str(raw_file.relative_to(root)))

    tasks_dir = root / "tasks"
    tasks_dir.mkdir(exist_ok=True)
    (tasks_dir / "latest_codex_task.md").write_text(prompt, encoding="utf-8")
    return prompt


def _relative_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Convert a live JSONL chat into an AI-LifeOS raw.md.")
    parser.add_argument("--root", default=ROOT, help="AI-LifeOS root directory.")
    parser.add_argument("--file", help="Target inbox/live/*.jsonl file. Defaults to the latest file.")
    parser.add_argument("--force", action="store_true", help="Overwrite raw.md if it already exists.")
    parser.add_argument("--no-session-metadata", action="store_true", help="Do not write .session.json metadata.")
    parser.add_argument("--run-codex", action="store_true", help="Run the existing Phase2.5 Codex task after raw.md is created.")
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Commit only public project files; generated conversations, journal, memory, inbox, and tasks stay local.",
    )
    parser.add_argument("--codex-command", default="codex.cmd", help="Codex CLI command.")
    parser.add_argument("--codex-model", default=DEFAULT_MEMORY_CODEX_MODEL, help="Codex model for summary/journal/memory.")
    parser.add_argument(
        "--codex-reasoning-effort",
        default=DEFAULT_MEMORY_CODEX_REASONING_EFFORT,
        choices=("minimal", "low", "medium", "high", "xhigh"),
        help="Codex reasoning effort for summary/journal/memory.",
    )
    parser.add_argument(
        "--codex-sandbox",
        default="workspace-write",
        choices=("read-only", "workspace-write", "danger-full-access"),
        help="Sandbox mode for Codex exec.",
    )
    parser.add_argument(
        "--codex-approval",
        default="never",
        choices=("untrusted", "on-request", "never"),
        help="Approval policy for Codex exec.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    try:
        result = finalize_live_chat(
            root=args.root,
            session_file=args.file,
            run_codex=args.run_codex,
            commit=args.commit,
            force=args.force,
            write_session_metadata=not args.no_session_metadata,
            codex_command=args.codex_command,
            codex_sandbox=args.codex_sandbox,
            codex_approval=args.codex_approval,
            codex_model=args.codex_model,
            codex_reasoning_effort=args.codex_reasoning_effort,
        )
    except (FileNotFoundError, FileExistsError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 1

    root = Path(args.root)
    print("Finalized live chat.")
    print(f"JSONL: {_relative_path(result.jsonl_file, root)}")
    print(f"raw.md: {_relative_path(result.raw_file, root)}")
    print(f"Task: {_relative_path(result.task_file, root)}")

    if result.codex:
        print("Codex task completed.")
    if result.git:
        print("Public project Git commit created." if result.git.committed else "No Git changes to commit.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
