import argparse
from dataclasses import dataclass
from pathlib import Path

from memory_index import ROOT, MemorySearchResult, search_memory


PRIVATE_KEYWORDS = (
    "俺",
    "私",
    "僕",
    "自分",
    "好み",
    "好き",
    "嫌い",
    "生活",
    "学習",
    "進捗",
    "日記",
    "前に",
    "以前",
    "過去",
    "昔",
    "話した",
    "決めた",
    "方針",
    "記憶",
    "ログ",
    "会話",
    "プロジェクト",
    "AI-LifeOS",
    "Phase",
    "memory",
    "journal",
    "覚えて",
    "この辺",
    "近く",
    "ご飯",
    "店",
    "おすすめ",
)


@dataclass(frozen=True)
class AnswerContext:
    should_use_memory: bool
    text: str
    results: tuple[MemorySearchResult, ...]


def should_use_memory(question: str) -> bool:
    normalized = question.strip()
    if not normalized:
        return False
    return any(keyword in normalized for keyword in PRIVATE_KEYWORDS)


def build_answer_context(
    root: Path | str = ROOT,
    question: str = "",
    max_memory_chars: int = 3000,
    max_results: int = 5,
    use_index: bool = True,
) -> AnswerContext:
    root = Path(root)
    if not should_use_memory(question):
        return AnswerContext(should_use_memory=False, text="", results=())

    memory_sections = _read_priority_memory(root=root, max_chars=max_memory_chars)
    journal_results = search_memory(
        root=root,
        query=question,
        limit=max_results,
        document_types=("journal",),
        use_index=use_index,
    )
    conversation_results = search_memory(
        root=root,
        query=question,
        limit=max_results,
        document_types=("summary", "raw"),
        use_index=use_index,
    )

    lines = [
        "AI-LifeOS memory context (read-only).",
        "Use this context only when it is relevant to the latest user message.",
        "Do not edit memory, journal, conversations, or any files while answering.",
        "If local memory is insufficient and current or external information is needed, web search is allowed.",
        "Normally blend source dates into natural language; show file paths only if the user asks for details.",
        "",
    ]

    if memory_sections:
        lines.append("## Priority Memory")
        lines.extend(memory_sections)
        lines.append("")

    if journal_results:
        lines.append("## Journal Matches")
        for result in journal_results:
            lines.extend(_format_result(result, root))
        lines.append("")

    if conversation_results:
        lines.append("## Conversation Matches")
        for result in conversation_results:
            lines.extend(_format_result(result, root))
        lines.append("")

    return AnswerContext(
        should_use_memory=True,
        text="\n".join(lines).rstrip(),
        results=tuple([*journal_results, *conversation_results]),
    )


def _read_priority_memory(root: Path, max_chars: int) -> list[str]:
    sections: list[str] = []
    remaining = max(max_chars, 1)
    for relative in (Path("memory") / "long_term.md", Path("memory") / "preferences.md"):
        path = root / relative
        if not path.exists():
            continue
        content = path.read_text(encoding="utf-8").strip()
        if not content:
            continue
        if len(content) > remaining:
            content = content[:remaining].rstrip() + "\n...[truncated]"
        sections.extend([f"### {relative.as_posix()}", content, ""])
        remaining -= len(content)
        if remaining <= 0:
            break
    return sections


def _display_path(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def _format_result(result: MemorySearchResult, root: Path) -> list[str]:
    return [
        f"- Date: {result.date or 'unknown'}",
        f"  Type: {result.document_type}",
        f"  Source: {_display_path(result.path, root)}",
        f"  Snippet: {result.snippet}",
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build read-only memory context for an AI-LifeOS answer.")
    parser.add_argument("question", help="Latest user question.")
    parser.add_argument("--root", default=ROOT, help="AI-LifeOS root directory.")
    parser.add_argument("--max-results", type=int, default=5, help="Maximum journal search results.")
    parser.add_argument("--max-memory-chars", type=int, default=3000, help="Maximum priority memory characters.")
    parser.add_argument("--no-index", action="store_true", help="Search Markdown files directly.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    context = build_answer_context(
        root=args.root,
        question=args.question,
        max_results=args.max_results,
        max_memory_chars=args.max_memory_chars,
        use_index=not args.no_index,
    )
    if context.text:
        print(context.text)
    else:
        print("No memory context needed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
