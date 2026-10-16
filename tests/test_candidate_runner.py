"""Frozen build-worker input contracts; no builds, networks or user data."""
import importlib.util,json,shutil,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
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
    def execute_fixture(self,controller):
        module=SimpleNamespace(Builder=lambda config:controller,Error=RuntimeError,auto=SimpleNamespace(Error=ValueError),save=lambda path,value:path.write_text(json.dumps(value)))
        spec=SimpleNamespace(loader=SimpleNamespace(exec_module=lambda module:None))
        with patch.object(r.importlib.util,'spec_from_file_location',return_value=spec),patch.object(r.importlib.util,'module_from_spec',return_value=module):return r.run(self.path)
    def test_corrupted_discovery_does_not_prevent_existing_queue_progress(self):
        controller=Mock();controller.discover.side_effect=ValueError('Synthetic corrupt discovery index');controller.once.return_value={'status':'IDLE'}
        result=self.execute_fixture(controller);controller.once.assert_called_once();self.assertTrue(result['requiresReview']);self.assertEqual(result['returnCode'],2);self.assertIn('Synthetic',result['discoveryIssue'])
    def test_queue_attention_cannot_be_reported_as_healthy_idle(self):
        controller=Mock();controller.discover.return_value=[];controller.once.return_value={'status':'ATTENTION','queueAnomalyCount':1}
        result=self.execute_fixture(controller);self.assertEqual(result['queueAnomalyCount'],1);self.assertTrue(result['requiresReview']);self.assertEqual(result['returnCode'],2)
if __name__=='__main__':unittest.main()
