import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIRECTORIES = (
    "conversations",
    "journal",
    "memory",
    "inbox",
    "tasks",
    "imports",
    "logs",
)
SEARCH_INDEX_PATH = Path("memory") / "search_index.sqlite3"
PLACEHOLDER_FILES = {".gitkeep"}


def build_local_data_report(root: Path | str = ROOT) -> dict[str, Any]:
    root = Path(root)
    directories = {name: _directory_report(root=root, name=name) for name in REPORT_DIRECTORIES}
    search_index = _file_report(root=root, relative_path=SEARCH_INDEX_PATH)

    return {
        "root": str(root),
        "read_only": True,
        "directories": directories,
        "search_index": search_index,
        "totals": {
            "existing_directories": sum(1 for item in directories.values() if item["exists"]),
            "file_count": sum(int(item["file_count"]) for item in directories.values()),
            "total_bytes": sum(int(item["total_bytes"]) for item in directories.values()),
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Report local AI-LifeOS personal data footprint without modifying files.")
    parser.add_argument("--root", default=ROOT, help="AI-LifeOS root directory.")
    parser.add_argument("--json", action="store_true", help="Print JSON output for GUI bridge use.")
    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_stdout()
    args = build_parser().parse_args(argv)
    report = build_local_data_report(root=args.root)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    print(_format_text_report(report))
    return 0


def _directory_report(root: Path, name: str) -> dict[str, Any]:
    path = root / name
    result: dict[str, Any] = {
        "path": name,
        "exists": path.exists(),
        "file_count": 0,
        "directory_count": 0,
        "total_bytes": 0,
        "newest_file": None,
        "newest_modified_at": None,
        "errors": [],
    }
    if not path.exists():
        return result
    if not path.is_dir():
        result["errors"].append("path is not a directory")
        return result

    newest: tuple[float, Path] | None = None
    try:
        entries = list(path.rglob("*"))
    except OSError as exc:
        result["errors"].append(str(exc))
        return result

    for entry in entries:
        try:
            if entry.is_dir():
                result["directory_count"] += 1
                continue
            if not entry.is_file() or entry.name in PLACEHOLDER_FILES:
                continue

            stat = entry.stat()
        except OSError as exc:
            result["errors"].append(f"{_display_path(entry, root)}: {exc}")
            continue

        result["file_count"] += 1
        result["total_bytes"] += stat.st_size
        if newest is None or stat.st_mtime > newest[0]:
            newest = (stat.st_mtime, entry)

    if newest is not None:
        result["newest_file"] = _display_path(newest[1], root)
        result["newest_modified_at"] = _iso_from_timestamp(newest[0])

    return result


def _file_report(root: Path, relative_path: Path) -> dict[str, Any]:
    path = root / relative_path
    result: dict[str, Any] = {
        "path": str(relative_path),
        "exists": path.exists(),
        "size_bytes": 0,
        "modified_at": None,
    }
    if not path.exists() or not path.is_file():
        return result

    try:
        stat = path.stat()
    except OSError as exc:
        result["error"] = str(exc)
        return result

    result["size_bytes"] = stat.st_size
    result["modified_at"] = _iso_from_timestamp(stat.st_mtime)
    return result


def _format_text_report(report: dict[str, Any]) -> str:
    lines = [
        f"Root: {report['root']}",
        "Read-only: true",
        "",
        "Directories:",
    ]
    for name, item in report["directories"].items():
        lines.append(
            f"- {name}: exists={item['exists']} files={item['file_count']} dirs={item['directory_count']} bytes={item['total_bytes']}"
        )
    search_index = report["search_index"]
    lines.extend(
        [
            "",
            f"Search index: exists={search_index['exists']} path={search_index['path']} bytes={search_index['size_bytes']}",
        ]
    )
    return "\n".join(lines)


def _display_path(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def _iso_from_timestamp(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).astimezone().isoformat(timespec="seconds")


def _configure_stdout() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
