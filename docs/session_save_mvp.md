# Phase2.65: Session Save / Resume MVP

Phase2.65 は、Phase2.6 のCLI会話と Phase2.7 のGUIの間に置く小さなセッション管理層です。

目的は、`inbox/live/*.jsonl` に残るライブ会話を、GUIや後続の finalize 処理から扱いやすい「保存済みセッション」として記録し、Codexの `/resume` のように過去の会話を再開できるようにすることです。

## 目的

* live JSONLを削除・移動せずに保存済みとしてマークする
* セッションID、タイトル、メッセージ数、開始時刻、更新時刻、保存時刻をメタデータ化する
* 最新セッションを簡単に保存できるようにする
* 保存済みセッションを一覧できるようにする
* `/resume` でuser入力のあるセッションを経過日数に関係なくロードできるようにする
* 会話ログ・live JSONL・セッション情報を削除せず保持する
* Phase2.7 のGUIから同じ保存処理を呼べるようにする

## 保存形式

元ログ:

```text
inbox/live/YYYY-MM-DD_HHMMSS.jsonl
```

保存メタデータ:

```text
inbox/live/YYYY-MM-DD_HHMMSS.session.json
```

例:

```json
{
  "version": 1,
  "session_id": "2026-07-01_223000",
  "status": "saved",
  "title": "セッション保存を追加したい",
  "jsonl_file": "inbox/live/2026-07-01_223000.jsonl",
  "message_count": 2,
  "started_at": "2026-07-01T22:30:00+09:00",
  "updated_at": "2026-07-01T22:30:05+09:00",
  "saved_at": "2026-07-01T22:31:00+09:00"
}
```

## コマンド

最新のlive JSONLを保存済みにする:

```powershell
python scripts\session_store.py save
```

対象ファイルとタイトルを指定する:

```powershell
python scripts\session_store.py save --file inbox\live\2026-07-01_223000.jsonl --title "Phase2.65検討"
```

保存済みセッションを一覧する:

```powershell
python scripts\session_store.py list
```

再開できるセッションを一覧する:

```powershell
python scripts\session_store.py resume-list
```

一覧は新しい順に最大50件を表示する。CLIでは `--limit` で表示件数を変更できる。

最後のuser入力から指定日数を超えたセッションを参考確認する:

```powershell
python scripts\session_store.py prune
```

`prune` は一覧表示のみで、削除操作はありません。表示されたセッションも引き続きresumeできます。

CLI会話を最新セッションから再開する:

```powershell
python scripts\codex_conversation.py --resume
```

CLI会話中に再開候補を表示する:

```text
/resume
```

PowerShellの対話端末では、表示された候補を `Up/Down` で移動して `Enter` で選択します。選択をやめる場合は `Esc` または `q` を入力します。

パイプ入力などカーソル選択できない環境では、表示された候補を番号で選択します。番号選択をやめる場合は `/cancel` を入力します。

CLI会話中に特定セッションを再開する:

```text
/resume 2026-07-01_223000
```

## 保持ルール

* user入力のある `inbox/live/*.jsonl` は経過日数に関係なく再開候補にする
* `prune` は指定日数を超えたセッションを参考表示するだけで、resume可否に影響せず削除もしない
* 会話ログ、live JSONL、`.session.json` は10年以上保持する

## やらないこと

* 通常の保存や再開ではJSONLを削除・移動しない
* `memory/long_term.md` や `journal` を編集しない
* Git commitしない
* 過去ログ検索UIを作らない
* ベクトル検索を先取りしない
* 複数会話の本格管理UIを作らない

## Phase2.7との接続

Phase2.7 のGUIでは、会話終了時または「セッションを保存」ボタン押下時に `scripts/session_store.py` の保存処理を呼びます。

GUIは最初から検索や複雑な履歴管理を持たず、保存済みセッションのメタデータ表示と再開に留めます。

## GUI履歴サイドバー

左サイドバーには、Phase2.65 の resume 対象であるuser入力付きlive sessionを、経過日数に関係なく新しい順に最大50件表示します。

表示する情報:

* セッションタイトル
* セッションID
* メッセージ数
* 最終user入力日時
* 整理状態

GUIから新規チャット開始と既存セッション再開を選べます。50件を超える全履歴の検索、ピン留め、長期スレッド管理は Session Save / Resume MVP の範囲外です。

## RT-0016 分岐機能との関係

ChatGPT風のメッセージ編集、回答再生成、会話分岐は `docs/conversation_branching.md` で検討中です。

現時点の方針では、分岐は既存JSONLを書き換えず、親セッションから派生した新しい live JSONL と `.session.json` として扱います。現在のSession Save / Resume MVPには分岐metadataを実装しません。

## 完了条件

* `python scripts\session_store.py save` で最新live JSONLの `.session.json` を作れる
* `--file` と `--title` を指定して保存できる
* `python scripts\session_store.py list` で保存済みセッションを確認できる
* `python scripts\session_store.py resume-list` で経過日数に関係なく再開候補を確認できる
* `python scripts\codex_conversation.py --resume`、`/resume <id>`、または `/resume` 後の番号入力でセッションをロードできる
* `python scripts\session_store.py prune` で指定日数を超えたセッションを参考確認できる
* 会話ログ、live JSONL、`.session.json` は削除しない
* 通常の保存や再開では元のJSONLを削除・移動しない
* `python -m unittest` が通る
