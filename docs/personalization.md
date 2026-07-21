# Phase3.10 パーソナライズ管理

## 目的

回答生成へローカル記憶候補を渡すかをユーザーが明示的に制御し、取得した候補と理由を確認できるようにします。候補が最終文面に採用されたかとは区別します。会話中に `memory` / `journal` を勝手に更新しない既存方針は維持します。

## 保存場所

全体設定は `memory/personalization_settings.json` に保存します。`memory/` は個人データ領域でありGit管理しません。ファイルがない場合は次の初期値を読み取りますが、読み取りだけではファイルを作りません。

```json
{
  "memory_enabled": true,
  "past_chat_search_enabled": true,
  "project_scope": null
}
```

ファイルを書き込むのはGUIの「既定値を保存」など、明示的な更新操作だけです。更新は同じディレクトリの一時ファイルへ書いてから置き換えます。新規セッション開始時に既定値をセッション側へ固定し、その後に全体の既定値を変えても再開セッションを暗黙に上書きしません。`send-message`へsession pathを渡さずbridgeがセッションを暗黙作成する経路でも、作成時点の全体既定値をsidecarへ同じようにsnapshotします。

セッション固有の状態は `inbox/live/<session>.session.json` の `personalization` に保存します。

```json
{
  "personalization": {
    "version": 1,
    "temporary": false,
    "exclude_from_memory": false,
    "memory_enabled": true,
    "past_chat_search_enabled": true,
    "project_scope": "AI-LifeOS"
  }
}
```

設定値やproject scopeはログへ出しません。bridgeログには更新の有無、セッションID、temporaryの真偽だけを残します。

## 設定の意味

### 長期メモリを使う

`long_term.md`、`preferences.md`、`projects.md`、`memory/items/*.md` を回答の根拠に使うことを許可します。

### 過去チャットを検索する

明示的な過去照会に対し、raw会話やlive JSONLを読み取り専用で検索することを許可します。

2つの設定は独立して適用します。長期メモリがONならcore memoryを静的contextへ含め、過去チャット検索がONならpast-chat contextと読み取り専用Memory MCPを有効にします。両方がOFFなら、静的memory contextもMemory MCPも回答生成へ渡しません。

### Project scope

最大120文字の表示・検索スコープです。空欄は全体スコープです。改行、制御文字、非文字列と予約語`all`は拒否します（全体は空欄で指定します）。指定時は静的memory contextとMemory MCPの両方で同じscopeを厳格に適用します。scopeに一致する記録が0件でも全体検索へfallbackせず、別プロジェクトの記憶を混ぜません。

### 一時チャット

一時チャットでは次を強制します。

- 既存memory contextを使わない。
- Memory MCPを公開しない。
- `exclude_from_memory: true` をセッションmetadataへ明示する。
- GUIの個別整理・一括整理対象から除外する。
- live JSONLとセッションmetadataは既存の長期保持方針どおり保持する。

通常／一時の区分は最初の発言より前なら双方向に変更できます。一時へ切り替えた時点で長期メモリと過去チャット検索の実効値はOFFになりますが、切り替え前のセッション設定をmetadata内に退避し、発言前に通常へ戻した場合はその設定を復元して`exclude_from_memory`も解除します。退避値は一時チャット中の検索を有効にするものではありません。

発言保存後や整理開始後は、通常から一時、一時から通常のどちらにも変更できません。途中変更で既に作成済みのrawや以前の発言が境界をまたぐ事故を防ぐためです。別の区分が必要なら新規セッションを作成します。`finalize_live_chat.py`を直接呼んだ場合もtemporaryまたは`exclude_from_memory`を下位層で拒否し、live JSONLを保持します。

CLIでは次のように開始できます。

```powershell
python scripts\codex_conversation.py --temporary
python scripts\codex_conversation.py --project-scope AI-LifeOS
```

## GUI

サイドバーの「管理 > パーソナライズ」で次を操作・確認できます。

- 新規セッションに使う全体既定値のON/OFFとproject scope
- 現在セッション専用のON/OFF、project scope、一時チャット指定（既定値とは別に保存）
- `long_term.md` / `preferences.md` / `projects.md` の読み取り専用プレビュー
- `memory/items/*.md` の読み取り専用プレビュー（最大100件）

回答メッセージの「記憶取得」詳細には、回答生成へ渡した静的context、MCPが列挙した検索候補、MCPで実際にopenした一次資料を分けて表示します。それぞれ参照パス、document type、speaker role、message番号、snippetを確認でき、検索候補すべてを「open済み」や「最終回答で使用した根拠」とは表示しません。さらにindex状態と理由、Markdown fallback、検索深度、core/structured/pastのhit数、検索語variant、適用project scopeを表示し、「なぜ取得できたか／できなかったか」を確認できます。bridgeから返す理由と検索語は件数・文字数を制限し、ログには本文を残しません。プレビューは固定された `memory` 配下だけを読み、各本文に表示上限を設けています。

送信時はuser発言をlive JSONLへ保存した後に、そのセッションへ固定された設定を読み直して回答生成へ渡します。これにより、最初の発言と同時に設定が確定する境界で古い既定値を使いません。GUI側もパーソナライズ要求へ世代番号とsession pathを持たせ、別セッションへ切り替わった後に古い読み込み／保存応答が返っても現在画面を上書きしません。

## Python API

`scripts/personalization_settings.py` の主要API:

- `load_personalization_settings(root)`
- `update_personalization_settings(root, ...)`
- `load_session_personalization(root, session_file)`
- `update_session_personalization(root, session_file, ...)`
- `build_memory_summary(root)`

セッションパスはAI-LifeOS rootの `inbox/live` 直下にある `.jsonl` だけを受け付けます。

GUI bridgeコマンド:

- `get-personalization`
- `update-personalization`
- `get-memory-summary`

## 確認

```powershell
python -m unittest tests.test_personalization_settings
cd desktop\app
npm run build
```
