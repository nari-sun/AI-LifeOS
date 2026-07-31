# Phase2.7 Chat GUI MVP

Phase2.7 は、Phase2.6 の live conversation workflow と Phase2.65 の session save / resume workflow を GUI から使えるようにする段階です。

## 決定事項

採用技術スタック:

```text
Tauri 2
+ React
+ Vite
+ TypeScript
+ Tailwind CSS
+ shadcn/ui
+ 既存Pythonスクリプト呼び出し
```

## 採用理由

* ChatGPT風のチャットUIを作りやすい
* Electronより軽量なデスクトップアプリにしやすい
* React / Vite / Tailwind CSS / shadcn/ui はAIによる実装支援と修正がしやすい
* 既存のPythonスクリプトを壊さず、Tauri側から薄く呼び出せる
* Phase3以降の検索UI、メモリ閲覧UI、設定UIへ拡張しやすい

## MVP範囲

最初のGUIで扱うこと:

* 新規live session作成
* user / assistant発言の表示
* user発言をCodex呼び出し前に `inbox/live/*.jsonl` へ保存
* assistant返答を受信後に `inbox/live/*.jsonl` へ保存
* assistant返答生成は `gpt-5.6-luna` / `model_reasoning_effort="medium"` を使い、service tierは指定せず `features.fast_mode=false` とする
* 経過日数に関係しない再開可能セッション一覧表示
* セッション再開
* セッションメタデータ保存
* 「会話を整理して保存」ボタンから `finalize_live_chat.py` 相当の処理をバックグラウンドジョブとして実行
* 送信直後のuser発言をUI一時状態として表示
* `.txt` / `.md` / `.pdf` / `.xlsx` 添付MVP
* ローカル個人データの読み取り専用管理画面
* 管理 > データ整理から、未整理・整理失敗の再開可能セッションを古い順に逐次整理
* 管理 > ChatGPTインポートから、エクスポートZIP、展開済みフォルダ、または `conversations.json` をdry-run確認して選択取り込み
* エラー表示

最初のGUIで扱わないこと:

* 過去ログ全文検索
* ベクトル検索
* 汎用MCP連携（Phase4.0では公式Notion MCPの検証済み読み取りtoolだけを追加）
* モデル・応答設定UI
* ChatGPT風のメッセージ編集、回答再生成、会話分岐
* 会話中の memory / journal 自動編集
* 自動Git commit

## 接続方針

GUIは既存処理の置き換えではなく、薄いラッパーとして扱います。

```text
desktop/app
↓
Tauri command
↓
scripts/chat_gui_bridge.py
↓
scripts/live_session.py
scripts/codex_conversation.py
scripts/session_store.py
scripts/finalize_live_chat.py
```

TauriのRust側は、GUIから呼ばれた操作をPythonブリッジへ渡します。Pythonブリッジは既存のPhase2.6 / 2.65スクリプトを直接importして使います。

この構成により、CLIとGUIで保存形式と再開ルールを共有できます。

## ディレクトリ

```text
desktop/
├─ README.md
└─ app/
   ├─ src/
   └─ src-tauri/
```

## 起動

Node.js は 22 LTS 以上を使います。`.nvmrc` は 22.23.1 に固定しています。

開発時:

```powershell
cd desktop\app
npm install
npm run tauri dev
```

フロントエンドのみ確認:

```powershell
cd desktop\app
npm run dev
```

配布用ビルド:

```powershell
cd desktop\app
npm run bundle
```

## 注意

* OpenAI APIを直接叩かない
* ChatGPT公式Webや公式デスクトップアプリをスクレイピングしない
* GUI中に `memory/long_term.md` や `journal` を勝手に編集しない
* memory / journal / summary 更新は「会話を整理して保存」操作で既存finalize処理に接続する
* memory / journal / summary 更新は `gpt-5.6-terra` / `model_reasoning_effort="medium"` を使う
* 入力欄横の「過去の会話をすべて参照」は送信単位の指定とし、ONの送信では対象となる過去チャットを全件ページングして確認する。指定自体は会話本文やlive JSONLへ追記せず、送信完了後にOFFへ戻す
* モデル・応答設定UIは RT-0015 として保留し、方針は `docs/response_settings_ui.md` に分ける
* 未実装の設定をGUIに出さない
* Git commitはGUIから自動連発しない
* メッセージ編集、回答再生成、会話分岐を入れる場合は、`docs/conversation_branching.md` の派生セッション方針に従い、既存JSONLを直接書き換えない
* 投稿欄上の「Notionを参照する」は既定OFFで、ONの送信だけ公式Notion MCPの読み取りtoolを公開する。現在のセッションでは送信後もONを維持し、手動で外すか新規・別セッションへ切り替えたときにOFFへ戻す。MCP response本文は保存せず、生成されたassistant回答だけが通常のlive JSONLへ残る。OAuth、tool isolation、出典表示、失敗時の詳細は `docs/notion_read_only_integration.md` を参照する

## ログ

GUIのエラー確認用ログ:

```text
logs/chat_gui_task.log
logs/chat_gui_tauri.log
logs/chat_gui_bridge.log
logs/chat_gui_jobs/*.json
logs/chat_gui_jobs/*.log
```

`chat_gui_task.log` は VS Code task、npm install、Vite、Tauri dev/build の標準出力とエラーを記録します。起動前に落ちるエラーはまずこのファイルを確認します。

`chat_gui_tauri.log` は Tauri 側から Python ブリッジを呼ぶ前後の状態、終了コード、起動失敗を記録します。

`chat_gui_bridge.log` は Python ブリッジ側のコマンド開始・完了・エラー種別を記録します。会話本文は書かず、文字数、session id、件数などの診断情報だけを残します。

## RT-0008 Chat GUI UX

RT-0008 では、日常的な ChatGPT 風利用に近づけるため、Chat GUI に以下を追加します。

* 返答生成中、停止要求中、失敗、完了の状態表示
* 返答生成の停止ボタン
* assistant 返答全体のコピー
* fenced code block の読みやすい表示とコード単位のコピー
* エラー時の入力欄への復元、セッション一覧更新、新規チャットへの誘導

停止はストリーミング停止ではありません。既存の GUI は Tauri command から `scripts/chat_gui_bridge.py` を呼び、bridge が `codex.cmd exec` を待つ構造です。そのため RT-0008 では、GUI が `request_id` 付きで送信し、停止ボタンが `cancel-message` を呼んで `logs/chat_gui_cancel/*.cancel` を作成します。bridge は生成中にその cancel file を監視し、検知した場合は Codex CLI のプロセスツリーを停止して assistant 返答を保存しません。

制約:

* user 発言は Codex 呼び出し前に live JSONL へ保存されるため、停止しても user 発言は残ります。
* user 発言のGUI即時表示は、RT-0018 の方針通り、保存済みメッセージではなくUI一時状態として扱います。bridge応答後は保存済み `messages` を正として置き換えます。
* assistant 返答の読み上げは RT-0019 として実装済みです。Kokoro TTS は任意依存であり、assistantメッセージごとの読み上げ・停止・voice選択を提供します。文ごとのWAVを生成できた順に再生キューへ追加するため、長文でも全文の合成完了を待ちません。モデルは `cache/tts/`、WAV はOS一時フォルダに置き、会話や記憶の保存処理には影響しません。セットアップ手順は `docs/kokoro_tts_read_aloud.md` を参照してください。
* エラー時の「入力に戻す」は再送信ではありません。JSONLへの重複保存を避けるため、直前入力を下書きとして戻し、必要ならユーザーが修正して新規メッセージとして送信します。
* Codex CLI や OS 側の都合でプロセス停止に時間がかかる場合、GUI は「停止中」と表示して bridge の終了を待ちます。
* RT-0017で `codex app-server` の `item/agentMessage/delta` をTauri Channel経由で表示するストリーミングを追加しました。完了したassistant本文だけをJSONLへ1回保存し、停止時の部分出力は保存しません。app-server非対応時は従来の `codex.cmd exec --output-last-message` へフォールバックします。詳細は `docs/streaming_response_ui.md` を参照してください。

## RT-0009 Conversation History Sidebar

左サイドバーには、Phase2.65 の resume 対象であるuser入力付きlive sessionを、経過日数に関係なく新しい順に最大50件表示します。

表示する情報:

* セッションタイトル
* セッションID
* メッセージ数
* 最終user入力日時
* 整理状態

GUIから新規チャット開始と既存セッション再開を選べます。50件を超える全履歴の検索、ピン留め、長期スレッド管理は別チケットで扱います。

## RT-0011 File Attachments MVP

GUIではファイル選択から `.txt`、`.md`、`.pdf`、`.xlsx` を添付できます。

MVP制限:

* 最大3ファイル
* 1ファイル最大1 MiB
* 抽出テキストは1ファイル最大12,000文字
* `.txt` / `.md` はGUIでテキスト抽出する
* `.pdf` はbridge側で `pypdf` が利用できる場合だけテキスト抽出する
* `.xlsx` はbridge側で `openpyxl` を使い、表示中シートのセル値と数式を行単位のテキストとして抽出する
* `.xlsx` は最大20シート、各シート最大200行・50列まで読み込む

添付本文は回答生成の一時コンテキストとして使います。live JSONL には本文全文を保存せず、ファイル名、形式、抽出状態、文字数、切り詰め有無だけをuser発言のメタデータとして残します。詳細方針は `docs/file_attachments_mvp.md` に整理しています。

## RT-0013 Background Finalize Jobs

「整理して保存」は同期実行ではなく、`logs/chat_gui_jobs/*.json` を状態ファイルとするバックグラウンドジョブとして起動します。

ジョブ状態:

* `queued`
* `running`
* `succeeded`
* `failed`
* `cancelled`

GUIはジョブIDをpollして、進捗、現在段階、完了結果、エラー、ログパスを表示します。ジョブ中も画面は応答しますが、同じセッションへの新規送信は競合防止のため無効化します。詳細は `docs/background_jobs.md` に整理しています。

## 管理メニュー

左サイドバーの「管理」は展開式メニューです。

* 「ローカルデータ」は既存の読み取り専用データ管理画面を開く
* 「データ整理」は、再開可能セッションのうち未整理または整理失敗のものを経過日数に関係なく古い順に1件ずつ整理する
* 「ChatGPTインポート」は、エクスポートを読み取り専用で確認し、新規または更新された会話を初期0件から明示選択して、最終確認を経て取り込む
* 「Notion連携」は、`mcp-remote` OAuth bridgeの接続状態、固定endpoint、公開された読み取りtool、login/logout手順だけを表示する。target管理画面やtoken入力欄は持たない

データ整理は確認後にだけ開始します。個別の失敗は記録して残りを続行し、停止した場合や失敗した場合は未処理のセッションを後で再実行できます。整理中は新規チャット、セッション切り替え、個別整理を無効化します。

ChatGPTインポートは `raw.md` と `import_metadata.json` を作成またはrevision-awareに更新し、成功後に派生検索indexを再構築します。summary、journal、memoryは更新せず、エクスポート元の絶対パスや会話本文をGUIログに残しません。変更なしの会話はスキップし、競合は自動上書きしません。詳細は `docs/chatgpt_export_import.md` を参照してください。

## RT-0014 Local Data Management

GUIに読み取り専用のローカルデータ管理画面を追加します。

表示対象:

* `conversations/`
* `journal/`
* `memory/`
* `inbox/`
* `tasks/`
* `imports/`
* `logs/`
* `memory/search_index.sqlite3`

MVPでは削除、移動、編集、index再構築、memory更新、Git操作を行いません。保存先フォルダを開く導線と privacy check コマンドの確認導線だけを提供します。詳細方針は `docs/local_data_management.md` に整理しています。
