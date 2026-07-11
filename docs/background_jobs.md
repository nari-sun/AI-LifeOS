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
logs/chat_gui_jobs/session-<hash>.lock
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
file, log path, cancel file, worker process id, timestamps, and final result metadata when
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
* A session-specific lock makes repeated or concurrent finalize requests
  idempotent. The active job is returned instead of starting a second writer.
* Polling checks the recorded worker process. If a GUI or worker restart leaves
  a `queued` or `running` status without a live worker, the job is recovered as
  `failed`, its lock is released, and the session can be retried.
* Each job log records worker start, progress stages, terminal state, and orphan
  recovery without copying conversation content. Abrupt exits therefore leave
  the last observed stage even when no final JSON result was written.
* On Windows, finalize workers are started as detached process groups and request
  breakaway from the GUI runner's job object. This prevents a Tauri development
  restart from terminating an authorized finalize operation with the window.
  Codex and other cancellable child commands use `CREATE_NO_WINDOW`, so the
  detached worker does not open a visible `cmd.exe` console while organizing.
* The recommended PowerShell development launcher passes `tauri dev --no-watch`.
  Vite frontend hot reload remains active, while Rust-side changes require a
  manual launcher restart. This avoids false `build.rs` or icon watch events
  restarting the app during a finalize job.
* The GUI stores the active finalize job id in local storage. After an app reload
  it re-reads the backend status, resumes the matching session, and continues
  polling or shows the recovered terminal error. Session switching and new-chat
  creation remain disabled while that finalize job is active.
* Codex output is captured as bytes and decoded after the process exits so a
  Windows CP932 diagnostic cannot hide the original CLI error behind a UTF-8
  reader exception.
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
