# Phase3.6 Phase4 Planning Checkpoint

Phase3.6 は、検索・記憶取得が一通り動いた後に、Phase4のMCP連携へ進むための認識合わせです。

その後Phase3.8で、ローカル保存済み記憶だけを扱う読み取り専用Memory MCPを実装しました。以下のPhase4候補は、外部サービスや汎用ファイル操作へ権限境界を広げる連携として引き続き別扱いです。

## Phase4で優先する候補

優先度が高いもの:

* Filesystem MCP: ローカルファイル検索・読み取りとの相性がよい。
* GitHub MCP: Issue / PR / commit 進捗を `memory/projects.md` と接続しやすい。
* Playwright MCP: GUIやWeb確認の自動化候補。

慎重に扱うもの:

* Gmail MCP
* Google Calendar MCP
* Discord MCP

これらは個人情報が多いため、最初から自動保存しません。読み取り範囲、保存範囲、確認ステップを設計してから扱います。

## Phase4.0で実装したNotion境界

RT-0024で追加した独自REST adapterはRT-0025で撤去し、`mcp-remote` OAuth bridge経由の公式Notion remote MCPへ置き換えました。ローカルMemory MCPとは統合せず、回答processだけで有効になる外部参照として分離しています。

* Notionチェックは既定OFFで、現在のセッションでは送信後も選択を維持する。手動OFFまたはセッション切替時にOFFへ戻し、OFFではNotion MCPを公開しない。
* OAuth credentialは`mcp-remote`の専用user-profile directoryで管理し、独自tokenとtarget allowlistを持たない。
* ONの回答だけ`search`、`fetch`、Notion database / data source queryを公開し、書き込みtoolを除外する。
* `search`は`workspace_search`へ固定し、connected sourceを対象にする`ai_search`は使わない。範囲をtool traceで確認できない回答は破棄する。
* MCP response本文は回答process内だけで利用し、本文cacheを作らない。
* 取得本文をmemory / journal / indexへ自動保存しない。
* 権限喪失、削除、失敗時は古い内容へfallbackせず、GUIへ失敗を示す。
* create / update / delete endpointをadapterから呼べないことをテストする。

詳細は [notion_read_only_integration.md](notion_read_only_integration.md) を参照してください。

## Phase3との境界

Phase3:

* ローカルに保存済みの会話・summary・journal・memoryを検索する
* 検索結果を回答用コンテキストにする
* SQLite indexを再構築可能な派生データとして扱う

Phase4:

* 外部ツールから情報を取得する
* 取得結果を保存するかどうかを判断する
* MCPや外部ツール連携の安全ルールを整備する

## Web検索との関係

Phase3.4の会話プロンプトでは、ローカル記憶だけで不足し、現在性や外部情報が必要な場合にWeb検索を補助手段として扱う方針を明記しています。

現時点のローカル実装はWeb検索クライアントを持ちません。Phase4で外部ツール連携を扱う時に、Web検索・MCP連携・保存ルールをまとめて設計します。

## 安全ルール

* 外部ツールから得た情報を、確認なしにmemoryへ自動保存しない。
* 個人情報が多い連携は、読み取り範囲を最小化する。
* 保存前に出典と内容を確認できる形にする。
* `memory/long_term.md`、`memory/preferences.md`、`memory/projects.md` の役割分担を崩さない。
* Git commit / push 前の個人情報・秘密情報確認ルールを維持する。

## 構造化メモリ

Phase4以降で外部連携を検討する前に、ローカル会話を根拠とする動的カテゴリ付き構造化メモリを導入しました。保存形式、既存メモリとの役割分担、カテゴリ提案、検索評価軸は [structured_memory.md](structured_memory.md) を参照してください。

外部ツールの取得結果を構造化メモリへ直接自動保存することは、現時点では対象外です。保存する場合は出典確認と「整理して保存」に相当する明示フローを別途設計します。
