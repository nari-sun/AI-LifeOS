# RT-0017 Streaming Response UI

RT-0017 は、Chat GUI で assistant 返答の生成途中の文章を表示できるようにするためのチケットです。

## Status

Backlog. まだ実装しません。

現在の `codex.cmd exec --output-last-message` 方式では、Codex の実行完了後に最終返答だけを受け取ります。そのため、GUI は生成中の文章を token 単位または chunk 単位で表示できません。

## Background

Phase2.7 Chat GUI MVP では、GUI から `scripts/chat_gui_bridge.py` を呼び、bridge が `scripts/codex_conversation.py` の `generate_assistant_reply()` を通じて `codex.cmd exec` を実行しています。

現在の流れ:

```text
User message
-> live JSONL に保存
-> codex.cmd exec を実行
-> Codex 完了後に --output-last-message の内容を読む
-> assistant message として live JSONL に保存
-> GUI に返す
```

この方式では、以下は見えません。

- assistant 返答の生成途中の文章
- キャンセル時点までに生成されていた部分返答
- Codex 実行中に外へ出力されない内部試行

## Goal

GUI で assistant 返答の生成途中の可視テキストを表示します。

最小ゴール:

- 返答生成中に、ユーザーが「今どこまで文章が出ているか」を見られる
- 完了時は従来通り、確定した assistant 返答だけを `inbox/live/*.jsonl` に保存する
- キャンセル時の部分出力を保存するか破棄するかを明確に決める
- 既存の `raw.md` / `summary.md` / `journal` / `memory` の整合性を壊さない

## Non-Goals

RT-0017 では以下を対象外にします。

- モデル内部の非公開推論を表示すること
- chain-of-thought を保存すること
- `memory/long_term.md` や `journal` を会話中に更新すること
- ChatGPT 公式Webや公式デスクトップアプリをスクレイピングすること
- OpenAI API の直接利用を前提にすること
- 生成途中の未確定文章を自動で長期メモリ化すること

## Design Direction

現実的な実装候補は2つあります。

### Option A: Codex SDK / app-server に置き換える

Codex SDK または `codex app-server` のイベントストリームを使い、assistant 返答の差分を GUI に送ります。

想定フロー:

```text
desktop/app
-> Tauri command
-> chat_gui_bridge.py
-> Codex SDK / app-server streaming event
-> GUI に partial text を逐次送信
-> 完了後に final assistant message を live JSONL に保存
```

利点:

- ChatGPT 風の自然なストリーミング表示に近い
- キャンセル処理と相性がよい
- 将来の永続スレッド化にもつながる

懸念:

- 現在より bridge が複雑になる
- JSON-RPC / SDK イベント処理のテストが必要
- Codex CLI のバージョン差分に影響される可能性がある

### Option B: 現行 exec の stdout/stderr を監視する

`codex.cmd exec` の標準出力を監視して、表示できる進捗を拾う案です。

ただし現行方式では、最終返答本文は `--output-last-message` で完了後に取得しています。stdout/stderr は進捗ログであり、assistant 返答本文の安定したストリームとは限りません。

結論:

- 本命ではない
- 短期の進捗表示には使える可能性がある
- assistant 本文のストリーミング表示としては採用しない

## Data Handling

生成途中の文章は、確定した assistant message とは別物として扱います。

推奨:

- UI state: 生成中の一時表示
- live JSONL: 完了後の確定 assistant message のみ保存
- cancel 時: デフォルトでは部分出力を live JSONL に保存しない

将来、部分出力を監査用に残す場合は、通常の会話ログとは別の派生ファイルにします。

候補:

```text
logs/chat_gui_stream/YYYY-MM-DD_HHMMSS_<request_id>.jsonl
```

この派生ログは `raw.md` 化や memory 更新の根拠には使いません。

## UI Requirements

- assistant bubble を生成中状態で表示する
- chunk が届くたびに bubble の本文を更新する
- 生成中は停止ボタンを表示する
- 停止後は「停止しました」と分かる状態にする
- 完了後は通常の assistant message として扱う
- エラー時は、保存済み user message を残したまま、assistant 側は未保存として表示する

## Backend Requirements

- `send-message` が一括応答だけでなく、streaming mode を扱える
- Tauri 側が Python bridge からのイベントを逐次受け取れる
- request id ごとに cancel と stream を対応づける
- 完了イベントを受け取るまで live JSONL に assistant message を確定保存しない
- 既存の非ストリーミング経路を fallback として残す

## Acceptance Criteria

- GUI で assistant 返答が生成途中から表示される
- 生成完了後、確定 assistant 返答が従来と同じ live JSONL 形式で保存される
- キャンセル時に、未確定の部分出力が live JSONL に混ざらない
- 既存の session resume / finalize / memory processing が壊れない
- `python -m unittest` が通る
- GUI の手動確認で、送信、ストリーミング表示、停止、エラー表示が確認できる

## Dependencies

- Codex SDK または `codex app-server` の採用判断
- Tauri command から Python bridge の streaming event を扱う方式の確認
- RT-0008 の停止処理との整合性
- RT-0010 の長期会話スレッド管理とは独立して進められるが、将来の統合時に再確認する

## Risks

- 部分出力を保存すると、未確定の文章が `raw.md` / summary / memory に混ざる危険がある
- Codex 側のイベント形式が変わると bridge が壊れやすい
- ストリーミング表示とキャンセル処理の競合で、UI と保存済み JSONL が不一致になる可能性がある
- 内部推論を見せる機能だと誤解されやすい

## Decision Notes

RT-0017 では「生成途中に外へ出力された assistant 返答本文」を扱います。モデル内部の非公開推論や chain-of-thought は対象にしません。

初期実装では、生成途中の文章は UI 表示専用にし、保存対象は完了後の final assistant message だけにします。
