"""Frozen controller boundary tests; no model, network or production access."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('frozen_runner',Path(__file__).resolve().parents[1]/'scripts/sync_runner.py')
runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
class FrozenRunnerTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name).resolve()
        self.private=self.root/'private';self.runtime=self.private/'runtime'/('a'*40);self.repo=self.root/'repository';self.source=self.root/'source'
        for p in (self.runtime/'scripts',self.repo,self.source):p.mkdir(parents=True)
        for name in runner.SCRIPTS:(self.runtime/'scripts'/name).write_text('# Accepted synthetic code\n')
        self.config=self.private/'config.json';self.config.write_text(json.dumps({'repository':str(self.repo),'source':str(self.source),'stateRoot':str(self.private),'python':str(Path(sys.executable).resolve())}))
        self.manifest={'version':1,'commit':'a'*40,'privateRoot':str(self.private),'runtimeRoot':str(self.runtime),'repository':str(self.repo),'source':str(self.source),'configPath':str(self.config),'configSha256':runner.sha(self.config),'pythonPath':str(Path(sys.executable).resolve()),'pythonSha256':runner.sha(Path(sys.executable)),'scripts':{name:runner.sha(self.runtime/'scripts'/name) for name in runner.SCRIPTS}}
        self.path=self.private/'runtime.json';self.save()
    def save(self):self.path.write_text(json.dumps(self.manifest))
    def test_complete_frozen_runtime_validates(self):self.assertEqual(runner.validate(self.path)[0]['commit'],'a'*40)
    def test_working_tree_controller_is_never_used(self):
        (self.repo/'auto_sync.py').write_text('UNAPPROVED=1')
        fake=subprocess.CompletedProcess([],0,b'{"status":"IDLE"}',b'')
        with patch.object(subprocess,'run',return_value=fake) as call:
            self.assertEqual(runner.run(self.path)['status'],'IDLE')
        command=call.call_args.args[0];self.assertIn('-I',command);self.assertEqual(Path(command[2]),self.runtime/'scripts/auto_sync.py')
        self.assertTrue((self.private/'last-run.json').exists())
    def test_tampered_controller_prevents_any_subprocess(self):
        (self.runtime/'scripts/auto_sync.py').write_text('UNREVIEWED=1')
        with patch.object(subprocess,'run') as call,self.assertRaises(runner.RunnerError):runner.run(self.path)
        call.assert_not_called()
    def test_config_tampering_is_rejected(self):
        self.config.write_text('{}')
        with self.assertRaises(runner.RunnerError):runner.validate(self.path)
    def test_missing_extra_and_traversal_script_names_rejected(self):
        original=copy.deepcopy(self.manifest)
        for scripts in ({},{**original['scripts'],'../other.py':'b'*64},{**original['scripts'],'unknown.py':'b'*64}):
            self.manifest={**original,'scripts':scripts};self.save()
            with self.assertRaises(runner.RunnerError):runner.validate(self.path)
    def test_runtime_outside_private_commit_directory_rejected(self):
        self.manifest['runtimeRoot']=str(self.repo);self.save()
        with self.assertRaises(runner.RunnerError):runner.validate(self.path)
    def test_controller_failure_is_not_success(self):
        with patch.object(subprocess,'run',return_value=subprocess.CompletedProcess([],1,b'',b'private diagnostic')):result=runner.run(self.path)
        self.assertEqual(result['status'],'CONTROLLER_STOPPED');self.assertTrue(result['requiresReview']);self.assertNotIn('private diagnostic',json.dumps(result))
    def test_timeout_is_bounded_and_recorded(self):
        with patch.object(subprocess,'run',side_effect=subprocess.TimeoutExpired('synthetic',110)):result=runner.run(self.path)
        self.assertEqual(result['status'],'TIMEOUT');self.assertEqual(result['returnCode'],2)
    def test_invalid_interpreter_identity_blocks(self):
        self.manifest['pythonSha256']='0'*64;self.save()
        with self.assertRaises(runner.RunnerError):runner.validate(self.path)
    def test_private_root_inside_publication_tree_rejected(self):
        self.manifest['privateRoot']=str(self.repo/'private');self.save()
        with self.assertRaises(runner.RunnerError):runner.validate(self.path)

    def test_malformed_or_unknown_output_never_reports_success(self):
        for output in (b'not JSON',b'{"status":"UNKNOWN"}',b'{"status":"RECOVERY_REQUIRED"}'):
            with patch.object(subprocess,'run',return_value=subprocess.CompletedProcess([],0,output,b'')):result=runner.run(self.path)
            self.assertEqual(result['returnCode'],2);self.assertTrue(result['requiresReview'])

if __name__=='__main__':unittest.main()
