import os
import tempfile
import unittest
import wave
from pathlib import Path
from voice_runtime import (
    normalize_speech_text,
    speech_chunks,
    cache_key_for,
    wav_duration_ms,
    prune_voice_cache,
    make_controller,
    voice_catalog,
    SPEEDS
)
import app


class InstantReadAloudTests(unittest.TestCase):
    def test_text_normalization_acronyms_units_decimals(self):
        input_text = "The system has 16 GB RAM and 8GB VRAM with a 3.5 GHz CPU. Latency is ~340 ms at $0 cost! 🚀"
        normalized = normalize_speech_text(input_text)
        self.assertIn("16 gigabytes", normalized)
        self.assertIn("8 gigabytes", normalized)
        self.assertIn("ram", normalized)
        self.assertIn("V-RAM", normalized)
        self.assertIn("3 point 5", normalized)
        self.assertIn("C P U", normalized)
        self.assertIn("340 milliseconds", normalized)
        self.assertIn("0 dollars", normalized)
        self.assertNotIn("🚀", normalized)

    def test_text_normalization_abbreviations(self):
        input_text = "Use fast inference (e.g., Q4_K_M vs. FP16), i.e., quantized models, etc."
        normalized = normalize_speech_text(input_text)
        self.assertIn("for example", normalized)
        self.assertIn("versus", normalized)
        self.assertIn("that is", normalized)
        self.assertIn("etcetera", normalized)

    def test_spoken_response_text_strips_markdown_and_metadata(self):
        raw = """### Overview
Here is the performance report:
```python
import torch
print("hidden code")
```
- Accuracy: 95%
METHOD: DIRECT_REASONING
PROMPT TOKENS: 450
DECODE_MS: 340
"""
        spoken = app.spoken_response_text(raw)
        self.assertNotIn("hidden code", spoken)
        self.assertNotIn("METHOD:", spoken)
        self.assertNotIn("PROMPT TOKENS:", spoken)
        self.assertNotIn("DECODE_MS:", spoken)
        self.assertIn("95 percent", spoken)

    def test_speech_chunks_exact_word_fidelity(self):
        text = "Hello! Here is an answer with natural punctuation. " + ("A longer paragraph retains every word and every sentence. " * 35)
        chunks = speech_chunks(text)
        self.assertEqual(" ".join(chunks), " ".join(text.split()))
        self.assertGreater(len(chunks), 3)
        self.assertLessEqual(len(chunks[0]), 140)
        self.assertTrue(all(len(c) <= 320 for c in chunks))

    def test_speech_chunks_first_chunk_rapid_start(self):
        response = (
            "Certainly! Here are three main points to consider when deploying local language models on Windows: "
            "First, ensure that your GPU drivers are up to date and that memory paging is enabled. "
            "Second, monitor your VRAM usage to prevent out-of-memory errors. "
            "Third, optimize your prompt cache to ensure fast responses."
        )
        chunks = speech_chunks(response, first_limit=130, limit=280)
        self.assertEqual(" ".join(chunks), " ".join(response.split()))
        # Chunk 0 should be short enough for fast synthesis (< 130 chars) and long enough to be a complete thought
        self.assertLessEqual(len(chunks[0]), 130)
        self.assertGreaterEqual(len(chunks[0]), 50)
        self.assertIn("Certainly!", chunks[0])

    def test_speech_chunks_500_words(self):
        words = ["word" + str(i) for i in range(500)]
        text = ". ".join([" ".join(words[i:i+20]) for i in range(0, 500, 20)]) + "."
        chunks = speech_chunks(text)
        self.assertEqual(" ".join(chunks), " ".join(text.split()))
        self.assertLessEqual(len(chunks[0]), 140)
        self.assertTrue(all(len(c) <= 320 for c in chunks))

    def test_content_addressable_cache_key(self):
        key1 = cache_key_for("Hello world!", "af_heart", 1.0)
        key2 = cache_key_for("  hello world!  ", "af_heart", 1.0)
        key3 = cache_key_for("Hello world!", "am_michael", 1.0)
        key4 = cache_key_for("Hello world!", "af_heart", 1.25)
        self.assertEqual(key1, key2)  # Case & whitespace invariant
        self.assertNotEqual(key1, key3)  # Voice differentiates
        self.assertNotEqual(key1, key4)  # Speed differentiates

    def test_wav_duration_reader(self):
        with tempfile.TemporaryDirectory() as tmp:
            wav_path = Path(tmp) / "test.wav"
            # Write a dummy 1-second 16kHz mono WAV file
            with wave.open(str(wav_path), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(16000)
                w.writeframes(b"\x00\x00" * 16000)
            duration = wav_duration_ms(wav_path)
            self.assertAlmostEqual(duration, 1000.0, delta=5.0)

    def test_cache_pruning_protects_previews(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            # Create a preview file
            prev_file = cache_dir / "preview-af_heart-1.0.wav"
            prev_file.write_bytes(b"x" * 1000)

            # Create several chunk files
            chunk_files = []
            for i in range(10):
                cf = cache_dir / f"preview-chunk-{i:03d}.wav"
                cf.write_bytes(b"x" * 1000)
                chunk_files.append(cf)

            # Prune with max_bytes=5000 (total is 11000)
            prune_voice_cache(cache_dir, max_bytes=5000, target_bytes=3000, max_files=50)

            # Preview file must still exist
            self.assertTrue(prev_file.is_file())
            # Total size should now be reduced
            remaining_size = sum(f.stat().st_size for f in cache_dir.iterdir() if f.is_file())
            self.assertLessEqual(remaining_size, 5000)

    def test_controller_stop_and_lifecycle(self):
        from PySide6.QtWidgets import QApplication
        app_inst = QApplication.instance() or QApplication([])

        cfg = {"speech_engine": "kokoro", "kokoro_voice": "af_heart", "speech_speed": 1.0}
        saved = {}
        def mock_save(c): saved.update(c)

        with tempfile.TemporaryDirectory() as tmp:
            ctrl = make_controller(None, tmp, cfg, mock_save)
            snap = ctrl.snapshot()
            self.assertEqual(snap["state"], "ready")
            self.assertEqual(snap["voice"], "af_heart")
            self.assertEqual(snap["speed"], 1.0)

            # Test invalidation on stop
            ctrl.token = "test-token"
            ctrl.state = "speaking"
            ctrl.stop()
            self.assertEqual(ctrl.token, "")
            self.assertEqual(ctrl.state, "ready")
            self.assertEqual(ctrl.ready, [])
            self.assertIsNone(ctrl.current)

            ctrl.shutdown()


if __name__ == "__main__":
    unittest.main()
