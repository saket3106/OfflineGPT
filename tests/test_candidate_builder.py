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
    def synthetic_completed_artifact(self,associated=False):
        job=self.enqueue();path,_=self.builder.job(job['buildKey']);package=Path(self.cfg['baselinePackage']);exe=package/'Offline GPT.exe';h=b.inputs.sha(exe)
        payload=self.private/'payload.json';payload.write_text(json.dumps({'Offline GPT.exe':h}))
        verification=self.private/'verification.json';verification.write_text(json.dumps({'sourceCommit':job['sourceCommit'],'candidateSha256':h,'applicationInputSha256':job['graph']['applicationInputSha256'],'files':job['graph']['files'],'compiledModuleMatches':{n:True for n in job['graph']['files'] if '/' not in n and n.endswith('.py')}}))
        smoke=self.private/'smoke.json';smoke.write_text(json.dumps({'status':'PASSED','remainingOwnedPids':[],'modelRequestsIssued':0,'exeSha256':h,'checks':dict.fromkeys(b.SMOKE_CHECKS,True),'fixtureOnly':True}))
        key='a'*64 if associated else job['buildKey']
        pointer={'package':str(package),'exeSha256':h,'buildKey':key,'sourceCommit':job['sourceCommit'],'payloadManifest':str(payload),'payloadManifestSha256':b.inputs.sha(payload),'verification':str(verification),'verificationSha256':b.inputs.sha(verification),'smokeReceipt':str(smoke),'smokeSha256':b.inputs.sha(smoke)}
        if associated:
            original=self.private/'jobs'/key;original.mkdir();b.save(original/'job.json',{**job,'buildKey':key,'status':'COMPLETE','actualBuildRun':False,'fixtureOnly':True,'pointerAfter':pointer})
            self.builder.record(path,'ASSOCIATED',actualBuildRun=False,association={'buildKey':key,'candidateSha256':h})
        else:self.builder.record(path,'COMPLETE',actualBuildRun=False,fixtureOnly=True,pointerAfter=pointer)
        b.save(self.builder.pointer,pointer);return job['buildKey'],exe
    def test_corrupt_completed_payload_never_accepts_new_source_from_cached_status(self):
        key,exe=self.synthetic_completed_artifact();path,_=self.builder.job(key);before=(path/'job.json').read_bytes();exe.write_bytes(b'Corrupt synthetic artifact, never executed')
        next_commit=self.change('README.md','Synthetic next accepted documentation')
        with self.assertRaisesRegex(b.Error,'reuse refused'):self.builder.enqueue(next_commit)
        self.assertEqual((path/'job.json').read_bytes(),before);self.assertEqual(len(list((self.private/'reuse-refusals').glob('*.json'))),1)
    def test_corrupt_associated_payload_is_revalidated_not_cached(self):
        key,exe=self.synthetic_completed_artifact(True);path,_=self.builder.job(key);before=(path/'job.json').read_bytes();exe.write_bytes(b'Corrupt associated artifact, never executed')
        with self.assertRaisesRegex(b.Error,'reuse refused'):self.builder.enqueue(self.change('README.md','Synthetic documentation after association'))
        self.assertEqual((path/'job.json').read_bytes(),before)
    def assert_valid_reuse(self,associated):
        key,exe=self.synthetic_completed_artifact(associated);before=self.builder.pointer.read_bytes();old=self.builder.job(key)[1]['sourceCommit'];new=self.change('README.md','Synthetic valid reuse request')
        result=self.builder.enqueue(new);self.assertEqual(result['sourceCommit'],old);self.assertEqual(result['lastReuseVerification']['requestedSourceCommit'],new);self.assertEqual(self.builder.pointer.read_bytes(),before)
        self.assertEqual(result['lastReuseVerification']['candidateSha256'],b.inputs.sha(exe))
    def test_valid_terminal_payload_reuses_without_rebuilding_or_relabeling_source(self):self.assert_valid_reuse(False)
    def test_valid_associated_payload_reuses_with_original_build_identity(self):self.assert_valid_reuse(True)
    def test_reuse_corruption_is_immediate_persistent_attention_not_network_retry(self):
        key,exe=self.synthetic_completed_artifact();exe.write_bytes(b'Corrupt artifact, no execution')
        commit=self.change('README.md','Synthetic source requiring integrity review');path=self.builder.manager.state/'batches'/('e'*32);path.mkdir()
        b.save(path/'batch.json',{'status':'COMPLETE','candidateBuild':'QUEUED_FOR_SEPARATE_VERIFIED_BUILD_CONTROLLER','commit':commit})
        result=self.builder.discover();self.assertEqual(result[0]['status'],'ATTENTION');self.assertEqual(self.builder.discover()[0]['status'],'ATTENTION')
        self.assertEqual(len(list((self.private/'reuse-refusals').glob('*.json'))),1)
    def test_discovery_retry_requires_restored_verified_bytes_and_keeps_evidence(self):
        key,exe=self.synthetic_completed_artifact();original=exe.read_bytes();exe.write_bytes(b'Corrupt candidate, never executable')
        commit=self.change('README.md','Synthetic recovery request');identifier='d'*32;path=self.builder.manager.state/'batches'/identifier;path.mkdir()
        b.save(path/'batch.json',{'status':'COMPLETE','candidateBuild':'QUEUED_FOR_SEPARATE_VERIFIED_BUILD_CONTROLLER','commit':commit});self.builder.discover()
        state=self.private/'source-discovery.json';before=state.read_bytes()
        with self.assertRaises(b.ReuseRefused):self.builder.retry_discovery(identifier)
        self.assertEqual(state.read_bytes(),before)
        exe.write_bytes(original);result=self.builder.retry_discovery(identifier);self.assertEqual(result['status'],'DISCOVERY_RETRY_VERIFIED')
        self.assertEqual(json.loads(state.read_text())[identifier]['status'],'QUEUED');self.assertEqual(json.loads(Path(result['recoveryReceipt']).read_text())['status'],'APPLIED')
        self.assertTrue(list((self.private/'reuse-refusals').glob('*.json')));self.assertIn(identifier,self.builder.status()['sourceDiscovery'])
    def test_discovery_retry_cannot_approve_unknown_or_nonattention_batch(self):
        b.save(self.private/'source-discovery.json',{});pointer=self.builder.pointer.read_bytes()
        with self.assertRaises(b.Error):self.builder.retry_discovery('e'*32)
        with self.assertRaises(b.Error):self.builder.retry_discovery('../unsafe')
        self.assertEqual(self.builder.pointer.read_bytes(),pointer)
    def test_malformed_pointer_schema_cannot_reuse_cached_success(self):
        key,_=self.synthetic_completed_artifact();before=(self.builder.job(key)[0]/'job.json').read_bytes();b.save(self.builder.pointer,[])
        with self.assertRaises(b.ReuseRefused):self.builder.enqueue(self.change('README.md','Synthetic malformed pointer recovery'))
        self.assertEqual((self.builder.job(key)[0]/'job.json').read_bytes(),before)
    def test_oversized_discovery_journal_is_bounded_before_parsing(self):
        state=self.private/'source-discovery.json';state.write_bytes(b' '*(8*1024**2+1));before=b.inputs.sha(state)
        with self.assertRaisesRegex(b.Error,'oversized'):self.builder.retry_discovery('d'*32)
        self.assertEqual(b.inputs.sha(state),before)
    def test_changed_active_pointer_refuses_reuse_without_overwriting_human_state(self):
        key,_=self.synthetic_completed_artifact();before=self.builder.job(key)[0].joinpath('job.json').read_bytes();b.save(self.builder.pointer,{'human':'new candidate pointer'});pointer=self.builder.pointer.read_bytes()
        with self.assertRaisesRegex(b.Error,'reuse refused'):self.builder.enqueue(self.change('README.md','Synthetic pointer drift source'))
        self.assertEqual(self.builder.pointer.read_bytes(),pointer);self.assertEqual((self.builder.job(key)[0]/'job.json').read_bytes(),before)
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
        with patch.object(self.builder,'enqueue',side_effect=b.Error('Synthetic network interruption')) as enqueue:
            for i in range(8):
                with patch.object(b.time,'time',return_value=1000000+i*10000):result=self.builder.discover()
            self.assertEqual(result[0]['status'],'ATTENTION');self.assertEqual(self.builder.discover()[0]['status'],'ATTENTION');self.assertEqual(enqueue.call_count,8)
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
    def test_corrupt_job_is_preserved_and_does_not_starve_valid_sibling(self):
        bad=self.enqueue();badpath,_=self.builder.job(bad['buildKey']);raw=b'{"interrupted":';(badpath/'job.json').write_bytes(raw)
        good=self.builder.enqueue(self.change('app.py','VALUE=2\n'))
        with patch.object(self.builder,'execute',side_effect=lambda path,job:self.builder.record(path,'COMPLETE',fixtureOnly=True,actualBuildRun=False)):
            result=self.builder.once()
        self.assertEqual(result['buildKey'],good['buildKey']);self.assertEqual(result['queueAnomalyCount'],1);self.assertEqual((badpath/'job.json').read_bytes(),raw)
        self.assertEqual(self.builder.once()['status'],'ATTENTION');self.assertEqual(len(self.builder.status()['queueIssues']),1)
        receipts=list((self.private/'queue-anomalies').glob('*.json'));self.assertEqual(len(receipts),1);self.assertTrue(json.loads(receipts[0].read_text())['originalPreserved'])
    def test_orphan_directory_and_unknown_lifecycle_are_explicit_attention(self):
        (self.private/'jobs'/('f'*64)).mkdir();job=self.enqueue();path,_=self.builder.job(job['buildKey']);self.builder.record(path,'NOT_A_REAL_STATE')
        self.assertEqual(self.builder.once()['status'],'ATTENTION');self.assertEqual(len(self.builder.status()['queueIssues']),2);self.assertTrue((self.private/'jobs'/('f'*64)).is_dir())
    def test_malformed_source_batch_does_not_abort_discovery_siblings(self):
        batches=self.builder.manager.state/'batches';bad=batches/('c'*32);bad.mkdir();raw=b'{bad';(bad/'batch.json').write_bytes(raw)
        good=batches/('d'*32);good.mkdir();b.save(good/'batch.json',{'status':'COMPLETE','candidateBuild':'QUEUED_FOR_SEPARATE_VERIFIED_BUILD_CONTROLLER','commit':self.commit})
        result=self.builder.discover();self.assertTrue(any(row['status']=='ATTENTION' for row in result));self.assertTrue(any(row['status']=='QUEUED' for row in result));self.assertEqual((bad/'batch.json').read_bytes(),raw)
        self.assertTrue(any(row['status']=='ATTENTION' for row in self.builder.discover()))
    def test_low_disk_defers_without_spending_build_attempt(self):
        job=self.enqueue()
        with patch.object(b.shutil,'disk_usage',return_value=type('Disk',(),{'free':1024**3})()):result=self.builder.once()
        self.assertEqual(result['status'],'WAITING_RESOURCE');self.assertEqual(result['attempts'],0);self.assertEqual(self.builder.pointer.read_bytes(),self.original_pointer)
    def test_incomplete_job_identity_never_reaches_executor(self):
        job=self.enqueue();path,_=self.builder.job(job['buildKey']);self.builder.record(path,'QUEUED',graph=None)
        with patch.object(self.builder,'execute',side_effect=AssertionError('Incomplete work must not execute')):self.assertEqual(self.builder.once()['status'],'ATTENTION')
    def test_misconfigured_production_root_is_rejected_before_state_writes(self):
        production=self.f.source.parent/'Offline GPT';production.mkdir();sentinel=production/'preserve.txt';sentinel.write_text('Synthetic production sentinel')
        config=production/'profile.json';b.save(config,{**self.cfg,'privateRoot':str(production)})
        with self.assertRaisesRegex(b.Error,'production'):b.Builder(config,fixture=True)
        self.assertFalse((production/'jobs').exists());self.assertFalse((production/'current-candidate.json').exists());self.assertEqual(sentinel.read_text(),'Synthetic production sentinel')
    def test_failed_phase_retains_resource_telemetry(self):
        job=self.enqueue()
        class ResourceFixture:
            def __init__(self):self.samples=[]
            def preflight(self):self.check()
            def check(self):self.samples.append({'fixtureOnly':True,'count':len(self.samples)})
            def incidents(self):pass
        def material_fixture(receipt,expected,destination,monitor,frontend,allowed):
            (frontend.parent.parent/'ui').mkdir();(frontend.parent.parent/'ui/index.html').write_text('Synthetic UI, never launched')
            return {'python':self.f.cfg['python'],'node':'not-executed','site':str(destination/'site')}
        with patch.object(b,'Resources',ResourceFixture),patch.object(b.material,'materialize',side_effect=material_fixture),patch.object(b.process,'run',side_effect=b.process.ProcessError('Synthetic phase failure')):
            result=self.builder.once()
        self.assertEqual(result['status'],'FAILED');telemetry=json.loads((Path(result['attempt'])/'resources.json').read_text());self.assertTrue(all(row['fixtureOnly'] for row in telemetry));self.assertEqual(self.builder.pointer.read_bytes(),self.original_pointer)

class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.job={'sourceCommit':'a'*40,'graph':{'files':{'app.py':'a','tool.py':'b'},'applicationInputSha256':'b'*64}}
        self.smoke={'status':'PASSED','remainingOwnedPids':[],'modelRequestsIssued':0,'exeSha256':'c'*64,'checks':dict.fromkeys(b.SMOKE_CHECKS,True)}
        self.verification={'sourceCommit':'a'*40,'compiledModuleMatches':{'app.py':True,'tool.py':True},'candidateSha256':'c'*64,'applicationInputSha256':'b'*64}
    def test_complete_non_native_evidence_is_required(self):b.acceptance(self.smoke,self.verification,self.job,'c'*64)
    def test_overall_budget_cannot_be_extended_by_wall_clock_rewind(self):
        with patch.object(b.time,'monotonic',return_value=100):resources=b.Resources()
        with patch.object(b.time,'monotonic',return_value=1001),patch.object(b.time,'time',return_value=-10000),self.assertRaisesRegex(b.Error,'fifteen-minute'):resources.check()
    def test_empty_partial_false_or_native_receipts_never_pass(self):
        for changed in ({'checks':{}},{'checks':{'normal_exit':True}},{'modelRequestsIssued':1},{'remainingOwnedPids':[123]},{'exeSha256':'d'*64}):
            with self.subTest(changed=changed),self.assertRaises(b.Error):b.acceptance({**self.smoke,**changed},self.verification,self.job,'c'*64)
        for changed in ({'compiledModuleMatches':{}},{'compiledModuleMatches':{'app.py':True}},{'sourceCommit':'d'*40},{'candidateSha256':'d'*64}):
            with self.subTest(changed=changed),self.assertRaises(b.Error):b.acceptance(self.smoke,{**self.verification,**changed},self.job,'c'*64)
    @unittest.skipUnless(os.name=='nt','Actual Windows event monitor')
    def test_event_monitor_ignores_inherited_powershell_module_injection(self):
        with patch.dict(os.environ,{'PSModulePath':str(Path(b.__file__).parent/'nonexistent-modules')}):b.Resources().incidents()
class HistoricalEvidenceTests(unittest.TestCase):
    """Hash-sealed history must not bypass genuine-build receipt validation."""
    def setUp(self):
        import tempfile
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.builder=object.__new__(b.Builder);self.builder.root=Path(self.temp.name)
        self.builder.profileHash='a'*64;self.builder.fixture=False;self.builder.adapters={'old':'adapter'}
        self.key='b'*64;self.path=self.builder.root/'jobs'/self.key;self.path.mkdir(parents=True)
        self.value={'buildKey':self.key,'profileSha256':'c'*64,'status':'COMPLETE','attempts':1,'created':1,
          'sourceCommit':'d'*40,'graph':{'sourceCommit':'d'*40,'files':{'app.py':'e'*64}},
          'adapters':{'old':'adapter'},'acceptedSources':[{'commit':'d'*40}],
          'actualBuildRun':True,'fixtureOnly':False,'pointerAfter':{'buildKey':self.key},
          'phaseReceipts':{n:{'returnCode':0,'remainingOwnedProcesses':0} for n in ('FRONTEND','PACKAGING','VERIFYING','SMOKE')}}
    def seal(self):
        b.save(self.path/'job.json',self.value)
        self.builder.cfg={'historicalJobs':{self.key:{'sha256':b.inputs.sha(self.path/'job.json'),
          'profileSha256':self.value['profileSha256'],'status':self.value['status']}}}
    def test_valid_historical_completion_retains_original_identity(self):
        self.seal();raw=(self.path/'job.json').read_bytes();self.assertEqual(self.builder.job(self.key)[1],self.value)
        self.assertEqual((self.path/'job.json').read_bytes(),raw)
    def test_sealed_fixture_completion_is_not_a_genuine_build(self):
        self.value.update(actualBuildRun=False,fixtureOnly=True);self.seal()
        with self.assertRaises(b.Error):self.builder.job(self.key)
    def test_sealed_incomplete_phases_are_not_a_genuine_build(self):
        self.value['phaseReceipts'].pop('SMOKE');self.seal()
        with self.assertRaises(b.Error):self.builder.job(self.key)
    def test_phase_fixture_label_is_rejected_for_current_and_historical_jobs(self):
        for historical in (True,False):
            with self.subTest(historical=historical):
                self.value['profileSha256']='c'*64 if historical else self.builder.profileHash
                self.value['phaseReceipts']['SMOKE']['fixtureOnly']=True;self.seal()
                with self.assertRaises(b.Error):self.builder.job(self.key)
    def test_failed_or_malformed_historical_phase_never_passes(self):
        for phase in ({'returnCode':1,'remainingOwnedProcesses':0},{'returnCode':0,'remainingOwnedProcesses':1},
                      {'returnCode':False,'remainingOwnedProcesses':0},None):
            with self.subTest(phase=phase):
                self.value['phaseReceipts']['SMOKE']=phase;self.seal()
                with self.assertRaises(b.Error):self.builder.job(self.key)
    def test_mismatched_historical_pointer_is_rejected(self):
        self.value['pointerAfter']['buildKey']='f'*64;self.seal()
        with self.assertRaises(b.Error):self.builder.job(self.key)
    def test_disposable_fixture_history_remains_explicitly_allowed(self):
        self.builder.fixture=True;self.value.update(actualBuildRun=False,fixtureOnly=True);self.seal()
        self.assertTrue(self.builder.job(self.key)[1]['fixtureOnly'])
    def test_historical_hash_guard_still_detects_tampering(self):
        self.seal();self.value['attempts']=2;b.save(self.path/'job.json',self.value)
        with self.assertRaises(b.Error):self.builder.job(self.key)

if __name__=='__main__':unittest.main()
