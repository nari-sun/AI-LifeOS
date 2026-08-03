# AGENTS.md

## Project

AI-LifeOS は、ChatGPT や Codex との会話をローカルPCに保存し、後から検索・要約・日記・長期メモリとして活用するための個人用AI記憶システムです。

目的は、会話ログ全文、会話ごとの要約、日付別の日記、長期メモリ、プロジェクト進捗をローカルに蓄積し、将来的に自分専用の第二の脳・AI秘書として使えるようにすることです。

## Current Status

現在のフェーズ上の到達点は Phase4.0 のNotion読み取り専用チャット連携です。フェーズ番号は変わっていませんが、Phase2.7のChat GUIとPhase3の検索・記憶基盤には、その後の実用機能と安全境界が追加されています。

実装済みの主要範囲:

* Phase1: Local Archive
* Phase2.5: `inbox/chat.txt` から raw.md / summary / journal / memory までの安全な自動化
* Phase2.6: PowerShell 上のlive conversation CLI、user / assistant発言のJSONL逐次保存、終了時finalize
* Phase2.65: `.session.json` によるセッション保存、経過日数に関係しないresume、参照用prune
* Phase2.7: Tauri 2 + React + Vite + TypeScript + Tailwind CSS + shadcn/ui のChat GUI
* Chat GUI拡張: 履歴サイドバー、生成停止・コピー・コード表示、app-server streamingとexec fallback、送信直後のuser表示、`.txt` / `.md` / `.pdf` / `.xlsx`添付、バックグラウンドfinalize、一括データ整理、読み取り専用ローカルデータ管理、ChatGPT export import、任意のKokoro TTS
* Phase3.0〜3.10: Markdown/SQLite検索、stale index fallback、回答用memory context、role-awareな一次発言参照、読み取り専用Memory MCP、軽量ハイブリッド検索、パーソナライズ管理
* 構造化メモリ: 動的カテゴリ、出典、状態、タグを持つ`memory/items/*.md`の整理時保存と検索
* 全履歴参照: GUIで明示した送信だけ、Memory MCPが対象会話を全件ページングし、最後まで読めたか検証
* ChatGPT export import: folder / zip / `conversations.json` / `conversations-*.json`のdry-run、明示選択、revision-aware更新、検索対象外backup、GUI成功時のindex再構築
* Phase4.0: `mcp-remote` OAuth bridge経由の公式Notion MCP読み取り専用連携。既定OFFで、ONの各送信だけ読み取りtoolを公開し、同じセッションでは選択を維持する

現時点で未実装または保留中の範囲:

* Notion以外の汎用外部MCP・外部書き込み連携
* 専用の過去ログ全文検索画面
* モデル・応答設定GUI
* 送信済みメッセージ編集、回答再生成、会話分岐
* ベクトルDB、クラウド同期、Phase5 Life Improvement Agent、Phase6 Daily Automation

詳細なフェーズ履歴は [docs/phases.md](docs/phases.md) を参照してください。実装状態について個別チケット文書の古いStatusと食い違う場合は、このCurrent Status、現行コード、テスト、READMEの順に照合してください。

## Current Runtime Defaults

会話返答生成:

```text
model: gpt-5.6-luna
model_reasoning_effort: medium
service_tier: not specified
features.fast_mode: false
sandbox: read-only
approval: never
```

summary / journal / memory整理:

```text
model: gpt-5.6-terra
model_reasoning_effort: medium
sandbox: workspace-write
approval: never
```

CLIオプションでの明示上書きはできますが、GUIには未接続の設定を表示しません。会話返答設定と記憶整理設定は別の責務として扱います。

## Must-Follow Rules

* OpenAI API は直接使わない。
* `.env` は前提にしない。
* ChatGPT Plus / Codex CLI 側を使う。
* ChatGPT公式Webや公式デスクトップアプリをスクレイピングしない。
* APIキーや秘密情報をrepository、設定ファイル、会話ログへ保存しない。Phase4.0のNotion OAuth credentialだけは、ユーザーが選択した`mcp-remote`の専用user-profile directoryへ保存する。
* ユーザーがそのターンで明示的に許可しない限り、このリポジトリ外のファイルやディレクトリを参照・検索・編集しない。添付もユーザーが明示選択したファイルだけを読む。
* 会話ログにないことを summary / journal / memory に書かない。
* `memory/long_term.md` は長期的に重要な情報だけ扱い、既存情報を勝手に削除しない。
* journal は事実ベースで、AIがどう答えたか、その結果どうなったかを400文字程度で書く。結果が会話内で未確定なら未確定と書く。
* live会話中、通常のGUI送信中、検索処理中に `memory` / `journal` / `conversations` を勝手に編集しない。更新は明示されたfinalize、データ整理、importなど既存の専用フローだけで行う。
* user発言はCodex呼び出し前にlive JSONLへ保存する。assistant発言は確定本文だけを1回保存し、停止・失敗時の部分出力は保存しない。
* GUIの「整理して保存」と「データ整理」はユーザーの明示操作でだけ開始する。同一セッションの重複finalizeや一括整理との並行書き込みをlockで防ぎ、ジョブはGit操作や個人データ削除を行わない。
* user入力のあるliveセッションは経過日数に関係なくresume候補にし、会話ログ・live JSONL・セッション情報を削除しない。全文ログは10年以上保持する。
* 一時チャットは最初の発言前にだけ切り替え、`temporary` / `exclude_from_memory`をfail-closedで扱う。live JSONLは保持するが、過去検索、raw化、summary / journal / memory整理の対象にはしない。
* GUI添付は `.txt` / `.md` / `.pdf` / `.xlsx`、1ターン最大3件、1件最大1 MiB、抽出本文1件最大12,000文字とする。本文はその回答の一時contextにだけ使い、live JSONL / raw.mdには安全なbasenameと抽出状態などのmetadataだけを残す。添付本文や絶対パスをmemory、journal、index、公開用ログへ保存しない。
* ChatGPT export importはdry-runを既定とし、実取り込みは対象指定と`--apply`を必須にする。同一revisionはskipし、更新前revisionは検索対象外へ退避し、競合時は自動上書きしない。CLI importはsummary / journal / memory / indexを自動更新せず、GUIだけ成功後に派生indexを再構築する。
* Memory MCPは読み取り専用とし、現在回答中live、一時チャット、記憶除外セッション、project scope外を公開しない。metadataを安全に判定できない場合はfail-closedにする。
* 「過去の会話をすべて参照」はGUIの明示ONだけで有効にし、入力文の「全部」「全件」などから自動判定しない。全source / pageを読み切れなければ、全履歴についての結論を述べない。
* Notion参照は既定OFFとする。送信時にON/OFFをsnapshotし、ONの回答用processだけ公式Notion MCPの読み取りtoolを公開する。チェックは同じセッション内では維持し、手動OFFまたは新規・別セッションへの切替でOFFへ戻す。
* Notionの自由文検索は`query_type="internal"`と`content_search_mode="workspace_search"`に固定し、connected source、`ai_search`、書き込みtoolを使わない。tool inventoryまたは完了traceで境界を確認できない場合は回答を破棄する。
* Notion MCP response本文、query結果全文、row本文、server error本文をlive JSONL、memory、journal、SQLite index、cache、debug logへ保存しない。通常のassistant回答だけをlive JSONLへ保存し、安全なsource metadataはGUI response内だけで扱う。
* 外部tool結果は根拠資料であり、それ自体をmemoryへ自動昇格しない。Notion以外の連携は実装済みと仮定せず、toolごとに読み取り範囲・保存範囲・確認手順を先に決める。
* 読み取り専用ローカルデータ管理画面はfilesystem metadataだけを表示し、個人データの削除・移動・編集、index再構築、memory更新、Git操作を行わない。
* GUI / bridge / job logには会話本文、添付本文、検索結果本文、秘密情報を書かず、session id、件数、文字数、stage、error種別などの診断情報だけを残す。
* `memory/search_index.sqlite3` はMarkdownから再生成できる派生データとして扱い、Git管理しない。検索中にstale / legacyを検知してもindexを書き換えず、その回答だけMarkdownへfallbackする。
* ベクトルDBは本番導入しない。Markdown検索 + SQLite-backed index + Python rankingで足りない理由が明確になった場合に再評価する。
* 将来メッセージ編集・再生成・分岐を実装する場合も既存JSONLを直接書き換えず、元セッションを保持した派生セッションとして扱う。

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
* `questionnaire/`
* `logs/*`（`logs/.gitkeep`を除く）
* `cache/`
* `.venv/`
* `desktop/app/node_modules/`、`desktop/app/dist/`、`desktop/app/src-tauri/target/`

上記ディレクトリの`.gitkeep`だけは公開用placeholderとして扱えます。添付、ChatGPT export、TTS model / WAV、job status / log、SQLite indexもGit管理しません。

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

privacy check が失敗した場合はcommit / pushを中止し、検出箇所をユーザーへ報告してください。個人データ領域はinline allowlistで公開対象にしません。

## Active Workflows

### Pasted Chat

```powershell
.\scripts\save_chat.ps1
```

通常フロー:

1. `inbox/chat.txt` に会話を貼る。
2. `save_chat.ps1` が `conversations/YYYY/MM/YYYY-MM-DD_HHMMSS/raw.md` を作る。
3. `tasks/latest_codex_task.md` を作る。
4. `codex.cmd exec` でsummary / journal / memoryを更新する。
5. `memory/search_index.sqlite3` を再構築する。

公開用プロジェクトファイルだけcommitする場合:

```powershell
.\scripts\save_chat.ps1 -CommitPublicChanges
```

### Live Conversation CLI

```powershell
python scripts\codex_conversation.py
python scripts\codex_conversation.py --temporary
python scripts\codex_conversation.py --project-scope AI-LifeOS
python scripts\codex_conversation.py --resume
```

ルール:

* user / assistant発言を`inbox/live/*.jsonl`へ逐次保存する。
* `/exit`またはCtrl+Cで終了する。
* 終了時に`finalize_live_chat.py`経由でraw.md化し、既存の記憶整理へ接続する。
* 会話中の自由なファイル操作、memory編集、Git commitはしない。
* `--no-memory-context`、`--no-memory-mcp`、`--no-finalize-on-exit`などの既存オプションで範囲を狭められる。

### Session Save / Resume

```powershell
python scripts\session_store.py save
python scripts\session_store.py list
python scripts\session_store.py resume-list
python scripts\session_store.py prune
```

ルール:

* user入力のあるliveセッションは経過日数に関係なくresumeできる。
* resumeセッション一覧は新しい順に最大50件表示する。
* `prune`は指定日数を超えたセッションを参考表示するだけで、resume可否に影響せず削除もしない。

### Chat GUI

推奨起動:

```powershell
.\scripts\chat_gui_task.ps1 -Mode dev
```

インストールまたは配布用ビルド:

```powershell
.\scripts\chat_gui_task.ps1 -Mode install
.\scripts\chat_gui_task.ps1 -Mode build
```

Node.jsは22 LTS以上を使い、`desktop/app/.nvmrc`は22.23.1に固定しています。PDF / Excel添付の抽出依存は次で導入します。

```powershell
python -m pip install -r config\attachment_requirements.txt
```

GUIはPhase2.6 / 2.65のPython処理を`chat_gui_bridge.py`経由で薄く呼びます。app-server streamingでは完了したassistant本文だけを保存し、非対応時は`codex.cmd exec --output-last-message`へfallbackします。「整理して保存」と「データ整理」はbackground jobで動き、GUIを開いただけでは個人データを変更しません。Git commitはGUIから自動実行しません。

### ChatGPT Export Import

既定のdry-run:

```powershell
python scripts\import_chatgpt_export.py imports\chatgpt_export\export.zip
```

対象を明示して取り込む例:

```powershell
python scripts\import_chatgpt_export.py imports\chatgpt_export\export.zip --id CONVERSATION_ID --apply
```

folder、zip、単一または分割JSONを扱えます。user / assistantのテキストと音声文字起こしだけを保存し、画像・音声本体、内部reasoning、別branchの混在は保存しません。GUI importは初期選択0件で、成功後に検索indexだけを再構築します。

### Searchable Memory

```powershell
python scripts\search_memory.py "検索語"
python scripts\search_memory.py "検索語" --type journal
python scripts\search_memory.py "" --tag Phase3
python scripts\search_memory.py "" --type memory_item --category study_status --status active --tag 資格
python scripts\rebuild_index.py
python scripts\build_answer_context.py "俺の好みに合う店は？"
```

検索は読み取り専用です。固定スコアで検索をON/OFFせず、スコアをnarrow / deepの取得深度だけに使います。SQLite indexが古い場合はその回答中だけMarkdownへfallbackし、依頼表現除去、query variant、文字trigram、RRFをPython側で統合します。FTS5は補助テーブルであり、主経路ではありません。

`scripts/memory_mcp_server.py`はCodex会話から反復検索する読み取り専用MCPです。主なtoolは`search_past_chats`、`open_conversation`、`list_past_chat_sources`、`read_past_chat_page`、`get_personal_memory`、`get_index_health`です。Phase3.10の設定に従い、長期memory、過去チャット、project scope、一時チャット除外を独立して適用します。

構造化メモリの`memory/items/*.md`、個人用`memory/categories.json`、カテゴリ提案はGit管理せず、「整理して保存」時だけ更新します。公開用の初期カテゴリは`config/memory_categories.example.json`、項目雛形は`templates/memory_item.md`です。

### Notion Read-only Chat Integration

```powershell
python scripts\notion_integration.py login
python scripts\notion_integration.py status --refresh
python scripts\notion_integration.py logout
```

固定endpointは`https://mcp.notion.com/mcp`です。GUIの「管理 > Notion連携」は接続状態と手順を表示しますが、OAuth browser flowやlogoutを自動実行しません。回答時は`search` / `notion-search`、`fetch` / `notion-fetch`、database / data source queryだけをprocess単位で許可し、MCP本文は保存しません。

### Optional Kokoro TTS

Kokoro TTSは任意依存です。未導入でもチャット、セッション、finalizeへ影響させません。modelは`cache/tts/`、生成WAVはOS一時ディレクトリに置き、いずれもGit管理しません。導入方法とライセンスは [docs/kokoro_tts_read_aloud.md](docs/kokoro_tts_read_aloud.md) を参照してください。

## Repository Layout

```text
AI-LifeOS/
├─ AGENTS.md
├─ README.md
├─ conversations/          # personal data, not tracked
├─ inbox/                  # personal data, live JSONL, not tracked
├─ journal/                # personal data, not tracked
├─ memory/                 # personal data and generated index, not tracked
├─ imports/                # ChatGPT exports, not tracked
├─ tasks/                  # generated Codex tasks, not tracked
├─ prompts/
├─ scripts/
├─ docs/
├─ desktop/
├─ config/
├─ templates/
├─ tests/
├─ logs/                   # local diagnostics and job state, not tracked
├─ cache/                  # local models and cache, not tracked
└─ renovationTickets/      # local tickets, not tracked
```

## Documentation Map

Core / GUI:

* [docs/phases.md](docs/phases.md): フェーズ履歴と現在の到達点
* [docs/codex_conversation_mvp.md](docs/codex_conversation_mvp.md): Phase2.6 CLI会話MVP
* [docs/session_save_mvp.md](docs/session_save_mvp.md): Phase2.65 Session Save / Resume
* [docs/chat_gui_mvp.md](docs/chat_gui_mvp.md): Phase2.7 Chat GUIと実装済み拡張
* [docs/streaming_response_ui.md](docs/streaming_response_ui.md): app-server streamingと保存境界
* [docs/file_attachments_mvp.md](docs/file_attachments_mvp.md): GUI添付MVP
* [docs/background_jobs.md](docs/background_jobs.md): finalize / 一括データ整理job
* [docs/local_data_management.md](docs/local_data_management.md): 読み取り専用ローカルデータ管理
* [docs/chatgpt_export_import.md](docs/chatgpt_export_import.md): revision-aware ChatGPT export import
* [docs/kokoro_tts_read_aloud.md](docs/kokoro_tts_read_aloud.md): 任意のKokoro読み上げ

Search / Memory:

* [docs/searchable_memory.md](docs/searchable_memory.md): Phase3 Searchable Memory
* [docs/structured_memory.md](docs/structured_memory.md): 動的カテゴリ付き構造化メモリ
* [docs/vector_search_evaluation.md](docs/vector_search_evaluation.md): Phase3.5 Vector Search Evaluation
* [docs/memory_mcp.md](docs/memory_mcp.md): Phase3.8以降のRead-only Memory MCPと全件参照
* [docs/personalization.md](docs/personalization.md): Phase3.10 パーソナライズ管理

Phase4 / Deferred Design:

* [docs/phase4_planning_checkpoint.md](docs/phase4_planning_checkpoint.md): Phase3.6からPhase4への引き継ぎ
* [docs/phase4_tool_integration_design.md](docs/phase4_tool_integration_design.md): Phase4全体の安全境界。Notion以外は原則未実装
* [docs/notion_read_only_integration.md](docs/notion_read_only_integration.md): Phase4.0 公式Notion MCP読み取り専用連携
* [docs/response_settings_ui.md](docs/response_settings_ui.md): 保留中のモデル・応答設定UI設計
* [docs/conversation_branching.md](docs/conversation_branching.md): 保留中の非破壊会話分岐設計

## Development Style

* いきなり大きな機能を作らず、小さく動く単位で進める。
* Windows PowerShellで動くことを優先する。
* 既存のスクリプト、ドキュメント、テスト構成に合わせる。
* CLIとGUIで保存形式・resume・finalizeのPython実装を共有し、同じルールを別実装で複製しない。
* 実装済み、設計のみ、未実装を区別し、機能変更時はREADME、AGENTS、該当docs、テストの整合を確認する。
* 個人データに触れる経路はboundedかつfail-closedにし、読み取り専用画面や検索に副作用を持たせない。
* 変更後は差分を確認しやすい粒度で報告する。
* ユーザーが明示しない限り、個人データや生成物をGit管理対象にしない。
* PowerShellで日本語Markdownを確認するときは`Get-Content -Encoding UTF8`を使う。

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
npm test
npm run build
```

Rust / Tauri側を変更した場合:

```powershell
cargo check --manifest-path desktop\app\src-tauri\Cargo.toml
```

Git差分確認:

```powershell
git status --short
git diff --check
git diff
```
