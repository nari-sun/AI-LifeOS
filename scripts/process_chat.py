import argparse
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from codex_cli_options import add_codex_model_options
from memory_index import ensure_memory_files, rebuild_index

ROOT = Path(__file__).resolve().parents[1]
# PublicEdition keeps generated personal data local; commits only stage project files.
DEFAULT_COMMIT_PATHS = (
    ".gitignore",
    "AGENTS.md",
    "README.md",
    "config",
    "desktop",
    "docs",
    "prompts",
    "scripts",
    "templates",
    "tests",
)
DEFAULT_MEMORY_CODEX_MODEL = "gpt-5.5"
DEFAULT_MEMORY_CODEX_REASONING_EFFORT = "xhigh"


@dataclass(frozen=True)
class ProcessChatResult:
    raw_file: Path
    task_file: Path
    prompt: str
    imported_at: datetime


@dataclass(frozen=True)
class CodexRunResult:
    command: tuple[str, ...]
    returncode: int


@dataclass(frozen=True)
class GitCommitResult:
    committed: bool
    message: str


@dataclass(frozen=True)
class ProcessChatSessionResult:
    chat: ProcessChatResult
    codex: CodexRunResult | None
    git: GitCommitResult | None


def _resolve_imported_at(date_text: str | None, imported_at: datetime | None) -> datetime:
    now = imported_at or datetime.now()
    if not date_text:
        return now

    try:
        target_date = datetime.strptime(date_text, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError("--date は YYYY-MM-DD 形式で指定してください。") from exc

    return datetime.combine(target_date, now.time())


def process_chat(
    root: Path | str = ROOT,
    imported_at: datetime | None = None,
    date_text: str | None = None,
    keep_inbox: bool = False,
) -> ProcessChatResult:
    root = Path(root)
    inbox = root / "inbox" / "chat.txt"
    prompt_template = root / "prompts" / "codex_phase2_prompt.md"
    now = _resolve_imported_at(date_text, imported_at)

    date = now.strftime("%Y-%m-%d")
    year = now.strftime("%Y")
    month = now.strftime("%m")
    time_str = now.strftime("%H%M%S")

    if not inbox.exists():
        raise FileNotFoundError("inbox/chat.txt がないぞ。まず会話を貼れ。")

    chat = inbox.read_text(encoding="utf-8").strip()

    if not chat:
        raise ValueError("inbox/chat.txt が空だぞ。")

    template_text = prompt_template.read_text(encoding="utf-8")

    session_dir = root / "conversations" / year / month / f"{date}_{time_str}"
    session_dir.mkdir(parents=True, exist_ok=True)

    raw_file = session_dir / "raw.md"

    raw_file.write_text(
        f"# Chat Log\n\nDate: {date}\nTime: {now.strftime('%H:%M:%S')}\n\n---\n\n{chat}\n",
        encoding="utf-8",
    )

    if not keep_inbox:
        inbox.write_text("", encoding="utf-8")

    prompt = template_text.replace("{RAW_FILE}", str(raw_file.relative_to(root)))

    tasks_dir = root / "tasks"
    tasks_dir.mkdir(exist_ok=True)

    task_file = tasks_dir / "latest_codex_task.md"
    task_file.write_text(prompt, encoding="utf-8")

    return ProcessChatResult(
        raw_file=raw_file,
        task_file=task_file,
        prompt=prompt,
        imported_at=now,
    )


def prepare_memory_targets(root: Path | str = ROOT, target_at: datetime | None = None) -> None:
    root = Path(root)
    now = target_at or datetime.now()

    ensure_memory_files(root)

    journal_dir = root / "journal" / now.strftime("%Y") / now.strftime("%m")
    journal_dir.mkdir(parents=True, exist_ok=True)


def run_codex_task(
    root: Path | str,
    prompt: str,
    codex_command: str = "codex.cmd",
    sandbox: str = "workspace-write",
    approval: str = "never",
    model: str | None = DEFAULT_MEMORY_CODEX_MODEL,
    reasoning_effort: str | None = DEFAULT_MEMORY_CODEX_REASONING_EFFORT,
    capture_output: bool = False,
    run_command=subprocess.run,
) -> CodexRunResult:
    root = Path(root)
    command = [
        codex_command,
        "--ask-for-approval",
        approval,
        "exec",
    ]
    add_codex_model_options(
        command,
        model=model,
        reasoning_effort=reasoning_effort,
    )
    command.extend(
        [
            "-C",
            str(root),
            "--sandbox",
            sandbox,
            "-",
        ]
    )

    try:
        run_kwargs = {
            "cwd": root,
            "input": prompt,
            "text": True,
            "encoding": "utf-8",
        }
        if capture_output:
            run_kwargs["capture_output"] = True

        completed = run_command(
            command,
            **run_kwargs,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "Codex CLI が見つかりません。PowerShellでは codex ではなく codex.cmd を使ってください。"
        ) from exc

    if completed.returncode != 0:
        detail = _completed_process_output(completed) if capture_output else ""
        suffix = f"\n{detail}" if detail else ""
        raise RuntimeError(f"Codex CLI が失敗しました。exit code: {completed.returncode}{suffix}")

    return CodexRunResult(command=tuple(command), returncode=completed.returncode)


def _completed_process_output(completed) -> str:
    parts = []
    for label in ("stderr", "stdout"):
        value = getattr(completed, label, "")
        if value:
            text = str(value).strip()
            if text:
                parts.append(f"{label}:\n{text[-2000:]}")

    return "\n".join(parts)


def commit_changes(
    root: Path | str,
    message: str,
    paths: tuple[str, ...] = DEFAULT_COMMIT_PATHS,
    run_command=subprocess.run,
) -> GitCommitResult:
    root = Path(root)

    add_result = run_command(
        ["git", "add", "--", *paths],
        cwd=root,
        text=True,
        encoding="utf-8",
    )
    if add_result.returncode != 0:
        raise RuntimeError(f"git add が失敗しました。exit code: {add_result.returncode}")

    privacy_result = run_command(
        [sys.executable, "scripts/privacy_check.py", "--staged"],
        cwd=root,
        text=True,
        encoding="utf-8",
    )
    if privacy_result.returncode != 0:
        raise RuntimeError("個人情報・秘密情報チェックが失敗しました。commitを中止します。")

    diff_result = run_command(
        ["git", "diff", "--cached", "--quiet"],
        cwd=root,
        text=True,
        encoding="utf-8",
    )
    if diff_result.returncode == 0:
        return GitCommitResult(committed=False, message=message)
    if diff_result.returncode != 1:
        raise RuntimeError(f"git diff --cached が失敗しました。exit code: {diff_result.returncode}")

    commit_result = run_command(
        ["git", "commit", "-m", message],
        cwd=root,
        text=True,
        encoding="utf-8",
    )
    if commit_result.returncode != 0:
        raise RuntimeError(f"git commit が失敗しました。exit code: {commit_result.returncode}")

    return GitCommitResult(committed=True, message=message)


def process_chat_session(
    root: Path | str = ROOT,
    imported_at: datetime | None = None,
    date_text: str | None = None,
    keep_inbox: bool = False,
    run_codex: bool = False,
    commit: bool = False,
    codex_command: str = "codex.cmd",
    codex_sandbox: str = "workspace-write",
    codex_approval: str = "never",
    codex_model: str | None = DEFAULT_MEMORY_CODEX_MODEL,
    codex_reasoning_effort: str | None = DEFAULT_MEMORY_CODEX_REASONING_EFFORT,
    run_command=subprocess.run,
) -> ProcessChatSessionResult:
    root = Path(root)
    chat_result = process_chat(
        root=root,
        imported_at=imported_at,
        date_text=date_text,
        keep_inbox=keep_inbox,
    )

    codex_result = None
    if run_codex:
        prepare_memory_targets(root=root, target_at=chat_result.imported_at)
        codex_result = run_codex_task(
            root=root,
            prompt=chat_result.prompt,
            codex_command=codex_command,
            sandbox=codex_sandbox,
            approval=codex_approval,
            model=codex_model,
            reasoning_effort=codex_reasoning_effort,
            run_command=run_command,
        )
        rebuild_index(root=root)

    git_result = None
    if commit:
        commit_date = chat_result.imported_at.strftime("%Y-%m-%d")
        git_result = commit_changes(
            root=root,
            message=f"Process chat session {commit_date}",
            run_command=run_command,
        )

    return ProcessChatSessionResult(chat=chat_result, codex=codex_result, git=git_result)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Save inbox/chat.txt as a raw AI-LifeOS chat log.")
    parser.add_argument("--date", help="保存日付を YYYY-MM-DD 形式で指定する")
    parser.add_argument("--keep-inbox", action="store_true", help="保存後も inbox/chat.txt を空にしない")
    parser.add_argument("--run-codex", action="store_true", help="保存後に Codex CLI を非対話で実行する")
    parser.add_argument(
        "--commit",
        action="store_true",
        help="保存・Codex実行後、公開用プロジェクトファイルだけをGit commitする",
    )
    parser.add_argument("--codex-command", default="codex.cmd", help="実行するCodex CLIコマンド")
    parser.add_argument("--codex-model", default=DEFAULT_MEMORY_CODEX_MODEL, help="記憶整理に使うCodexモデル")
    parser.add_argument(
        "--codex-reasoning-effort",
        default=DEFAULT_MEMORY_CODEX_REASONING_EFFORT,
        choices=("minimal", "low", "medium", "high", "xhigh"),
        help="記憶整理に使うCodex reasoning effort",
    )
    parser.add_argument(
        "--codex-sandbox",
        default="workspace-write",
        choices=("read-only", "workspace-write", "danger-full-access"),
        help="Codex execのサンドボックス設定",
    )
    parser.add_argument(
        "--codex-approval",
        default="never",
        choices=("untrusted", "on-request", "never"),
        help="Codex execの承認ポリシー",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    try:
        result = process_chat_session(
            date_text=args.date,
            keep_inbox=args.keep_inbox,
            run_codex=args.run_codex,
            commit=args.commit,
            codex_command=args.codex_command,
            codex_sandbox=args.codex_sandbox,
            codex_approval=args.codex_approval,
            codex_model=args.codex_model,
            codex_reasoning_effort=args.codex_reasoning_effort,
        )
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 1

    print("raw.md を保存したぞ。")
    print(result.chat.raw_file)

    print("\nCodex用タスクも作ったぞ。")
    print(result.chat.task_file)

    if result.codex:
        print("\nCodex CLIで記憶整理まで実行したぞ。")
        print(" ".join(result.codex.command))
    else:
        print("\n次はCodex CLIでこの内容を貼れ。")
        print("----- ここから -----")
        print(result.chat.prompt)
        print("----- ここまで -----")

    if result.git:
        if result.git.committed:
            print(f"\n公開用プロジェクトファイルをGit commitしたぞ: {result.git.message}")
        else:
            print("\n公開用Git commit対象の変更はなかったぞ。")
    else:
        print("\nGit commit はしていないぞ。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
