import { useEffect, useMemo, useRef, useState } from "react"
import type { ReactNode } from "react"
import {
  AlertTriangle,
  Archive,
  Bot,
  Brain,
  CheckCircle2,
  Clipboard,
  ClipboardCheck,
  Database,
  FileText,
  FolderOpen,
  HardDrive,
  Loader2,
  MessageSquarePlus,
  Paperclip,
  RefreshCw,
  RotateCcw,
  Send,
  Square,
  User,
  X,
} from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { cn } from "@/lib/utils"
import {
  cancelMessage,
  cancelFinalizeJob,
  getFinalizeJob,
  getLocalDataReport,
  isTauriRuntime,
  listResumableSessions,
  openLocalDataFolder,
  resumeSession,
  sendMessage,
  startFinalizeJob,
  startSession,
} from "@/tauri"
import type {
  AttachmentPayload,
  ChatMessage,
  FinalizeJob,
  LocalDataReport,
  MemoryContextSummary,
  ResumeSession,
  SessionFile,
  SessionOrganization,
} from "@/types"

type BusyState = "idle" | "starting" | "generating" | "stopping" | "resuming" | "finalizing" | "refreshing"
type ReplyState = "idle" | "generating" | "stopping" | "stopped" | "failed" | "completed"
type ViewMode = "chat" | "data"
type PendingMessageStatus = "sending" | "failed"

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

const busyLabel: Record<BusyState, string> = {
  idle: "",
  starting: "起動中",
  generating: "生成中",
  stopping: "停止中",
  resuming: "再開中",
  finalizing: "整理中",
  refreshing: "更新中",
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
const SUPPORTED_ATTACHMENT_EXTENSIONS = new Set(["txt", "md", "pdf"])

function App() {
  const [session, setSession] = useState<SessionFile | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [sessions, setSessions] = useState<ResumeSession[]>([])
  const [viewMode, setViewMode] = useState<ViewMode>("chat")
  const [input, setInput] = useState("")
  const [busy, setBusy] = useState<BusyState>("idle")
  const [replyState, setReplyState] = useState<ReplyState>("idle")
  const [notice, setNotice] = useState("AI-LifeOS Chat")
  const [error, setError] = useState<string | null>(null)
  const [lastMemoryContext, setLastMemoryContext] = useState<MemoryContextSummary | null>(null)
  const [lastSubmittedText, setLastSubmittedText] = useState("")
  const [pendingUserMessage, setPendingUserMessage] = useState<PendingUserMessage | null>(null)
  const [attachments, setAttachments] = useState<AttachmentDraft[]>([])
  const [activeRequestId, setActiveRequestId] = useState<string | null>(null)
  const [finalizeJob, setFinalizeJob] = useState<FinalizeJob | null>(null)
  const [localDataReport, setLocalDataReport] = useState<LocalDataReport | null>(null)
  const [localDataLoading, setLocalDataLoading] = useState(false)
  const viewportRef = useRef<HTMLDivElement | null>(null)
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const initializedRef = useRef(false)
  const activeRequestIdRef = useRef<string | null>(null)

  const isBusy = busy !== "idle"
  const isFinalizeActive = finalizeJob?.status === "queued" || finalizeJob?.status === "running"
  const isGenerating = busy === "generating" || busy === "stopping"
  const hasAttachmentError = attachments.some((attachment) => attachment.status === "error")
  const canSend = input.trim().length > 0 && !isBusy && !isFinalizeActive && !hasAttachmentError
  const canStop = busy === "generating" && activeRequestId !== null
  const canRestoreInput = lastSubmittedText.trim().length > 0 && !isBusy
  const organization = session?.organization ?? null
  const canFinalize = Boolean(session && organization?.can_organize && !isBusy && !isFinalizeActive)
  const finalizeButtonLabel = getFinalizeButtonLabel(organization)
  const statusLabel = organization?.label ?? "未開始"
  const displayMessages = pendingUserMessage ? [...messages, pendingUserMessage] : messages

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

  useEffect(() => {
    const node = viewportRef.current
    if (node) {
      node.scrollTop = node.scrollHeight
    }
  }, [displayMessages, isGenerating])

  useEffect(() => {
    if (viewMode === "data" && !localDataReport && !localDataLoading && isTauriRuntime()) {
      void refreshLocalDataReport()
    }
  }, [viewMode, localDataReport, localDataLoading])

  useEffect(() => {
    if (!finalizeJob || (finalizeJob.status !== "queued" && finalizeJob.status !== "running")) {
      return
    }

    const timer = window.setInterval(() => {
      void pollFinalizeJob(finalizeJob.job_id)
    }, 1200)
    return () => window.clearInterval(timer)
  }, [finalizeJob?.job_id, finalizeJob?.status])

  async function initialize() {
    setBusy("starting")
    setReplyState("idle")
    setError(null)
    try {
      const [sessionResult, listResult] = await Promise.all([startSession(), listResumableSessions()])
      setSession(sessionResult.session)
      setMessages(sessionResult.messages)
      setSessions(listResult.sessions)
      setPendingUserMessage(null)
      setAttachments([])
      setLastMemoryContext(null)
      setNotice("新規セッションを開始しました。")
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
    if (isBusy) {
      return
    }

    setBusy("starting")
    setReplyState("idle")
    setError(null)
    try {
      const result = await startSession()
      setSession(result.session)
      setMessages(result.messages)
      setLastMemoryContext(null)
      setInput("")
      setPendingUserMessage(null)
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
    if (!content || isBusy || isFinalizeActive || hasAttachmentError) {
      return
    }

    const requestId = createRequestId()
    const attachmentPayloads = attachments.filter((attachment) => attachment.status === "ready").map(attachmentToPayload)
    activeRequestIdRef.current = requestId
    setActiveRequestId(requestId)
    setBusy("generating")
    setReplyState("generating")
    setError(null)
    setLastMemoryContext(null)
    setInput("")
    setLastSubmittedText(content)
    setPendingUserMessage({
      role: "user",
      content: buildPendingUserContent(content, attachments),
      timestamp: new Date().toISOString(),
      pending_status: "sending",
    })
    setNotice("返答を生成しています。停止ボタンで中断できます。")

    try {
      const result = await sendMessage(session?.jsonl_file ?? null, content, requestId, attachmentPayloads)
      if (activeRequestIdRef.current !== requestId) {
        return
      }

      setSession(result.session)
      const memoryContext = result.assistant ? result.memory_context : null
      setMessages(attachMemoryContextToLatestAssistant(result.messages, memoryContext))
      setPendingUserMessage(null)
      setAttachments([])
      setLastMemoryContext(memoryContext)
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
    if (isBusy) {
      return
    }

    setBusy("resuming")
    setReplyState("idle")
    setError(null)
    try {
      const result = await resumeSession(sessionId)
      setSession(result.session)
      setMessages(result.messages)
      setLastMemoryContext(null)
      setPendingUserMessage(null)
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
    if (!session || !organization?.can_organize || isBusy || isFinalizeActive) {
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
        setSession(result.job.result.session)
        setNotice(`整理済み: ${result.job.result.raw_file}`)
        await refreshSessionsAfterAction()
      } else if (result.job.status === "failed") {
        setReplyState("failed")
        setError(`整理処理に失敗しました: ${result.job.error ?? result.job.message ?? "不明なエラー"}`)
      } else if (result.job.status === "cancelled") {
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
          <Button className="w-full justify-start" variant="secondary" onClick={createSession} disabled={isBusy}>
            <MessageSquarePlus className="h-4 w-4" />
            新規チャット
          </Button>
          <Button
            className="w-full justify-start"
            variant={viewMode === "data" ? "secondary" : "ghost"}
            onClick={() => setViewMode((current) => (current === "data" ? "chat" : "data"))}
          >
            <Database className="h-4 w-4" />
            ローカルデータ
          </Button>
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
                  disabled={isBusy}
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
            <div className="truncate text-sm font-semibold">{viewMode === "data" ? "ローカルデータ管理" : sessionTitle}</div>
            <div className="truncate text-xs text-muted-foreground">
              {viewMode === "data" ? (localDataReport?.root ?? "AI-LifeOS root") : (session?.jsonl_file ?? "inbox/live")}
            </div>
          </div>
          <div className="flex items-center gap-2">
            {viewMode === "data" ? (
              <>
                <Badge variant="secondary">読み取り専用</Badge>
                <Button variant="outline" onClick={() => void refreshLocalDataReport()} disabled={localDataLoading}>
                  <RefreshCw className={cn("h-4 w-4", localDataLoading && "animate-spin")} />
                  更新
                </Button>
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
                <Badge variant={organization?.is_organized ? "secondary" : "outline"}>{statusLabel}</Badge>
                <Button variant="outline" onClick={finalizeCurrentSession} disabled={!canFinalize}>
                  <Archive className="h-4 w-4" />
                  {finalizeButtonLabel}
                </Button>
              </>
            )}
          </div>
        </header>

        {viewMode === "data" ? (
          <DataManagementScreen report={localDataReport} loading={localDataLoading} onRefresh={refreshLocalDataReport} onOpenFolder={openDataFolder} />
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
          {finalizeJob && <FinalizeJobPanel job={finalizeJob} onCancel={cancelCurrentFinalizeJob} />}
          {lastMemoryContext && <MemoryContextDetails context={lastMemoryContext} className="mt-2" />}
        </div>

        <div ref={viewportRef} className="min-h-0 flex-1 overflow-y-auto px-5 py-5">
          <div className="mx-auto flex max-w-4xl flex-col gap-4">
            {displayMessages.length === 0 ? (
              <div className="flex min-h-80 items-center justify-center rounded-md border border-dashed border-border text-sm text-muted-foreground">
                メッセージなし
              </div>
            ) : (
              displayMessages.map((message, index) => <MessageBubble key={`${message.timestamp}-${index}`} message={message} />)
            )}
            {isGenerating && <GeneratingRow stopping={busy === "stopping"} />}
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
            accept=".txt,.md,.pdf,text/plain,text/markdown,application/pdf"
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

function MessageBubble({ message }: { message: ChatMessage | PendingUserMessage }) {
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
            <div className="mb-2 flex justify-end">
              <CopyButton text={message.content} label="返答をコピー" />
            </div>
          )}
          {isUser ? <div className="whitespace-pre-wrap break-words">{message.content}</div> : <MarkdownContent content={message.content} />}
          {!isUser && message.memory_context && <MemoryContextDetails context={message.memory_context} compact className="mt-3" />}
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
  compact = false,
  className,
}: {
  context: MemoryContextSummary
  compact?: boolean
  className?: string
}) {
  const label = context.used ? `記憶参照: あり (${context.reference_count}件)` : "記憶参照: なし"
  const scoreLabel = context.threshold > 0 ? `score ${context.score}/${context.threshold}` : "score -"
  const references = context.references.slice(0, 5)

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
      </summary>
      <div className="mt-2 space-y-2">
        {context.used ? (
          references.map((reference) => (
            <div key={reference.path} className="min-w-0">
              <div className="break-all font-mono text-[11px] text-foreground">{reference.path}</div>
              <div className="mt-1 flex flex-wrap gap-2">
                <span>{reference.document_type}</span>
                {reference.date && <span>{reference.date}</span>}
                {reference.score > 0 && <span>match {reference.score}</span>}
              </div>
              {reference.snippet && <div className="mt-1 line-clamp-2 break-words">{reference.snippet}</div>}
            </div>
          ))
        ) : (
          <div>今回の回答では memory context を使っていません。</div>
        )}
        {context.references.length > references.length && <div>他{context.references.length - references.length}件</div>}
      </div>
    </details>
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

function attachMemoryContextToLatestAssistant(messages: ChatMessage[], context: MemoryContextSummary | null) {
  if (!context) {
    return messages
  }

  const next = messages.map((message) => ({ ...message }))
  for (let index = next.length - 1; index >= 0; index -= 1) {
    if (next[index].role === "assistant") {
      next[index].memory_context = context
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

function createRequestId() {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID()
  }
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`
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
