import { invoke } from "@tauri-apps/api/core"

import type {
  CancelMessageResult,
  CleanupExpiredResult,
  FinalizeSessionResult,
  ListResumableResult,
  ResumeSessionResult,
  SaveSessionResult,
  SendMessageResult,
  StartSessionResult,
} from "@/types"

const defaultPayload = {
  retention_days: 10,
}

export function isTauriRuntime() {
  return "__TAURI_INTERNALS__" in window
}

export async function startSession() {
  return invoke<StartSessionResult>("start_session", { payload: defaultPayload })
}

export async function sendMessage(sessionFile: string | null, content: string, requestId: string) {
  return invoke<SendMessageResult>("send_message", {
    payload: {
      ...defaultPayload,
      session_file: sessionFile,
      content,
      request_id: requestId,
    },
  })
}

export async function cancelMessage(requestId: string) {
  return invoke<CancelMessageResult>("cancel_message", {
    payload: {
      request_id: requestId,
    },
  })
}

export async function saveSession(sessionFile: string) {
  return invoke<SaveSessionResult>("save_session", {
    payload: {
      session_file: sessionFile,
    },
  })
}

export async function listResumableSessions() {
  return invoke<ListResumableResult>("list_resumable_sessions", { payload: defaultPayload })
}

export async function resumeSession(sessionRef: string) {
  return invoke<ResumeSessionResult>("resume_session", {
    payload: {
      ...defaultPayload,
      session_ref: sessionRef,
    },
  })
}

export async function finalizeSession(sessionFile: string) {
  return invoke<FinalizeSessionResult>("finalize_session", {
    payload: {
      session_file: sessionFile,
      run_codex: true,
    },
  })
}

export async function cleanupExpiredSessions() {
  return invoke<CleanupExpiredResult>("cleanup_expired_sessions", { payload: defaultPayload })
}
