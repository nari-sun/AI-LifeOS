import argparse
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from memory_index import (
    ROOT,
    IndexHealth,
    MemorySearchResult,
    expand_query_variants,
    inspect_index_health,
    search_memory,
)
from memory_items import load_categories
from retrieval_feedback import classify_retrieval_features, feedback_bonus


DEEP_SEARCH_SCORE_THRESHOLD = 3
# Backward-compatible display field for existing CLI/GUI consumers.  This is
# no longer an on/off gate; it is the boundary between narrow and deep search.
MEMORY_SCORE_THRESHOLD = DEEP_SEARCH_SCORE_THRESHOLD
CORE_MEMORY_CHAR_LIMIT = 1000
RAW_EVIDENCE_LIMIT = 2
NARROW_SEARCH_RESULT_LIMIT = 2
TOKYO_TIMEZONE = ZoneInfo("Asia/Tokyo")

USER_EVIDENCE_SIGNALS = (
    "私が",
    "俺が",
    "僕が",
    "自分が",
    "私の発言",
    "俺の発言",
    "僕の発言",
    "何と言った",
    "what did i say",
    "my message",
)

ASSISTANT_EVIDENCE_SIGNALS = (
    "AIの回答",
    "AIの応答",
    "ChatGPTの回答",
    "ChatGPTの応答",
    "Codexの回答",
    "Codexの応答",
    "assistantの回答",
    "assistantの応答",
    "結論",
    "提案",
    "説明",
    "答え",
    "回答",
    "what did ai",
    "assistant response",
    "conclusion",
)


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
    MemoryNeedSignal("なんだっけ", 3, "past-conversation"),
    MemoryNeedSignal("俺", 2, "self-reference"),
    MemoryNeedSignal("おれ", 2, "self-reference"),
    MemoryNeedSignal("オレ", 2, "self-reference"),
    MemoryNeedSignal("私", 2, "self-reference"),
    MemoryNeedSignal("わたし", 2, "self-reference"),
    MemoryNeedSignal("僕", 2, "self-reference"),
    MemoryNeedSignal("ぼく", 2, "self-reference"),
    MemoryNeedSignal("自分", 2, "self-reference"),
    MemoryNeedSignal("好み", 3, "preference"),
    MemoryNeedSignal("好き", 2, "preference"),
    MemoryNeedSignal("嫌い", 2, "preference"),
    MemoryNeedSignal("感想", 2, "personal-topic"),
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
    MemoryNeedSignal("スマホ", 1, "personal-topic"),
    MemoryNeedSignal("携帯", 1, "personal-topic"),
    MemoryNeedSignal("端末", 1, "personal-topic"),
    MemoryNeedSignal("PC", 1, "personal-topic"),
    MemoryNeedSignal("パソコン", 1, "personal-topic"),
    MemoryNeedSignal("おすすめ", 1, "recommendation"),
)

SELF_REFERENCES = ("俺", "おれ", "オレ", "私", "わたし", "僕", "ぼく", "自分")
PERSONAL_TOPIC_SIGNALS = (
    "好み",
    "好き",
    "嫌い",
    "感想",
    "生活",
    "学習",
    "進捗",
    "日記",
    "習慣",
    "ルーティン",
    "ご飯",
    "店",
    "スマホ",
    "携帯",
    "端末",
    "PC",
    "パソコン",
    "おすすめ",
)
PAST_SIGNALS = ("前回", "前に", "以前", "過去", "昔", "話した", "決めた", "ログ", "履歴", "なんだっけ")
PROJECT_SIGNALS = ("AI-LifeOS", "プロジェクト", "Phase", "フェーズ", "方針", "journal", "memory")
FOLLOW_UP_SIGNALS = ("じゃあ", "それで", "ちなみに", "それ", "前者", "後者")
GENERIC_PAST_TERMS = ("前回", "前に", "以前", "過去", "昔", "話した", "決めた", "ログ", "履歴", "なんだっけ", "何て", "聞いた", "答えた")
REQUEST_TERMS = (
    "教えてください",
    "教えて",
    "聞かせて",
    "知りたい",
    "なんだっけ",
    "覚えてる",
    "覚えている",
)
FAILED_RETRIEVAL_SIGNALS = (
    "具体的な感想は確認できない",
    "具体的な感想を確認できない",
    "記録上では確認できない",
    "過去の会話を確認できない",
    "見つけられませんでした",
    "results=0",
    "raw_chunks=0",
)

RAW_MESSAGE_PATTERN = re.compile(
    r"^## (?P<role>User|Assistant)[ \t]*\n"
    r"(?:[ \t]*\n)*"
    r"(?:Timestamp:[ \t]*(?P<timestamp>[^\n]+)\n(?:[ \t]*\n)*)?"
    r"(?P<content>.*?)(?=^## (?:User|Assistant)[ \t]*$|\Z)",
    re.MULTILINE | re.DOTALL,
)

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
    speaker_role: str | None = None
    message_number: int | None = None


@dataclass(frozen=True)
class RetrievalHealth:
    """Explain which independent personalization layers contributed."""

    index_status: str = "disabled"
    index_reasons: tuple[str, ...] = ()
    markdown_fallback_used: bool = False
    retrieval_depth: str = "none"
    query_variants: tuple[str, ...] = ()
    core_enabled: bool = True
    past_chats_enabled: bool = True
    core_reference_count: int = 0
    structured_memory_hit_count: int = 0
    past_chat_hit_count: int = 0
    project_scope: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "index_status": self.index_status,
            "index_reasons": list(self.index_reasons),
            "markdown_fallback_used": self.markdown_fallback_used,
            "retrieval_depth": self.retrieval_depth,
            "query_variants": list(self.query_variants),
            "core_enabled": self.core_enabled,
            "past_chats_enabled": self.past_chats_enabled,
            "core_reference_count": self.core_reference_count,
            "structured_memory_hit_count": self.structured_memory_hit_count,
            "past_chat_hit_count": self.past_chat_hit_count,
            "project_scope": self.project_scope,
        }


@dataclass(frozen=True)
class AnswerContext:
    should_use_memory: bool
    text: str
    results: tuple[MemorySearchResult, ...]
    references: tuple[MemoryContextReference, ...] = ()
    score: int = 0
    threshold: int = MEMORY_SCORE_THRESHOLD
    reasons: tuple[str, ...] = ()
    retrieval_modes: tuple[str, ...] = ()
    retrieval_health: RetrievalHealth = field(default_factory=RetrievalHealth)

    @property
    def used_memory(self) -> bool:
        return bool(self.text.strip() and self.references)


def should_use_memory(question: str, recent_user_messages: tuple[str, ...] = ()) -> bool:
    return assess_memory_need(question, recent_user_messages=recent_user_messages).should_use_memory


def assess_memory_need(
    question: str,
    recent_user_messages: tuple[str, ...] = (),
    learned_bonus: int = 0,
    learned_reasons: tuple[str, ...] = (),
) -> MemoryNeedAssessment:
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

    previous_messages = _recent_distinct_messages(question, recent_user_messages)
    previous_text = "\n".join(previous_messages)
    if _is_follow_up_question(normalized) and previous_text and _is_memory_related_text(previous_text):
        score += 2
        reasons.append("conversation-follow-up")

    if learned_bonus:
        score += max(learned_bonus, 0)
        reasons.extend(learned_reasons)

    reasons_tuple = tuple(_dedupe(reasons))
    return MemoryNeedAssessment(
        # Retrieval is no longer gated on a fixed score.  Every non-empty
        # question gets at least a narrow read-only search; the score only
        # controls how deeply past-chat evidence is explored.
        should_use_memory=True,
        score=score,
        threshold=MEMORY_SCORE_THRESHOLD,
        reasons=reasons_tuple,
    )


def build_answer_context(
    root: Path | str = ROOT,
    question: str = "",
    max_memory_chars: int = CORE_MEMORY_CHAR_LIMIT,
    max_results: int = 5,
    use_index: bool = True,
    recent_user_messages: tuple[str, ...] = (),
    *,
    include_core_memory: bool = True,
    include_past_chats: bool = True,
    project_scope: str | None = None,
    exclude_live_session: Path | str | None = None,
) -> AnswerContext:
    root = Path(root)
    history = _recent_distinct_messages(question, recent_user_messages)
    retrieval_requested = include_core_memory or include_past_chats
    learned_bonus, learned_reasons = (
        feedback_bonus(root=root, question=question)
        if retrieval_requested
        else (0, ())
    )
    assessment = assess_memory_need(
        question,
        recent_user_messages=history,
        learned_bonus=learned_bonus,
        learned_reasons=learned_reasons,
    )
    normalized_scope = _normalize_project_scope(project_scope)
    memory_sections, priority_references = (
        _read_priority_memory(
            root=root,
            max_chars=min(max(max_memory_chars, 1), CORE_MEMORY_CHAR_LIMIT),
            project_scope=normalized_scope,
        )
        if include_core_memory
        else ([], [])
    )
    has_question = bool(question.strip())
    search_enabled = retrieval_requested
    fallback = _should_use_fallback(question, history, learned_bonus)
    should_expand_search = has_question and search_enabled and (
        assessment.score >= DEEP_SEARCH_SCORE_THRESHOLD or fallback
    )
    retrieval_depth = "deep" if should_expand_search else "narrow" if has_question else "none"
    if not search_enabled:
        retrieval_depth = "none"
    index_health = (
        inspect_index_health(root)
        if use_index and has_question and (include_core_memory or include_past_chats)
        else IndexHealth(status="disabled")
    )
    retrieval_modes: list[str] = ["core"] if priority_references else []
    if has_question and search_enabled and not should_expand_search:
        retrieval_modes.append("narrow")
    if should_expand_search:
        retrieval_modes.append("fallback" if fallback else "search")

    narrow_results: list[MemorySearchResult] = []
    structured_results: list[MemorySearchResult] = []
    journal_results: list[MemorySearchResult] = []
    conversation_results: list[MemorySearchResult] = []
    raw_chunk_results: list[MemorySearchResult] = []
    live_results: list[MemorySearchResult] = []
    search_query = _search_query(question, history)
    narrow_document_types = tuple(
        [
            *(("memory_item",) if include_core_memory else ()),
            *(("journal", "summary") if include_past_chats else ()),
        ]
    )
    if has_question and not should_expand_search:
        narrow_limit = min(max(max_results, 1), NARROW_SEARCH_RESULT_LIMIT)
        if narrow_document_types:
            narrow_results = _search_scoped(
                root=root,
                query=question.strip()[:800],
                limit=narrow_limit,
                document_types=narrow_document_types,
                use_index=use_index,
                project_scope=normalized_scope,
            )

    if should_expand_search:
        if include_core_memory:
            inferred_categories = infer_memory_categories(root, search_query)
            structured_results = _search_structured_memory(
                root=root,
                question=search_query,
                categories=inferred_categories,
                max_results=max_results,
                use_index=use_index,
                project_scope=normalized_scope,
            )
        raw_chunk_candidates: list[MemorySearchResult] = []
        if include_past_chats:
            journal_results = _search_scoped(
                root=root,
                query=search_query,
                limit=max_results,
                document_types=("journal",),
                use_index=use_index,
                project_scope=normalized_scope,
            )
            conversation_results = _search_scoped(
                root=root,
                query=search_query,
                limit=max_results,
                document_types=("summary", "raw"),
                use_index=use_index,
                project_scope=normalized_scope,
            )
            raw_chunk_candidates = _search_scoped(
                root=root,
                query=search_query,
                limit=max(max_results * 4, 8),
                document_types=("raw_chunk",),
                use_index=use_index,
                project_scope=normalized_scope,
            )
        if include_past_chats and _needs_past_conversation_evidence(question):
            concrete_query = _concrete_query(question)
            if concrete_query:
                conversation_results = _merge_results(
                    _search_scoped(
                        root=root,
                        query=concrete_query,
                        limit=max_results,
                        document_types=("summary", "raw"),
                        use_index=use_index,
                        project_scope=normalized_scope,
                    ),
                    conversation_results,
                    limit=max_results,
                )
                raw_chunk_candidates = _merge_results(
                    _search_scoped(
                        root=root,
                        query=concrete_query,
                        limit=max(max_results * 4, 8),
                        document_types=("raw_chunk",),
                        use_index=use_index,
                        project_scope=normalized_scope,
                    ),
                    raw_chunk_candidates,
                    limit=max(max_results * 4, 8),
                )
                live_results = _search_unorganized_live(
                    root,
                    search_query,
                    question,
                    normalized_scope,
                    exclude_live_session=exclude_live_session,
                )
        conversation_results = _exclude_failed_retrieval_messages(conversation_results)
        raw_chunk_candidates = _exclude_failed_retrieval_messages(raw_chunk_candidates)
        raw_chunk_results = _select_role_aware_raw_evidence(
            root=root,
            candidates=raw_chunk_candidates,
            question=question,
            use_index=use_index,
            project_scope=normalized_scope,
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

    if narrow_results:
        lines.append("## Narrow Memory Matches")
        lines.append("Use at most two short, directly relevant memory excerpts for ordinary questions.")
        for result in narrow_results:
            lines.extend(_format_result(result, root))
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

    if raw_chunk_results:
        lines.append("## Raw Conversation Evidence")
        lines.append("Use these message-level excerpts as the primary evidence for past conversations.")
        for result in raw_chunk_results:
            lines.extend(_format_result(result, root))
        lines.append("")

    if live_results:
        lines.append("## Unorganized Live Conversation Evidence")
        lines.append("These excerpts are read from an unorganized live JSONL file and are not saved or modified.")
        for result in live_results:
            lines.extend(_format_result(result, root))
        lines.append("")

    references = _dedupe_references(
        [
            *priority_references,
            *(_reference_from_result(result, root) for result in narrow_results),
            *(_reference_from_result(result, root) for result in structured_results),
            *(_reference_from_result(result, root) for result in journal_results),
            *(_reference_from_result(result, root) for result in conversation_results),
            *(_reference_from_result(result, root) for result in raw_chunk_results),
            *(_reference_from_result(result, root) for result in live_results),
        ]
    )
    context_text = "\n".join(lines).rstrip() if references else ""

    return AnswerContext(
        should_use_memory=has_question,
        text=context_text,
        results=tuple(
            [
                *narrow_results,
                *structured_results,
                *journal_results,
                *conversation_results,
                *raw_chunk_results,
                *live_results,
            ]
        ),
        references=tuple(references),
        score=assessment.score,
        threshold=assessment.threshold,
        reasons=assessment.reasons,
        retrieval_modes=tuple(retrieval_modes),
        retrieval_health=RetrievalHealth(
            index_status=index_health.status,
            index_reasons=index_health.reasons,
            markdown_fallback_used=index_health.needs_markdown_fallback,
            retrieval_depth=retrieval_depth,
            query_variants=expand_query_variants(search_query),
            core_enabled=include_core_memory,
            past_chats_enabled=include_past_chats,
            core_reference_count=len(priority_references),
            structured_memory_hit_count=len(structured_results)
            + sum(result.document_type == "memory_item" for result in narrow_results),
            past_chat_hit_count=len(journal_results)
            + len(conversation_results)
            + len(raw_chunk_results)
            + len(live_results)
            + sum(result.document_type in {"journal", "summary"} for result in narrow_results),
            project_scope=normalized_scope,
        ),
    )


def _read_priority_memory(
    root: Path,
    max_chars: int,
    project_scope: str | None = None,
) -> tuple[list[str], list[MemoryContextReference]]:
    sections: list[str] = []
    references: list[MemoryContextReference] = []
    sources: list[tuple[Path, Path, str]] = []
    for relative in (
        Path("memory") / "long_term.md",
        Path("memory") / "preferences.md",
        Path("memory") / "projects.md",
    ):
        path = root / relative
        if not path.exists():
            continue
        content = path.read_text(encoding="utf-8").strip()
        if not content:
            continue
        if project_scope:
            content = _project_scoped_memory_content(content, project_scope)
            if not content:
                continue
        sources.append((relative, path, content))

    remaining = max(max_chars, 1)
    for index, (relative, path, content) in enumerate(sources):
        slots_left = len(sources) - index
        limit = max(remaining // slots_left, 1)
        if len(content) > limit:
            suffix = "\n...[truncated]"
            content = suffix[:limit] if limit <= len(suffix) else content[: limit - len(suffix)].rstrip() + suffix
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
    return sections, references


def _project_scoped_memory_content(content: str, project_scope: str) -> str:
    """Return only matching lines or matching Markdown sections.

    Passing an entire long-term-memory file because one line names a project
    would cross the requested project boundary.  A heading that names the scope
    owns its section; a non-heading match contributes only that factual line.
    """

    lines = content.splitlines()
    selected: set[int] = set()
    normalized_scope = "".join(project_scope.lower().split())
    for index, line in enumerate(lines):
        if normalized_scope not in "".join(line.lower().split()):
            continue
        heading = re.match(r"^(#{1,6})\s+", line)
        if not heading:
            selected.add(index)
            continue
        level = len(heading.group(1))
        selected.add(index)
        for following in range(index + 1, len(lines)):
            next_heading = re.match(r"^(#{1,6})\s+", lines[following])
            if next_heading and len(next_heading.group(1)) <= level:
                break
            selected.add(following)
    return "\n".join(lines[index] for index in sorted(selected)).strip()


def _recent_distinct_messages(question: str, recent_user_messages: tuple[str, ...]) -> tuple[str, ...]:
    latest = question.strip()
    messages = [message.strip() for message in recent_user_messages if message and message.strip()]
    if messages and messages[-1] == latest:
        messages.pop()
    return tuple(messages[-2:])


def _is_follow_up_question(question: str) -> bool:
    compact = "".join(question.split())
    return len(compact) <= 32 and _has_any(compact, FOLLOW_UP_SIGNALS)


def _is_memory_related_text(text: str) -> bool:
    assessment = assess_memory_need(text)
    return assessment.score >= 2 or (
        _has_any(text, SELF_REFERENCES) and _has_any(text, PERSONAL_TOPIC_SIGNALS)
    )


def _should_use_fallback(question: str, history: tuple[str, ...], learned_bonus: int) -> bool:
    features = set(classify_retrieval_features(question))
    if {"self-reference", "owned-device-question"}.issubset(features):
        return True
    if "follow-up" in features and history and _is_memory_related_text("\n".join(history)):
        return True
    return bool(learned_bonus and "owned-device-question" in features)


def _search_query(question: str, history: tuple[str, ...]) -> str:
    # The current question remains the primary query.  Only a bounded amount of
    # immediately preceding user context is added for short follow-up questions.
    parts = [question.strip()]
    if _is_follow_up_question(question):
        parts = [*history[-2:], *parts]
    return "\n".join(part for part in parts if part)[:800]


def _needs_past_conversation_evidence(question: str) -> bool:
    return _has_any(question, PAST_SIGNALS + ("いつ話した", "何て聞いた", "何て答えた")) or (
        _has_any(question, SELF_REFERENCES)
        and _has_any(question, ("感想", "好み", "意見", "考え"))
    )


def _concrete_query(question: str) -> str:
    normalized = question
    for value in (*GENERIC_PAST_TERMS, *REQUEST_TERMS):
        normalized = normalized.replace(value, " ")
    for value in ("について", "という", "から", "まで", "の", "は", "を", "に", "が", "で", "と", "や", "も", "へ"):
        normalized = normalized.replace(value, " ")
    terms = [term for term in normalized.replace("？", " ").replace("?", " ").split() if len(term) >= 2]
    return " ".join(_dedupe(terms))


def _normalize_project_scope(project_scope: str | None) -> str | None:
    if project_scope is None:
        return None
    normalized = project_scope.strip()
    if not normalized:
        return None
    return normalized


def _scope_matches_text(project_scope: str, *values: str) -> bool:
    needle = "".join(project_scope.lower().split())
    return any(needle in "".join(value.lower().split()) for value in values if value)


def _filter_project_scope(
    results: list[MemorySearchResult],
    project_scope: str | None,
    root: Path,
) -> list[MemorySearchResult]:
    """Constrain results to an explicit project boundary.

    Scope is matched against stable path/title/tags/category metadata and the
    returned excerpt.  An empty match remains empty; it never broadens back to
    unrelated personal history.
    """

    if not project_scope:
        return results
    content_cache: dict[Path, str] = {}
    scoped: list[MemorySearchResult] = []
    for result in results:
        path_key = result.path.resolve()
        if path_key not in content_cache:
            try:
                content_cache[path_key] = result.path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                content_cache[path_key] = ""
        if _scope_matches_text(
            project_scope,
            _display_path(result.path, root),
            result.title,
            " ".join(result.tags),
            result.category or "",
            result.category_label or "",
            result.source or "",
            result.snippet,
            content_cache[path_key],
        ):
            scoped.append(result)
    return scoped


def _search_scoped(
    *,
    root: Path,
    query: str,
    limit: int,
    document_types: tuple[str, ...],
    use_index: bool,
    project_scope: str | None,
    category: str | None = None,
) -> list[MemorySearchResult]:
    results = search_memory(
        root=root,
        query=query,
        limit=limit,
        document_types=document_types,
        category=category,
        scope=project_scope,
        use_index=use_index,
    )
    return _filter_project_scope(results, project_scope, root)[: max(limit, 1)]


def _contains_failed_retrieval_signal(content: str) -> bool:
    normalized = content.casefold()
    return any(signal.casefold() in normalized for signal in FAILED_RETRIEVAL_SIGNALS)


def _exclude_failed_retrieval_messages(
    results: list[MemorySearchResult],
) -> list[MemorySearchResult]:
    """Drop a failed reply and its immediate request, not the whole session.

    A later assistant miss must not invalidate a correct user statement in the
    same session. Conversely, the user request immediately before a failed
    assistant reply is a retrieval request, not evidence of the user's stored
    view, so that one adjacent request is excluded as well.
    """

    failure_cache: dict[Path, tuple[set[int], set[int]]] = {}
    filtered: list[MemorySearchResult] = []
    for result in results:
        failed_assistants: set[int] = set()
        failed_requests: set[int] = set()
        if result.path.name.casefold() == "raw.md":
            path_key = result.path.resolve()
            if path_key not in failure_cache:
                failure_cache[path_key] = _failed_retrieval_message_numbers(path_key)
            failed_assistants, failed_requests = failure_cache[path_key]

        # A whole raw document containing a failed assistant reply is too
        # ambiguous to use as evidence. Safe message-level chunks from the
        # same file remain eligible below.
        if result.document_type == "raw" and failed_assistants:
            continue
        if result.message_number is not None:
            if result.message_number in failed_assistants or result.message_number in failed_requests:
                continue
        elif _contains_failed_retrieval_signal(result.snippet):
            continue
        if result.speaker_role == "assistant" and _contains_failed_retrieval_signal(result.snippet):
            continue
        filtered.append(result)
    return filtered


def _failed_retrieval_message_numbers(path: Path) -> tuple[set[int], set[int]]:
    records = _read_raw_message_records(path)
    failed_assistants = {
        int(record["message_number"])
        for record in records
        if record["role"] == "assistant"
        and _contains_failed_retrieval_signal(str(record["content"]))
    }
    failed_requests: set[int] = set()
    by_number = {int(record["message_number"]): record for record in records}
    for number in failed_assistants:
        preceding = by_number.get(number - 1)
        if preceding is not None and preceding["role"] == "user":
            failed_requests.add(number - 1)
    return failed_assistants, failed_requests


def _read_raw_message_records(path: Path) -> list[dict[str, object]]:
    try:
        raw_content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    return [
        {
            "message_number": message_number,
            "role": match.group("role").lower(),
            "content": match.group("content").strip(),
        }
        for message_number, match in enumerate(RAW_MESSAGE_PATTERN.finditer(raw_content), start=1)
        if match.group("content").strip()
    ]


def _merge_results(*groups: list[MemorySearchResult], limit: int) -> list[MemorySearchResult]:
    merged: list[MemorySearchResult] = []
    seen: set[tuple[str, str, str | None, int | None]] = set()
    for group in groups:
        for result in group:
            key = (str(result.path), result.document_type, result.speaker_role, result.message_number)
            if key in seen:
                continue
            seen.add(key)
            merged.append(result)
    return merged[: max(limit, 1)]


def _search_unorganized_live(
    root: Path,
    query: str,
    question: str,
    project_scope: str | None = None,
    *,
    exclude_live_session: Path | str | None = None,
) -> list[MemorySearchResult]:
    live_dir = root / "inbox" / "live"
    if not live_dir.exists() or not query.strip():
        return []

    terms = _live_query_terms(query)
    all_results: list[MemorySearchResult] = []
    excluded_path = _resolved_live_session_path(root, exclude_live_session)
    if exclude_live_session is not None and excluded_path is None:
        return []
    for path in sorted(live_dir.glob("*.jsonl")):
        if excluded_path is not None and path.resolve() == excluded_path:
            continue
        if not _is_unorganized_live(path):
            continue
        scope_mode = _live_project_scope_mode(path, project_scope, root)
        if scope_mode == "none":
            continue
        records = _read_live_records(path)
        failed_assistants = {
            number
            for number, record in enumerate(records, start=1)
            if record.get("role") == "assistant"
            and _contains_failed_retrieval_signal(record.get("content", ""))
        }
        failed_requests = {
            number - 1
            for number in failed_assistants
            if number > 1 and records[number - 2].get("role") == "user"
        }
        for message_number, record in enumerate(records, start=1):
            content = record.get("content", "")
            if message_number in failed_assistants or message_number in failed_requests:
                continue
            if scope_mode == "messages" and project_scope and not _scope_matches_text(project_scope, content):
                continue
            score = _live_match_score(content, terms)
            timestamp = _parse_local_timestamp(record.get("timestamp"))
            all_results.append(
                MemorySearchResult(
                    document_type="live_message",
                    path=path,
                    title=f"Live session {path.stem} / {record['role']} message {message_number}",
                    date=timestamp.date().isoformat() if timestamp else None,
                    tags=(),
                    snippet=_short_snippet(content, width=2200),
                    score=score,
                    speaker_role=record["role"],
                    message_number=message_number,
                )
            )

    candidates = sorted(
        (result for result in all_results if result.score > 0),
        key=lambda result: (result.score, result.date or "", str(result.path)),
        reverse=True,
    )
    if not candidates:
        return []
    anchor = _select_raw_evidence_anchor(candidates, _raw_evidence_scope(question))
    if anchor is None:
        return []
    if _raw_evidence_scope(question) == "user":
        return [anchor]
    related = [result for result in all_results if result.path == anchor.path]
    return _paired_raw_evidence(anchor, related, limit=RAW_EVIDENCE_LIMIT)


def _resolved_live_session_path(
    root: Path,
    exclude_live_session: Path | str | None,
) -> Path | None:
    if exclude_live_session is None:
        return None
    path = Path(exclude_live_session)
    if not path.is_absolute():
        path = root / path
    try:
        return path.resolve()
    except OSError:
        return None


def _live_project_scope_mode(path: Path, project_scope: str | None, root: Path) -> str:
    if not project_scope:
        return "session"

    metadata_path = path.with_suffix(".session.json")
    metadata: dict[str, object] = {}
    if metadata_path.exists():
        try:
            loaded = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return "none"
        if not isinstance(loaded, dict):
            return "none"
        metadata = loaded
        personalization = metadata.get("personalization")
        if personalization is not None:
            if not isinstance(personalization, dict):
                return "none"
            stored_scope = personalization.get("project_scope")
            if stored_scope is not None:
                if not isinstance(stored_scope, str):
                    return "none"
                normalized_stored = "".join(stored_scope.casefold().split())
                if normalized_stored:
                    normalized_requested = "".join(project_scope.casefold().split())
                    return "session" if normalized_stored == normalized_requested else "none"

    if _scope_matches_text(
        project_scope,
        _display_path(path, root),
        str(metadata.get("title") or ""),
        str(metadata.get("session_id") or ""),
    ):
        return "session"
    return "messages"


def _is_unorganized_live(path: Path) -> bool:
    metadata_path = path.with_suffix(".session.json")
    if not metadata_path.exists():
        return True
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # An existing but unreadable metadata file may contain a temporary-chat
        # exclusion.  Fail closed rather than exposing that log as memory.
        return False
    if not isinstance(metadata, dict):
        return False
    personalization = metadata.get("personalization")
    if personalization is not None:
        if not isinstance(personalization, dict):
            return False
        for field_name in ("temporary", "exclude_from_memory"):
            value = personalization.get(field_name, False)
            if not isinstance(value, bool):
                return False
            if value:
                return False
    organize = metadata.get("organize") if isinstance(metadata, dict) else None
    return not isinstance(organize, dict) or not bool(organize.get("index_updated"))


def _read_live_records(path: Path) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return records
    for line in lines:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(value, dict):
            continue
        role = value.get("role")
        content = value.get("content")
        timestamp = value.get("timestamp")
        if role in {"user", "assistant"} and isinstance(content, str) and isinstance(timestamp, str):
            records.append({"role": role, "content": content, "timestamp": timestamp})
    return records


def _parse_local_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError:
        return None
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=TOKYO_TIMEZONE)
    return timestamp.astimezone(TOKYO_TIMEZONE)


def _live_query_terms(query: str) -> tuple[str, ...]:
    terms = [
        term
        for variant in expand_query_variants(query)
        for term in variant.replace("？", " ").replace("?", " ").split()
        if len(term) >= 2
    ]
    return tuple(_dedupe(terms))


def _live_match_score(content: str, terms: tuple[str, ...]) -> int:
    lowered = content.lower()
    return sum(lowered.count(term.lower()) for term in terms)


def _display_path(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def _format_result(result: MemorySearchResult, root: Path) -> list[str]:
    lines = [
        f"- Date: {result.date or 'unknown'}",
        f"  Type: {result.document_type}",
    ]
    if result.speaker_role:
        lines.append(f"  Role: {result.speaker_role}")
    if result.message_number is not None:
        lines.append(f"  Message: {result.message_number}")
    if result.category:
        lines.append(f"  Category: {result.category} ({result.category_label or result.category})")
    if result.status:
        lines.append(f"  Status: {result.status}")
    lines.extend(
        [
            f"  Source: {_display_path(result.path, root)}",
            f"  Snippet: {result.snippet}",
        ]
    )
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
    project_scope: str | None = None,
) -> list[MemorySearchResult]:
    results: list[MemorySearchResult] = []
    for category in categories:
        results.extend(
            _search_scoped(
                root=root,
                query="",
                limit=max_results,
                document_types=("memory_item",),
                use_index=use_index,
                project_scope=project_scope,
                category=category,
            )
        )
    if not categories:
        results.extend(
            _search_scoped(
                root=root,
                query=question,
                limit=max_results,
                document_types=("memory_item",),
                use_index=use_index,
                project_scope=project_scope,
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


def _select_role_aware_raw_evidence(
    root: Path,
    candidates: list[MemorySearchResult],
    question: str,
    use_index: bool,
    project_scope: str | None = None,
) -> list[MemorySearchResult]:
    if not candidates:
        return []

    scope = _raw_evidence_scope(question)
    anchor = _select_raw_evidence_anchor(candidates, scope)
    if anchor is None:
        return []
    if scope == "user":
        return [anchor]

    related = _related_raw_chunks(root, anchor, use_index, project_scope=project_scope)
    if not related:
        return [anchor]
    return _paired_raw_evidence(anchor, related, limit=RAW_EVIDENCE_LIMIT)


def _raw_evidence_scope(question: str) -> str:
    normalized = question.lower()
    if _has_any(normalized, SELF_REFERENCES) and _has_any(
        normalized, ("感想", "好み", "意見", "考え", "my thoughts", "my impression")
    ):
        return "user"
    if any(signal.lower() in normalized for signal in ASSISTANT_EVIDENCE_SIGNALS):
        return "assistant"
    if any(signal.lower() in normalized for signal in USER_EVIDENCE_SIGNALS):
        return "user"
    return "both"


def _select_raw_evidence_anchor(
    candidates: list[MemorySearchResult], scope: str
) -> MemorySearchResult | None:
    if scope == "user":
        return next((result for result in candidates if result.speaker_role == "user"), None)
    if scope == "assistant":
        return next(
            (result for result in candidates if result.speaker_role == "assistant"),
            candidates[0],
        )
    return candidates[0]


def _related_raw_chunks(
    root: Path,
    anchor: MemorySearchResult,
    use_index: bool,
    project_scope: str | None = None,
) -> list[MemorySearchResult]:
    """Read only the two source messages adjacent to the selected anchor.

    An empty-query index read used to cap the session at its first ranked 50
    chunks. In long sessions that could omit the anchor's real neighbor and
    pair an unrelated later response. Reading the already-authorized raw path
    gives an exact, bounded neighborhood independent of session length.
    """

    del use_index  # The source Markdown is authoritative for exact adjacency.
    if anchor.message_number is None or anchor.path.suffix.lower() != ".md":
        return []
    try:
        raw_content = anchor.path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    first_heading = re.search(
        r"^## (?:User|Assistant)[ \t]*$",
        raw_content,
        flags=re.MULTILINE | re.IGNORECASE,
    )
    raw_header = raw_content[: first_heading.start()] if first_heading else raw_content
    target_numbers = {anchor.message_number - 1, anchor.message_number + 1}
    base_title = re.sub(
        r"\s*/\s*(?:User|Assistant) message \d+\s*$",
        "",
        anchor.title,
        flags=re.IGNORECASE,
    ).strip() or anchor.title
    related: list[MemorySearchResult] = []
    for message_number, match in enumerate(RAW_MESSAGE_PATTERN.finditer(raw_content), start=1):
        if message_number not in target_numbers:
            continue
        message_content = match.group("content").strip()
        if not message_content or _contains_failed_retrieval_signal(message_content):
            continue
        if project_scope and not _scope_matches_text(
            project_scope,
            _display_path(anchor.path, root),
            raw_header,
            base_title,
            " ".join(anchor.tags),
            message_content,
        ):
            continue
        role = match.group("role").lower()
        related.append(
            MemorySearchResult(
                document_type="raw_chunk",
                path=anchor.path,
                title=f"{base_title} / {role.title()} message {message_number}",
                date=anchor.date,
                tags=anchor.tags,
                snippet=_short_snippet(message_content, width=2200),
                score=0,
                speaker_role=role,
                message_number=message_number,
            )
        )
    return related


def _paired_raw_evidence(
    anchor: MemorySearchResult,
    related: list[MemorySearchResult],
    limit: int,
) -> list[MemorySearchResult]:
    if anchor.message_number is None:
        return [anchor]

    if anchor.speaker_role == "assistant":
        preceding_user = _adjacent_related_chunk(
            related,
            message_number=anchor.message_number - 1,
            speaker_role="user",
        )
        pair = [item for item in (preceding_user, anchor) if item is not None]
    else:
        following_assistant = _adjacent_related_chunk(
            related,
            message_number=anchor.message_number + 1,
            speaker_role="assistant",
        )
        pair = [item for item in (anchor, following_assistant) if item is not None]

    unique: list[MemorySearchResult] = []
    seen: set[tuple[str, str | None, int | None]] = set()
    for result in pair:
        key = (str(result.path), result.speaker_role, result.message_number)
        if key not in seen:
            seen.add(key)
            unique.append(result)
    return unique[: max(limit, 1)]


def _adjacent_related_chunk(
    related: list[MemorySearchResult],
    message_number: int,
    speaker_role: str,
) -> MemorySearchResult | None:
    return next(
        (
            result
            for result in related
            if result.speaker_role == speaker_role
            and result.message_number == message_number
            and not _contains_failed_retrieval_signal(result.snippet)
        ),
        None,
    )


def _reference_from_result(result: MemorySearchResult, root: Path) -> MemoryContextReference:
    return MemoryContextReference(
        path=_display_path(result.path, root),
        document_type=result.document_type,
        title=result.title,
        date=result.date,
        snippet=result.snippet,
        score=result.score,
        speaker_role=result.speaker_role,
        message_number=result.message_number,
    )


def _dedupe_references(references: list[MemoryContextReference]) -> list[MemoryContextReference]:
    result: list[MemoryContextReference] = []
    seen: set[tuple[str, str, str | None, int | None]] = set()
    for reference in references:
        key = (
            reference.path,
            reference.document_type,
            reference.speaker_role,
            reference.message_number,
        )
        if key in seen:
            continue
        seen.add(key)
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
    parser.add_argument(
        "--max-memory-chars",
        type=int,
        default=CORE_MEMORY_CHAR_LIMIT,
        help="Maximum core memory characters (capped at 1000).",
    )
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
            f"depth score {context.score}; deep-search threshold {context.threshold})"
        )
        for reference in context.references:
            print(f"- {reference.path}")
    else:
        print(
            f"No memory context used. depth-score={context.score}; "
            f"deep-search-threshold={context.threshold}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
