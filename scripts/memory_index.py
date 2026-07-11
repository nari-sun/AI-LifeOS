import json
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from memory_items import read_memory_item


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INDEX_PATH = Path("memory") / "search_index.sqlite3"
SUPPORTED_MEMORY_FILES = {"long_term.md", "preferences.md", "projects.md"}


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


def default_index_path(root: Path | str = ROOT) -> Path:
    return Path(root) / DEFAULT_INDEX_PATH


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

        document = MemoryDocument(
            document_key=relative,
            document_type=document_type,
            path=path,
            title=structured.category_label if structured else _extract_title(content, path),
            date=structured.source_date if structured else _extract_date(content, path, root, document_type),
            tags=structured.tags if structured else tuple(_extract_tags(content)),
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
    use_index: bool = True,
) -> list[MemorySearchResult]:
    root = Path(root)
    db_file = _resolve_db_path(root, db_path)
    type_filter = tuple(document_types or ())

    if use_index and db_file.exists():
        documents = _load_documents_from_index(
            root=root,
            db_path=db_file,
            document_types=type_filter,
            tag=tag,
            category=category,
            status=status,
        )
        # Existing indexes predate message-level raw evidence.  Keep them usable
        # without requiring a manual rebuild before the first answer after an
        # upgrade.
        if "raw_chunk" in type_filter and not documents:
            documents = [
                document
                for document in collect_documents(root)
                if document.document_type == "raw_chunk"
            ]
    else:
        documents = collect_documents(root)
        if type_filter:
            documents = [document for document in documents if document.document_type in type_filter]
        if tag:
            documents = [document for document in documents if tag in document.tags]
        if category:
            documents = [document for document in documents if document.category == category]
        if status:
            documents = [document for document in documents if document.status == status]

    return _rank_documents(documents, query=query, limit=limit)


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
    """Create searchable message-sized evidence from a finalized live-chat raw.md.

    The complete raw file remains indexed for compatibility and manual search.
    Pasted or imported files without the live-chat headings fall back to that
    whole-document index entry.
    """
    pattern = re.compile(
        r"^## (?P<role>User|Assistant)\s*\n\s*Timestamp:\s*(?P<timestamp>[^\n]+)\s*\n\s*"
        r"(?P<content>.*?)(?=^## (?:User|Assistant)\s*$|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    chunks: list[MemoryDocument] = []
    for message_number, match in enumerate(pattern.finditer(document.content), start=1):
        role = match.group("role")
        timestamp = match.group("timestamp").strip()
        content = match.group("content").strip()
        if not content:
            continue

        chunks.append(
            MemoryDocument(
                document_key=f"{document.document_key}#message-{message_number:03d}-{role.lower()}",
                document_type="raw_chunk",
                path=document.path,
                title=f"{document.title} / {role} message {message_number}",
                date=document.date,
                tags=document.tags,
                content="\n".join(
                    (
                        f"Session: {document.title}",
                        f"Role: {role}",
                        f"Timestamp: {timestamp}",
                        "",
                        content,
                    )
                ),
            )
        )
    return chunks


def _extract_title(content: str, path: Path) -> str:
    session = _field_value(content, "Session")
    if session:
        return session

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


def _rank_documents(documents: list[MemoryDocument], query: str, limit: int) -> list[MemorySearchResult]:
    terms = _query_terms(query)
    results: list[MemorySearchResult] = []

    for document in documents:
        score = _score(document, terms)
        if terms and score <= 0:
            continue
        results.append(
            MemorySearchResult(
                document_type=document.document_type,
                path=document.path,
                title=document.title,
                date=document.date,
                tags=document.tags,
                snippet=_snippet(document.content, terms, width=2200 if document.document_type == "raw_chunk" else 160),
                score=score,
                category=document.category,
                category_label=document.category_label,
                status=document.status,
                source=document.source,
                source_date=document.source_date,
                confidence=document.confidence,
            )
        )

    results.sort(key=lambda result: (result.score, result.date or "", str(result.path)), reverse=True)
    return results[: max(limit, 1)]


def _score(document: MemoryDocument, terms: list[str]) -> int:
    if not terms:
        return 1

    text = f"{document.title}\n{' '.join(document.tags)}\n{document.content}".lower()
    score = 0
    for term in terms:
        score += text.count(term.lower())

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
    return score


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

    terms = [
        term
        for term in re.split(r"[\s、。,.!?！？「」『』（）()\[\]【】]+", stripped)
        if term
    ]
    expanded: list[str] = []
    for term in terms:
        expanded.extend(
            part
            for part in re.split(r"(?:の|は|を|に|が|で|と|や|も|へ|から|まで|です|ます)", term)
            if len(part) >= 2
        )
    return _dedupe(expanded or terms)


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
        """
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
                content, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
) -> list[MemoryDocument]:
    with closing(sqlite3.connect(db_path)) as connection:
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(documents)").fetchall()}
    structured_columns = {"category", "category_label", "status", "source", "source_date", "confidence"}
    has_structured_columns = structured_columns.issubset(columns)
    if (category or status) and not has_structured_columns:
        return []

    structured_select = (
        "d.category, d.category_label, d.status, d.source, d.source_date, d.confidence"
        if has_structured_columns
        else "NULL, NULL, NULL, NULL, NULL, NULL"
    )
    query = [
        "SELECT d.document_key, d.document_type, d.path, d.title, d.date, d.tags_json,",
        f"       {structured_select}, d.content",
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
        content,
    ) in rows:
        tags = tuple(json.loads(tags_json))
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
            )
        )
    return documents


def _relative_path(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)
