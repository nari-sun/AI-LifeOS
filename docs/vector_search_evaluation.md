# Phase3.5 Vector Search Evaluation

Phase3.5 では、ベクトル検索をすぐ導入せず、SQLite検索で不足する理由が明確になった場合の候補を評価します。

## 現時点の結論

当面はベクトルDBを本番導入しません。

理由:

* 保存済み会話の件数が少ない段階では、Markdown検索とSQLite indexで十分に確認できる。
* 日本語の固有名詞、日付、プロジェクト名はキーワード検索と相性がよい。
* ベクトル検索はembedding生成、再構築、モデル選定、バックアップ、個人情報管理が増える。
* OpenAI API直叩きを前提にしない方針と、外部送信しない方針を維持したい。

## 候補比較

| 候補 | 長所 | 懸念 | 現判断 |
| --- | --- | --- | --- |
| SQLiteVec | SQLiteに寄せられる。単一ファイル運用と相性がよい。 | Windows環境での導入・バイナリ依存を確認する必要がある。 | 最有力候補。ただし未導入。 |
| LanceDB | ローカル運用しやすく、検索品質を上げやすい。 | 依存が増え、既存SQLite運用より重い。 | 候補として保留。 |
| Chroma | 情報が多く試しやすい。 | 運用コンポーネントが増えやすい。 | 実験候補。 |
| Qdrant | 本格的なベクトル検索に強い。 | AI-LifeOS初期運用には過剰。 | 後回し。 |

## 導入判断基準

以下が起きたら再評価します。

* 「言い換え」や「似た話題」がキーワード検索で見つからないケースが増える。
* 会話ログが増えて、日付・タグ・キーワードだけでは候補を絞りにくくなる。
* ローカルembedding生成の安全な方法が決まる。
* SQLite indexだけでは回答用コンテキストの品質が明確に不足する。

## RT-0020の比較方法

検索経路を変更する前に、合成データだけを使って次を計測します。

```powershell
python scripts\search_benchmark.py --sizes 100,1000,5000 --runs 7 --compare-japanese
```

出力はmetadata filter付きのSQLite検索について、index読込、SQL filter、Python ranking、全体時間を分けます。日本語の候補取得は、現行Python部分一致、SQLite `LIKE`、一時bigram補助table、利用可能ならSQLite標準FTS5 tokenizerの候補件数と時間を比較します。bigram tableはベンチマーク用の一時DBだけで使い、本番schemaには追加しません。

品質は速度だけで判定しません。`tests/test_phase3_memory.py`の代表クエリで、期待する構造化メモリ・journal・raw会話証拠が回答コンテキストに入ることを確認します。RT-0022でFTS5を主経路候補にするには、同じ代表クエリでPython baselineより候補漏れがなく、速度面の利点もベンチマークで確認できることが必要です。

この比較でSQLite検索が十分な品質と速度を保つ限り、ベクトル検索は導入しません。類義語・文脈検索の候補漏れが継続的に確認され、SQLite/FTS5の改善でも解消できない場合だけ、ローカルembeddingを含むベクトル検索を再評価します。

## 導入する場合の条件

* 個人情報を外部サービスへ送らない。
* OpenAI API直叩きを前提にしない。
* 元Markdownから再構築できる。
* DBファイルはGit管理しない。
* 既存の `search_memory.py` / `build_answer_context.py` の入出力を壊さない。
