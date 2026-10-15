import tempfile
import unittest
from pathlib import Path

from PySide6.QtGui import QImage, QImageWriter

import app as offline_app
from image_pipeline import validate_image_file


class FakeImageEngine:
    def __init__(self, root, model_paths):
        self.root = Path(root)
        self.cfg = {
            "flux_width": 960,
            "flux_height": 528,
            "flux_steps": 1,
            "flux_threads": 2,
            "flux_cfg_scale": 4,
            "flux_memory": "low",
            "image_output_format": "png",
            "image_jpeg_quality": 96,
            "upscale_repeats": 1,
            "image_output_directory": str(self.root / "generated"),
        }
        self.model_paths = model_paths

    def image_path(self, key, optional=False):
        if key == "image_output_directory":
            return Path(self.cfg[key])
        return self.model_paths.get(key)


class FluxWorkerImageFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.previous_app_dir = offline_app.APP_DIR
        offline_app.APP_DIR = self.root
        self.models = self.root / "mock-models"
        self.models.mkdir()
        self.model_paths = {}
        for key, name in (
            ("flux_model", "mock-flux.gguf"),
            ("flux_encoder", "mock-qwen.gguf"),
            ("flux_vae", "mock-vae.safetensors"),
            ("flux_upscaler", "4x-UltraSharp.pth"),
        ):
            path = self.models / name
            path.write_bytes(b"test placeholder; inference is mocked")
            self.model_paths[key] = path
        self.source = self.root / "input.png"
        self.write_png(self.source, 960, 528)
        self.engine_cli = self.root / "sd-cli.exe"
        self.engine_cli.write_bytes(b"test placeholder; subprocess is mocked")

        self.orig_mem_status = offline_app.physical_memory_status
        offline_app.physical_memory_status = lambda: {"available": 16 * 1024**3, "total": 32 * 1024**3}

    def tearDown(self):
        offline_app.physical_memory_status = self.orig_mem_status
        offline_app.APP_DIR = self.previous_app_dir
        self.temp.cleanup()

    @staticmethod
    def write_png(path, width, height):
        image = QImage(width, height, QImage.Format_RGB32)
        image.fill(0xFF253040)
        writer = QImageWriter(str(path), b"png")
        if not writer.write(image):
            raise AssertionError(writer.errorString())

    def run_worker(self, prompt, upscale_payload="valid", flux_oom_once=False, performance=False, capabilities=None):
        engine = FakeImageEngine(self.root, self.model_paths)
        if performance:engine.cfg['flux_memory']='performance'
        if capabilities is not None:engine.image_capabilities=lambda _cli:set(capabilities)
        worker = offline_app.FluxWorker("edit_image", prompt, str(self.source), engine)
        worker.commands = []
        worker.pipeline_log = self.root / "logs" / "pipeline.log"
        worker.pipeline_log.parent.mkdir(parents=True, exist_ok=True)

        def fake_run_cli(command, cli, label, stage_log=None, upscale=False):
            worker.commands.append(list(command))
            if label == "flux" and flux_oom_once and sum("--diffusion-model" in c for c in worker.commands) == 1:
                raise RuntimeError("CUDA error: out of memory; cudaMalloc failed: out of memory")
            output = Path(command[command.index("-o") + 1])
            if label == "flux":
                self.write_png(output, 960, 528)
                if stage_log:
                    stage_log.write_text("mock FLUX output\n", encoding="utf-8")
            elif upscale_payload == "svg":
                output.write_bytes(b"<svg>svgsvg</svg>")
                if stage_log:
                    stage_log.write_text(
                        f"[INFO] load {self.model_paths['flux_upscaler']} using torch legacy format\n",
                        encoding="utf-8",
                    )
            else:
                self.write_png(output, 3840, 2112)
                if stage_log:
                    stage_log.write_text(
                        f"[INFO] load {self.model_paths['flux_upscaler']} using torch legacy format\n",
                        encoding="utf-8",
                    )
            return 0.01

        worker._run_cli = fake_run_cli
        return worker._generate_or_edit(engine.cfg, self.engine_cli, "cuda0"), worker

    def test_edit_without_upscale_returns_valid_flux_png(self):
        result, worker = self.run_worker("darken the background while preserving the car")
        image, info = validate_image_file(result["path"], stage="worker edit result")
        self.assertEqual((info.mime_type, info.width, info.height), ("image/png", 960, 528))
        self.assertFalse(image.isNull())
        command = worker.commands[0]
        self.assertIn("--max-vram", command)
        self.assertIn("--vae-tiling", command)
        self.assertEqual(command[command.index("--params-backend") + 1], "disk")

    def test_cuda_oom_retries_once_with_smaller_diffusion_graph(self):
        result, worker = self.run_worker("darken the background while preserving the car", flux_oom_once=True)
        self.assertTrue(Path(result["path"]).is_file())
        self.assertEqual(sum("--diffusion-model" in c for c in worker.commands), 2)
        self.assertLess(int(worker.commands[1][worker.commands[1].index("-W") + 1]), 960)
        self.assertIn("GPU memory was tight", result["notice"])

    def test_performance_uses_only_supported_acceleration_and_keeps_steps(self):
        result,worker=self.run_worker('A car',performance=True,capabilities={'--auto-fit','--sage-attn'})
        command=worker.commands[0]
        self.assertIn('--auto-fit',command);self.assertIn('--sage-attn',command)
        self.assertNotIn('--params-backend',command)
        self.assertEqual(command[command.index('--steps')+1],'1')
        self.assertEqual(worker.image_metrics['parameter_placement'],'auto-fit')

    def test_older_runtime_retains_safe_disk_path(self):
        _,worker=self.run_worker('A car',performance=True,capabilities=set())
        self.assertNotIn('--auto-fit',worker.commands[0]);self.assertNotIn('--sage-attn',worker.commands[0])
        self.assertIn('--offload-to-cpu',worker.commands[0])

    def test_oom_in_performance_returns_to_conservative_placement(self):
        _,worker=self.run_worker('A car',flux_oom_once=True,performance=True,capabilities={'--auto-fit','--sage-attn'})
        self.assertIn('--auto-fit',worker.commands[0]);self.assertIn('--params-backend',worker.commands[1])
        self.assertNotIn('--sage-attn',worker.commands[1]);self.assertEqual(worker.image_metrics['parameter_placement'],'disk')

    def test_edit_then_ultrasharp_returns_valid_4k_png(self):
        result, _ = self.run_worker("darken the background; output in 4K")
        image, info = validate_image_file(result["path"], stage="worker 4K result")
        self.assertEqual((info.mime_type, info.width, info.height), ("image/png", 3840, 2160))
        self.assertFalse(image.isNull())

    def test_invalid_svg_upscale_falls_back_to_valid_flux_png(self):
        result, worker = self.run_worker("darken the background; output in 4K", upscale_payload="svg")
        image, info = validate_image_file(result["path"], stage="worker upscale fallback")
        self.assertEqual((info.mime_type, info.width, info.height), ("image/png", 3840, 2160))
        self.assertFalse(image.isNull())
        self.assertIn("UltraSharp could not finish", result["notice"])
        log = worker.pipeline_log.read_text(encoding="utf-8")
        self.assertIn("first32_hex", log)
        self.assertIn("SVG markup", log)


if __name__ == "__main__":
    unittest.main()
