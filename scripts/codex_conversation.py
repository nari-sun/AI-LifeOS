import argparse
import shutil
import textwrap
from pathlib import Path

from live_session import ROOT, LiveMessage, create_live_message, create_live_session


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Start an AI-LifeOS live conversation session.")
    parser.add_argument("--root", default=ROOT, help="AI-LifeOSのルートディレクトリ")
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
        "Enter: send    /exit: quit    Ctrl+C: quit",
        rule,
    ]
    footer = [
        rule,
        status,
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


def main() -> int:
    args = build_parser().parse_args()
    root = Path(args.root)
    session = create_live_session(root=root)
    session_display_path = _display_path(session.path, root)
    messages: list[LiveMessage] = []
    status = "起動成功"
    saved = False

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

            if not message.strip():
                status = "空のメッセージは保存しません。"
                _render_screen(messages, session_display_path, status)
                continue

            messages.append(create_live_message("user", message))
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
