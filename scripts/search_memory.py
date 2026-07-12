import argparse
import json
from pathlib import Path

from memory_index import ROOT, MemorySearchResult, SearchProfile, rebuild_index, search_memory, search_memory_with_profile


def format_result(result: MemorySearchResult, root: Path) -> str:
    try:
        display_path = str(result.path.resolve().relative_to(root.resolve()))
    except ValueError:
        display_path = str(result.path)

    parts = [
        f"{display_path}",
        f"  type: {result.document_type}",
        f"  title: {result.title}",
    ]
    if result.date:
        parts.append(f"  date: {result.date}")
    if result.tags:
        parts.append(f"  tags: {', '.join(result.tags)}")
    if result.category:
        parts.append(f"  category: {result.category} ({result.category_label or result.category})")
    if result.status:
        parts.append(f"  status: {result.status}")
    if result.source:
        parts.append(f"  source: {result.source}")
    if result.confidence:
        parts.append(f"  confidence: {result.confidence}")
    if result.snippet:
        parts.append(f"  snippet: {result.snippet}")
    return "\n".join(parts)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Search AI-LifeOS conversations, journal, and memory.")
    parser.add_argument("query", nargs="?", default="", help="Search query.")
    parser.add_argument("--root", default=ROOT, help="AI-LifeOS root directory.")
    parser.add_argument("--limit", type=int, default=10, help="Maximum number of results.")
    parser.add_argument("--type", action="append", dest="document_types", help="Filter by document type.")
    parser.add_argument("--tag", help="Filter by tag.")
    parser.add_argument("--category", help="Filter structured memory by category slug.")
    parser.add_argument("--status", help="Filter structured memory by status.")
    parser.add_argument("--date", help="Filter by an exact document date (YYYY-MM-DD).")
    parser.add_argument("--from-date", dest="date_from", help="Filter documents dated on or after YYYY-MM-DD.")
    parser.add_argument("--to-date", dest="date_to", help="Filter documents dated on or before YYYY-MM-DD.")
    parser.add_argument("--path", dest="path_filter", help="Filter by a relative-path substring.")
    parser.add_argument("--db", help="SQLite index path. Defaults to memory/search_index.sqlite3.")
    parser.add_argument("--no-index", action="store_true", help="Search Markdown files directly.")
    parser.add_argument("--rebuild-index", action="store_true", help="Rebuild SQLite index before searching.")
    parser.add_argument("--profile", action="store_true", help="Show index loading, filtering, and ranking timings.")
    parser.add_argument("--json", action="store_true", help="Print JSON output.")
    return parser


def format_profile(profile: SearchProfile) -> str:
    source_label = {
        "sqlite": "SQLite index",
        "markdown": "Markdown scan",
        "markdown-fallback": "Markdown fallback for legacy index",
    }.get(profile.source, profile.source)
    filters = ", ".join(profile.filters) if profile.filters else "none"
    return "\n".join(
        (
            "## Search Profile",
            f"  source: {source_label}",
            f"  filters: {filters}",
            f"  candidates: {profile.candidate_count}",
            f"  results: {profile.result_count}",
            f"  index load: {profile.index_load_ms:.3f} ms",
            f"  filter: {profile.filter_ms:.3f} ms",
            f"  ranking: {profile.ranking_ms:.3f} ms",
            f"  total: {profile.total_ms:.3f} ms",
        )
    )


def main() -> int:
    args = build_parser().parse_args()
    root = Path(args.root)

    if args.rebuild_index:
        rebuild_index(root=root, db_path=args.db)

    search_kwargs = {
        "root": root,
        "query": args.query,
        "db_path": args.db,
        "limit": args.limit,
        "document_types": args.document_types,
        "tag": args.tag,
        "category": args.category,
        "status": args.status,
        "date": args.date,
        "date_from": args.date_from,
        "date_to": args.date_to,
        "path": args.path_filter,
        "use_index": not args.no_index,
    }
    profile = None
    if args.profile:
        results, profile = search_memory_with_profile(**search_kwargs)
    else:
        results = search_memory(**search_kwargs)

    if args.json:
        print(
            json.dumps(
                {
                    "results": [_result_to_dict(result, root) for result in results],
                    "profile": profile.to_dict(),
                }
                if profile
                else [_result_to_dict(result, root) for result in results],
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if not results:
        print("No results.")
        if profile:
            print()
            print(format_profile(profile))
        return 0

    for index, result in enumerate(results, start=1):
        print(f"## Result {index}")
        print(format_result(result, root))
        print()
    if profile:
        print(format_profile(profile))
    return 0


def _result_to_dict(result: MemorySearchResult, root: Path) -> dict[str, object]:
    return {
        "path": str(result.path.resolve().relative_to(root.resolve())),
        "document_type": result.document_type,
        "title": result.title,
        "date": result.date,
        "tags": list(result.tags),
        "category": result.category,
        "category_label": result.category_label,
        "status": result.status,
        "source": result.source,
        "source_date": result.source_date,
        "confidence": result.confidence,
        "snippet": result.snippet,
        "score": result.score,
    }


if __name__ == "__main__":
    raise SystemExit(main())
