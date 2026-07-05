import { useEffect, useMemo, useRef, useState } from "react"
import {
  Archive,
  Bot,
  CheckCircle2,
  Loader2,
  MessageSquarePlus,
  RefreshCw,
  RotateCcw,
  Save,
  Send,
  User,
} from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { cn } from "@/lib/utils"
import {
  finalizeSession,
  isTauriRuntime,
  listResumableSessions,
  resumeSession,
  saveSession,
  sendMessage,
  startSession,
} from "@/tauri"
import type { ChatMessage, ResumeSession, SessionFile } from "@/types"

type BusyState = "idle" | "starting" | "sending" | "saving" | "resuming" | "finalizing" | "refreshing"

const busyLabel: Record<BusyState, string> = {
  idle: "",
  starting: "起動中",
  sending: "送信中",
  saving: "保存中",
  resuming: "再開中",
  finalizing: "整理中",
  refreshing: "更新中",
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

  async function saveCurrentSession() {
    if (!session || isBusy) {
      return
    }

    setBusy("saving")
    setError(null)
    try {
      await saveSession(session.jsonl_file)
      setNotice("セッションを保存しました")
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
    if (!session || isBusy) {
      return
    }

    setBusy("finalizing")
    setError(null)
    try {
      const result = await finalizeSession(session.jsonl_file)
      setNotice(`整理して保存しました: ${result.raw_file}`)
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
                    <Badge variant="secondary">{item.message_count}</Badge>
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
            <Button variant="outline" onClick={saveCurrentSession} disabled={!session || isBusy}>
              <Save className="h-4 w-4" />
              保存
            </Button>
            <Button variant="outline" onClick={finalizeCurrentSession} disabled={!session || isBusy || messages.length === 0}>
              <Archive className="h-4 w-4" />
              整理して保存
            </Button>
          </div>
        </header>

        <div className="border-b border-border bg-muted/40 px-4 py-2">
          <div className="flex min-h-6 items-center gap-2 text-sm">
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
