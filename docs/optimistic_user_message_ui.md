# RT-0018 Optimistic User Message UI

RT-0018 は、Chat GUI でユーザーが送信した文章を assistant 返答完了前に即表示するためのチケットです。

## Status

Backlog. まだ実装しません。

現在の GUI は、ユーザーが送信した直後に入力欄を空にして生成中表示へ切り替えますが、会話欄の `messages` は `sendMessage()` の完了後に更新します。そのため、ユーザー発言と assistant 返答がほぼ同時に表示されます。

バックエンドでは user 発言は Codex 呼び出し前に live JSONL へ保存されています。問題は保存順序ではなく、GUI の表示タイミングです。

## Background

現在の `desktop/app/src/App.tsx` の流れ:

```text
submitMessage()
-> input を空にする
-> busy = generating
-> sendMessage(session, content, requestId) を await
-> bridge が user 発言を保存
-> Codex が assistant 返答を生成
-> bridge が assistant 返答を保存
-> result.messages を GUI に返す
-> setMessages(result.messages)
```

このため、生成中の画面では以下の状態になります。

- 入力欄は空
- 生成中 row は表示される
- 送信した user bubble はまだ表示されない

## Goal

ユーザーが送信した文章を、送信直後に chat timeline へ表示します。

最小ゴール:

- Enter または送信ボタン押下直後に user bubble が表示される
- assistant 生成中 row は user bubble の下に表示される
- bridge 完了後は、保存済み `result.messages` と同期する
- エラーまたはキャンセル時も user bubble が消えない
- live JSONL の保存ルールは変更しない

## Non-Goals

RT-0018 では以下を対象外にします。

- assistant 返答のストリーミング表示
- assistant 部分出力の保存
- 送信済みメッセージ編集
- 回答再生成
- 会話分岐
- live JSONL の形式変更
- `memory` / `journal` / `conversations` の会話中更新

assistant 返答の生成途中表示は RT-0017 で扱います。

## Design Direction

送信直後に、GUI local state へ仮の user message を追加します。

想定:

```ts
const optimisticMessage = {
  role: "user",
  content,
  timestamp: new Date().toISOString(),
  pending: true
}

setMessages((current) => [...current, optimisticMessage])
```

ただし既存の `ChatMessage` 型へ `pending` を足すか、UI 内部専用の型を作るかは実装時に決めます。

bridge から `result.messages` が返ったら、仮メッセージを含む local state を authoritative な保存済みメッセージで置き換えます。

```text
optimistic user bubble
-> bridge response
-> setMessages(result.messages)
```

## Error Handling

assistant 生成が失敗した場合でも、bridge 側では user 発言が保存済みのため、`result.messages` には user 発言が含まれる想定です。

必要な扱い:

- bridge が成功レスポンスを返し、`result.error` がある場合: `setMessages(result.messages)` で同期する
- invoke 自体が失敗した場合: optimistic user bubble を残すか、保存状態不明として表示する
- request id が古い場合: stale response で現在の timeline を壊さない

invoke 自体が失敗した場合の表示は、初期実装では user bubble に「保存状態未確認」の見た目を付けるか、notice/error で明示します。

## UI Requirements

- 送信直後に user bubble を表示する
- pending 状態の user bubble は通常表示より少し薄くするか、保存中状態を分かるようにする
- assistant 生成中 row は pending user bubble の後ろに表示する
- 完了後は pending 表示を消す
- エラー時は「入力を保存済みか確認中 / 保存状態未確認」が分かる
- 既存の「直前入力を復元」ボタンと矛盾しない

## Backend Requirements

基本的には不要です。

必要になった場合のみ、bridge response に以下のような情報を追加します。

```json
{
  "saved_user": {
    "role": "user",
    "timestamp": "...",
    "content": "..."
  }
}
```

ただし初期実装では、既存の `result.messages` だけで同期できるかを優先して確認します。

## Acceptance Criteria

- 送信直後に user bubble が会話欄へ表示される
- assistant 生成中 row が user bubble の下に表示される
- assistant 返答完了後、保存済み message list と表示が一致する
- assistant 生成失敗時も user 発言が消えない
- キャンセル時も user 発言が消えない
- 同じ user 発言が二重表示されない
- session resume / finalize の挙動が変わらない
- フロントエンドのビルドまたは既存テストが通る

## Dependencies

- 既存の `sendMessage()` 一括応答方式
- RT-0008 の停止処理
- RT-0017 とは独立して実装できる

## Risks

- optimistic message と `result.messages` の同期に失敗すると二重表示になる
- invoke 失敗時に、実際には保存済みなのか未保存なのか GUI だけでは判断しづらい
- 送信直後の timestamp と保存済み timestamp が微妙に違うため、key 設計が雑だと再描画でちらつく

## Decision Notes

RT-0018 は表示タイミングだけを直すチケットです。保存順序や会話ログ形式は変えません。

ユーザー体験としては、送信した文章がすぐ画面に残ることを優先します。assistant 返答の途中表示は RT-0017 の範囲に残します。
