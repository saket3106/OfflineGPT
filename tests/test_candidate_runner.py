"""Frozen build-worker input contracts; no builds, networks or user data."""
import importlib.util,json,shutil,sys,tempfile,unittest
from pathlib import Path
spec=importlib.util.spec_from_file_location('frozen_build_worker',Path(__file__).resolve().parents[1]/'scripts/candidate_runner.py')
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
class RunnerTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name).resolve();self.runtime=self.root/'runtime'/('a'*40);(self.runtime/'scripts').mkdir(parents=True)
        for name in r.SCRIPTS:(self.runtime/'scripts'/name).write_text('# Synthetic frozen script\n')
        self.python=self.root/'python.exe';self.python.write_bytes(b'Not executable');self.python.with_name('pythonw.exe').write_bytes(b'Not executable')
        self.config=self.root/'config.json';self.config.write_text(json.dumps({'controllerCommit':'a'*40,'privateRoot':str(self.root)}))
        self.v={'version':1,'commit':'a'*40,'privateRoot':str(self.root),'runtimeRoot':str(self.runtime),'configPath':str(self.config),'configSha256':r.sha(self.config),'pythonPath':str(self.python),'pythonSha256':r.sha(self.python),'pythonwSha256':r.sha(self.python.with_name('pythonw.exe')),'scripts':{n:r.sha(self.runtime/'scripts'/n) for n in r.SCRIPTS}}
        self.path=self.root/'worker.json';self.save()
    def save(self):self.path.write_text(json.dumps(self.v))
    def test_bound_frozen_inputs_validate(self):self.assertEqual(r.validate(self.path)[0]['commit'],'a'*40)
    def test_changed_script_is_rejected(self):
        (self.runtime/'scripts/candidate_builder.py').write_text('Changed')
        with self.assertRaises(r.Error):r.validate(self.path)
    def test_changed_config_or_interpreter_is_rejected(self):
        self.config.write_text('{}')
        with self.assertRaises(r.Error):r.validate(self.path)
        self.v['configSha256']=r.sha(self.config);self.save();self.python.with_name('pythonw.exe').write_bytes(b'Changed')
        with self.assertRaises(r.Error):r.validate(self.path)
    def test_partial_or_traversal_script_sets_fail(self):
        self.v['scripts']={'../outside.py':'a'*64};self.save()
        with self.assertRaises(r.Error):r.validate(self.path)
    def test_editable_runtime_path_is_not_accepted(self):
        self.v['runtimeRoot']=str(self.root/'working');self.save()
        with self.assertRaises(r.Error):r.validate(self.path)
if __name__=='__main__':unittest.main()
