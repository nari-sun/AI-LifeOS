# RT-0019 Kokoro TTS Read Aloud

RT-0019 は、Chat GUI の assistant メッセージを Kokoro TTS で読み上げるためのチケットです。

## Status

Implemented on 2026-07-12. Kokoro TTS は Chat GUI の任意依存です。

assistant メッセージごとに読み上げ・停止ボタンと voice 選択を表示します。Kokoro が未導入のPCでは、チャット保存・セッション・finalizeに影響を与えず、セットアップコマンドを含むエラーだけを表示します。VOICEVOX / COEIROINK / AquesTalk はこのチケットの対象外です。

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
-> 文ごとの OS 一時 WAV を生成
-> Tauri Channel で各WAVをGUIへ通知
-> GUI の Audio 再生キューへ追加
-> 最初の文から再生し、後続文を生成完了順に続けて再生
```

`scripts/kokoro_tts.py` は本文を最大12,000文字に制限し、420文字以下の文単位に分割して合成します。最初のWAVができるとすぐGUIが再生を始め、後続WAVは合成完了順にキューへ追加します。停止要求は各文・音声チャンクの境界で確認し、再生中音声・未再生キュー・進行中合成を停止します。生成済みの WAV は再生終了または停止時に削除し、残った一時ファイルも次回要求時に1時間で掃除します。

## Dependency Policy

Kokoro TTS は任意依存です。GUI本体の `npm run build`、通常の Python テスト、会話保存にKokoroの導入は不要です。

推奨:

- PublicEdition には連携コードだけを置く
- Kokoro 本体、モデル、生成 wav は Git 管理しない
- 初回セットアップはユーザー操作または明示コマンドにする
- `.env` は使わない
- OpenAI API は使わない

候補ディレクトリ:

```text
cache/tts/                 # Kokoro / Hugging Face のモデルキャッシュ
%TEMP%/AI-LifeOS/tts/      # 再生用の一時 WAV と停止マーカー
logs/tts/
```

`cache/tts/` と `logs/tts/` は `.gitignore` 対象です。WAV は OS の一時ディレクトリにだけ置き、`conversations` / `journal` / `memory` には保存しません。TTSログには本文を書かず、request ID、voice、文字数、エラー種別だけを残します。

### Windows セットアップ

既存のPython環境への影響を避けるため、Python 3.10〜3.12 でプロジェクト用の`.venv`を作ってから、PowerShellで次を実行します。

```powershell
python -m venv --system-site-packages .venv
.\.venv\Scripts\python.exe -m pip install "kokoro==0.9.4" "misaki[ja]" soundfile
.\.venv\Scripts\python.exe -m unidic download
```

`unidic download` は約526MBの日本語辞書を取得します。初回の読み上げ時にはKokoroモデルもダウンロードされます。`scripts/chat_gui_task.ps1`とTauri GUIは`.venv\Scripts\python.exe`を自動優先します。実装は `KPipeline(lang_code="j")` と日本語 voice を使います。導入後も音声品質はローカル環境で確認してください。

### 取得元とライセンス

- Python package: [PyPI: kokoro 0.9.4](https://pypi.org/project/kokoro/)、Apache-2.0、Python 3.10以上・3.13未満
- モデル・voice: [hexgrad/Kokoro-82M model card](https://huggingface.co/hexgrad/Kokoro-82M)、Apache-2.0
- 日本語G2P: [hexgrad/misaki](https://github.com/hexgrad/misaki) の `ja` extra

公開物に音声を含める場合は、このモデルカードと各依存パッケージのライセンスをその時点で再確認します。PublicEditionにはモデル・voice・生成音声を同梱しません。

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
- 文ごとのWAVをTauri Channelで通知し、最初の文から再生キューへ追加する
- 生成 wav は OS の一時ファイルとして扱う
- 停止要求で再生または合成を中断できる

## Performance Notes

Kokoro は軽量寄りですが、初回ロードには時間がかかります。

想定:

- 初回モデルロード: 数秒から十数秒
- 初回モデルダウンロード: ネットワーク状況に依存
- 短文読み上げ: CPU でも数秒以内を目標
- 長文読み上げ: 最初の文が完成した時点で再生を始め、後続文を順次キューへ追加

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

- Kokoro TTS の任意依存セットアップ
- 日本語 G2P の `misaki[ja]`
- Tauri asset protocol による OS 一時 WAV の再生
- `.gitignore` によるモデルキャッシュ・ログの除外

## Risks

- 日本語品質が期待より低い可能性がある
- 初回ロードが重く、GUI 操作が固まる可能性がある
- 長文を一括合成すると待ち時間が長くなる
- Kokoro や依存パッケージのインストールが Windows で詰まる可能性がある
- Apache-2.0 / 依存ライセンス / モデルカードの整理を忘れると、PublicEdition 公開時に不明瞭になる

## Decision Notes

RT-0019 は、読み上げ機能を Kokoro TTS に固定して実装しました。`jf_alpha` をデフォルトにし、`jf_gongitsune`、`jf_nezumi`、`jf_tebukuro`、`jm_kumo` を allowlist から選べます。

Kokoro TTS は同梱せず、任意依存のままです。VOICEVOX や AquesTalk は、別チケットで再評価するまで入れません。
