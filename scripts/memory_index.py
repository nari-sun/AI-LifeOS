import json
import re
import sqlite3
from contextlib import closing
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Iterable, Protocol, Sequence

from memory_items import read_memory_item


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INDEX_PATH = Path("memory") / "search_index.sqlite3"
SUPPORTED_MEMORY_FILES = {"long_term.md", "preferences.md", "projects.md"}
RRF_K = 60
INDEX_SCHEMA_VERSION = 1
RAW_METADATA_PARSER_VERSION = 1
INDEX_METADATA_TABLE = "index_metadata"
INDEX_VERSION_METADATA = {
    "schema_version": str(INDEX_SCHEMA_VERSION),
    "raw_metadata_parser_version": str(RAW_METADATA_PARSER_VERSION),
}

REQUEST_EXPRESSIONS = (
    "教えてください",
    "教えて",
    "教えてほしい",
    "聞かせてください",
    "聞かせて",
    "知りたい",
    "覚えてる",
    "覚えている",
    "なんだっけ",
    "だっけ",
    "please tell me",
    "tell me",
    "do you remember",
)

QUERY_STOP_TERMS = {
    "教えて",
    "教えてください",
    "なんだっけ",
    "だっけ",
    "について",
    "こと",
    "これ",
    "それ",
    "あれ",
    "して",
    "ください",
    "tell",
    "please",
}


@dataclass(frozen=True)
class MemoryDocument:
    document_key: str
    document_type: str
    path: Path
    title: str
    date: str | None
    tags: tuple[str, ...]
    content: str
    category: str | None = None
    category_label: str | None = None
    status: str | None = None
    source: str | None = None
    source_date: str | None = None
    confidence: str | None = None
    speaker_role: str | None = None
    message_number: int | None = None


class LocalSemanticBackend(Protocol):
    """Optional interface for a future, entirely local semantic ranker.

    Implementations receive the already metadata-filtered documents and return
    stable ``document_key`` values in relevance order.  The default search does
    not instantiate a backend and therefore adds no dependency or external
    network access.
    """

    def rank(
        self,
        query: str,
        documents: Sequence[MemoryDocument],
        limit: int,
    ) -> Sequence[str]: ...


@dataclass(frozen=True)
class MemorySearchResult:
    document_type: str
    path: Path
    title: str
    date: str | None
    tags: tuple[str, ...]
    snippet: str
    score: int
    category: str | None = None
    category_label: str | None = None
    status: str | None = None
    source: str | None = None
    source_date: str | None = None
    confidence: str | None = None
    speaker_role: str | None = None
    message_number: int | None = None


@dataclass(frozen=True)
class IndexHealth:
    """Read-only freshness information for the derived SQLite index."""

    status: str
    reasons: tuple[str, ...] = ()
    source_count: int = 0
    indexed_source_count: int = 0
    checked_at: str | None = None

    @property
    def needs_markdown_fallback(self) -> bool:
        # Legacy indexes have no source manifest and may also contain metadata
        # extracted from raw message bodies by an older parser. Even when the
        # path set and timestamps happen to match, their title/tags are not a
        # safe basis for scope filtering. Keep the derived DB untouched and use
        # the current Markdown parser for this search instead.
        return self.status in {"legacy", "missing", "stale", "unreadable"}

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reasons": list(self.reasons),
            "source_count": self.source_count,
            "indexed_source_count": self.indexed_source_count,
            "checked_at": self.checked_at,
            "needs_markdown_fallback": self.needs_markdown_fallback,
        }


@dataclass(frozen=True)
class SearchProfile:
    """Timing and candidate counts for one read-only memory search.

    ``index_load_ms`` is the time spent opening and inspecting the SQLite
    index (or collecting Markdown when ``source`` is ``markdown``).
    ``filter_ms`` is the time spent applying the metadata filters.  For an
    index-backed search that work is the parameterized SQLite query itself,
    so candidate documents never need to be loaded into Python merely to be
    discarded.  ``ranking_ms`` covers the existing Python Japanese partial
    match ranking.
    """

    source: str
    index_load_ms: float
    filter_ms: float
    ranking_ms: float
    total_ms: float
    candidate_count: int
    result_count: int
    filters: tuple[str, ...]
    index_status: str = "disabled"
    fallback_document_count: int = 0
    query_variants: tuple[str, ...] = ()
    retrieval_mode: str = "lexical"

    def to_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "index_load_ms": round(self.index_load_ms, 3),
            "filter_ms": round(self.filter_ms, 3),
            "ranking_ms": round(self.ranking_ms, 3),
            "total_ms": round(self.total_ms, 3),
            "candidate_count": self.candidate_count,
            "result_count": self.result_count,
            "filters": list(self.filters),
            "index_status": self.index_status,
            "fallback_document_count": self.fallback_document_count,
            "query_variants": list(self.query_variants),
            "retrieval_mode": self.retrieval_mode,
        }


def default_index_path(root: Path | str = ROOT) -> Path:
    return Path(root) / DEFAULT_INDEX_PATH


def inspect_index_health(
    root: Path | str = ROOT,
    db_path: Path | str | None = None,
) -> IndexHealth:
    """Inspect index freshness without rebuilding or modifying any file.

    New indexes contain a path/mtime/size manifest. Legacy indexes still have
    their path set and build timestamp inspected for diagnostics, but searches
    always fall back to Markdown because their raw metadata cannot be trusted
    for scope filtering. Missing or changed Markdown is also handled by direct
    search later.
    """

    root = Path(root)
    db_file = _resolve_db_path(root, db_path)
    checked_at = datetime.now().astimezone().isoformat(timespec="seconds")
    source_stats = _source_stats(root)
    if not db_file.exists():
        return IndexHealth(
            status="missing",
            reasons=("index-missing",),
            source_count=len(source_stats),
            checked_at=checked_at,
        )
    try:
        with closing(sqlite3.connect(f"file:{db_file.as_posix()}?mode=ro", uri=True)) as connection:
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if "documents" not in tables:
                raise sqlite3.DatabaseError("documents table is missing")
            indexed_paths = {
                str(row[0])
                for row in connection.execute(
                    "SELECT DISTINCT path FROM documents WHERE document_type != 'raw_chunk'"
                ).fetchall()
            }
            version_reasons = _index_version_reasons(connection, tables)
            if "indexed_sources" in tables:
                manifest = {
                    str(path): (int(mtime_ns), int(size))
                    for path, mtime_ns, size in connection.execute(
                        "SELECT path, mtime_ns, size FROM indexed_sources"
                    ).fetchall()
                }
                reasons: list[str] = list(version_reasons)
                if set(source_stats) - set(manifest):
                    reasons.append("unindexed-source")
                if set(manifest) - set(source_stats):
                    reasons.append("deleted-source")
                if any(manifest.get(path) != stat for path, stat in source_stats.items()):
                    reasons.append("changed-source")
                status = "legacy" if version_reasons else "stale" if reasons else "fresh"
                return IndexHealth(
                    status=status,
                    reasons=tuple(_dedupe(reasons)),
                    source_count=len(source_stats),
                    indexed_source_count=len(manifest),
                    checked_at=checked_at,
                )

            # Legacy indexes did not store source mtimes.  Path differences are
            # still enough to detect the common stale-index failure where a new
            # raw.md was finalized after the last rebuild.
            source_reasons: list[str] = []
            if set(source_stats) - indexed_paths:
                source_reasons.append("unindexed-source")
            if indexed_paths - set(source_stats):
                source_reasons.append("deleted-source")
            try:
                index_mtime_ns = db_file.stat().st_mtime_ns
            except OSError:
                index_mtime_ns = 0
            if any(mtime_ns > index_mtime_ns for mtime_ns, _ in source_stats.values()):
                source_reasons.append("changed-source")
            reasons = [*version_reasons, "source-manifest-unavailable", *source_reasons]
            return IndexHealth(
                status="stale" if source_reasons else "legacy",
                reasons=tuple(_dedupe(reasons)),
                source_count=len(source_stats),
                indexed_source_count=len(indexed_paths),
                checked_at=checked_at,
            )
    except (OSError, sqlite3.DatabaseError, sqlite3.OperationalError) as error:
        return IndexHealth(
            status="unreadable",
            reasons=(f"index-unreadable:{type(error).__name__}",),
            source_count=len(source_stats),
            checked_at=checked_at,
        )


def _index_version_reasons(
    connection: sqlite3.Connection,
    tables: set[str],
) -> tuple[str, ...]:
    """Return compatibility reasons without trusting a source manifest alone.

    A manifest proves that source files did not change after a build, but it
    cannot prove which metadata parser produced raw titles/tags.  Scope checks
    must therefore use SQLite only when both explicit build versions match.
    """

    if INDEX_METADATA_TABLE not in tables:
        return ("index-version-metadata-missing",)
    rows = connection.execute(
        f"SELECT key, value FROM {INDEX_METADATA_TABLE}"
    ).fetchall()
    metadata = {
        str(key): str(value)
        for key, value in rows
        if isinstance(key, str) and value is not None
    }
    reasons: list[str] = []
    for key, expected in INDEX_VERSION_METADATA.items():
        actual = metadata.get(key)
        reason_prefix = "parser-version" if key == "raw_metadata_parser_version" else "schema-version"
        if actual is None:
            reasons.append(f"{reason_prefix}-missing")
        elif actual != expected:
            reasons.append(f"{reason_prefix}-mismatch")
    return tuple(reasons)


def _source_stats(root: Path) -> dict[str, tuple[int, int]]:
    stats: dict[str, tuple[int, int]] = {}
    for path in _candidate_paths(root):
        try:
            stat = path.stat()
        except OSError:
            continue
        stats[_relative_path(path, root)] = (stat.st_mtime_ns, stat.st_size)
    return stats


def ensure_memory_files(root: Path | str = ROOT) -> None:
    root = Path(root)
    memory_dir = root / "memory"
    memory_dir.mkdir(exist_ok=True)

    defaults = {
        "long_term.md": "# Long-Term Memory\n\n",
        "preferences.md": "# Preferences\n\n",
        "projects.md": "# Projects\n\n",
    }
    for filename, text in defaults.items():
        path = memory_dir / filename
        if not path.exists():
            path.write_text(text, encoding="utf-8")


def collect_documents(root: Path | str = ROOT) -> list[MemoryDocument]:
    root = Path(root)
    documents: list[MemoryDocument] = []

    for path in _candidate_paths(root):
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue

        document_type = _document_type(path, root)
        if not document_type:
            continue

        relative = _relative_path(path, root)
        structured = None
        if document_type == "memory_item":
            try:
                structured = read_memory_item(path)
            except ValueError:
                continue

        # Message bodies may contain strings such as ``Session:`` or tag-like
        # headings.  They are evidence for that message, not immutable metadata
        # for every sibling message in the raw transcript.
        metadata_content = _raw_session_header(content) if document_type == "raw" else content

        document = MemoryDocument(
            document_key=relative,
            document_type=document_type,
            path=path,
            title=structured.category_label if structured else _extract_title(metadata_content, path),
            date=structured.source_date if structured else _extract_date(metadata_content, path, root, document_type),
            tags=structured.tags if structured else tuple(_extract_tags(metadata_content)),
            content=structured.content if structured else content,
            category=structured.category if structured else None,
            category_label=structured.category_label if structured else None,
            status=structured.status if structured else None,
            source=structured.source if structured else None,
            source_date=structured.source_date if structured else None,
            confidence=structured.confidence if structured else None,
        )
        documents.append(document)
        if document_type == "raw":
            documents.extend(_raw_message_documents(document))

    return sorted(documents, key=lambda document: (document.date or "", document.document_type, document.document_key))


def rebuild_index(root: Path | str = ROOT, db_path: Path | str | None = None) -> Path:
    root = Path(root)
    ensure_memory_files(root)
    db_file = _resolve_db_path(root, db_path)
    db_file.parent.mkdir(parents=True, exist_ok=True)
    if db_file.exists():
        db_file.unlink()

    documents = collect_documents(root)
    with closing(sqlite3.connect(db_file)) as connection:
        _create_schema(connection)
        _insert_documents(connection, root, documents)
        _insert_source_manifest(connection, root)
        connection.commit()

    return db_file


def search_memory(
    root: Path | str = ROOT,
    query: str = "",
    db_path: Path | str | None = None,
    limit: int = 10,
    document_types: Iterable[str] | None = None,
    tag: str | None = None,
    category: str | None = None,
    status: str | None = None,
    date: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    path: str | None = None,
    use_index: bool = True,
    scope: str | None = None,
    semantic_backend: LocalSemanticBackend | None = None,
    speaker_role: str | None = None,
) -> list[MemorySearchResult]:
    results, _ = search_memory_with_profile(
        root=root,
        query=query,
        db_path=db_path,
        limit=limit,
        document_types=document_types,
        tag=tag,
        category=category,
        status=status,
        date=date,
        date_from=date_from,
        date_to=date_to,
        path=path,
        scope=scope,
        use_index=use_index,
        semantic_backend=semantic_backend,
        speaker_role=speaker_role,
    )
    return results


def search_memory_with_profile(
    root: Path | str = ROOT,
    query: str = "",
    db_path: Path | str | None = None,
    limit: int = 10,
    document_types: Iterable[str] | None = None,
    tag: str | None = None,
    category: str | None = None,
    status: str | None = None,
    date: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    path: str | None = None,
    use_index: bool = True,
    scope: str | None = None,
    semantic_backend: LocalSemanticBackend | None = None,
    speaker_role: str | None = None,
) -> tuple[list[MemorySearchResult], SearchProfile]:
    """Search memory and return the normal results together with timings.

    This deliberately remains separate from :func:`search_memory` so existing
    callers continue to receive only a result list.
    """
    root = Path(root)
    db_file = _resolve_db_path(root, db_path)
    type_filter = tuple(document_types or ())
    started_at = perf_counter()
    index_load_ms = 0.0
    filter_ms = 0.0
    source = "markdown"
    fallback_document_count = 0
    health = (
        inspect_index_health(root=root, db_path=db_file)
        if use_index
        else IndexHealth(status="disabled")
    )

    if use_index and db_file.exists() and not health.needs_markdown_fallback:
        source = "sqlite"
        documents, index_load_ms, filter_ms = _load_documents_from_index(
            root=root,
            db_path=db_file,
            document_types=type_filter,
            tag=tag,
            category=category,
            status=status,
            date=date,
            date_from=date_from,
            date_to=date_to,
            path=path,
            scope=scope,
            speaker_role=speaker_role,
        )
        if scope:
            strict_filter_started_at = perf_counter()
            normalized_scope = _normalize_scope(scope)
            source_header_cache: dict[Path, str] = {}
            documents = [
                document
                for document in documents
                if _document_matches_scope(document, normalized_scope, root, source_header_cache)
            ]
            filter_ms += (perf_counter() - strict_filter_started_at) * 1000
        # Existing indexes predate message-level raw evidence.  Keep them usable
        # without requiring a manual rebuild before the first answer after an
        # upgrade.
        if "raw_chunk" in type_filter and not documents:
            collect_started_at = perf_counter()
            documents = [
                document
                for document in collect_documents(root)
                if document.document_type == "raw_chunk"
            ]
            index_load_ms += (perf_counter() - collect_started_at) * 1000
            filter_started_at = perf_counter()
            documents = _filter_documents(
                documents,
                document_types=type_filter,
                tag=tag,
                category=category,
                status=status,
                date=date,
                date_from=date_from,
                date_to=date_to,
                path=path,
                scope=scope,
                speaker_role=speaker_role,
                root=root,
            )
            filter_ms += (perf_counter() - filter_started_at) * 1000
            source = "markdown-fallback"
    else:
        collect_started_at = perf_counter()
        documents = collect_documents(root)
        index_load_ms = (perf_counter() - collect_started_at) * 1000
        filter_started_at = perf_counter()
        documents = _filter_documents(
            documents,
            document_types=type_filter,
            tag=tag,
            category=category,
            status=status,
            date=date,
            date_from=date_from,
            date_to=date_to,
            path=path,
            scope=scope,
            speaker_role=speaker_role,
            root=root,
        )
        filter_ms = (perf_counter() - filter_started_at) * 1000
        fallback_document_count = len(documents) if use_index else 0
        if use_index:
            source = "sqlite+markdown-fallback"

    ranking_started_at = perf_counter()
    query_variants = expand_query_variants(query)
    results = _rank_documents(
        documents,
        query=query,
        limit=limit,
        semantic_backend=semantic_backend,
        query_variants=query_variants,
    )
    ranking_ms = (perf_counter() - ranking_started_at) * 1000
    profile = SearchProfile(
        source=source,
        index_load_ms=index_load_ms,
        filter_ms=filter_ms,
        ranking_ms=ranking_ms,
        total_ms=(perf_counter() - started_at) * 1000,
        candidate_count=len(documents),
        result_count=len(results),
        filters=_filter_names(
            document_types=type_filter,
            tag=tag,
            category=category,
            status=status,
            date=date,
            date_from=date_from,
            date_to=date_to,
            path=path,
            scope=scope,
            speaker_role=speaker_role,
        ),
        index_status=health.status,
        fallback_document_count=fallback_document_count,
        query_variants=query_variants,
        retrieval_mode="hybrid-local" if semantic_backend is not None else "hybrid-lexical",
    )
    return results, profile


def _filter_documents(
    documents: list[MemoryDocument],
    document_types: tuple[str, ...],
    tag: str | None,
    category: str | None,
    status: str | None,
    date: str | None,
    date_from: str | None,
    date_to: str | None,
    path: str | None,
    scope: str | None,
    speaker_role: str | None,
    root: Path,
) -> list[MemoryDocument]:
    filtered = documents
    if document_types:
        filtered = [document for document in filtered if document.document_type in document_types]
    if tag:
        filtered = [document for document in filtered if tag in document.tags]
    if category:
        filtered = [document for document in filtered if document.category == category]
    if status:
        filtered = [document for document in filtered if document.status == status]
    if date:
        filtered = [document for document in filtered if document.date == date]
    if date_from:
        filtered = [document for document in filtered if document.date and document.date >= date_from]
    if date_to:
        filtered = [document for document in filtered if document.date and document.date <= date_to]
    if path:
        normalized_path = _normalize_path(path)
        filtered = [
            document
            for document in filtered
            if normalized_path in _normalize_path(_relative_path(document.path, root))
        ]
    if speaker_role:
        normalized_role = speaker_role.strip().lower()
        filtered = [document for document in filtered if document.speaker_role == normalized_role]
    if scope:
        normalized_scope = _normalize_scope(scope)
        source_header_cache: dict[Path, str] = {}
        filtered = [
            document
            for document in filtered
            if _document_matches_scope(document, normalized_scope, root, source_header_cache)
        ]
    return filtered


def _document_matches_scope(
    document: MemoryDocument,
    normalized_scope: str,
    root: Path,
    source_header_cache: dict[Path, str],
) -> bool:
    # A raw session is scoped by its immutable header and the current message,
    # never by a different message elsewhere in the same transcript.
    raw_header = ""
    if document.document_type == "raw":
        raw_header = _raw_session_header(document.content)
    elif document.document_type == "raw_chunk":
        path_key = document.path.resolve()
        if path_key not in source_header_cache:
            try:
                source_header_cache[path_key] = _raw_session_header(
                    document.path.read_text(encoding="utf-8")
                )
            except (OSError, UnicodeDecodeError):
                source_header_cache[path_key] = ""
        raw_header = source_header_cache[path_key]

    stored_scope = _field_value(raw_header, "Project Scope") if raw_header else None
    if stored_scope and _normalize_scope(stored_scope):
        # An explicit session assignment is authoritative. Prefix/substring
        # matches must not cross from (for example) Alpha into Alpha-Secret.
        return _normalize_scope(stored_scope) == normalized_scope

    content_for_scope = raw_header if document.document_type == "raw" else document.content
    metadata_text = "\n".join(
        (
            _relative_path(document.path, root),
            document.title,
            " ".join(document.tags),
            document.category or "",
            document.category_label or "",
            document.source or "",
            content_for_scope,
        )
    )
    if normalized_scope in _normalize_scope(metadata_text):
        return True
    if document.document_type != "raw_chunk":
        return False

    return normalized_scope in _normalize_scope(raw_header)


def _raw_session_header(content: str) -> str:
    match = re.search(r"^## (?:User|Assistant)[ \t]*$", content, flags=re.MULTILINE | re.IGNORECASE)
    return content[: match.start()] if match else content


def _filter_names(
    document_types: tuple[str, ...],
    tag: str | None,
    category: str | None,
    status: str | None,
    date: str | None,
    date_from: str | None,
    date_to: str | None,
    path: str | None,
    scope: str | None,
    speaker_role: str | None,
) -> tuple[str, ...]:
    names: list[str] = []
    if document_types:
        names.append("document_type")
    if tag:
        names.append("tag")
    if category:
        names.append("category")
    if status:
        names.append("status")
    if date:
        names.append("date")
    if date_from:
        names.append("date_from")
    if date_to:
        names.append("date_to")
    if path:
        names.append("path")
    if scope:
        names.append("scope")
    if speaker_role:
        names.append("speaker_role")
    return tuple(names)


def _normalize_path(value: str) -> str:
    return value.replace("\\", "/").lower()


def _normalize_scope(value: str) -> str:
    return "".join(value.lower().split())


def _sql_contains_pattern(value: str) -> str:
    escaped = _normalize_path(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _sql_scope_pattern(value: str) -> str:
    escaped = _normalize_scope(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _candidate_paths(root: Path) -> list[Path]:
    paths: list[Path] = []
    conversations = root / "conversations"
    if conversations.exists():
        paths.extend(path for path in conversations.rglob("*.md") if path.name in {"raw.md", "summary.md"})

    journal = root / "journal"
    if journal.exists():
        paths.extend(path for path in journal.rglob("*.md") if path.is_file())

    memory = root / "memory"
    if memory.exists():
        paths.extend(path for path in memory.glob("*.md") if path.name in SUPPORTED_MEMORY_FILES)
        items = memory / "items"
        if items.exists():
            paths.extend(path for path in items.glob("*.md") if path.is_file())

    return sorted(set(paths))


def _document_type(path: Path, root: Path) -> str | None:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return None

    parts = relative.parts
    if not parts:
        return None
    if parts[0] == "conversations" and path.name == "raw.md":
        return "raw"
    if parts[0] == "conversations" and path.name == "summary.md":
        return "summary"
    if parts[0] == "journal":
        return "journal"
    if parts[0] == "memory":
        if len(parts) >= 3 and parts[1] == "items":
            return "memory_item"
        return "memory"
    return None


def _raw_message_documents(document: MemoryDocument) -> list[MemoryDocument]:
    """Create searchable message-sized evidence from a role-headed raw.md.

    The complete raw file remains indexed for compatibility and manual search.
    ChatGPT exports can omit individual message timestamps, so a timestamp is
    optional; role headings and their source order remain sufficient evidence.
    Pasted files without role headings fall back to the whole-document index
    entry.
    """
    pattern = re.compile(
        r"^## (?P<role>User|Assistant)[ \t]*\n"
        r"(?:[ \t]*\n)*"
        r"(?:Timestamp:[ \t]*(?P<timestamp>[^\n]+)\n(?:[ \t]*\n)*)?"
        r"(?P<content>.*?)(?=^## (?:User|Assistant)[ \t]*$|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    chunks: list[MemoryDocument] = []
    for message_number, match in enumerate(pattern.finditer(document.content), start=1):
        role = match.group("role")
        timestamp_value = match.group("timestamp")
        timestamp = timestamp_value.strip() if timestamp_value else None
        content = match.group("content").strip()
        if not content:
            continue

        chunk_lines = [f"Session: {document.title}", f"Role: {role}"]
        if timestamp:
            chunk_lines.append(f"Timestamp: {timestamp}")
        chunk_lines.extend(("", content))

        chunks.append(
            MemoryDocument(
                document_key=f"{document.document_key}#message-{message_number:03d}-{role.lower()}",
                document_type="raw_chunk",
                path=document.path,
                title=f"{document.title} / {role} message {message_number}",
                date=document.date,
                tags=document.tags,
                content="\n".join(chunk_lines),
                speaker_role=role.lower(),
                message_number=message_number,
            )
        )
    return chunks


def _raw_chunk_metadata(document_key: str, document_type: str) -> tuple[str | None, int | None]:
    if document_type != "raw_chunk":
        return None, None
    match = re.search(r"#message-(\d+)-(user|assistant)$", document_key, re.IGNORECASE)
    if not match:
        return None, None
    return match.group(2).lower(), int(match.group(1))


def _extract_title(content: str, path: Path) -> str:
    session = _field_value(content, "Session")
    if session:
        return session

    imported_title = _field_value(content, "Title")
    if imported_title:
        return imported_title

    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or path.stem

    return path.stem


def _extract_date(content: str, path: Path, root: Path, document_type: str) -> str | None:
    field_date = _field_value(content, "Date")
    if field_date and re.fullmatch(r"\d{4}-\d{2}-\d{2}", field_date):
        return field_date

    if document_type == "journal":
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", path.stem):
            return path.stem

    try:
        relative = path.relative_to(root)
    except ValueError:
        relative = path

    for part in relative.parts:
        match = re.match(r"(\d{4}-\d{2}-\d{2})_", part)
        if match:
            return match.group(1)

    return None


def _extract_tags(content: str) -> list[str]:
    tags: list[str] = []
    in_tags = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            heading = stripped.lstrip("#").strip().lower()
            in_tags = heading in {"タグ", "tags", "tag"}
            continue
        if in_tags and stripped.startswith("-"):
            tag = stripped.lstrip("-").strip()
            if tag:
                tags.append(tag)
        elif in_tags and stripped.startswith("## "):
            break

    return _dedupe(tags)


def _field_value(content: str, name: str) -> str | None:
    pattern = re.compile(rf"^{re.escape(name)}:\s*(.+)$", re.MULTILINE)
    match = pattern.search(content)
    return match.group(1).strip() if match else None


def expand_query_variants(query: str) -> tuple[str, ...]:
    """Return deterministic OR-style query reformulations.

    Request wording is removed before matching so phrases such as ``教えて``
    never become the strongest term.  A quoted/nested personal topic is kept as
    a separate phrase. Product search deliberately has no fixed dictionary that
    maps a user's private topic to related names or phrases; interactive MCP
    callers handle genuine vocabulary mismatches through iterative queries.
    """

    stripped = query.strip()
    if not stripped:
        return ()

    cleaned = stripped
    for expression in REQUEST_EXPRESSIONS:
        cleaned = re.sub(re.escape(expression), " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"[?？!！]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    variants: list[str] = []
    if cleaned:
        variants.append(cleaned)

    # ``俺のXの感想`` and its common variants preserve X as a complete topic;
    # blindly splitting every Japanese particle can lose a multiword title.
    topic_patterns = (
        r"(?:俺|おれ|オレ|私|わたし|僕|ぼく|自分)の(.+?)の(?:感想|好み|意見|考え)",
        r"[\u300e「](.+?)[\u300f」]",
    )
    for pattern in topic_patterns:
        match = re.search(pattern, cleaned, flags=re.IGNORECASE)
        if match and len(match.group(1).strip()) >= 2:
            variants.append(match.group(1).strip())

    # A term-only variant catches punctuation and polite-wording differences.
    normalized_terms = _query_terms(cleaned)
    if normalized_terms:
        variants.append(" ".join(normalized_terms))
    return tuple(_dedupe(variants))


def _rank_documents(
    documents: list[MemoryDocument],
    query: str,
    limit: int,
    semantic_backend: LocalSemanticBackend | None = None,
    query_variants: tuple[str, ...] | None = None,
) -> list[MemorySearchResult]:
    variants = query_variants if query_variants is not None else expand_query_variants(query)
    if not variants and query.strip():
        variants = (query.strip(),)

    document_by_key = {document.document_key: document for document in documents}
    base_scores: dict[str, int] = defaultdict(int)
    rrf_scores: dict[str, float] = defaultdict(float)
    matched_terms: dict[str, list[str]] = defaultdict(list)

    if not variants:
        for document in documents:
            base_scores[document.document_key] = 1
    else:
        seen_term_sets: set[tuple[str, ...]] = set()
        for variant in variants:
            terms = _query_terms(variant)
            term_key = tuple(term.lower() for term in terms)
            if not terms or term_key in seen_term_sets:
                continue
            seen_term_sets.add(term_key)
            ranked_variant: list[tuple[MemoryDocument, int, list[str]]] = []
            for document in documents:
                score, evidence_terms = _score_with_evidence(document, terms)
                if score > 0:
                    ranked_variant.append((document, score, evidence_terms))
            ranked_variant.sort(
                key=lambda item: (
                    item[1],
                    _document_evidence_quality(item[0]),
                    item[0].date or "",
                    item[0].document_key,
                ),
                reverse=True,
            )
            for rank, (document, score, evidence_terms) in enumerate(ranked_variant, start=1):
                key = document.document_key
                base_scores[key] = max(base_scores[key], score)
                rrf_scores[key] += 1.0 / (RRF_K + rank)
                matched_terms[key].extend(evidence_terms)

    if semantic_backend is not None and query.strip():
        semantic_keys = semantic_backend.rank(query, tuple(documents), max(limit * 4, 20))
        seen_semantic: set[str] = set()
        for rank, key in enumerate(semantic_keys, start=1):
            if key in seen_semantic or key not in document_by_key:
                continue
            seen_semantic.add(key)
            rrf_scores[key] += 1.0 / (RRF_K + rank)
            base_scores.setdefault(key, 0)

    ranked_keys = sorted(
        base_scores,
        key=lambda key: (
            base_scores[key],
            rrf_scores[key],
            _document_evidence_quality(document_by_key[key]),
            document_by_key[key].date or "",
            key,
        ),
        reverse=True,
    )
    results: list[MemorySearchResult] = []
    for key in ranked_keys[: max(limit, 1)]:
        document = document_by_key[key]
        terms = _dedupe(matched_terms.get(key, []))
        score = base_scores[key] * 100 + round(rrf_scores[key] * 1000)
        results.append(
            MemorySearchResult(
                document_type=document.document_type,
                path=document.path,
                title=document.title,
                date=document.date,
                tags=document.tags,
                snippet=_snippet(
                    document.content,
                    terms,
                    width=2200 if document.document_type == "raw_chunk" else 160,
                ),
                score=max(score, 1),
                category=document.category,
                category_label=document.category_label,
                status=document.status,
                source=document.source,
                source_date=document.source_date,
                confidence=document.confidence,
                speaker_role=document.speaker_role,
                message_number=document.message_number,
            )
        )
    return results


def _score_with_evidence(document: MemoryDocument, terms: list[str]) -> tuple[int, list[str]]:
    if not terms:
        return 1, []

    text = f"{document.title}\n{' '.join(document.tags)}\n{document.content}".lower()
    score = 0
    evidence_terms: list[str] = []
    for term in terms:
        normalized_term = term.lower()
        count = text.count(normalized_term)
        if count:
            # Exact lexical evidence must stay stronger than a partial n-gram
            # resemblance (for example two different *_USER_SENTINEL values).
            score += 4 + min(count, 3)
            evidence_terms.append(term)
            continue
        ngram_score = _character_ngram_score(normalized_term, text)
        if ngram_score:
            score += ngram_score
            evidence_terms.append(term)

    if document.document_type in {"memory", "memory_item"} and score:
        score += 3
    if document.document_type == "memory_item" and score:
        score += 2
    if document.document_type == "summary" and score:
        score += 2
    if document.document_type == "raw_chunk" and score:
        # Detailed user messages are more useful evidence than a short question
        # that merely repeats the search wording.  Keep the bonus capped so a
        # long but weakly related transcript cannot dominate keyword matches.
        score += min(max((len(document.content) - 160) // 400, 0), 4)
    return score, evidence_terms


def _document_evidence_quality(document: MemoryDocument) -> int:
    """Tie-break toward first-party, substantive evidence."""

    quality = 0
    if document.document_type == "raw_chunk":
        quality += 2 if document.speaker_role == "user" else 0
        quality += min(len(document.content) // 300, 4)
    elif document.document_type == "raw":
        quality += min(len(document.content) // 600, 3)
    return quality


def _character_ngram_score(term: str, text: str) -> int:
    """Return a conservative typo/orthography score using character trigrams."""

    compact_term = _compact_search_text(term)
    if len(compact_term) < 4:
        return 0
    query_ngrams = {
        compact_term[index : index + 3]
        for index in range(len(compact_term) - 2)
    }
    if len(query_ngrams) < 2:
        return 0
    compact_text = _compact_search_text(text)
    overlap = sum(1 for ngram in query_ngrams if ngram in compact_text)
    ratio = overlap / len(query_ngrams)
    if overlap < 2 or ratio < 0.5:
        return 0
    return min(1 + overlap // 2, 2)


def _compact_search_text(value: str) -> str:
    return re.sub(r"[^0-9a-zA-Zぁ-んァ-ン一-鿿]+", "", value.lower())


def _snippet(content: str, terms: list[str], width: int = 160) -> str:
    text = " ".join(line.strip() for line in content.splitlines() if line.strip())
    if not text:
        return ""

    lower = text.lower()
    index = 0
    for term in terms:
        found = lower.find(term.lower())
        if found >= 0:
            index = found
            break

    start = max(index - width // 2, 0)
    end = min(start + width, len(text))
    snippet = text[start:end].strip()
    if start > 0:
        snippet = "..." + snippet
    if end < len(text):
        snippet += "..."
    return snippet


def _query_terms(query: str) -> list[str]:
    stripped = query.strip()
    if not stripped:
        return []

    normalized = stripped
    for expression in REQUEST_EXPRESSIONS:
        normalized = re.sub(re.escape(expression), " ", normalized, flags=re.IGNORECASE)
    terms = [
        term
        for term in re.split(r"[\s、。,.!?！？「」『』（）()\[\]【】:;：；]+", normalized)
        if term
    ]
    expanded: list[str] = []
    for term in terms:
        if 2 <= len(term) <= 80 and term.lower() not in QUERY_STOP_TERMS:
            expanded.append(term)
        expanded.extend(
            part
            for part in re.split(r"(?:の|は|を|に|が|で|と|や|も|へ|から|まで|です|ます)", term)
            if len(part) >= 2 and part.lower() not in QUERY_STOP_TERMS
        )
    return _dedupe(expanded)


def _dedupe(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = value.strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def _resolve_db_path(root: Path, db_path: Path | str | None) -> Path:
    if db_path is None:
        return default_index_path(root)

    path = Path(db_path)
    if not path.is_absolute():
        path = root / path
    return path


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_key TEXT NOT NULL UNIQUE,
            document_type TEXT NOT NULL,
            path TEXT NOT NULL,
            title TEXT NOT NULL,
            date TEXT,
            tags_json TEXT NOT NULL,
            category TEXT,
            category_label TEXT,
            status TEXT,
            source TEXT,
            source_date TEXT,
            confidence TEXT,
            speaker_role TEXT,
            message_number INTEGER,
            content TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE tags (
            document_id INTEGER NOT NULL,
            tag TEXT NOT NULL,
            FOREIGN KEY(document_id) REFERENCES documents(id)
        );

        CREATE INDEX idx_documents_type ON documents(document_type);
        CREATE INDEX idx_documents_date ON documents(date);
        CREATE INDEX idx_documents_category ON documents(category);
        CREATE INDEX idx_documents_status ON documents(status);
        CREATE INDEX idx_tags_tag ON tags(tag);

        CREATE TABLE indexed_sources (
            path TEXT PRIMARY KEY,
            mtime_ns INTEGER NOT NULL,
            size INTEGER NOT NULL
        );

        CREATE TABLE index_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )

    connection.executemany(
        "INSERT INTO index_metadata(key, value) VALUES (?, ?)",
        INDEX_VERSION_METADATA.items(),
    )

    try:
        connection.execute(
            "CREATE VIRTUAL TABLE documents_fts USING fts5(title, content, path, content='documents', content_rowid='id')"
        )
    except sqlite3.OperationalError:
        connection.execute("CREATE TABLE documents_fts_unavailable (reason TEXT)")
        connection.execute("INSERT INTO documents_fts_unavailable(reason) VALUES ('fts5 unavailable')")


def _insert_documents(connection: sqlite3.Connection, root: Path, documents: list[MemoryDocument]) -> None:
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    has_fts = _has_fts(connection)

    for document in documents:
        cursor = connection.execute(
            """
            INSERT INTO documents(
                document_key, document_type, path, title, date, tags_json,
                category, category_label, status, source, source_date, confidence,
                speaker_role, message_number, content, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document.document_key,
                document.document_type,
                _relative_path(document.path, root),
                document.title,
                document.date,
                json.dumps(list(document.tags), ensure_ascii=False),
                document.category,
                document.category_label,
                document.status,
                document.source,
                document.source_date,
                document.confidence,
                document.speaker_role,
                document.message_number,
                document.content,
                now,
            ),
        )
        document_id = int(cursor.lastrowid)
        for tag in document.tags:
            connection.execute("INSERT INTO tags(document_id, tag) VALUES (?, ?)", (document_id, tag))
        if has_fts:
            connection.execute(
                "INSERT INTO documents_fts(rowid, title, content, path) VALUES (?, ?, ?, ?)",
                (document_id, document.title, document.content, _relative_path(document.path, root)),
            )


def _insert_source_manifest(connection: sqlite3.Connection, root: Path) -> None:
    for path in _candidate_paths(root):
        try:
            stat = path.stat()
        except OSError:
            continue
        connection.execute(
            "INSERT INTO indexed_sources(path, mtime_ns, size) VALUES (?, ?, ?)",
            (_relative_path(path, root), stat.st_mtime_ns, stat.st_size),
        )


def _has_fts(connection: sqlite3.Connection) -> bool:
    cursor = connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='documents_fts'")
    return cursor.fetchone() is not None


def _load_documents_from_index(
    root: Path,
    db_path: Path,
    document_types: tuple[str, ...] = (),
    tag: str | None = None,
    category: str | None = None,
    status: str | None = None,
    date: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    path: str | None = None,
    scope: str | None = None,
    speaker_role: str | None = None,
) -> tuple[list[MemoryDocument], float, float]:
    index_load_started_at = perf_counter()
    with closing(sqlite3.connect(db_path)) as connection:
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(documents)").fetchall()}
    index_load_ms = (perf_counter() - index_load_started_at) * 1000
    structured_columns = {"category", "category_label", "status", "source", "source_date", "confidence"}
    message_columns = {"speaker_role", "message_number"}
    has_structured_columns = structured_columns.issubset(columns)
    has_message_columns = message_columns.issubset(columns)
    if (category or status) and not has_structured_columns:
        return [], index_load_ms, 0.0

    filter_started_at = perf_counter()
    structured_select = (
        "d.category, d.category_label, d.status, d.source, d.source_date, d.confidence"
        if has_structured_columns
        else "NULL, NULL, NULL, NULL, NULL, NULL"
    )
    message_select = "d.speaker_role, d.message_number" if has_message_columns else "NULL, NULL"
    query = [
        "SELECT d.document_key, d.document_type, d.path, d.title, d.date, d.tags_json,",
        f"       {structured_select}, {message_select}, d.content",
        "FROM documents d",
    ]
    params: list[object] = []
    if tag:
        query.append("JOIN tags t ON t.document_id = d.id")

    where: list[str] = []
    if document_types:
        placeholders = ",".join("?" for _ in document_types)
        where.append(f"d.document_type IN ({placeholders})")
        params.extend(document_types)
    if tag:
        where.append("t.tag = ?")
        params.append(tag)
    if category:
        where.append("d.category = ?")
        params.append(category)
    if status:
        where.append("d.status = ?")
        params.append(status)
    if date:
        where.append("d.date = ?")
        params.append(date)
    if date_from:
        where.append("d.date >= ?")
        params.append(date_from)
    if date_to:
        where.append("d.date <= ?")
        params.append(date_to)
    if path:
        where.append("LOWER(REPLACE(d.path, CHAR(92), '/')) LIKE ? ESCAPE '\\'")
        params.append(_sql_contains_pattern(path))
    if speaker_role:
        normalized_role = speaker_role.strip().lower()
        if has_message_columns:
            where.append("LOWER(COALESCE(d.speaker_role, '')) = ?")
            params.append(normalized_role)
        else:
            # Legacy indexes did not have speaker_role but message chunks still
            # encode it at the end of their stable document key.
            where.append("LOWER(d.document_key) LIKE ?")
            params.append(f"%-{normalized_role}")
    if scope:
        scope_columns = ["d.path", "d.title", "d.tags_json", "d.content"]
        if has_structured_columns:
            scope_columns.extend(("d.category", "d.category_label", "d.source"))
        scope_clauses = [
            "LOWER(REPLACE(REPLACE(REPLACE(REPLACE("
            f"COALESCE({column}, ''), ' ', ''), CHAR(9), ''), CHAR(10), ''), CHAR(13), '')) "
            "LIKE ? ESCAPE '\\'"
            for column in scope_columns
        ]
        scope_clauses.append(
            "EXISTS (SELECT 1 FROM documents scope_document "
            "WHERE scope_document.path = d.path "
            "AND scope_document.document_type != 'raw_chunk' "
            "AND LOWER(REPLACE(REPLACE(REPLACE(REPLACE("
            "COALESCE(scope_document.content, ''), ' ', ''), CHAR(9), ''), CHAR(10), ''), CHAR(13), '')) "
            "LIKE ? ESCAPE '\\')"
        )
        where.append("(" + " OR ".join(scope_clauses) + ")")
        params.extend(_sql_scope_pattern(scope) for _ in range(len(scope_columns) + 1))
    if where:
        query.append("WHERE " + " AND ".join(where))
    query.append("ORDER BY COALESCE(d.date, ''), d.path")

    with closing(sqlite3.connect(db_path)) as connection:
        rows = connection.execute("\n".join(query), params).fetchall()

    documents: list[MemoryDocument] = []
    for (
        document_key,
        document_type,
        path_text,
        title,
        date,
        tags_json,
        category_value,
        category_label,
        status_value,
        source,
        source_date,
        confidence,
        speaker_role,
        message_number,
        content,
    ) in rows:
        tags = tuple(json.loads(tags_json))
        fallback_role, fallback_number = _raw_chunk_metadata(str(document_key), str(document_type))
        documents.append(
            MemoryDocument(
                document_key=str(document_key),
                document_type=str(document_type),
                path=root / str(path_text),
                title=str(title),
                date=str(date) if date else None,
                tags=tags,
                content=str(content),
                category=str(category_value) if category_value else None,
                category_label=str(category_label) if category_label else None,
                status=str(status_value) if status_value else None,
                source=str(source) if source else None,
                source_date=str(source_date) if source_date else None,
                confidence=str(confidence) if confidence else None,
                speaker_role=str(speaker_role) if speaker_role else fallback_role,
                message_number=int(message_number) if message_number is not None else fallback_number,
            )
        )
    filter_ms = (perf_counter() - filter_started_at) * 1000
    return documents, index_load_ms, filter_ms


def _relative_path(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)
