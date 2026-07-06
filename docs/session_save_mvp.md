# Phase2.65: Session Save / Resume MVP

Phase2.65 は、Phase2.6 のCLI会話と Phase2.7 のGUIの間に置く小さなセッション管理層です。

目的は、`inbox/live/*.jsonl` に残るライブ会話を、GUIや後続の finalize 処理から扱いやすい「保存済みセッション」として記録し、Codexの `/resume` のように過去の会話を再開できるようにすることです。

## 目的

* live JSONLを削除・移動せずに保存済みとしてマークする
* セッションID、タイトル、メッセージ数、開始時刻、更新時刻、保存時刻をメタデータ化する
* 最新セッションを簡単に保存できるようにする
* 保存済みセッションを一覧できるようにする
* `/resume` で最後のuser入力が10日以内のセッションをロードできるようにする
* 10日を超えたセッションは削除候補にできるようにする
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

10日を超えたセッションを確認する:

```powershell
python scripts\session_store.py prune
```

実際に削除する:

```powershell
python scripts\session_store.py prune --delete
```

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

* 再開候補は、最後の `user` 入力が10日以内の `inbox/live/*.jsonl` に限定する
* 10日を超えたセッションは `prune` の対象にする
* `prune` はデフォルトでは削除せず、対象表示だけ行う
* 実削除は `prune --delete` を明示した場合だけ行う
* 削除対象はJSONLと同名の `.session.json` に限定する

## やらないこと

* 通常の保存や再開ではJSONLを削除・移動しない
* `memory/long_term.md` や `journal` を編集しない
* Git commitしない
* 過去ログ検索UIを作らない
* ベクトル検索を先取りしない
* 複数会話の本格管理UIを作らない

## Phase2.7との接続

Phase2.7 のGUIでは、会話終了時または「セッションを保存」ボタン押下時に `scripts/session_store.py` の保存処理を呼びます。

GUIは最初から検索や複雑な履歴管理を持たず、保存済みセッションのメタデータ表示と、10日以内セッションの再開に留めます。

## RT-0016 分岐機能との関係

ChatGPT風のメッセージ編集、回答再生成、会話分岐は `docs/conversation_branching.md` で検討中です。

現時点の方針では、分岐は既存JSONLを書き換えず、親セッションから派生した新しい live JSONL と `.session.json` として扱います。現在のSession Save / Resume MVPには分岐metadataを実装しません。

## 完了条件

* `python scripts\session_store.py save` で最新live JSONLの `.session.json` を作れる
* `--file` と `--title` を指定して保存できる
* `python scripts\session_store.py list` で保存済みセッションを確認できる
* `python scripts\session_store.py resume-list` で10日以内の再開候補を確認できる
* `python scripts\codex_conversation.py --resume`、`/resume <id>`、または `/resume` 後の番号入力でセッションをロードできる
* `python scripts\session_store.py prune` で10日超の削除候補を確認できる
* 実削除は `prune --delete` を明示した場合だけ行う
* 通常の保存や再開では元のJSONLを削除・移動しない
* `python -m unittest` が通る
