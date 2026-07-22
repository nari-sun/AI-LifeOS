# AGENTS.md

## Project

AI-LifeOS は、ChatGPT や Codex との会話をローカルPCに保存し、後から検索・要約・日記・長期メモリとして活用するための個人用AI記憶システムです。

目的は、会話ログ全文、会話ごとの要約、日付別の日記、長期メモリ、プロジェクト進捗をローカルに蓄積し、将来的に自分専用の第二の脳・AI秘書として使えるようにすることです。

## Current Status

現在は Phase3.10 まで実装済みです。次は Phase4.0 として、ローカル記憶用の読み取り専用MCPとは分けて、外部ツール連携の範囲を決めます。

実装済みの主要範囲:

* Phase1: Local Archive
* Phase2.5: `inbox/chat.txt` から raw.md / summary / journal / memory までの安全な自動化
* Phase2.6: PowerShell 上の live conversation CLI、JSONL逐次保存、終了時finalize
* Phase2.65: `.session.json` によるセッション保存、10日以内の resume、dry-run prune
* Phase2.7: Tauri 2 + React + Vite + TypeScript + Tailwind CSS + shadcn/ui の Chat GUI MVP
* Phase3: Markdown/SQLite検索、stale index fallback、回答用memory context、読み取り専用Memory MCP、軽量ハイブリッド検索、パーソナライズ管理

詳細なフェーズ履歴は [docs/phases.md](docs/phases.md) を参照してください。

## Must-Follow Rules

* OpenAI API は直接使わない。
* `.env` は前提にしない。
* ChatGPT Plus / Codex CLI 側を使う。
* ChatGPT公式Webや公式デスクトップアプリをスクレイピングしない。
* APIキーや秘密情報を保存しない。
* ユーザーがそのターンで明示的に許可しない限り、このリポジトリ外のファイルやディレクトリを参照・検索・編集しない。
* 会話ログにないことを summary / journal / memory に書かない。
* `memory/long_term.md` は長期的に重要な情報だけ扱い、既存情報を勝手に削除しない。
* journal は事実ベースで、AIがどう答えたか、その結果どうなったかを400文字程度で書く。結果が会話内で未確定なら未確定と書く。
* live会話中、GUI操作中、検索処理中に `memory` / `journal` / `conversations` を勝手に編集しない。
* 10日超セッションは resume 候補から外すが、会話ログ・live JSONL・セッション情報を削除しない。全文ログは10年以上保持する。
* `memory/search_index.sqlite3` はMarkdownから再生成できる派生データとして扱い、Git管理しない。
* ベクトルDBは本番導入しない。Markdown検索 + SQLite-backed index + Python ranking で足りない理由が明確になった場合に再評価する。

## PublicEdition Git Rules

PublicEdition では、個人データ・生成物を原則Git管理しません。

Git管理しないもの:

* `conversations/`
* `journal/`
* `memory/`
* `inbox/`
* `tasks/`
* `imports/`
* `renovationTickets/`

公開用プロジェクトファイルとして扱えるもの:

* `scripts/`
* `prompts/`
* `docs/`
* `desktop/`
* `config/`
* `templates/`
* `tests/`
* `README.md`
* `AGENTS.md`
* `.codex/`
* `.vscode/`
* `.gitignore`

commit / push 前の原則:

```powershell
python scripts\privacy_check.py --staged
python scripts\privacy_check.py --range origin/main..HEAD
```

公開前チェック:

```powershell
python scripts\privacy_check.py --publish
```

privacy check が失敗した場合は commit / push を中止し、検出箇所をユーザーへ報告してください。

## Active Workflows

### Pasted Chat

```powershell
.\scripts\save_chat.ps1
```

通常フロー:

1. `inbox/chat.txt` に会話を貼る。
2. `save_chat.ps1` が `conversations/YYYY/MM/YYYY-MM-DD_HHMMSS/raw.md` を作る。
3. `tasks/latest_codex_task.md` を作る。
4. `codex.cmd exec` で summary / journal / memory を更新する。
5. `memory/search_index.sqlite3` を再構築する。

公開用プロジェクトファイルだけ commit する場合:

```powershell
.\scripts\save_chat.ps1 -CommitPublicChanges
```

### Live Conversation CLI

```powershell
python scripts\codex_conversation.py
python scripts\codex_conversation.py --temporary
python scripts\codex_conversation.py --project-scope AI-LifeOS
```

ルール:

* user / assistant 発言を `inbox/live/*.jsonl` に逐次保存する。
* `/exit` または Ctrl+C で終了する。
* 終了時に `finalize_live_chat.py` 経由で raw.md 化し、既存の記憶整理へ接続する。
* 会話中の自由なファイル操作、memory編集、Git commit はしない。

### Session Save / Resume

```powershell
python scripts\session_store.py save
python scripts\session_store.py resume-list
python scripts\codex_conversation.py --resume
python scripts\session_store.py prune
```

ルール:

* resume 候補は最後の user 入力から10日以内に限定する。
* resume セッション一覧は新しい順に最大50件表示する。
* `prune` は resume 対象外になったセッションを一覧するだけで、削除しない。

### Chat GUI

推奨起動:

```powershell
.\scripts\chat_gui_task.ps1 -Mode dev
```

GUIは Phase2.6 の会話エンジンと Phase2.65 のセッション保存・再開処理を薄く呼びます。GUI中に `memory` / `journal` を勝手に編集しません。

### Searchable Memory

```powershell
python scripts\search_memory.py "検索語"
python scripts\search_memory.py "検索語" --type journal
python scripts\search_memory.py "" --tag Phase3
python scripts\search_memory.py "" --type memory_item --category study_status --status active --tag 資格
python scripts\rebuild_index.py
python scripts\build_answer_context.py "俺の好みに合う店は？"
```

検索は読み取り専用です。Phase3.7以降は固定スコアで検索をON/OFFせず、スコアをnarrow/deepの取得深度だけに使います。SQLite indexが古い場合はその回答中だけMarkdownへfallbackし、依頼表現除去、query variant、文字trigram、RRFをPython側で統合します。FTS5は補助テーブルであり、主経路ではありません。

`scripts/memory_mcp_server.py` はCodex会話から反復検索する読み取り専用MCPです。Phase3.10の設定に従い、長期memory、過去チャット、project scope、一時チャット除外を独立して適用します。

構造化メモリの`memory/items/*.md`、個人用`memory/categories.json`、カテゴリ提案はGit管理せず、「整理して保存」時だけ更新します。公開用の初期カテゴリは`config/memory_categories.example.json`、項目雛形は`templates/memory_item.md`です。

## Repository Layout

```text
AI-LifeOS/
├─ AGENTS.md
├─ README.md
├─ conversations/          # personal data, not tracked
├─ inbox/                  # personal data, not tracked
├─ journal/                # personal data, not tracked
├─ memory/                 # personal data/index, not tracked
├─ prompts/
├─ scripts/
├─ docs/
├─ desktop/
├─ config/
├─ templates/
├─ tests/
├─ logs/
└─ renovationTickets/      # local tickets, not tracked
```

## Documentation Map

* [docs/phases.md](docs/phases.md): フェーズ履歴と現在の到達点
* [docs/codex_conversation_mvp.md](docs/codex_conversation_mvp.md): Phase2.6 CLI会話MVP
* [docs/session_save_mvp.md](docs/session_save_mvp.md): Phase2.65 Session Save / Resume
* [docs/chat_gui_mvp.md](docs/chat_gui_mvp.md): Phase2.7 Chat GUI MVP
* [docs/searchable_memory.md](docs/searchable_memory.md): Phase3 Searchable Memory
* [docs/structured_memory.md](docs/structured_memory.md): 動的カテゴリ付き構造化メモリ
* [docs/vector_search_evaluation.md](docs/vector_search_evaluation.md): Phase3.5 Vector Search Evaluation
* [docs/phase4_planning_checkpoint.md](docs/phase4_planning_checkpoint.md): Phase3.6 Phase4引き継ぎ
* [docs/memory_mcp.md](docs/memory_mcp.md): Phase3.8 Read-only Memory MCP
* [docs/personalization.md](docs/personalization.md): Phase3.10 パーソナライズ管理

## Development Style

* いきなり大きな機能を作らず、小さく動く単位で進める。
* Windows PowerShellで動くことを優先する。
* 既存のスクリプト、ドキュメント、テスト構成に合わせる。
* 変更後は差分を確認しやすい粒度で報告する。
* ユーザーが明示しない限り、個人データや生成物をGit管理対象にしない。

優先順位:

```text
1. データを壊さない
2. 手動で確認できる
3. Git差分が読みやすい
4. 後から拡張できる
5. 自動化する
```

## Verification Commands

Python側:

```powershell
python -m unittest
```

GUI側:

```powershell
cd desktop\app
npm run build
```

Git差分確認:

```powershell
git status
git diff
```
