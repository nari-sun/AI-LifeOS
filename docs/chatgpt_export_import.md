# ChatGPT Export Import

`scripts/import_chatgpt_export.py` は、ChatGPT のデータエクスポートに含まれる
`conversations.json` または分割された `conversations-*.json` を、AI-LifeOS の `conversations/YYYY/MM/YYYY-MM-DD_HHMMSS/raw.md`
へローカルで変換します。ChatGPT 公式Webやデスクトップアプリは操作せず、OpenAI APIも使いません。

## 安全方針

- 既定は dry-run で、ファイルを書きません。
- 実取り込みは `--apply` に加え、`--all`、期間、タイトル、会話IDのいずれかを明示した場合だけ行います。
- `summary.md`、journal、memoryは自動更新しません。GUIから取り込んだ場合だけ、派生データである検索indexを取り込み後に再構築します。
- zip は展開せず、内部の `conversations.json` または連番の `conversations-*.json` を読みます。
- `user` / `assistant` のテキスト発言と音声文字起こしだけを保存します。分岐情報がある場合は `current_node` につながる現在の枝を選びます。`current_node` が欠損・不正なら、最新leafから親を辿った単一branchだけを選び、別branchを混在させません。画像・音声ファイル本体や内部reasoningは保存しません。
- source conversation ID と抽出内容のfingerprintを `import_metadata.json` に残します。同じID・同じ内容はスキップし、同じIDで内容が更新されていれば既存rawを更新します。更新前revisionは検索対象外の `import_revisions/` に残します。
- 同じsource conversation IDが複数の保存先にある場合、1つのexport内で異なるrevisionが重複する場合、または保存済みrevisionより古く内容も異なるexportを選んだ場合は、競合として自動更新・rollbackを止めます。IDがない場合は内容から安定したSHA-256識別子を作ります。
- エクスポート元は `imports/`、変換結果は `conversations/` に置き、いずれもPublicEditionではGit管理しません。

## 1. Dry-run

ChatGPT の export zip を `imports/chatgpt_export/` に置いた例です。

```powershell
python scripts\import_chatgpt_export.py imports\chatgpt_export\export.zip
```

exportを展開済みなら、フォルダ、`conversations.json`、または個別の `conversations-*.json` を直接指定できます。dry-runには、
export内件数、選択件数、UTC期間、タイトル、会話ID、抽出発言数、新規・更新・変更なし・競合の状態、音声文字起こし数、非テキスト部分数が表示されます。

### Chat GUIから確認する

Chat GUIでは、左サイドバーの **管理 > ChatGPTインポート** から、export ZIP、展開済みフォルダ、`conversations.json`、または個別の `conversations-*.json` を選択できます。

最初の選択操作はdry-runで、会話一覧、新規・更新・変更なし・競合件数を表示するだけです。誤操作を避けるため初期選択は0件です。タイトル、会話ID、UTC作成日で絞り込み、「表示中の対象を選択」または個別チェックで対象を明示します。フィルタを変更すると、それまでの選択は解除されます。変更なしと競合の会話は選択できません。

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

新規会話には次の2ファイルが作られます。

```text
conversations/YYYY/MM/YYYY-MM-DD_HHMMSS/
├─ raw.md
└─ import_metadata.json
```

同じ秒に複数の会話がある場合は、既存セッションを上書きせず、空いている次の秒の標準形式ディレクトリへ保存します。
`raw.md` 内の `Created At` と sidecar metadata には元のUTC時刻が保持されます。

同じsource conversation IDの新しいexport revisionを取り込む場合は、同じ保存先の `raw.md` と `import_metadata.json` を原子的に更新します。更新前の内容は次へ退避し、通常検索には含めません。

```text
conversations/YYYY/MM/YYYY-MM-DD_HHMMSS/import_revisions/
├─ revision-0001.raw.md
└─ revision-0001.metadata.json
```

GUIでは、表示中の選択件数を確認するダイアログに同意した場合だけ取り込みを実行します。成功後は検索indexを再構築します。index更新に失敗しても保存済みrawは成功として保持し、GUIに再構築が必要なことを表示します。CLIのimportコマンドはindexを更新しないため、必要なら `python scripts/rebuild_index.py` を実行します。

## 記憶整理

インポートしたrawは過去会話検索の対象です。明示的な過去照会に加え、通常会話でも話題が強く一致したuser発言を最大1件・短い抜粋だけnarrow contextへ入れます。

summary / journal / memoryへの昇格は行いません。長期記憶への反映は内容を確認してから別途明示的に行ってください。CLIのimportコマンド自身は `process_chat.py`、`finalize_live_chat.py`、`rebuild_index.py` を呼びません。
