import argparse
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
CATEGORY_EXAMPLE = Path("config") / "memory_categories.example.json"
PERSONAL_CATEGORIES = Path("memory") / "categories.json"
SUGGESTIONS_FILE = Path("memory") / "category_suggestions.md"
ITEMS_DIR = Path("memory") / "items"
SLUG_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class MemoryCategory:
    name: str
    label: str
    description: str
    source: str | None = None
    created_at: str | None = None


@dataclass(frozen=True)
class StructuredMemoryItem:
    id: str
    category: str
    category_label: str
    status: str
    source: str
    source_date: str
    confidence: str
    tags: tuple[str, ...]
    created_at: str
    updated_at: str
    content: str


def load_categories(root: Path | str = ROOT) -> list[MemoryCategory]:
    root = Path(root)
    personal = root / PERSONAL_CATEGORIES
    source = personal if personal.exists() else root / CATEGORY_EXAMPLE
    if not source.exists():
        return []

    data = json.loads(source.read_text(encoding="utf-8"))
    raw_categories = data.get("categories", []) if isinstance(data, dict) else []
    categories: list[MemoryCategory] = []
    for value in raw_categories:
        if not isinstance(value, dict):
            continue
        name = str(value.get("name", "")).strip()
        label = str(value.get("label", "")).strip()
        description = str(value.get("description", "")).strip()
        if not name or not label:
            continue
        categories.append(
            MemoryCategory(
                name=name,
                label=label,
                description=description,
                source=_optional_text(value.get("source")),
                created_at=_optional_text(value.get("created_at")),
            )
        )
    return categories


def add_category(
    root: Path | str,
    name: str,
    label: str,
    description: str,
    source: str,
    created_at: str | None = None,
) -> MemoryCategory:
    root = Path(root)
    name = name.strip()
    label = label.strip()
    description = description.strip()
    source = source.strip()
    if not SLUG_PATTERN.fullmatch(name):
        raise ValueError("category name must be a lower snake_case slug")
    if not label or not description or not source:
        raise ValueError("label, description, and source are required")

    categories = load_categories(root)
    normalized_label = _normalize_label(label)
    for category in categories:
        if category.name == name:
            raise ValueError(f"category already exists: {name}")
        if _normalize_label(category.label) == normalized_label:
            raise ValueError(f"similar category label already exists: {category.name}")

    category = MemoryCategory(
        name=name,
        label=label,
        description=description,
        source=source,
        created_at=created_at or _now(),
    )
    _write_personal_categories(root, [*categories, category])
    return category


def propose_category(
    root: Path | str,
    name: str,
    label: str,
    reason: str,
    source: str,
    created_at: str | None = None,
) -> Path:
    root = Path(root)
    if not all(value.strip() for value in (name, label, reason, source)):
        raise ValueError("name, label, reason, and source are required")
    path = root / SUGGESTIONS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    heading = "# Category Suggestions\n\n"
    current = path.read_text(encoding="utf-8") if path.exists() else heading
    entry = "\n".join(
        [
            f"## {created_at or _now()} {name.strip()}",
            "",
            f"- Label: {label.strip()}",
            f"- Source: {source.strip()}",
            f"- Reason: {reason.strip()}",
            "- Status: pending",
            "",
        ]
    )
    path.write_text(current.rstrip() + "\n\n" + entry, encoding="utf-8")
    return path


def create_memory_item(
    root: Path | str,
    item: StructuredMemoryItem,
    overwrite: bool = False,
) -> Path:
    root = Path(root)
    _validate_item(item)
    path = root / ITEMS_DIR / f"{item.id}.md"
    if path.exists() and not overwrite:
        raise FileExistsError(f"memory item already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(format_memory_item(item), encoding="utf-8")
    return path


def read_memory_item(path: Path | str) -> StructuredMemoryItem:
    path = Path(path)
    metadata, content = _parse_front_matter(path.read_text(encoding="utf-8"))
    required = (
        "id",
        "category",
        "category_label",
        "status",
        "source",
        "source_date",
        "confidence",
        "created_at",
        "updated_at",
    )
    missing = [name for name in required if not str(metadata.get(name, "")).strip()]
    if missing:
        raise ValueError(f"missing structured memory fields in {path}: {', '.join(missing)}")
    tags_value = metadata.get("tags", [])
    if isinstance(tags_value, str):
        tags = (tags_value,) if tags_value else ()
    else:
        tags = tuple(str(tag).strip() for tag in tags_value if str(tag).strip())
    item = StructuredMemoryItem(
        id=str(metadata["id"]),
        category=str(metadata["category"]),
        category_label=str(metadata["category_label"]),
        status=str(metadata["status"]),
        source=str(metadata["source"]),
        source_date=str(metadata["source_date"]),
        confidence=str(metadata["confidence"]),
        tags=tags,
        created_at=str(metadata["created_at"]),
        updated_at=str(metadata["updated_at"]),
        content=content.strip(),
    )
    _validate_item(item)
    return item


def format_memory_item(item: StructuredMemoryItem) -> str:
    lines = [
        "---",
        f"id: {item.id}",
        f"category: {item.category}",
        f"category_label: {item.category_label}",
        f"status: {item.status}",
        f"source: {item.source}",
        f"source_date: {item.source_date}",
        f"confidence: {item.confidence}",
        "tags:",
        *(f"  - {tag}" for tag in item.tags),
        f"created_at: {item.created_at}",
        f"updated_at: {item.updated_at}",
        "---",
        "",
        item.content.strip(),
        "",
    ]
    return "\n".join(lines)


def _parse_front_matter(text: str) -> tuple[dict[str, object], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("structured memory item must start with YAML front matter")
    try:
        end = next(index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---")
    except StopIteration as exc:
        raise ValueError("structured memory item has unclosed YAML front matter") from exc

    metadata: dict[str, object] = {}
    active_list: str | None = None
    for line in lines[1:end]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if active_list and stripped.startswith("-"):
            value = stripped[1:].strip()
            if value:
                cast_list = metadata.setdefault(active_list, [])
                if isinstance(cast_list, list):
                    cast_list.append(value)
            continue
        if ":" not in line:
            raise ValueError(f"invalid front matter line: {line}")
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            raise ValueError("front matter key cannot be empty")
        if value:
            metadata[key] = value.strip("\"'")
            active_list = None
        else:
            metadata[key] = []
            active_list = key
    return metadata, "\n".join(lines[end + 1 :])


def _validate_item(item: StructuredMemoryItem) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", item.id):
        raise ValueError("memory item id contains unsupported characters")
    if not SLUG_PATTERN.fullmatch(item.category):
        raise ValueError("category must be a lower snake_case slug")
    required = (
        item.category_label,
        item.status,
        item.source,
        item.source_date,
        item.confidence,
        item.created_at,
        item.updated_at,
        item.content,
    )
    if not all(value.strip() for value in required):
        raise ValueError("structured memory item has an empty required field")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", item.source_date):
        raise ValueError("source_date must use YYYY-MM-DD")


def _write_personal_categories(root: Path, categories: Iterable[MemoryCategory]) -> None:
    path = root / PERSONAL_CATEGORIES
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "version": 1,
        "categories": [
            {
                "name": value.name,
                "label": value.label,
                "description": value.description,
                **({"source": value.source} if value.source else {}),
                **({"created_at": value.created_at} if value.created_at else {}),
            }
            for value in categories
        ],
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _normalize_label(value: str) -> str:
    return re.sub(r"[\s_\-・、。]+", "", value).lower()


def _optional_text(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage local structured memory items and dynamic categories.")
    parser.add_argument("--root", default=ROOT, help="AI-LifeOS root directory.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("categories", help="Print the effective category definitions.")

    add = subparsers.add_parser("add-category", help="Add a category after duplicate checking.")
    add.add_argument("--name", required=True)
    add.add_argument("--label", required=True)
    add.add_argument("--description", required=True)
    add.add_argument("--source", required=True)

    propose = subparsers.add_parser("propose-category", help="Record an uncertain category without activating it.")
    propose.add_argument("--name", required=True)
    propose.add_argument("--label", required=True)
    propose.add_argument("--reason", required=True)
    propose.add_argument("--source", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "categories":
        print(json.dumps([category.__dict__ for category in load_categories(args.root)], ensure_ascii=False, indent=2))
    elif args.command == "add-category":
        category = add_category(args.root, args.name, args.label, args.description, args.source)
        print(f"Added category: {category.name}")
    elif args.command == "propose-category":
        path = propose_category(args.root, args.name, args.label, args.reason, args.source)
        print(f"Recorded suggestion: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
