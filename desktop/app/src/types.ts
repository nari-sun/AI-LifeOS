export type Role = "user" | "assistant"

export interface ChatMessage {
  role: Role
  content: string
  timestamp: string
  memory_context?: MemoryContextSummary | null
}

export interface ReadAloudResult {
  request_id: string
  voice: string
  audio_file: string
  audio_path: string
}

export interface ReadAloudAudioChunk {
  request_id: string
  voice: string
  index: number
  audio_file: string
  audio_path: string
}

export interface ReadAloudStreamEvent {
  type: "audio"
  audio: ReadAloudAudioChunk
}

export interface ReadAloudStreamResult {
  request_id: string
  voice: string
  chunk_count: number
}

export interface CancelReadAloudResult {
  request_id: string
  cancelled: boolean
}

export interface DiscardReadAloudAudioResult {
  audio_file: string
  removed: boolean
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

export interface AttachmentPayload {
  name: string
  extension: string
  size_bytes: number
  text?: string
  data_base64?: string
  truncated?: boolean
}

export interface AttachmentResult {
  file_name: string
  extension: string
  size_bytes: number
  status: "extracted" | "error"
  error: string | null
  extracted_chars: number
  truncated: boolean
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
  attachments: AttachmentResult[]
  error: string | null
  cancelled: boolean
}

export interface AssistantStreamEvent {
  type: "delta"
  delta: string
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

export interface FinalizeJob {
  job_id: string
  name: string
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled"
  stage: string | null
  message: string | null
  error: string | null
  percent: number
  session_file: string
  log_path: string
  cancel_file: string
  created_at: string
  started_at: string | null
  finished_at: string | null
  launcher_pid: number | null
  worker_pid: number | null
  result: FinalizeSessionResult | null
}

export interface FinalizeJobResult {
  ok: boolean
  job: FinalizeJob
}

export interface OrganizeSessionsFailure {
  session_id: string
  error: string
}

export interface OrganizeSessionsJob {
  job_id: string
  name: "organize-sessions"
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled"
  stage: string | null
  message: string | null
  error: string | null
  percent: number
  total_sessions: number
  completed_count: number
  failed_count: number
  skipped_count: number
  failed_sessions: OrganizeSessionsFailure[]
  current_session: string | null
  log_path: string
  cancel_file: string
  created_at: string
  started_at: string | null
  finished_at: string | null
  launcher_pid: number | null
  worker_pid: number | null
  result: {
    total_sessions: number
    completed_sessions: string[]
    failed_sessions: OrganizeSessionsFailure[]
    skipped_sessions: string[]
  } | null
}

export interface OrganizeSessionsJobResult {
  ok: boolean
  job: OrganizeSessionsJob
}

export interface StartOrganizeSessionsJobResult {
  ok: boolean
  job: OrganizeSessionsJob | null
  eligible_count: number
}

export interface LocalDataDirectoryReport {
  path: string
  exists: boolean
  file_count: number
  directory_count: number
  total_bytes: number
  newest_file: string | null
  newest_modified_at: string | null
  errors: string[]
}

export interface LocalDataFileReport {
  path: string
  exists: boolean
  size_bytes: number
  modified_at: string | null
  error?: string
}

export interface LocalDataReport {
  root: string
  read_only: boolean
  directories: Record<string, LocalDataDirectoryReport>
  search_index: LocalDataFileReport
  totals: {
    existing_directories: number
    file_count: number
    total_bytes: number
  }
}

export interface LocalDataReportResult {
  ok: boolean
  report: LocalDataReport
}

export interface ChatGptImportConversation {
  source_id: string
  title: string
  created_at: string | null
  updated_at: string | null
  message_count: number
  duplicate: boolean
}

export interface ChatGptImportPreview {
  source: string
  total_count: number
  new_count: number
  duplicate_count: number
  conversations: ChatGptImportConversation[]
}

export interface ChatGptImportPreviewResult {
  ok: boolean
  source: string
  total_count: number
  new_count: number
  duplicate_count: number
  conversations: ChatGptImportConversation[]
}

export interface ChatGptImportApplyResult {
  ok: boolean
  source: string
  selected_count: number
  imported_count: number
  duplicate_count: number
  imported: Array<{
    source_id: string
    title: string
    raw_file: string | null
  }>
}
