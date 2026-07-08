# prompts

このフォルダには、AI-LifeOS の記憶整理に使うプロンプトを置きます。

現在の実行経路は意図的に絞っています。Phase2.5 の summary / journal / memory 整理で実行時に読まれるのは `prompts/codex_phase2_prompt.md` だけです。他のプロンプトは過去の分割案として残っている参照用ファイルで、現行スクリプトからは読まれていません。

## 分類

| ファイル | 分類 | 実行時の利用 | 備考 |
| --- | --- | --- | --- |
| `codex_phase2_prompt.md` | 使用中 | `scripts/process_chat.py` と `scripts/finalize_live_chat.py` が読む。 | `summary.md`、`journal`、`memory/long_term.md`、`memory/preferences.md` の生成・更新ルールの正本。現行の記憶整理ルールを変える場合はこのファイルを編集する。 |
| `summary_prompt.md` | 参考用 / 廃止候補 | 現行スクリプトからは読まれない。 | 旧 standalone summary prompt。過去方針の確認用として一時的に残す。現行の summary 動作を変える目的では編集しない。 |
| `journal_prompt.md` | 参考用 / 廃止候補 | 現行スクリプトからは読まれない。 | 旧 standalone journal prompt。過去方針の確認用として一時的に残す。現行の journal 動作を変える目的では編集しない。 |
| `memory_extract_prompt.md` | 参考用 / 廃止候補 | 現行スクリプトからは読まれない。 | 旧 standalone memory extraction prompt。過去方針の確認用として一時的に残す。現行の memory 動作を変える目的では編集しない。 |

## 実行スクリプトとの対応

| スクリプト | 読むプロンプト | 影響する出力 |
| --- | --- | --- |
| `scripts/process_chat.py` | `prompts/codex_phase2_prompt.md` | `{RAW_FILE}` を生成済み `raw.md` のパスに置換して `tasks/latest_codex_task.md` を作る。 |
| `scripts/finalize_live_chat.py` | `prompts/codex_phase2_prompt.md` | live JSONL から作った `raw.md` に対して `tasks/latest_codex_task.md` を作る、または更新する。 |

## 編集ルール

- 現行の `summary.md`、`journal`、`memory` の挙動を変える場合は `codex_phase2_prompt.md` を編集する。
- `summary_prompt.md`、`journal_prompt.md`、`memory_extract_prompt.md` に現行ルールを重複して書かない。
- 廃止候補の分割プロンプトを再び実行時に使う場合は、スクリプトとこの README を同じ変更で更新する。
- 未使用プロンプトが参照用としても不要になった場合は、別チケットで `docs/` へ移すか削除する。
