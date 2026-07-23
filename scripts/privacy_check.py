import argparse
import math
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_FILE_BYTES = 1024 * 1024
PROFILE_COMMIT = "commit"
PROFILE_PUBLISH = "publish"

ALLOWLIST_MARKERS = (
    "privacy-check: allow",
    "privacy_check: allow",
)
ALLOWLISTABLE_LABELS = {
    "account id",
    "email address",
    "Japanese phone number",
    "Japanese address",
    "US street address",
    "long random string",
}

PRIVATE_DATA_ROOTS = {
    "conversations",
    "journal",
    "memory",
    "inbox",
    "tasks",
    "imports",
    "logs",
    "renovationTickets",
}
ALLOWED_PRIVATE_DATA_FILES = {
    "conversations/.gitkeep",
    "journal/.gitkeep",
    "memory/.gitkeep",
    "inbox/.gitkeep",
    "tasks/.gitkeep",
    "imports/.gitkeep",
    "imports/chatgpt_export/.gitkeep",
    "logs/.gitkeep",
    "renovationTickets/.gitkeep",
}


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
            r"""(?ix)
            (?<![?&])\b(?:[a-z0-9]+[_-])*
            (?:api[_-]?key|access[_-]?key|secret(?:[_-]?key)?|client[_-]?secret|
               password|passwd|token|bearer|private[_-]?key|session[_-]?cookie|
               database[_-]?url|webhook[_-]?url|dsn)
            \b\s*[:=]\s*['"]?
            (?!example\b|sample\b|dummy\b|test\b|placeholder\b|changeme\b|replace[_-]?me\b|your[_-])
            [^'"\s#]{8,}
            """
        ),
    ),
    ("authorization bearer", re.compile(r"(?i)\bAuthorization\s*:\s*Bearer\s+[A-Za-z0-9._~+/=-]{16,}")),
    ("OpenAI-style key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{16,}\b")),
    (
        "URL query secret",
        re.compile(
            r"""(?ix)
            \bhttps?://[^\s'"<>]+[?&]
            (?:access[_-]?token|auth[_-]?token|api[_-]?key|client[_-]?secret|
               secret|token|signature|sig|session|code)
            =
            [^&\s'"<>]{8,}
            """
        ),
    ),
    (
        "account id",
        re.compile(r"(?i)\b(?:account|tenant|customer|user)[_-]?id\b\s*[:=]\s*['\"]?[A-Za-z0-9_-]{8,}"),
    ),
    ("email address", re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)),
    ("Japanese phone number", re.compile(r"\b0\d{1,4}-\d{1,4}-\d{3,4}\b")),
    (
        "Japanese address",
        re.compile(
            r"(?:〒\s*)?\d{3}-\d{4}\s*(?:東京都|北海道|(?:京都|大阪)府|.{2,3}県).{2,60}"
            r"|(?:住所|address)\s*[:=]\s*[^\s,，]{6,}"
            r"|(?:東京都|北海道|(?:京都|大阪)府|.{2,3}県).{0,20}(?:市|区|町|村).{0,30}(?:\d{1,4}|丁目|番地|号)",  # privacy-check: allow regex pattern
            re.IGNORECASE,
        ),
    ),
    (
        "US street address",
        re.compile(
            r"\b\d{1,6}\s+[A-Z][A-Za-z0-9.'-]*(?:\s+[A-Z][A-Za-z0-9.'-]*){0,4}\s+"
            r"(?:Street|St\.?|Avenue|Ave\.?|Road|Rd\.?|Boulevard|Blvd\.?|Lane|Ln\.?|Drive|Dr\.?)\b"
            r"(?:,\s*[A-Z][A-Za-z .'-]+)?(?:,\s*[A-Z]{2}\s*\d{5}(?:-\d{4})?)?",
        ),
    ),
)

HIGH_ENTROPY_PATTERN = re.compile(r"(?<![A-Za-z0-9_+/-])[A-Za-z0-9][A-Za-z0-9_+/-]{39,}={0,2}(?![A-Za-z0-9_+/-])")
HIGH_ENTROPY_SKIP_PATHS = {
    "desktop/app/package-lock.json",
    "desktop/app/src-tauri/Cargo.lock",
}
HIGH_ENTROPY_SKIP_WORDS = (
    "checksum",
    "fingerprint",
    "integrity",
    "lockfile",
    "sha1",
    "sha256",
    "sha384",
    "sha512",
)


def scan_text(path: str, text: str, profile: str = PROFILE_COMMIT) -> list[Finding]:
    findings: list[Finding] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        allowlisted = _is_allowlisted_line(line)

        for label, pattern in PATTERNS:
            for match in pattern.finditer(line):
                if allowlisted and _can_allowlist_label(label):
                    continue
                findings.append(
                    Finding(
                        path=path,
                        line_number=line_number,
                        label=label,
                        match=_mask_match(match.group(0)),
                    )
                )

        if profile == PROFILE_PUBLISH:
            for match in HIGH_ENTROPY_PATTERN.finditer(line):
                value = match.group(0)
                if not _looks_like_high_entropy_secret(path, line, value):
                    continue
                if allowlisted and _can_allowlist_label("long random string"):
                    continue
                findings.append(
                    Finding(
                        path=path,
                        line_number=line_number,
                        label="long random string",
                        match=_mask_match(value),
                    )
                )

    return findings


def scan_path(path: str) -> list[Finding]:
    normalized = _normalize_git_path(path)
    if normalized in ALLOWED_PRIVATE_DATA_FILES:
        return []

    root_name = normalized.split("/", maxsplit=1)[0]
    if root_name in PRIVATE_DATA_ROOTS:
        return [
            Finding(
                path=path,
                line_number=1,
                label="personal data path",
                match=normalized,
            )
        ]

    return []


def scan_staged(root: Path | str = ROOT, profile: str = PROFILE_COMMIT) -> list[Finding]:
    root = Path(root)
    paths = _git_lines(root, ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"])
    return _scan_git_paths(root, paths, profile=profile)


def scan_working(root: Path | str = ROOT, profile: str = PROFILE_COMMIT) -> list[Finding]:
    root = Path(root)
    paths = _git_lines(root, ["git", "diff", "--name-only", "--diff-filter=ACMR"])
    paths.extend(_git_lines(root, ["git", "ls-files", "--others", "--exclude-standard"]))
    return _scan_working_paths(root, sorted(set(paths)), profile=profile)


def scan_range(root: Path | str = ROOT, rev_range: str = "origin/main..HEAD", profile: str = PROFILE_COMMIT) -> list[Finding]:
    root = Path(root)
    paths = _git_lines(root, ["git", "diff", "--name-only", "--diff-filter=ACMR", rev_range])
    return _scan_git_revision_paths(root, "HEAD", paths, profile=profile)


def scan_publish(root: Path | str = ROOT) -> list[Finding]:
    root = Path(root)
    paths = _git_lines(root, ["git", "ls-files"])
    paths.extend(_git_lines(root, ["git", "ls-files", "--others", "--exclude-standard"]))
    return _scan_working_paths(root, sorted(set(paths)), profile=PROFILE_PUBLISH)


def _scan_git_paths(root: Path, paths: list[str], profile: str = PROFILE_COMMIT) -> list[Finding]:
    findings: list[Finding] = []
    for path in paths:
        findings.extend(scan_path(path))

        content = _git_blob(root, path)
        if content is None or not _looks_like_text(content):
            continue

        findings.extend(scan_text(path, content.decode("utf-8", errors="replace"), profile=profile))

    return findings


def _scan_git_revision_paths(root: Path, revision: str, paths: list[str], profile: str = PROFILE_COMMIT) -> list[Finding]:
    findings: list[Finding] = []
    for path in paths:
        findings.extend(scan_path(path))

        content = _git_blob(root, path, revision=revision)
        if content is None or not _looks_like_text(content):
            continue

        findings.extend(scan_text(path, content.decode("utf-8", errors="replace"), profile=profile))

    return findings


def _scan_working_paths(root: Path, paths: list[str], profile: str = PROFILE_COMMIT) -> list[Finding]:
    findings: list[Finding] = []
    for path_text in paths:
        findings.extend(scan_path(path_text))

        path = root / path_text
        if not path.is_file():
            continue
        if path.stat().st_size > MAX_FILE_BYTES:
            continue

        content = path.read_bytes()
        if not _looks_like_text(content):
            continue

        findings.extend(scan_text(path_text, content.decode("utf-8", errors="replace"), profile=profile))

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


def _is_allowlisted_line(line: str) -> bool:
    lowered = line.lower()
    return any(marker in lowered for marker in ALLOWLIST_MARKERS)


def _can_allowlist_label(label: str) -> bool:
    return label in ALLOWLISTABLE_LABELS


def _normalize_git_path(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def _looks_like_high_entropy_secret(path: str, line: str, value: str) -> bool:
    normalized_path = _normalize_git_path(path)
    if normalized_path in HIGH_ENTROPY_SKIP_PATHS:
        return False

    lowered_line = line.lower()
    if any(word in lowered_line for word in HIGH_ENTROPY_SKIP_WORDS):
        return False

    value = value.strip("=-_")
    if len(value) < 40:
        return False
    if "/" in value and not any(char in value for char in "+="):
        return False

    classes = sum(
        (
            any(char.islower() for char in value),
            any(char.isupper() for char in value),
            any(char.isdigit() for char in value),
            any(char in "+/=_-" for char in value),
        )
    )
    if classes < 3:
        return False

    return _shannon_entropy(value) >= 4.0


def _shannon_entropy(value: str) -> float:
    if not value:
        return 0.0

    entropy = 0.0
    length = len(value)
    for char in set(value):
        probability = value.count(char) / length
        entropy -= probability * math.log2(probability)
    return entropy


def _mask_match(value: str) -> str:
    normalized = value.strip()
    if len(normalized) <= 8:
        return "***"

    if "@" in normalized and "=" not in normalized and ":" not in normalized:
        name, domain = normalized.split("@", maxsplit=1)
        return f"{name[:1]}***@{domain[:1]}***"

    return f"{normalized[:4]}...{normalized[-4:]}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check changed files for private or secret information.")
    parser.add_argument("--root", default=ROOT, help="Repository root")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--staged", action="store_true", help="Check staged files. This is the default pre-commit check.")
    mode.add_argument("--working", action="store_true", help="Check unstaged changes and untracked, non-ignored files.")
    mode.add_argument("--range", dest="rev_range", help="Check files changed in a git revision range.")
    mode.add_argument(
        "--publish",
        "--public",
        dest="publish",
        action="store_true",
        help="Run a stronger PublicEdition pre-publish check across tracked and untracked public files.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    root = Path(args.root)

    try:
        if args.working:
            findings = scan_working(root)
        elif args.rev_range:
            findings = scan_range(root, args.rev_range)
        elif args.publish:
            findings = scan_publish(root)
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
