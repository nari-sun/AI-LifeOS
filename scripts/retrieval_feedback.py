"""Read-only retrieval-pattern feedback for AI-LifeOS memory context.

This module intentionally stores only normalized retrieval features after a
conversation is finalized.  It never stores user messages, assistant replies,
or facts from memory in the feedback data.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from memory_index import ROOT, search_memory


FEEDBACK_RELATIVE_PATH = Path("memory") / "retrieval_feedback.jsonl"
FEEDBACK_VERSION = 1
MAX_EVENTS = 200
SELF_REFERENCES = ("俺", "おれ", "オレ", "私", "わたし", "僕", "ぼく", "自分")
FOLLOW_UP_SIGNALS = ("じゃあ", "それで", "ちなみに", "それ", "前者", "後者")
DEVICE_SIGNALS = ("スマホ", "携帯", "端末", "iphone", "android", "pc", "パソコン", "ノートpc")
CORRECTION_SIGNALS = ("残ってる", "前に話した", "過去のこと", "覚えてない", "覚えてな", "前にも言った")


def classify_retrieval_features(question: str) -> tuple[str, ...]:
    """Return normalized retrieval features without retaining the question text."""

    normalized = "".join(question.split()).lower()
    if not normalized:
        return ()

    features: list[str] = []
    if len(normalized) <= 32:
        features.append("short-question")
    if _has_any(normalized, SELF_REFERENCES):
        features.append("self-reference")
    if _has_any(normalized, FOLLOW_UP_SIGNALS):
        features.append("follow-up")
    if _has_any(normalized, DEVICE_SIGNALS):
        features.append("owned-device-question")
    return tuple(features)


def is_retrieval_correction(question: str) -> bool:
    return _has_any("".join(question.split()).lower(), CORRECTION_SIGNALS)


def feedback_bonus(root: Path | str = ROOT, question: str = "") -> tuple[int, tuple[str, ...]]:
    """Return a bounded, non-evidentiary search-start bonus for a known pattern."""

    features = set(classify_retrieval_features(question))
    if not features:
        return 0, ()

    for event in _read_events(Path(root)):
        learned = set(event.get("features", ()))
        # A device/self-reference pattern is specific enough to be useful.  A
        # short question alone is never enough to broaden retrieval.
        if "owned-device-question" in learned and "owned-device-question" in features:
            return 1, ("learned-retrieval-pattern",)
        learned_specific = learned - {"short-question"}
        if learned_specific and learned_specific.issubset(features):
            return 1, ("learned-retrieval-pattern",)
    return 0, ()


def record_confirmed_retrieval_feedback(
    root: Path | str,
    records: Iterable[dict[str, Any]],
    session_id: str,
) -> int:
    """Persist verified retrieval-pattern events after finalization only.

    The event records normalized feature names, outcome, timestamp, and session
    id.  It deliberately omits conversation text and the matched memory value.
    """

    root = Path(root)
    normalized_records = [record for record in records if _valid_record(record)]
    existing = _read_events(root)
    existing_keys = {
        (str(event.get("session_id", "")), tuple(event.get("features", ())))
        for event in existing
    }
    new_events: list[dict[str, Any]] = []

    for index, record in enumerate(normalized_records):
        if record["role"] != "user" or not is_retrieval_correction(record["content"]):
            continue
        previous_question = _previous_user_question(normalized_records, index)
        if not previous_question:
            continue
        features = classify_retrieval_features(previous_question)
        if not features or not _has_confirming_evidence(root, previous_question, record["content"]):
            continue

        key = (session_id, features)
        if key in existing_keys:
            continue
        existing_keys.add(key)
        new_events.append(
            {
                "version": FEEDBACK_VERSION,
                "session_id": session_id,
                "recorded_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "features": list(features),
                "outcome": "confirmed-retrieval-miss",
            }
        )

    if not new_events:
        return 0

    feedback_path = root / FEEDBACK_RELATIVE_PATH
    feedback_path.parent.mkdir(parents=True, exist_ok=True)
    retained = [*existing, *new_events][-MAX_EVENTS:]
    feedback_path.write_text(
        "".join(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n" for event in retained),
        encoding="utf-8",
    )
    return len(new_events)


def _previous_user_question(records: list[dict[str, Any]], correction_index: int) -> str:
    saw_assistant = False
    for index in range(correction_index - 1, -1, -1):
        record = records[index]
        if record["role"] == "assistant":
            saw_assistant = True
            continue
        if record["role"] == "user":
            return record["content"] if saw_assistant else ""
    return ""


def _has_confirming_evidence(root: Path, question: str, correction: str) -> bool:
    query = _feedback_query(question, correction)
    if not query:
        return False
    results = search_memory(
        root=root,
        query=query,
        limit=3,
        document_types=("memory", "memory_item", "journal", "summary"),
        use_index=True,
    )
    return any(result.score > 0 for result in results)


def _feedback_query(question: str, correction: str) -> str:
    text = f"{question} {correction}".lower()
    terms = [term for term in re.split(r"[^0-9a-zA-Zぁ-んァ-ン一-龠]+", text) if len(term) >= 2]
    if _has_any(text, DEVICE_SIGNALS):
        terms.extend(("スマホ", "端末", "iPhone", "Android", "PC", "パソコン"))
    return " ".join(_dedupe(terms))


def _read_events(root: Path) -> list[dict[str, Any]]:
    path = root / FEEDBACK_RELATIVE_PATH
    if not path.exists():
        return []

    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(value, dict) or value.get("outcome") != "confirmed-retrieval-miss":
            continue
        features = value.get("features")
        if not isinstance(features, list) or not all(isinstance(item, str) for item in features):
            continue
        events.append(value)
    return events[-MAX_EVENTS:]


def _valid_record(record: dict[str, Any]) -> bool:
    return (
        isinstance(record, dict)
        and record.get("role") in {"user", "assistant"}
        and isinstance(record.get("content"), str)
    )


def _has_any(text: str, values: tuple[str, ...]) -> bool:
    return any(value.lower() in text for value in values)


def _dedupe(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = value.strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result
