# AI-LifeOS Desktop

Phase2.7 の Chat GUI MVP です。

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

## 開発起動

Node.js は 22 LTS 以上を使います。`.nvmrc` は 22.23.1 に固定しています。

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

配布用ビルド:

```powershell
cd desktop\app
npm run bundle
```

## 役割

GUIは既存Python処理の薄いラッパーです。

* `scripts/live_session.py`
* `scripts/codex_conversation.py`
* `scripts/session_store.py`
* `scripts/finalize_live_chat.py`

Tauriからは `scripts/chat_gui_bridge.py` を呼び、CLIと同じ保存・再開・finalize処理を使います。

## ログ

GUIのエラー確認用ログ:

```text
logs/chat_gui_task.log
logs/chat_gui_tauri.log
logs/chat_gui_bridge.log
```

`chat_gui_task.log` は VS Code task、npm、Vite、Tauri dev/build の出力を残します。`chat_gui_tauri.log` と `chat_gui_bridge.log` はアプリ内部の呼び出し状態を残します。

会話本文はログに書かず、コマンド名、session id、終了コード、エラー種別を中心に残します。
