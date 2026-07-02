import argparse
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_FILE_BYTES = 1024 * 1024


@dataclass(frozen=True)
class Finding:
    path: str
    line_number: int
    label: str
    match: str


PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "secret assignment",
        re.compile(
            r"(?i)\b(api[_-]?key|secret|password|passwd|token|bearer)\b\s*[:=]\s*['\"]?[^'\"\s]{8,}"
        ),
    ),
    ("OpenAI-style key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{16,}\b")),
    ("email address", re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)),
    ("Japanese phone number", re.compile(r"\b0\d{1,4}-\d{1,4}-\d{3,4}\b")),
)


def scan_text(path: str, text: str) -> list[Finding]:
    findings: list[Finding] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        for label, pattern in PATTERNS:
            for match in pattern.finditer(line):
                findings.append(
                    Finding(
                        path=path,
                        line_number=line_number,
                        label=label,
                        match=_mask_match(match.group(0)),
                    )
                )

    return findings


def scan_staged(root: Path | str = ROOT) -> list[Finding]:
    root = Path(root)
    paths = _git_lines(root, ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"])
    return _scan_git_paths(root, paths)


def scan_working(root: Path | str = ROOT) -> list[Finding]:
    root = Path(root)
    paths = _git_lines(root, ["git", "diff", "--name-only", "--diff-filter=ACMR"])
    paths.extend(_git_lines(root, ["git", "ls-files", "--others", "--exclude-standard"]))
    return _scan_working_paths(root, sorted(set(paths)))


def scan_range(root: Path | str = ROOT, rev_range: str = "origin/main..HEAD") -> list[Finding]:
    root = Path(root)
    paths = _git_lines(root, ["git", "diff", "--name-only", "--diff-filter=ACMR", rev_range])
    return _scan_git_revision_paths(root, "HEAD", paths)


def _scan_git_paths(root: Path, paths: list[str]) -> list[Finding]:
    findings: list[Finding] = []
    for path in paths:
        content = _git_blob(root, path)
        if content is None or not _looks_like_text(content):
            continue

        findings.extend(scan_text(path, content.decode("utf-8", errors="replace")))

    return findings


def _scan_git_revision_paths(root: Path, revision: str, paths: list[str]) -> list[Finding]:
    findings: list[Finding] = []
    for path in paths:
        content = _git_blob(root, path, revision=revision)
        if content is None or not _looks_like_text(content):
            continue

        findings.extend(scan_text(path, content.decode("utf-8", errors="replace")))

    return findings


def _scan_working_paths(root: Path, paths: list[str]) -> list[Finding]:
    findings: list[Finding] = []
    for path_text in paths:
        path = root / path_text
        if not path.is_file():
            continue
        if path.stat().st_size > MAX_FILE_BYTES:
            continue

        content = path.read_bytes()
        if not _looks_like_text(content):
            continue

        findings.extend(scan_text(path_text, content.decode("utf-8", errors="replace")))

    return findings


def _git_lines(root: Path, command: list[str]) -> list[str]:
    completed = subprocess.run(
        command,
        cwd=root,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=True,
    )
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


def _git_blob(root: Path, path: str, revision: str = "") -> bytes | None:
    object_name = f"{revision}:{path}" if revision else f":{path}"
    completed = subprocess.run(
        ["git", "show", object_name],
        cwd=root,
        capture_output=True,
    )
    if completed.returncode != 0 or len(completed.stdout) > MAX_FILE_BYTES:
        return None

    return completed.stdout


def _looks_like_text(content: bytes) -> bool:
    if b"\0" in content[:4096]:
        return False

    return True


def _mask_match(value: str) -> str:
    normalized = value.strip()
    if len(normalized) <= 8:
        return "***"

    if "@" in normalized and "=" not in normalized and ":" not in normalized:
        name, domain = normalized.split("@", maxsplit=1)
        return f"{name[:1]}***@{domain[:1]}***"

    return f"{normalized[:4]}...{normalized[-4:]}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check staged or working changes for private information.")
    parser.add_argument("--root", default=ROOT, help="AI-LifeOSのルートディレクトリ")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--staged", action="store_true", help="ステージ済みファイルを確認する")
    mode.add_argument("--working", action="store_true", help="未ステージ変更と未追跡ファイルを確認する")
    mode.add_argument("--range", dest="rev_range", help="指定したgit revision rangeの変更ファイルを確認する")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    root = Path(args.root)

    try:
        if args.working:
            findings = scan_working(root)
        elif args.rev_range:
            findings = scan_range(root, args.rev_range)
        else:
            findings = scan_staged(root)
    except subprocess.CalledProcessError as exc:
        print(f"ERROR: git command failed: {' '.join(exc.cmd)}")
        return 2

    if not findings:
        print("Privacy check passed.")
        return 0

    print("Privacy check failed. Review these possible private values before commit/push:")
    for finding in findings:
        print(f"- {finding.path}:{finding.line_number}: {finding.label}: {finding.match}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
