# Phase3 Searchable Memory

Phase3 は、保存済みの会話・要約・日記・memory を検索し、必要な過去情報を回答用コンテキストとして渡せるようにする段階です。

## 実装済み範囲

### Phase3.0: Searchable Memory Design

検索対象:

```text
conversations/**/raw.md
conversations/**/summary.md
journal/**/*.md
memory/long_term.md
memory/preferences.md
memory/projects.md
memory/items/*.md
```

役割:

* `raw.md`: 会話全文。詳細確認用。
* `summary.md`: 会話単位の要約、タグ、決定事項。検索の主対象。
* `journal/YYYY/MM/YYYY-MM-DD.md`: 日付単位の行動・進捗。日記検索の主対象。
* `memory/long_term.md`: 長期的に重要な事実・方針。
* `memory/preferences.md`: ユーザーの好み、判断基準、回答スタイル、生活・学習・開発上の嗜好。
* `memory/projects.md`: プロジェクト進捗。
* `memory/items/*.md`: カテゴリ、状態、タグ、出典を持つ構造化メモリ項目。詳細は [structured_memory.md](structured_memory.md) を参照。

### Phase3.1: Markdown Search MVP

`scripts/search_memory.py` で保存済みMarkdownを検索できます。

```powershell
python scripts\search_memory.py "Phase3"
python scripts\search_memory.py "ラーメン 好み" --no-index
python scripts\search_memory.py "AI-LifeOS" --type summary
```

検索は読み取り専用で、`conversations` / `journal` / `memory` は更新しません。

### Phase3.2: Tags and Metadata

`summary.md` の `## タグ` / `## Tags` / `## Tag` セクションを読み取り、タグ検索できます。

```powershell
python scripts\search_memory.py "" --tag Phase3
```

抽出するメタデータ:

* document key
* document type
* path
* title
* date
* tags
* content
* structured memory category / status / source / confidence

### Phase3.3: SQLite-backed Memory Index MVP

`scripts/index_conversations.py` と `scripts/rebuild_index.py` でSQLite indexを作成・再構築できます。

```powershell
python scripts\index_conversations.py
python scripts\rebuild_index.py
```

DB保存先:

```text
memory/search_index.sqlite3
```

このDBはMarkdownから再生成できる派生データです。Git管理しません。

SQLite schema:

* `documents`: document type、path、title、date、tags_json、content を保持
  * 構造化メモリではcategory、category_label、status、source、source_date、confidenceも保持
* `tags`: タグ検索用
* `documents_fts`: FTS5 が使える環境では補助テーブルとして作成

現時点の検索方式は `SQLite-backed index + Python ranking` です。SQLiteには全文とメタデータを保存し、検索時はSQLiteから対象文書を読み出して、Python側で日本語の部分一致ランキングを行います。

FTS5は環境によって日本語トークン化が弱いため、MVPでは検索品質を優先してPython側の一致判定を使います。`documents_fts` が作成される環境でも、現在の検索結果ランキングの主経路はFTS5ではありません。

### Metadata filter pushdown and search profile

SQLite indexを使う検索では、候補をPythonへ読み込む前に次の条件をSQLiteのパラメータ化クエリで絞り込みます。

* document type
* tag
* structured-memory category / status
* exact date、開始日、終了日
* relative-path substring

```powershell
python scripts\search_memory.py "検索語" --type summary --from-date 2026-01-01 --to-date 2026-12-31 --path conversations --profile
python scripts\search_memory.py "" --type memory_item --category study_status --status active --tag 資格 --profile --json
```

`--profile`は検索結果を変えず、次を表示します。

* `index load`: SQLite indexを開きschemaを確認する時間。`--no-index`ではMarkdown収集時間。
* `filter`: SQLite側の絞り込みクエリ時間。`--no-index`では同じ条件のPython絞り込み時間。
* `ranking`: 日本語部分一致のPython ranking時間。
* candidates / results: ranking前の候補数と返却件数。

`--json --profile`では従来の結果配列を`results`に保持し、同じJSONオブジェクトの`profile`で上記の計測値を返します。通常の`--json`出力形式は変わりません。

### Long-term synthetic benchmark

`scripts/search_benchmark.py`は、長期運用時の検索速度と日本語候補取得方式を比較するためのベンチマークです。現在の`conversations` / `journal` / `memory`や既存indexは読みません。毎回一時ディレクトリに合成文書とSQLite indexを作り、完了時に削除します。

```powershell
python scripts\search_benchmark.py
python scripts\search_benchmark.py --sizes 100,1000,5000 --runs 7 --compare-japanese
python scripts\search_benchmark.py --sizes 1000 --compare-japanese --json --output logs\search_benchmark.json
```

各文書数について、metadata filter付きのindex検索を複数回実行し、`index load`、SQLite `filter`、Python `ranking`、全体時間の中央値を出力します。`--compare-japanese`は同じ合成データで次を比較します。

* 現行のPython部分一致 + ranking（本番baseline）
* SQLite `LIKE`（候補取得のみ）
* 一時的なSQLite bigram補助テーブル（候補取得のみ）
* SQLite標準FTS5 tokenizer（利用可能な環境のみ、候補取得のみ）

比較用bigram tableは一時DBだけに作られ、FTS5も本番検索経路へ切り替えません。`--output`の結果もGitへ追加せず、ローカル計測記録として扱います。

`process_chat.py --run-codex` と `finalize_live_chat.py --run-codex` の完了後には、indexを自動再構築します。

### Phase3.4: Memory Retrieval for Answers

`scripts/build_answer_context.py` が、ユーザー質問に対して読み取り専用の回答用コンテキストを生成します。

```powershell
python scripts\build_answer_context.py "俺の好みに合う店は？"
```

動作:

* 私的な質問、好み、生活、学習進捗、過去行動、AI-LifeOSの過去方針に関係する質問だけでmemoryを使う
* memory context の要否は単一キーワード一致ではなく、自己参照、過去会話、好み・生活、AI-LifeOS/プロジェクト語などの重み付きスコアで判定する
* まず `memory/long_term.md` と `memory/preferences.md` を読む
* 質問から構造化メモリのカテゴリを推定できる場合は、`memory/items/*.md` の該当カテゴリを優先取得する
* 追加情報が必要な場合は `journal` と `summary.md` / `raw.md` を検索する
* 結果は短い抜粋と出典情報に絞る
* 会話中に `memory` / `journal` / `conversations` を編集しない

`scripts/codex_conversation.py` は、通常の会話返答生成時にこのコンテキストを自動で挿入します。無効化する場合:

```powershell
python scripts\codex_conversation.py --no-memory-context
```

現在性や外部情報が必要な質問では、ローカル記憶だけで断定せず、Web検索が必要なことを会話プロンプトに明示します。現時点のローカル実装自体はWeb検索クライアントを持ちません。

CLI と GUI は回答生成後に `記憶参照: あり / なし` を表示します。参照ありの場合は、回答時に使った参照元ファイルパス、件数、短い抜粋を保持し、必要に応じて確認できます。通常の assistant 返答本文には出典パスを混ぜず、参照情報はメタデータとして表示します。

### Phase3.5: Vector Search Evaluation

ベクトル検索は本番導入していません。評価結果は `docs/vector_search_evaluation.md` にまとめています。

結論:

* まずはMarkdown検索 + SQLite index + 日本語部分一致で運用する
* ベクトル検索は、キーワード検索で見つからない類義語・文脈検索が明確に必要になってから導入する
* OpenAI API直叩きや外部送信を前提にしない

### Phase3.6: Phase4 Planning Checkpoint

Phase4への引き継ぎは `docs/phase4_planning_checkpoint.md` にまとめています。

Phase4では、まずFilesystem / GitHub MCPなど、検索・記憶取得と相性がよく、個人情報リスクを管理しやすい連携から検討します。

## コマンド一覧

```powershell
python scripts\search_memory.py "検索語"
python scripts\search_memory.py "検索語" --rebuild-index
python scripts\search_memory.py "検索語" --json
python scripts\search_memory.py "検索語" --type journal
python scripts\search_memory.py "" --tag Phase3
python scripts\search_memory.py "" --type memory_item --category study_status --status active --tag 資格
python scripts\search_memory.py "検索語" --date 2026-07-05 --path conversations --profile
python scripts\index_conversations.py
python scripts\rebuild_index.py
python scripts\build_answer_context.py "質問"
python scripts\search_benchmark.py --compare-japanese
```

## 安全ルール

* 検索は読み取り専用。
* SQLite index は再生成可能な派生データ。
* `memory/search_index.sqlite3` はGit管理しない。
* 会話中に `memory` / `journal` を勝手に編集しない。
* 構造化メモリと動的カテゴリの更新は「整理して保存」時だけ行う。
* 回答に使った記憶が不確かな場合は「見つかった範囲では」と扱う。
* 出典パスは保持するが、通常回答では自然文に混ぜ、詳細を求められた場合だけ明示する。
