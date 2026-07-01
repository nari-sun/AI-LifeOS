import argparse
import shutil
import sys
import textwrap
from pathlib import Path

from live_session import ROOT, LiveMessage, LiveSession, create_live_message, create_live_session
from session_store import ResumeSession, list_resumable_sessions, load_resume_session


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Start an AI-LifeOS live conversation session.")
    parser.add_argument("--root", default=ROOT, help="AI-LifeOSのルートディレクトリ")
    parser.add_argument(
        "--resume",
        nargs="?",
        const="latest",
        help="指定したセッションID、または未指定なら最新の再開可能セッションをロードする",
    )
    parser.add_argument("--resume-days", type=int, default=10, help="最後のuser入力から何日以内を再開対象にするか")
    return parser


def _display_path(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _terminal_size() -> tuple[int, int]:
    size = shutil.get_terminal_size(fallback=(100, 30))
    return max(size.columns, 40), max(size.lines, 16)


def _clear_screen() -> None:
    print("\033[2J\033[H", end="")


def _wrap_message(role: str, content: str, width: int) -> list[str]:
    label = "You" if role == "user" else "Assistant"
    prefix = f"{label}: "
    indent = " " * len(prefix)
    wrapped_lines: list[str] = []

    for raw_line in content.splitlines() or [""]:
        available_width = max(width - len(prefix), 10)
        parts = textwrap.wrap(
            raw_line,
            width=available_width,
            break_long_words=True,
            replace_whitespace=False,
        ) or [""]

        for index, part in enumerate(parts):
            wrapped_lines.append(f"{prefix if index == 0 else indent}{part}")

    return wrapped_lines


def _render_screen(
    messages: list[LiveMessage],
    session_display_path: str,
    status: str = "",
) -> None:
    width, height = _terminal_size()
    rule = "-" * width
    header = [
        "AI-LifeOS Phase2.6 - Codex Conversation MVP",
        f"Log: {session_display_path}",
        "Enter: send    /resume: list    /resume <id|latest>: load    /exit: quit",
        rule,
    ]
    footer = [
        rule,
        *status.splitlines(),
    ]
    message_height = max(height - len(header) - len(footer) - 1, 4)

    message_lines: list[str] = []
    if messages:
        for message in messages:
            message_lines.extend(_wrap_message(message.role, message.content, width))
            message_lines.append("")
    else:
        message_lines.append("(まだメッセージはありません)")

    visible_messages = message_lines[-message_height:]
    blank_count = max(message_height - len(visible_messages), 0)

    _clear_screen()
    for line in header:
        print(line[:width])

    for _ in range(blank_count):
        print()

    for line in visible_messages:
        print(line[:width])

    for line in footer:
        print(line[:width])


def _load_resume_messages(root: Path, session_ref: str, retention_days: int) -> tuple[LiveSession, list[LiveMessage]]:
    summary, records = load_resume_session(root=root, session_ref=session_ref, retention_days=retention_days)
    messages = [
        LiveMessage(
            role=record["role"],
            content=record["content"],
            timestamp=record["timestamp"],
        )
        for record in records
    ]

    return LiveSession(path=summary.jsonl_file, started_at=summary.started_at), messages


def _format_resume_list(sessions: list[ResumeSession]) -> str:
    if not sessions:
        return "再開できるセッションはありません。"

    lines = ["再開する番号を入力してください。/cancel で中止します。"]
    for index, session in enumerate(sessions[:10], start=1):
        lines.append(
            f"{index}. {session.session_id} | {session.last_user_at.isoformat(timespec='seconds')} | "
            f"{session.message_count} messages | {session.title}"
        )

    return "\n".join(lines)


def _resume_candidates(root: Path, retention_days: int) -> list[ResumeSession]:
    sessions = list_resumable_sessions(root=root, retention_days=retention_days)
    return sessions[:10]


def _cursor_selection_available() -> bool:
    if not sys.stdin.isatty():
        return False

    try:
        import msvcrt  # noqa: F401
    except ImportError:
        return False

    return True


def _render_resume_menu(candidates: list[ResumeSession], selected_index: int) -> None:
    width, _ = _terminal_size()
    rule = "-" * width

    _clear_screen()
    print("AI-LifeOS Resume")
    print("Up/Down: move    Enter: resume    Esc/q: cancel")
    print(rule)

    for index, session in enumerate(candidates):
        marker = ">" if index == selected_index else " "
        line = (
            f"{marker} {index + 1}. {session.session_id} | "
            f"{session.last_user_at.isoformat(timespec='seconds')} | "
            f"{session.message_count} messages | {session.title}"
        )
        print(line[:width])


def _read_resume_menu_key() -> str:
    import msvcrt

    key = msvcrt.getwch()

    if key == "\x03":
        raise KeyboardInterrupt
    if key in ("\x00", "\xe0"):
        key = msvcrt.getwch()
        if key == "H":
            return "up"
        if key == "P":
            return "down"
        return "unknown"
    if key in ("\r", "\n"):
        return "enter"
    if key == "\x1b" or key.lower() == "q":
        return "cancel"
    if key.isdigit():
        return f"digit:{key}"

    return "unknown"


def _select_resume_candidate_with_cursor(candidates: list[ResumeSession]) -> ResumeSession | None:
    selected_index = 0

    while True:
        _render_resume_menu(candidates, selected_index)
        action = _read_resume_menu_key()

        if action == "up":
            selected_index = (selected_index - 1) % len(candidates)
            continue
        if action == "down":
            selected_index = (selected_index + 1) % len(candidates)
            continue
        if action == "enter":
            return candidates[selected_index]
        if action == "cancel":
            return None
        if action.startswith("digit:"):
            digit = int(action.split(":", maxsplit=1)[1])
            if 1 <= digit <= min(len(candidates), 9):
                return candidates[digit - 1]


def main() -> int:
    args = build_parser().parse_args()
    root = Path(args.root)

    try:
        if args.resume:
            session, messages = _load_resume_messages(root=root, session_ref=args.resume, retention_days=args.resume_days)
            status = f"セッションを再開しました: {session.path.name}"
        else:
            session = create_live_session(root=root)
            messages: list[LiveMessage] = []
            status = "起動成功"
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 1

    session_display_path = _display_path(session.path, root)
    saved = False
    resume_candidates: list[ResumeSession] = []

    _render_screen(messages, session_display_path, status)

    try:
        while True:
            message = input("You > ")

            if message.strip().lower() == "/exit":
                if messages:
                    session.write_messages(messages)
                    saved = True
                    status = f"{len(messages)}件のメッセージを保存して終了します。"
                else:
                    status = "保存するメッセージはありません。終了します。"
                _render_screen(messages, session_display_path, status)
                break

            if message.strip().lower() == "/resume":
                resume_candidates = _resume_candidates(root=root, retention_days=args.resume_days)
                if not resume_candidates:
                    status = "再開できるセッションはありません。"
                    _render_screen(messages, session_display_path, status)
                    continue

                if _cursor_selection_available():
                    selected = _select_resume_candidate_with_cursor(resume_candidates)
                    if selected is None:
                        resume_candidates = []
                        status = "セッション再開を中止しました。"
                        _render_screen(messages, session_display_path, status)
                        continue

                    if messages:
                        session.write_messages(messages)

                    try:
                        session, messages = _load_resume_messages(
                            root=root,
                            session_ref=selected.session_id,
                            retention_days=args.resume_days,
                        )
                        session_display_path = _display_path(session.path, root)
                        status = f"セッションを再開しました: {session.path.name}"
                    except (FileNotFoundError, ValueError) as exc:
                        status = f"再開できません: {exc}"
                    resume_candidates = []
                    _render_screen(messages, session_display_path, status)
                    continue

                status = _format_resume_list(resume_candidates)
                _render_screen(messages, session_display_path, status)
                continue

            if resume_candidates and message.strip().lower() == "/cancel":
                resume_candidates = []
                status = "セッション再開を中止しました。"
                _render_screen(messages, session_display_path, status)
                continue

            if resume_candidates and message.strip().isdigit():
                selected_index = int(message.strip()) - 1
                if not 0 <= selected_index < len(resume_candidates):
                    status = f"番号は1から{len(resume_candidates)}の範囲で入力してください。/cancel で中止できます。"
                    _render_screen(messages, session_display_path, status)
                    continue

                if messages:
                    session.write_messages(messages)

                selected = resume_candidates[selected_index]
                try:
                    session, messages = _load_resume_messages(
                        root=root,
                        session_ref=selected.session_id,
                        retention_days=args.resume_days,
                    )
                    session_display_path = _display_path(session.path, root)
                    status = f"セッションを再開しました: {session.path.name}"
                    resume_candidates = []
                except (FileNotFoundError, ValueError) as exc:
                    status = f"再開できません: {exc}"
                _render_screen(messages, session_display_path, status)
                continue

            if message.strip().lower().startswith("/resume "):
                session_ref = message.strip().split(maxsplit=1)[1]
                if messages:
                    session.write_messages(messages)
                try:
                    session, messages = _load_resume_messages(
                        root=root,
                        session_ref=session_ref,
                        retention_days=args.resume_days,
                    )
                    session_display_path = _display_path(session.path, root)
                    status = f"セッションを再開しました: {session.path.name}"
                    resume_candidates = []
                except (FileNotFoundError, ValueError) as exc:
                    status = f"再開できません: {exc}"
                _render_screen(messages, session_display_path, status)
                continue

            if resume_candidates:
                status = f"再開する番号を1から{len(resume_candidates)}の範囲で入力してください。/cancel で中止できます。"
                _render_screen(messages, session_display_path, status)
                continue

            if not message.strip():
                status = "空のメッセージは保存しません。"
                _render_screen(messages, session_display_path, status)
                continue

            messages.append(create_live_message("user", message))
            resume_candidates = []
            status = "AI返答の接続は次のステップです。/exit でまとめて保存して終了します。"
            _render_screen(messages, session_display_path, status)
    except (KeyboardInterrupt, EOFError):
        if messages:
            session.write_messages(messages)
            saved = True
            status = f"{len(messages)}件のメッセージを保存して終了します。"
        else:
            status = "保存するメッセージはありません。終了します。"
        _render_screen(messages, session_display_path, status)

    if saved:
        print(f"ログ: {session_display_path}")
    else:
        print("ログファイルは作成していません。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
