"""Run the exported synthetic suite with a new temporary data profile."""
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
with tempfile.TemporaryDirectory(prefix='offlinegpt-source-tests-') as profile:
    os.environ['OFFLINE_GPT_DATA_DIR'] = profile
    os.environ['OFFLINE_GPT_CAPTURE_FITTED_PROMPTS'] = '0'
    os.environ['OFFLINE_GPT_SAVE_TRAINING_CANDIDATES'] = '0'
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    suite = unittest.defaultTestLoader.discover(str(ROOT / 'tests'), pattern='test_*.py')
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    raise SystemExit(not result.wasSuccessful())
