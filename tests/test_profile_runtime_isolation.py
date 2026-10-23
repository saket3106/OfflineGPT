import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
import integrated_runtime as runtime
from model_runtime import ModelBehavior,Family

class ProfileIsolationTests(unittest.TestCase):
    def test_runtime_diagnostics_follow_profile_not_executable(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);application=root/'sealed';application.mkdir();profile=root/'profile'
            with patch.object(runtime,'APP_DIR',application),patch.object(runtime,'CONFIG_PATH',profile/'offline_gpt_config.json'),patch.object(runtime,'load_config',return_value=dict(runtime.DEFAULT_CONFIG)):
                engine=runtime.EngineManager()
                try:
                    self.assertEqual(engine.log_dir,profile/'logs')
                    self.assertTrue((profile/'logs').is_dir());self.assertFalse((application/'logs').exists())
                finally:engine.shutdown()
    def test_qwen_standard_and_deep_are_explicit_and_budget_independent(self):
        b=ModelBehavior(Family.QWEN3,'fixture','metadata',True)
        for budget in (200,700,2400):
            self.assertEqual(b.generation_options('medium',budget),{'chat_template_kwargs':{'enable_thinking':False}})
            self.assertEqual(b.generation_options('high',budget),{'chat_template_kwargs':{'enable_thinking':True}})
        self.assertEqual(ModelBehavior(Family.QWEN35).generation_options('medium',700),{})
        self.assertEqual(ModelBehavior().template_kwargs('medium'),{'reasoning_effort':'medium'})
