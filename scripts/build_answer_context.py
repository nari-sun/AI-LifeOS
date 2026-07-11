import argparse
from dataclasses import dataclass
from pathlib import Path

from memory_index import ROOT, MemorySearchResult, search_memory
from memory_items import load_categories


MEMORY_SCORE_THRESHOLD = 3


@dataclass(frozen=True)
class MemoryNeedSignal:
    text: str
    score: int
    reason: str


MEMORY_NEED_SIGNALS = (
    MemoryNeedSignal("覚えて", 4, "explicit-memory"),
    MemoryNeedSignal("記憶", 4, "explicit-memory"),
    MemoryNeedSignal("memory context", 4, "explicit-memory"),
    MemoryNeedSignal("前回", 4, "past-conversation"),
    MemoryNeedSignal("前に", 3, "past-conversation"),
    MemoryNeedSignal("以前", 3, "past-conversation"),
    MemoryNeedSignal("過去", 3, "past-conversation"),
    MemoryNeedSignal("昔", 2, "past-conversation"),
    MemoryNeedSignal("話した", 3, "past-conversation"),
    MemoryNeedSignal("決めた", 3, "past-decision"),
    MemoryNeedSignal("ログ", 3, "past-conversation"),
    MemoryNeedSignal("履歴", 3, "past-conversation"),
    MemoryNeedSignal("俺", 2, "self-reference"),
    MemoryNeedSignal("私", 2, "self-reference"),
    MemoryNeedSignal("僕", 2, "self-reference"),
    MemoryNeedSignal("自分", 2, "self-reference"),
    MemoryNeedSignal("わたし", 2, "self-reference"),
    MemoryNeedSignal("好み", 3, "preference"),
    MemoryNeedSignal("好き", 2, "preference"),
    MemoryNeedSignal("嫌い", 2, "preference"),
    MemoryNeedSignal("生活", 2, "personal-topic"),
    MemoryNeedSignal("学習", 2, "personal-topic"),
    MemoryNeedSignal("やりたいこと", 4, "structured-memory"),
    MemoryNeedSignal("候補タスク", 4, "structured-memory"),
    MemoryNeedSignal("家の状況", 4, "structured-memory"),
    MemoryNeedSignal("学習状況", 4, "structured-memory"),
    MemoryNeedSignal("進捗", 2, "personal-topic"),
    MemoryNeedSignal("日記", 3, "personal-record"),
    MemoryNeedSignal("習慣", 2, "personal-topic"),
    MemoryNeedSignal("ルーティン", 2, "personal-topic"),
    MemoryNeedSignal("AI-LifeOS", 4, "project"),
    MemoryNeedSignal("プロジェクト", 2, "project"),
    MemoryNeedSignal("Phase", 2, "project"),
    MemoryNeedSignal("フェーズ", 2, "project"),
    MemoryNeedSignal("方針", 2, "project-decision"),
    MemoryNeedSignal("会話", 2, "conversation"),
    MemoryNeedSignal("journal", 2, "project-memory"),
    MemoryNeedSignal("memory", 2, "project-memory"),
    MemoryNeedSignal("この辺", 1, "local-context"),
    MemoryNeedSignal("近く", 1, "local-context"),
    MemoryNeedSignal("ご飯", 1, "personal-topic"),
    MemoryNeedSignal("店", 1, "personal-topic"),
    MemoryNeedSignal("おすすめ", 1, "recommendation"),
)

SELF_REFERENCES = ("俺", "私", "僕", "自分", "わたし")
PERSONAL_TOPIC_SIGNALS = (
    "好み",
    "好き",
    "嫌い",
    "生活",
    "学習",
    "進捗",
    "日記",
    "習慣",
    "ルーティン",
    "ご飯",
    "店",
    "おすすめ",
)
PAST_SIGNALS = ("前回", "前に", "以前", "過去", "昔", "話した", "決めた", "ログ", "履歴")
PROJECT_SIGNALS = ("AI-LifeOS", "プロジェクト", "Phase", "フェーズ", "方針", "journal", "memory")

CATEGORY_QUERY_ALIASES = {
    "future_wishlist": ("やりたいこと", "いつか", "将来", "wishlist"),
    "candidate_task": ("候補タスク", "作業候補"),
    "home_status": ("家の状況", "住居", "家について", "home"),
    "study_status": ("学習状況", "勉強の状況", "資格の学習", "安全確保支援士", "study"),
    "project_status": ("プロジェクト状況", "プロジェクトの進捗", "project status"),
    "preference": ("好み", "判断基準", "回答スタイル", "preference"),
}


@dataclass(frozen=True)
class MemoryNeedAssessment:
    should_use_memory: bool
    score: int
    threshold: int
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class MemoryContextReference:
    path: str
    document_type: str
    title: str
    date: str | None
    snippet: str
    score: int


@dataclass(frozen=True)
class AnswerContext:
    should_use_memory: bool
    text: str
    results: tuple[MemorySearchResult, ...]
    references: tuple[MemoryContextReference, ...] = ()
    score: int = 0
    threshold: int = MEMORY_SCORE_THRESHOLD
    reasons: tuple[str, ...] = ()

    @property
    def used_memory(self) -> bool:
        return bool(self.text.strip() and self.references)


def should_use_memory(question: str) -> bool:
    return assess_memory_need(question).should_use_memory


def assess_memory_need(question: str) -> MemoryNeedAssessment:
    normalized = question.strip()
    if not normalized:
        return MemoryNeedAssessment(
            should_use_memory=False,
            score=0,
            threshold=MEMORY_SCORE_THRESHOLD,
            reasons=(),
        )

    lowered = normalized.lower()
    score = 0
    reasons: list[str] = []
    for signal in MEMORY_NEED_SIGNALS:
        if signal.text.lower() in lowered:
            score += signal.score
            reasons.append(signal.reason)

    if _has_any(normalized, SELF_REFERENCES) and _has_any(normalized, PERSONAL_TOPIC_SIGNALS):
        score += 2
        reasons.append("self-plus-personal-topic")

    if _has_any(normalized, PAST_SIGNALS) and _has_any(normalized, PROJECT_SIGNALS):
        score += 2
        reasons.append("past-plus-project")

    reasons_tuple = tuple(_dedupe(reasons))
    return MemoryNeedAssessment(
        should_use_memory=score >= MEMORY_SCORE_THRESHOLD,
        score=score,
        threshold=MEMORY_SCORE_THRESHOLD,
        reasons=reasons_tuple,
    )


def build_answer_context(
    root: Path | str = ROOT,
    question: str = "",
    max_memory_chars: int = 3000,
    max_results: int = 5,
    use_index: bool = True,
) -> AnswerContext:
    root = Path(root)
    assessment = assess_memory_need(question)
    if not assessment.should_use_memory:
        return AnswerContext(
            should_use_memory=False,
            text="",
            results=(),
            score=assessment.score,
            threshold=assessment.threshold,
            reasons=assessment.reasons,
        )

    memory_sections, priority_references = _read_priority_memory(root=root, max_chars=max_memory_chars)
    inferred_categories = infer_memory_categories(root, question)
    structured_results = _search_structured_memory(
        root=root,
        question=question,
        categories=inferred_categories,
        max_results=max_results,
        use_index=use_index,
    )
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

    if structured_results:
        lines.append("## Structured Memory Matches")
        for result in structured_results:
            lines.extend(_format_result(result, root))
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

    references = _dedupe_references(
        [
            *priority_references,
            *(_reference_from_result(result, root) for result in structured_results),
            *(_reference_from_result(result, root) for result in journal_results),
            *(_reference_from_result(result, root) for result in conversation_results),
        ]
    )
    context_text = "\n".join(lines).rstrip() if references else ""

    return AnswerContext(
        should_use_memory=True,
        text=context_text,
        results=tuple([*structured_results, *journal_results, *conversation_results]),
        references=tuple(references),
        score=assessment.score,
        threshold=assessment.threshold,
        reasons=assessment.reasons,
    )


def _read_priority_memory(root: Path, max_chars: int) -> tuple[list[str], list[MemoryContextReference]]:
    sections: list[str] = []
    references: list[MemoryContextReference] = []
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
        references.append(
            MemoryContextReference(
                path=relative.as_posix(),
                document_type="memory",
                title=path.stem,
                date=None,
                snippet=_short_snippet(content),
                score=0,
            )
        )
        remaining -= len(content)
        if remaining <= 0:
            break
    return sections, references


def _display_path(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def _format_result(result: MemorySearchResult, root: Path) -> list[str]:
    lines = [
        f"- Date: {result.date or 'unknown'}",
        f"  Type: {result.document_type}",
        f"  Source: {_display_path(result.path, root)}",
        f"  Snippet: {result.snippet}",
    ]
    if result.category:
        lines.insert(2, f"  Category: {result.category} ({result.category_label or result.category})")
    if result.status:
        lines.insert(3, f"  Status: {result.status}")
    if result.source:
        lines.append(f"  Evidence: {result.source}")
    return lines


def infer_memory_categories(root: Path | str, question: str) -> tuple[str, ...]:
    normalized = question.strip().lower()
    if not normalized:
        return ()
    categories = load_categories(root)
    matched: list[str] = []
    for category in categories:
        signals = [category.name, category.label, *CATEGORY_QUERY_ALIASES.get(category.name, ())]
        if any(signal.lower() in normalized for signal in signals if signal):
            matched.append(category.name)
    return tuple(_dedupe(matched))


def _search_structured_memory(
    root: Path,
    question: str,
    categories: tuple[str, ...],
    max_results: int,
    use_index: bool,
) -> list[MemorySearchResult]:
    results: list[MemorySearchResult] = []
    for category in categories:
        results.extend(
            search_memory(
                root=root,
                query="",
                limit=max_results,
                document_types=("memory_item",),
                category=category,
                use_index=use_index,
            )
        )
    if not categories:
        results.extend(
            search_memory(
                root=root,
                query=question,
                limit=max_results,
                document_types=("memory_item",),
                use_index=use_index,
            )
        )

    deduped: list[MemorySearchResult] = []
    seen: set[str] = set()
    for result in results:
        key = str(result.path.resolve())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(result)
    return deduped[: max(max_results, 1)]


def _reference_from_result(result: MemorySearchResult, root: Path) -> MemoryContextReference:
    return MemoryContextReference(
        path=_display_path(result.path, root),
        document_type=result.document_type,
        title=result.title,
        date=result.date,
        snippet=result.snippet,
        score=result.score,
    )


def _dedupe_references(references: list[MemoryContextReference]) -> list[MemoryContextReference]:
    result: list[MemoryContextReference] = []
    seen: set[str] = set()
    for reference in references:
        if reference.path in seen:
            continue
        seen.add(reference.path)
        result.append(reference)
    return result


def _short_snippet(content: str, width: int = 120) -> str:
    text = " ".join(line.strip() for line in content.splitlines() if line.strip())
    if len(text) <= width:
        return text
    return text[:width].rstrip() + "..."


def _has_any(text: str, values: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(value.lower() in lowered for value in values)


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


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
        print("")
        print(
            f"Memory context used: yes ({len(context.references)} sources, "
            f"score {context.score}/{context.threshold})"
        )
        for reference in context.references:
            print(f"- {reference.path}")
    else:
        print(f"No memory context needed. score={context.score}/{context.threshold}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
