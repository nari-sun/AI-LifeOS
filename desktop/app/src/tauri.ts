import { Channel, invoke } from "@tauri-apps/api/core"

import type {
  CancelMessageResult,
  FinalizeJobResult,
  FinalizeSessionResult,
  ListResumableResult,
  LocalDataReportResult,
  OrganizeSessionsJobResult,
  StartOrganizeSessionsJobResult,
  AttachmentPayload,
  AssistantStreamEvent,
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

export async function sendMessage(
  sessionFile: string | null,
  content: string,
  requestId: string,
  attachments: AttachmentPayload[] = [],
) {
  return invoke<SendMessageResult>("send_message", {
    payload: {
      ...defaultPayload,
      session_file: sessionFile,
      content,
      request_id: requestId,
      attachments,
    },
  })
}

export async function sendMessageStream(
  sessionFile: string | null,
  content: string,
  requestId: string,
  attachments: AttachmentPayload[] = [],
  onDelta: (delta: string) => void,
) {
  const onEvent = new Channel<AssistantStreamEvent>()
  onEvent.onmessage = (event) => {
    if (event.type === "delta" && event.delta) {
      onDelta(event.delta)
    }
  }
  return invoke<SendMessageResult>("send_message_stream", {
    payload: {
      ...defaultPayload,
      session_file: sessionFile,
      content,
      request_id: requestId,
      attachments,
    },
    onEvent,
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
  return invoke<ListResumableResult>("list_resumable_sessions", {
    payload: {
      ...defaultPayload,
      max_sessions: 50,
    },
  })
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

export async function startFinalizeJob(sessionFile: string) {
  return invoke<FinalizeJobResult>("start_finalize_job", {
    payload: {
      session_file: sessionFile,
      run_codex: true,
    },
  })
}

export async function getFinalizeJob(jobId: string) {
  return invoke<FinalizeJobResult>("get_finalize_job", {
    payload: {
      job_id: jobId,
    },
  })
}

export async function cancelFinalizeJob(jobId: string) {
  return invoke<FinalizeJobResult>("cancel_finalize_job", {
    payload: {
      job_id: jobId,
    },
  })
}

export async function startOrganizeSessionsJob() {
  return invoke<StartOrganizeSessionsJobResult>("start_organize_sessions_job", {
    payload: {
      ...defaultPayload,
      run_codex: true,
    },
  })
}

export async function getOrganizeSessionsJob(jobId: string) {
  return invoke<OrganizeSessionsJobResult>("get_organize_sessions_job", {
    payload: {
      job_id: jobId,
    },
  })
}

export async function cancelOrganizeSessionsJob(jobId: string) {
  return invoke<OrganizeSessionsJobResult>("cancel_organize_sessions_job", {
    payload: {
      job_id: jobId,
    },
  })
}

export async function getLocalDataReport() {
  return invoke<LocalDataReportResult>("local_data_report", { payload: defaultPayload })
}

export async function openLocalDataFolder(folder: string) {
  return invoke<{ ok: boolean; folder: string; path: string }>("open_local_data_folder", {
    payload: {
      folder,
    },
  })
}
