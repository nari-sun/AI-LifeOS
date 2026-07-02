# AI-LifeOS

AI-LifeOS は、ChatGPT や Codex との会話をローカルPCに保存し、後から要約・日記・長期メモリとして活用するための個人用AI記憶システムです。

現在は Phase2.65 までのMVPが入っています。OpenAI API、`.env`、外部ベクトルDBは使わず、ローカルMarkdown、Codex CLI、Gitで運用します。

Windows PowerShellでMarkdownの日本語が文字化けして見える場合は、ファイル自体ではなく表示時の文字コードが原因のことがあります。確認するときは次のようにUTF-8を指定してください。

```powershell
Get-Content -Encoding UTF8 README.md
Get-Content -Encoding UTF8 prompts\codex_phase2_prompt.md
```

## できること

- `inbox/chat.txt` に貼った会話を `raw.md` として保存する
- 保存した会話ごとに Codex 用タスク `tasks/latest_codex_task.md` を生成する
- `codex.cmd exec` で `summary.md`、`journal`、`memory/long_term.md` を自動更新する
- PowerShellスクリプトで保存、Codex実行、Git commitまで自動実行する
- PowerShell上でlive会話を行い、`inbox/live/*.jsonl` に逐次保存する
- live JSONLを `raw.md` に変換し、既存のPhase2.5記憶整理へ接続する
- live会話セッションを保存済みメタデータ化し、10日以内のセッションを再開候補として扱う
- commit前にステージ済み差分の個人情報・秘密情報チェックを実行する
- `python -m unittest` で保存処理をテストする

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
│  └─ long_term.md
├─ prompts/
│  └─ codex_phase2_prompt.md
├─ scripts/
│  ├─ process_chat.py
│  ├─ save_chat.ps1
│  ├─ codex_conversation.py
│  ├─ finalize_live_chat.py
│  ├─ live_session.py
│  └─ session_store.py
├─ docs/
│  ├─ codex_conversation_mvp.md
│  └─ session_save_mvp.md
├─ tasks/
│  └─ latest_codex_task.md
└─ tests/
   ├─ test_codex_conversation.py
   ├─ test_finalize_live_chat.py
   ├─ test_live_session.py
   ├─ test_process_chat.py
   └─ test_session_store.py
```

## 基本フロー

1. ChatGPTなどの会話をコピーする
2. `inbox/chat.txt` に貼る
3. `.\scripts\save_chat.ps1` を実行する
4. `conversations/YYYY/MM/YYYY-MM-DD_HHMMSS/raw.md` が作成される
5. `tasks/latest_codex_task.md` が作成される
6. `codex.cmd exec` が非対話で実行される
7. Codexが `summary.md`、`journal/YYYY/MM/YYYY-MM-DD.md`、`memory/long_term.md` を更新する
8. 対象ファイルがGit commitされる

live会話側の基本フロー:

```text
python scripts\codex_conversation.py
↓
会話する
↓
inbox/live/YYYY-MM-DD_HHMMSS.jsonl に user/assistant を逐次保存
↓
/exit または Ctrl+C
↓
conversations/YYYY/MM/YYYY-MM-DD_HHMMSS/raw.md を作成
↓
Codexが summary / journal / memory を更新
↓
必要なら --commit-on-exit でcommit
```

会話中は `journal` や `memory/long_term.md` を更新しません。journal / memory への反映は、`/exit` または Ctrl+C の終了処理で自動実行します。

## コマンド

### 会話を保存するだけ

```powershell
python scripts\process_chat.py
```

起こること:

- `inbox/chat.txt` を読む
- `conversations/YYYY/MM/YYYY-MM-DD_HHMMSS/raw.md` を作る
- `tasks/latest_codex_task.md` を作る
- `inbox/chat.txt` を空にする
- Codex は実行しない
- Git commit はしない

### inboxを残して保存する

```powershell
python scripts\process_chat.py --keep-inbox
```

起こること:

- `raw.md` と `latest_codex_task.md` を作る
- `inbox/chat.txt` は空にしない
- Codex は実行しない
- Git commit はしない

### 日付を指定して保存する

```powershell
python scripts\process_chat.py --date 2026-06-28
```

起こること:

- 指定した日付で `conversations/2026/06/2026-06-28_HHMMSS/raw.md` を作る
- 時刻部分は実行時刻を使う
- Codex は実行しない
- Git commit はしない

### Pythonだけで最後まで実行する

```powershell
python scripts\process_chat.py --run-codex --commit
```

起こること:

- `raw.md` と `latest_codex_task.md` を作る
- `codex.cmd exec` を非対話で実行する
- Codexが `summary.md`、`journal`、`memory/long_term.md` を更新する
- `conversations`、`journal`、`memory`、`inbox`、`tasks` を `git add` する
- 変更があれば `Process chat session YYYY-MM-DD` でGit commitする

### 保存、Codex実行、commitまで自動実行する

```powershell
.\scripts\save_chat.ps1
```

起こること:

- 内部で `python scripts\process_chat.py --run-codex --commit` を実行する
- `raw.md` と `latest_codex_task.md` を作る
- `codex.cmd exec` で記憶整理を実行する
- `conversations`、`journal`、`memory`、`inbox`、`tasks` を `git add` する
- 変更があれば `Process chat session YYYY-MM-DD` でGit commitする
- Codexが失敗した場合はGit commitしない

### inboxを残して最後まで自動実行する

```powershell
.\scripts\save_chat.ps1 -KeepInbox
```

起こること:

- 保存とGit commitを行う
- Codexも実行する
- `inbox/chat.txt` は空にしない

### 日付を指定して最後まで自動実行する

```powershell
.\scripts\save_chat.ps1 -Date 2026-06-28
```

起こること:

- 指定した日付で保存する
- Codexも実行する
- 変更があれば `Process chat session 2026-06-28` でGit commitする

### Codexだけ飛ばして保存とcommitをする

```powershell
.\scripts\save_chat.ps1 -SkipCodex
```

起こること:

- `raw.md` と `latest_codex_task.md` を作る
- Codexは実行しない
- 保存結果だけGit commitする

### live会話CLIを起動する

```powershell
python scripts\codex_conversation.py
```

起こること:

- `inbox/live/YYYY-MM-DD_HHMMSS.jsonl` を作る
- ユーザー発言をCodexへ送る前に保存する
- Codex返答を受け取った後にassistant発言として保存する
- Codex返答生成は `codex.cmd exec` を `read-only` サンドボックスで呼ぶ
- `/exit` または Ctrl+C で終了する
- 終了時にlive JSONLを `raw.md` 化する
- 終了時に既存Phase2.5処理で `summary.md`、`journal`、`memory/long_term.md` を更新する
- 終了時の整理中はスピナーと段階ベースの%を表示する
- 会話中に `journal`、`memory/long_term.md`、Git commit は実行しない
- Git commit は自動では実行しない

AI返答なしで起動する場合:

```powershell
python scripts\codex_conversation.py --no-ai
```

AI返答も終了時整理も止めて、JSONLログ保存だけ確認する場合:

```powershell
python scripts\codex_conversation.py --no-ai --no-finalize-on-exit
```

終了時の自動整理を止める場合:

```powershell
python scripts\codex_conversation.py --no-finalize-on-exit
```

終了時にraw.md化だけして、summary / journal / memory 更新を止める場合:

```powershell
python scripts\codex_conversation.py --no-process-on-exit
```

終了時に整理処理後のcommitまで行う場合:

```powershell
python scripts\codex_conversation.py --commit-on-exit
```

終了時の進捗表示を消す場合:

```powershell
python scripts\codex_conversation.py --no-exit-progress
```

### live JSONLをraw.md化する

```powershell
python scripts\finalize_live_chat.py
```

起こること:

- 最新の `inbox/live/*.jsonl` を対象にする
- `conversations/YYYY/MM/YYYY-MM-DD_HHMMSS/raw.md` を作る
- `tasks/latest_codex_task.md` を作る
- 元のJSONLは削除・移動しない
- Codex は実行しない
- Git commit はしない

通常の `python scripts\codex_conversation.py` では終了時に自動実行されます。このコマンドは、過去のlive JSONLを手動で再処理したい場合に使います。

raw.md化後にsummary / journal / memoryまで更新する場合:

```powershell
python scripts\finalize_live_chat.py --run-codex
```

更新後にcommitまで行う場合:

```powershell
python scripts\finalize_live_chat.py --run-codex --commit
```

### liveセッションを保存済みにする

```powershell
python scripts\session_store.py save
```

起こること:

- 最新の `inbox/live/*.jsonl` を対象にする
- 同じ場所に `.session.json` を作る
- 元のJSONLは削除・移動しない
- Git commit はしない

### 再開できるセッションを見る

```powershell
python scripts\session_store.py resume-list
```

起こること:

- 最後のuser入力が10日以内の `inbox/live/*.jsonl` を表示する
- 10日を超えたセッションは再開候補に出さない

### 最新セッションを再開する

```powershell
python scripts\codex_conversation.py --resume
```

会話中に候補を見る場合:

```text
/resume
```

PowerShellの対話端末では、候補一覧を `Up/Down` で移動して `Enter` で再開します。中止は `Esc` または `q` です。

パイプ入力などカーソル選択できない環境では、番号入力に戻ります。

会話中に特定セッションを再開する場合:

```text
/resume 2026-07-01_223000
```

### 10日超セッションの削除候補を見る

```powershell
python scripts\session_store.py prune
```

実際に削除する場合:

```powershell
python scripts\session_store.py prune --delete
```

`prune` はデフォルトでは削除せず、対象表示だけ行います。

### commit前に個人情報・秘密情報を確認する

```powershell
python scripts\privacy_check.py --staged
```

起こること:

- ステージ済みファイルを確認する
- APIキー、token、password、メールアドレス、電話番号らしき文字列を検出する
- 検出した場合はexit code 1で止める

`python scripts\process_chat.py --run-codex --commit` と `.\scripts\save_chat.ps1` の自動commit前にも、このチェックを実行します。

### push前に未pushコミットを確認する

```powershell
python scripts\privacy_check.py --range origin/main..HEAD
```

起こること:

- `origin/main..HEAD` に含まれる変更ファイルを確認する
- push前に、すでにcommit済みの内容へ個人情報・秘密情報が含まれていないか確認できる

## Codexで記憶整理する

通常は `.\scripts\save_chat.ps1` が `codex.cmd exec` を自動実行します。

手動でCodexに渡したい場合は、`python scripts\process_chat.py` を実行した後、`tasks/latest_codex_task.md` を確認します。

```powershell
Get-Content -Raw -Encoding UTF8 tasks\latest_codex_task.md
```

この内容をCodexに渡すと、対象の `raw.md` を読んで以下を作成・更新します。

- 同じ会話フォルダの `summary.md`
- `journal/YYYY/MM/YYYY-MM-DD.md`
- `memory/long_term.md`

Codex用プロンプトの元ファイルは `prompts/codex_phase2_prompt.md` です。

## テスト

```powershell
python -m unittest
```

確認していること:

- `raw.md` が正しい場所に作られる
- `latest_codex_task.md` が作られる
- `--date` が効く
- `--keep-inbox` が効く
- `codex.cmd exec` 用のコマンドが組み立てられる
- live会話JSONLが保存される
- live JSONLを `raw.md` に変換できる
- `finalize_live_chat.py --run-codex --commit` の接続コマンドが組み立てられる
- Git commit用の対象ファイルが限定される
- commit前の個人情報・秘密情報チェックが失敗した場合はcommitしない
- 空の `inbox/chat.txt` では保存しない
- プロンプトテンプレートがない場合に中途半端なファイルを作らない

## 方針

- 会話ログにないことは記録しない
- APIキーや秘密情報は保存しない
- commit / push 前に `python scripts\privacy_check.py --staged` を実行する
- push 前に未pushコミットがある場合は `python scripts\privacy_check.py --range origin/main..HEAD` を実行する
- `.env` やOpenAI API直叩きは前提にしない
- `memory/long_term.md` は長期的に重要な情報だけ追記する
- 自動commit対象は `conversations`、`journal`、`memory`、`inbox`、`tasks` に限定する
- まずは小さく動く単位で安定させる
