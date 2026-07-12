"""Run repeatable, synthetic search benchmarks for AI-LifeOS.

The benchmark never reads conversations, journal, or memory from the current
repository.  It creates a temporary SQLite index from generated documents and
deletes it at the end of each data-size run.  This keeps performance evaluation
safe to run in PublicEdition and makes results comparable between machines.
"""

import argparse
import json
import re
import sqlite3
import tempfile
from contextlib import closing
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import median
from time import perf_counter

from memory_index import (
    MemoryDocument,
    _create_schema,
    _insert_documents,
    default_index_path,
    search_memory_with_profile,
)


DEFAULT_SIZES = (100, 1000, 5000)
DEFAULT_QUERY = "長期検索"


@dataclass(frozen=True)
class JapaneseComparison:
    method: str
    median_ms: float | None
    matched_documents: int | None
    available: bool
    note: str = ""


@dataclass(frozen=True)
class BenchmarkResult:
    document_count: int
    index_build_ms: float
    runs: int
    candidate_count: int
    result_count: int
    index_load_ms: float
    filter_ms: float
    ranking_ms: float
    total_ms: float
    japanese_comparison: tuple[JapaneseComparison, ...] = ()

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["japanese_comparison"] = [asdict(item) for item in self.japanese_comparison]
        return data


def synthetic_documents(root: Path, document_count: int) -> list[MemoryDocument]:
    """Create deterministic documents with a realistic metadata distribution."""
    documents: list[MemoryDocument] = []
    for number in range(document_count):
        date = f"2026-{number % 12 + 1:02d}-{number % 28 + 1:02d}"
        is_target = number % 10 == 0
        if is_target:
            relative = Path("memory") / "items" / f"benchmark_{number:06d}.md"
            document_type = "memory_item"
            title = "長期検索ベンチマーク対象"
            tags = ("benchmark", "benchmark-target", "日本語")
            category = "search_benchmark"
            category_label = "検索ベンチマーク"
            status = "active"
            content = (
                f"長期検索の品質と速度を確認する合成メモリ項目 {number}。"
                "SQLite filter と Python ranking の計測対象です。"
            )
        else:
            relative = (
                Path("conversations")
                / "2026"
                / f"{number % 12 + 1:02d}"
                / f"synthetic_{number:06d}"
                / "summary.md"
            )
            document_type = "summary"
            title = f"合成会話ログ {number}"
            tags = ("benchmark", "synthetic")
            category = None
            category_label = None
            status = None
            content = f"長期運用を想定した合成会話ログ {number}。一般的な検索候補です。"

        documents.append(
            MemoryDocument(
                document_key=relative.as_posix(),
                document_type=document_type,
                path=root / relative,
                title=title,
                date=date,
                tags=tags,
                content=content,
                category=category,
                category_label=category_label,
                status=status,
                source="synthetic/benchmark.md" if is_target else None,
                source_date=date if is_target else None,
                confidence="synthetic" if is_target else None,
            )
        )
    return documents


def build_synthetic_index(root: Path, document_count: int) -> tuple[Path, float]:
    """Build the normal search schema from generated documents only."""
    db_path = default_index_path(root)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    documents = synthetic_documents(root, document_count)
    started_at = perf_counter()
    with closing(sqlite3.connect(db_path)) as connection:
        _create_schema(connection)
        _insert_documents(connection, root, documents)
        connection.commit()
    return db_path, (perf_counter() - started_at) * 1000


def run_benchmark(
    document_count: int,
    query: str = DEFAULT_QUERY,
    runs: int = 5,
    compare_japanese: bool = False,
) -> BenchmarkResult:
    """Benchmark an indexed, metadata-filtered search against synthetic data."""
    if document_count < 1:
        raise ValueError("document_count must be at least 1")
    if runs < 1:
        raise ValueError("runs must be at least 1")

    with tempfile.TemporaryDirectory(prefix="ai_lifeos_search_benchmark_") as temporary_dir:
        root = Path(temporary_dir)
        db_path, index_build_ms = build_synthetic_index(root, document_count)
        profiles = []
        for _ in range(runs):
            _, profile = search_memory_with_profile(
                root=root,
                db_path=db_path,
                query=query,
                limit=10,
                document_types=("memory_item",),
                tag="benchmark-target",
                category="search_benchmark",
                status="active",
                date_from="2026-01-01",
                date_to="2026-12-31",
                path="memory/items",
            )
            profiles.append(profile)

        comparison = (
            tuple(_compare_japanese_methods(root, db_path, query, runs)) if compare_japanese else ()
        )
        return BenchmarkResult(
            document_count=document_count,
            index_build_ms=index_build_ms,
            runs=runs,
            candidate_count=int(median(profile.candidate_count for profile in profiles)),
            result_count=int(median(profile.result_count for profile in profiles)),
            index_load_ms=median(profile.index_load_ms for profile in profiles),
            filter_ms=median(profile.filter_ms for profile in profiles),
            ranking_ms=median(profile.ranking_ms for profile in profiles),
            total_ms=median(profile.total_ms for profile in profiles),
            japanese_comparison=comparison,
        )


def run_benchmarks(
    sizes: tuple[int, ...] = DEFAULT_SIZES,
    query: str = DEFAULT_QUERY,
    runs: int = 5,
    compare_japanese: bool = False,
) -> list[BenchmarkResult]:
    return [
        run_benchmark(size, query=query, runs=runs, compare_japanese=compare_japanese)
        for size in sizes
    ]


def _compare_japanese_methods(
    root: Path,
    db_path: Path,
    query: str,
    runs: int,
) -> list[JapaneseComparison]:
    """Compare candidate retrieval methods without changing the production path.

    The N-gram table exists only in the temporary benchmark database.  FTS5 is
    queried only when SQLite provided it.  Neither becomes a production search
    dependency through this comparison.
    """
    python_times: list[float] = []
    python_matches: list[int] = []
    for _ in range(runs):
        results, profile = search_memory_with_profile(
            root=root,
            db_path=db_path,
            query=query,
            limit=1_000_000,
        )
        python_times.append(profile.total_ms)
        python_matches.append(len(results))

    methods = [
        JapaneseComparison(
            method="python_partial_match_rank",
            median_ms=median(python_times),
            matched_documents=int(median(python_matches)),
            available=True,
            note="Production baseline",
        ),
        _compare_sql_like(db_path, query, runs),
    ]

    _build_benchmark_bigrams(db_path)
    methods.append(_compare_bigrams(db_path, query, runs))
    methods.append(_compare_fts5(db_path, query, runs))
    return methods


def _compare_sql_like(db_path: Path, query: str, runs: int) -> JapaneseComparison:
    return _time_sql_count(
        db_path,
        "SELECT COUNT(*) FROM documents WHERE LOWER(title) LIKE ? OR LOWER(content) LIKE ?",
        (f"%{query.lower()}%", f"%{query.lower()}%"),
        method="sqlite_like",
        runs=runs,
        note="Candidate retrieval only; no relevance ranking",
    )


def _build_benchmark_bigrams(db_path: Path) -> None:
    with closing(sqlite3.connect(db_path)) as connection:
        connection.executescript(
            """
            CREATE TABLE benchmark_bigrams (
                document_id INTEGER NOT NULL,
                gram TEXT NOT NULL,
                PRIMARY KEY(document_id, gram)
            ) WITHOUT ROWID;
            CREATE INDEX idx_benchmark_bigrams_gram_document
                ON benchmark_bigrams(gram, document_id);
            """
        )
        rows = connection.execute("SELECT id, title, content FROM documents").fetchall()
        values = [
            (int(document_id), gram)
            for document_id, title, content in rows
            for gram in _bigrams(f"{title}\n{content}")
        ]
        connection.executemany(
            "INSERT INTO benchmark_bigrams(document_id, gram) VALUES (?, ?)", values
        )
        connection.commit()


def _compare_bigrams(db_path: Path, query: str, runs: int) -> JapaneseComparison:
    grams = sorted(_bigrams(query))
    if not grams:
        return JapaneseComparison(
            method="sqlite_bigram_auxiliary",
            median_ms=None,
            matched_documents=None,
            available=False,
            note="Query needs at least two non-space characters",
        )
    placeholders = ",".join("?" for _ in grams)
    sql = (
        "SELECT COUNT(*) FROM ("
        "SELECT document_id FROM benchmark_bigrams "
        f"WHERE gram IN ({placeholders}) "
        "GROUP BY document_id HAVING COUNT(DISTINCT gram) = ?"
        ")"
    )
    return _time_sql_count(
        db_path,
        sql,
        tuple([*grams, len(grams)]),
        method="sqlite_bigram_auxiliary",
        runs=runs,
        note="Benchmark-only auxiliary table; candidate retrieval only",
    )


def _compare_fts5(db_path: Path, query: str, runs: int) -> JapaneseComparison:
    with closing(sqlite3.connect(db_path)) as connection:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'documents_fts'"
        ).fetchone()
    if not exists:
        return JapaneseComparison(
            method="sqlite_fts5_default_tokenizer",
            median_ms=None,
            matched_documents=None,
            available=False,
            note="FTS5 is unavailable in this SQLite build",
        )
    try:
        return _time_sql_count(
            db_path,
            "SELECT COUNT(*) FROM documents_fts WHERE documents_fts MATCH ?",
            (query,),
            method="sqlite_fts5_default_tokenizer",
            runs=runs,
            note="Default tokenizer only; not the production path",
        )
    except sqlite3.OperationalError as error:
        return JapaneseComparison(
            method="sqlite_fts5_default_tokenizer",
            median_ms=None,
            matched_documents=None,
            available=False,
            note=str(error),
        )


def _time_sql_count(
    db_path: Path,
    sql: str,
    params: tuple[object, ...],
    method: str,
    runs: int,
    note: str,
) -> JapaneseComparison:
    times: list[float] = []
    matched_documents: list[int] = []
    for _ in range(runs):
        started_at = perf_counter()
        with closing(sqlite3.connect(db_path)) as connection:
            count = connection.execute(sql, params).fetchone()[0]
        times.append((perf_counter() - started_at) * 1000)
        matched_documents.append(int(count))
    return JapaneseComparison(
        method=method,
        median_ms=median(times),
        matched_documents=int(median(matched_documents)),
        available=True,
        note=note,
    )


def _bigrams(text: str) -> set[str]:
    normalized = re.sub(r"\s+", "", text.lower())
    return {normalized[index : index + 2] for index in range(len(normalized) - 1)}


def _parse_sizes(value: str) -> tuple[int, ...]:
    try:
        sizes = tuple(int(item.strip()) for item in value.split(",") if item.strip())
    except ValueError as error:
        raise argparse.ArgumentTypeError("--sizes must be comma-separated positive integers") from error
    if not sizes or any(size < 1 for size in sizes):
        raise argparse.ArgumentTypeError("--sizes must contain at least one positive integer")
    return sizes


def _format_result(result: BenchmarkResult) -> str:
    lines = [
        f"## {result.document_count} synthetic documents",
        f"  index build: {result.index_build_ms:.3f} ms (not included in search total)",
        f"  filtered candidates/results: {result.candidate_count}/{result.result_count}",
        f"  median of {result.runs} indexed searches:",
        f"    index load: {result.index_load_ms:.3f} ms",
        f"    SQL filter: {result.filter_ms:.3f} ms",
        f"    Python ranking: {result.ranking_ms:.3f} ms",
        f"    total: {result.total_ms:.3f} ms",
    ]
    if result.japanese_comparison:
        lines.append("  Japanese candidate retrieval comparison:")
        for item in result.japanese_comparison:
            if item.available:
                lines.append(
                    f"    {item.method}: {item.median_ms:.3f} ms, "
                    f"matches={item.matched_documents} ({item.note})"
                )
            else:
                lines.append(f"    {item.method}: unavailable ({item.note})")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Benchmark AI-LifeOS search with temporary synthetic data only.")
    parser.add_argument(
        "--sizes",
        type=_parse_sizes,
        default=DEFAULT_SIZES,
        help="Comma-separated document counts (default: 100,1000,5000).",
    )
    parser.add_argument("--query", default=DEFAULT_QUERY, help="Japanese benchmark query.")
    parser.add_argument("--runs", type=int, default=5, help="Searches per data size; median is reported.")
    parser.add_argument(
        "--compare-japanese",
        action="store_true",
        help="Also compare Python rank, SQLite LIKE, temporary bigrams, and default FTS5.",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument(
        "--output",
        help="Optional path to save the JSON result. Keep local benchmark results out of Git.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.runs < 1:
        raise SystemExit("--runs must be at least 1")
    results = run_benchmarks(
        sizes=args.sizes,
        query=args.query,
        runs=args.runs,
        compare_japanese=args.compare_japanese,
    )
    payload = {
        "dataset": "temporary synthetic documents only",
        "query": args.query,
        "results": [result.to_dict() for result in results],
    }
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("Synthetic benchmark only: no local conversations, journal, memory, or index are read or retained.")
        for result in results:
            print()
            print(_format_result(result))
        if args.output:
            print(f"\nJSON result written to: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
