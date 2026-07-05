import { useEffect, useMemo, useRef, useState } from "react"
import {
  Archive,
  Bot,
  CheckCircle2,
  Loader2,
  MessageSquarePlus,
  RefreshCw,
  RotateCcw,
  Send,
  User,
} from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { cn } from "@/lib/utils"
import {
  cleanupExpiredSessions,
  finalizeSession,
  isTauriRuntime,
  listResumableSessions,
  resumeSession,
  sendMessage,
  startSession,
} from "@/tauri"
import type { ChatMessage, ResumeSession, SessionFile, SessionOrganization } from "@/types"

type BusyState = "idle" | "starting" | "sending" | "resuming" | "finalizing" | "refreshing" | "cleaning"

const busyLabel: Record<BusyState, string> = {
  idle: "",
  starting: "起動中",
  sending: "送信中",
  resuming: "再開中",
  finalizing: "整理中",
  refreshing: "更新中",
  cleaning: "期限切れ整理中",
}

function App() {
  const [session, setSession] = useState<SessionFile | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [sessions, setSessions] = useState<ResumeSession[]>([])
  const [input, setInput] = useState("")
  const [busy, setBusy] = useState<BusyState>("idle")
  const [notice, setNotice] = useState("AI-LifeOS Chat")
  const [error, setError] = useState<string | null>(null)
  const viewportRef = useRef<HTMLDivElement | null>(null)
  const initializedRef = useRef(false)

  const isBusy = busy !== "idle"
  const canSend = input.trim().length > 0 && !isBusy
  const organization = session?.organization ?? null
  const canFinalize = Boolean(session && organization?.can_organize && !isBusy)
  const finalizeButtonLabel = getFinalizeButtonLabel(organization)
  const statusLabel = organization?.label ?? "未開始"

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
      setError("Tauri環境で起動してください。")
      return
    }

    void initialize()
  }, [])

  useEffect(() => {
    const node = viewportRef.current
    if (node) {
      node.scrollTop = node.scrollHeight
    }
  }, [messages])

  async function initialize() {
    setBusy("starting")
    setError(null)
    try {
      const [sessionResult, listResult] = await Promise.all([startSession(), listResumableSessions()])
      setSession(sessionResult.session)
      setMessages(sessionResult.messages)
      setSessions(listResult.sessions)
      setNotice("新規セッションを開始しました")
      setBusy("cleaning")
      const cleanupResult = await cleanupExpiredSessions()
      if (cleanupResult.results.length > 0) {
        const failed = cleanupResult.results.filter((result) => result.error).length
        const deleted = cleanupResult.results.reduce((count, result) => count + result.deleted_paths.length, 0)
        setNotice(failed > 0 ? `期限切れ整理に失敗があります: ${failed}件` : `期限切れセッションを整理しました: ${deleted}件削除`)
        const refreshed = await listResumableSessions()
        setSessions(refreshed.sessions)
      }
    } catch (err) {
      setError(formatError(err))
    } finally {
      setBusy("idle")
    }
  }

  async function refreshSessions() {
    setBusy("refreshing")
    setError(null)
    try {
      const result = await listResumableSessions()
      setSessions(result.sessions)
      setNotice("セッション一覧を更新しました")
    } catch (err) {
      setError(formatError(err))
    } finally {
      setBusy("idle")
    }
  }

  async function createSession() {
    setBusy("starting")
    setError(null)
    try {
      const result = await startSession()
      setSession(result.session)
      setMessages(result.messages)
      setInput("")
      setNotice("新規セッションを開始しました")
      await refreshSessionsAfterAction()
    } catch (err) {
      setError(formatError(err))
    } finally {
      setBusy("idle")
    }
  }

  async function submitMessage() {
    const content = input.trim()
    if (!content || isBusy) {
      return
    }

    setBusy("sending")
    setError(null)
    setInput("")
    try {
      const result = await sendMessage(session?.jsonl_file ?? null, content)
      setSession(result.session)
      setMessages(result.messages)
      setNotice(result.assistant ? "返答を保存しました" : "入力を保存しました")
      if (result.error) {
        setError(result.error)
      }
      await refreshSessionsAfterAction()
    } catch (err) {
      setError(formatError(err))
    } finally {
      setBusy("idle")
    }
  }

  async function loadSession(sessionId: string) {
    if (isBusy) {
      return
    }

    setBusy("resuming")
    setError(null)
    try {
      const result = await resumeSession(sessionId)
      setSession(result.session)
      setMessages(result.messages)
      setNotice("セッションを再開しました")
    } catch (err) {
      setError(formatError(err))
    } finally {
      setBusy("idle")
    }
  }

  async function finalizeCurrentSession() {
    if (!session || !organization?.can_organize || isBusy) {
      return
    }
    const confirmed = window.confirm("raw.md作成、記憶整理、検索index更新を実行します。時間がかかる場合があります。")
    if (!confirmed) {
      return
    }

    setBusy("finalizing")
    setError(null)
    try {
      const result = await finalizeSession(session.jsonl_file)
      setSession(result.session)
      setNotice(result.organization.is_organized ? `整理済み: ${result.raw_file}` : result.organization.label)
      await refreshSessionsAfterAction()
    } catch (err) {
      setError(formatError(err))
    } finally {
      setBusy("idle")
    }
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

        <div className="border-b border-border p-3">
          <Button className="w-full justify-start" variant="secondary" onClick={createSession} disabled={isBusy}>
            <MessageSquarePlus className="h-4 w-4" />
            新規チャット
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
            <div className="truncate text-sm font-semibold">{sessionTitle}</div>
            <div className="truncate text-xs text-muted-foreground">{session?.jsonl_file ?? "inbox/live"}</div>
          </div>
          <div className="flex items-center gap-2">
            {busy !== "idle" && (
              <Badge>
                <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                {busyLabel[busy]}
              </Badge>
            )}
            <Badge variant={organization?.is_organized ? "secondary" : "outline"}>{statusLabel}</Badge>
            <Button variant="outline" onClick={finalizeCurrentSession} disabled={!canFinalize}>
              <Archive className="h-4 w-4" />
              {finalizeButtonLabel}
            </Button>
          </div>
        </header>

        <div className="border-b border-border bg-muted/40 px-4 py-2">
          <div className="flex min-h-6 flex-wrap items-center gap-2 text-sm">
            <div className="flex min-w-0 items-center gap-2">
              {error ? (
                <>
                  <RotateCcw className="h-4 w-4 shrink-0 text-destructive" />
                  <span className="break-words text-destructive">{error}</span>
                </>
              ) : (
                <>
                  <CheckCircle2 className="h-4 w-4 shrink-0 text-primary" />
                  <span className="break-words text-muted-foreground">{notice}</span>
                </>
              )}
            </div>
            {organization && <OrganizeStages organization={organization} running={busy === "finalizing"} />}
          </div>
          {organization?.last_error && !error && (
            <div className="mt-1 break-words text-xs text-destructive">{organization.last_error}</div>
          )}
        </div>

        <div ref={viewportRef} className="min-h-0 flex-1 overflow-y-auto px-5 py-5">
          <div className="mx-auto flex max-w-4xl flex-col gap-4">
            {messages.length === 0 ? (
              <div className="flex min-h-80 items-center justify-center rounded-md border border-dashed border-border text-sm text-muted-foreground">
                メッセージなし
              </div>
            ) : (
              messages.map((message, index) => <MessageBubble key={`${message.timestamp}-${index}`} message={message} />)
            )}
          </div>
        </div>

        <form
          className="shrink-0 border-t border-border bg-background px-5 py-4"
          onSubmit={(event) => {
            event.preventDefault()
            void submitMessage()
          }}
        >
          <div className="mx-auto flex max-w-4xl items-end gap-3">
            <Textarea
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={handleInputKeyDown}
              disabled={isBusy}
              placeholder="メッセージを入力"
              className="max-h-44 min-h-20 resize-none"
            />
            <Button type="submit" size="icon" disabled={!canSend} title="送信">
              {busy === "sending" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
            </Button>
          </div>
        </form>
      </section>
    </main>
  )
}

function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user"
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
          <div className="whitespace-pre-wrap break-words">{message.content}</div>
        </div>
        <div className={cn("mt-1 text-xs text-muted-foreground", isUser ? "text-right" : "text-left")}>
          {formatDateTime(message.timestamp)}
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

function formatError(error: unknown) {
  if (error instanceof Error) {
    return error.message
  }
  return String(error)
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

export default App
