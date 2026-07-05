# RT-0016 Conversation Branching Design Note

RT-0016 は、ChatGPT風の送信済みメッセージ編集、回答再生成、別の流れでの会話継続を AI-LifeOS に入れるかどうかを検討するための設計メモです。

現在の結論は保留です。今回は実装せず、RT-0010 の長期会話スレッド管理が固まった後に再評価します。

## 背景

現在の会話保存は、live JSONL を直線的なログとして保存し、必要に応じて `raw.md`、`summary.md`、`journal`、`memory` へ接続します。

```text
inbox/live/*.jsonl
↓
inbox/live/*.session.json
↓
conversations/YYYY/MM/YYYY-MM-DD_HHMMSS/raw.md
↓
summary.md / journal / memory
```

この構成では、途中のuser発言を編集したり、assistant返答を再生成したりすると、その時点以降の発言、要約、日記、長期メモリとの整合性が崩れます。

## 判断

RT-0016 では、既存ログを直接編集する方式は採用しません。

将来実装する場合は、分岐を「既存セッションの破壊的編集」ではなく「親セッションから派生した新しいセッション」として扱います。

採用候補:

```text
parent session
├─ original live JSONL
├─ original raw.md / summary.md
└─ derived session
   ├─ new live JSONL
   ├─ new session metadata
   └─ new raw.md / summary.md
```

この方針なら、既存の live JSONL、`raw.md`、`summary.md`、`journal`、`memory` を書き換えずに、ChatGPT風の編集・再生成に近い体験を作れます。

## 非破壊ルール

実装する場合は、以下を必須ルールにします。

* 既存の `inbox/live/*.jsonl` を書き換えない
* 既存の `.session.json` を破壊的に書き換えない
* 既存の `raw.md`、`summary.md`、`journal`、`memory` を分岐操作だけで更新しない
* 分岐は新しい live JSONL と新しい session metadata として作る
* 親セッションとの関係は metadata に記録する
* 分岐後の finalize は、派生セッションに対してだけ実行する
* memory / journal 更新は、ユーザーが明示的に「会話を整理して保存」した時だけ行う

## 用語

* 親セッション: 分岐元のセッション
* 派生セッション: 分岐後に作られる新しいセッション
* 分岐点: 編集または再生成の基準になるメッセージ
* ルートスレッド: RT-0010 で定義予定の、複数セッションを束ねる長期会話単位
* primary branch: ユーザーが現在の本流として扱うセッション

## 最小データモデル案

既存の `.session.json` に直接この形を入れるかは未決定ですが、将来は派生セッション側に親情報を持たせます。

```json
{
  "version": 2,
  "session_id": "2026-07-10_210000",
  "status": "saved",
  "title": "分岐後の会話",
  "jsonl_file": "inbox/live/2026-07-10_210000.jsonl",
  "root_thread_id": "thread_2026-07-01_ai-lifeos",
  "branch": {
    "kind": "message_edit",
    "parent_session_id": "2026-07-10_203000",
    "parent_jsonl_file": "inbox/live/2026-07-10_203000.jsonl",
    "fork_message_index": 4,
    "fork_message_timestamp": "2026-07-10T20:45:00+09:00",
    "fork_message_hash": "sha256:...",
    "source_role": "user",
    "created_at": "2026-07-10T21:00:00+09:00"
  }
}
```

既存ログには message id がないため、古いセッションから分岐する場合は `fork_message_index`、timestamp、content hash の組み合わせで分岐点を示します。将来の実装では、新規メッセージに `message_id` を付ける方が安全です。

## 派生セッションの作り方

MVPでは、派生セッションの live JSONL は直線ログとして作ります。

理由は、既存の `finalize_live_chat.py` が直線的な JSONL を `raw.md` に変換する前提だからです。

### メッセージ編集

送信済みuserメッセージを編集する場合:

1. 親セッションを変更しない
2. 編集対象より前のメッセージを派生セッションへコピーする
3. 編集後のuserメッセージを派生セッションへ新規メッセージとして保存する
4. 編集対象以降の親セッションのassistant返答や後続発言は派生セッションへコピーしない
5. 以後のassistant返答は派生セッション上で新規生成する

### 回答再生成

assistant返答を再生成する場合:

1. 親セッションを変更しない
2. 再生成対象のassistant返答より前のメッセージを派生セッションへコピーする
3. 同じ直前userメッセージに対して新しいassistant返答を生成する
4. 古いassistant返答と、それ以降の親セッションの発言は派生セッションへコピーしない

### 途中から別の流れで継続

任意のメッセージから別の流れで続ける場合:

1. 分岐点までのメッセージを派生セッションへコピーする
2. 以降は派生セッションとして通常の live JSONL 保存を続ける

## raw.md / summary.md / journal / memory の扱い

分岐後の派生セッションは、親とは別の会話として finalize します。

### raw.md

派生セッションを finalize した場合は、新しい `conversations/YYYY/MM/YYYY-MM-DD_HHMMSS/raw.md` を作ります。親セッションの `raw.md` は変更しません。

将来は `raw.md` のヘッダーに、分岐元セッションID、分岐種別、分岐点を記録できると追跡しやすくなります。

### summary.md

派生セッションの `summary.md` は親とは別に生成します。

summary には、可能であれば「この会話は親セッションから派生した」ことと、分岐後に決まったことを明記します。親セッションの `summary.md` は書き換えません。

### journal

派生セッションを明示的に整理して保存した場合だけ、journal へ新しい事実として追記します。

親セッションのjournal記録を後から修正しません。分岐が試行錯誤で、ユーザーが保存しなかった場合はjournalへ反映しません。

### memory

memory は、ユーザーが明示的に整理保存した派生セッションからのみ更新候補を出します。

親セッションから既に長期メモリが作られている場合でも、分岐操作だけで自動削除や自動修正はしません。矛盾が出た場合は、通常の memory 更新ルールに従って、根拠となる会話を明記した追記または手動修正で扱います。

## GUIでの見せ方

Phase2.7 GUI に将来入れる場合、ボタン名はユーザーに分かりやすくしても、内部処理はすべて派生セッション作成に寄せます。

候補:

* 「編集」: このメッセージから派生セッションを作成
* 「再生成」: このassistant返答の直前から派生セッションを作成
* 「別案で続ける」: この位置から派生セッションを作成

GUIは、親セッションと派生セッションを混ぜて1本のログに見せない方が安全です。最低限、現在表示している会話が派生セッションかどうかを分かるようにします。

## RT-0010との関係

RT-0016 は RT-0010 の長期会話スレッド管理に依存します。

必要な前提:

* session と thread の違いが定義されている
* 複数セッションを同じ root thread に紐づけられる
* どのセッションが primary branch かを表せる
* `raw.md` と `summary.md` が session 単位か thread 単位か整理されている
* memory 更新時に、どの session / branch を根拠にしたか追跡できる

RT-0010 が完了するまでは、RT-0016 は実装しません。

## 採用しない案

### 既存JSONLのインプレース編集

送信済みメッセージを既存JSONL内で書き換える方式は採用しません。

理由:

* raw.md / summary.md / journal / memory の根拠が失われる
* 既に finalize 済みの会話との整合性が壊れる
* SourceTree や git diff で変更内容を確認しづらくなる

### 1つのJSONL内に分岐グラフを埋め込む

1つのJSONLに `parent_message_id` などを入れて分岐グラフを持たせる方式は、初期MVPでは採用しません。

理由:

* 既存の finalize 処理が直線ログ前提
* GUI、検索、summary生成が一気に複雑になる
* 分岐したどの経路を journal / memory に反映するか判断しづらい

将来、thread-level renderer が必要になった場合の候補としては残します。

## 再評価条件

RT-0016 は、以下が揃った時に再評価します。

* RT-0010 長期会話スレッド管理が完了している
* session / thread / branch の用語と保存単位が定義されている
* raw.md / summary.md / journal / memory の根拠追跡ができる
* 派生セッションをGUIから安全に選択・保存できる
* 既存ログを破壊的に変更しない実装方針が維持できる

## 未解決リスク

* 派生セッションで親のメッセージをコピーすると、検索結果に重複が増える
* どの分岐を primary branch として扱うかのUIが必要になる
* 既に memory 化された内容と、後から作った派生セッションの内容が矛盾する可能性がある
* 既存ログには message id がないため、古い会話の分岐点特定は index / timestamp / hash に依存する
* thread 単位の要約を作る場合、session単位 summary との関係を別途定義する必要がある

