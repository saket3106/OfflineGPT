"""Independent helper contracts; actual layout/interaction is checked in Qt."""
import json
import re
import shutil
import subprocess
import unittest
import tempfile
from unittest.mock import patch
from pathlib import Path


class ImagePresentationContracts(unittest.TestCase):
    def test_isolated_library_discovery_and_diagnostics_use_active_profile(self):
        import app
        from PIL import Image
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);package=root/'app';profile=root/'profile'
            for directory in [package/'generated',profile/'generated',root/'Offline GPT/generated']:directory.mkdir(parents=True)
            Image.new('RGB',(8,8),'red').save(package/'generated/other-profile.png')
            Image.new('RGB',(8,8),'blue').save(root/'Offline GPT/generated/legacy.png')
            Image.new('RGB',(8,8),'green').save(profile/'generated/own.png')
            with patch.object(app,'APP_DIR',package),patch.object(app,'DATA_DIR',profile),patch.object(app,'DB_PATH',profile/'test.sqlite3'),patch.object(app,'load_config',return_value={}):
                store=app.Store()
                try:
                    paths=[Path(row[0]) for row in store.con.execute('SELECT path FROM library_images')]
                    self.assertEqual(paths,[profile/'generated/own.png'])
                    self.assertTrue((profile/'thumbnails').is_dir());self.assertFalse((package/'thumbnails').exists())
                    app.ChatWorker._log_pipeline_diagnostics(None,'public response',0,0,0,0)
                    self.assertTrue((profile/'logs/intelligence.log').is_file());self.assertFalse((package/'logs').exists())
                finally:store.con.close()
            # The ordinary portable profile retains existing legacy discovery.
            with patch.object(app,'APP_DIR',package),patch.object(app,'DATA_DIR',package),patch.object(app,'DB_PATH',package/'portable.sqlite3'),patch.object(app,'load_config',return_value={}):
                store=app.Store()
                try:self.assertEqual(store.con.execute('SELECT count(*) FROM library_images').fetchone()[0],2)
                finally:store.con.close()

    def test_intrinsic_dimensions_and_inspector_targeting(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node is required for actual frontend helper execution')
        source = (Path(__file__).resolve().parents[1] / 'frontend-source/src/main.jsx').read_text(encoding='utf-8')
        functions = []
        for name in ['imageSizeFromPixels', 'isInspectableModelSetting']:
            match = re.search(r'function ' + name + r'\([^\n]+\) \{.*?\n\}', source, re.S)
            self.assertIsNotNone(match)
            functions.append(match.group())
        script = '\n'.join(functions) + r'''
const assert = require('assert/strict');
for (const [width,height] of [[3840,2160],[2160,3840],[7680,4320]]) {
  assert.equal(imageSizeFromPixels(width,height).is4K,true);
}
for (const [width,height] of [[512,320],[3840,1080],[2880,2880],[1920,1080]]) {
  assert.equal(imageSizeFromPixels(width,height).is4K,false);
}
for (const [width,height] of [[0,2160],[-1,2],[NaN,1],[Infinity,2],[1.5,2],['3840',2160]]) {
  assert.equal(imageSizeFromPixels(width,height),null);
}
assert.equal(imageSizeFromPixels(512,320).label,'512 × 320');
for (const name of ['gpt_oss_gguf','vision_model','vision_projector','flux_encoder']) {
  assert.equal(isInspectableModelSetting(name,'model.GGUF'),true);
}
for (const [name,value] of [['model_keepalive_seconds',90],['model_lifecycle_mode','balanced'],['voice_model_path','voice.bin'],['flux_model','model.safetensors'],['arbitrary_model','file.gguf']]) {
  assert.equal(isInspectableModelSetting(name,value),false);
}
console.log(JSON.stringify({passed:23}));
'''
        result = subprocess.run([node, '-e', script], check=True, capture_output=True, text=True, timeout=15)
        self.assertEqual(json.loads(result.stdout)['passed'], 23)


if __name__ == '__main__':
    unittest.main()
