# RT-0019 Kokoro TTS Read Aloud

RT-0019 は、Chat GUI の assistant メッセージを Kokoro TTS で読み上げるためのチケットです。

## Status

Backlog. まだ実装しません。

Kokoro TTS を、AI-LifeOS の任意依存ローカル読み上げエンジンとして評価します。VOICEVOX / COEIROINK / AquesTalk は、クレジット表記や配布ライセンスの扱いが重くなるため、このチケットでは採用しません。

## Background

Chat GUI に assistant 返答の読み上げボタンを追加したいです。

候補検討の結果:

- VOICEVOX / ずんだもん: クレジット表記が必要
- COEIROINK: クレジット表記が必要
- AquesTalk / ゆっくりボイス: クレジット表記は不要寄りだが、利用・配布ライセンス確認が必要
- Windows 標準 TTS: 実装は軽いが、声が聞きづらい
- Kokoro TTS: Apache-2.0 の open-weight TTS として利用しやすく、日本語 voice もある

RT-0019 では Kokoro TTS を第一候補にします。

## Goal

Chat GUI で assistant メッセージをローカル音声合成で読み上げられるようにします。

最小ゴール:

- assistant message に読み上げボタンを追加する
- Kokoro TTS で日本語読み上げを行う
- 声を選べるようにする
- 読み上げ中の停止ボタンを用意する
- モデル・生成 wav・キャッシュを PublicEdition の Git 管理対象に入れない
- Kokoro が未セットアップの場合は、分かりやすいエラーまたはセットアップ案内を出す

## Non-Goals

RT-0019 では以下を対象外にします。

- VOICEVOX / ずんだもん連携
- AquesTalk / ゆっくりボイス連携
- Windows 標準 TTS 連携
- 音声認識
- リアルタイム会話音声入力
- assistant 生成中の逐次読み上げ
- 生成 wav の長期保存
- 生成音声を memory / journal / conversations の根拠にすること

assistant 返答の生成途中表示は RT-0017、user 発言の即時表示は RT-0018 で扱います。

## Voice Candidates

Kokoro の日本語 voice は、まず以下を候補にします。

| Voice ID | Type | Note |
| --- | --- | --- |
| `jf_alpha` | female | デフォルト候補 |
| `jf_gongitsune` | female | 選択候補 |
| `jf_nezumi` | female | 選択候補 |
| `jf_tebukuro` | female | 選択候補 |
| `jm_kumo` | male | 男声候補 |

初期実装では `jf_alpha` をデフォルトにします。

## Design Direction

読み上げ処理は、GUI 本体に直接重い依存を入れず、Python bridge 側に寄せます。

想定フロー:

```text
assistant message の読み上げボタン
-> Tauri command
-> scripts/chat_gui_bridge.py
-> Kokoro TTS helper
-> 一時 wav を生成
-> GUI または OS の音声再生機能で再生
```

長い assistant 返答は、一括 wav 生成ではなく数文単位に分割して順次合成・再生する方針を検討します。これにより、体感待ち時間を短くします。

## Dependency Policy

Kokoro TTS は任意依存にします。

推奨:

- PublicEdition には連携コードだけを置く
- Kokoro 本体、モデル、生成 wav は Git 管理しない
- 初回セットアップはユーザー操作または明示コマンドにする
- `.env` は使わない
- OpenAI API は使わない

候補ディレクトリ:

```text
cache/tts/
logs/tts/
```

ただし PublicEdition では、これらは `.gitignore` 対象にします。

## UI Requirements

- assistant message の横に読み上げボタンを表示する
- 読み上げ中は停止ボタンに切り替える
- 声を選ぶUIは最初は簡素でよい
- デフォルト voice は `jf_alpha`
- Kokoro 未セットアップ時は、ボタン押下でセットアップ不足を表示する
- 読み上げ失敗時は、チャット保存処理に影響を与えない

## Backend Requirements

- `chat_gui_bridge.py` に読み上げ用 command を追加する
- Kokoro が import 可能か確認する
- 日本語向けに `lang_code="j"` を使う
- voice id を allowlist で検証する
- 入力テキストの最大長を制限する
- 長文は文単位で分割する
- 生成 wav は一時ファイルとして扱う
- 停止要求で再生または合成を中断できる

## Performance Notes

Kokoro は軽量寄りですが、初回ロードには時間がかかります。

想定:

- 初回モデルロード: 数秒から十数秒
- 初回モデルダウンロード: ネットワーク状況に依存
- 短文読み上げ: CPU でも数秒以内を目標
- 長文読み上げ: 数文ごとに分割して順次再生

実装時に、AI-LifeOS の実機で `jf_alpha` の短文・中文・長文ベンチを記録します。

## Data Handling

- 生成 wav は会話ログではない
- 生成 wav は原則として一時ファイルにする
- 生成 wav を `conversations` / `journal` / `memory` に保存しない
- 生成 wav を Git 管理しない
- 読み上げエラーは `logs` に診断情報だけ残す
- assistant 本文そのものを追加ログへ重複保存しない

## Acceptance Criteria

- assistant message の読み上げボタンから Kokoro TTS 音声を再生できる
- `jf_alpha` で日本語 assistant 返答を読み上げられる
- voice id を選べる
- 読み上げを停止できる
- Kokoro 未セットアップ時に明確なエラーを出せる
- 読み上げ失敗時も chat / session / finalize の保存処理が壊れない
- モデル、生成 wav、一時キャッシュが Git 管理対象に入らない
- Python テストまたは GUI 手動確認で、読み上げ・停止・未セットアップ時エラーを確認できる

## Dependencies

- Kokoro TTS の任意依存セットアップ方針
- 日本語 G2P 用依存の確認
- Tauri からローカル wav を再生する方式の確認
- `.gitignore` のキャッシュ・生成物除外確認

## Risks

- 日本語品質が期待より低い可能性がある
- 初回ロードが重く、GUI 操作が固まる可能性がある
- 長文を一括合成すると待ち時間が長くなる
- Kokoro や依存パッケージのインストールが Windows で詰まる可能性がある
- Apache-2.0 / 依存ライセンス / モデルカードの整理を忘れると、PublicEdition 公開時に不明瞭になる

## Decision Notes

RT-0019 は、読み上げ機能の第一候補を Kokoro TTS に固定して検証するためのチケットです。

初期実装では、Kokoro TTS を同梱せず、任意依存として扱います。VOICEVOX や AquesTalk は、別チケットで再評価するまで入れません。
