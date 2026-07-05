import argparse
from pathlib import Path

from memory_index import ROOT, collect_documents, default_index_path, rebuild_index


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build the AI-LifeOS SQLite memory index.")
    parser.add_argument("--root", default=ROOT, help="AI-LifeOS root directory.")
    parser.add_argument("--db", help="SQLite index path. Defaults to memory/search_index.sqlite3.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    root = Path(args.root)
    db_path = rebuild_index(root=root, db_path=args.db)
    count = len(collect_documents(root))
    display = db_path if args.db else default_index_path(root)
    print(f"Indexed {count} documents.")
    print(f"DB: {display}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

