# Phase4.0 Notion Read-only Chat Integration

RT-0024 では、Chat GUI の回答時に、ユーザーが明示的に許可した Notion page / data source を読み取り専用で一時参照できるようにしました。Phase3 のローカル Memory MCP とは別の外部参照アダプターです。

## 決定事項

* 接続方式: Notion公式REST API
* API version: `2026-03-11`
* 認証: Notion internal integration token
* token保存: Windows Credential Managerの `AI-LifeOS/Notion`
* `.env`: 使用しない
* OpenAI API: 使用しない
* 参照制御: `config/notion_settings.json` のallowlist
* 取得方式: 回答ごとの都度取得
* 本文cache: 作らない
* チェック既定値: OFF
* チェック保持: 同じGUIセッションを表示している間。別セッションへ切り替えたときと再起動時はOFFへ戻す
* 一時チャット: 参照可能。ただし取得本文は保存せず、生成されたassistant回答だけは通常どおりlive JSONLへ残る

## 読み取り専用境界

`scripts/notion_integration.py` が許可するAPI呼び出しは次だけです。

| Method | Endpoint | 用途 |
| --- | --- | --- |
| `POST` | `/v1/search` | integrationへ共有済みのpage / data source一覧を設定画面用に取得 |
| `GET` | `/v1/pages/{page_id}` | 許可pageのmetadata/propertyを取得 |
| `GET` | `/v1/blocks/{block_id}/children` | 許可pageまたは許可data source内pageの本文blockを取得 |
| `GET` | `/v1/data_sources/{data_source_id}` | 許可data sourceのmetadata/schemaを取得 |
| `POST` | `/v1/data_sources/{data_source_id}/query` | 許可data sourceのrow pageを読み取りquery |

`POST`を使う2 endpointは検索／queryであり、作成・更新ではありません。page、block、database、data sourceに対するcreate / append / update / delete endpointはadapterのallowlistに存在せず、呼び出そうとするとnetwork送信前に拒否します。

Notion integration側でも `Read content` だけを有効にしてください。insert / update capabilityは不要です。アプリ側のendpoint制限とNotion側capabilityの両方で境界を作ります。

## 初期設定

### 1. Notion connectionを作る

Notionのconnection設定からinternal integrationを作り、capabilityは `Read content` だけを有効にします。参照させるpage / databaseをNotionで開き、`Add connections` から作成したconnectionへ共有します。

data sourceを使う場合、Notionの `Manage data sources` からdata source IDを確認できます。GUIの一覧はNotion Search APIから取得するため、共有直後に表示されない場合は少し待ってから更新してください。直接connectionへ共有した対象が優先です。

### 2. tokenをWindows Credential Managerへ保存する

repository、設定JSON、会話ログ、command line引数へtokenを書きません。対話入力で保存します。

```powershell
python scripts\notion_integration.py credential set
```

入力内容は画面へ表示されません。状態だけ確認する場合:

```powershell
python scripts\notion_integration.py credential status
```

接続と共有対象をCLIで確認する場合:

```powershell
python scripts\notion_integration.py connection
```

### 3. GUIでallowlistを保存する

1. Chat GUIの「管理 > Notion連携」を開く。
2. 「接続・対象一覧を更新」を押す。
3. 回答で参照してよいpage / data sourceだけをONにする。
4. 必要なら表示名と用途メモを編集する。
5. 「allowlistを保存」を押す。

実設定は `config/notion_settings.json` へ保存します。このファイルは個人のpage名、ID、用途を含み得るためGit管理外です。公開用の形式例は `config/notion_settings.example.json` です。設定一覧は保存可能な形で最大200件に制限し、有効な既存設定を優先します。自動backupは作りません。backupする場合もtokenとは分け、利用者が保護した場所へ手動で保存してください。

tokenを削除した後や接続障害中でも、安全側の操作として既存targetをすべてOFFにするローカル保存は可能です。targetをONにする操作だけは、その時点のNotion接続で対象を再確認できる必要があります。

## チャットでの使い方

投稿欄の上にある「Notionを参照する」をONにした送信だけ、Notion adapterを呼びます。

OFFの場合:

* Credential Managerを読まない。
* Notion APIへ接続しない。
* 既存の長期memory、過去チャット検索、Memory MCPだけで回答する。

ONの場合:

1. 有効allowlistから質問と表示名／用途が近いtargetを最大4件選ぶ。
2. pageはpropertyとblock childrenを読み取る。
3. data sourceは直近20 rowをqueryし、質問との文字一致で最大5 pageへ絞って本文を読む。
4. 合計18,000文字までをNotion専用contextとしてCodex CLI / app-serverの回答生成へ渡す。
5. page名、URL、取得時刻を回答詳細へ返す。

Notion Search APIはworkspace全文検索や完全な列挙を保証するものではありません。data sourceのrow検索も初期実装では直近20件を対象とするbounded retrievalです。大規模data sourceで必要なrowが出ない場合は、対象をpageとして個別共有するか、将来のfilter設計を別チケットで扱います。

Notion本文は外部入力として扱います。本文内に命令、tool要求、秘密情報の開示指示があっても従わず、回答の根拠データとしてだけ使うよう会話プロンプトで指示します。本文をAI-LifeOSのローカルファイルへ保存しないことと、回答生成のためCodex側へ送信・処理されることは別の境界です。秘匿性が高くCodexへ渡したくないtargetではチェックをONにしないでください。

## allowlist enforcement

* `enabled=true` のtargetだけを回答時の候補にする。
* GUI保存時、現在のconnectionから取得できないtargetは有効化できない。
* pageは設定されたpage IDだけを直接取得する。
* page内の `child_page` / `child_database` はタイトル表示までで止め、親pageの取得から子本文へ再帰しない。子本文を参照するには、その子page / data source自体を別targetとしてallowlistで有効にする。
* data sourceは設定されたdata source IDだけをqueryし、そのrow pageだけを読む。
* 未許可target IDを質問文やNotion本文から追加しない。
* `in_trash`、404、403、object type不一致は本文なしの失敗として扱う。
* 権限喪失／削除済みtargetについて、以前の本文cacheへfallbackしない。

## 取得上限と停止

既定値:

| 項目 | 上限 |
| --- | ---: |
| 1回答のtarget | 4 |
| data sourceの取得row | 20 |
| data sourceから本文を読むpage | 5 |
| 1 targetの本文 | 6,000文字 |
| 合計本文 | 18,000文字 |
| block再帰depth | 2 |
| API request | 30 |
| 1 request timeout | 8秒 |
| 取得全体timeout | 20秒 |
| 設定画面の共有target一覧 | 200件 |

Notion APIの429では `Retry-After` が2秒以内で全体deadline内の場合だけ、各requestを最大1回再試行します。設定画面の一覧取得にも20秒の全体deadlineとrequest上限を適用します。GUIの回答停止ではrequest間でcancelを確認し、実行中のHTTP requestは最大8秒のtimeoutを待ちます。

## 失敗時

token未登録、接続失敗、401 / 403、404、rate limit、timeout、削除済みtargetでは、古い本文を使いません。

* 取得できたtargetが0件: Notion contextなしでローカル回答を続け、GUIへ「Notionを参照できなかった」と表示する。
* 一部だけ成功: 成功した許可targetだけをcontextにし、失敗targetをGUIへ表示する。
* 取得成功: 回答詳細に許可target、page/data source名、リンク、取得時刻を表示する。

API error body、token、取得本文、質問本文はGUIログへ出しません。ログはstatus、件数、session IDだけです。

## 保存境界

| データ | 保存 |
| --- | --- |
| Notion installation token | Windows Credential Managerだけ |
| allowlist ID、表示名、用途、最終取得時刻・状態 | `config/notion_settings.json`（Git管理外） |
| 取得したpage / row本文 | ローカル保存しない。回答生成中にCodexへ渡す一時contextだけ |
| 本文cache | 作らない |
| user発言 | 従来どおりlive JSONLへ保存 |
| assistant回答 | 従来どおりlive JSONLへ保存 |
| 回答詳細のNotion source metadata | GUIの現在表示だけ。live JSONLへsidecar保存しない |

assistant回答には、Notion由来の短い要約、page名、リンクが含まれる場合があります。その回答は通常の会話ログへ残り、後でユーザーが「整理して保存」を実行すれば既存finalizeの入力になります。一方、取得本文そのものを `memory`、`journal`、SQLite index、構造化メモリへ直接書く経路はありません。allowlist設定と最終取得statusの同時更新はプロセス間lock内で最新設定へmergeし、回答中にユーザーがOFFへ変更した値を古いsnapshotで戻しません。

一時チャットでもuser / assistantのlive JSONL自体を保持する既存ルールは変わりません。Notion本文は保存しませんが、assistantが回答に含めた内容はlive JSONLへ残ります。秘匿性が高いtargetではチェックをOFFにしてください。

## tokenの失効・削除

ローカル保存を削除:

```powershell
python scripts\notion_integration.py credential delete
```

Notion側でもconnectionを対象pageから外すか、internal integration tokenを再生成／connectionを削除します。ローカルallowlistを残しても、権限を失ったtargetは取得できず、cache fallbackもありません。

## テスト

```powershell
python -m unittest tests.test_notion_integration -v
python -m unittest tests.test_chat_gui_bridge tests.test_codex_conversation -v
cd desktop\app
npm run build
```

テストはOFF時にadapterを呼ばないこと、allowlist、権限喪失、部分失敗、本文非保存、公開responseへ本文を含めないこと、create / update / delete endpoint拒否、会話プロンプトの外部入力境界を確認します。

## 公式資料

* [Notion authorization](https://developers.notion.com/guides/get-started/authorization)
* [Search by title](https://developers.notion.com/reference/post-search)
* [Retrieve a page](https://developers.notion.com/reference/retrieve-a-page)
* [Retrieve block children](https://developers.notion.com/reference/get-block-children)
* [Retrieve a data source](https://developers.notion.com/reference/retrieve-a-data-source)
* [Query a data source](https://developers.notion.com/reference/query-a-data-source)
* [Request limits](https://developers.notion.com/reference/request-limits)
