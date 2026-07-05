# Phase3.6 Phase4 Planning Checkpoint

Phase3.6 は、検索・記憶取得が一通り動いた後に、Phase4のMCP連携へ進むための認識合わせです。

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
