# Read-only Memory MCP

`scripts/memory_mcp_server.py` は、AI-LifeOS のローカル会話・記憶を Codex から反復検索するための読み取り専用 MCP サーバーです。OpenAI API を直接呼ばず、API キー、`.env`、外部サービスを必要としません。

サーバーは Python の外部 `mcp` パッケージに依存せず、MCP の newline-delimited JSON-RPC を stdio で処理します。通常起動中の stdout は JSON-RPC 専用です。診断を stderr に出す場合も、query、snippet、ファイル本文、例外本文は出しません。

## 起動

プロジェクトルートで次を実行すると stdio 待受を開始します。

```powershell
python scripts\memory_mcp_server.py --root .
```

プロジェクト境界を固定する場合は、起動時に指定します。

```powershell
python scripts\memory_mcp_server.py --root . --project-scope "Project-Alpha"
```

`--project-scope` はそのMCPプロセスのimmutableな境界です。設定中は`search_past_chats`、`open_conversation`、`get_personal_memory`のすべてへサーバー側で強制されます。tool引数を省略しても境界は外れず、別の値による緩和・上書きはエラーになります。会話アプリはさらに`--exclude-live-session`へ現在のlive JSONLを固定し、現在の質問や直前までの同一セッションを「過去の記憶」として検索・open・件数集計しません。この値もtool引数から解除できません。

CLI の確認だけなら、MCP 待受には入りません。

```powershell
python scripts\memory_mcp_server.py --help
python scripts\memory_mcp_server.py --version
```

## Codex CLI への登録

`scripts/codex_conversation.py` とChat GUIから使う場合、アプリがそのCodexプロセスだけに絶対パス設定を渡すため、手動登録は不要です。セッションにproject scopeがあれば、同じ値をMCPの`--project-scope`へ渡します。過去チャット検索がOFFならサーバー自体を公開せず、長期memoryがOFFなら`get_personal_memory`をCodexのtool allow-listから外します。会話用Codexプロセスではshell、apps、web searchなどを無効にし、解決済みのambient MCPを明示的にdisableしたうえで、turn開始前のinventoryにもMemory MCP以外のtool/resourceが公開されていないことを検証します。Memory MCPが起動できない場合は無言で回答を続けません。

agentic検索では、`search_past_chats`が列挙した候補と、`open_conversation`で実際に開いた一次資料を分けて記録します。GUIの回答詳細では、静的context、MCP検索候補、MCPでopenした一次資料を別々に表示します。検索一覧だけを「open済み」または「最終回答で使用した根拠」とは表示しません。

単独のCodex TUIから使いたい場合だけ、次の永続登録を行います。

絶対パスで登録しておくと、別の作業ディレクトリから Codex を起動しても同じ AI-LifeOS を参照できます。

```powershell
$projectRoot = (Resolve-Path .).Path
codex mcp add ai_lifeos_memory -- python "$projectRoot\scripts\memory_mcp_server.py" --root "$projectRoot"
codex mcp list
```

登録を永続化せず、その Codex 起動だけで使う場合の例です。

```powershell
codex `
  -c 'mcp_servers.ai_lifeos_memory.command="python"' `
  -c 'mcp_servers.ai_lifeos_memory.args=["scripts/memory_mcp_server.py","--root","."]'
```

Codex の TUI では `/mcp` で接続状態とツールを確認できます。MCP 設定は Codex の設定へ保存されるため、登録操作そのものはこのサーバーの read-only 保証とは別です。

## ツール

| Tool | 用途 | 主な引数 |
|---|---|---|
| `search_past_chats` | 確定済み会話と未整理 live JSONL を検索する | `query`, `role`, `scope`, `path`, `project_scope`, `limit` |
| `open_conversation` | 検索結果の一次出典を発言単位で開く | `reference`, `around_message`, `max_chars` |
| `get_personal_memory` | 長期記憶・好み・プロジェクト・構造化項目を読む | `scope`, `project_scope`, `max_chars` |
| `get_index_health` | SQLite index の欠損・旧形式・stale を確認する | なし |

全ツールは MCP annotation で `readOnlyHint=true`、`destructiveHint=false`、`openWorldHint=false` を宣言します。同じ stdio セッションで `tools/call` を何度でも処理するため、一つの回答中に検索語を変えて再検索できます。

### `search_past_chats`

`role` は `any`、`user`、`assistant` です。「自分がどう思っていたか」「以前何と言ったか」の根拠を探す場合は `user` を指定します。assistant 発言は、ユーザー自身の見解の証拠として扱いません。

`scope` は次の値を取ります。

- `all`: 確定済み会話、summary、未整理 live
- `finalized`: `conversations/` の raw、発言 chunk、summary
- `messages`: raw の発言 chunk、見出しのない raw、未整理 live
- `summaries`: summary のみ
- `live`: `inbox/live/` のうち index 更新済みでなく、記憶除外されていない JSONL のみ

セッション metadata の `personalization.temporary` または `personalization.exclude_from_memory` が `true` の live JSONL は、検索結果、`open_conversation`、`get_index_health` の未整理件数から除外します。metadata が壊れていて安全に判定できない場合も fail-closed で除外します。会話アプリから指定された現在のlive JSONLも同じ3経路から除外します。

結果の `reference` は `conversations/.../raw.md#message-3-user` または `inbox/live/...jsonl#message-3-user` の形式です。その値を変更せず `open_conversation` へ渡せます。

`path` は `conversations/` または `inbox/live/` 配下の相対パスだけを受け付けます。絶対パス、`..`、許可ディレクトリ外へ解決される symlink は拒否します。

`project_scope` を指定すると、path、title、metadata、対象発言本文・snippet のいずれかにその文字列がある結果だけを返します。確定済み会話ではscopeとspeaker roleをSQLite/Markdownのデータ層でranking前に絞るため、全体上位候補の後段filterによる取りこぼしはありません。raw chunkの所属はsession headerとその発言本文で判定し、別発言にscope名があるだけでは所属扱いにしません。該当結果がなくても全体検索へ fallback しません。

live JSONLは、対応する`.session.json`に`personalization.project_scope`が保存されていればその値をsession所属の一次情報として使います。保存scopeが異なる場合、本文中の偶然の一致で所属を上書きしません。sidecarに明示scopeがないliveは、path/titleが一致しない限り、scopeを含む個々の発言だけを候補にします。

今後liveからfinalizeするrawには`Project Scope:`をsession headerへ固定保存します。Phase3.10以前に作成され、このheaderを持たない既存rawは勝手に書き換えず、path、title、既存header、対象発言の一致だけで判定します。そのため古いrawをscope付きで検索する場合、明示的な所属情報がない記録は安全側で候補から外れることがあります。

### `open_conversation`

`reference` に含まれる message 番号、または `around_message` を中心として、時系列を保った bounded window を返します。`max_chars` は 200〜50,000 文字です。確定済み会話は `raw.md` / `summary.md`、live は `.jsonl` だけを開けます。

active project scope設定中は、検索結果を経由せずpathを直接指定してもscope検証を省略できません。session header/sidecarがscope所属なら通常のbounded windowを返します。明示的なsession所属がなく発言本文だけが一致した場合は一致発言だけを返し、別scopeの隣接発言をwindowへ混ぜません。

### `get_personal_memory`

`scope` は `all`、`long_term`、`preferences`、`projects`、`items` です。ファイルが存在しない場合も新規作成しません。`project_scope` を指定した場合、構造化itemはmetadata/contentが一致するitemだけを返します。`long_term.md`、`preferences.md`、`projects.md`はファイル全体を返さず、scope名を持つMarkdown見出しの節、またはscope名を持つ個別行だけを抽出します。一致なしなら空で返します。

### `get_index_health`

次を本文なしの metadata として返します。

- `status`: `missing` / `unreadable` / `legacy` / `stale` / `ready`
- current schema 対応、document 件数、raw chunk 件数、FTS5 の有無
- source の更新時刻、index 未登録・削除済み source の件数
- 未整理 live ファイル件数
- 次の検索で使う `sqlite` / `markdown` strategy

このツールは index を rebuild しません。`search_past_chats` は health が `ready` のときだけ SQLite を使い、それ以外は Markdown を直接検索します。source manifestが一致していても、schema versionまたはraw metadata parser versionがない／一致しないindexは`legacy`です。旧parser由来のscope metadataを信用せずMarkdownへfallbackし、検索中に `memory/search_index.sqlite3` を更新しません。

## 推奨する検索手順

過去の具体的な感想を回答する場合は、次の順序を想定しています。

1. `get_index_health()` で取得元の状態を確認する。
2. 作品名を `role=user` で検索する。
3. 0件または根拠不足なら、登場人物・固有の台詞・具体的な話題へ query を変えて再検索する。
4. 正しい user 発言の `reference` を `open_conversation` で開く。
5. user の一次発言だけを根拠に回答し、必要なら source path と message 番号を示す。

この反復経路により、作品名と本文の語彙が一致しない場合も、Codex側が会話上わかっている別表現へ検索語を変えられます。たとえば完全な合成例なら、架空作品「星舟クロニクル」の0件後に、架空の人物名「リオナ」「ベルク」や架空の話題「青い羅針盤」で再検索する流れです。これらの対応表を含め、個人の作品・人物・台詞を結び付ける固定辞書は製品に持たず、実運用の語彙不一致はMCPの反復reformulationで扱います。

## Read-only 境界

サーバーが読む範囲は次のとおりです。

- `conversations/**/raw.md`
- `conversations/**/summary.md`
- `inbox/live/*.jsonl` と整理状態確認用の対応 `.session.json`
- `journal/**/*.md` と `memory/**/*.md`（index health の source 確認）
- `memory/search_index.sqlite3`（SQLite read-only URI と `PRAGMA query_only`）

ツール呼び出しでは、`conversations`、`journal`、`memory`、`inbox` を作成・更新・削除しません。SQLite index がない場合も作りません。返された個人データ本文はMCP tool resultとしてCodex clientへ渡され、回答生成に必要な範囲はCodexサービスへ送信され得ます。OpenAI APIを直接呼んだりAPI keyを保存したりはしませんが、完全な端末内推論ではありません。利用者はCodex/ChatGPT側のデータ取扱い設定も確認してください。

## 検証

```powershell
python -m unittest tests.test_memory_mcp_server -v
python scripts\memory_mcp_server.py --help
```

テストは、role filter、live 検索、一時・記憶除外・現在回答中liveの非公開、immutable project scope、core memoryの節/行抽出、raw headerとlive sidecarによる所属、scope/roleのranking前filter、別発言の非公開、出典open、path traversal、index stale/legacy/parser version判定、個人ファイルとindexの非変更、同一stdioセッションでの反復tool callを確認します。

参考仕様:

- [MCP lifecycle 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle)
- [MCP stdio transport](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)
- [MCP tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)
