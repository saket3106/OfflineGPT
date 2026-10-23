import unittest
import os
from integrated_runtime import _local_memory_profile
from model_runtime import ModelInfo, estimate_memory, Family, GIB


def make_model_info(architecture='qwen3', block_count=32, file_bytes=4802012992, context_length=40960):
    metadata = {
        'general.architecture': architecture,
        'general.name': f'Mock-{architecture}',
        f'{architecture}.block_count': block_count,
        f'{architecture}.context_length': context_length,
        f'{architecture}.embedding_length': 4096,
        f'{architecture}.attention.head_count': 32,
        f'{architecture}.attention.head_count_kv': 8,
        f'{architecture}.attention.key_length': 128,
        f'{architecture}.attention.value_length': 128,
        'tokenizer.chat_template': {'sha256': 'mock_sha'},
        'split.count': 1,
    }
    return ModelInfo(
        architecture=architecture,
        display_name=f'Mock {architecture}',
        file_bytes=file_bytes,
        context_limit=context_length,
        template_sha256='mock_sha',
        header_sha256='mock_header',
        observation=('file', file_bytes),
        metadata=metadata,
        warnings=(),
    )


class TestHardwareProfiles(unittest.TestCase):
    def test_eco_profile_bounds_resources_conservatively(self):
        # 1. _local_memory_profile in eco mode
        mem = {'total': 32 * GIB, 'available': 20 * GIB, 'pagefile_available': 30 * GIB}
        prof = _local_memory_profile(mem, 8192, hardware_profile='eco')
        self.assertEqual(prof['context'], 2048)
        self.assertEqual(prof['batch'], 32)
        self.assertLessEqual(prof['threads'], 2)
        self.assertEqual(prof['hardware_profile'], 'eco')

        # 2. estimate_memory in eco mode
        info = make_model_info()
        est = estimate_memory(info, 8192, available_ram=8 * GIB, available_vram=6 * GIB, profile='eco')
        self.assertEqual(est['context'], 2048)
        self.assertEqual(est['batch'], 32)
        self.assertLessEqual(est['threads'], 2)
        self.assertEqual(est['profile'], 'eco')

    def test_balanced_profile_parameters(self):
        mem = {'total': 32 * GIB, 'available': 20 * GIB, 'pagefile_available': 30 * GIB}
        prof = _local_memory_profile(mem, 4096, hardware_profile='balanced')
        self.assertEqual(prof['context'], 4096)
        self.assertEqual(prof['batch'], 64)
        self.assertLessEqual(prof['threads'], 4)
        self.assertEqual(prof['hardware_profile'], 'balanced')

        info = make_model_info()
        est = estimate_memory(info, 4096, available_ram=8 * GIB, available_vram=6 * GIB, profile='balanced')
        self.assertEqual(est['context'], 4096)
        self.assertEqual(est['batch'], 64)
        self.assertLessEqual(est['threads'], 4)
        self.assertEqual(est['profile'], 'balanced')

    def test_performance_profile_parameters(self):
        mem = {'total': 32 * GIB, 'available': 20 * GIB, 'pagefile_available': 30 * GIB}
        prof = _local_memory_profile(mem, 8192, hardware_profile='performance')
        self.assertEqual(prof['context'], 8192)
        self.assertEqual(prof['batch'], 128)
        self.assertLessEqual(prof['threads'], 8)
        self.assertEqual(prof['hardware_profile'], 'performance')

        info = make_model_info()
        est = estimate_memory(info, 8192, available_ram=16 * GIB, available_vram=8 * GIB, profile='performance')
        self.assertEqual(est['context'], 8192)
        self.assertEqual(est['batch'], 128)
        self.assertLessEqual(est['threads'], 8)
        self.assertEqual(est['profile'], 'performance')

    def test_custom_profile_parameters(self):
        opts = {'context': 3072, 'batch': 48, 'threads': 3, 'gpu_layers': 24}
        mem = {'total': 32 * GIB, 'available': 20 * GIB, 'pagefile_available': 30 * GIB}
        prof = _local_memory_profile(mem, 4096, hardware_profile='custom', custom_options=opts)
        self.assertEqual(prof['context'], 3072)
        self.assertEqual(prof['batch'], 48)
        self.assertEqual(prof['threads'], 3)
        self.assertEqual(prof['hardware_profile'], 'custom')

        info = make_model_info()
        est = estimate_memory(info, 4096, available_ram=8 * GIB, available_vram=6 * GIB, profile='custom', custom_options=opts)
        self.assertEqual(est['context'], 3072)
        self.assertEqual(est['batch'], 48)
        self.assertEqual(est['threads'], 3)
        self.assertEqual(est['gpu_layers_possible'], 24)

    def test_gpu_offload_allows_model_to_fit_on_constrained_laptop(self):
        # 4.47 GB available RAM, 6.37 GB available VRAM (RTX 4060 laptop scenario)
        ram = int(4.47 * GIB)
        vram = int(6.37 * GIB)
        info = make_model_info(file_bytes=4802012992, block_count=32)

        # In performance mode with GPU offload
        est = estimate_memory(info, 2048, available_ram=ram, available_vram=vram, profile='performance')

        # Pure CPU envelope exceeds available RAM
        self.assertFalse(est['cpuLikelyFits'])
        self.assertGreater(est['cpuEnvelopeBytes'], ram)

        # But with GPU offload, layers are placed in VRAM
        self.assertGreater(est['gpu_layers_possible'], 0)
        self.assertGreater(est['gpu_vram_consumed_bytes'], 0)
        self.assertGreater(est['host_ram_reduction_bytes'], 0)

        # Hybrid CPU envelope fits comfortably in available RAM
        self.assertTrue(est['hybrid_likely_fits'])
        self.assertLessEqual(est['hybrid_cpu_envelope_bytes'], ram)

    def test_unknown_profile_fails_closed(self):
        info = make_model_info()
        with self.assertRaises(ValueError):
            estimate_memory(info, 2048, available_ram=4 * GIB, profile='overclocked_turbo')


if __name__ == '__main__':
    unittest.main()
