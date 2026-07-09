# Local Data Management

Status: Draft for RT-0014

This document defines the read-only local personal data management screen. The
MVP is an inventory and safety surface only: it reports what exists locally and
what is derived, but it does not change personal data.

## Scope

The screen reports local AI-LifeOS data by directory or generated artifact:

| Area | Path | Reported in MVP |
| --- | --- | --- |
| Conversations | `conversations/` | Existence, file count, directory count, total bytes, newest file, and newest modified time |
| Journal | `journal/` | Existence, file count, directory count, total bytes, newest file, and newest modified time |
| Memory | `memory/` | Existence, file count, directory count, total bytes, newest file, and newest modified time |
| Inbox | `inbox/` | Existence, file count, directory count, total bytes, newest file, and newest modified time |
| Tasks | `tasks/` | Existence, file count, directory count, total bytes, newest file, and newest modified time |
| Imports | `imports/` | File and folder counts, total size, and newest modified time |
| Logs | `logs/` | Log file count, total size, newest modified time, and known GUI/bridge log groups when present |
| Search index | `memory/search_index.sqlite3` | Presence, size, last modified time, and whether it is treated as rebuildable derived data |

The MVP intentionally does not parse Markdown, JSONL, or SQLite content. It uses
filesystem metadata only, so the report can be shown without reading personal
conversation text.

## Non-Actions

The MVP never deletes, moves, edits, truncates, normalizes, or rewrites local
personal data.

Specifically, the MVP does not:

* Delete old conversations, live sessions, logs, imports, or generated tasks.
* Move files into archive, trash, backup, or export folders.
* Edit `conversations`, `journal`, `memory`, `inbox`, `tasks`, `imports`, or
  `logs`.
* Rebuild `memory/search_index.sqlite3`.
* Update `memory/long_term.md`, `memory/preferences.md`, or `memory/projects.md`.
* Run Codex memory extraction, journal generation, or summary generation.
* Stage, commit, or push files.

If a future action is needed, the UI should present it as a disabled or separate
follow-up, not as part of the read-only MVP.

## Privacy Check Guidance

The screen may remind the user to run privacy checks before committing or
publishing public project files:

```powershell
python scripts\privacy_check.py --staged
python scripts\privacy_check.py --range origin/main..HEAD
```

Before public release:

```powershell
python scripts\privacy_check.py --publish
```

If a privacy check fails, stop the commit or push and report the detected paths
or findings. Do not use allowlist comments to include personal data directories
in PublicEdition.

## Follow-Up Work

Backup, delete, and export are separate features and must not be hidden inside
the read-only screen.

Future backup work should define:

* Explicit destination selection.
* Preview of included paths and estimated size.
* Confirmation before writing backup files.
* Clear handling for generated and rebuildable files such as the search index.

Future delete work should define:

* Explicit path-level confirmation.
* Protection for unfinished, failed, or recent live sessions.
* Dry-run output before any real deletion.
* A recovery or trash strategy where possible.

Future export work should define:

* Export formats and redaction policy.
* Whether summaries, journals, and long-term memory are included.
* Whether generated tasks, logs, imports, and the search index are excluded by
  default.
* Privacy check guidance for any export intended for sharing.

## PublicEdition Git Boundaries

PublicEdition must not Git-track personal data or generated memory artifacts.

Do not commit:

* `conversations/`
* `journal/`
* `memory/`
* `inbox/`
* `tasks/`
* `imports/`
* `renovationTickets/`

`logs/` should be treated as local diagnostics and not committed unless a future
public-safe fixture or example is intentionally added.

Allowed public project files include:

* `scripts/`
* `prompts/`
* `docs/`
* `desktop/`
* `config/`
* `templates/`
* `tests/`
* `README.md`
* `AGENTS.md`
* `.gitignore`

`memory/search_index.sqlite3` is derived from Markdown and can be regenerated.
It is not a source of truth and must not be committed.
