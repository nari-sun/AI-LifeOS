# ChatGPT Export Import

`scripts/import_chatgpt_export.py` は、ChatGPT のデータエクスポートに含まれる
`conversations.json` を、AI-LifeOS の `conversations/YYYY/MM/YYYY-MM-DD_HHMMSS/raw.md`
へローカルで変換します。ChatGPT 公式Webやデスクトップアプリは操作せず、OpenAI APIも使いません。

## 安全方針

- 既定は dry-run で、ファイルを書きません。
- 実取り込みは `--apply` に加え、`--all`、期間、タイトル、会話IDのいずれかを明示した場合だけ行います。
- `summary.md`、journal、memory、検索indexは自動更新しません。
- zip は展開せず、内部の `conversations.json` だけを読みます。
- `user` / `assistant` のテキスト発言だけを保存します。分岐情報がある場合は `current_node` につながる現在の枝を選びます。
- source conversation ID を `import_metadata.json` に残し、同じIDの再取り込みをスキップします。IDがない場合は内容から安定したSHA-256識別子を作ります。
- エクスポート元は `imports/`、変換結果は `conversations/` に置き、いずれもPublicEditionではGit管理しません。

## 1. Dry-run

ChatGPT の export zip を `imports/chatgpt_export/` に置いた例です。

```powershell
python scripts\import_chatgpt_export.py imports\chatgpt_export\export.zip
```

exportを展開済みなら、フォルダまたは `conversations.json` を直接指定できます。dry-runには、
export内件数、選択件数、UTC期間、タイトル、会話ID、発言数、重複状態が表示されます。

### Chat GUIから確認する

Chat GUIでは、左サイドバーの **管理 > ChatGPTインポート** から、export ZIP、展開済みフォルダ、または `conversations.json` を選択できます。

最初の選択操作はdry-runで、会話一覧、新規件数、重複件数を表示するだけです。タイトル、会話ID、UTC作成日で絞り込み、取り込む会話にチェックを付けます。重複済みの会話は選択できません。

## 2. 対象を絞る

```powershell
# UTCの作成日
python scripts\import_chatgpt_export.py imports\chatgpt_export --from-date 2025-01-01 --to-date 2025-12-31

# タイトル部分一致
python scripts\import_chatgpt_export.py imports\chatgpt_export --title "project"

# dry-runに表示された会話IDを個別選択（--idは複数指定可）
python scripts\import_chatgpt_export.py imports\chatgpt_export --id CONVERSATION_ID
```

期間、タイトル、IDを組み合わせた場合は、すべての条件に一致する会話だけが対象です。

## 3. 取り込む

dry-runを確認してから、同じ対象指定に `--apply` を追加します。

```powershell
python scripts\import_chatgpt_export.py imports\chatgpt_export --id CONVERSATION_ID --apply
```

全件を明示して取り込む場合:

```powershell
python scripts\import_chatgpt_export.py imports\chatgpt_export --all --apply
```

各会話には次の2ファイルが作られます。

```text
conversations/YYYY/MM/YYYY-MM-DD_HHMMSS/
├─ raw.md
└─ import_metadata.json
```

同じ秒に複数の会話がある場合は、既存セッションを上書きせず、空いている次の秒の標準形式ディレクトリへ保存します。
`raw.md` 内の `Created At` と sidecar metadata には元のUTC時刻が保持されます。

GUIでは、選択件数を確認するダイアログに同意した場合だけ同じ取り込みを実行します。GUIの取り込みも、summary / journal / memory / 検索indexを更新しません。

## 記憶整理

インポート直後は過去ログの確認だけに留めます。必要な会話だけを確認し、summary / journal / memoryへ
反映する場合は、既存のPhase2.5処理へ別途明示的に渡してください。インポートコマンド自身は
`process_chat.py`、`finalize_live_chat.py`、`rebuild_index.py` を呼びません。
