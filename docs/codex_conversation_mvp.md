# Phase2.6: Codex Conversation MVP

## Status

Phase2.6 MVP is implemented.

The current implementation keeps the project policy of not using the OpenAI API directly and not relying on `.env`. It uses the local Codex CLI login/session and stores live conversations as JSONL before converting them into the existing AI-LifeOS `raw.md` workflow.

## Implemented Files

- `scripts/live_session.py`
- `scripts/codex_conversation.py`
- `scripts/finalize_live_chat.py`
- `tests/test_live_session.py`
- `tests/test_codex_conversation.py`
- `tests/test_finalize_live_chat.py`

## Implemented Flow

```text
python scripts\codex_conversation.py
↓
User message is written to inbox/live/YYYY-MM-DD_HHMMSS.jsonl
↓
Codex reply is generated through codex.cmd exec in read-only mode
↓
Assistant message is written to the same JSONL
↓
/exit or Ctrl+C ends the session
↓
conversations/YYYY/MM/YYYY-MM-DD_HHMMSS/raw.md is created
↓
tasks/latest_codex_task.md is created
↓
summary.md / journal / memory are updated by the existing Phase2.5 prompt
```

`codex_conversation.py` automatically runs the finalize and Phase2.5 memory-processing step on exit.
During exit processing, the CLI shows a spinner and stage-based percentage. The percentage reflects AI-LifeOS workflow stages, not Codex model-internal progress.

Git commit remains explicit and only targets public project files in PublicEdition:

```powershell
python scripts\codex_conversation.py --commit-on-exit
```

The original live JSONL is not deleted or moved.

## Codex SDK Option

The Codex manual documents the Python SDK as a way to control local Codex app-server through JSON-RPC. It supports starting a thread, continuing the same thread, and running turns with sandbox presets.

In this local environment, `openai_codex` is not installed:

```text
openai_codex None
```

Because the project avoids adding dependencies too early, the Phase2.6 MVP does not install the SDK yet.

Later upgrade path:

```powershell
pip install openai-codex
```

Then replace the current `codex exec` turn adapter in `codex_conversation.py` with a persistent SDK thread.

## Codex App Server Option

The Codex manual documents `codex app-server` as a JSON-RPC interface for rich clients. It can start and resume threads, stream turn events, and power deeper IDE-like integrations.

Current local CLI support exists:

```powershell
codex.cmd app-server --help
```

For this project, direct app-server integration is deferred because it would require a more complex JSON-RPC client and event handling layer. That is a better fit after the CLI MVP is stable or when Phase2.7 GUI needs streamed events.

## Adopted MVP Approach

Phase2.6 currently uses:

```powershell
codex.cmd --ask-for-approval never exec --model gpt-5.4-mini -c 'model_reasoning_effort="medium"' -c 'service_tier="fast"' -c features.fast_mode=true -C <repo> --sandbox read-only --output-last-message <tempfile> -
```

Reasoning:

- Reuses the user's existing Codex CLI authentication.
- Does not require OpenAI API keys.
- Does not add new dependencies.
- Keeps chat turns read-only.
- Uses `gpt-5.4-mini` with medium reasoning and requests Fast mode for chat replies.
- Lets the application save the user message before Codex runs.
- Writes the assistant reply only after Codex returns.

Exit-time summary / journal / memory processing uses `gpt-5.5` with `model_reasoning_effort="xhigh"` through the existing Phase2.5 task path.

Limitation:

- `codex exec` is non-interactive per turn, so the app passes recent transcript context each time instead of holding a true app-server/SDK thread.

This is acceptable for Phase2.6 MVP. A persistent SDK/app-server thread can replace the adapter later without changing the JSONL or finalize format.

## Commands

Start live chat:

```powershell
python scripts\codex_conversation.py
```

Start live chat without AI replies:

```powershell
python scripts\codex_conversation.py --no-ai
```

Start live chat as JSONL logging only:

```powershell
python scripts\codex_conversation.py --no-ai --no-finalize-on-exit
```

Start live chat without automatic finalization:

```powershell
python scripts\codex_conversation.py --no-finalize-on-exit
```

Start live chat and create only `raw.md` on exit:

```powershell
python scripts\codex_conversation.py --no-process-on-exit
```

Start live chat and commit public project file changes on exit:

```powershell
python scripts\codex_conversation.py --commit-on-exit
```

Hide exit progress:

```powershell
python scripts\codex_conversation.py --no-exit-progress
```

Finalize latest live JSONL into `raw.md`:

```powershell
python scripts\finalize_live_chat.py
```

This manual finalize command is mainly for re-processing an older live JSONL, because normal live chat finalizes automatically on exit.

Finalize a specific live JSONL:

```powershell
python scripts\finalize_live_chat.py --file inbox\live\2026-07-01_223000.jsonl
```

Finalize and run existing memory processing:

```powershell
python scripts\finalize_live_chat.py --run-codex
```

Finalize, run memory processing, and commit public project file changes:

```powershell
python scripts\finalize_live_chat.py --run-codex --commit
```

## Safety Boundaries

- During live chat, `memory/long_term.md` and `journal` are not edited.
- During live chat, Git commit is not run.
- During live chat, Codex is invoked with `read-only` sandbox by default.
- On exit, `codex_conversation.py` runs finalize and Phase2.5 processing automatically.
- `finalize_live_chat.py` does not delete or move the original JSONL.
- `--no-finalize-on-exit` disables automatic exit finalization.
- `--no-process-on-exit` writes `raw.md` but skips summary / journal / memory updates.
- `--commit-on-exit` is explicit, uses the existing privacy check inside `process_chat.commit_changes`, and does not commit generated conversation, journal, memory, inbox, or task files in PublicEdition.

## Completion Criteria

- `python scripts\codex_conversation.py` starts a live CLI chat.
- User messages are saved before Codex is called.
- Assistant replies are saved after Codex returns.
- `/exit` and Ctrl+C save safely and finalize new messages.
- `python scripts\finalize_live_chat.py` converts live JSONL to `raw.md`.
- `tasks/latest_codex_task.md` is generated for the new `raw.md`.
- Exit processing connects to the existing Phase2.5 summary / journal / memory process.
- `python -m unittest` passes.
