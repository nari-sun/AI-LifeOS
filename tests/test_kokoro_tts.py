import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"

import sys

sys.path.insert(0, str(SCRIPTS))

import kokoro_tts  # noqa: E402


class KokoroTtsTests(unittest.TestCase):
    def test_split_text_keeps_sentence_boundaries_and_bounds_chunks(self):
        chunks = kokoro_tts.split_text("一文目です。二文目です！" + "長" * 12, max_chunk_chars=6)

        self.assertEqual("一文目です。", chunks[0])
        self.assertEqual("二文目です！", chunks[1])
        self.assertTrue(all(len(chunk) <= 6 for chunk in chunks[2:]))

    def test_synthesize_rejects_unknown_voice_before_loading_optional_dependencies(self):
        with self.assertRaisesRegex(ValueError, "未対応"):
            kokoro_tts.synthesize_to_wav(root=ROOT, text="テスト", voice="not-a-voice")

    def test_synthesize_reports_cancel_before_loading_optional_dependencies(self):
        with self.assertRaisesRegex(kokoro_tts.KokoroSynthesisCancelled, "停止"):
            kokoro_tts.synthesize_to_wav(root=ROOT, text="テスト", is_cancelled=lambda: True)

    def test_synthesize_writes_one_temporary_wav_with_fake_pipeline(self):
        calls: list[tuple[str, str]] = []

        class FakePipeline:
            def __init__(self, lang_code, **_kwargs):
                self.lang_code = lang_code

            def __call__(self, text, voice):
                calls.append((text, voice))
                yield text, "", [0.1, -0.1]

        class FakeSoundFile:
            @staticmethod
            def write(path, audio, sample_rate, subtype):
                Path(path).write_bytes(b"RIFF" + str((len(audio), sample_rate, subtype)).encode("ascii"))

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            audio_dir = root / "runtime-tts"
            with (
                mock.patch.object(kokoro_tts, "temporary_audio_dir", return_value=audio_dir),
                mock.patch.object(kokoro_tts, "_load_dependencies", return_value=(FakePipeline, numpy, FakeSoundFile)),
            ):
                output = kokoro_tts.synthesize_to_wav(
                    root=root,
                    text="最初です。次です。",
                    voice="jf_alpha",
                    request_id="test-request",
                )

            self.assertTrue(output.exists())
            self.assertEqual(audio_dir, output.parent)
            self.assertEqual("j", FakePipeline("j").lang_code)
            self.assertEqual([("最初です。", "jf_alpha"), ("次です。", "jf_alpha")], calls)
            self.assertTrue((root / "cache" / "tts").is_dir())

    def test_synthesize_explains_how_to_install_missing_unidic_dictionary(self):
        class FailingPipeline:
            def __init__(self, lang_code, **_kwargs):
                del lang_code
                raise RuntimeError("Failed initializing MeCab: unidic dicdir mecabrc")

        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                mock.patch.object(kokoro_tts, "temporary_audio_dir", return_value=Path(temp_dir) / "runtime-tts"),
                mock.patch.object(kokoro_tts, "_load_dependencies", return_value=(FailingPipeline, numpy, object())),
            ):
                with self.assertRaisesRegex(kokoro_tts.KokoroUnavailableError, "unidic download"):
                    kokoro_tts.synthesize_to_wav(root=Path(temp_dir), text="テスト")

    def test_synthesize_publishes_each_audio_chunk_before_the_full_text_finishes(self):
        class FakePipeline:
            def __init__(self, _lang_code=None, **_kwargs):
                pass

            def __call__(self, text, voice):
                yield text, "", [0.1, -0.1]

        class FakeSoundFile:
            @staticmethod
            def write(path, audio, sample_rate, subtype):
                Path(path).write_bytes(b"RIFF" + str((len(audio), sample_rate, subtype)).encode("ascii"))

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            audio_dir = root / "runtime-tts"
            published: list[tuple[Path, int]] = []
            with (
                mock.patch.object(kokoro_tts, "temporary_audio_dir", return_value=audio_dir),
                mock.patch.object(kokoro_tts, "_load_dependencies", return_value=(FakePipeline, numpy, FakeSoundFile)),
            ):
                paths = kokoro_tts.synthesize_to_wav_chunks(
                    root=root,
                    text="最初です。次です。",
                    request_id="stream-test",
                    on_chunk=lambda path, index: published.append((path, index)),
                )

            self.assertEqual([0, 1], [index for _, index in published])
            self.assertEqual(paths, [path for path, _ in published])
            self.assertTrue(all(path.exists() for path in paths))

    def test_synthesize_stream_cleans_already_published_audio_when_cancelled(self):
        class FakePipeline:
            def __init__(self, _lang_code=None, **_kwargs):
                pass

            def __call__(self, text, voice):
                yield text, "", [0.1, -0.1]

        class FakeSoundFile:
            @staticmethod
            def write(path, audio, sample_rate, subtype):
                Path(path).write_bytes(b"RIFF" + str((len(audio), sample_rate, subtype)).encode("ascii"))

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cancelled = False
            published: list[Path] = []

            def publish(path, _index):
                nonlocal cancelled
                published.append(path)
                cancelled = True

            with (
                mock.patch.object(kokoro_tts, "temporary_audio_dir", return_value=root / "runtime-tts"),
                mock.patch.object(kokoro_tts, "_load_dependencies", return_value=(FakePipeline, numpy, FakeSoundFile)),
            ):
                with self.assertRaisesRegex(kokoro_tts.KokoroSynthesisCancelled, "停止"):
                    kokoro_tts.synthesize_to_wav_chunks(
                        root=root,
                        text="最初です。次です。",
                        is_cancelled=lambda: cancelled,
                        on_chunk=publish,
                    )

            self.assertEqual(1, len(published))
            self.assertFalse(published[0].exists())

    def test_cleanup_removes_only_stale_temporary_audio(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir)
            stale = cache_dir / "read-aloud-old.wav"
            recent = cache_dir / "read-aloud-recent.wav"
            model = cache_dir / "model.bin"
            stale.write_bytes(b"old")
            recent.write_bytes(b"recent")
            model.write_bytes(b"model")
            now = 1_000_000.0
            stale.touch()
            recent.touch()
            # Set predictable ages without touching model artifacts.
            import os

            os.utime(stale, (now - kokoro_tts.TEMP_AUDIO_MAX_AGE_SECONDS - 1, now - kokoro_tts.TEMP_AUDIO_MAX_AGE_SECONDS - 1))
            os.utime(recent, (now - 1, now - 1))

            kokoro_tts.cleanup_temp_audio(cache_dir, now=now)

            self.assertFalse(stale.exists())
            self.assertTrue(recent.exists())
            self.assertTrue(model.exists())
