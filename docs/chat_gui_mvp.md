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
* 10日以内の再開可能セッション一覧表示
* セッション再開
* セッションメタデータ保存
* 「会話を整理して保存」ボタンから `finalize_live_chat.py` 相当の処理を実行
* エラー表示

最初のGUIで扱わないこと:

* 過去ログ全文検索
* ベクトル検索
* MCP連携
* 複雑な履歴管理
* 10日超セッションの自動削除
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
* Git commitはGUIから自動連発しない

## ログ

GUIのエラー確認用ログ:

```text
logs/chat_gui_task.log
logs/chat_gui_tauri.log
logs/chat_gui_bridge.log
```

`chat_gui_task.log` は VS Code task、npm install、Vite、Tauri dev/build の標準出力とエラーを記録します。起動前に落ちるエラーはまずこのファイルを確認します。

`chat_gui_tauri.log` は Tauri 側から Python ブリッジを呼ぶ前後の状態、終了コード、起動失敗を記録します。

`chat_gui_bridge.log` は Python ブリッジ側のコマンド開始・完了・エラー種別を記録します。会話本文は書かず、文字数、session id、件数などの診断情報だけを残します。
