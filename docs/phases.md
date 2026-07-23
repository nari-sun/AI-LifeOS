# AI-LifeOS Phase History

この文書は、AI-LifeOS のフェーズ履歴と実装済み範囲を整理するための参照資料です。作業時に必ず守る現在ルールは [AGENTS.md](../AGENTS.md) を優先してください。

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
Phase3.3 : SQLite-backed Memory Index MVP
Phase3.4 : Memory Retrieval for Answers
Phase3.5 : Vector Search Evaluation
Phase3.6 : Phase4 Planning Checkpoint
Phase3.7 : Retrieval Correctness
Phase3.8 : Read-only Memory MCP
Phase3.9 : Hybrid Local Retrieval
Phase3.10: Personalization Controls
Phase4   : External Tool Integration
Phase5   : Life Improvement Agent
Phase6   : Daily Automation
```

## Current Checkpoint

現在は Phase3.10 まで実装済みです。

Phase4.0 では、Phase3 のローカル記憶MCPとは分けて、外部MCP・外部ツール連携の範囲を決めます。

## Phase1: Local Archive

完了済みです。

実装済み:

* AI-LifeOS フォルダ作成
* Git管理開始
* `conversations` / `inbox` / `journal` / `memory` / `scripts` などの基本構成作成
* `inbox/chat.txt` に会話を貼る運用
* `scripts/process_chat.py` で `raw.md` を `conversations` 配下に保存

## Phase2.0: Semi-Automatic Memory Processing

完了済みです。

目的:

* raw.md 保存後に `summary.md` を作る
* 日付別 `journal/YYYY/MM/YYYY-MM-DD.md` を更新する
* `memory/long_term.md` に長期的に役立つ情報だけ追記する

重要ルール:

* 会話ログにないことは書かない
* journal は事実ベースで、結果が未確定なら未確定と書く
* memory は重複を避け、既存情報を勝手に削除しない

## Phase2.5: Safer Automation

完了済みです。

実装済み:

* `tasks/latest_codex_task.md` の生成
* `prompts/codex_phase2_prompt.md` による Codex 用プロンプト管理
* `codex.cmd exec` による summary / journal / memory 更新
* `scripts/save_chat.ps1` による保存からCodex実行までの自動化
* `-CommitPublicChanges` 指定時だけ公開用プロジェクトファイルを commit 対象にする運用

## Phase2.6: Codex Conversation MVP

完了済みです。詳細は [codex_conversation_mvp.md](codex_conversation_mvp.md) を参照してください。

実装済み:

* `scripts/codex_conversation.py`
* `scripts/live_session.py`
* `scripts/finalize_live_chat.py`
* PowerShell上での継続会話
* `inbox/live/*.jsonl` への user / assistant 発言の逐次保存
* `/exit` または Ctrl+C 終了時の raw.md 化
* 既存Phase2.5処理への接続

採用方針:

* OpenAI APIを直接叩かない
* ChatGPT公式Webや公式デスクトップアプリをスクレイピングしない
* 現MVPでは `codex.cmd exec` を read-only サンドボックスで使う
* 会話中に `memory` / `journal` を勝手に編集しない

## Phase2.65: Session Save / Resume MVP

完了済みです。詳細は [session_save_mvp.md](session_save_mvp.md) を参照してください。

実装済み:

* `scripts/session_store.py`
* `inbox/live/YYYY-MM-DD_HHMMSS.session.json` によるセッションメタデータ管理
* `save` / `list` / `resume-list` / `prune`
* `python scripts\codex_conversation.py --resume`
* 会話中 `/resume` による再開

保持ルール:

* user入力のあるliveセッションは経過日数に関係なくresume候補にする
* `prune` は指定日数を超えたセッションの参考表示のみで、resume可否に影響しない
* 会話ログ・live JSONL・セッション情報は削除しない

## Phase2.7: Chat GUI MVP

完了済みです。詳細は [chat_gui_mvp.md](chat_gui_mvp.md) を参照してください。

採用技術:

```text
Tauri 2
+ React
+ Vite
+ TypeScript
+ Tailwind CSS
+ shadcn/ui
+ 既存Pythonスクリプト呼び出し
```

実装済み:

* Tauri GUIから既存Python処理を呼ぶブリッジ
* 新規チャット
* 送信
* セッション保存
* 経過日数に関係しないセッション再開
* 整理して保存
* GUIログ

スコープ外:

* 過去ログ検索UI
* ベクトルDB検索
* MCP連携
* クラウド同期
* ChatGPT公式WebのDOM取得
* GUI中の `memory` / `journal` 自動編集

## Phase3: Searchable Memory

完了済みです。詳細は [searchable_memory.md](searchable_memory.md) を参照してください。

検索対象:

```text
conversations/**/raw.md
conversations/**/summary.md
journal/**/*.md
memory/long_term.md
memory/preferences.md
memory/projects.md
```

### Phase3.0: Searchable Memory Design

検索対象、メタデータ、DB化方針を整理済みです。

### Phase3.1: Markdown Search MVP

`scripts/search_memory.py` で保存済みMarkdownを読み取り専用検索できます。

### Phase3.2: Tags and Metadata

`summary.md` の `## タグ` / `## Tags` / `## Tag` からタグを抽出し、タグ検索に使います。

### Phase3.3: SQLite-backed Memory Index MVP

`scripts/index_conversations.py` と `scripts/rebuild_index.py` で `memory/search_index.sqlite3` を作成・再構築できます。

現時点の検索方式は `SQLite-backed index + Python ranking` です。SQLiteには全文とメタデータを保存し、検索時はSQLiteから対象文書を読み出して、Python側で日本語の部分一致ランキングを行います。

FTS5は環境によって日本語トークン化が弱いため、MVPでは検索品質を優先してPython側の一致判定を使います。`documents_fts` が作成される環境でも、現在の検索結果ランキングの主経路はFTS5ではありません。

### Phase3.4: Memory Retrieval for Answers

`scripts/build_answer_context.py` が読み取り専用の回答用コンテキストを生成します。Phase3.7以降、非空質問には最低限のbounded narrow検索を行い、私的な質問、好み、生活、学習進捗、過去行動などのsignal scoreはraw会話まで広げるdeep検索の判断にだけ使います。

`scripts/codex_conversation.py` は通常の会話返答生成時にこのコンテキストをプロンプトへ渡します。無効化する場合:

```powershell
python scripts\codex_conversation.py --no-memory-context
```

### Phase3.5: Vector Search Evaluation

ベクトル検索は本番導入していません。評価結果は [vector_search_evaluation.md](vector_search_evaluation.md) に整理済みです。

結論:

* まずはMarkdown検索 + SQLite-backed index + Python ranking で運用する
* ベクトル検索は、キーワード検索で見つからない類義語・文脈検索が明確に必要になってから導入する
* OpenAI API直叩きや外部送信を前提にしない

### Phase3.6: Phase4 Planning Checkpoint

Phase4への引き継ぎは [phase4_planning_checkpoint.md](phase4_planning_checkpoint.md) に整理済みです。

Phase4では、まず Filesystem MCP / GitHub MCP / Playwright MCP など、検索・記憶取得と相性がよく、個人情報リスクを管理しやすい連携から検討します。

### Phase3.7: Retrieval Correctness

固定スコアを検索ON/OFFのゲートとして使う方式を廃止しました。すべての非空質問でboundedなnarrow検索を行い、スコアはraw会話まで読むdeep検索の深度にだけ使います。

SQLite indexには元Markdownのpath・mtime・size manifestと、schema/raw metadata parser versionを追加しました。indexの欠損、旧schema/parser、追加・更新・削除を読み取り専用で検知し、legacy/stale時はindexを書き換えず、その回答だけ現在のMarkdownへfallbackします。現在回答中のlive sessionは過去記憶から除外し、「作品名なし・summaryなし・stale index」でもuser一次発言へ到達する回帰テストを含みます。

### Phase3.8: Read-only Memory MCP

`scripts/memory_mcp_server.py` を実装しました。Codex会話プロセスへ一時的に接続し、次の読み取り専用ツールで検索語の改写、一次発言の確認、index診断を反復できます。

* `search_past_chats`
* `open_conversation`
* `get_personal_memory`
* `get_index_health`

外部MCP package、API key、`.env`、OpenAI API直叩きは不要です。path traversalを拒否し、一時チャットと`exclude_from_memory`は壊れたmetadataを含めfail-closedで検索対象外にします。詳細は [memory_mcp.md](memory_mcp.md) を参照してください。

### Phase3.9: Hybrid Local Retrieval

依頼表現除去、一般的なtopic/query variant、保守的な文字trigramをOR候補として作り、reciprocal-rank fusionで統合します。個人の話題を対応付ける固定語彙bridgeは持たず、静的検索で語彙が一致しない場合はMemory MCPが検索語を変えて反復検索します。外部embeddingやvector DBは導入していません。将来、完全ローカルrankerを比較できる`LocalSemanticBackend` interfaceだけを追加しています。

### Phase3.10: Personalization Controls

長期memoryと過去チャット検索の独立ON/OFF、project scope、一時チャット、読み取り専用memory preview、回答ごとの静的context・MCP検索候補・MCP open済み一次資料・取得理由・index health表示をChat GUIへ追加しました。全体既定値と現在セッション設定は別々に保存し、暗黙作成セッションにも既定値をsnapshotします。一時チャットは最初の発言前にだけ指定でき、live JSONLを保持しつつ、回答時の記憶利用、将来の検索、raw化、summary/journal/memory整理から除外します。詳細は [personalization.md](personalization.md) を参照してください。

Phase3.10後のimport/retrieval保守として、ChatGPT exportを内容fingerprint付きrevisionとして扱い、更新前rawを検索対象外のbackupへ保存するようにしました。音声文字起こしをテキストとして復元し、GUIの初期選択・期間filter・競合表示をfail-closedに変更しています。GUI import後は派生indexを再構築し、通常のnarrow retrievalでも強く一致したuser raw発言を1件だけ参照します。summary / journal / memoryへの昇格は引き続き明示操作時だけです。

## Phase4: External Tool Integration

未実装です。

目的:

* AI-LifeOSを外部ツールと連携させる
* ローカル記憶を読み取り、必要に応じて安全に外部操作する
* ファイル操作、GitHub、Web確認などの範囲と安全ルールを定義する

Phase4の具体範囲は Phase4.0 で決めます。

## Phase5: Life Improvement Agent

未実装です。

候補:

* 日々の生活ログや会話履歴から改善提案を作る
* 予定、体調、学習、開発進捗を横断して振り返る
* ユーザー確認なしに生活判断や外部操作を自動化しない

## Phase6: Daily Automation

未実装です。

候補:

* その日の会話・ログ収集
* summary生成
* journal更新
* memory更新候補作成
* 差分確認
* 明示操作による commit

完全自動にする前に、必ず確認用モードを作ります。
