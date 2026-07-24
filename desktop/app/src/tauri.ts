import { Channel, invoke } from "@tauri-apps/api/core"
import { open } from "@tauri-apps/plugin-dialog"

import type {
  CancelMessageResult,
  FinalizeJobResult,
  FinalizeSessionResult,
  ListResumableResult,
  LocalDataReportResult,
  MemorySummaryResult,
  OrganizeSessionsJobResult,
  PersonalizationResult,
  PersonalizationSettings,
  SessionPersonalization,
  StartOrganizeSessionsJobResult,
  AttachmentPayload,
  AssistantStreamEvent,
  CancelReadAloudResult,
  ChatGptImportApplyResult,
  ChatGptImportPreviewResult,
  DiscardReadAloudAudioResult,
  ReadAloudResult,
  ReadAloudStreamEvent,
  ReadAloudStreamResult,
  ResumeSessionResult,
  SaveSessionResult,
  SendMessageResult,
  StartSessionResult,
} from "@/types"

const defaultPayload = {}

export function isTauriRuntime() {
  return "__TAURI_INTERNALS__" in window
}

export async function startSession() {
  return invoke<StartSessionResult>("start_session", { payload: defaultPayload })
}

export async function readAloud(text: string, voice: string, requestId: string) {
  return invoke<ReadAloudResult>("read_aloud", {
    payload: {
      ...defaultPayload,
      text,
      voice,
      request_id: requestId,
    },
  })
}

export async function readAloudStream(
  text: string,
  voice: string,
  requestId: string,
  onAudio: (audio: ReadAloudStreamEvent["audio"]) => void,
) {
  const onEvent = new Channel<ReadAloudStreamEvent>()
  onEvent.onmessage = (event) => {
    if (event.type === "audio") {
      onAudio(event.audio)
    }
  }
  return invoke<ReadAloudStreamResult>("read_aloud_stream", {
    payload: {
      ...defaultPayload,
      text,
      voice,
      request_id: requestId,
    },
    onEvent,
  })
}

export async function cancelReadAloud(requestId: string) {
  return invoke<CancelReadAloudResult>("cancel_read_aloud", {
    payload: {
      request_id: requestId,
    },
  })
}

export async function discardReadAloudAudio(audioPath: string) {
  return invoke<DiscardReadAloudAudioResult>("discard_read_aloud_audio", {
    payload: {
      audio_path: audioPath,
    },
  })
}

export async function sendMessage(
  sessionFile: string | null,
  content: string,
  requestId: string,
  attachments: AttachmentPayload[] = [],
  fullArchiveReview = false,
) {
  return invoke<SendMessageResult>("send_message", {
    payload: {
      ...defaultPayload,
      session_file: sessionFile,
      content,
      request_id: requestId,
      attachments,
      full_archive_review: fullArchiveReview,
    },
  })
}

export async function sendMessageStream(
  sessionFile: string | null,
  content: string,
  requestId: string,
  attachments: AttachmentPayload[] = [],
  fullArchiveReview: boolean,
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
      full_archive_review: fullArchiveReview,
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

export async function getPersonalization(sessionFile: string | null) {
  return invoke<PersonalizationResult>("get_personalization", {
    payload: {
      session_file: sessionFile,
    },
  })
}

export async function updatePersonalization(
  sessionFile: string | null,
  settings: PersonalizationSettings | null,
  session: Pick<SessionPersonalization, "temporary" | "memory_enabled" | "past_chat_search_enabled" | "project_scope"> | null,
) {
  return invoke<PersonalizationResult>("update_personalization", {
    payload: {
      session_file: sessionFile,
      settings,
      ...(session ? { session } : {}),
    },
  })
}

export async function getMemorySummary() {
  return invoke<MemorySummaryResult>("get_memory_summary", { payload: defaultPayload })
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

export async function chooseChatGptExportFile() {
  const selected = await open({
    title: "ChatGPTエクスポートを選択",
    multiple: false,
    filters: [
      { name: "ChatGPT export", extensions: ["zip", "json"] },
    ],
  })
  return typeof selected === "string" ? selected : null
}

export async function chooseChatGptExportFolder() {
  const selected = await open({
    title: "ChatGPTエクスポートフォルダを選択",
    directory: true,
    multiple: false,
  })
  return typeof selected === "string" ? selected : null
}

export async function previewChatGptImport(source: string) {
  return invoke<ChatGptImportPreviewResult>("preview_chatgpt_import", {
    payload: { source },
  })
}

export async function applyChatGptImport(source: string, selectedIds: string[]) {
  return invoke<ChatGptImportApplyResult>("apply_chatgpt_import", {
    payload: {
      source,
      selected_ids: selectedIds,
      confirmed: true,
    },
  })
}
