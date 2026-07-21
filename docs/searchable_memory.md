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
* `indexed_sources`: 元Markdownのpath、mtime、sizeを保持する鮮度確認用マニフェスト

現時点の検索方式は `SQLite-backed index + Python ranking` です。SQLiteには全文とメタデータを保存し、検索時はSQLiteから対象文書を読み出して、Python側で日本語の部分一致ランキングを行います。

FTS5は環境によって日本語トークン化が弱いため、MVPでは検索品質を優先してPython側の一致判定を使います。`documents_fts` が作成される環境でも、現在の検索結果ランキングの主経路はFTS5ではありません。

### Metadata filter pushdown and search profile

SQLite indexを使う検索では、候補をPythonへ読み込む前に次の条件をSQLiteのパラメータ化クエリで絞り込みます。

* document type
* tag
* structured-memory category / status
* exact date、開始日、終了日
* relative-path substring
* project scopeのpath / title / tags / category / source / content一致

```powershell
python scripts\search_memory.py "検索語" --type summary --from-date 2026-01-01 --to-date 2026-12-31 --path conversations --profile
python scripts\search_memory.py "" --type memory_item --category study_status --status active --tag 資格 --profile --json
```

`--profile`は検索結果を変えず、次を表示します。

* `index load`: SQLite indexを開きschemaを確認する時間。`--no-index`ではMarkdown収集時間。
* `filter`: SQLite側の絞り込みクエリ時間。`--no-index`では同じ条件のPython絞り込み時間。
* `ranking`: 日本語部分一致のPython ranking時間。
* candidates / results: ranking前の候補数と返却件数。
* `index_status`: `fresh` / `legacy` / `missing` / `stale` / `unreadable` / `disabled`。
* `query_variants`: 依頼表現を除外した検索語と、一般的なtopic variantの一覧。
* `retrieval_mode`: 標準の `hybrid-lexical`、または将来のローカル意味rankerを併用する `hybrid-local`。

index構築後にMarkdownが追加・更新・削除された場合、検索はindexを書き換えません。`index_status=stale`とし、その回答中だけ現在のMarkdownを直接検索します。現行indexはsource manifestに加えてschema versionとraw metadata parser versionを保存します。version metadataがない／一致しないDBや`indexed_sources`を持たない旧DBは`index_status=legacy`として、旧parser由来のtitle / tagsをproject scope判定へ使わず、常に現在のMarkdownへfallbackします。indexの再構築は従来どおりfinalizeまたは明示コマンドの責務です。

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

* 毎回答で `memory/long_term.md`、`memory/preferences.md`、`memory/projects.md` から最大1,000文字のコア記憶だけを読み取り専用で渡す。日記全文・会話全文・無関係な項目は常時渡さない
* すべての質問で記憶検索を行う。通常の質問では、構造化メモリ・journal・summaryから関連度の高い短い抜粋を最大2件だけ読む「narrow」検索を使い、会話全文やrawチャンクは常時渡さない
* 自己参照、過去会話、好み・生活、AI-LifeOS/プロジェクト語などの重み付きスコアは、検索する/しないの閾値に使わない。すべての非空質問を検索対象とし、スコアは narrow / deep の検索深度にだけ使う
* `俺` / `おれ` / `オレ` / `私` / `わたし` / `僕` / `ぼく` / `自分` を同じ自己参照として扱い、短いフォローアップでは直近2件までのuser発話を補助にする
* 自己情報の短い質問や、保存済みの検索取りこぼしと確認済みのパターンでは、少数件だけのフォールバック検索を行う。フォールバックは検索開始の判断にだけ使い、回答の根拠にはしない
* 質問から構造化メモリのカテゴリを推定できる場合は、`memory/items/*.md` の該当カテゴリを優先取得する
* 追加情報が必要な場合は `journal` と `summary.md` / `raw.md` を検索する
* 過去会話の事実照合では具体語を優先して再検索し、未整理の `inbox/live/*.jsonl` も読み取り専用で確認する。ただし現在回答中のlive JSONLは「過去の記憶」として自己検索せず、日時はAsia/Tokyoの日付で表示する
* `教えて` / `なんだっけ` などの依頼表現は検索語から除外し、一般的なトピック抽出・query variantと日本語文字trigramをOR候補とし、reciprocal-rank fusionで統合する。個人の作品・人物などを結び付ける固定辞書は持たず、語彙が一致しない場合はMemory MCPが検索語を変えて反復検索する
* ユーザー自身の感想・好みを尋ねる場合はuser発言を一次根拠とし、後の「記録を確認できない」という取得失敗回答を正解として再利用しない
* 結果は短い抜粋と出典情報に絞る
* 会話中に `memory` / `journal` / `conversations` を編集しない

`scripts/codex_conversation.py` は、通常の会話返答生成時にこのコンテキストを自動で挿入します。無効化する場合:

```powershell
python scripts\codex_conversation.py --no-memory-context
```

このオプションは静的contextとMemory MCPの両方を無効化します。MCPだけを無効にする場合は`--no-memory-mcp`を使います。

現在性や外部情報が必要な質問では、ローカル記憶だけで断定せず、Web検索が必要なことを会話プロンプトに明示します。現時点のローカル実装自体はWeb検索クライアントを持ちません。

CLI と GUI は回答生成後に、回答生成へ渡した静的context、MCPが列挙した検索候補、MCPで実際に`open_conversation`した一次資料を別々に保持・表示します。候補ありの場合は、取得元ファイルパス、件数、短い抜粋を必要に応じて確認できます。検索で列挙しただけの候補を「open済み」または「最終回答に使用済み」とは扱いません。通常の assistant 返答本文には出典パスを混ぜず、取得情報はメタデータとして表示します。

表示には、コア記憶のみを使ったか、narrow検索、通常検索またはフォールバック検索を追加したかも含めます。検索取りこぼしへの訂正が保存済み記録で確認できた場合だけ、会話のfinalize後に `memory/retrieval_feedback.jsonl` へ正規化済み特徴量と結果を派生データとして保存します。会話本文・回答本文・個人情報の値は保存せず、live会話中やGUI送信中には更新しません。

`AnswerContext.retrieval_health` は、コア記憶件数、構造化メモリ件数、過去会話件数、index鮮度、Markdown fallback、query variantsを別々に保持します。`include_core_memory` と `include_past_chats` は独立して無効化でき、`project_scope` 指定時はpath、title、tags、category、source、本文のスコープ一致をranking前に必須にします。固定コア記憶もファイル全体ではなく、scope名を持つMarkdownセクションまたは一致行だけを取得します。一致が0件でも無関係なプロジェクトへ自動で広げません。

### Phase3.7 / 3.9: Retrieval correctness and hybrid lexical search

固定閾値による検索ON/OFFは廃止しました。Phase3.9のMVPは外部APIやvector DBを追加せず、完全一致、複数query variant、保守的な文字trigram、RRFを使います。`LocalSemanticBackend` は、将来、metadata filter後の文書に対して完全ローカルなembedding rankerを差し込むためのinterfaceだけを定義しています。標準実行ではbackendを読み込まず、新しい依存も外部送信もありません。

### Phase3.8 / 3.10: Agentic retrieval and personalization

Codexは読み取り専用Memory MCPを使い、初回0件時に検索語を変えて再検索し、`open_conversation`で一次発言を確認できます。長期memoryと過去チャット検索は独立して無効化でき、project scope、一時チャット、取得元・retrieval healthをChat GUIから管理・確認できます。詳細は [memory_mcp.md](memory_mcp.md) と [personalization.md](personalization.md) を参照してください。

### 会話発言の役割付き参照

`raw.md` の `## User` / `## Assistant` 発言は、検索indexで `raw_chunk` として役割（`user` / `assistant`）と発言順を保持します。回答用memory contextは、関連するユーザー発言だけでなく、対応するAI応答も役割ラベル付きで参照します。

* 「自分が何と言ったか」の照会では、user発言だけを根拠にします。
* AIの回答・説明・結論を尋ねる照会では、対応するassistant応答を根拠にします。
* 過去会話全体の照会では、対応するuser / assistant発言を役割付きで時系列順に参照します。

この仕組みはChatGPT exportから取り込んだ会話と、AI-LifeOSのLive Conversation / Chat GUIから保存された会話の両方に適用されます。ChatGPT exportで個別発言の時刻が欠けていても、`## User` / `## Assistant` の見出しと出現順から役割情報を保持します。役割情報はSQLite indexの派生データなので、`python scripts\rebuild_index.py`で再生成できます。

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
* live fallbackは `.session.json` の `personalization.temporary` / `exclude_from_memory` を最優先し、該当セッションを過去会話証拠として絶対に返さない。存在するmetadataを読めない場合もfail-closedで除外する。
* SQLite index は再生成可能な派生データ。
* `memory/search_index.sqlite3` はGit管理しない。
* 会話中に `memory` / `journal` を勝手に編集しない。
* 構造化メモリと動的カテゴリの更新は「整理して保存」時だけ行う。
* 回答に使った記憶が不確かな場合は「見つかった範囲では」と扱う。
* 出典パスは保持するが、通常回答では自然文に混ぜ、詳細を求められた場合だけ明示する。
