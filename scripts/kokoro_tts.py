"""Optional Kokoro TTS support for the desktop chat GUI.

This module deliberately imports Kokoro only while a read-aloud request is
running.  The ordinary chat workflows therefore keep working on installations
where the optional TTS dependencies have not been installed.
"""

from __future__ import annotations

import os
import re
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

SUPPORTED_VOICES = ("jf_alpha", "jf_gongitsune", "jf_nezumi", "jf_tebukuro", "jm_kumo")
DEFAULT_VOICE = "jf_alpha"
LANG_CODE = "j"
SAMPLE_RATE = 24_000
MAX_TEXT_CHARS = 12_000
MAX_CHUNK_CHARS = 420
TEMP_AUDIO_MAX_AGE_SECONDS = 60 * 60


class KokoroTtsError(RuntimeError):
    """Base error for a read-aloud operation."""


class KokoroUnavailableError(KokoroTtsError):
    """The optional local Kokoro installation is not ready."""


class KokoroSynthesisCancelled(KokoroTtsError):
    """A user cancelled synthesis before the temporary WAV was completed."""


def synthesize_to_wav(
    *,
    root: Path,
    text: str,
    voice: str = DEFAULT_VOICE,
    request_id: str = "read-aloud",
    is_cancelled: Callable[[], bool] | None = None,
) -> Path:
    """Create one temporary WAV for a read-aloud request and return its path."""

    normalized_text = _validate_text(text)
    selected_voice = _validate_voice(voice)
    cancelled = is_cancelled or (lambda: False)
    if cancelled():
        raise KokoroSynthesisCancelled("読み上げを停止しました。")

    root = root.resolve()
    model_cache_dir = root / "cache" / "tts"
    model_cache_dir.mkdir(parents=True, exist_ok=True)
    audio_dir = temporary_audio_dir()
    audio_dir.mkdir(parents=True, exist_ok=True)
    cleanup_temp_audio(audio_dir)
    _configure_model_cache(model_cache_dir)

    pipeline_class, numpy, soundfile = _load_dependencies()
    output_path = audio_dir / _temporary_audio_name(request_id)
    audio_segments: list[Any] = []

    try:
        try:
            pipeline = pipeline_class(lang_code=LANG_CODE, repo_id="hexgrad/Kokoro-82M")
        except Exception as exc:
            raise _pipeline_setup_error(exc) from exc
        for chunk in split_text(normalized_text):
            if cancelled():
                raise KokoroSynthesisCancelled("読み上げを停止しました。")

            for _graphemes, _phonemes, audio in pipeline(chunk, voice=selected_voice):
                if cancelled():
                    raise KokoroSynthesisCancelled("読み上げを停止しました。")
                samples = numpy.asarray(audio, dtype=numpy.float32).reshape(-1)
                if samples.size:
                    audio_segments.append(samples)
                    audio_segments.append(numpy.zeros(int(SAMPLE_RATE * 0.12), dtype=numpy.float32))

        if not audio_segments:
            raise KokoroTtsError("Kokoro TTS が音声データを生成しませんでした。")

        if cancelled():
            raise KokoroSynthesisCancelled("読み上げを停止しました。")
        soundfile.write(str(output_path), numpy.concatenate(audio_segments), SAMPLE_RATE, subtype="PCM_16")
        return output_path
    except Exception:
        output_path.unlink(missing_ok=True)
        raise


def synthesize_to_wav_chunks(
    *,
    root: Path,
    text: str,
    voice: str = DEFAULT_VOICE,
    request_id: str = "read-aloud",
    is_cancelled: Callable[[], bool] | None = None,
    on_chunk: Callable[[Path, int], None],
) -> list[Path]:
    """Generate and publish temporary WAV files one synthesis chunk at a time."""

    normalized_text = _validate_text(text)
    selected_voice = _validate_voice(voice)
    cancelled = is_cancelled or (lambda: False)
    if cancelled():
        raise KokoroSynthesisCancelled("読み上げを停止しました。")

    root = root.resolve()
    model_cache_dir = root / "cache" / "tts"
    model_cache_dir.mkdir(parents=True, exist_ok=True)
    audio_dir = temporary_audio_dir()
    audio_dir.mkdir(parents=True, exist_ok=True)
    cleanup_temp_audio(audio_dir)
    _configure_model_cache(model_cache_dir)

    pipeline_class, numpy, soundfile = _load_dependencies()
    output_paths: list[Path] = []
    chunk_index = 0
    try:
        try:
            pipeline = pipeline_class(lang_code=LANG_CODE, repo_id="hexgrad/Kokoro-82M")
        except Exception as exc:
            raise _pipeline_setup_error(exc) from exc

        for text_chunk in split_text(normalized_text):
            if cancelled():
                raise KokoroSynthesisCancelled("読み上げを停止しました。")

            for _graphemes, _phonemes, audio in pipeline(text_chunk, voice=selected_voice):
                if cancelled():
                    raise KokoroSynthesisCancelled("読み上げを停止しました。")
                samples = numpy.asarray(audio, dtype=numpy.float32).reshape(-1)
                if not samples.size:
                    continue

                output_path = audio_dir / _temporary_audio_name(f"{request_id}-{chunk_index:04d}")
                soundfile.write(str(output_path), samples, SAMPLE_RATE, subtype="PCM_16")
                output_paths.append(output_path)
                try:
                    on_chunk(output_path, chunk_index)
                except Exception:
                    output_path.unlink(missing_ok=True)
                    output_paths.pop()
                    raise
                chunk_index += 1

        if not output_paths:
            raise KokoroTtsError("Kokoro TTS が音声データを生成しませんでした。")
        return output_paths
    except Exception:
        for output_path in output_paths:
            output_path.unlink(missing_ok=True)
        raise


def split_text(text: str, max_chunk_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    """Split Japanese text into bounded sentence-oriented synthesis chunks."""

    if max_chunk_chars <= 0:
        raise ValueError("max_chunk_chars は1以上で指定してください。")

    sentences = [part.strip() for part in re.findall(r"[^。！？!?]+[。！？!?]*", text) if part.strip()]
    if not sentences:
        sentences = [text]

    chunks: list[str] = []
    for sentence in sentences:
        chunks.extend(sentence[index : index + max_chunk_chars] for index in range(0, len(sentence), max_chunk_chars))
    return chunks


def cleanup_temp_audio(cache_dir: Path, *, now: float | None = None) -> None:
    """Remove stale temporary audio and cancellation markers, never model files."""

    current = time.time() if now is None else now
    for pattern in ("read-aloud-*.wav", "read-aloud-*.cancel"):
        for path in cache_dir.glob(pattern):
            try:
                if current - path.stat().st_mtime > TEMP_AUDIO_MAX_AGE_SECONDS:
                    path.unlink(missing_ok=True)
            except OSError:
                continue


def temporary_audio_dir() -> Path:
    """Return the OS-managed temporary directory used for generated WAV files."""

    return Path(tempfile.gettempdir()) / "AI-LifeOS" / "tts"


def _validate_text(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("読み上げる assistant 返答がありません。")
    if len(text) > MAX_TEXT_CHARS:
        raise ValueError(f"読み上げは {MAX_TEXT_CHARS:,} 文字までです。返答を短くしてから試してください。")
    return text


def _validate_voice(value: str) -> str:
    voice = str(value or DEFAULT_VOICE).strip()
    if voice not in SUPPORTED_VOICES:
        choices = ", ".join(SUPPORTED_VOICES)
        raise ValueError(f"未対応の読み上げ voice です。選択できる voice: {choices}")
    return voice


def _configure_model_cache(cache_dir: Path) -> None:
    """Keep model downloads below the ignored project-local TTS cache."""

    huggingface_cache = cache_dir / "huggingface"
    os.environ["HF_HOME"] = str(huggingface_cache)
    os.environ["HF_HUB_CACHE"] = str(huggingface_cache / "hub")
    os.environ["HUGGINGFACE_HUB_CACHE"] = str(huggingface_cache / "hub")
    os.environ["XDG_CACHE_HOME"] = str(cache_dir)


def _load_dependencies() -> tuple[Any, Any, Any]:
    try:
        from kokoro import KPipeline
        import numpy
        import soundfile
    except ModuleNotFoundError as exc:
        raise KokoroUnavailableError(
            "Kokoro TTS は未セットアップです。PowerShell で "
            '`python -m pip install "kokoro==0.9.4" "misaki[ja]" soundfile` を実行してください。'
        ) from exc
    except Exception as exc:
        raise KokoroUnavailableError(f"Kokoro TTS を読み込めませんでした: {exc}") from exc
    return KPipeline, numpy, soundfile


def _pipeline_setup_error(exc: Exception) -> KokoroTtsError:
    message = str(exc)
    if "Failed initializing MeCab" in message or ("unidic" in message.lower() and "mecabrc" in message.lower()):
        return KokoroUnavailableError(
            "日本語読み上げ用の UniDic 辞書が未セットアップです。PowerShell で "
            '`python -m unidic download` を一度実行してから、読み上げを再試行してください。'
        )
    return KokoroTtsError(f"Kokoro TTS の初期化に失敗しました: {message}")


def _temporary_audio_name(request_id: str) -> str:
    safe_request_id = re.sub(r"[^A-Za-z0-9_-]", "_", request_id)[:128] or "read-aloud"
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"read-aloud-{safe_request_id}-{timestamp}-{uuid4().hex}.wav"
