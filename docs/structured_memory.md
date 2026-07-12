# Dynamic Structured Memory

RT-0023では、会話ログに明示された継続的に有用な情報を、動的カテゴリ付きの1項目1Markdownとして保存・検索できるようにします。

## 保存領域とGit方針

個人データとしてGit管理しないもの:

```text
memory/items/*.md
memory/categories.json
memory/category_suggestions.md
```

公開できる定義・雛形・実装:

```text
config/memory_categories.example.json
templates/memory_item.md
scripts/memory_items.py
```

`.gitignore` は `memory/**` 全体を除外しています。SQLite indexもMarkdownから再生成できる派生データであり、Git管理しません。

## 既存記憶との役割分担

* `raw.md`: 出典となる会話全文。構造化メモリにない事実を補わない。
* `summary.md`: 1会話の要約、決定事項、次の作業、タグ。
* `journal`: 日付単位の事実記録。状態一覧の正本にはしない。
* `memory/long_term.md`: 長期的に重要な事実・方針を人間が読む総合メモ。
* `memory/preferences.md`: 好み、判断基準、回答スタイルの人間向け総合メモ。
* `memory/projects.md`: プロジェクト進捗の人間向け総合メモ。
* `memory/items/*.md`: 出典、カテゴリ、状態、確度、タグを持ち、個別に更新・検索する原子的な記憶項目。

既存の総合メモは廃止・自動削除しません。構造化項目への移行でも元データを削除せず、重複確認と手動確認を優先します。

## 項目形式

`templates/memory_item.md`を基準に、次のfront matterを必須とします。

* `id`
* `category` / `category_label`
* `status`
* `source` / `source_date`
* `confidence`
* `tags`
* `created_at` / `updated_at`

`scripts/memory_items.py`の`read_memory_item()`と`create_memory_item()`が読み書きと必須項目検証を行います。カテゴリ定義にまだ存在しないslugの項目も読み取れるため、カテゴリ定義が増減しても既存項目は壊れません。

## 動的カテゴリの安全な運用

実効カテゴリは、個人用`memory/categories.json`があればそれを使い、なければ公開用`config/memory_categories.example.json`を初期値として使います。

```powershell
python scripts\memory_items.py categories
python scripts\memory_items.py add-category --name health_status --label 健康状況 --description "継続的な健康関連の状況" --source conversations/YYYY/MM/session/raw.md
python scripts\memory_items.py propose-category --name health_candidate --label 健康候補 --reason "既存カテゴリとの境界が不明" --source conversations/YYYY/MM/session/raw.md
```

新カテゴリ追加ではlower snake_case、必須説明、会話出典、作成日時を保存し、同名または正規化後に同じラベルを拒否します。実行前にはAIもカテゴリ名・ラベル・説明を比較し、意味の近い既存カテゴリを優先します。

分類に迷う場合は、新カテゴリを確定しません。根拠が明確な項目だけ`uncategorized`へ保存するか、`memory/category_suggestions.md`へ`pending`提案として残します。

この更新は`prompts/codex_phase2_prompt.md`を使う「整理して保存」時だけ行います。live会話中、GUI操作中、検索中には更新しません。

## 検索と回答コンテキスト

indexには`category`、`category_label`、`status`、`source`、`source_date`、`confidence`、`tags`を保存します。

```powershell
python scripts\rebuild_index.py
python scripts\search_memory.py "" --type memory_item --category future_wishlist
python scripts\search_memory.py "" --category study_status --status active --tag 資格
python scripts\build_answer_context.py "安全確保支援士の学習状況を教えて"
```

カテゴリ・状態・タグはindex利用時にSQLite側で絞り込み、ランキングは従来どおりPython側の日本語部分一致を使います。`--no-index`でも同じ条件でMarkdownを直接検索できます。

`build_answer_context.py`はカテゴリ名、ラベル、代表的な言い換えからカテゴリ候補を推定します。推定カテゴリは本文キーワードが一致しなくても構造化メモリを先に取得し、`Structured Memory Matches`として通常のjournal・conversation検索より前に渡します。

## RT-0020 / RT-0022との境界

RT-0020では、合成データのみを使う長期ベンチマーク、`search_memory.py --profile`、SQLite metadata filter pushdownを実装しました。RT-0023では、その共通評価軸とカテゴリ代表クエリを使って検索品質を維持します。

評価軸:

* 正解カテゴリの関連項目を上位`max_results`内で取得できること。
* category / status / tagのAND絞り込みに無関係な項目が混ざらないこと。
* indexあり・`--no-index`でカテゴリ絞り込みの結果集合が一致すること。
* 回答コンテキストが記憶参照を起動し、構造化項目をjournal / raw / summaryより先に配置すること。
* RT-0020の`search_benchmark.py`で、文書数別にindex読込、SQL filter、Python ranking、全体時間を分けて測ること。

代表クエリ:

| 質問 | 期待カテゴリ | 確認点 |
| --- | --- | --- |
| やりたいことリストを見せて | `future_wishlist` | 本文語彙に依存せず一覧取得 |
| 家の状況を教えて | `home_status` | 状況カテゴリを優先 |
| 安全確保支援士の学習状況を教えて | `study_status` | 資格名から学習カテゴリを推定 |
| 未完了の候補タスクは？ | `candidate_task` | category + statusの絞り込み |
| 自分の回答スタイルの好みは？ | `preference` | 既存preferencesとの役割を維持 |

RT-0022は保留し、RT-0023ではFTS5を検索主経路にしません。現行の`SQLite-backed index + Python ranking`を維持し、FTS5採用判断はRT-0020の計測後に行います。
