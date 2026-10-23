"""Deterministic regression tests for centralized Whisper backend policy and worker lifecycle."""
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import app
import integrated_runtime
from app import select_whisper_backend, WhisperTranscriptionWorker, _WARM_WHISPER


class TestSelectWhisperBackend(unittest.TestCase):
    """Direct tests for the centralized production decision function."""

    def test_explicit_cpu_always_selects_cpu(self):
        self.assertEqual(select_whisper_backend('cpu', None), ('cpu', 'explicit_cpu'))
        self.assertEqual(select_whisper_backend('cpu', 0), ('cpu', 'explicit_cpu'))
        self.assertEqual(select_whisper_backend('cpu', 16 * 1024**3), ('cpu', 'explicit_cpu'))
        self.assertEqual(select_whisper_backend('CPU ', 8 * 1024**3), ('cpu', 'explicit_cpu'))

    def test_explicit_cuda_satisfied_with_sufficient_vram(self):
        sufficient = 2000 * 1024**2
        self.assertEqual(select_whisper_backend('cuda', sufficient), ('cuda', 'explicit_cuda_satisfied'))
        self.assertEqual(select_whisper_backend('CUDA', 1400 * 1024**2), ('cuda', 'explicit_cuda_satisfied'))

    def test_explicit_cuda_fails_with_insufficient_vram_by_default(self):
        insufficient = 800 * 1024**2
        with self.assertRaisesRegex(RuntimeError, "Explicit CUDA voice backend requested, but available VRAM"):
            select_whisper_backend('cuda', insufficient, allow_cuda_fallback=False)

        with self.assertRaisesRegex(RuntimeError, "Explicit CUDA voice backend requested, but available VRAM"):
            select_whisper_backend('cuda', None, allow_cuda_fallback=False)

    def test_explicit_cuda_fallback_when_allowed(self):
        insufficient = 800 * 1024**2
        self.assertEqual(
            select_whisper_backend('cuda', insufficient, allow_cuda_fallback=True),
            ('cpu', 'cuda_fallback_insufficient_vram')
        )

    def test_auto_selects_cuda_when_vram_sufficient(self):
        sufficient = 1500 * 1024**2
        self.assertEqual(select_whisper_backend('auto', sufficient), ('cuda', 'auto_cuda'))
        self.assertEqual(select_whisper_backend(None, sufficient), ('cuda', 'auto_cuda'))

    def test_auto_selects_cpu_when_vram_insufficient_or_none(self):
        insufficient = 1000 * 1024**2
        self.assertEqual(select_whisper_backend('auto', insufficient), ('cpu', 'auto_cpu'))
        self.assertEqual(select_whisper_backend('auto', None), ('cpu', 'auto_cpu'))
        self.assertEqual(select_whisper_backend(None, None), ('cpu', 'auto_cpu'))


class TestWhisperTranscriptionWorkerInteractions(unittest.TestCase):
    """Worker-level interaction tests verifying chat unloading, mode selection, and warm reuse."""

    def setUp(self):
        self.fake_engine = MagicMock()
        self.fake_engine.resource_lock.acquire.return_value = True
        self.fake_engine._unload_chat = MagicMock()

        self.dummy_model = MagicMock()
        self.dummy_model.is_file.return_value = True
        self.dummy_model.stat.return_value.st_size = 500 * 1024 * 1024  # 500 MB

        self.dummy_server = MagicMock()
        self.dummy_server.is_file.return_value = True
        self.dummy_server.parent = Path(r"C:\fake")

    def _create_worker(self, cfg=None):
        config = {'voice_server_path': 'fake.exe', 'voice_model_path': 'fake.bin'}
        if cfg:
            config.update(cfg)
        worker = WhisperTranscriptionWorker(
            pcm=b"\x00" * 16000,
            sample_rate=16000,
            cfg=config,
            parent=None,
            prewarm_only=True
        )
        worker.engine = self.fake_engine
        worker._path = lambda key: self.dummy_server if 'server' in key else self.dummy_model
        return worker

    def test_worker_auto_adequate_ram_cpu_fallback_does_not_unload_chat(self):
        worker = self._create_worker({'voice_backend': 'auto'})
        fake_ram = {'available': 16 * 1024**3}
        fake_cuda = 1000 * 1024**2  # 1 GiB free VRAM (< 1.4 GiB threshold)

        with patch.object(app, 'available_cuda_memory', return_value=fake_cuda), \
             patch.object(integrated_runtime, 'available_cuda_memory', return_value=fake_cuda), \
             patch.object(app, 'physical_memory_status', return_value=fake_ram), \
             patch.object(app, 'stop_warm_whisper'), \
             patch.dict(_WARM_WHISPER, {'process': None, 'key': None, 'lock': MagicMock()}):
            
            worker.run()
            self.fake_engine._unload_chat.assert_not_called()
            self.assertEqual(worker.metrics.get('selectedBackend'), 'cpu')
            self.assertEqual(worker.metrics.get('backendReason'), 'auto_cpu')

    def test_worker_explicit_cpu_does_not_unload_chat_even_with_high_vram(self):
        worker = self._create_worker({'voice_backend': 'cpu'})
        fake_ram = {'available': 16 * 1024**3}
        fake_cuda = 8000 * 1024**2  # 8 GiB free VRAM

        with patch.object(app, 'available_cuda_memory', return_value=fake_cuda), \
             patch.object(integrated_runtime, 'available_cuda_memory', return_value=fake_cuda), \
             patch.object(app, 'physical_memory_status', return_value=fake_ram), \
             patch.object(app, 'stop_warm_whisper'), \
             patch.dict(_WARM_WHISPER, {'process': None, 'key': None, 'lock': MagicMock()}):
            
            worker.run()
            self.fake_engine._unload_chat.assert_not_called()
            self.assertEqual(worker.metrics.get('selectedBackend'), 'cpu')
            self.assertEqual(worker.metrics.get('backendReason'), 'explicit_cpu')

    def test_worker_low_ram_triggers_chat_unload_for_safety(self):
        worker = self._create_worker({'voice_backend': 'auto'})
        fake_ram = {'available': 600 * 1024**2}  # 600 MB (< 500 MB model + 700 MB headroom)
        fake_cuda = 2000 * 1024**2

        with patch.object(app, 'available_cuda_memory', return_value=fake_cuda), \
             patch.object(integrated_runtime, 'available_cuda_memory', return_value=fake_cuda), \
             patch.object(app, 'physical_memory_status', return_value=fake_ram), \
             patch.object(app, 'stop_warm_whisper'), \
             patch.dict(_WARM_WHISPER, {'process': None, 'key': None, 'lock': MagicMock()}):
            
            worker.run()
            self.fake_engine._unload_chat.assert_called()

    def test_worker_explicit_cuda_low_vram_triggers_unload_to_free_vram(self):
        worker = self._create_worker({'voice_backend': 'cuda'})
        fake_ram = {'available': 16 * 1024**3}
        initial_cuda = 800 * 1024**2   # 800 MB initially
        freed_cuda = 2000 * 1024**2     # 2000 MB after unload

        with patch.object(app, 'available_cuda_memory', side_effect=[initial_cuda, freed_cuda, freed_cuda]), \
             patch.object(integrated_runtime, 'available_cuda_memory', side_effect=[initial_cuda, freed_cuda, freed_cuda]), \
             patch.object(app, 'physical_memory_status', return_value=fake_ram), \
             patch.object(app, 'stop_warm_whisper'), \
             patch.dict(_WARM_WHISPER, {'process': None, 'key': None, 'lock': MagicMock()}):
            
            worker.run()
            self.fake_engine._unload_chat.assert_called()
            self.assertEqual(worker.metrics.get('selectedBackend'), 'cuda')

    def test_worker_warm_worker_reuse_on_exact_backend_match(self):
        worker = self._create_worker({'voice_backend': 'cuda', 'voice_threads': 4})
        fake_ram = {'available': 16 * 1024**3}
        fake_cuda = 2000 * 1024**2

        mock_process = MagicMock()
        mock_process.poll.return_value = None
        exact_key = (str(self.dummy_server), str(self.dummy_model), 4, 'cuda')

        with patch.object(app, 'available_cuda_memory', return_value=fake_cuda), \
             patch.object(integrated_runtime, 'available_cuda_memory', return_value=fake_cuda), \
             patch.object(app, 'physical_memory_status', return_value=fake_ram), \
             patch.dict(_WARM_WHISPER, {'process': mock_process, 'port': 8080, 'key': exact_key, 'lock': MagicMock()}):
            
            worker.run()
            self.assertTrue(worker.metrics.get('warmSession'))
            self.assertTrue(worker.succeeded)

    def test_worker_warm_worker_stopped_on_backend_mismatch(self):
        # Warm worker is CPU, but request is CUDA
        worker = self._create_worker({'voice_backend': 'cuda', 'voice_threads': 4})
        fake_ram = {'available': 16 * 1024**3}
        fake_cuda = 2000 * 1024**2

        mock_process = MagicMock()
        mock_process.poll.return_value = None
        mismatched_key = (str(self.dummy_server), str(self.dummy_model), 4, 'cpu')

        with patch.object(app, 'available_cuda_memory', return_value=fake_cuda), \
             patch.object(integrated_runtime, 'available_cuda_memory', return_value=fake_cuda), \
             patch.object(app, 'physical_memory_status', return_value=fake_ram), \
             patch.object(app, 'stop_warm_whisper') as mock_stop, \
             patch.dict(_WARM_WHISPER, {'process': mock_process, 'port': 8080, 'key': mismatched_key, 'lock': MagicMock()}):
            
            worker.run()
            self.assertGreaterEqual(mock_stop.call_count, 1)
            self.assertFalse(worker.metrics.get('warmSession', False))


if __name__ == '__main__':
    unittest.main()
