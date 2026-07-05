# AGENTS.md

## Project: AI-LifeOS

AI-LifeOS は、ChatGPT や Codex との会話をローカルPCに保存し、後から検索・要約・活用できる「個人用AI記憶システム」を作るプロジェクトです。

目的は、単なる会話ログ保存ではなく、以下をローカルに蓄積して、将来的に自分専用の第二の脳・AI秘書として使えるようにすることです。

* 会話ログ全文
* 会話ごとの要約
* 日付別の日記
* 長期メモリ
* プロジェクト進捗
* 将来的なベクトル検索
* 将来的なMCP連携
* 将来的な生活改善エージェント

---

## Current Status

現在は Phase2.7 Chat GUI MVP まで実装済みです。

Phase2.6 として、PowerShell上の会話専用MVP、live JSONL保存、raw.md化、既存Phase2.5記憶整理への接続を実装済みです。

Phase2.65 として、会話セッションを保存・再開できる Session Save / Resume MVP を実装済みです。

Phase2.7 として、Phase2.6 と Phase2.65 の会話エンジン・セッション保存処理を Tauri 2 + React + Vite + TypeScript + Tailwind CSS + shadcn/ui のGUIから利用できる Chat GUI MVP として実装済みです。

現在は Phase3.6 まで実装済みです。

Phase3 として、保存済み会話・summary・journal・memory を検索する Markdown Search MVP、タグ/メタデータ抽出、SQLite Memory Index、記憶を読んで回答するための read-only memory context、Vector Search Evaluation、Phase4 Planning Checkpoint を実装済みです。

次は Phase4.0 として、Phase3 の検索・記憶取得基盤を前提に、MCP連携や外部ツール連携の範囲を決めます。

Phase1 は完了済みです。

できていること:

* AI-LifeOS フォルダ作成
* Git管理開始
* conversations / inbox / journal / memory / scripts などの基本構成作成
* inbox/chat.txt に会話を貼る運用
* process_chat.py で raw.md を conversations 配下に保存
* process_chat.py で raw.md と Codex 用タスクを生成する運用
* codex.cmd exec で summary / journal / memory 更新を非対話実行する運用
* 保存から Codex 実行、Git commit までを save_chat.ps1 で実行する運用
* Phase2.6 の会話専用MVPを実装済み
* Phase2.6 の live JSONL から raw.md への変換を実装済み
* Phase2.6 の live会話から既存Phase2.5記憶整理への接続を実装済み
* Phase2.65 のSession Save / Resume MVPを実装済み
* Phase2.7 のChat GUI MVPを実装済み
* Phase2.7 のGUI技術スタックは Tauri 2 + React + Vite + TypeScript + Tailwind CSS + shadcn/ui に決定済み
* Phase3.1 の Markdown Search MVP を実装済み
* Phase3.2 のタグ/メタデータ抽出を実装済み
* Phase3.3 の SQLite Memory Index を実装済み
* Phase3.4 の Memory Retrieval for Answers を実装済み
* Phase3.5 の Vector Search Evaluation を docs/vector_search_evaluation.md に整理済み
* Phase3.6 の Phase4 Planning Checkpoint を docs/phase4_planning_checkpoint.md に整理済み

現在の方針:

* OpenAI API は直接使わない
* .env は使わない
* ChatGPT Plus / Codex CLI 側を使う
* Phase2.5 では自動実行を進めるが、必要に応じて SourceTree や git diff で確認できる状態を保つ
* save_chat.ps1 は保存、Codex実行、Git commit まで自動で行う
* Phase2.6 では会話中に自由なファイル操作をさせず、/exit または Ctrl+C の終了処理で既存スクリプト経由の整理処理を行う
* Phase2.65 では /resume で過去10日以内の会話セッションを再開できるようにし、10日超の削除は明示コマンドに限定する
* Phase2.7 ではPhase2.6の会話エンジンとPhase2.65のセッション保存処理を薄く包むGUIを作り、検索機能や多機能化はPhase3以降に分ける
* Phase2.7 のGUIは Tauri 2 + React + Vite + TypeScript + Tailwind CSS + shadcn/ui で作る
* Phase3 では保存済みMarkdownを読み取り専用で検索し、SQLite index は再生成可能な派生データとして扱う
* Phase3 の会話中memory contextは `memory/long_term.md` と `memory/preferences.md` を優先し、必要に応じて `journal` を検索する
* Phase3 の検索・回答用コンテキスト生成では、`memory` / `journal` / `conversations` を勝手に編集しない
* Phase3 ではベクトルDBを本番導入せず、SQLite検索で足りない理由が明確になった場合に再評価する

---

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

---

## Repository Layout

想定構成:

```text
AI-LifeOS/
├─ conversations/
│  └─ YYYY/
│     └─ MM/
│        └─ YYYY-MM-DD_HHMMSS/
│           ├─ raw.md
│           └─ summary.md
│
├─ inbox/
│  ├─ chat.txt
│  └─ live/
│     ├─ YYYY-MM-DD_HHMMSS.jsonl
│     └─ YYYY-MM-DD_HHMMSS.session.json
│
├─ journal/
│  └─ YYYY/
│     └─ MM/
│        └─ YYYY-MM-DD.md
│
├─ memory/
│  ├─ long_term.md
│  ├─ preferences.md
│  ├─ projects.md
│  └─ search_index.sqlite3  (generated, not tracked)
│
├─ prompts/
│  └─ codex_phase2_prompt.md
│
├─ tasks/
│  └─ latest_codex_task.md
│
├─ scripts/
│  ├─ process_chat.py
│  ├─ save_chat.ps1
│  ├─ codex_conversation.py
│  ├─ chat_gui.py
│  ├─ finalize_live_chat.py
│  ├─ session_store.py
│  ├─ live_session.py
│  ├─ memory_index.py
│  ├─ search_memory.py
│  ├─ index_conversations.py
│  ├─ rebuild_index.py
│  └─ build_answer_context.py
│
├─ docs/
│  ├─ codex_conversation_mvp.md
│  ├─ session_save_mvp.md
│  ├─ chat_gui_mvp.md
│  ├─ searchable_memory.md
│  ├─ vector_search_evaluation.md
│  └─ phase4_planning_checkpoint.md
│
├─ desktop/
│  ├─ README.md
│  ├─ app/
│  └─ backend/
│
├─ logs/
├─ config/
├─ README.md
└─ AGENTS.md
```

---

## Core Workflow

Phase2.5 までの基本フロー:

```text
1. ChatGPTの会話をコピーする
2. inbox/chat.txt に貼る
3. .\scripts\save_chat.ps1 を実行する
4. conversations/YYYY/MM/YYYY-MM-DD_HHMMSS/raw.md が作成される
5. tasks/latest_codex_task.md が作成される
6. codex.cmd exec が latest_codex_task.md の内容を非対話で処理する
7. Codex が summary.md / journal / memory を更新する
8. conversations / journal / memory / inbox / tasks を git add する
9. 変更があれば git commit する
```

Phase2.6 で追加予定の会話専用MVPフロー:

```text
1. python scripts\codex_conversation.py を実行する
2. PowerShell上で継続会話する
3. 会話を inbox/live/YYYY-MM-DD_HHMMSS.jsonl に逐次保存する
4. /exit または Ctrl+C で会話を終了する
5. /exit または Ctrl+C の終了処理で JSONL を conversations/YYYY/MM/YYYY-MM-DD_HHMMSS/raw.md に変換する
6. 終了処理で既存Phase2.5処理を実行し summary.md / journal / memory を更新する
7. 必要に応じて --commit-on-exit または別コマンドで Git commit する
```

Phase2.65 で追加予定のSession Save / Resume MVPフロー:

```text
1. python scripts\session_store.py save を実行する
2. 最新の inbox/live/YYYY-MM-DD_HHMMSS.jsonl に対して .session.json を作る
3. python scripts\session_store.py resume-list で再開可能なセッションを確認する
4. python scripts\codex_conversation.py --resume または /resume <session_id> で過去セッションをロードする
5. 再開対象は最後のuser入力が10日以内のセッションに限定する
6. python scripts\session_store.py prune で10日超の削除候補を確認する
7. 実削除は python scripts\session_store.py prune --delete を明示した場合だけ行う
```

Phase2.7 で追加予定のChat GUI MVPフロー:

```text
1. GUIを起動する
2. ユーザーが入力する
3. Phase2.6の会話処理へ送信する
4. assistant返答をGUIに表示する
5. user/assistant発言を inbox/live/YYYY-MM-DD_HHMMSS.jsonl に逐次保存する
6. 必要に応じて Phase2.65 の保存済みセッションを /resume 相当でロードする
7. ユーザーが「会話を整理して保存」ボタンを押す
8. finalize_live_chat.py を実行する
9. raw.md を生成する
10. 既存Phase2.5処理で summary / journal / memory を生成する
11. 必要に応じて Git commit する
```

Phase3 で追加済みのSearchable Memoryフロー:

```text
1. raw.md / summary.md / journal / memory をローカルMarkdownとして保存する
2. python scripts\index_conversations.py または scripts\rebuild_index.py で memory/search_index.sqlite3 を作る
3. python scripts\search_memory.py で保存済み記憶を読み取り専用検索する
4. タグ検索が必要な場合は summary.md のタグを使う
5. 私的な質問、好み、生活、学習進捗、過去行動、AI-LifeOSの過去方針に関係する質問では build_answer_context.py が memory/long_term.md と memory/preferences.md を優先して読む
6. memoryだけで足りない場合は journal と summary.md / raw.md を検索して短い抜粋を回答用コンテキストに入れる
7. codex_conversation.py は通常の会話返答生成時に read-only memory context をプロンプトへ渡す
8. 会話中に memory / journal / conversations は編集しない
```

---

## Phase2.0: Semi-Automatic Memory Processing

Phase2.0 の目的は、raw.md 保存後に Codex が以下を作ることです。

### 1. summary.md

保存場所:

```text
conversations/YYYY/MM/YYYY-MM-DD_HHMMSS/summary.md
```

内容:

* 話題
* 決まったこと
* 次にやること
* 重要ポイント
* タグ
* 長期メモリ候補

summary.md は AI が後で読むための要約です。人間の日記より詳しくてよいです。

---

### 2. journal/YYYY/MM/YYYY-MM-DD.md

保存場所:

```text
journal/YYYY/MM/YYYY-MM-DD.md
```

内容:

* 150文字程度
* 日付とやったことが分かればよい
* 事実ベース
* 推測や創作は禁止
* 感情を勝手に盛らない

例:

```md
# 2026-06-28

AI-LifeOSのPhase2.0を進めた。Codex CLIでsummary、journal、memoryを半自動更新する方針を整理し、AGENTS.mdで全体方針を管理することにした。
```

---

### 3. memory/long_term.md

保存場所:

```text
memory/long_term.md
```

内容:

* 長期的に役立つ情報だけ追記する
* 一時的な作業ログは入れすぎない
* 重複を避ける
* 既存情報を勝手に削除しない
* 不確かな情報は「候補」として書く

記録対象の例:

* ユーザーは AI-LifeOS を作っている
* ユーザーは Codex CLI を生活改善にも使いたい
* ユーザーはローカル保存、Git管理、SourceTree確認を重視している
* ユーザーは日記を150文字程度の簡潔な形式にしたい
* ユーザーはOpenAI API直叩きより、ChatGPT Plus / Codex CLIを使う運用を好む

---

## Phase2.5: Safer Automation

Phase2.5 では、Phase2.0 の半自動運用をより安全に自動化します。

目的:

* process_chat.py から Codex 用タスクをより正確に生成する
* 最新 raw.md を自動判定する
* Codex が作業しやすいプロンプトを自動出力する
* codex.cmd exec で Codex を非対話実行する
* 対象ファイルを限定して Git commit する

やること:

* tasks/latest_codex_task.md の品質改善
* prompts/codex_phase2_prompt.md の改善
* summary.md のテンプレート統一
* journal の追記ルール統一
* memory 更新ルールの厳格化
* SourceTree確認もできるが、通常運用では save_chat.ps1 で最後まで実行する

自動化時の注意:

* memory/long_term.md は重要な記憶なので、AIの誤追記を防ぐ必要がある
* 会話ログにないことを書かないプロンプトを維持する
* Git commit 対象は conversations / journal / memory / inbox / tasks に限定する
* 不安な場合は python scripts\process_chat.py だけを実行して手動確認に戻せるようにする

---

## Phase2.6: Codex Conversation MVP

Phase2.6 の目的は、OpenAI APIを直接使わず、ChatGPTログイン済みのCodex環境を活用して、AI-LifeOS上で継続会話できる最小構成を作ることです。

codex exec は1回ごとの非対話実行に近く、ChatGPTのような自然な継続会話には向きにくいです。

ただし、Phase2.6 MVPでは依存関係を増やさず、既存のCodex CLIログインを使えるようにするため、`codex.cmd exec` を read-only サンドボックスで呼び、直近会話をプロンプトに含める最小アダプタを採用しています。

Codex SDK または Codex app-server は、将来的に永続スレッドやストリーミング応答が必要になった時の置き換え候補として `docs/codex_conversation_mvp.md` に整理済みです。

Phase2.6 は Phase3 の検索機能へ進む前の実験フェーズとして扱います。

### 基本方針

* ChatGPT公式Webや公式デスクトップアプリをスクレイピングしない
* OpenAI APIを直接叩く構成を前提にしない
* Codex CLI / Codex SDK / Codex app-server の利用を優先する
* 現MVPでは `codex.cmd exec` を read-only サンドボックスで使う
* 会話返答生成は `gpt-5.4-mini` / `model_reasoning_effort="medium"` / `service_tier="fast"` を使う
* summary / journal / memory 更新は `gpt-5.5` / `model_reasoning_effort="xhigh"` を使う
* まずはデスクトップGUIではなく、PowerShellで動くCLIチャットMVPから始める
* 会話は逐次ローカル保存する
* 普段の会話ではファイル操作をさせない
* ファイル操作、要約、日記、メモリ更新は会話終了時の既存スクリプト経由で行う
* Git commit は --commit-on-exit または明示コマンドで行う
* 既存の inbox/chat.txt 運用は壊さない

### 作りたいもの

#### 1. 会話専用CLI

追加候補:

```text
scripts/codex_conversation.py
```

起動コマンド:

```powershell
python scripts\codex_conversation.py
```

想定動作:

```text
You > こんにちは
Assistant > 返答

You > さっきの続きだけど、Phase3は何からやる？
Assistant > 直前の会話を踏まえて返答
```

#### 2. 会話の逐次保存

保存先:

```text
inbox/live/YYYY-MM-DD_HHMMSS.jsonl
```

保存形式:

```json
{"role":"user","timestamp":"2026-06-28T21:30:00+09:00","content":"こんにちは"}
{"role":"assistant","timestamp":"2026-06-28T21:30:05+09:00","content":"返答"}
```

ルール:

* ユーザー発言はCodexへ送る前に保存する
* assistant返答は受信後に保存する
* 1メッセージ1行のJSONL形式にする
* Ctrl+C または /exit で終了できるようにする
* セッション開始時刻でファイル名を決める
* 既存の inbox/chat.txt 運用は壊さない

#### 3. JSONLからraw.mdへの変換

追加候補:

```text
scripts/finalize_live_chat.py
```

目的:

```text
inbox/live/YYYY-MM-DD_HHMMSS.jsonl
↓
conversations/YYYY/MM/YYYY-MM-DD_HHMMSS/raw.md
↓
既存Phase2.5処理
↓
summary.md / journal / memory / git commit
```

要件:

* JSONLをUser/Assistant形式のraw.mdに変換する
* 既存のprocess_chat.pyまたはPhase2.5処理に接続できるようにする
* まずは既存処理を壊さず、別スクリプトとして作る
* 正常処理後も元のJSONLは残す
* 変換後のraw.mdの保存場所は既存conversations構成に合わせる

### ファイル操作方針

Phase2.6の会話中、Codexには原則として自由なファイル操作をさせない。

基本:

* 会話中は読み取り専用に近い扱い
* AGENTS.md / memory/long_term.md / 直近summary.md は必要に応じてコンテキストとして読む
* ファイル更新は /exit または Ctrl+C の終了処理、またはユーザーが明示的に実行する既存スクリプトに任せる
* Git commit も自動会話中には行わない

許可する処理:

* 会話ログJSONLへの追記
* 会話終了時の自動 finalize 処理
* 既存Phase2.5のsummary/journal/memory生成スクリプト呼び出し
* ユーザーが --commit-on-exit または別コマンドで明示した場合のみGit commit

禁止:

* 会話中にmemory/long_term.mdを勝手に編集する
* 会話中に過去ログを勝手に削除・移動する
* 会話中にGit commitを勝手に実行する
* APIキーや秘密情報を保存する
* .env前提に戻す
* OpenAI API直叩き前提に戻す

### Codex SDK / app-server 調査結果

以下を確認済みです。

* 現在の環境では Python SDK `openai_codex` は未インストール
* Codex SDK は将来的に永続スレッド化する候補
* Codex app-server はGUIやストリーミング応答が必要になった時の候補
* 現MVPでは依存関係を増やさず `codex.cmd exec` を利用
* 会話中のCodex実行は read-only サンドボックスをデフォルトにする

調査結果は以下にまとめ済みです。

```text
docs/codex_conversation_mvp.md
```

### 実装順

#### Step 1: 調査ドキュメント作成

作成済み:

```text
docs/codex_conversation_mvp.md
```

内容:

* Codex SDKを使う案
* Codex app-serverを使う案
* codex execを使わない理由
* 採用方針
* 未確定事項
* 実装上の注意

#### Step 2: JSONL保存の土台を作る

作成済み:

```text
scripts/live_session.py
```

役割:

* セッションファイル作成
* user/assistantメッセージ追記
* timestamp付与
* JSONLとして保存

#### Step 3: CLIチャットMVPを作る

作成済み:

```text
scripts/codex_conversation.py
```

要件:

* PowerShellで起動できる
* 1起動 = 1会話セッション
* ユーザー入力を受け付ける
* Codex SDKまたはapp-serverへ送る
* 同じ会話スレッドを維持する
* 返答を表示する
* 逐次JSONL保存する
* /exitで終了する
* /exit または Ctrl+C の終了時に finalize_live_chat.py 相当の処理を行う
* 会話中のファイル更新やGit commitはしない
* Git commitは --commit-on-exit または別コマンドで明示した時だけ行う

#### Step 4: live JSONLをraw.md化する

作成済み:

```text
scripts/finalize_live_chat.py
```

要件:

* inbox/live/*.jsonl を選んでraw.md化する
* 最新ファイルを対象にできる
* conversations/YYYY/MM/YYYY-MM-DD_HHMMSS/raw.md を作る
* 既存Phase2.5処理に渡せる形にする
* codex_conversation.py の終了時にも同じ変換処理を呼べる
* 変換後にsummary/journal/memory生成を行える

#### Step 5: 既存Phase2.5処理と接続

最終的には以下の流れにする。

```text
python scripts\codex_conversation.py
↓
会話する
↓
inbox/live/YYYY-MM-DD_HHMMSS.jsonl に逐次保存
↓
/exit または Ctrl+C
↓
raw.md生成
↓
既存Phase2.5処理
↓
summary.md / journal / memory 更新
↓
必要に応じて --commit-on-exit または別コマンドでGit commit
```

### Phase3との関係

Phase2.6 はPhase3の前段階です。

Phase3では検索機能を実装済みですが、Phase2.6 はその前に「今後の会話を最初から構造化して保存できる入口」を作る段階でした。

Phase2.6によって、将来的に以下がやりやすくなります。

* 会話ログの逐次保存
* セッション単位の管理
* JSONLからMarkdownへの変換
* raw.md / summary.md / journal / memory への統合
* 検索インデックス作成
* 将来的なデスクトップGUI化
* AI-LifeOS専用ChatGPT風アプリ化

---

## Phase2.65: Session Save / Resume MVP

Phase2.65 の目的は、Phase2.6 で作成した live JSONL 会話を、Codex の `/resume` のように後からロードして再開できるようにすることです。

Phase2.7 のGUIに進む前に、CLIでもGUIでも使えるセッション保存・再開の最小層を作ります。

### 基本方針

* 元の `inbox/live/*.jsonl` は削除・移動せず、横にメタデータを置く
* 保存済みセッションは `.session.json` で管理する
* `/resume` で再開できるセッションは、最後のuser入力が10日以内のものに限定する
* 10日を超えたセッションは削除候補にできるが、自動削除はしない
* 実削除は `prune --delete` のような明示コマンドに限定する
* memory / journal / summary は会話再開中に勝手に編集しない
* Git commit はユーザー明示操作または既存スクリプト経由にする

### 作りたいもの

#### 1. セッション保存メタデータ

保存先:

```text
inbox/live/YYYY-MM-DD_HHMMSS.session.json
```

内容:

* session_id
* status
* title
* jsonl_file
* message_count
* started_at
* updated_at
* saved_at

#### 2. セッション保存・一覧スクリプト

追加候補:

```text
scripts/session_store.py
```

想定コマンド:

```powershell
python scripts\session_store.py save
python scripts\session_store.py save --file inbox\live\YYYY-MM-DD_HHMMSS.jsonl --title "タイトル"
python scripts\session_store.py list
python scripts\session_store.py resume-list
python scripts\session_store.py prune
python scripts\session_store.py prune --delete
```

#### 3. CLI会話の /resume

想定コマンド:

```powershell
python scripts\codex_conversation.py --resume
```

会話中コマンド:

```text
/resume
/cancel
/resume latest
/resume YYYY-MM-DD_HHMMSS
```

`/resume` だけの場合は、PowerShellの対話端末では再開可能なセッション一覧をカーソル選択UIで表示します。`Up/Down` で移動し、`Enter` でロードします。パイプ入力などカーソル選択できない環境では番号入力に戻します。

`/resume latest` または `--resume` は、最後のuser入力が10日以内の最新セッションをロードします。

### 保持ルール

* 判断基準は「最後のassistant返答」ではなく「最後のuser入力」にする
* デフォルト保持期間は10日
* 10日以内のセッションだけを再開候補に表示する
* 10日を超えたセッションは `prune` で削除候補として表示する
* `prune` はデフォルトでdry-runにし、実削除しない
* 実削除する場合は `prune --delete` を明示する
* 削除対象は `inbox/live/*.jsonl` と同名の `.session.json` のみに限定する

### Phase2.7との関係

Phase2.7 のGUIは、Phase2.65 のセッション保存・再開処理を薄く呼び出します。

GUIで最初からやること:

* 現在のセッションファイル表示
* セッション保存
* 10日以内のセッション一覧
* セッション再開

GUIでまだやらないこと:

* 過去ログ全文検索
* ベクトル検索
* 複雑な履歴管理
* 自動削除
* 自動Git commit

---

## Phase2.7: Chat GUI MVP

Phase2.7 の目的は、Phase2.6 で作成する Codex Conversation MVP と Phase2.65 の Session Save / Resume MVP を GUI から利用できるようにすることです。

Phase2.6 では、PowerShell上で継続会話できるCLIチャットを作り、会話を inbox/live/*.jsonl に逐次保存し、finalize_live_chat.py で raw.md 化する想定です。

Phase2.7 では、その会話エンジン、保存処理、再開処理を流用し、ChatGPT風の最小GUIを作ります。

Phase2.7 は Phase3 の検索機能とは分離し、今後の会話をGUIから自然に保存できる入口を作る段階です。

### 基本方針

* Phase2.6 の会話エンジンを再利用する
* Phase2.65 のセッション保存・再開処理を再利用する
* GUI技術スタックは Tauri 2 + React + Vite + TypeScript + Tailwind CSS + shadcn/ui を採用する
* GUIは既存処理の薄いラッパーにする
* いきなり多機能化しない
* ChatGPT公式Webや公式デスクトップアプリをスクレイピングしない
* OpenAI API直叩き前提にしない
* Codex SDK または app-server を使った会話処理を前提にする
* 会話ログは引き続き inbox/live/*.jsonl に逐次保存する
* 保存済みセッションは Phase2.65 の `.session.json` で扱う
* 10日以内のセッションをGUIから再開できるようにする
* 会話終了後に finalize_live_chat.py で raw.md 化できるようにする
* summary / journal / memory / Git commit は既存Phase2.5処理に接続する
* memory/long_term.md を会話中に勝手に編集しない
* Git commit はユーザー明示操作または既存スクリプト経由にする
* Phase3の検索機能とは分離する

### 決定事項

* Phase2.7 のGUI技術スタックは Tauri 2 + React + Vite + TypeScript + Tailwind CSS + shadcn/ui にする
* Tauri 側は既存Pythonスクリプトを安全に呼ぶ薄いラッパーとして扱う
* React/Vite/shadcn/ui でChatGPT風のチャット表示、入力欄、セッション一覧、保存ボタンなどのUIを作る
* 既存の Phase2.6 / Phase2.65 の保存・再開・finalize 処理を壊さず再利用する

### 作りたいもの

#### 1. ChatGPT風の最小GUI

候補ディレクトリ:

```text
desktop/
├─ README.md
├─ app/
└─ backend/
```

GUIに必要な最小要素:

* チャット表示欄
* 入力欄
* 送信ボタン
* セッション再開ボタン
* 会話終了ボタン
* 会話を整理して保存ボタン
* 現在のセッションファイル表示
* エラー表示欄

### 想定フロー

```text
GUIを起動
↓
ユーザーが入力
↓
Phase2.6の会話処理へ送信
↓
assistant返答をGUIに表示
↓
user/assistant発言を inbox/live/YYYY-MM-DD_HHMMSS.jsonl に逐次保存
↓
必要に応じて Phase2.65 の /resume 相当で過去10日以内のセッションをロード
↓
ユーザーが「会話を整理して保存」ボタンを押す
↓
finalize_live_chat.py を実行
↓
raw.md生成
↓
既存Phase2.5処理で summary / journal / memory 生成
↓
Git commit
```

### 採用技術スタック

Phase2.7 では以下を採用する。

```text
Tauri 2
+ React
+ Vite
+ TypeScript
+ Tailwind CSS
+ shadcn/ui
+ 既存Pythonスクリプト呼び出し
```

採用理由:

* 見た目のよいChatGPT風UIを作りやすい
* Electronより軽量なデスクトップアプリにしやすい
* React / Vite / Tailwind CSS / shadcn/ui はAIが実装支援しやすい
* 既存のPython処理をTauri側から呼ぶ薄い構成にできる
* Phase3以降の検索UIやメモリ閲覧UIへ拡張しやすい

### 推奨実装順

#### Step 1: GUI方式の整理

作成候補:

```text
docs/chat_gui_mvp.md
```

内容:

* Tauri 2 + React + Vite + TypeScript + Tailwind CSS + shadcn/ui の採用理由
* 初期MVPの範囲
* 既存Pythonスクリプトとの接続方針
* 未確定事項

#### Step 2: GUIなしでも使える会話エンジンを確認

Phase2.7は、Phase2.6の以下が動いていることを前提にする。

```text
scripts/codex_conversation.py
scripts/live_session.py
scripts/finalize_live_chat.py
scripts/session_store.py
```

未実装の場合はPhase2.7で直接実装せず、Phase2.6またはPhase2.65の依存として明記する。

#### Step 3: 最小GUIを作る

候補:

```text
scripts/chat_gui.py
```

または

```text
desktop/
└─ README.md
```

最初のGUIは以下だけでよい。

* 入力する
* 送信する
* 返答を表示する
* JSONLに保存される
* 10日以内のセッションを再開できる
* 終了できる
* finalize処理を呼び出せる

#### Step 4: 既存処理と接続する

GUIの「会話を整理して保存」ボタンは、最終的に以下の処理に接続する。

```text
inbox/live/*.jsonl
↓
scripts/finalize_live_chat.py
↓
raw.md
↓
既存Phase2.5処理
↓
summary.md / journal / memory
↓
Git commit
```

初期MVPでは、ボタン押下時にコマンドを表示するだけでもよいです。いきなり破壊的な自動実行をしないでください。

### GUIでやらないこと

Phase2.7では以下をやらない。

* 過去ログ検索UI
* ベクトルDB検索
* MCP連携
* Gmail / Discord / Calendar連携
* 本格的な設定画面
* 複数会話管理
* 10日超セッションの自動削除
* ユーザー認証
* クラウド同期
* ChatGPT公式WebのDOM取得
* memory/long_term.md の会話中自動編集
* 勝手なGit commit連発

これらはPhase3以降または別フェーズで扱う。

### Phase3との関係

Phase2.7 は Phase3 の前段階です。

Phase3では保存済み会話の検索機能を実装済みです。Phase2.7 は、その前段として今後の会話をGUIから自然に保存できる入口を作る段階でした。

Phase2.7によって、将来的に以下がやりやすくなります。

* 自作ChatGPT風アプリ化
* 会話の逐次保存
* セッション単位の管理
* JSONLからraw.mdへの変換
* summary / journal / memory への統合
* Phase3の検索インデックス作成
* 将来的なデスクトップアプリ化

---

## Phase3: Searchable Memory

Phase3 の目的は、保存済みの会話ログを検索できるようにすることです。

Phase3 は一気にDBやベクトル検索へ進めず、以下の小フェーズに分けます。

```text
Phase3.0 : Searchable Memory Design
Phase3.1 : Markdown Search MVP
Phase3.2 : Tags and Metadata
Phase3.3 : SQLite Memory Index
Phase3.4 : Memory Retrieval for Answers
Phase3.5 : Vector Search Evaluation
Phase3.6 : Phase4 Planning Checkpoint
```

候補:

* SQLite全文検索
* ripgrep検索
* LanceDB
* Chroma
* Qdrant
* SQLiteVec

最初はベクトルDBに飛びつかず、以下の順番で進めます。

```text
1. 検索対象とメタデータ設計
2. Markdown / ripgrep 検索
3. タグ検索
4. SQLite管理
5. 回答用コンテキスト抽出
6. ベクトル検索の評価
7. Phase4追加機能の認識合わせ
```

Phase3で作るもの:

```text
scripts/
├─ memory_index.py
├─ index_conversations.py
├─ search_memory.py
├─ rebuild_index.py
└─ build_answer_context.py
```

Phase3実装済みドキュメント:

```text
docs/searchable_memory.md
docs/vector_search_evaluation.md
docs/phase4_planning_checkpoint.md
```

Phase3実装済み仕様:

* `scripts/memory_index.py` は検索対象Markdownの収集、タグ/日付/タイトル抽出、SQLite index作成、検索結果ランキングを提供する共通モジュールです。
* `scripts/search_memory.py` は手動検索CLIです。`--no-index` でMarkdown直接検索、`--rebuild-index` で検索前index再構築、`--type` と `--tag` で絞り込みできます。
* `scripts/index_conversations.py` と `scripts/rebuild_index.py` は `memory/search_index.sqlite3` を再構築します。
* `scripts/build_answer_context.py` は私的な質問、好み、生活、学習進捗、過去行動、AI-LifeOSの過去方針に関係する質問だけ、read-only memory context を生成します。
* `scripts/codex_conversation.py` は通常の会話返答生成時に `build_answer_context.py` の結果をプロンプトへ入れます。`--no-memory-context` で無効化できます。
* `scripts/process_chat.py --run-codex` と `scripts/finalize_live_chat.py --run-codex` は、summary / journal / memory 更新後に SQLite index を再構築します。
* SQLite index は `memory/search_index.sqlite3` に作ります。これは再生成可能な派生データで、Git管理しません。
* SQLiteには全文を保存しますが、日本語の部分一致を安定させるため、MVPのランキングはPython側で行います。FTS5が使える環境では `documents_fts` も作成します。
* 現時点のローカル実装自体はWeb検索クライアントを持ちません。現在性や外部情報が必要な質問では、会話プロンプト上でWeb検索が必要な補助手段であることを明示します。

### Phase3.0: Searchable Memory Design

目的:

* 何を検索対象にするかを決める
* raw.md / summary.md / journal / memory の役割を分ける
* 検索結果として返す項目を決める
* DB化する前に、必要なメタデータを整理する

対象候補:

* conversations/YYYY/MM/YYYY-MM-DD_HHMMSS/raw.md
* conversations/YYYY/MM/YYYY-MM-DD_HHMMSS/summary.md
* journal/YYYY/MM/YYYY-MM-DD.md
* memory/long_term.md
* memory/preferences.md
* memory/projects.md

この段階ではDBを作らず、設計と読み取り方針を決めます。

Phase3.0 ヒアリング結果として、以下を採用方針にします。

* 検索の入口は独立した検索画面ではなく、会話中に必要な場合に検索する形を最終形にする
* ただし実装は再利用可能な検索エンジンとして作り、CLIとGUIの両方から呼べるようにする
* 私的な質問、好み、生活、学習進捗、過去行動に関係する質問では、まず `memory/long_term.md` と `memory/preferences.md` を確認する
* memory内の情報で十分ならそれを元に回答する
* memoryだけで足りない場合は、SQLite index を使って `journal` を日付単位で検索し、必要な情報を回答に使う
* journal検索は基本的に全期間を対象にする。ただし、直近傾向を答える方が自然な質問では対象期間を調整する
* 現在性や外部情報が必要な質問では、ユーザーへ毎回確認せずWeb検索してよい
* Web検索語に含める情報には原則として制限を設けない
* 記憶を読んで回答した場合、通常は出典を自然文に混ぜる。詳細確認を求められた場合だけ日付やファイルパスを明示する
* 検索結果はファイルパスだけではなく、AIが「過去にこう話していた」と日付付きで要約して返せる形を目指す
* 優先用途は、ユーザーの好み・方針、日記・日別行動、雑談やアイデア、知識・学習進捗の把握とする
* SQLite index は再生成可能な派生データとして扱い、Git管理しない
* SQLite index はセットアップ時または初回起動時に生成する方針にする
* `memory/preferences.md` は Phase3.0 で役割を決め、空ファイルを作成する
* `memory/preferences.md` にはユーザーの好み、判断基準、回答スタイル、生活・学習・開発上の嗜好を入れる
* `memory/long_term.md` は長期的に重要な事実・方針全般、`memory/preferences.md` は好み・判断基準、`memory/projects.md` はプロジェクト進捗に分ける
* 記憶を読んで回答する機能は、検索認識合わせとSQLite index整備後の Phase3.4 で本実装する
* Phase3.0後は、検索機能の認識合わせを優先する
* Phase4.0での追加機能は、Phase3.6であらためて議論する

### Phase3.1: Markdown Search MVP

目的:

* まずはDBなしで保存済みMarkdownを検索できるようにする
* ripgrep または Python の全文検索で、過去会話を探せる最小CLIを作る
* 将来の会話中検索に流用できる検索エンジンの入出力を固める

実装候補:

```text
scripts/search_memory.py
```

要件:

* raw.md / summary.md / journal / memory を読み取り専用で検索する
* 検索語に一致したファイルパス、見出し、前後の短い抜粋を表示する
* 初期実装は手動CLIでよいが、最終的な入口は会話エンジンからの自動検索を想定する
* 検索だけを行い、memory / journal / conversations を更新しない
* 個人情報を外部サービスへ送らない

実装済み:

```powershell
python scripts\search_memory.py "検索語"
python scripts\search_memory.py "検索語" --no-index
python scripts\search_memory.py "検索語" --json
```

### Phase3.2: Tags and Metadata

目的:

* 会話単位で探しやすくするため、タグや日付などのメタデータを扱えるようにする
* summary.md のタグ欄を検索に使える形へ寄せる
* `memory/preferences.md` への好み・判断基準の抽出、更新ルールを実装する

扱うメタデータ候補:

* conversation_id
* date
* title
* tags
* source_path
* summary_path
* raw_path
* journal_path
* memory_source

この段階では、メタデータ抽出とタグ検索を優先し、まだ高度なDB設計に踏み込みすぎない。

`preferences.md` の更新ルール:

* 会話ログに明示された好み・判断基準だけを候補にする
* 一時的な気分や単発の作業ログは入れない
* `long_term.md` と重複する場合は、事実・方針は long_term、好み・選好は preferences に分ける
* 既存内容を勝手に削除・大幅改変しない
* 自動更新前に、summary の「長期メモリ候補」相当で確認できる形にする

実装済み:

* `memory/preferences.md` は `memory/long_term.md`、`memory/projects.md` と同じく記憶ファイルとして自動作成対象です。
* `prompts/codex_phase2_prompt.md` は、明示された好み・判断基準・回答スタイル・生活/学習/開発上の嗜好だけを `memory/preferences.md` へ追記するよう更新済みです。
* `summary.md` の `## タグ` / `## Tags` / `## Tag` からタグを抽出し、検索とSQLite indexに使います。

### Phase3.3: SQLite Memory Index

目的:

* 記憶を取り出しやすくするため、保存済み会話・要約・タグをSQLiteで管理する
* Markdown検索より速く、構造化された検索をできるようにする

実装候補:

```text
scripts/index_conversations.py
scripts/rebuild_index.py
```

DB保存先候補:

```text
memory/search_index.sqlite3
```

要件:

* DBは既存Markdownから再生成できる派生データとして扱う
* conversations / journal / memory の元ファイルを勝手に書き換えない
* conversation、document、tag のように検索しやすい単位で保存する
* FTS5 が使える場合は全文検索テーブルを使う
* rebuild_index.py でゼロから再構築できるようにする
* DBファイルに秘密情報を追加で生成しない。元ファイルにある情報の索引化に限定する
* DBファイルはGit管理しない
* セットアップ時または初回起動時にDBを生成できるようにする
* `journal` は日付単位で検索・取得しやすい形でindexする
* `memory/preferences.md` も memory document としてindex対象に含める

実装済み:

```powershell
python scripts\index_conversations.py
python scripts\rebuild_index.py
python scripts\search_memory.py "検索語" --rebuild-index
```

### Phase3.4: Memory Retrieval for Answers

目的:

* AI-LifeOSの記憶を読んで回答できるようにする
* ユーザー質問に関連する memory / summary / journal / raw.md の抜粋を取得し、会話プロンプトへ読み取り専用コンテキストとして渡す

実装候補:

```text
scripts/search_memory.py
scripts/build_answer_context.py
```

要件:

* 回答時は memory優先、journal検索補助、必要に応じたWeb検索の順で扱う
* ユーザーの好み・判断基準が関係する質問では `memory/preferences.md` を優先的に参照する
* 私的な質問、生活、学習進捗、過去行動に関係する質問では `memory/long_term.md` と `memory/preferences.md` を読む
* 一般質問や明らかに記憶不要な質問では、毎回memoryを読まない
* memory内の情報で十分回答できる場合は、DB検索を必須にしない
* memoryだけでは不足する場合に SQLite index から journal を日付単位で検索する
* journal検索は基本的に全期間を対象にし、直近傾向が重要な場合だけ期間を調整する
* 検索結果をそのまま大量投入せず、短い抜粋と出典パスに絞る
* 出典ファイルパスを保持し、後で確認できるようにする
* 回答には必要に応じて日付を含める
* GUIやCLIの会話中に memory / journal を勝手に編集しない
* 回答に使った記憶が不確かな場合は、推測ではなく「見つかった範囲では」と扱う
* まずはキーワード検索とSQLite検索の結果を使い、ベクトル検索は必須にしない
* 外部Web検索は、ローカル記憶だけでは不足し、かつ現在性や外部情報が必要な場合の補助手段として扱う
* 現在性が必要な質問では、ユーザーへ毎回確認せずWeb検索してよい
* Web検索語に含める情報には原則として制限を設けない
* 記憶を読んだ回答の出典は、通常は自然文に混ぜる。ユーザーが詳細確認を求めた場合だけ、日付やファイルパスを明示する

実装済み:

```powershell
python scripts\build_answer_context.py "俺の好みに合う店は？"
python scripts\codex_conversation.py --no-memory-context
```

`build_answer_context.py` は、一般質問ではmemory contextを作らず、私的な質問・好み・生活・学習進捗・過去行動・AI-LifeOSの過去方針に関係する質問だけで `memory/long_term.md` と `memory/preferences.md` を読みます。必要に応じて `journal` と `summary.md` / `raw.md` を検索し、短い抜粋と出典を保持します。

### Phase3.5: Vector Search Evaluation

目的:

* キーワード検索やSQLite検索だけでは弱い場合に、ベクトル検索を評価する
* いきなり本番依存にせず、ローカルで安全に扱える候補を比較する

候補:

* SQLiteVec
* LanceDB
* Chroma
* Qdrant

要件:

* OpenAI API直叩きを前提にしない
* ローカル運用、再構築可能性、バックアップ容易性を優先する
* ベクトルDB導入前に、SQLite全文検索で足りない理由を明確にする
* 個人情報を外部サービスへ送らない

実装済み:

* `docs/vector_search_evaluation.md` に SQLiteVec / LanceDB / Chroma / Qdrant の評価を整理済みです。
* 現時点ではベクトルDBを本番導入しません。Markdown検索 + SQLite index + 日本語部分一致で不足が明確になった場合に再評価します。
* 最有力候補はSQLite単一ファイル運用と相性がよいSQLiteVecですが、Windows導入性とローカルembedding方針が決まるまでは保留します。

### Phase3.6: Phase4 Planning Checkpoint

目的:

* Phase4.0で追加したいMCP連携や外部ツール連携を、Phase3完了前に認識合わせする
* Phase3の検索・記憶取得と、Phase4の外部連携の境界を決める

扱うこと:

* Phase4で優先する連携候補
* 外部Web検索とMCP連携の扱い
* 個人情報を含む検索・連携の安全ルール
* Phase3.4の記憶回答機能からPhase4へ渡すべき設計課題
* 現在性が必要な質問での自動Web検索を、MCP連携や外部ツール連携とどう統合するか

実装済み:

* `docs/phase4_planning_checkpoint.md` に Phase4 優先候補と安全ルールを整理済みです。
* Phase4では、まず Filesystem MCP / GitHub MCP / Playwright MCP を優先候補にします。
* Gmail / Calendar / Discord など個人情報が多い連携は、読み取り範囲と保存前確認を設計してから扱います。

検索したい例:

* 「前にCodexの許可設定について話した内容」
* 「AI-LifeOSのPhase2を決めた会話」
* 「Unityのリアル映像について話した内容」
* 「投資方針について話した過去ログ」

---

## Phase4: MCP Integration

Phase4 の目的は、AI-LifeOSを外部ツールと連携させることです。

候補:

* GitHub MCP
* Filesystem MCP
* Google Calendar MCP
* Gmail MCP
* Discord MCP
* Obsidian MCP
* Playwright MCP
* Firecrawl MCP
* Context7 MCP

やりたいこと:

* GitHub IssueやPRの進捗を記憶
* カレンダー予定を日記に反映
* GmailやDiscordから重要情報を抽出
* Obsidianと連携
* Web調査結果をプロジェクト記憶に保存

注意:

* いきなり全部つなげない
* まずはFilesystemとGitHubから始める
* 個人情報が多いものは慎重に扱う
* 自動保存前に確認ステップを入れる

---

## Phase5: Life Improvement Agent

Phase5 の目的は、AI-LifeOSを生活改善に使うことです。

やりたいこと:

* 朝のブリーフィング
* 夜の振り返り
* ゲーム練習ログ
* 投資メモ
* お出かけ候補
* 学習ログ
* 開発進捗整理
* 体調や睡眠の軽い記録
* TODO抽出
* 次にやること提案

ただし、最初は過剰に自動化しないこと。

AI-LifeOSの基本思想:

```text
保存する
↓
整理する
↓
検索する
↓
思い出す
↓
提案する
↓
生活を改善する
```

---

## Phase6: Daily Automation

Phase6 の目的は、毎日自動で記憶整理を走らせることです。

候補:

* Windows タスクスケジューラ
* PowerShellスクリプト
* Codex CLI
* Git自動コミット
* 定期レポート生成

想定フロー:

```text
毎日夜
↓
その日の会話・ログを収集
↓
summary生成
↓
journal更新
↓
memory更新候補作成
↓
差分確認
↓
commit
```

完全自動にする前に、必ず確認用モードを作ること。

---

## Important Rules for Codex

Codexは以下のルールを守ること。

### Do

* 既存ファイルを壊さない
* 変更前に構成を確認する
* raw.md の内容に基づいて書く
* 会話ログにないことは書かない
* memory は長期的に重要な情報だけ扱う
* journal は150文字程度にする
* summary は後でAIが読んで分かるように書く
* 変更後はどのファイルを変更したか報告する
* 可能なら差分確認しやすい粒度で変更する
* commit または push の前に、必ず差分へ個人情報・秘密情報が含まれていないか確認する
* commit する場合は、コミットコメントに日本語で修正内容を必ず記載する
* commit の前に、原則として `python scripts\privacy_check.py --staged` を実行する
* push の前に未pushコミットがある場合は、原則として `python scripts\privacy_check.py --range origin/main..HEAD` を実行する
* privacy check が失敗した場合は commit / push を中止し、検出箇所をユーザーへ報告する
* Phase2.6 の会話専用MVPでは、会話ログJSONLへの追記以外のファイル操作をユーザー明示操作に限定する
* Phase2.6 の調査結果は docs/codex_conversation_mvp.md に整理する
* Phase2.65 のSession Save / Resume MVPでは、/resume対象を最後のuser入力から10日以内に限定する
* Phase2.65 の調査・設計結果は docs/session_save_mvp.md に整理する
* Phase2.65 の10日超セッション削除は、dry-run確認後の明示コマンドに限定する
* Phase2.7 のChat GUI MVPはPhase2.6の会話エンジンを再利用する薄いラッパーとして設計する
* Phase2.7 の調査結果は docs/chat_gui_mvp.md に整理する
* Phase2.7 のGUIは会話ログを inbox/live/*.jsonl に逐次保存する方針を維持する
* Phase3 の検索・index・memory context仕様は docs/searchable_memory.md に整理する
* Phase3 の SQLite index は `memory/search_index.sqlite3` に作り、Markdownから再生成可能な派生データとして扱う
* Phase3 の会話中memory contextは読み取り専用にし、`memory` / `journal` / `conversations` を勝手に編集しない
* Phase3 のベクトル検索評価は docs/vector_search_evaluation.md に整理し、導入前にSQLite検索で足りない理由を明確にする
* Phase3.6 のPhase4引き継ぎは docs/phase4_planning_checkpoint.md に整理する

### Do Not

* 会話ログにない設定を勝手に作らない
* APIキーや秘密情報をファイルに書かない
* .env を前提にしない
* OpenAI API直叩きを前提にしない
* memory/long_term.md を勝手に大幅改変しない
* 過去ログを勝手に削除しない
* journal に感情や体調を勝手に推測して書かない
* Git commit を勝手に連発しない
* 個人情報・秘密情報チェックを省略して commit / push しない
* privacy check が失敗した状態で commit / push しない
* 破壊的変更をしない
* Phase2.6 の会話中に memory/long_term.md を勝手に編集しない
* Phase2.6 の会話中に過去ログを勝手に削除・移動しない
* ChatGPT公式Webや公式デスクトップアプリをスクレイピングしない
* Phase2.65 で10日超セッションを自動削除しない
* Phase2.65 の /resume で memory/long_term.md や journal を勝手に編集しない
* Phase2.7 のGUI中に memory/long_term.md や journal を勝手に編集しない
* Phase2.7 で過去ログ検索UI、ベクトルDB検索、MCP連携を先取りしない
* Phase2.7 で勝手なGit commitを連発しない
* Phase3 の検索処理で元Markdownを勝手に書き換えない
* `memory/search_index.sqlite3` をGit管理対象として扱わない
* ベクトルDBを必要性の説明なしに本番依存へ追加しない

---

## Preferred Development Style

このプロジェクトでは、いきなり大きな機能を作らず、小さく動く単位で進めます。

優先順位:

```text
1. データを壊さない
2. 手動で確認できる
3. Git差分が読みやすい
4. 後から拡張できる
5. 自動化する
```

コードを書くときは、Windows PowerShellで動くことを優先します。

---

## Common Commands

保存、Codex実行、Git commit:

```powershell
.\scripts\save_chat.ps1
```

raw.md と Codex 用タスクだけ保存:

```powershell
python scripts\process_chat.py
```

Pythonだけで最後まで実行:

```powershell
python scripts\process_chat.py --run-codex --commit
```

commit / push 前の個人情報・秘密情報チェック:

```powershell
python scripts\privacy_check.py --staged
python scripts\privacy_check.py --range origin/main..HEAD
```

Phase2.6 調査ドキュメント作成後の想定CLIチャット:

```powershell
python scripts\codex_conversation.py
```

Phase2.6 live JSONLをraw.md化:

```powershell
python scripts\finalize_live_chat.py
```

Phase2.65 最新live JSONLを保存済みセッション化:

```powershell
python scripts\session_store.py save
```

Phase2.65 再開できるセッション一覧:

```powershell
python scripts\session_store.py resume-list
```

Phase2.65 最新セッションを再開:

```powershell
python scripts\codex_conversation.py --resume
```

Phase2.65 10日超セッションの削除候補確認:

```powershell
python scripts\session_store.py prune
```

Phase2.7 Chat GUI MVP 開発起動:

```powershell
cd desktop\app
npm install
npm run tauri dev
```

Phase2.7 配布用ビルド:

```powershell
cd desktop\app
npm run bundle
```

Phase3 保存済み記憶検索:

```powershell
python scripts\search_memory.py "検索語"
python scripts\search_memory.py "検索語" --type journal
python scripts\search_memory.py "" --tag Phase3
```

Phase3 SQLite index 再構築:

```powershell
python scripts\index_conversations.py
python scripts\rebuild_index.py
```

Phase3 回答用memory context確認:

```powershell
python scripts\build_answer_context.py "俺の好みに合う店は？"
```

Git状態確認:

```powershell
git status
```

差分確認:

```powershell
git diff
```

ステージング:

```powershell
git add .
```

コミット:

```powershell
git commit -m "YYYY-MM-DDの会話保存処理を更新"
```

コミットコメントには、修正内容が分かる日本語の説明を必ず記載する。

ブランチ名を main に変更する場合:

```powershell
git branch -m main
```

---

## Definition of Done

Phase2.0 の完了条件:

* inbox/chat.txt から raw.md を保存できる
* tasks/latest_codex_task.md が作られる
* Codex が summary.md を作成できる
* Codex が journal/YYYY/MM/YYYY-MM-DD.md を150文字程度で更新できる
* Codex が memory/long_term.md に長期メモリ候補を追記できる
* SourceTreeで差分確認できる
* 問題なければGit commitできる

Phase2.5 の完了条件:

* save_chat.ps1 で raw.md 保存、Codex実行、summary/journal/memory更新、Git commit まで実行できる
* process_chat.py が --run-codex と --commit を扱える
* Codex失敗時にGit commitしない
* Git commit対象が conversations / journal / memory / inbox / tasks に限定されている
* python -m unittest が通る

Phase2.6 の完了条件:

* Codex SDK / app-server の調査結果と現MVPの採用方針が docs/codex_conversation_mvp.md に整理されている
* python scripts\codex_conversation.py で継続会話できる
* 会話が inbox/live/*.jsonl に逐次保存される
* ユーザー発言とassistant返答が role / timestamp / content 付きで保存される
* 会話中にmemoryやjournalを勝手に編集しない
* /exit または Ctrl+C で安全に終了できる
* finalize_live_chat.py でJSONLをraw.mdへ変換できる
* 既存Phase2.5処理と接続できる
* SourceTreeで差分確認できる
* Git commitできる

Phase2.65 の完了条件:

* AGENTS.mdにPhase2.65が追加されている
* docs/session_save_mvp.md にセッション保存・再開方針が整理されている
* python scripts\session_store.py save で最新live JSONLの `.session.json` を作れる
* python scripts\session_store.py list で保存済みセッションを確認できる
* python scripts\session_store.py resume-list で最後のuser入力が10日以内のセッションを確認できる
* python scripts\codex_conversation.py --resume で最新の再開可能セッションをロードできる
* 会話中に /resume でカーソル選択式の候補一覧を表示できる
* カーソル選択できない環境では /resume 後の番号入力でセッションをロードできる
* 会話中に /resume <session_id> で特定セッションをロードできる
* python scripts\session_store.py prune で10日超セッションの削除候補を確認できる
* 10日超セッションの実削除は prune --delete を明示した場合だけ行う
* 通常の保存や再開では memory / journal / summary / Git commit を勝手に実行しない
* python -m unittest が通る

Phase2.7 の完了条件:

* AGENTS.mdにPhase2.7が追加されている
* docs/chat_gui_mvp.md にGUI方針が整理されている
* GUI技術スタックが Tauri 2 + React + Vite + TypeScript + Tailwind CSS + shadcn/ui に決まっている
* 最小GUIの実装方針が決まっている
* Tauri GUIから既存Python処理を呼ぶブリッジが実装されている
* Phase2.6の会話処理との接続方針が明記されている
* Phase2.65のセッション保存・再開処理との接続方針が明記されている
* inbox/live/*.jsonl への逐次保存方針が維持されている
* 10日以内セッションの再開方針が明記されている
* finalize_live_chat.py との接続方針が明記されている
* GUI中にmemoryやjournalを勝手に編集しないルールが明記されている
* Phase3の検索機能とは分離されている
* SourceTreeで差分確認できる粒度で変更されている

Phase3 の完了条件:

* Phase3.0: 検索対象、メタデータ、DB化方針が整理されている
* Phase3.1: 過去の raw.md / summary.md / journal / memory をMarkdown検索できる
* Phase3.2: タグまたはメタデータで探せる
* Phase3.3: SQLite index を作成・再構築できる
* Phase3.4: 検索結果を回答用コンテキストとして安全に渡せる
* Phase3.5: ベクトル検索の必要性と候補が評価されている
* Phase3.6: Phase4.0の追加機能と外部連携方針が整理されている

最終形の完了条件:

* 会話を保存できる
* 要約できる
* 日記化できる
* 長期メモリ化できる
* 検索できる
* 必要な過去情報をAIに渡せる
* 生活改善の提案に使える
* ローカルPC上で安全に管理できる
