export type Role = "user" | "assistant"

export interface ChatMessage {
  role: Role
  content: string
  timestamp: string
}

export interface SessionFile {
  session_id: string
  jsonl_file: string
}

export interface ResumeSession {
  session_id: string
  title: string
  jsonl_file: string
  message_count: number
  started_at: string
  updated_at: string
  last_user_at: string
}

export interface StartSessionResult {
  ok: boolean
  session: SessionFile
  messages: ChatMessage[]
}

export interface SendMessageResult {
  ok: boolean
  session: SessionFile
  messages: ChatMessage[]
  assistant: ChatMessage | null
  error: string | null
}

export interface ListResumableResult {
  ok: boolean
  sessions: ResumeSession[]
}

export interface ResumeSessionResult {
  ok: boolean
  session: SessionFile
  summary: ResumeSession
  messages: ChatMessage[]
}

export interface SaveSessionResult {
  ok: boolean
  saved: {
    session_id: string
    status: string
    title: string
    jsonl_file: string
    metadata_file: string
    message_count: number
    started_at: string
    updated_at: string
    saved_at: string
  }
}

export interface FinalizeSessionResult {
  ok: boolean
  jsonl_file: string
  raw_file: string
  task_file: string
  imported_at: string
  codex_updated: boolean
  git_committed: boolean
}
