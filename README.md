# AI-LifeOS

AI-LifeOS は、ChatGPT や Codex との会話をローカルPCに保存し、後から要約・日記・長期メモリとして活用するための個人用AI記憶システムです。

現在は Phase2.5 付近です。OpenAI API、`.env`、外部ベクトルDBは使わず、ローカルMarkdown、Codex CLI、Gitで運用します。

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
│  └─ chat.txt
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
│  └─ save_chat.ps1
├─ tasks/
│  └─ latest_codex_task.md
└─ tests/
   └─ test_process_chat.py
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
- Git commit用の対象ファイルが限定される
- 空の `inbox/chat.txt` では保存しない
- プロンプトテンプレートがない場合に中途半端なファイルを作らない

## 方針

- 会話ログにないことは記録しない
- APIキーや秘密情報は保存しない
- `.env` やOpenAI API直叩きは前提にしない
- `memory/long_term.md` は長期的に重要な情報だけ追記する
- 自動commit対象は `conversations`、`journal`、`memory`、`inbox`、`tasks` に限定する
- まずは小さく動く単位で安定させる
