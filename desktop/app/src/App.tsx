import { useEffect, useMemo, useRef, useState } from "react"
import type { ReactNode } from "react"
import { convertFileSrc } from "@tauri-apps/api/core"
import {
  AlertTriangle,
  Archive,
  Bot,
  Brain,
  ChevronDown,
  CheckCircle2,
  Clipboard,
  ClipboardCheck,
  Database,
  FileUp,
  FileText,
  FolderOpen,
  HardDrive,
  Loader2,
  MessageSquarePlus,
  Paperclip,
  RefreshCw,
  RotateCcw,
  Send,
  Settings,
  Square,
  User,
  Volume2,
  X,
} from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import {
  filterChatGptImportConversations,
  hasInvalidChatGptImportDateRange,
  isChatGptImportEligible,
} from "@/chatgptImportFilters"
import { cn } from "@/lib/utils"
import {
  cancelMessage,
  cancelReadAloud,
  cancelFinalizeJob,
  cancelOrganizeSessionsJob,
  chooseChatGptExportFile,
  chooseChatGptExportFolder,
  discardReadAloudAudio,
  applyChatGptImport,
  getFinalizeJob,
  getLocalDataReport,
  getMemorySummary,
  getOrganizeSessionsJob,
  getPersonalization,
  isTauriRuntime,
  listResumableSessions,
  openLocalDataFolder,
  previewChatGptImport,
  readAloudStream,
  resumeSession,
  sendMessageStream,
  startFinalizeJob,
  startOrganizeSessionsJob,
  startSession,
  updatePersonalization,
} from "@/tauri"
import type {
  AttachmentPayload,
  ChatGptImportConversation,
  ChatGptImportPreview,
  ChatMessage,
  FinalizeJob,
  LocalDataReport,
  MemoryContextReference,
  MemorySummary,
  MemoryContextSummary,
  PersonalizationSettings,
  ReadAloudAudioChunk,
  ResumeSession,
  OrganizeSessionsJob,
  SessionFile,
  SessionPersonalization,
  SessionOrganization,
} from "@/types"

type BusyState = "idle" | "starting" | "generating" | "stopping" | "resuming" | "finalizing" | "refreshing" | "importing"
type ReplyState = "idle" | "generating" | "stopping" | "stopped" | "failed" | "completed"
type ViewMode = "chat" | "data" | "organize" | "import" | "personalization"
type PendingMessageStatus = "sending" | "failed"
type ReadAloudStatus = "synthesizing" | "playing" | "stopping"

interface PendingUserMessage extends ChatMessage {
  pending_status: PendingMessageStatus
}

interface AttachmentDraft {
  id: string
  name: string
  extension: string
  size_bytes: number
  status: "ready" | "error"
  error: string | null
  text?: string
  data_base64?: string
  extracted_chars: number
  truncated: boolean
}

interface ActiveReadAloud {
  requestId: string
  messageKey: string
  status: ReadAloudStatus
  audioPath: string | null
  queuedCount: number
}

interface SessionPersonalizationDraft {
  temporary: boolean
  memory_enabled: boolean
  past_chat_search_enabled: boolean
  project_scope: string | null
}

const busyLabel: Record<BusyState, string> = {
  idle: "",
  starting: "起動中",
  generating: "生成中",
  stopping: "停止中",
  resuming: "再開中",
  finalizing: "整理中",
  refreshing: "更新中",
  importing: "インポート中",
}

const replyStateLabel: Record<ReplyState, string> = {
  idle: "待機中",
  generating: "生成中",
  stopping: "停止要求中",
  stopped: "停止済み",
  failed: "失敗",
  completed: "完了",
}

const MAX_ATTACHMENTS = 3
const MAX_ATTACHMENT_BYTES = 1024 * 1024
const MAX_ATTACHMENT_TEXT_CHARS = 12_000
const SUPPORTED_ATTACHMENT_EXTENSIONS = new Set(["txt", "md", "pdf", "xlsx"])
const FINALIZE_JOB_STORAGE_KEY = "ai-lifeos.active-finalize-job"
const ORGANIZE_SESSIONS_JOB_STORAGE_KEY = "ai-lifeos.organize-sessions-job"
const TTS_VOICES = [
  { id: "jf_alpha", label: "jf_alpha（女性）" },
  { id: "jf_gongitsune", label: "jf_gongitsune（女性）" },
  { id: "jf_nezumi", label: "jf_nezumi（女性）" },
  { id: "jf_tebukuro", label: "jf_tebukuro（女性）" },
  { id: "jm_kumo", label: "jm_kumo（男性）" },
]

function App() {
  const [session, setSession] = useState<SessionFile | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [sessions, setSessions] = useState<ResumeSession[]>([])
  const [viewMode, setViewMode] = useState<ViewMode>("chat")
  const [managementExpanded, setManagementExpanded] = useState(false)
  const [input, setInput] = useState("")
  const [busy, setBusy] = useState<BusyState>("idle")
  const [replyState, setReplyState] = useState<ReplyState>("idle")
  const [notice, setNotice] = useState("AI-LifeOS Chat")
  const [error, setError] = useState<string | null>(null)
  const [lastMemoryContext, setLastMemoryContext] = useState<MemoryContextSummary | null>(null)
  const [lastMemoryCandidates, setLastMemoryCandidates] = useState<MemoryContextReference[]>([])
  const [lastMemoryOpened, setLastMemoryOpened] = useState<MemoryContextReference[]>([])
  const [lastSubmittedText, setLastSubmittedText] = useState("")
  const [pendingUserMessage, setPendingUserMessage] = useState<PendingUserMessage | null>(null)
  const [streamingAssistant, setStreamingAssistant] = useState<ChatMessage | null>(null)
  const [attachments, setAttachments] = useState<AttachmentDraft[]>([])
  const [fullArchiveReview, setFullArchiveReview] = useState(false)
  const [activeRequestId, setActiveRequestId] = useState<string | null>(null)
  const [finalizeJob, setFinalizeJob] = useState<FinalizeJob | null>(null)
  const [organizeSessionsJob, setOrganizeSessionsJob] = useState<OrganizeSessionsJob | null>(null)
  const [localDataReport, setLocalDataReport] = useState<LocalDataReport | null>(null)
  const [localDataLoading, setLocalDataLoading] = useState(false)
  const [personalizationSettings, setPersonalizationSettings] = useState<PersonalizationSettings | null>(null)
  const [sessionPersonalization, setSessionPersonalization] = useState<SessionPersonalization | null>(null)
  const [personalizationDraft, setPersonalizationDraft] = useState<PersonalizationSettings | null>(null)
  const [sessionPersonalizationDraft, setSessionPersonalizationDraft] = useState<SessionPersonalizationDraft | null>(null)
  const [personalizationLoading, setPersonalizationLoading] = useState(false)
  const [personalizationSaving, setPersonalizationSaving] = useState(false)
  const [memorySummary, setMemorySummary] = useState<MemorySummary | null>(null)
  const [personalizationSettingsFile, setPersonalizationSettingsFile] = useState("memory/personalization_settings.json")
  const [chatGptImportSourcePath, setChatGptImportSourcePath] = useState<string | null>(null)
  const [chatGptImportPreview, setChatGptImportPreview] = useState<ChatGptImportPreview | null>(null)
  const [chatGptImportSelectedIds, setChatGptImportSelectedIds] = useState<string[]>([])
  const [ttsVoice, setTtsVoice] = useState("jf_alpha")
  const [activeReadAloud, setActiveReadAloud] = useState<ActiveReadAloud | null>(null)
  const [readAloudError, setReadAloudError] = useState<string | null>(null)
  const viewportRef = useRef<HTMLDivElement | null>(null)
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const initializedRef = useRef(false)
  const activeRequestIdRef = useRef<string | null>(null)
  const sessionRef = useRef<SessionFile | null>(null)
  const personalizationRequestRef = useRef(0)
  const activeReadAloudRef = useRef<ActiveReadAloud | null>(null)
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const readAloudQueueRef = useRef<ReadAloudAudioChunk[]>([])
  const readAloudSynthesisFinishedRef = useRef(false)
  const streamingTextRef = useRef("")
  const streamingFrameRef = useRef<number | null>(null)

  function activateSession(next: SessionFile | null) {
    personalizationRequestRef.current += 1
    sessionRef.current = next
    setSession(next)
    setFullArchiveReview(false)
    setPersonalizationLoading(false)
    setPersonalizationSaving(false)
  }

  function updateCurrentSession(next: SessionFile) {
    if (sessionRef.current?.jsonl_file !== next.jsonl_file) {
      return false
    }
    sessionRef.current = next
    setSession(next)
    return true
  }

  function isCurrentPersonalizationRequest(requestToken: number, sessionFile: string | null) {
    return personalizationRequestRef.current === requestToken
      && (sessionRef.current?.jsonl_file ?? null) === sessionFile
  }

  function clearLastMemoryRetrieval() {
    setLastMemoryContext(null)
    setLastMemoryCandidates([])
    setLastMemoryOpened([])
  }

  function clearStreamingAssistant() {
    streamingTextRef.current = ""
    if (streamingFrameRef.current !== null) {
      window.cancelAnimationFrame(streamingFrameRef.current)
      streamingFrameRef.current = null
    }
    setStreamingAssistant(null)
  }

  function queueStreamingDelta(requestId: string, delta: string, timestamp: string) {
    streamingTextRef.current += delta
    if (streamingFrameRef.current !== null) {
      return
    }

    streamingFrameRef.current = window.requestAnimationFrame(() => {
      streamingFrameRef.current = null
      if (activeRequestIdRef.current !== requestId || !streamingTextRef.current) {
        return
      }
      setStreamingAssistant({
        role: "assistant",
        content: streamingTextRef.current,
        timestamp,
      })
    })
  }

  const isBusy = busy !== "idle"
  const isFinalizeActive = finalizeJob?.status === "queued" || finalizeJob?.status === "running"
  const isOrganizeSessionsActive = organizeSessionsJob?.status === "queued" || organizeSessionsJob?.status === "running"
  const isOrganizationActive = isFinalizeActive || isOrganizeSessionsActive
  const isGenerating = busy === "generating" || busy === "stopping"
  const hasAttachmentError = attachments.some((attachment) => attachment.status === "error")
  const canReviewFullArchive = Boolean(session?.personalization.past_chat_search_enabled)
  const canSend = input.trim().length > 0 && !isBusy && !isOrganizationActive && !hasAttachmentError
  const canStop = busy === "generating" && activeRequestId !== null
  const canRestoreInput = lastSubmittedText.trim().length > 0 && !isBusy
  const organization = session?.organization ?? null
  const canFinalize = Boolean(session && organization?.can_organize && !isBusy && !isOrganizationActive)
  const finalizeButtonLabel = getFinalizeButtonLabel(organization)
  const statusLabel = organization?.label ?? "未開始"
  const eligibleSessionCount = sessions.filter((item) => item.organization.can_organize).length
  const chatGptImportSelectedCount = chatGptImportSelectedIds.length
  const displayMessages = [
    ...messages,
    ...(pendingUserMessage ? [pendingUserMessage] : []),
    ...(streamingAssistant ? [streamingAssistant] : []),
  ]

  const sessionTitle = useMemo(() => {
    if (!session) {
      return "未開始"
    }
    return session.session_id
  }, [session])

  useEffect(() => {
    if (initializedRef.current) {
      return
    }
    initializedRef.current = true

    if (!isTauriRuntime()) {
      setError("Tauri 環境で起動してください。")
      return
    }

    void initialize()
  }, [])

  useEffect(() => () => {
    if (streamingFrameRef.current !== null) {
      window.cancelAnimationFrame(streamingFrameRef.current)
    }
  }, [])

  useEffect(() => {
    const cancelActiveRequest = () => {
      const requestId = activeRequestIdRef.current
      if (requestId) {
        void cancelMessage(requestId).catch(() => undefined)
      }
    }

    window.addEventListener("beforeunload", cancelActiveRequest)
    return () => {
      window.removeEventListener("beforeunload", cancelActiveRequest)
      cancelActiveRequest()
    }
  }, [])

  useEffect(() => {
    const node = viewportRef.current
    if (node) {
      node.scrollTop = node.scrollHeight
    }
  }, [displayMessages, isGenerating])

  useEffect(() => () => {
    const active = activeReadAloudRef.current
    const queuedAudio = readAloudQueueRef.current.splice(0)
    audioRef.current?.pause()
    audioRef.current = null
    if (active?.requestId) {
      void cancelReadAloud(active.requestId)
    }
    if (active?.audioPath) {
      void discardReadAloudAudio(active.audioPath)
    }
    for (const audio of queuedAudio) {
      void discardReadAloudAudio(audio.audio_path)
    }
  }, [])

  useEffect(() => {
    if (viewMode === "data" && !localDataReport && !localDataLoading && isTauriRuntime()) {
      void refreshLocalDataReport()
    }
  }, [viewMode, localDataReport, localDataLoading])

  useEffect(() => {
    if (viewMode === "personalization" && isTauriRuntime()) {
      void refreshPersonalization()
    }
  }, [viewMode, session?.jsonl_file])

  useEffect(() => {
    if (!finalizeJob || (finalizeJob.status !== "queued" && finalizeJob.status !== "running")) {
      return
    }

    const timer = window.setInterval(() => {
      void pollFinalizeJob(finalizeJob.job_id)
    }, 1200)
    return () => window.clearInterval(timer)
  }, [finalizeJob?.job_id, finalizeJob?.status])

  useEffect(() => {
    if (!organizeSessionsJob || (organizeSessionsJob.status !== "queued" && organizeSessionsJob.status !== "running")) {
      return
    }

    const timer = window.setInterval(() => {
      void pollOrganizeSessionsJob(organizeSessionsJob.job_id)
    }, 1200)
    return () => window.clearInterval(timer)
  }, [organizeSessionsJob?.job_id, organizeSessionsJob?.status])

  async function initialize() {
    setBusy("starting")
    setReplyState("idle")
    setError(null)
    try {
      const listPromise = listResumableSessions()
      const trackedJobId = readTrackedFinalizeJobId()
      let restored = false

      if (trackedJobId) {
        try {
          const jobResult = await getFinalizeJob(trackedJobId)
          const resumed = await resumeSession(finalizeSessionId(jobResult.job.session_file))
          activateSession(resumed.session)
          setMessages(resumed.messages)
          setFinalizeJob(jobResult.job)
          setPendingUserMessage(null)
          setStreamingAssistant(null)
          setAttachments([])
          clearLastMemoryRetrieval()
          restored = true

          if (jobResult.job.status === "failed") {
            clearTrackedFinalizeJob(trackedJobId)
            setReplyState("failed")
            setError(`整理処理に失敗しました: ${jobResult.job.error ?? jobResult.job.message ?? "不明なエラー"}`)
            setNotice("再起動前の整理ジョブを回収しました。必要なら再実行できます。")
          } else if (jobResult.job.status === "cancelled") {
            clearTrackedFinalizeJob(trackedJobId)
            setNotice("再起動前の整理ジョブはキャンセル済みです。")
          } else if (jobResult.job.status === "succeeded") {
            clearTrackedFinalizeJob(trackedJobId)
            setNotice("再起動前の整理ジョブは完了しています。")
          } else {
            setNotice("再起動前の整理ジョブへ再接続しました。")
          }
        } catch {
          clearTrackedFinalizeJob(trackedJobId)
        }
      }

      if (!restored) {
        const sessionResult = await startSession()
        activateSession(sessionResult.session)
        setMessages(sessionResult.messages)
        setPendingUserMessage(null)
        setStreamingAssistant(null)
        setAttachments([])
        clearLastMemoryRetrieval()
        setNotice("新規セッションを開始しました。")
      }

      const listResult = await listPromise
      setSessions(listResult.sessions)

      if (!trackedJobId) {
        const trackedOrganizeJobId = readTrackedOrganizeSessionsJobId()
        if (trackedOrganizeJobId) {
          try {
            const result = await getOrganizeSessionsJob(trackedOrganizeJobId)
            setOrganizeSessionsJob(result.job)
            setManagementExpanded(true)
            setViewMode("organize")
            if (result.job.status === "failed") {
              clearTrackedOrganizeSessionsJob(trackedOrganizeJobId)
              setError(`データ整理に失敗しました: ${result.job.error ?? result.job.message ?? "不明なエラー"}`)
            } else if (result.job.status === "cancelled") {
              clearTrackedOrganizeSessionsJob(trackedOrganizeJobId)
              setNotice("再起動前のデータ整理はキャンセル済みです。")
            } else if (result.job.status === "succeeded") {
              clearTrackedOrganizeSessionsJob(trackedOrganizeJobId)
              setNotice("再起動前のデータ整理は完了しています。")
            } else {
              setNotice("再起動前のデータ整理へ再接続しました。")
            }
          } catch {
            clearTrackedOrganizeSessionsJob(trackedOrganizeJobId)
          }
        }
      }
    } catch (err) {
      setReplyState("failed")
      setError(withNextAction(formatError(err)))
    } finally {
      setBusy("idle")
    }
  }

  async function refreshSessions() {
    if (isBusy) {
      return
    }

    setBusy("refreshing")
    setError(null)
    try {
      const result = await listResumableSessions()
      setSessions(result.sessions)
      setNotice("セッション一覧を更新しました。")
    } catch (err) {
      setReplyState("failed")
      setError(withNextAction(formatError(err)))
    } finally {
      setBusy("idle")
    }
  }

  async function createSession() {
    if (isBusy || isOrganizationActive || personalizationLoading || personalizationSaving) {
      return
    }

    setBusy("starting")
    setReplyState("idle")
    setError(null)
    try {
      const result = await startSession()
      activateSession(result.session)
      setMessages(result.messages)
      clearLastMemoryRetrieval()
      setInput("")
      setPendingUserMessage(null)
      setStreamingAssistant(null)
      setAttachments([])
      setFinalizeJob(null)
      setViewMode("chat")
      setNotice("新規セッションを開始しました。")
      await refreshSessionsAfterAction()
    } catch (err) {
      setReplyState("failed")
      setError(withNextAction(formatError(err)))
    } finally {
      setBusy("idle")
    }
  }

  async function submitMessage(overrideContent?: string) {
    const content = (overrideContent ?? input).trim()
    if (!content || isBusy || isOrganizationActive || hasAttachmentError) {
      return
    }

    const requestId = createRequestId()
    const requestFullArchiveReview = fullArchiveReview && canReviewFullArchive
    const attachmentPayloads = attachments.filter((attachment) => attachment.status === "ready").map(attachmentToPayload)
    activeRequestIdRef.current = requestId
    setActiveRequestId(requestId)
    setBusy("generating")
    setReplyState("generating")
    setError(null)
    clearLastMemoryRetrieval()
    setInput("")
    setLastSubmittedText(content)
    clearStreamingAssistant()
    setPendingUserMessage({
      role: "user",
      content: buildPendingUserContent(content, attachments),
      timestamp: new Date().toISOString(),
      pending_status: "sending",
    })
    setNotice("返答を生成しています。停止ボタンで中断できます。")

    try {
      const streamTimestamp = new Date().toISOString()
      const result = await sendMessageStream(
        session?.jsonl_file ?? null,
        content,
        requestId,
        attachmentPayloads,
        requestFullArchiveReview,
        (delta) => {
          if (activeRequestIdRef.current !== requestId) {
            return
          }
          queueStreamingDelta(requestId, delta, streamTimestamp)
        },
      )
      if (activeRequestIdRef.current !== requestId) {
        return
      }

      activateSession(result.session)
      const memoryContext = result.assistant ? result.memory_context : null
      const memoryCandidates = result.assistant ? result.memory_candidates ?? [] : []
      const memoryOpened = result.assistant ? result.memory_opened ?? [] : []
      setMessages(attachMemoryContextToLatestAssistant(result.messages, memoryContext, memoryCandidates, memoryOpened))
      setPendingUserMessage(null)
      clearStreamingAssistant()
      setAttachments([])
      setLastMemoryContext(memoryContext)
      setLastMemoryCandidates(memoryCandidates)
      setLastMemoryOpened(memoryOpened)
      const attachmentNotice = formatAttachmentResultNotice(result.attachments)
      if (result.cancelled) {
        setReplyState("stopped")
        setNotice(compactNotice("返答生成を停止しました。ユーザー発言は live JSONL に保存されています。", attachmentNotice))
      } else if (result.error) {
        setReplyState("failed")
        setError(withNextAction(result.error))
        setNotice(compactNotice("返答生成に失敗しました。", attachmentNotice))
      } else {
        setReplyState("completed")
        setNotice(compactNotice(result.assistant ? "返答を保存しました。" : "入力を保存しました。", attachmentNotice))
      }
      await refreshSessionsAfterAction()
    } catch (err) {
      if (activeRequestIdRef.current === requestId) {
        setReplyState("failed")
        setError(withNextAction(formatError(err)))
        setNotice("返答生成に失敗しました。")
        setPendingUserMessage((current) => (current ? { ...current, pending_status: "failed" } : current))
        clearStreamingAssistant()
      }
    } finally {
      if (activeRequestIdRef.current === requestId) {
        activeRequestIdRef.current = null
        setActiveRequestId(null)
        setBusy("idle")
      }
    }
  }

  async function stopGeneration() {
    if (!canStop || !activeRequestId) {
      return
    }

    setBusy("stopping")
    setReplyState("stopping")
    setNotice("停止要求を送信しています。")
    setError(null)
    try {
      await cancelMessage(activeRequestId)
      setNotice("停止要求を送信しました。Codex CLI の終了を待っています。")
    } catch (err) {
      setBusy("generating")
      setReplyState("generating")
      setError(`停止要求に失敗しました: ${formatError(err)}\n次の操作: 生成完了を待つか、アプリを再起動してください。`)
    }
  }

  function restoreLastSubmittedText() {
    if (!canRestoreInput) {
      return
    }
    setInput(lastSubmittedText)
    setError(null)
    setNotice("直前の入力を入力欄へ戻しました。必要なら修正して新規メッセージとして送信してください。")
  }

  async function loadSession(sessionId: string) {
    if (isBusy || isOrganizationActive || personalizationLoading || personalizationSaving) {
      return
    }

    setBusy("resuming")
    setReplyState("idle")
    setError(null)
    try {
      const result = await resumeSession(sessionId)
      activateSession(result.session)
      setMessages(result.messages)
      clearLastMemoryRetrieval()
      setPendingUserMessage(null)
      setStreamingAssistant(null)
      setAttachments([])
      setFinalizeJob(null)
      setViewMode("chat")
      setNotice("セッションを再開しました。")
    } catch (err) {
      setReplyState("failed")
      setError(withNextAction(formatError(err)))
    } finally {
      setBusy("idle")
    }
  }

  async function finalizeCurrentSession() {
    if (!session || !organization?.can_organize || isBusy || isOrganizationActive) {
      return
    }
    const confirmed = window.confirm("raw.md 作成、記憶整理、検索 index 更新を実行します。時間がかかる場合があります。")
    if (!confirmed) {
      return
    }

    setBusy("finalizing")
    setError(null)
    try {
      const result = await startFinalizeJob(session.jsonl_file)
      trackFinalizeJob(result.job.job_id)
      setFinalizeJob(result.job)
      setNotice("整理ジョブをバックグラウンドで開始しました。")
      await refreshSessionsAfterAction()
    } catch (err) {
      setReplyState("failed")
      setError(withNextAction(formatError(err)))
    } finally {
      setBusy("idle")
    }
  }

  async function pollFinalizeJob(jobId: string) {
    try {
      const result = await getFinalizeJob(jobId)
      setFinalizeJob(result.job)
      if (result.job.status === "succeeded" && result.job.result) {
        clearTrackedFinalizeJob(jobId)
        activateSession(result.job.result.session)
        setNotice(`整理済み: ${result.job.result.raw_file}`)
        await refreshSessionsAfterAction()
      } else if (result.job.status === "failed") {
        clearTrackedFinalizeJob(jobId)
        setReplyState("failed")
        setError(`整理処理に失敗しました: ${result.job.error ?? result.job.message ?? "不明なエラー"}`)
      } else if (result.job.status === "cancelled") {
        clearTrackedFinalizeJob(jobId)
        setNotice("整理ジョブをキャンセルしました。必要なら再度実行してください。")
      }
    } catch (err) {
      setError(withNextAction(formatError(err)))
    }
  }

  async function cancelCurrentFinalizeJob() {
    if (!finalizeJob || !isFinalizeActive) {
      return
    }
    try {
      const result = await cancelFinalizeJob(finalizeJob.job_id)
      setFinalizeJob(result.job)
      setNotice("整理ジョブへキャンセル要求を送信しました。")
    } catch (err) {
      setError(`整理ジョブのキャンセル要求に失敗しました: ${formatError(err)}`)
    }
  }

  async function organizeUnorganizedSessions() {
    if (isBusy || isOrganizationActive) {
      return
    }
    if (eligibleSessionCount === 0) {
      setNotice("整理対象のセッションはありません。")
      return
    }
    const confirmed = window.confirm(
      `未整理または整理失敗の ${eligibleSessionCount} 件を古い順に整理します。途中の失敗は記録して次のセッションへ進みます。`,
    )
    if (!confirmed) {
      return
    }

    setBusy("finalizing")
    setError(null)
    try {
      const result = await startOrganizeSessionsJob()
      if (!result.job) {
        setNotice("整理対象のセッションはありません。")
        await refreshSessionsAfterAction()
        return
      }
      trackOrganizeSessionsJob(result.job.job_id)
      setOrganizeSessionsJob(result.job)
      setManagementExpanded(true)
      setViewMode("organize")
      setNotice(`データ整理を開始しました（${result.eligible_count}件）。`)
    } catch (err) {
      setError(`データ整理の開始に失敗しました: ${formatError(err)}`)
    } finally {
      setBusy("idle")
    }
  }

  async function pollOrganizeSessionsJob(jobId: string) {
    try {
      const result = await getOrganizeSessionsJob(jobId)
      setOrganizeSessionsJob(result.job)
      if (result.job.status === "succeeded") {
        clearTrackedOrganizeSessionsJob(jobId)
        setNotice(result.job.message ?? "データ整理が完了しました。")
        await refreshSessionsAfterAction()
      } else if (result.job.status === "failed") {
        clearTrackedOrganizeSessionsJob(jobId)
        setError(`データ整理に失敗しました: ${result.job.error ?? result.job.message ?? "不明なエラー"}`)
        await refreshSessionsAfterAction()
      } else if (result.job.status === "cancelled") {
        clearTrackedOrganizeSessionsJob(jobId)
        setNotice("データ整理を停止しました。未処理のセッションは後で再実行できます。")
        await refreshSessionsAfterAction()
      }
    } catch (err) {
      setError(withNextAction(formatError(err)))
    }
  }

  async function cancelOrganizeSessions() {
    if (!organizeSessionsJob || !isOrganizeSessionsActive) {
      return
    }
    try {
      const result = await cancelOrganizeSessionsJob(organizeSessionsJob.job_id)
      setOrganizeSessionsJob(result.job)
      setNotice("データ整理へ停止要求を送信しました。")
    } catch (err) {
      setError(`データ整理の停止要求に失敗しました: ${formatError(err)}`)
    }
  }

  async function selectChatGptExportSource(kind: "file" | "folder") {
    if (isBusy || isOrganizationActive) {
      return
    }

    setBusy("importing")
    setError(null)
    try {
      const source = kind === "file" ? await chooseChatGptExportFile() : await chooseChatGptExportFolder()
      if (!source) {
        setNotice("ChatGPTエクスポートの選択をキャンセルしました。")
        return
      }

      setChatGptImportSourcePath(null)
      setChatGptImportPreview(null)
      setChatGptImportSelectedIds([])
      const preview = await previewChatGptImport(source)
      setChatGptImportSourcePath(source)
      setChatGptImportPreview(preview)
      setChatGptImportSelectedIds([])
      setNotice(`取り込み前確認を完了しました（新規 ${preview.new_count}件、更新 ${preview.updated_count}件、変更なし ${preview.duplicate_count}件、競合 ${preview.conflict_count}件）。`)
    } catch (err) {
      setError(`ChatGPTエクスポートを確認できませんでした: ${formatError(err)}`)
    } finally {
      setBusy("idle")
    }
  }

  function toggleChatGptImportConversation(sourceId: string) {
    setChatGptImportSelectedIds((current) => (
      current.includes(sourceId) ? current.filter((item) => item !== sourceId) : [...current, sourceId]
    ))
  }

  function selectChatGptImportConversations(sourceIds: string[]) {
    setChatGptImportSelectedIds(sourceIds)
  }

  async function applySelectedChatGptImport(selectedIds: string[]) {
    if (!chatGptImportSourcePath || !chatGptImportPreview || selectedIds.length === 0 || isBusy || isOrganizationActive) {
      return
    }

    const confirmed = window.confirm(
      `表示中から選択した ${selectedIds.length} 件を raw.md と import_metadata.json として取り込み、検索indexを更新します。summary、journal、memoryは更新しません。`,
    )
    if (!confirmed) {
      return
    }

    setBusy("importing")
    setError(null)
    try {
      const result = await applyChatGptImport(chatGptImportSourcePath, selectedIds)
      const refreshedPreview = await previewChatGptImport(chatGptImportSourcePath)
      setChatGptImportPreview(refreshedPreview)
      setChatGptImportSelectedIds([])
      setLocalDataReport(null)
      const indexMessage = result.index_updated
        ? `検索indexも更新しました（${result.index_status}）。`
        : (result.index_error ?? `検索indexは更新できませんでした（${result.index_status}）。`)
      setNotice(`ChatGPT会話を処理しました（新規 ${result.imported_count}件、更新 ${result.updated_count}件、変更なし ${result.duplicate_count}件）。${indexMessage} 記憶整理は必要な会話だけ後から実行してください。`)
    } catch (err) {
      setError(`ChatGPT会話の取り込みに失敗しました: ${formatError(err)}`)
    } finally {
      setBusy("idle")
    }
  }

  async function refreshLocalDataReport() {
    if (localDataLoading) {
      return
    }
    setLocalDataLoading(true)
    setError(null)
    try {
      const result = await getLocalDataReport()
      setLocalDataReport(result.report)
      setNotice("ローカルデータ状況を更新しました。")
    } catch (err) {
      setError(withNextAction(formatError(err)))
    } finally {
      setLocalDataLoading(false)
    }
  }

  async function openDataFolder(folder: string) {
    try {
      await openLocalDataFolder(folder)
    } catch (err) {
      setError(`フォルダを開けませんでした: ${formatError(err)}`)
    }
  }

  async function refreshPersonalization() {
    if (personalizationLoading || personalizationSaving) {
      return
    }
    const requestedSessionFile = sessionRef.current?.jsonl_file ?? null
    const requestToken = personalizationRequestRef.current + 1
    personalizationRequestRef.current = requestToken
    setPersonalizationLoading(true)
    setError(null)
    try {
      const [result, summaryResult] = await Promise.all([
        getPersonalization(requestedSessionFile),
        getMemorySummary(),
      ])
      if (!isCurrentPersonalizationRequest(requestToken, requestedSessionFile)) {
        return
      }
      setPersonalizationSettings(result.settings)
      setSessionPersonalization(result.session)
      setPersonalizationSettingsFile(result.settings_file)
      setMemorySummary(summaryResult.summary)
      setPersonalizationDraft({ ...result.settings })
      setSessionPersonalizationDraft(result.session ? {
        temporary: result.session.temporary,
        memory_enabled: result.session.memory_enabled,
        past_chat_search_enabled: result.session.past_chat_search_enabled,
        project_scope: result.session.project_scope,
      } : null)
      if (result.session_state) {
        updateCurrentSession(result.session_state)
      }
      setNotice("パーソナライズ設定と記憶プレビューを読み込みました。")
    } catch (err) {
      if (isCurrentPersonalizationRequest(requestToken, requestedSessionFile)) {
        setError(`パーソナライズ情報を読み込めませんでした: ${formatError(err)}`)
      }
    } finally {
      if (isCurrentPersonalizationRequest(requestToken, requestedSessionFile)) {
        setPersonalizationLoading(false)
      }
    }
  }

  async function savePersonalization() {
    if (!personalizationDraft || personalizationSaving || personalizationLoading) {
      return
    }
    const requestedSessionFile = sessionRef.current?.jsonl_file ?? null
    const requestToken = personalizationRequestRef.current + 1
    personalizationRequestRef.current = requestToken
    setPersonalizationSaving(true)
    setError(null)
    try {
      const settings: PersonalizationSettings = {
        memory_enabled: personalizationDraft.memory_enabled,
        past_chat_search_enabled: personalizationDraft.past_chat_search_enabled,
        project_scope: personalizationDraft.project_scope?.trim() || null,
      }
      const result = await updatePersonalization(requestedSessionFile, settings, null)
      if (!isCurrentPersonalizationRequest(requestToken, requestedSessionFile)) {
        return
      }
      setPersonalizationSettings(result.settings)
      setPersonalizationSettingsFile(result.settings_file)
      setPersonalizationDraft({ ...result.settings })
      clearLastMemoryRetrieval()
      setNotice("新しいセッションに使うパーソナライズ既定値を保存しました。")
      await refreshSessionsAfterAction()
    } catch (err) {
      if (isCurrentPersonalizationRequest(requestToken, requestedSessionFile)) {
        setError(`パーソナライズ設定を保存できませんでした: ${formatError(err)}`)
      }
    } finally {
      if (isCurrentPersonalizationRequest(requestToken, requestedSessionFile)) {
        setPersonalizationSaving(false)
      }
    }
  }

  async function saveSessionPersonalization() {
    const requestedSession = sessionRef.current
    if (!requestedSession || !sessionPersonalizationDraft || personalizationSaving || personalizationLoading) {
      return
    }
    const requestedSessionFile = requestedSession.jsonl_file
    const requestToken = personalizationRequestRef.current + 1
    personalizationRequestRef.current = requestToken
    setPersonalizationSaving(true)
    setError(null)
    try {
      const sessionSettings: SessionPersonalizationDraft = {
        temporary: sessionPersonalizationDraft.temporary,
        memory_enabled: sessionPersonalizationDraft.memory_enabled,
        past_chat_search_enabled: sessionPersonalizationDraft.past_chat_search_enabled,
        project_scope: sessionPersonalizationDraft.project_scope?.trim() || null,
      }
      const result = await updatePersonalization(requestedSessionFile, null, sessionSettings)
      if (!isCurrentPersonalizationRequest(requestToken, requestedSessionFile)) {
        return
      }
      setPersonalizationSettings(result.settings)
      setSessionPersonalization(result.session)
      setPersonalizationSettingsFile(result.settings_file)
      if (result.session_state) {
        updateCurrentSession(result.session_state)
      }
      setSessionPersonalizationDraft(result.session ? {
        temporary: result.session.temporary,
        memory_enabled: result.session.memory_enabled,
        past_chat_search_enabled: result.session.past_chat_search_enabled,
        project_scope: result.session.project_scope,
      } : null)
      clearLastMemoryRetrieval()
      setNotice(result.session?.temporary
        ? "このセッションを一時チャットとして固定しました。会話ログは保持し、記憶整理から除外します。"
        : "現在のセッションのパーソナライズ設定を保存しました。")
      await refreshSessionsAfterAction()
    } catch (err) {
      if (isCurrentPersonalizationRequest(requestToken, requestedSessionFile)) {
        setError(`セッション設定を保存できませんでした: ${formatError(err)}`)
      }
    } finally {
      if (isCurrentPersonalizationRequest(requestToken, requestedSessionFile)) {
        setPersonalizationSaving(false)
      }
    }
  }

  async function handleAttachmentInputChange(event: React.ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? [])
    event.target.value = ""
    if (files.length === 0) {
      return
    }

    const availableSlots = Math.max(0, MAX_ATTACHMENTS - attachments.length)
    const selected = files.slice(0, availableSlots)
    if (selected.length < files.length) {
      setError(`添付ファイルは最大${MAX_ATTACHMENTS}件までです。`)
    }

    const drafts = await Promise.all(selected.map(fileToAttachmentDraft))
    setAttachments((current) => [...current, ...drafts].slice(0, MAX_ATTACHMENTS))
  }

  function removeAttachment(id: string) {
    setAttachments((current) => current.filter((attachment) => attachment.id !== id))
  }

  async function refreshSessionsAfterAction() {
    try {
      const result = await listResumableSessions()
      setSessions(result.sessions)
    } catch {
      // The primary action succeeded; keep the UI usable if the sidebar refresh fails.
    }
  }

  function handleInputKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault()
      void submitMessage()
    }
  }

  function updateActiveReadAloud(next: ActiveReadAloud | null) {
    activeReadAloudRef.current = next
    setActiveReadAloud(next)
  }

  async function discardTemporaryReadAloudAudio(audioPath: string | null) {
    if (!audioPath) {
      return
    }
    try {
      await discardReadAloudAudio(audioPath)
    } catch {
      // A stale temporary WAV is cleaned by the bridge on the next read-aloud request.
    }
  }

  async function discardReadAloudChunks(chunks: ReadAloudAudioChunk[]) {
    await Promise.all(chunks.map((chunk) => discardTemporaryReadAloudAudio(chunk.audio_path)))
  }

  async function finishReadAloud(requestId: string) {
    const active = activeReadAloudRef.current
    if (!active || active.requestId !== requestId) {
      return
    }

    const queuedAudio = readAloudQueueRef.current.splice(0)
    audioRef.current?.pause()
    audioRef.current = null
    readAloudSynthesisFinishedRef.current = false
    updateActiveReadAloud(null)
    await Promise.all([
      discardTemporaryReadAloudAudio(active.audioPath),
      discardReadAloudChunks(queuedAudio),
    ])
  }

  async function playNextReadAloudChunk(requestId: string) {
    const active = activeReadAloudRef.current
    if (!active || active.requestId !== requestId || audioRef.current) {
      return
    }

    const next = readAloudQueueRef.current.shift()
    if (!next) {
      if (readAloudSynthesisFinishedRef.current) {
        await finishReadAloud(requestId)
      }
      return
    }

    const audio = new Audio(convertFileSrc(next.audio_path))
    audio.preload = "auto"
    let settled = false
    const finishChunk = (playbackError?: string) => {
      if (settled) {
        return
      }
      settled = true
      if (audioRef.current === audio) {
        audioRef.current = null
      }
      if (playbackError && activeReadAloudRef.current?.requestId === requestId) {
        setReadAloudError(playbackError)
      }
      void discardTemporaryReadAloudAudio(next.audio_path)
      void playNextReadAloudChunk(requestId)
    }

    audioRef.current = audio
    updateActiveReadAloud({
      ...active,
      status: "playing",
      audioPath: next.audio_path,
      queuedCount: readAloudQueueRef.current.length,
    })
    audio.onended = () => finishChunk()
    audio.onerror = () => finishChunk("読み上げ音声を再生できませんでした。")
    try {
      await audio.play()
      if (activeReadAloudRef.current?.requestId === requestId) {
        setNotice(`読み上げ中: ${next.voice}（${next.index + 1}文目）`)
      }
    } catch {
      finishChunk("読み上げ音声を再生できませんでした。")
    }
  }

  function enqueueReadAloudChunk(chunk: ReadAloudAudioChunk) {
    const active = activeReadAloudRef.current
    if (!active || active.requestId !== chunk.request_id) {
      void discardTemporaryReadAloudAudio(chunk.audio_path)
      return
    }

    readAloudQueueRef.current.push(chunk)
    updateActiveReadAloud({ ...active, queuedCount: readAloudQueueRef.current.length })
    void playNextReadAloudChunk(chunk.request_id)
  }

  async function stopReadAloud() {
    const active = activeReadAloudRef.current
    if (!active) {
      return
    }

    updateActiveReadAloud({ ...active, status: "stopping" })
    const queuedAudio = readAloudQueueRef.current.splice(0)
    audioRef.current?.pause()
    audioRef.current = null
    readAloudSynthesisFinishedRef.current = false
    updateActiveReadAloud(null)
    try {
      await cancelReadAloud(active.requestId)
    } catch {
      // Playback is already stopped in the WebView. The bridge cleanup is best-effort.
    }
    await Promise.all([
      discardTemporaryReadAloudAudio(active.audioPath),
      discardReadAloudChunks(queuedAudio),
    ])
  }

  async function startReadAloud(messageKey: string, text: string) {
    if (!isTauriRuntime() || !text.trim()) {
      return
    }

    if (activeReadAloudRef.current) {
      await stopReadAloud()
    }

    const requestId = createRequestId()
    readAloudQueueRef.current = []
    readAloudSynthesisFinishedRef.current = false
    updateActiveReadAloud({ requestId, messageKey, status: "synthesizing", audioPath: null, queuedCount: 0 })
    setReadAloudError(null)
    setNotice("最初の文の読み上げ音声を準備しています。")
    try {
      await readAloudStream(text, ttsVoice, requestId, enqueueReadAloudChunk)
      if (activeReadAloudRef.current?.requestId !== requestId) {
        return
      }
      readAloudSynthesisFinishedRef.current = true
      void playNextReadAloudChunk(requestId)
    } catch (err) {
      if (activeReadAloudRef.current?.requestId === requestId) {
        setReadAloudError(formatError(err))
        readAloudSynthesisFinishedRef.current = true
        void playNextReadAloudChunk(requestId)
      }
    }
  }

  return (
    <main className="flex h-screen min-h-0 bg-background text-foreground">
      <aside className="flex w-80 shrink-0 flex-col border-r border-border bg-sidebar">
        <div className="flex h-14 items-center justify-between border-b border-border px-4">
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold">AI-LifeOS</div>
            <div className="truncate text-xs text-muted-foreground">Chat GUI</div>
          </div>
          <Button variant="ghost" size="icon" onClick={refreshSessions} disabled={isBusy} title="更新">
            <RefreshCw className={cn("h-4 w-4", busy === "refreshing" && "animate-spin")} />
          </Button>
        </div>

        <div className="space-y-2 border-b border-border p-3">
          <Button className="w-full justify-start" variant="secondary" onClick={createSession} disabled={isBusy || isOrganizationActive || personalizationLoading || personalizationSaving}>
            <MessageSquarePlus className="h-4 w-4" />
            新規チャット
          </Button>
          <Button
            className="w-full justify-start"
            variant={viewMode === "data" || viewMode === "organize" || viewMode === "import" || viewMode === "personalization" ? "secondary" : "ghost"}
            onClick={() => setManagementExpanded((current) => !current)}
          >
            <Settings className="h-4 w-4" />
            管理
            <ChevronDown className={cn("ml-auto h-4 w-4 transition-transform", managementExpanded && "rotate-180")} />
          </Button>
          {managementExpanded && (
            <div className="space-y-1 border-l border-border pl-3">
              <Button
                className="w-full justify-start"
                variant={viewMode === "personalization" ? "secondary" : "ghost"}
                onClick={() => {
                  setManagementExpanded(true)
                  setViewMode("personalization")
                }}
              >
                <Brain className="h-4 w-4" />
                パーソナライズ
              </Button>
              <Button
                className="w-full justify-start"
                variant={viewMode === "data" ? "secondary" : "ghost"}
                onClick={() => {
                  setManagementExpanded(true)
                  setViewMode("data")
                }}
              >
                <Database className="h-4 w-4" />
                ローカルデータ
              </Button>
              <Button
                className="w-full justify-start"
                variant={viewMode === "organize" ? "secondary" : "ghost"}
                onClick={() => {
                  setManagementExpanded(true)
                  setViewMode("organize")
                }}
              >
                <Archive className="h-4 w-4" />
                データ整理
                {eligibleSessionCount > 0 && <Badge variant="outline" className="ml-auto">{eligibleSessionCount}</Badge>}
              </Button>
              <Button
                className="w-full justify-start"
                variant={viewMode === "import" ? "secondary" : "ghost"}
                onClick={() => {
                  setManagementExpanded(true)
                  setViewMode("import")
                }}
              >
                <FileUp className="h-4 w-4" />
                ChatGPTインポート
              </Button>
            </div>
          )}
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-3 py-3">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-xs font-medium text-muted-foreground">再開可能</span>
            <Badge variant="outline">{sessions.length}</Badge>
          </div>

          <div className="space-y-2">
            {sessions.length === 0 ? (
              <div className="rounded-md border border-dashed border-border px-3 py-6 text-center text-sm text-muted-foreground">
                セッションなし
              </div>
            ) : (
              sessions.map((item) => (
                <button
                  key={item.session_id}
                  className={cn(
                    "w-full rounded-md border border-border bg-background px-3 py-2 text-left transition-colors hover:bg-accent",
                    session?.session_id === item.session_id && "border-primary bg-primary/10",
                  )}
                  onClick={() => void loadSession(item.session_id)}
                  disabled={isBusy || isOrganizationActive || personalizationLoading || personalizationSaving}
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-medium">{item.title || item.session_id}</div>
                      <div className="mt-1 truncate text-xs text-muted-foreground">{item.session_id}</div>
                    </div>
                    <div className="flex shrink-0 flex-col items-end gap-1">
                      <Badge variant="secondary">{item.message_count}</Badge>
                      <Badge variant={item.organization.is_organized ? "secondary" : "outline"}>{item.organization.label}</Badge>
                    </div>
                  </div>
                  <div className="mt-2 text-xs text-muted-foreground">{formatDateTime(item.last_user_at)}</div>
                </button>
              ))
            )}
          </div>
        </div>
      </aside>

      <section className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center justify-between border-b border-border px-4">
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold">
              {viewMode === "personalization" ? "パーソナライズと記憶" : viewMode === "data" ? "ローカルデータ管理" : viewMode === "organize" ? "データ整理" : viewMode === "import" ? "ChatGPTエクスポートを取り込む" : sessionTitle}
            </div>
            <div className="truncate text-xs text-muted-foreground">
              {viewMode === "personalization" ? personalizationSettingsFile : viewMode === "data" || viewMode === "organize" || viewMode === "import" ? (localDataReport?.root ?? "AI-LifeOS root") : (session?.jsonl_file ?? "inbox/live")}
            </div>
          </div>
          <div className="flex items-center gap-2">
            {viewMode === "personalization" ? (
              <>
                <Badge variant="secondary">明示操作時のみ保存</Badge>
                <Button variant="outline" onClick={() => void refreshPersonalization()} disabled={personalizationLoading || personalizationSaving}>
                  <RefreshCw className={cn("h-4 w-4", personalizationLoading && "animate-spin")} />
                  更新
                </Button>
              </>
            ) : viewMode === "data" ? (
              <>
                <Badge variant="secondary">読み取り専用</Badge>
                <Button variant="outline" onClick={() => void refreshLocalDataReport()} disabled={localDataLoading}>
                  <RefreshCw className={cn("h-4 w-4", localDataLoading && "animate-spin")} />
                  更新
                </Button>
              </>
            ) : viewMode === "organize" ? (
              <>
                <Badge variant="secondary">対象 {eligibleSessionCount}件</Badge>
                {isOrganizeSessionsActive && <Badge>実行中</Badge>}
              </>
            ) : viewMode === "import" ? (
              <>
                <Badge variant="secondary">dry-run確認</Badge>
                {chatGptImportPreview && <Badge variant="outline">選択 {chatGptImportSelectedCount}件</Badge>}
              </>
            ) : (
              <>
                {busy !== "idle" && (
                  <Badge>
                    <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                    {busyLabel[busy]}
                  </Badge>
                )}
                <Badge variant={replyState === "failed" ? "outline" : "secondary"}>{replyStateLabel[replyState]}</Badge>
                {session?.personalization.temporary && <Badge variant="outline">一時チャット</Badge>}
                {session?.personalization.project_scope && <Badge variant="outline">project: {session.personalization.project_scope}</Badge>}
                {session && !session.personalization.memory_enabled && !session.personalization.past_chat_search_enabled && <Badge variant="outline">記憶参照OFF</Badge>}
                {session?.personalization.memory_enabled === false && session.personalization.past_chat_search_enabled && <Badge variant="outline">長期memory OFF</Badge>}
                {session?.personalization.memory_enabled && session.personalization.past_chat_search_enabled === false && <Badge variant="outline">過去検索OFF</Badge>}
                <Badge variant={organization?.is_organized ? "secondary" : "outline"}>{statusLabel}</Badge>
                <Button variant="outline" onClick={finalizeCurrentSession} disabled={!canFinalize}>
                  <Archive className="h-4 w-4" />
                  {finalizeButtonLabel}
                </Button>
              </>
            )}
          </div>
        </header>

        {viewMode === "personalization" ? (
          <PersonalizationScreen
            settings={personalizationSettings}
            sessionSettings={sessionPersonalization}
            draft={personalizationDraft}
            sessionDraft={sessionPersonalizationDraft}
            summary={memorySummary}
            settingsFile={personalizationSettingsFile}
            hasSession={Boolean(session)}
            loading={personalizationLoading}
            saving={personalizationSaving}
            error={error}
            onDraftChange={setPersonalizationDraft}
            onSessionDraftChange={setSessionPersonalizationDraft}
            onSave={() => void savePersonalization()}
            onSaveSession={() => void saveSessionPersonalization()}
            onRefresh={() => void refreshPersonalization()}
          />
        ) : viewMode === "data" ? (
          <DataManagementScreen report={localDataReport} loading={localDataLoading} onRefresh={refreshLocalDataReport} onOpenFolder={openDataFolder} />
        ) : viewMode === "organize" ? (
          <OrganizeSessionsScreen
            eligibleSessionCount={eligibleSessionCount}
            job={organizeSessionsJob}
            disabled={isBusy || isOrganizationActive}
            onStart={organizeUnorganizedSessions}
            onCancel={cancelOrganizeSessions}
          />
        ) : viewMode === "import" ? (
          <ChatGptImportScreen
            preview={chatGptImportPreview}
            selectedIds={chatGptImportSelectedIds}
            loading={busy === "importing"}
            disabled={isBusy || isOrganizationActive}
            onChooseFile={() => void selectChatGptExportSource("file")}
            onChooseFolder={() => void selectChatGptExportSource("folder")}
            onToggle={toggleChatGptImportConversation}
            onSelectNew={selectChatGptImportConversations}
            onClearSelection={() => setChatGptImportSelectedIds([])}
            onApply={(selectedIds) => void applySelectedChatGptImport(selectedIds)}
          />
        ) : (
          <>
        <div className="border-b border-border bg-muted/40 px-4 py-2">
          <div className="flex min-h-8 flex-wrap items-center gap-2 text-sm">
            {error ? (
              <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
                <AlertTriangle className="h-4 w-4 shrink-0 text-destructive" />
                <span className="min-w-0 flex-1 whitespace-pre-wrap break-words text-destructive">{error}</span>
                <Button type="button" size="sm" variant="outline" onClick={restoreLastSubmittedText} disabled={!canRestoreInput}>
                  <RotateCcw className="h-3.5 w-3.5" />
                  入力に戻す
                </Button>
                <Button type="button" size="sm" variant="ghost" onClick={() => void refreshSessions()} disabled={isBusy}>
                  <RefreshCw className="h-3.5 w-3.5" />
                  一覧更新
                </Button>
              </div>
            ) : (
              <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
                <CheckCircle2 className="h-4 w-4 shrink-0 text-primary" />
                <span className="min-w-0 break-words text-muted-foreground">{notice}</span>
              </div>
            )}
            {organization && <OrganizeStages organization={organization} running={busy === "finalizing" || isFinalizeActive} />}
          </div>
          {organization?.last_error && !error && (
            <div className="mt-1 break-words text-xs text-destructive">{organization.last_error}</div>
          )}
          {readAloudError && (
            <div className="mt-2 flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span className="break-words">読み上げ: {readAloudError}</span>
            </div>
          )}
          {finalizeJob && <FinalizeJobPanel job={finalizeJob} onCancel={cancelCurrentFinalizeJob} />}
          {lastMemoryContext && (
            <MemoryContextDetails
              context={lastMemoryContext}
              candidates={lastMemoryCandidates}
              opened={lastMemoryOpened}
              className="mt-2"
            />
          )}
        </div>

        <div ref={viewportRef} className="min-h-0 flex-1 overflow-y-auto px-5 py-5">
          <div className="mx-auto flex max-w-4xl flex-col gap-4">
            {displayMessages.length === 0 ? (
              <div className="flex min-h-80 items-center justify-center rounded-md border border-dashed border-border text-sm text-muted-foreground">
                メッセージなし
              </div>
            ) : (
              displayMessages.map((message, index) => {
                const messageKey = `${message.timestamp}-${index}`
                return (
                  <MessageBubble
                    key={messageKey}
                    message={message}
                    messageKey={messageKey}
                    ttsVoice={ttsVoice}
                    onTtsVoiceChange={setTtsVoice}
                    readAloudStatus={activeReadAloud?.messageKey === messageKey ? activeReadAloud.status : null}
                    onStartReadAloud={startReadAloud}
                    onStopReadAloud={stopReadAloud}
                  />
                )
              })
            )}
            {isGenerating && !streamingAssistant && <GeneratingRow stopping={busy === "stopping"} />}
          </div>
        </div>

        <form
          className="shrink-0 border-t border-border bg-background px-5 py-4"
          onSubmit={(event) => {
            event.preventDefault()
            void submitMessage()
          }}
        >
          <input
            ref={fileInputRef}
            type="file"
            className="hidden"
            multiple
            accept=".txt,.md,.pdf,.xlsx,text/plain,text/markdown,application/pdf,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            onChange={(event) => void handleAttachmentInputChange(event)}
          />
          {attachments.length > 0 && (
            <div className="mx-auto mb-3 flex max-w-4xl flex-wrap gap-2">
              {attachments.map((attachment) => (
                <AttachmentPill key={attachment.id} attachment={attachment} onRemove={() => removeAttachment(attachment.id)} />
              ))}
            </div>
          )}
          <div className="mx-auto flex max-w-4xl items-end gap-3">
            <Button
              type="button"
              size="icon"
              variant="outline"
              onClick={() => fileInputRef.current?.click()}
              disabled={isBusy || isFinalizeActive || attachments.length >= MAX_ATTACHMENTS}
              title="ファイル添付"
            >
              <Paperclip className="h-4 w-4" />
            </Button>
            <label
              className={cn(
                "flex h-9 shrink-0 cursor-pointer items-center gap-1.5 rounded-md border border-input bg-background px-2 text-xs",
                !canReviewFullArchive && "cursor-not-allowed opacity-50",
              )}
              title={canReviewFullArchive
                ? "この送信では、対象となる過去の会話をすべて確認してから回答します"
                : "パーソナライズ設定で過去チャット検索をONにすると利用できます"}
            >
              <input
                type="checkbox"
                checked={fullArchiveReview && canReviewFullArchive}
                disabled={!canReviewFullArchive || isBusy || isOrganizationActive}
                onChange={(event) => setFullArchiveReview(event.target.checked)}
                className="h-4 w-4 shrink-0 accent-primary"
              />
              <span>全参照</span>
            </label>
            <Textarea
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={handleInputKeyDown}
              disabled={busy === "starting" || busy === "resuming" || busy === "finalizing" || busy === "refreshing" || isFinalizeActive}
              placeholder={isFinalizeActive ? "整理ジョブの完了後に送信できます" : isGenerating ? "生成中でも次の入力を下書きできます" : "メッセージを入力"}
              className="max-h-44 min-h-20 resize-none"
            />
            {isGenerating ? (
              <Button type="button" size="icon" variant="destructive" onClick={stopGeneration} disabled={!canStop} title="返答生成を停止">
                {busy === "stopping" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Square className="h-4 w-4" />}
              </Button>
            ) : (
              <Button type="submit" size="icon" disabled={!canSend} title="送信">
                <Send className="h-4 w-4" />
              </Button>
            )}
          </div>
        </form>
          </>
        )}
      </section>
    </main>
  )
}

function MessageBubble({
  message,
  messageKey,
  ttsVoice,
  onTtsVoiceChange,
  readAloudStatus,
  onStartReadAloud,
  onStopReadAloud,
}: {
  message: ChatMessage | PendingUserMessage
  messageKey: string
  ttsVoice: string
  onTtsVoiceChange: (voice: string) => void
  readAloudStatus: ReadAloudStatus | null
  onStartReadAloud: (messageKey: string, text: string) => Promise<void>
  onStopReadAloud: () => Promise<void>
}) {
  const isUser = message.role === "user"
  const pendingStatus = "pending_status" in message ? message.pending_status : null
  return (
    <div className={cn("flex gap-3", isUser ? "justify-end" : "justify-start")}>
      {!isUser && (
        <div className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-secondary text-secondary-foreground">
          <Bot className="h-4 w-4" />
        </div>
      )}
      <div className={cn("max-w-[78%]", isUser && "order-first")}>
        <div
          className={cn(
            "rounded-md px-4 py-3 text-sm leading-6 shadow-sm",
            isUser ? "bg-primary text-primary-foreground" : "border border-border bg-surface text-foreground",
          )}
        >
          {!isUser && (
            <div className="mb-2 flex flex-wrap items-center justify-end gap-2">
              <label className="flex items-center gap-1 text-xs text-muted-foreground">
                <Volume2 className="h-3.5 w-3.5" />
                <span className="sr-only">読み上げ音声</span>
                <select
                  value={ttsVoice}
                  onChange={(event) => onTtsVoiceChange(event.target.value)}
                  className="h-7 max-w-44 rounded-md border border-border bg-background px-2 text-xs text-foreground"
                  aria-label="読み上げ音声"
                >
                  {TTS_VOICES.map((voice) => <option key={voice.id} value={voice.id}>{voice.label}</option>)}
                </select>
              </label>
              <CopyButton text={message.content} label="返答をコピー" />
              {readAloudStatus ? (
                <Button type="button" size="sm" variant="outline" onClick={() => void onStopReadAloud()}>
                  {readAloudStatus === "synthesizing" || readAloudStatus === "stopping" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Square className="h-3.5 w-3.5" />}
                  停止
                </Button>
              ) : (
                <Button type="button" size="sm" variant="outline" onClick={() => void onStartReadAloud(messageKey, message.content)}>
                  <Volume2 className="h-3.5 w-3.5" />
                  読み上げ
                </Button>
              )}
            </div>
          )}
          {isUser ? <div className="whitespace-pre-wrap break-words">{message.content}</div> : <MarkdownContent content={message.content} />}
          {!isUser && message.memory_context && (
            <MemoryContextDetails
              context={message.memory_context}
              candidates={message.memory_candidates ?? []}
              opened={message.memory_opened ?? []}
              compact
              className="mt-3"
            />
          )}
        </div>
        <div className={cn("mt-1 text-xs text-muted-foreground", isUser ? "text-right" : "text-left")}>
          {pendingStatus ? `${formatDateTime(message.timestamp)}・${pendingStatus === "sending" ? "送信中" : "未同期"}` : formatDateTime(message.timestamp)}
        </div>
      </div>
      {isUser && (
        <div className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-primary text-primary-foreground">
          <User className="h-4 w-4" />
        </div>
      )}
    </div>
  )
}

function GeneratingRow({ stopping }: { stopping: boolean }) {
  return (
    <div className="flex gap-3">
      <div className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-secondary text-secondary-foreground">
        <Bot className="h-4 w-4" />
      </div>
      <div className="rounded-md border border-border bg-surface px-4 py-3 text-sm text-muted-foreground shadow-sm">
        <div className="flex items-center gap-2">
          <Loader2 className="h-4 w-4 animate-spin" />
          {stopping ? "停止処理中です。" : "返答を生成しています。"}
        </div>
      </div>
    </div>
  )
}

function AttachmentPill({ attachment, onRemove }: { attachment: AttachmentDraft; onRemove: () => void }) {
  return (
    <div
      className={cn(
        "flex max-w-full items-center gap-2 rounded-md border px-2.5 py-1.5 text-xs",
        attachment.status === "error" ? "border-destructive/40 bg-destructive/5 text-destructive" : "border-border bg-muted text-foreground",
      )}
    >
      <FileText className="h-3.5 w-3.5 shrink-0" />
      <span className="min-w-0 truncate">{attachment.name}</span>
      <span className="shrink-0 text-muted-foreground">
        {attachment.status === "error" ? attachment.error : `${formatBytes(attachment.size_bytes)} / ${attachment.extracted_chars}字`}
      </span>
      {attachment.truncated && <Badge variant="outline">切詰</Badge>}
      <Button type="button" size="icon" variant="ghost" className="h-5 w-5 shrink-0" onClick={onRemove} title="添付を外す">
        <X className="h-3 w-3" />
      </Button>
    </div>
  )
}

function FinalizeJobPanel({ job, onCancel }: { job: FinalizeJob; onCancel: () => void }) {
  const active = job.status === "queued" || job.status === "running"
  return (
    <div className="mt-2 flex flex-wrap items-center gap-2 rounded-md border border-border bg-background px-3 py-2 text-xs">
      {active ? <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" /> : <Archive className="h-3.5 w-3.5 text-muted-foreground" />}
      <span className="font-medium">整理ジョブ</span>
      <Badge variant={job.status === "failed" ? "outline" : "secondary"}>{job.status}</Badge>
      <span className="text-muted-foreground">{job.percent ?? 0}%</span>
      <span className="min-w-0 flex-1 break-words text-muted-foreground">{job.error ?? job.message}</span>
      {active && (
        <Button type="button" size="sm" variant="outline" onClick={onCancel}>
          <Square className="h-3.5 w-3.5" />
          停止
        </Button>
      )}
    </div>
  )
}

function OrganizeSessionsScreen({
  eligibleSessionCount,
  job,
  disabled,
  onStart,
  onCancel,
}: {
  eligibleSessionCount: number
  job: OrganizeSessionsJob | null
  disabled: boolean
  onStart: () => void
  onCancel: () => void
}) {
  const active = job?.status === "queued" || job?.status === "running"
  const failedSessions = job?.failed_sessions ?? []

  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5">
      <div className="mx-auto flex max-w-4xl flex-col gap-5">
        <section className="rounded-md border border-border p-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <div className="flex items-center gap-2 text-sm font-semibold">
                <Archive className="h-4 w-4" />
                未整理セッションを順に整理
              </div>
              <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">
                再開可能なセッションのうち、未整理・新規会話あり・整理失敗のものを古い順に1件ずつ処理します。失敗したセッションは記録し、残りの処理を続けます。
              </p>
            </div>
            <Button onClick={onStart} disabled={disabled || eligibleSessionCount === 0}>
              <Archive className="h-4 w-4" />
              {eligibleSessionCount > 0 ? `${eligibleSessionCount}件を整理` : "整理対象なし"}
            </Button>
          </div>
        </section>

        <section className="grid gap-3 sm:grid-cols-4">
          <DataMetric icon={<Archive className="h-4 w-4" />} label="整理対象" value={`${eligibleSessionCount}件`} />
          <DataMetric icon={<CheckCircle2 className="h-4 w-4" />} label="完了" value={`${job?.completed_count ?? 0}件`} />
          <DataMetric icon={<AlertTriangle className="h-4 w-4" />} label="失敗" value={`${job?.failed_count ?? 0}件`} />
          <DataMetric icon={<RotateCcw className="h-4 w-4" />} label="スキップ" value={`${job?.skipped_count ?? 0}件`} />
        </section>

        {job && (
          <section className="rounded-md border border-border p-4">
            <div className="flex flex-wrap items-center gap-2">
              {active ? <Loader2 className="h-4 w-4 animate-spin text-primary" /> : <Archive className="h-4 w-4" />}
              <span className="font-medium">データ整理ジョブ</span>
              <Badge variant={job.status === "failed" ? "outline" : "secondary"}>{job.status}</Badge>
              <span className="text-sm text-muted-foreground">{job.percent}%</span>
              {active && (
                <Button type="button" size="sm" variant="outline" className="ml-auto" onClick={onCancel}>
                  <Square className="h-3.5 w-3.5" />
                  停止
                </Button>
              )}
            </div>
            <div className="mt-3 break-words text-sm text-muted-foreground">{job.error ?? job.message}</div>
            {job.current_session && <div className="mt-2 text-xs text-muted-foreground">処理中: {job.current_session}</div>}
            {failedSessions.length > 0 && (
              <div className="mt-4 rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm">
                <div className="mb-2 flex items-center gap-2 font-medium text-destructive">
                  <AlertTriangle className="h-4 w-4" />
                  失敗したセッション
                </div>
                <ul className="space-y-2 text-xs text-muted-foreground">
                  {failedSessions.map((item) => (
                    <li key={item.session_id} className="break-words">
                      <span className="font-mono text-foreground">{item.session_id}</span>: {item.error}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </section>
        )}
      </div>
    </div>
  )
}

function ChatGptImportScreen({
  preview,
  selectedIds,
  loading,
  disabled,
  onChooseFile,
  onChooseFolder,
  onToggle,
  onSelectNew,
  onClearSelection,
  onApply,
}: {
  preview: ChatGptImportPreview | null
  selectedIds: string[]
  loading: boolean
  disabled: boolean
  onChooseFile: () => void
  onChooseFolder: () => void
  onToggle: (sourceId: string) => void
  onSelectNew: (sourceIds: string[]) => void
  onClearSelection: () => void
  onApply: (selectedIds: string[]) => void
}) {
  const [query, setQuery] = useState("")
  const [fromDate, setFromDate] = useState("")
  const [toDate, setToDate] = useState("")
  const selectedIdSet = useMemo(() => new Set(selectedIds), [selectedIds])
  const invalidDateRange = hasInvalidChatGptImportDateRange(fromDate, toDate)
  const filteredConversations = useMemo(() => {
    return filterChatGptImportConversations(preview?.conversations ?? [], {
      query,
      fromDate,
      toDate,
    })
  }, [preview, query, fromDate, toDate, invalidDateRange])
  const filteredEligibleIds = filteredConversations.filter(isChatGptImportEligible).map((item) => item.source_id)
  const visibleSelectedIds = filteredEligibleIds.filter((sourceId) => selectedIdSet.has(sourceId))

  function updateFilter(setter: (value: string) => void, value: string) {
    // A changed filter may hide previously selected conversations.  Clear the
    // selection so only an explicit selection in the current view can apply.
    onClearSelection()
    setter(value)
  }

  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5">
      <div className="mx-auto flex max-w-5xl flex-col gap-5">
        <section className="rounded-md border border-border p-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <div className="flex items-center gap-2 text-sm font-semibold">
                <FileUp className="h-4 w-4" />
                ChatGPTエクスポートを確認して取り込む
              </div>
              <p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">
                最初の確認ではファイルを書き換えません。会話を選択して最終確認した後だけ、raw.mdとimport_metadata.jsonを作成し、派生検索indexを再構築します。summary、journal、memoryは更新しません。
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button type="button" variant="outline" onClick={onChooseFolder} disabled={disabled}>
                <FolderOpen className="h-4 w-4" />
                フォルダを選択
              </Button>
              <Button type="button" onClick={onChooseFile} disabled={disabled}>
                {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileUp className="h-4 w-4" />}
                ZIP / 会話JSONを選択
              </Button>
            </div>
          </div>
        </section>

        {!preview ? (
          <section className="rounded-md border border-dashed border-border px-4 py-10 text-center text-sm text-muted-foreground">
            ChatGPTのエクスポートZIP、展開済みフォルダ、または conversations.json / conversations-*.json を選択してください。
          </section>
        ) : (
          <>
            <section className="grid gap-3 sm:grid-cols-3 xl:grid-cols-6">
              <DataMetric icon={<FileText className="h-4 w-4" />} label="エクスポート内" value={`${preview.total_count}件`} />
              <DataMetric icon={<CheckCircle2 className="h-4 w-4" />} label="新規" value={`${preview.new_count}件`} />
              <DataMetric icon={<RefreshCw className="h-4 w-4" />} label="更新あり" value={`${preview.updated_count}件`} />
              <DataMetric icon={<RotateCcw className="h-4 w-4" />} label="変更なし" value={`${preview.duplicate_count}件`} />
              <DataMetric icon={<AlertTriangle className="h-4 w-4" />} label="競合・要確認" value={`${preview.conflict_count}件`} />
              <DataMetric icon={<FileUp className="h-4 w-4" />} label="表示中の選択" value={`${visibleSelectedIds.length}件`} />
            </section>

            <section className="rounded-md border border-border p-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="min-w-0">
                  <div className="text-sm font-medium">{preview.source}</div>
                  <div className="mt-1 text-xs text-muted-foreground">新規と更新ありだけを選択できます。変更なしと競合は自動適用せず、既存rawを保護します。</div>
                </div>
                <div className="flex flex-wrap gap-2">
                  <Button type="button" size="sm" variant="outline" onClick={() => onSelectNew(filteredEligibleIds)} disabled={disabled || filteredEligibleIds.length === 0}>
                    表示中の新規・更新を選択
                  </Button>
                  <Button type="button" size="sm" variant="ghost" onClick={onClearSelection} disabled={disabled || selectedIds.length === 0}>
                    選択を解除
                  </Button>
                  <Button type="button" size="sm" onClick={() => onApply(visibleSelectedIds)} disabled={disabled || visibleSelectedIds.length === 0}>
                    {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileUp className="h-4 w-4" />}
                    表示中の{visibleSelectedIds.length}件を取り込む
                  </Button>
                </div>
              </div>

              <div className="mt-4 grid gap-2 sm:grid-cols-[minmax(0,1fr)_150px_150px]">
                <input
                  type="search"
                  value={query}
                  onChange={(event) => updateFilter(setQuery, event.target.value)}
                  placeholder="タイトルまたは会話IDで絞り込み"
                  className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm outline-none ring-offset-background placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                />
                <label className="flex items-center gap-2 text-xs text-muted-foreground">
                  UTC開始
                  <input
                    type="date"
                    value={fromDate}
                    onChange={(event) => updateFilter(setFromDate, event.target.value)}
                    aria-label="UTC作成日の開始日"
                    aria-invalid={invalidDateRange}
                    className="h-9 min-w-0 flex-1 rounded-md border border-input bg-background px-2 text-sm text-foreground outline-none ring-offset-background focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                  />
                </label>
                <label className="flex items-center gap-2 text-xs text-muted-foreground">
                  UTC終了
                  <input
                    type="date"
                    value={toDate}
                    onChange={(event) => updateFilter(setToDate, event.target.value)}
                    aria-label="UTC作成日の終了日"
                    aria-invalid={invalidDateRange}
                    className="h-9 min-w-0 flex-1 rounded-md border border-input bg-background px-2 text-sm text-foreground outline-none ring-offset-background focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                  />
                </label>
              </div>
              <div className="mt-2 text-xs text-muted-foreground">
                絞り込み条件を変更すると、非表示の会話を誤って取り込まないよう現在の選択を解除します。
              </div>
              {invalidDateRange && (
                <div className="mt-2 flex items-center gap-2 text-xs text-destructive" role="alert">
                  <AlertTriangle className="h-3.5 w-3.5" />
                  UTC開始日はUTC終了日以前にしてください。日付を直すまで選択・取り込みはできません。
                </div>
              )}

              <div className="mt-3 overflow-hidden rounded-md border border-border">
                <div className="grid grid-cols-[32px_minmax(0,1fr)_110px_110px] gap-3 border-b border-border bg-muted px-3 py-2 text-xs font-medium text-muted-foreground">
                  <div></div>
                  <div>会話</div>
                  <div>作成日 (UTC)</div>
                  <div>状態</div>
                </div>
                {filteredConversations.length === 0 ? (
                  <div className="px-3 py-6 text-sm text-muted-foreground">一致する会話はありません。</div>
                ) : (
                  filteredConversations.map((item) => (
                    <label key={item.source_id} className={cn("grid grid-cols-[32px_minmax(0,1fr)_110px_110px] gap-3 border-b border-border px-3 py-2 text-sm last:border-b-0", !isChatGptImportEligible(item) && "bg-muted/40 text-muted-foreground")}>
                      <input
                        type="checkbox"
                        checked={selectedIdSet.has(item.source_id)}
                        disabled={disabled || !isChatGptImportEligible(item)}
                        onChange={() => onToggle(item.source_id)}
                        className="mt-1 h-4 w-4"
                      />
                      <div className="min-w-0">
                        <div className="truncate font-medium">{item.title}</div>
                        <div className="mt-1 truncate font-mono text-xs text-muted-foreground">{item.source_id}</div>
                        <div className="mt-1 text-xs text-muted-foreground">
                          テキスト保存 {item.message_count}/{item.source_message_count}発言
                        </div>
                        {(item.audio_transcription_count > 0 || item.attachment_count > 0 || item.skipped_message_count > 0 || item.non_text_message_count > 0 || item.non_text_part_count > 0 || item.empty_conversation) && (
                          <div className="mt-1 flex flex-wrap gap-1">
                            {item.audio_transcription_count > 0 && <Badge variant="outline">音声文字起こし {item.audio_transcription_count}</Badge>}
                            {item.attachment_count > 0 && <Badge variant="outline">添付 {item.attachment_count}</Badge>}
                            {item.skipped_message_count > 0 && <Badge variant="outline">保存対象外発言 {item.skipped_message_count}</Badge>}
                            {item.non_text_message_count > 0 && <Badge variant="outline">本文なし発言 {item.non_text_message_count}</Badge>}
                            {item.non_text_part_count > 0 && <Badge variant="outline">非テキスト要素 {item.non_text_part_count}</Badge>}
                            {item.empty_conversation && <Badge variant="destructive">保存できるテキストなし</Badge>}
                          </div>
                        )}
                      </div>
                      <div className="text-xs text-muted-foreground">{formatImportDate(item.created_at)}</div>
                      <div>
                        <Badge variant={item.import_state === "conflict" ? "destructive" : item.import_state === "new" ? "secondary" : "outline"}>
                          {chatGptImportStateLabel(item.import_state)}
                        </Badge>
                      </div>
                    </label>
                  ))
                )}
              </div>
            </section>
          </>
        )}
      </div>
    </div>
  )
}

function PersonalizationScreen({
  settings,
  sessionSettings,
  draft,
  sessionDraft,
  summary,
  settingsFile,
  hasSession,
  loading,
  saving,
  error,
  onDraftChange,
  onSessionDraftChange,
  onSave,
  onSaveSession,
  onRefresh,
}: {
  settings: PersonalizationSettings | null
  sessionSettings: SessionPersonalization | null
  draft: PersonalizationSettings | null
  sessionDraft: SessionPersonalizationDraft | null
  summary: MemorySummary | null
  settingsFile: string
  hasSession: boolean
  loading: boolean
  saving: boolean
  error: string | null
  onDraftChange: (draft: PersonalizationSettings) => void
  onSessionDraftChange: (draft: SessionPersonalizationDraft | null) => void
  onSave: () => void
  onSaveSession: () => void
  onRefresh: () => void
}) {
  if (loading && !draft) {
    return (
      <div className="flex min-h-0 flex-1 items-center justify-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        パーソナライズ情報を読み込み中
      </div>
    )
  }

  if (!draft) {
    return (
      <div className="flex min-h-0 flex-1 items-center justify-center">
        <div className="rounded-md border border-dashed border-border p-6 text-center text-sm text-muted-foreground">
          <div>パーソナライズ情報を取得できませんでした。</div>
          <Button type="button" variant="outline" className="mt-3" onClick={onRefresh} disabled={loading}>
            <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
            再読み込み
          </Button>
        </div>
      </div>
    )
  }

  const globalRetrievalEnabled = draft.memory_enabled || draft.past_chat_search_enabled
  const sessionRetrievalEnabled = Boolean(
    sessionDraft
    && (sessionDraft.memory_enabled || sessionDraft.past_chat_search_enabled)
    && !sessionDraft.temporary,
  )
  const savedRetrievalEnabled = Boolean(
    ((sessionSettings?.memory_enabled ?? settings?.memory_enabled)
      || (sessionSettings?.past_chat_search_enabled ?? settings?.past_chat_search_enabled))
    && !sessionSettings?.temporary,
  )

  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5">
      <div className="mx-auto flex max-w-5xl flex-col gap-5">
        {error && (
          <div className="flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <span className="break-words">{error}</span>
          </div>
        )}

        <section className="rounded-md border border-border p-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <div className="flex items-center gap-2 text-sm font-semibold">
                <Brain className="h-4 w-4" />
                新しいセッションの既定値
              </div>
              <p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">
                新規セッションの初期値です。「既定値を保存」を押したときだけ {settingsFile} に書き込みます。現在のセッション設定は変更しません。
              </p>
            </div>
            <Badge variant={globalRetrievalEnabled ? "secondary" : "outline"}>
              既定の記憶参照: {globalRetrievalEnabled ? "ON" : "OFF"}
            </Badge>
          </div>

          <div className="mt-4 grid gap-3 md:grid-cols-2">
            <PersonalizationToggle
              checked={draft.memory_enabled}
              label="長期メモリを使う"
              description="long_term、preferences、projects、構造化メモリを回答の根拠として利用します。"
              disabled={saving}
              onChange={(checked) => onDraftChange({ ...draft, memory_enabled: checked })}
            />
            <PersonalizationToggle
              checked={draft.past_chat_search_enabled}
              label="過去チャットを検索する"
              description="明示的な過去照会で raw.md / live JSONL を読み取り専用検索します。"
              disabled={saving}
              onChange={(checked) => onDraftChange({ ...draft, past_chat_search_enabled: checked })}
            />
          </div>

          <label className="mt-4 block text-sm font-medium">
            既定のProject scope
            <input
              type="text"
              value={draft.project_scope ?? ""}
              maxLength={120}
              placeholder="例: AI-LifeOS / 学習 / 個人"
              disabled={saving}
              onChange={(event) => onDraftChange({ ...draft, project_scope: event.target.value || null })}
              className="mt-2 h-10 w-full rounded-md border border-input bg-background px-3 text-sm outline-none ring-offset-background placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
            />
            <span className="mt-1 block text-xs font-normal text-muted-foreground">
              空欄は全体スコープです。改行・制御文字は保存時に拒否されます。
            </span>
          </label>

          <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4">
            <div className="text-xs text-muted-foreground">長期メモリと過去チャット検索は独立して設定できます。</div>
            <Button type="button" onClick={onSave} disabled={saving || loading}>
              {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}
              既定値を保存
            </Button>
          </div>
        </section>

        <section className="rounded-md border border-border p-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <div className="text-sm font-semibold">現在のセッション</div>
              <p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">
                このセッションだけに適用します。既定値とは別に保存されるため、再開時にも上書きされません。
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              <Badge variant={savedRetrievalEnabled ? "secondary" : "outline"}>
                保存済み: 記憶参照{savedRetrievalEnabled ? "ON" : "OFF"}
              </Badge>
              {sessionSettings?.project_scope && <Badge variant="outline">project: {sessionSettings.project_scope}</Badge>}
            </div>
          </div>

          {!hasSession || !sessionDraft ? (
            <div className="mt-4 rounded-md border border-dashed border-border px-3 py-5 text-sm text-muted-foreground">
              セッション開始後に、この会話専用の設定を変更できます。
            </div>
          ) : (
            <>
              <div className="mt-4 grid gap-3 md:grid-cols-2">
                <PersonalizationToggle
                  checked={sessionDraft.memory_enabled}
                  label="この会話で長期メモリを使う"
                  description="long_term、preferences、projects、構造化メモリを参照候補に含めます。"
                  disabled={saving || sessionDraft.temporary}
                  onChange={(checked) => onSessionDraftChange({ ...sessionDraft, memory_enabled: checked })}
                />
                <PersonalizationToggle
                  checked={sessionDraft.past_chat_search_enabled}
                  label="この会話で過去チャットを検索する"
                  description="raw.md / live JSONLを読み取り専用で検索します。"
                  disabled={saving || sessionDraft.temporary}
                  onChange={(checked) => onSessionDraftChange({ ...sessionDraft, past_chat_search_enabled: checked })}
                />
              </div>

              <label className="mt-4 block text-sm font-medium">
                この会話のProject scope
                <input
                  type="text"
                  value={sessionDraft.project_scope ?? ""}
                  maxLength={120}
                  placeholder="例: AI-LifeOS / 学習 / 個人"
                  disabled={saving || sessionDraft.temporary}
                  onChange={(event) => onSessionDraftChange({ ...sessionDraft, project_scope: event.target.value || null })}
                  className="mt-2 h-10 w-full rounded-md border border-input bg-background px-3 text-sm outline-none ring-offset-background placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                />
                <span className="mt-1 block text-xs font-normal text-muted-foreground">
                  空欄は全体スコープです。scopeは検索サーバー側でも強制されます。
                </span>
              </label>

              <div className="mt-4 rounded-md border border-border p-3">
                <label className="flex cursor-pointer items-start gap-3">
                  <input
                    type="checkbox"
                    checked={sessionDraft.temporary}
                    disabled={saving || Boolean(sessionSettings?.temporary_locked)}
                    onChange={(event) => onSessionDraftChange({ ...sessionDraft, temporary: event.target.checked })}
                    className="mt-1 h-4 w-4"
                  />
                  <span>
                    <span className="block text-sm font-medium">このセッションを一時チャットにする</span>
                    <span className="mt-1 block text-xs leading-5 text-muted-foreground">
                      既存memoryと過去チャットを使わず、この会話をsummary・journal・memory・検索indexの整理対象から除外します。live会話ログ自体は保持します。
                    </span>
                  </span>
                </label>
                {sessionSettings?.temporary_locked && (
                  <div className="mt-2 text-xs text-muted-foreground">
                    最初の発言保存後は、通常／一時の区分を変更できません。
                  </div>
                )}
              </div>

              <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4">
                <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
                  <Badge variant={sessionRetrievalEnabled ? "secondary" : "outline"}>保存後の記憶参照: {sessionRetrievalEnabled ? "ON" : "OFF"}</Badge>
                  {sessionDraft.temporary && <Badge variant="outline">整理対象外</Badge>}
                </div>
                <Button type="button" onClick={onSaveSession} disabled={saving || loading}>
                  {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}
                  セッション設定を保存
                </Button>
              </div>
            </>
          )}
        </section>

        <section className="rounded-md border border-border p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <div className="flex items-center gap-2 text-sm font-semibold">
                <FileText className="h-4 w-4" />
                Memory summary
              </div>
              <div className="mt-1 text-xs text-muted-foreground">固定されたmemory配下のファイルだけを読み取り専用で表示します。</div>
            </div>
            <Badge variant="secondary">読み取り専用</Badge>
          </div>

          {!summary ? (
            <div className="mt-4 rounded-md border border-dashed border-border px-3 py-6 text-center text-sm text-muted-foreground">
              プレビューを取得できませんでした。
            </div>
          ) : (
            <>
              <div className="mt-4 grid gap-3 lg:grid-cols-3">
                {summary.sections.map((item) => <MemoryPreviewCard key={item.key} item={item} />)}
              </div>
              <div className="mt-5 flex items-center justify-between gap-2">
                <div className="text-sm font-medium">構造化メモリ</div>
                <Badge variant="outline">{summary.structured_item_count}件</Badge>
              </div>
              {summary.structured_items.length === 0 ? (
                <div className="mt-3 rounded-md border border-dashed border-border px-3 py-5 text-sm text-muted-foreground">memory/items/*.md はありません。</div>
              ) : (
                <div className="mt-3 grid gap-3 lg:grid-cols-2">
                  {summary.structured_items.map((item) => <MemoryPreviewCard key={item.path} item={item} />)}
                </div>
              )}
              {summary.structured_items_truncated && <div className="mt-2 text-xs text-muted-foreground">先頭100件だけ表示しています。</div>}
            </>
          )}
        </section>
      </div>
    </div>
  )
}

function PersonalizationToggle({
  checked,
  label,
  description,
  disabled,
  onChange,
}: {
  checked: boolean
  label: string
  description: string
  disabled: boolean
  onChange: (checked: boolean) => void
}) {
  return (
    <label className={cn("flex cursor-pointer items-start gap-3 rounded-md border border-border p-3", disabled && "cursor-not-allowed opacity-70")}>
      <input type="checkbox" checked={checked} disabled={disabled} onChange={(event) => onChange(event.target.checked)} className="mt-1 h-4 w-4" />
      <span>
        <span className="block text-sm font-medium">{label}</span>
        <span className="mt-1 block text-xs leading-5 text-muted-foreground">{description}</span>
      </span>
    </label>
  )
}

function MemoryPreviewCard({ item }: { item: MemorySummary["sections"][number] }) {
  return (
    <details className="min-w-0 rounded-md border border-border bg-muted/30 p-3">
      <summary className="cursor-pointer list-none">
        <div className="flex items-center justify-between gap-2">
          <span className="truncate text-sm font-medium">{item.label}</span>
          <Badge variant={item.exists ? "secondary" : "outline"}>{item.exists ? `${item.character_count}字` : "未作成"}</Badge>
        </div>
        <div className="mt-1 truncate font-mono text-[11px] text-muted-foreground">{item.path}</div>
      </summary>
      {item.exists && (
        <div className="mt-3">
          <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-md bg-background p-3 text-xs leading-5 text-foreground">{item.content}</pre>
          <div className="mt-2 flex flex-wrap gap-2 text-[11px] text-muted-foreground">
            {item.modified_at && <span>更新: {formatDateTime(item.modified_at)}</span>}
            {item.truncated && <span>表示上限で省略</span>}
          </div>
        </div>
      )}
    </details>
  )
}

function DataManagementScreen({
  report,
  loading,
  onRefresh,
  onOpenFolder,
}: {
  report: LocalDataReport | null
  loading: boolean
  onRefresh: () => void
  onOpenFolder: (folder: string) => void
}) {
  const directories = report ? Object.entries(report.directories) : []
  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5">
      <div className="mx-auto flex max-w-5xl flex-col gap-5">
        <section className="flex flex-wrap items-center justify-between gap-3 border-b border-border pb-4">
          <div className="min-w-0">
            <div className="text-sm font-semibold">保存状況</div>
            <div className="mt-1 truncate text-xs text-muted-foreground">{report?.root ?? "未取得"}</div>
          </div>
          <Button type="button" variant="outline" onClick={onRefresh} disabled={loading}>
            <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
            更新
          </Button>
        </section>

        {report && (
          <section className="grid gap-3 sm:grid-cols-3">
            <DataMetric icon={<HardDrive className="h-4 w-4" />} label="フォルダ" value={`${report.totals.existing_directories}`} />
            <DataMetric icon={<FileText className="h-4 w-4" />} label="ファイル" value={`${report.totals.file_count}`} />
            <DataMetric icon={<Database className="h-4 w-4" />} label="合計サイズ" value={formatBytes(report.totals.total_bytes)} />
          </section>
        )}

        <section className="overflow-hidden rounded-md border border-border">
          <div className="grid grid-cols-[minmax(0,1.2fr)_100px_120px_minmax(0,1fr)_80px] gap-3 border-b border-border bg-muted px-3 py-2 text-xs font-medium text-muted-foreground">
            <div>領域</div>
            <div>件数</div>
            <div>サイズ</div>
            <div>最新</div>
            <div></div>
          </div>
          {loading && !report ? (
            <div className="flex items-center gap-2 px-3 py-6 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              読み込み中
            </div>
          ) : directories.length === 0 ? (
            <div className="px-3 py-6 text-sm text-muted-foreground">データなし</div>
          ) : (
            directories.map(([name, item]) => (
              <div key={name} className="grid grid-cols-[minmax(0,1.2fr)_100px_120px_minmax(0,1fr)_80px] gap-3 border-b border-border px-3 py-2 text-sm last:border-b-0">
                <div className="min-w-0">
                  <div className="truncate font-medium">{name}</div>
                  <div className="truncate text-xs text-muted-foreground">{item.path}</div>
                </div>
                <div className="text-muted-foreground">{item.exists ? item.file_count : "-"}</div>
                <div className="text-muted-foreground">{item.exists ? formatBytes(item.total_bytes) : "-"}</div>
                <div className="min-w-0 truncate text-xs text-muted-foreground">{item.newest_file ?? (item.exists ? "空" : "未作成")}</div>
                <Button type="button" size="icon" variant="ghost" onClick={() => onOpenFolder(name)} disabled={!item.exists} title="フォルダを開く">
                  <FolderOpen className="h-4 w-4" />
                </Button>
              </div>
            ))
          )}
        </section>

        {report && (
          <section className="rounded-md border border-border px-3 py-3 text-sm">
            <div className="mb-2 flex items-center gap-2 font-medium">
              <Database className="h-4 w-4" />
              search_index.sqlite3
            </div>
            <div className="text-xs text-muted-foreground">
              {report.search_index.exists
                ? `${report.search_index.path} / ${formatBytes(report.search_index.size_bytes)} / ${report.search_index.modified_at ?? "-"}`
                : "未作成"}
            </div>
          </section>
        )}

        <section className="rounded-md border border-border px-3 py-3 text-sm">
          <div className="mb-2 flex items-center gap-2 font-medium">
            <AlertTriangle className="h-4 w-4" />
            privacy check
          </div>
          <pre className="overflow-auto whitespace-pre-wrap rounded-md bg-muted p-3 text-xs">{`python scripts\\privacy_check.py --staged
python scripts\\privacy_check.py --range origin/main..HEAD
python scripts\\privacy_check.py --publish`}</pre>
        </section>
      </div>
    </div>
  )
}

function DataMetric({ icon, label, value }: { icon: ReactNode; label: string; value: string }) {
  return (
    <div className="rounded-md border border-border px-3 py-3">
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        {icon}
        {label}
      </div>
      <div className="mt-2 text-lg font-semibold">{value}</div>
    </div>
  )
}

function MemoryContextDetails({
  context,
  candidates = [],
  opened = [],
  compact = false,
  className,
}: {
  context: MemoryContextSummary
  candidates?: MemoryContextReference[]
  opened?: MemoryContextReference[]
  compact?: boolean
  className?: string
}) {
  const label = context.used ? `記憶取得: あり (${context.reference_count}件)` : "記憶取得: なし"
  const scoreLabel = context.threshold > 0
    ? `depth score ${context.score}（deep基準 ${context.threshold}）`
    : "depth score -"
  const modeLabel = context.retrieval_modes.length > 0 ? context.retrieval_modes.join(" / ") : "なし"
  const health = context.retrieval_health

  return (
    <details
      className={cn(
        "rounded-md border border-border bg-muted/50 px-3 py-2 text-xs text-muted-foreground",
        compact && "bg-background/60",
        className,
      )}
    >
      <summary className="flex cursor-pointer list-none flex-wrap items-center gap-2">
        <Brain className={cn("h-3.5 w-3.5", context.used ? "text-primary" : "text-muted-foreground")} />
        <span className={cn("font-medium", context.used ? "text-foreground" : "text-muted-foreground")}>{label}</span>
        <span>{scoreLabel}</span>
        <span>取得: {modeLabel}</span>
      </summary>
      <div className="mt-2 space-y-2">
        <div className="grid gap-2 rounded-md border border-border/70 bg-background/60 p-2 sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <div className="font-medium text-foreground">Index</div>
            <div>{health.index_status}{health.markdown_fallback_used ? " / Markdown fallback" : ""}</div>
          </div>
          <div>
            <div className="font-medium text-foreground">検索深度</div>
            <div>{health.retrieval_depth}</div>
          </div>
          <div>
            <div className="font-medium text-foreground">Hit count</div>
            <div>core {health.core_reference_count} / structured {health.structured_memory_hit_count} / past {health.past_chat_hit_count}</div>
          </div>
          <div>
            <div className="font-medium text-foreground">Scope</div>
            <div className="break-words">{health.project_scope ?? "全体"}</div>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <span>core: {health.core_enabled ? "ON" : "OFF"}</span>
          <span>past chats: {health.past_chats_enabled ? "ON" : "OFF"}</span>
          {health.index_reasons.length > 0 && <span>index理由: {health.index_reasons.join(" / ")}</span>}
        </div>
        {health.query_variants.length > 0 && (
          <div className="break-words">検索語: {health.query_variants.join(" / ")}</div>
        )}
        <div className="flex flex-wrap gap-2">
          <span className="font-medium text-foreground">取得理由:</span>
          <span>{context.reasons.length > 0 ? context.reasons.join(" / ") : "理由なし"}</span>
        </div>
        <div className="font-medium text-foreground">回答へ渡した記憶参照</div>
        {context.used
          ? <MemoryReferenceRows references={context.references} />
          : <div>今回の回答へ渡した記憶参照はありません。</div>}
        {candidates.length > 0 && (
          <div className="space-y-2 border-t border-border/70 pt-2">
            <div className="font-medium text-foreground">MCP検索候補（未open・回答未使用を含む）</div>
            <MemoryReferenceRows references={candidates} />
          </div>
        )}
        {opened.length > 0 && (
          <div className="space-y-2 border-t border-border/70 pt-2">
            <div className="font-medium text-foreground">MCPでopenした一次資料</div>
            <MemoryReferenceRows references={opened} />
          </div>
        )}
      </div>
    </details>
  )
}

function MemoryReferenceRows({ references }: { references: MemoryContextReference[] }) {
  const visible = references.slice(0, 5)
  return (
    <>
      {visible.map((reference) => (
        <div key={`${reference.path}:${reference.document_type}:${reference.speaker_role ?? ""}:${reference.message_number ?? ""}`} className="min-w-0">
          <div className="break-all font-mono text-[11px] text-foreground">{reference.path}</div>
          <div className="mt-1 flex flex-wrap gap-2">
            <span>{reference.document_type}</span>
            {reference.speaker_role && <span>role: {reference.speaker_role}</span>}
            {reference.message_number !== null && <span>message {reference.message_number}</span>}
            {reference.date && <span>{reference.date}</span>}
            {reference.score > 0 && <span>match {reference.score}</span>}
          </div>
          {reference.snippet && <div className="mt-1 line-clamp-2 break-words">{reference.snippet}</div>}
        </div>
      ))}
      {references.length > visible.length && <div>他{references.length - visible.length}件</div>}
    </>
  )
}

function MarkdownContent({ content }: { content: string }) {
  const blocks = splitMarkdownBlocks(content)
  return (
    <div className="space-y-3 break-words">
      {blocks.map((block, index) =>
        block.type === "code" ? (
          <CodeBlock key={index} language={block.language} code={block.content} />
        ) : (
          <TextBlock key={index} content={block.content} />
        ),
      )}
    </div>
  )
}

function CodeBlock({ language, code }: { language: string; code: string }) {
  return (
    <div className="overflow-hidden rounded-md border border-border bg-muted">
      <div className="flex h-9 items-center justify-between border-b border-border px-3 text-xs text-muted-foreground">
        <span className="font-medium">{language || "code"}</span>
        <CopyButton text={code} label="コードをコピー" />
      </div>
      <pre className="max-h-96 overflow-auto p-3 text-[13px] leading-5">
        <code className="whitespace-pre font-mono">{code}</code>
      </pre>
    </div>
  )
}

function TextBlock({ content }: { content: string }) {
  const lines = content.split(/\r?\n/)
  const elements: ReactNode[] = []
  let listItems: ReactNode[] = []
  let orderedItems: ReactNode[] = []

  function flushLists(key: string) {
    if (listItems.length > 0) {
      elements.push(
        <ul key={`${key}-ul`} className="my-2 list-disc space-y-1 pl-5">
          {listItems}
        </ul>,
      )
      listItems = []
    }
    if (orderedItems.length > 0) {
      elements.push(
        <ol key={`${key}-ol`} className="my-2 list-decimal space-y-1 pl-5">
          {orderedItems}
        </ol>,
      )
      orderedItems = []
    }
  }

  lines.forEach((line, index) => {
    const trimmed = line.trim()
    if (!trimmed) {
      flushLists(`blank-${index}`)
      return
    }

    const heading = /^(#{1,4})\s+(.+)$/.exec(trimmed)
    if (heading) {
      flushLists(`heading-${index}`)
      const level = heading[1].length
      const className = cn("font-semibold leading-tight", level === 1 ? "text-lg" : level === 2 ? "text-base" : "text-sm")
      elements.push(
        <div key={index} className={className}>
          {renderInline(heading[2])}
        </div>,
      )
      return
    }

    const unordered = /^[-*]\s+(.+)$/.exec(trimmed)
    if (unordered) {
      if (orderedItems.length > 0) {
        flushLists(`list-switch-${index}`)
      }
      listItems.push(<li key={index}>{renderInline(unordered[1])}</li>)
      return
    }

    const ordered = /^\d+[.)]\s+(.+)$/.exec(trimmed)
    if (ordered) {
      if (listItems.length > 0) {
        flushLists(`list-switch-${index}`)
      }
      orderedItems.push(<li key={index}>{renderInline(ordered[1])}</li>)
      return
    }

    const quote = /^>\s?(.+)$/.exec(trimmed)
    if (quote) {
      flushLists(`quote-${index}`)
      elements.push(
        <blockquote key={index} className="border-l-2 border-primary/50 pl-3 text-muted-foreground">
          {renderInline(quote[1])}
        </blockquote>,
      )
      return
    }

    flushLists(`p-${index}`)
    elements.push(
      <p key={index} className="whitespace-pre-wrap">
        {renderInline(line)}
      </p>,
    )
  })
  flushLists("end")

  return <div className="space-y-2">{elements}</div>
}

function CopyButton({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false)

  async function handleCopy() {
    const ok = await copyText(text)
    if (!ok) {
      return
    }
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1200)
  }

  return (
    <Button type="button" size="sm" variant="ghost" className="h-7 px-2 text-xs" onClick={handleCopy} title={label}>
      {copied ? <ClipboardCheck className="h-3.5 w-3.5" /> : <Clipboard className="h-3.5 w-3.5" />}
      {copied ? "コピー済み" : "コピー"}
    </Button>
  )
}

function OrganizeStages({ organization, running }: { organization: SessionOrganization; running: boolean }) {
  const stages = [organization.stages.raw, organization.stages.memory, organization.stages.index]
  return (
    <div className="flex flex-wrap items-center gap-1">
      {stages.map((stage) => {
        const isRunning = running && organization.next_stage === stage.name && stage.status !== "done"
        return (
          <Badge key={stage.name} variant="outline" className={cn("gap-1", stageClassName(stage.status, isRunning))}>
            {isRunning && <Loader2 className="h-3 w-3 animate-spin" />}
            {stage.label}:{isRunning ? "実行中" : stageStatusLabel(stage.status)}
          </Badge>
        )
      })}
    </div>
  )
}

function getFinalizeButtonLabel(organization: SessionOrganization | null) {
  if (!organization) {
    return "整理して保存"
  }
  if (organization.is_organized) {
    return "整理済み"
  }
  if (organization.next_stage === "memory") {
    return "記憶整理から再開"
  }
  if (organization.next_stage === "index") {
    return "index更新から再開"
  }
  return "整理して保存"
}

function stageStatusLabel(status: string) {
  if (status === "done") {
    return "完了"
  }
  if (status === "failed") {
    return "失敗"
  }
  return "待機"
}

function stageClassName(status: string, running: boolean) {
  if (running) {
    return "border-primary text-primary"
  }
  if (status === "done") {
    return "border-emerald-600/40 text-emerald-700"
  }
  if (status === "failed") {
    return "border-destructive/40 text-destructive"
  }
  return "text-muted-foreground"
}

function splitMarkdownBlocks(content: string) {
  const blocks: Array<{ type: "text"; content: string } | { type: "code"; language: string; content: string }> = []
  const fence = /```([^\r\n`]*)\r?\n?([\s\S]*?)```/g
  let cursor = 0
  let match: RegExpExecArray | null

  while ((match = fence.exec(content)) !== null) {
    if (match.index > cursor) {
      blocks.push({ type: "text", content: content.slice(cursor, match.index).trim() })
    }
    blocks.push({ type: "code", language: match[1].trim(), content: trimCodeBlock(match[2]) })
    cursor = match.index + match[0].length
  }

  if (cursor < content.length) {
    blocks.push({ type: "text", content: content.slice(cursor).trim() })
  }

  return blocks.filter((block) => block.content.length > 0)
}

function renderInline(text: string) {
  const nodes: ReactNode[] = []
  const pattern = /(`[^`]+`|\*\*[^*]+\*\*)/g
  let cursor = 0
  let match: RegExpExecArray | null

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > cursor) {
      nodes.push(text.slice(cursor, match.index))
    }

    const inlineMarker = match[0]
    if (inlineMarker.startsWith("`")) {
      nodes.push(
        <code key={`${match.index}-code`} className="rounded bg-muted px-1 py-0.5 font-mono text-[0.92em]">
          {inlineMarker.slice(1, -1)}
        </code>,
      )
    } else {
      nodes.push(
        <strong key={`${match.index}-strong`} className="font-semibold">
          {inlineMarker.slice(2, -2)}
        </strong>,
      )
    }
    cursor = match.index + inlineMarker.length
  }

  if (cursor < text.length) {
    nodes.push(text.slice(cursor))
  }

  return nodes
}

function attachMemoryContextToLatestAssistant(
  messages: ChatMessage[],
  context: MemoryContextSummary | null,
  candidates: MemoryContextReference[] = [],
  opened: MemoryContextReference[] = [],
) {
  if (!context && candidates.length === 0 && opened.length === 0) {
    return messages
  }

  const next = messages.map((message) => ({ ...message }))
  for (let index = next.length - 1; index >= 0; index -= 1) {
    if (next[index].role === "assistant") {
      next[index].memory_context = context ?? undefined
      next[index].memory_candidates = candidates
      next[index].memory_opened = opened
      break
    }
  }
  return next
}

function trimCodeBlock(value: string) {
  return value.replace(/^\r?\n/, "").replace(/\r?\n$/, "")
}

function formatError(error: unknown) {
  if (error instanceof Error) {
    return error.message
  }
  return String(error)
}

function withNextAction(message: string) {
  return `${message}\n次の操作: 入力に戻す、セッション一覧更新、または新規チャットを選べます。`
}

function formatAttachmentResultNotice(results: Array<{ file_name: string; status: string; error: string | null }>) {
  if (!results || results.length === 0) {
    return ""
  }
  const failed = results.filter((item) => item.status !== "extracted")
  if (failed.length === 0) {
    return `添付 ${results.length}件を回答コンテキストに使いました。`
  }
  const names = failed.map((item) => `${item.file_name}: ${item.error ?? "抽出失敗"}`).join(" / ")
  return `使えなかった添付 ${failed.length}件: ${names}`
}

function compactNotice(primary: string, secondary: string) {
  return secondary ? `${primary} ${secondary}` : primary
}

async function fileToAttachmentDraft(file: File): Promise<AttachmentDraft> {
  const extension = fileExtension(file.name)
  const base = {
    id: createRequestId(),
    name: safeDisplayFileName(file.name),
    extension,
    size_bytes: file.size,
    extracted_chars: 0,
    truncated: false,
  }

  if (!SUPPORTED_ATTACHMENT_EXTENSIONS.has(extension)) {
    return { ...base, status: "error", error: "未対応形式です。" }
  }
  if (file.size > MAX_ATTACHMENT_BYTES) {
    return { ...base, status: "error", error: `上限 ${formatBytes(MAX_ATTACHMENT_BYTES)} を超えています。` }
  }

  try {
    if (extension === "txt" || extension === "md") {
      const rawText = await file.text()
      const truncated = rawText.length > MAX_ATTACHMENT_TEXT_CHARS
      const text = truncated ? rawText.slice(0, MAX_ATTACHMENT_TEXT_CHARS) : rawText
      return {
        ...base,
        status: text.trim().length > 0 ? "ready" : "error",
        error: text.trim().length > 0 ? null : "本文が空です。",
        text,
        extracted_chars: text.length,
        truncated,
      }
    }

    const dataBase64 = await fileToBase64(file)
    return {
      ...base,
      status: "ready",
      error: null,
      data_base64: dataBase64,
      extracted_chars: 0,
      truncated: false,
    }
  } catch (err) {
    return { ...base, status: "error", error: formatError(err) }
  }
}

function attachmentToPayload(attachment: AttachmentDraft): AttachmentPayload {
  return {
    name: attachment.name,
    extension: attachment.extension,
    size_bytes: attachment.size_bytes,
    text: attachment.text,
    data_base64: attachment.data_base64,
    truncated: attachment.truncated,
  }
}

function buildPendingUserContent(content: string, attachmentDrafts: AttachmentDraft[]) {
  if (attachmentDrafts.length === 0) {
    return content
  }
  const lines = ["", "", "[Attachments]"]
  for (const attachment of attachmentDrafts) {
    lines.push(
      `- name=${attachment.name}; type=${attachment.extension || "unknown"}; status=${attachment.status}; chars=${attachment.extracted_chars}; truncated=${attachment.truncated ? "yes" : "no"}`,
    )
  }
  return content + lines.join("\n")
}

function fileExtension(name: string) {
  const index = name.lastIndexOf(".")
  return index >= 0 ? name.slice(index + 1).toLowerCase() : ""
}

function safeDisplayFileName(value: string) {
  const name = value.split(/[\\/]/).pop()?.replace(/[\x00-\x1f<>:"/\\|?*]+/g, "_").trim() || "attachment"
  return name.length > 120 ? `${name.slice(0, 117)}...` : name
}

async function fileToBase64(file: File) {
  const buffer = await file.arrayBuffer()
  let binary = ""
  const bytes = new Uint8Array(buffer)
  for (const byte of bytes) {
    binary += String.fromCharCode(byte)
  }
  return btoa(binary)
}

function formatBytes(value: number) {
  if (!Number.isFinite(value) || value <= 0) {
    return "0 B"
  }
  const units = ["B", "KB", "MB", "GB"]
  let size = value
  let unitIndex = 0
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024
    unitIndex += 1
  }
  return `${size >= 10 || unitIndex === 0 ? size.toFixed(0) : size.toFixed(1)} ${units[unitIndex]}`
}

function formatDateTime(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return value
  }
  return new Intl.DateTimeFormat("ja-JP", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date)
}

function formatImportDate(value: string | null) {
  if (!value) {
    return "不明"
  }
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return value
  }
  return new Intl.DateTimeFormat("ja-JP", {
    timeZone: "UTC",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(date)
}

function chatGptImportStateLabel(state: ChatGptImportConversation["import_state"]) {
  switch (state) {
    case "new":
      return "新規"
    case "updated":
      return "更新あり"
    case "duplicate":
      return "変更なし"
    case "conflict":
      return "競合・要確認"
  }
}

function createRequestId() {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID()
  }
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`
}

function trackFinalizeJob(jobId: string) {
  try {
    window.localStorage.setItem(FINALIZE_JOB_STORAGE_KEY, jobId)
  } catch {
    // The status file remains authoritative when local storage is unavailable.
  }
}

function readTrackedFinalizeJobId() {
  try {
    return window.localStorage.getItem(FINALIZE_JOB_STORAGE_KEY)
  } catch {
    return null
  }
}

function clearTrackedFinalizeJob(expectedJobId?: string) {
  try {
    if (expectedJobId && window.localStorage.getItem(FINALIZE_JOB_STORAGE_KEY) !== expectedJobId) {
      return
    }
    window.localStorage.removeItem(FINALIZE_JOB_STORAGE_KEY)
  } catch {
    // Ignore storage cleanup failures; backend status recovery still applies.
  }
}

function trackOrganizeSessionsJob(jobId: string) {
  try {
    window.localStorage.setItem(ORGANIZE_SESSIONS_JOB_STORAGE_KEY, jobId)
  } catch {
    // The status file remains authoritative when local storage is unavailable.
  }
}

function readTrackedOrganizeSessionsJobId() {
  try {
    return window.localStorage.getItem(ORGANIZE_SESSIONS_JOB_STORAGE_KEY)
  } catch {
    return null
  }
}

function clearTrackedOrganizeSessionsJob(expectedJobId?: string) {
  try {
    if (expectedJobId && window.localStorage.getItem(ORGANIZE_SESSIONS_JOB_STORAGE_KEY) !== expectedJobId) {
      return
    }
    window.localStorage.removeItem(ORGANIZE_SESSIONS_JOB_STORAGE_KEY)
  } catch {
    // Ignore storage cleanup failures; backend status recovery still applies.
  }
}

function finalizeSessionId(sessionFile: string) {
  const name = sessionFile.split(/[\\/]/).pop() ?? sessionFile
  return name.replace(/\.jsonl$/i, "")
}

async function copyText(text: string) {
  try {
    if (navigator.clipboard) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
    // Fall through to the textarea fallback.
  }

  try {
    const textarea = document.createElement("textarea")
    textarea.value = text
    textarea.setAttribute("readonly", "")
    textarea.style.position = "fixed"
    textarea.style.left = "-9999px"
    document.body.appendChild(textarea)
    textarea.select()
    const ok = document.execCommand("copy")
    document.body.removeChild(textarea)
    return ok
  } catch {
    return false
  }
}

export default App
