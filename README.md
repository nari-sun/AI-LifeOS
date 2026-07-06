# AI-LifeOS

## Privacy Check

Commit前チェック:

```powershell
python scripts\privacy_check.py --staged
```

PublicEdition公開前チェック:

```powershell
python scripts\privacy_check.py --publish
```

`--publish` は tracked files と未追跡の公開候補ファイルを対象に、通常の secret/email/phone 検出に加えて URL query secret、長いランダム文字列、`.env` 形式の秘密値、住所らしき文字列、個人データディレクトリの誤追加を強めに確認します。

誤検出の場合は、該当行に `privacy-check: allow` と理由をコメントで残してください。inline allowlist は住所・メールアドレス・長いランダム文字列など偽陽性が起きやすい検出だけに効きます。API key、token、bearer、URL query secret などの高確度secretは allowlist では通さず、公開前に除去してください。`conversations`、`journal`、`memory`、`inbox`、`tasks`、`renovationTickets` 配下の個人データも allowlist では通さず、PublicEditionに含めないでください。

AI-LifeOS は、ChatGPT や Codex との会話をローカルPCに保存し、後から検索・要約・日記・長期メモリとして活用するための個人用AI記憶システムです。

現在は Phase3 Searchable Memory までの実装が入っています。Phase2.6 の live conversation、Phase2.65 の Session Save / Resume、Phase2.7 の Tauri 2 + React GUI、Phase3 の検索・SQLite index・回答用記憶コンテキストを、OpenAI API 直叩きや `.env` 前提なしで動かす方針です。

運用の中心は、ローカルの Markdown / JSONL / SQLite、Codex CLI、Git です。ChatGPT公式Webや公式デスクトップアプリのスクレイピング、外部ベクトルDB、クラウド同期はまだ扱いません。

Windows PowerShellでMarkdownの日本語が文字化けして見える場合は、ファイル自体ではなく表示時の文字コードが原因のことがあります。確認するときはUTF-8を指定してください。

```powershell
Get-Content -Encoding UTF8 README.md
Get-Content -Encoding UTF8 prompts\codex_phase2_prompt.md
```

## Current Status

- Phase1: Local Archive は完了済み
- Phase2.5: `inbox/chat.txt` から raw.md / summary / journal / memory までの安全な自動化と、公開用ファイル限定のGit commitを実装済み
- Phase2.6: PowerShell上の live conversation CLI、JSONL逐次保存、終了時finalizeを実装済み
- Phase2.65: `.session.json` によるセッション保存、10日以内の resume、dry-run prune を実装済み
- Phase2.7: Tauri 2 + React + Vite + TypeScript + Tailwind CSS + shadcn/ui の Chat GUI MVP を実装済み
- Phase3: Markdown検索、タグ/メタデータ抽出、SQLite index、回答用memory context、ベクトル検索評価、Phase4引き継ぎを実装済み

## できること

- `inbox/chat.txt` に貼った会話を `conversations/YYYY/MM/YYYY-MM-DD_HHMMSS/raw.md` として保存する
- 保存した会話ごとに Codex 用タスク `tasks/latest_codex_task.md` を生成する
- `codex.cmd exec` で `summary.md`、`journal`、`memory/long_term.md` を更新する
- `scripts/save_chat.ps1` で保存とCodex実行をまとめて実行する
- PowerShell上で live 会話を行い、`inbox/live/*.jsonl` に user / assistant 発言を逐次保存する
- live JSONLを raw.md に変換し、既存の Phase2.5 記憶整理へ接続する
- live 会話セッションを `.session.json` として保存し、最後のuser入力から10日以内のセッションを再開する
- Tauri GUIから新規チャット、送信、保存、セッション再開、整理して保存を実行する
- 保存済みの raw.md / summary.md / journal / memory を検索する
- `memory/search_index.sqlite3` を再構築可能な検索indexとして生成する
- 私的な質問や好みに関係する会話では、`memory/long_term.md` と `memory/preferences.md` を読み取り専用コンテキストとして回答に渡す
- `python -m unittest` で Python 側の保存・再開・GUIブリッジ処理をテストする

## Phase Overview

```text
Phase1   : Local Archive
Phase2.0 : Semi-Automatic Memory Processing
Phase2.5 : Safer Automation
Phase2.6 : Codex Conversation MVP
Phase2.65: Session Save / Resume MVP
Phase2.7 : Chat GUI MVP
Phase3.0 : Searchable Memory Design
Phase3.1 : Markdown Search MVP
Phase3.2 : Tags and Metadata
Phase3.3 : SQLite Memory Index
Phase3.4 : Memory Retrieval for Answers
Phase3.5 : Vector Search Evaluation
Phase3.6 : Phase4 Planning Checkpoint
Phase4   : MCP Integration
Phase5   : Life Improvement Agent
Phase6   : Daily Automation
```

## Directory Layout

```text
AI-LifeOS/
├─ AGENTS.md
├─ README.md
├─ conversations/
│  └─ YYYY/
│     └─ MM/
│        └─ YYYY-MM-DD_HHMMSS/
│           ├─ raw.md
│           └─ summary.md
├─ inbox/
│  ├─ chat.txt
│  └─ live/
│     ├─ YYYY-MM-DD_HHMMSS.jsonl
│     └─ YYYY-MM-DD_HHMMSS.session.json
├─ journal/
│  └─ YYYY/
│     └─ MM/
│        └─ YYYY-MM-DD.md
├─ memory/
│  ├─ long_term.md
│  ├─ preferences.md
│  ├─ projects.md
│  └─ search_index.sqlite3  (generated, not tracked)
├─ prompts/
│  ├─ codex_phase2_prompt.md
│  ├─ journal_prompt.md
│  ├─ memory_extract_prompt.md
│  └─ summary_prompt.md
├─ scripts/
│  ├─ process_chat.py
│  ├─ save_chat.ps1
│  ├─ codex_conversation.py
│  ├─ finalize_live_chat.py
│  ├─ live_session.py
│  ├─ session_store.py
│  ├─ chat_gui_bridge.py
│  ├─ chat_gui_task.ps1
│  ├─ memory_index.py
│  ├─ search_memory.py
│  ├─ index_conversations.py
│  ├─ rebuild_index.py
│  ├─ build_answer_context.py
│  └─ codex_cli_options.py
├─ docs/
│  ├─ codex_conversation_mvp.md
│  ├─ session_save_mvp.md
│  ├─ chat_gui_mvp.md
│  ├─ searchable_memory.md
│  ├─ response_settings_ui.md
│  ├─ vector_search_evaluation.md
│  └─ phase4_planning_checkpoint.md
├─ desktop/
│  ├─ README.md
│  └─ app/
│     ├─ .nvmrc
│     ├─ package.json
│     ├─ src/
│     └─ src-tauri/
├─ logs/
├─ tasks/
│  └─ latest_codex_task.md
└─ tests/
   ├─ test_chat_gui_bridge.py
   ├─ test_codex_conversation.py
   ├─ test_finalize_live_chat.py
   ├─ test_live_session.py
   ├─ test_phase3_memory.py
   ├─ test_process_chat.py
   └─ test_session_store.py
```

## PublicEdition の Git 管理方針

PublicEdition では、会話ログや記憶ファイルなどの個人データを原則Git管理しません。

Git管理するもの:

- `scripts/`, `prompts/`, `docs/`, `desktop/`, `config/`, `templates/`, `tests/`
- `README.md`, `AGENTS.md`, `.gitignore`
- 空ディレクトリ維持用の `.gitkeep`

Git管理しないもの:

- `conversations/**`
- `journal/**`
- `memory/**`
- `inbox/chat.txt`
- `inbox/live/**`
- `tasks/**`
- `logs/*`
- `imports/**`
- `questionnaire/`
- `renovationTickets/`

個人データもGitで残したい場合は、PublicEditionではなくPrivateEditionやローカル専用運用として、`.gitignore` と commit 対象を明示的に変えて扱います。

## 基本フロー

### inbox/chat.txt 運用

```text
ChatGPTなどの会話をコピー
↓
inbox/chat.txt に貼る
↓
.\scripts\save_chat.ps1
↓
conversations/YYYY/MM/YYYY-MM-DD_HHMMSS/raw.md を作成
↓
tasks/latest_codex_task.md を作成
↓
Codexが summary / journal / memory を更新
↓
必要な場合だけ公開用プロジェクトファイルをGit commit
```

### live CLI 運用

```text
python scripts\codex_conversation.py
↓
PowerShell上で会話する
↓
inbox/live/YYYY-MM-DD_HHMMSS.jsonl に逐次保存
↓
/exit または Ctrl+C
↓
raw.md に変換
↓
summary / journal / memory を更新
↓
必要なら --commit-on-exit で公開用プロジェクトファイルだけGit commit
```

会話中は `journal`、`memory/long_term.md`、Git commit を実行しません。記憶整理は終了時の finalize 処理に限定します。

### Chat GUI 運用

```text
.\scripts\chat_gui_task.ps1 -Mode dev
↓
GUIで入力する
↓
Tauri command から scripts/chat_gui_bridge.py を呼ぶ
↓
既存Python処理で user / assistant を inbox/live/*.jsonl に保存
↓
10日以内のセッションを一覧・再開
↓
「整理して保存」で finalize_live_chat.py 相当の処理を実行
```

GUIの「整理して保存」は raw.md 作成と summary / journal / memory 更新に接続します。Git commit はGUIから自動実行しません。

## Codex Settings

会話返答生成:

```text
model: gpt-5.4-mini
model_reasoning_effort: medium
service_tier: fast
features.fast_mode: true
sandbox: read-only
approval: never
```

summary / journal / memory 更新:

```text
model: gpt-5.5
model_reasoning_effort: xhigh
sandbox: workspace-write
approval: never
```

各スクリプトの `--codex-model`、`--codex-reasoning-effort`、`--codex-sandbox`、`--codex-approval` で必要に応じて上書きできます。

GUIからのモデル・応答設定UIはまだ実装しません。会話返答生成と記憶整理の設定責務、将来GUIに出せる最小範囲は `docs/response_settings_ui.md` に整理しています。

## Commands

### 会話を保存するだけ

```powershell
python scripts\process_chat.py
```

`inbox/chat.txt` を読み、`raw.md` と `tasks/latest_codex_task.md` を作成します。Codex実行とGit commitは行いません。

### inboxを残して保存する

```powershell
python scripts\process_chat.py --keep-inbox
```

`inbox/chat.txt` を空にせず、`raw.md` と `latest_codex_task.md` だけ作成します。

### 日付を指定して保存する

```powershell
python scripts\process_chat.py --date 2026-06-28
```

指定した日付で `conversations/YYYY/MM/YYYY-MM-DD_HHMMSS/raw.md` を作成します。時刻部分は実行時刻を使います。

### Pythonだけで保存、記憶整理、公開用commitを実行する

```powershell
python scripts\process_chat.py --run-codex --commit
```

`raw.md` 作成後に Codex CLI で記憶整理を実行します。`--commit` は `scripts`、`prompts`、`docs`、`desktop`、`config`、`templates`、`tests`、`README.md`、`AGENTS.md`、`.gitignore` など公開用プロジェクトファイルだけをGit commit対象にします。会話ログ、journal、memory、inbox、task生成物は `.gitignore` に従ってローカルに残します。

### PowerShellから保存、Codex実行をまとめて実行する

```powershell
.\scripts\save_chat.ps1
```

内部で `python scripts\process_chat.py --run-codex` を実行します。通常はGit commitしません。

オプション:

```powershell
.\scripts\save_chat.ps1 -KeepInbox
.\scripts\save_chat.ps1 -Date 2026-06-28
.\scripts\save_chat.ps1 -SkipCodex
.\scripts\save_chat.ps1 -CommitPublicChanges
```

`-CommitPublicChanges` を付けた場合だけ、公開用プロジェクトファイルをGit commit対象にします。個人データは対象にしません。

### live会話CLIを起動する

```powershell
python scripts\codex_conversation.py
```

1起動を1セッションとして `inbox/live/YYYY-MM-DD_HHMMSS.jsonl` を作成します。ユーザー発言はCodexへ送る前に保存し、assistant返答は受信後に保存します。

主なオプション:

```powershell
python scripts\codex_conversation.py --no-ai
python scripts\codex_conversation.py --no-finalize-on-exit
python scripts\codex_conversation.py --no-process-on-exit
python scripts\codex_conversation.py --commit-on-exit
python scripts\codex_conversation.py --resume
python scripts\codex_conversation.py --no-memory-context
```

会話中コマンド:

```text
/resume
/resume latest
/resume 2026-07-01_223000
/exit
```

`/resume` は最後のuser入力が10日以内のセッションだけを候補にします。PowerShellの対話端末ではカーソル選択、パイプ入力などでは番号入力に戻ります。

### live JSONLをraw.md化する

```powershell
python scripts\finalize_live_chat.py
```

最新の `inbox/live/*.jsonl` を `conversations/YYYY/MM/YYYY-MM-DD_HHMMSS/raw.md` に変換し、`tasks/latest_codex_task.md` を作成します。元のJSONLは削除・移動しません。

オプション:

```powershell
python scripts\finalize_live_chat.py --file inbox\live\2026-07-01_223000.jsonl
python scripts\finalize_live_chat.py --run-codex
python scripts\finalize_live_chat.py --run-codex --commit
python scripts\finalize_live_chat.py --force
```

### liveセッションを保存・再開する

```powershell
python scripts\session_store.py save
python scripts\session_store.py list
python scripts\session_store.py resume-list
python scripts\session_store.py prune
python scripts\session_store.py prune --delete
```

ルール:

- `.session.json` は元の `inbox/live/*.jsonl` の横に作る
- 再開候補は最後のuser入力から10日以内に限定する
- `prune` はデフォルトでは削除しない
- 実削除は `prune --delete` を明示した場合だけ行う
- 削除対象は同名の `.jsonl` と `.session.json` に限定する

### 保存済み記憶を検索する

```powershell
python scripts\search_memory.py "検索語"
python scripts\search_memory.py "検索語" --no-index
python scripts\search_memory.py "検索語" --type journal
python scripts\search_memory.py "" --tag Phase3
python scripts\search_memory.py "検索語" --json
```

`raw.md` / `summary.md` / `journal` / `memory` を読み取り専用で検索します。

### SQLite indexを再構築する

```powershell
python scripts\index_conversations.py
python scripts\rebuild_index.py
python scripts\search_memory.py "検索語" --rebuild-index
```

DBは `memory/search_index.sqlite3` に作成されます。このDBはMarkdownから再生成できる派生データで、Git管理しません。

### 回答用memory contextを作る

```powershell
python scripts\build_answer_context.py "俺の好みに合う店は？"
```

私的な質問、好み、生活、学習進捗、過去行動、AI-LifeOSの過去方針に関係する質問では、`memory/long_term.md` と `memory/preferences.md` を優先して読み、必要に応じて `journal` と `summary.md` / `raw.md` を検索した短い抜粋を返します。

### Chat GUIを起動する

推奨:

```powershell
.\scripts\chat_gui_task.ps1 -Mode dev
```

このコマンドは `desktop\app` で `npm install` を実行し、続けて `npm run tauri dev` を起動します。GUI用ログの環境変数も設定します。

インストールだけ:

```powershell
.\scripts\chat_gui_task.ps1 -Mode install
```

配布用ビルド:

```powershell
.\scripts\chat_gui_task.ps1 -Mode build
```

直接実行する場合:

```powershell
cd desktop\app
npm install
npm run tauri dev
```

フロントエンドだけ確認する場合:

```powershell
cd desktop\app
npm run dev
```

Node.js は 22 LTS 以上を使います。バージョンは `desktop/app/.nvmrc` で `22.23.1` に固定しています。

GUIでできること:

- 新規チャット作成
- メッセージ送信
- user / assistant 発言の表示
- セッション保存
- 10日以内の再開可能セッション一覧表示
- セッション再開
- 「整理して保存」による raw.md 化と summary / journal / memory 更新
- エラー表示

GUIでまだやらないこと:

- 専用の過去ログ検索画面
- ベクトル検索
- MCP連携
- モデル・応答設定UI
- 10日超セッションの自動削除
- 会話中の memory / journal 自動編集
- 自動Git commit

### GUIブリッジを直接確認する

通常はGUIから呼ぶため、手動実行はデバッグ用です。

```powershell
python scripts\chat_gui_bridge.py --help
```

ブリッジのコマンド:

```text
start-session
send-message
save-session
list-resumable
resume-session
finalize-session
```

## Logs

GUI関連ログ:

```text
logs/chat_gui_task.log
logs/chat_gui_tauri.log
logs/chat_gui_bridge.log
```

`chat_gui_task.log` は PowerShellタスク、npm、Vite、Tauri dev/build の出力を残します。`chat_gui_tauri.log` は Tauri から Python ブリッジを呼ぶ前後の状態を残します。`chat_gui_bridge.log` は Python ブリッジのコマンド開始・完了・エラー種別を残します。

ログには会話本文を書かず、session id、文字数、件数、終了コードなどの診断情報だけを残します。

## Tests

Python側:

```powershell
python -m unittest
```

確認していること:

- `process_chat.py` が raw.md と `latest_codex_task.md` を作る
- `--date` と `--keep-inbox` が効く
- Codex CLI用コマンドが組み立てられる
- live会話JSONLが保存される
- live JSONLを raw.md に変換できる
- finalize後にCodex実行と公開用commitへ接続できる
- session save / list / resume-list / prune が動く
- 10日以内のセッションだけを resume 候補にする
- CLIの `/resume` が番号選択に対応する
- GUIブリッジが start / send / resume を処理できる
- GUIブリッジログに会話本文を残さない
- Phase3のMarkdown検索、タグ抽出、SQLite index、回答用memory contextが動く

GUI側のビルド確認:

```powershell
cd desktop\app
npm run build
```

## Codexで記憶整理する

通常は `.\scripts\save_chat.ps1` または live会話終了時の finalize が `codex.cmd exec` を実行します。

手動でCodexに渡したい場合は、まず `python scripts\process_chat.py` を実行し、生成された `tasks/latest_codex_task.md` を確認します。

```powershell
Get-Content -Raw -Encoding UTF8 tasks\latest_codex_task.md
```

この内容をCodexに渡すと、対象の `raw.md` を読んで以下を作成・更新します。

- 同じ会話フォルダの `summary.md`
- `journal/YYYY/MM/YYYY-MM-DD.md`
- `memory/long_term.md`
- `memory/preferences.md`
- `memory/projects.md`

`--run-codex` を使う処理の完了後は、`memory/search_index.sqlite3` も再構築されます。

Codex用プロンプトの元ファイルは `prompts/codex_phase2_prompt.md` です。

これらの会話ログ・記憶ファイル・検索indexはPublicEditionではGit管理しません。

## 方針

- 会話ログにないことは記録しない
- APIキーや秘密情報は保存しない
- `.env` やOpenAI API直叩きは前提にしない
- ChatGPT公式Webや公式デスクトップアプリをスクレイピングしない
- `memory/long_term.md` は長期的に重要な情報だけ追記する
- live会話中に `journal` や `memory/long_term.md` を勝手に編集しない
- 10日超セッションは自動削除しない
- Git commit はユーザー明示操作、または既存スクリプトの明示オプション経由にする
- PublicEdition の自動commit対象は公開用プロジェクトファイルに限定し、`conversations`、`journal`、`memory`、`inbox`、`tasks` はGit管理しない
- SQLite index は再生成可能な派生データとして扱い、Git管理しない
- ベクトル検索とMCP連携は、Phase3の検索基盤の上で必要性を確認してから扱う
