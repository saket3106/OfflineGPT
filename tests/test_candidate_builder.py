"""Queue/recovery contracts with real disposable Git; fake builds are labeled."""
import importlib.util,json,os,shutil,unittest
from pathlib import Path
from unittest.mock import patch
import test_auto_sync as fixtures
spec=importlib.util.spec_from_file_location('candidate_queue_tests',Path(__file__).resolve().parents[1]/'scripts/candidate_builder.py')
b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)
class BuilderTests(unittest.TestCase):
    def setUp(self):
        f=fixtures.AutoSyncTests('runTest');f.setUp();self.addCleanup(f.doCleanups);self.f=f
        names=['app.py','Offline GPT.spec','assistant_system_prompt.md','frontend-source/package.json','frontend-source/pnpm-lock.yaml','frontend-source/vite.config.js']
        for name in names:
            p=f.repo/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('VALUE=1\n' if name=='app.py' else 'Synthetic build input\n')
        policy=json.loads((f.repo/'scripts/source_sync_policy.json').read_text());policy['managed']=names;(f.repo/'scripts/source_sync_policy.json').write_text(json.dumps(policy))
        for name in b.SCRIPTS:shutil.copyfile(Path(b.__file__).with_name(name),f.repo/'scripts'/name)
        f.g('add','--all');f.g('commit','-qm','Synthetic builder inputs');f.g('push','-q','origin','HEAD:refs/heads/codex/test')
        self.commit=f.g('rev-parse','HEAD').decode().strip();self.private=f.f.root/'build-private';self.private.mkdir()
        roots={k:str(self.private/k) for k in ('pythonBase','pythonEnvironment','frontendDependencies')}
        value={'version':1,'roots':roots,'source':str(f.source),'repository':str(f.repo),'files':{},'internalLinks':{},'executables':{},'executableHashes':{}}
        value['environmentSha256']=b.inputs.digest(value);receipt=self.private/'environment.json';receipt.write_text(json.dumps(value))
        baseline=self.private/'baseline';baseline.mkdir();(baseline/'Offline GPT.exe').write_bytes(b'Synthetic candidate, never executed')
        graph=b.inputs.inputs(f.repo,self.commit,f.f.git)
        self.cfg={'version':1,'syncConfig':str(f.config),'privateRoot':str(self.private),'controllerCommit':self.commit,'environmentReceipt':str(receipt),'environmentReceiptSha256':b.inputs.sha(receipt),
          'executableInputs':{p:h for p,h in graph['files'].items() if p!='app.py' and p!='assistant_system_prompt.md'},'allowedPth':{},'systemInputs':{},'baselinePackage':str(baseline),'baselineExeSha256':b.inputs.sha(baseline/'Offline GPT.exe')}
        self.config=self.private/'config.json';self.config.write_text(json.dumps(self.cfg));self.builder=b.Builder(self.config,fixture=True)
        self.original_pointer=self.builder.pointer.read_bytes()
    def change(self,name,text):
        (self.f.repo/name).write_text(text);self.f.g('add',name);self.f.g('commit','-qm','Synthetic next revision');self.f.g('push','-q','origin','HEAD:refs/heads/codex/test');return self.f.g('rev-parse','HEAD').decode().strip()
    def enqueue(self,required=True):return self.builder.enqueue(self.commit,required)
    def test_identical_docs_revision_coalesces_without_losing_ci_evidence(self):
        first=self.enqueue();next_commit=self.change('README.md','Synthetic docs maintenance')
        second=self.builder.enqueue(next_commit);self.assertEqual(first['buildKey'],second['buildKey']);self.assertEqual(len(second['acceptedSources']),2)
        self.assertEqual(len(list((self.private/'jobs').iterdir())),1)
    def test_required_revisions_are_never_coalesced_or_dropped_when_inputs_differ(self):
        first=self.enqueue();next_commit=self.change('app.py','VALUE=2\n');second=self.builder.enqueue(next_commit)
        self.assertNotEqual(first['buildKey'],second['buildKey']);self.assertEqual(self.builder.job(first['buildKey'])[1]['status'],'QUEUED')
    def test_only_explicitly_optional_pending_revision_can_be_superseded(self):
        first=self.enqueue(False);next_commit=self.change('app.py','VALUE=2\n');second=self.builder.enqueue(next_commit)
        self.assertEqual(self.builder.job(first['buildKey'])[1]['status'],'SUPERSEDED');self.assertEqual(self.builder.job(first['buildKey'])[1]['supersededBy'],second['buildKey'])
    def test_unreviewed_executable_inputs_and_failed_ci_are_rejected(self):
        with patch.object(self.builder.manager,'ci',return_value={'status':'completed','conclusion':'failure'}),self.assertRaises(b.Error):self.enqueue()
        changed=self.change('frontend-source/vite.config.js','Unreviewed executable config')
        with self.assertRaises(b.Error):self.builder.enqueue(changed)
        self.assertEqual(self.builder.pointer.read_bytes(),self.original_pointer)
    def test_build_failure_retains_candidate_and_sibling_progress(self):
        first=self.enqueue();second=self.builder.enqueue(self.change('app.py','VALUE=2\n'))
        def fail(path,job):self.builder.record(path,'PACKAGING',attempts=job['attempts']+1);raise b.Error('Synthetic packaging failure')
        with patch.object(self.builder,'execute',side_effect=fail):self.assertEqual(self.builder.once()['status'],'FAILED')
        self.assertEqual(self.builder.pointer.read_bytes(),self.original_pointer)
        with patch.object(self.builder,'execute',side_effect=lambda path,job:self.builder.record(path,'COMPLETE',fixtureOnly=True,actualBuildRun=False)):
            result=self.builder.once();self.assertEqual(result['buildKey'],second['buildKey'])
        self.assertEqual(self.builder.job(first['buildKey'])[1]['status'],'FAILED')
    def test_interrupted_phase_requires_recovery_and_preserves_outputs(self):
        job=self.enqueue();path,_=self.builder.job(job['buildKey']);self.builder.record(path,'PACKAGING',attempts=1)
        self.assertEqual(self.builder.once()['status'],'RECOVERY_REQUIRED');self.assertEqual(self.builder.pointer.read_bytes(),self.original_pointer)
        self.assertEqual(self.builder.retry(job['buildKey'])['status'],'QUEUED')
    def test_retry_budget_and_cancellation_are_explicit(self):
        job=self.enqueue();key=job['buildKey'];self.assertEqual(self.builder.cancel(key)['status'],'CANCELLED');self.assertEqual(self.builder.retry(key)['status'],'QUEUED')
        path,_=self.builder.job(key);self.builder.record(path,'FAILED',attempts=2)
        with self.assertRaises(b.Error):self.builder.retry(key)
    def test_build_lock_does_not_block_source_publication_discovery(self):
        with b.auto.exclusive(self.builder.lock):
            with self.assertRaises(b.auto.Error):self.builder.once()
            self.assertEqual(self.builder.manager.once()['status'],'IDLE')
    def test_unknown_promotion_pointer_is_never_overwritten(self):
        job=self.enqueue();path,_=self.builder.job(job['buildKey']);old=json.loads(self.original_pointer)
        self.builder.record(path,'RECOVERY_REQUIRED',pointerBefore=old,pointerAfter={**old,'exeSha256':'0'*64})
        b.save(self.builder.pointer,{'human':'changed'});before=self.builder.pointer.read_bytes()
        with self.assertRaises(b.Error):self.builder.recover(job['buildKey'])
        self.assertEqual(self.builder.pointer.read_bytes(),before)
    def test_old_pointer_recovery_keeps_original_and_allows_bounded_retry(self):
        job=self.enqueue();path,_=self.builder.job(job['buildKey']);old=json.loads(self.original_pointer)
        self.builder.record(path,'RECOVERY_REQUIRED',attempts=1,pointerBefore=old,pointerAfter={**old,'exeSha256':'0'*64})
        self.assertEqual(self.builder.recover(job['buildKey'])['status'],'FAILED')
        self.assertEqual(self.builder.retry(job['buildKey'])['status'],'QUEUED');self.assertEqual(self.builder.pointer.read_bytes(),self.original_pointer)
    def test_dependency_declaration_change_requires_renewed_profile_review(self):
        changed=self.change('requirements.txt','AChangedDependency==9\n')
        with self.assertRaises(b.Error):self.builder.enqueue(changed)
    def test_discovery_is_persistent_bounded_and_does_not_block_existing_jobs(self):
        first=self.enqueue();batch=self.builder.manager.state/'batches'/'synthetic-source-batch';batch.mkdir()
        b.save(batch/'batch.json',{'status':'COMPLETE','candidateBuild':'QUEUED_FOR_SEPARATE_VERIFIED_BUILD_CONTROLLER','commit':self.commit})
        with patch.object(self.builder,'enqueue',side_effect=b.Error('Synthetic network interruption')):
            for i in range(8):
                with patch.object(b.time,'time',return_value=1000000+i*10000):result=self.builder.discover()
            self.assertEqual(result[0]['status'],'ATTENTION');self.assertEqual(self.builder.discover(),[])
        self.assertEqual(self.builder.job(first['buildKey'])[1]['status'],'QUEUED')
    def test_discovery_deduplicates_accepted_batches(self):
        batch=self.builder.manager.state/'batches'/'synthetic-source-batch';batch.mkdir()
        b.save(batch/'batch.json',{'status':'COMPLETE','candidateBuild':'QUEUED_FOR_SEPARATE_VERIFIED_BUILD_CONTROLLER','commit':self.commit})
        self.assertEqual(len(self.builder.discover()),1)
        with patch.object(self.builder,'enqueue',side_effect=AssertionError('Must not rediscover')):self.assertEqual(self.builder.discover(),[])
    def test_resource_deferral_preserves_queue_and_attempt_budget(self):
        job=self.enqueue()
        with patch.object(b.Resources,'preflight',side_effect=b.Deferred('Synthetic active user app')):
            result=self.builder.once();self.assertEqual(result['status'],'WAITING_RESOURCE');self.assertEqual(result['attempts'],0)
        self.assertEqual(self.builder.once()['status'],'IDLE');self.assertEqual(self.builder.pointer.read_bytes(),self.original_pointer)
        self.assertEqual(self.builder.cancel(job['buildKey'])['status'],'CANCELLED')
    def test_preflight_network_retry_is_bounded_without_consuming_build_attempts(self):
        job=self.enqueue();path,_=self.builder.job(job['buildKey'])
        with patch.object(self.builder,'accepted',side_effect=b.auto.NetworkError('Synthetic offline state')):
            for i in range(8):
                with patch.object(b.time,'time',return_value=1000000+i*10000):result=self.builder.once()
        self.assertEqual(result['status'],'BLOCKED');self.assertEqual(result['attempts'],0);self.assertEqual(result['networkRetries'],8)

class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.job={'sourceCommit':'a'*40,'graph':{'files':{'app.py':'a','tool.py':'b'},'applicationInputSha256':'b'*64}}
        self.smoke={'status':'PASSED','remainingOwnedPids':[],'modelRequestsIssued':0,'exeSha256':'c'*64,'checks':dict.fromkeys(b.SMOKE_CHECKS,True)}
        self.verification={'sourceCommit':'a'*40,'compiledModuleMatches':{'app.py':True,'tool.py':True},'candidateSha256':'c'*64,'applicationInputSha256':'b'*64}
    def test_complete_non_native_evidence_is_required(self):b.acceptance(self.smoke,self.verification,self.job,'c'*64)
    def test_empty_partial_false_or_native_receipts_never_pass(self):
        for changed in ({'checks':{}},{'checks':{'normal_exit':True}},{'modelRequestsIssued':1},{'remainingOwnedPids':[123]},{'exeSha256':'d'*64}):
            with self.subTest(changed=changed),self.assertRaises(b.Error):b.acceptance({**self.smoke,**changed},self.verification,self.job,'c'*64)
        for changed in ({'compiledModuleMatches':{}},{'compiledModuleMatches':{'app.py':True}},{'sourceCommit':'d'*40},{'candidateSha256':'d'*64}):
            with self.subTest(changed=changed),self.assertRaises(b.Error):b.acceptance(self.smoke,{**self.verification,**changed},self.job,'c'*64)
    @unittest.skipUnless(os.name=='nt','Actual Windows event monitor')
    def test_event_monitor_ignores_inherited_powershell_module_injection(self):
        with patch.dict(os.environ,{'PSModulePath':str(Path(b.__file__).parent/'nonexistent-modules')}):b.Resources().incidents()
if __name__=='__main__':unittest.main()
