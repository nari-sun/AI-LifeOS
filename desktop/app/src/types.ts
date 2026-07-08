export type Role = "user" | "assistant"

export interface ChatMessage {
  role: Role
  content: string
  timestamp: string
  memory_context?: MemoryContextSummary | null
}

export interface MemoryContextReference {
  path: string
  document_type: string
  title: string
  date: string | null
  snippet: string
  score: number
}

export interface MemoryContextSummary {
  used: boolean
  should_use: boolean
  score: number
  threshold: number
  reasons: string[]
  reference_count: number
  references: MemoryContextReference[]
}

export type OrganizeStageName = "raw" | "memory" | "index"
export type OrganizeStageStatus = "pending" | "done" | "failed"

export interface OrganizeStage {
  name: OrganizeStageName
  label: string
  status: OrganizeStageStatus
}

export interface SessionOrganization {
  status: string
  label: string
  can_organize: boolean
  is_organized: boolean
  next_stage: OrganizeStageName | null
  failed_stage: OrganizeStageName | null
  last_error: string | null
  raw_file: string | null
  task_file: string | null
  current_message_count: number
  current_updated_at: string | null
  organized_message_count: number
  organized_updated_at: string | null
  stages: Record<OrganizeStageName, OrganizeStage>
}

export interface SessionFile {
  session_id: string
  jsonl_file: string
  organization: SessionOrganization
}

export interface ResumeSession {
  session_id: string
  title: string
  jsonl_file: string
  message_count: number
  started_at: string
  updated_at: string
  last_user_at: string
  organization: SessionOrganization
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
  memory_context: MemoryContextSummary
  error: string | null
  cancelled: boolean
}

export interface CancelMessageResult {
  ok: boolean
  request_id: string
  cancelled: boolean
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
  session: SessionFile
  jsonl_file: string
  raw_file: string
  task_file: string
  imported_at: string
  codex_updated: boolean
  git_committed: boolean
  organization: SessionOrganization
}

export interface CleanupExpiredResult {
  ok: boolean
  results: Array<{
    session_id: string
    status: string
    deleted_paths: string[]
    raw_file: string | null
    error: string | null
  }>
}
