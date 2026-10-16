"""Real disposable Git plus synthetic artifact/task; never native acceptance."""
import importlib.util,json,shutil,subprocess,sys,time,unittest
from pathlib import Path
from unittest.mock import patch
import test_candidate_builder as queue_tests
b=queue_tests.b
from test_runtime_upgrade import Task
s=importlib.util.spec_from_file_location('candidate_upgrade_tests',Path(__file__).resolve().parents[1]/'scripts/candidate_upgrade.py');u=importlib.util.module_from_spec(s);s.loader.exec_module(u)
class UpgradeTests(unittest.TestCase):
    def setUp(self):
        case=queue_tests.BuilderTests('runTest');case.setUp();self.addCleanup(case.doCleanups);self.case=case;self.builder=case.builder;self.root=case.private;self.old_commit=case.commit
        job=case.enqueue();path,_=self.builder.job(job['buildKey']);graph=job['graph'];package=Path(case.cfg['baselinePackage']);exe=b.inputs.sha(package/'Offline GPT.exe')
        payload=self.root/'fixture-payload.json';b.save(payload,{'Offline GPT.exe':exe});verification=self.root/'fixture-verification.json';b.save(verification,{**graph,'compiledModuleMatches':{'app.py':True},'candidateSha256':exe,'fixtureOnly':True})
        smoke=self.root/'fixture-smoke.json';b.save(smoke,{'status':'PASSED','remainingOwnedPids':[],'modelRequestsIssued':0,'exeSha256':exe,'checks':dict.fromkeys(b.SMOKE_CHECKS,True),'fixtureOnly':True})
        self.pointer={'kind':'VALIDATED_NON_NATIVE_CANDIDATE','buildKey':job['buildKey'],'sourceCommit':self.old_commit,'package':str(package),'exeSha256':exe,'payloadManifest':str(payload),'payloadManifestSha256':b.inputs.sha(payload),'verification':str(verification),'verificationSha256':b.inputs.sha(verification),'smokeReceipt':str(smoke),'smokeSha256':b.inputs.sha(smoke)}
        b.save(self.builder.pointer,self.pointer);self.builder.record(path,'COMPLETE',actualBuildRun=False,fixtureOnly=True,pointerAfter=self.pointer)
        runtime=self.root/'runtime'/self.old_commit/'scripts';runtime.mkdir(parents=True)
        for name in b.SCRIPTS:shutil.copyfile(Path(b.__file__).with_name(name),runtime/name)
        python=self.root/'fixture-python.exe';python.write_bytes(b'Never executed');pythonw=python.with_name('pythonw.exe');pythonw.write_bytes(b'Never executed')
        self.manifest={'version':1,'commit':self.old_commit,'privateRoot':str(self.root),'runtimeRoot':str(runtime.parent),'configPath':str(case.config),'configSha256':b.inputs.sha(case.config),'pythonPath':str(python),'pythonSha256':b.inputs.sha(python),'pythonwSha256':b.inputs.sha(pythonw),'scripts':{n:b.inputs.sha(runtime/n) for n in b.SCRIPTS}}
        self.active=self.root/'active-worker.json';b.save(self.active,self.manifest);self.original=self.active.read_bytes();self.original_candidate=self.builder.pointer.read_bytes()
        case.change('README.md','Synthetic control maintenance')
        self.task=Task({'execute':str(pythonw),'arguments':'-I -S -B "'+str(runtime/'candidate_runner.py')+'" --manifest "'+str(self.active)+'"','workingDirectory':str(self.root)})
        self.upgrade=u.Upgrade(self.active,self.task,fixture=True)
    def prepare(self):return self.upgrade.prepare()['id']
    def scheduled(self,identifier):
        path,value,_=self.upgrade.load(identifier);stamp=value['applied']+1;self.task.last_run=stamp
        b.save(self.root/'last-worker-run.json',{'controllerCommit':value['commit'],'started':stamp,'returnCode':0,'status':'IDLE','actualBuildRun':False,'fixtureOnly':True})
    def test_profile_upgrade_preserves_candidate_and_terminal_history(self):
        identifier=self.prepare();self.upgrade.apply(identifier)
        with self.assertRaises((RuntimeError,OSError)):self.upgrade.verify(identifier)
        self.scheduled(identifier);result=self.upgrade.verify(identifier);self.assertEqual(result['status'],'COMPLETE');self.assertTrue(result['fixtureOnly']);self.assertFalse(result['actualBuildRun']);self.assertEqual(self.builder.pointer.read_bytes(),self.original_candidate)
        new=json.loads(self.active.read_text());cfg=json.loads(Path(new['configPath']).read_text());self.assertEqual(cfg['version'],2);self.assertEqual(len(cfg['historicalJobs']),1)
        module=u.module_at(Path(new['runtimeRoot'])/'scripts/candidate_builder.py','upgraded_fixture_builder');controller=module.Builder(Path(new['configPath']),fixture=True)
        self.assertEqual(controller.once()['status'],'IDLE')
        association=controller.enqueue(controller.manager.head());self.assertEqual(association['status'],'ASSOCIATED');self.assertFalse(association['actualBuildRun']);self.assertEqual(self.builder.pointer.read_bytes(),self.original_candidate)
    def test_pending_work_blocks_without_interrupting_task(self):
        key=self.pointer['buildKey'];path,_=self.builder.job(key);self.builder.record(path,'QUEUED')
        with self.assertRaises(RuntimeError):self.prepare()
        self.assertEqual(self.active.read_bytes(),self.original);self.assertTrue(self.task.enabled)
    def test_application_input_change_requires_real_rebuild(self):
        self.case.change('app.py','VALUE=2\n')
        with self.assertRaisesRegex(RuntimeError,'Application inputs changed'):self.prepare()
        self.assertEqual(self.active.read_bytes(),self.original)
    def test_failed_ci_and_remote_drift_do_not_change_worker(self):
        with patch.object(self.upgrade.manager,'ci',return_value={'status':'completed','conclusion':'failure'}),self.assertRaises(RuntimeError):self.prepare()
        with patch.object(self.upgrade.manager,'remote_head',return_value='a'*40),self.assertRaises(RuntimeError):self.prepare()
        self.assertEqual(self.active.read_bytes(),self.original)
    def test_changed_trigger_blocks_apply(self):
        identifier=self.prepare();self.task.trigger='Human change'
        with self.assertRaises(RuntimeError):self.upgrade.apply(identifier)
        self.assertEqual(self.active.read_bytes(),self.original)
    def test_interrupted_action_switch_can_restore_exact_old_manifest(self):
        identifier=self.prepare()
        with patch.object(self.task,'action',side_effect=RuntimeError('Synthetic interruption')),self.assertRaises(RuntimeError):self.upgrade.apply(identifier)
        self.assertFalse(self.task.enabled);self.assertNotEqual(self.active.read_bytes(),self.original)
        self.assertEqual(self.upgrade.rollback(identifier)['status'],'ROLLED_BACK');self.assertEqual(self.active.read_bytes(),self.original);self.assertTrue(self.task.enabled);self.assertEqual(self.builder.pointer.read_bytes(),self.original_candidate)
    def test_unknown_pointer_or_action_is_never_overwritten(self):
        identifier=self.prepare();self.active.write_text('{}')
        with self.assertRaises(RuntimeError):self.upgrade.rollback(identifier)
        self.assertEqual(self.active.read_text(),'{}');self.active.write_bytes(self.original);self.task.current['arguments']='Human change'
        with self.assertRaises(RuntimeError):self.upgrade.rollback(identifier)
        self.assertEqual(self.task.current['arguments'],'Human change')
    def test_historical_job_mutation_blocks_upgrade(self):
        identifier=self.prepare();path,_=self.builder.job(self.pointer['buildKey']);self.builder.record(path,'COMPLETE',extra='Unexpected change')
        with self.assertRaises(RuntimeError):self.upgrade.apply(identifier)
        self.assertEqual(self.active.read_bytes(),self.original)
    def test_running_task_is_not_force_closed(self):
        self.task.running=True
        with self.assertRaises(RuntimeError):self.prepare()
        self.assertTrue(self.task.running);self.assertEqual(self.active.read_bytes(),self.original)
    def test_fixture_cannot_create_a_real_windows_task_adapter(self):
        with self.assertRaises(RuntimeError):u.Upgrade(self.active,fixture=True)
    def test_repeatable_upgrade_uses_fresh_accepted_profile(self):
        first=self.prepare();self.upgrade.apply(first);self.scheduled(first);self.upgrade.verify(first)
        self.case.change('README.md','Next synthetic control maintenance');second=u.Upgrade(self.active,self.task,fixture=True);identifier=second.prepare()['id'];second.apply(identifier)
        path,value,_=second.load(identifier);self.task.last_run=value['applied']+1;b.save(self.root/'last-worker-run.json',{'controllerCommit':value['commit'],'started':self.task.last_run,'returnCode':0,'status':'IDLE','fixtureOnly':True})
        self.assertEqual(second.verify(identifier)['status'],'COMPLETE');self.assertEqual(self.builder.pointer.read_bytes(),self.original_candidate)
    def test_corrupt_retained_artifact_is_not_associated_or_upgraded(self):
        (Path(self.pointer['package'])/'unexpected.dll').write_bytes(b'Synthetic injection')
        with self.assertRaises(RuntimeError):self.prepare()
        self.assertEqual(self.active.read_bytes(),self.original);self.assertTrue(self.task.enabled)
    def test_cli_rejects_changed_dependency_before_any_sibling_import(self):
        directory=self.root/'admin-runtime'/('a'*40)/'scripts';directory.mkdir(parents=True)
        for name in u.ADMIN:shutil.copyfile(Path(u.__file__).with_name(name),directory/name)
        manifest={'commit':'a'*40,'scripts':{n:b.inputs.sha(directory/n) for n in u.ADMIN}};b.save(directory.parent/'admin-manifest.json',manifest)
        marker=self.root/'must-not-exist';(directory/'candidate_builder.py').write_text('from pathlib import Path\nPath('+repr(str(marker))+').write_text("unreviewed import")\n')
        result=subprocess.run([sys.executable,'-I','-S','-B',str(directory/'candidate_upgrade.py'),'--manifest',str(self.active),'prepare'],capture_output=True,timeout=5)
        self.assertNotEqual(result.returncode,0);self.assertFalse(marker.exists());self.assertEqual(self.active.read_bytes(),self.original)
if __name__=='__main__':unittest.main()
