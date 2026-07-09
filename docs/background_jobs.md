# Background Jobs

RT-0013 adds a small background-job model for long-running GUI operations such
as `finalize_live_chat.py` with summary, journal, memory, and search-index
updates.

## Scope

The first GUI job is `finalize-session`.

It runs outside the foreground Tauri command so the React UI can keep rendering,
poll status, and show progress while Codex and indexing work continue.

## Status Files

GUI finalize jobs write status under:

```text
logs/chat_gui_jobs/<job_id>.json
logs/chat_gui_jobs/<job_id>.log
logs/chat_gui_jobs/<job_id>.cancel
```

`logs/` is local diagnostics data and must not be committed. `privacy_check.py`
also treats `logs/` as a private root, with `logs/.gitkeep` as the only allowed
placeholder.

## Status Values

Jobs use these states:

* `queued`
* `running`
* `succeeded`
* `failed`
* `cancelled`

The JSON status includes job id, status, stage, message, percent, error, session
file, log path, cancel file, timestamps, and final result metadata when
available.

## GUI Behavior

The GUI starts a finalize job, then polls the job status by id.

While the job is active:

* The chat screen remains responsive.
* Current stage and progress are displayed.
* New sends for the same session are disabled to avoid save/finalize races.
* A cancel request writes the job cancel file.

Completion updates the session organization state from the job result. Failure
shows the error and leaves the session metadata available for retry.

## Safety Rules

* Jobs do not commit Git changes.
* Jobs do not delete personal data.
* Failed or incomplete session states remain protected by existing
  `session_store.py` organization metadata.
* Cancellation is best effort. If a Codex subprocess is already running, the
  bridge attempts to terminate it through the same cancel-file mechanism used
  by response generation.

## Verification

Covered by:

```powershell
python -m unittest tests.test_chat_gui_bridge tests.test_background_jobs
npm run build
cargo check
```
