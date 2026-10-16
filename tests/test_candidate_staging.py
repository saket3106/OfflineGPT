"""Real disposable Git and synthetic packaging only; no EXE/model is executed."""
import contextlib,copy,json,time,unittest,uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import test_candidate_builder as fixtures
b=fixtures.b

class StagingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Share the disposable Git foundation and its verified immutable adapter
        # identity. Constructor contracts have their own suite. Every staging
        # test gets independent profile/queue/artifacts and real Git source reads.
        # History is append-only, never force-pushed.
        cls.seed=fixtures.BuilderTests('runTest');cls.addClassCleanup(cls.seed.doCleanups);cls.seed.setUp()
        orig_inputs=b.inputs.inputs;cls.cached_graphs={}
        def cached_inputs(r,c,git):
            key=(str(r),str(c))
            if key not in cls.cached_graphs:cls.cached_graphs[key]=orig_inputs(r,c,git)
            return dict(cls.cached_graphs[key])
        b.inputs.inputs=cached_inputs;cls.addClassCleanup(setattr,b.inputs,'inputs',orig_inputs)
        orig_snapshot=b.Builder.source_snapshot;cls.cached_blobs={}
        def fast_snapshot(self,job,attempt,monitor=lambda:None):
            source=attempt/'source';source.mkdir()
            for name,h in job['graph']['files'].items():
                monitor()
                target=source/b.inputs.relative(name);target.parent.mkdir(parents=True,exist_ok=True)
                key=(job['sourceCommit'],name)
                if key not in cls.cached_blobs:cls.cached_blobs[key]=self.manager.git('show',job['sourceCommit']+':'+name)
                target.write_bytes(cls.cached_blobs[key])
            controller=attempt/'controller';controller.mkdir()
            for name in b.SCRIPTS:
                target=controller/name
                key=(self.cfg['controllerCommit'],'scripts/'+name)
                if key not in cls.cached_blobs:cls.cached_blobs[key]=self.manager.git('show',self.cfg['controllerCommit']+':scripts/'+name)
                target.write_bytes(cls.cached_blobs[key])
            return source,controller
        b.Builder.source_snapshot=fast_snapshot;cls.addClassCleanup(setattr,b.Builder,'source_snapshot',orig_snapshot)
    def setUp(self):
        seed=self.seed
        commit=seed.commit if (seed.f.repo/'app.py').read_text()=='VALUE=1\n' else seed.change('app.py','VALUE=1\n')
        private=seed.private.parent/('staging-'+uuid.uuid4().hex);private.mkdir()
        environment=json.loads(Path(seed.cfg['environmentReceipt']).read_text())
        environment['roots']={name:str(private/name) for name in environment['roots']}
        environment.pop('environmentSha256');environment['environmentSha256']=b.inputs.digest(environment)
        receipt=private/'environment.json';b.save(receipt,environment)
        baseline=private/'baseline';baseline.mkdir();(baseline/'Offline GPT.exe').write_bytes(b'Synthetic candidate, never executed')
        cfg={**seed.cfg,'privateRoot':str(private),'environmentReceipt':str(receipt),'environmentReceiptSha256':b.inputs.sha(receipt),'baselinePackage':str(baseline),'baselineExeSha256':b.inputs.sha(baseline/'Offline GPT.exe')}
        config=private/'config.json';b.save(config,cfg)
        builder=copy.copy(seed.builder);builder.config=config;builder.cfg=cfg;builder.profileHash=b.inputs.sha(config)
        builder.root=b.inputs.ordinary(private);builder.lock=private/'builder.lock';builder.pointer=private/'current-candidate.json'
        builder.manager=b.auto.Manager(cfg['syncConfig'],local_fixture=True);builder.adapters=dict(seed.builder.adapters)
        builder.environment=b.material.load(receipt,cfg['environmentReceiptSha256']);(private/'jobs').mkdir()
        b.save(builder.pointer,{'package':str(baseline),'exeSha256':cfg['baselineExeSha256'],'kind':'PREVIOUSLY_VERIFIED_BASELINE','buildKey':None})
        self.case=SimpleNamespace(f=seed.f,commit=commit,builder=builder,enqueue=lambda:builder.enqueue(commit),change=seed.change)
        self.builder=self.case.builder;self.job=self.case.enqueue();self.key=self.job['buildKey']
        self.path,_=self.builder.job(self.key);self.before=self.builder.pointer.read_bytes()
        b.save(self.builder.root/'control.json',{'mode':'pause'})
    @contextlib.contextmanager
    def pipeline(self,hook=lambda phase:None):
        owner=self
        class Resources:
            def __init__(self):self.samples=[]
            def preflight(self):self.check()
            def check(self):self.samples.append({'fixtureOnly':True})
            def incidents(self):pass
        def dependencies(receipt,expected,destination,monitor,frontend,allowed):
            monitor();return {'python':owner.case.f.cfg['python'],'node':'never-executed','site':str(destination/'site')}
        def run(command,source,env,log,budget,monitor):
            name=log.stem.upper();monitor();hook(name)
            if name=='FRONTEND':
                (source/'ui').mkdir();(source/'ui/index.html').write_text('Synthetic UI, not rendered')
            elif name=='PACKAGING':
                package=source/'dist/Offline GPT';package.mkdir(parents=True)
                exe=package/'Offline GPT.exe';exe.write_bytes(b'Synthetic packaging fixture; never launched')
                (source/'build/Offline GPT').mkdir(parents=True)
                (source/'build/Offline GPT/COLLECT-00.toc').write_text(repr(([('Offline GPT.exe',str(exe),'EXECUTABLE')],)))
            elif name=='VERIFYING':
                exe=source/'dist/Offline GPT/Offline GPT.exe'
                b.save(Path(command[-1]),{**owner.job['graph'],'candidateSha256':b.inputs.sha(exe),'compiledModuleMatches':{'app.py':True},'fixtureOnly':True})
            elif name=='SMOKE':
                profile=Path(command[-2]);profile.mkdir()
                b.save(profile/'shell-smoke.json',{'status':'PASSED','remainingOwnedPids':[],'modelRequestsIssued':0,'exeSha256':command[-1],'checks':dict.fromkeys(b.SMOKE_CHECKS,True),'fixtureOnly':True})
            return {'returnCode':0,'remainingOwnedProcesses':0,'fixtureOnly':True}
        with patch.object(b,'Resources',Resources),patch.object(b.material,'materialize',side_effect=dependencies),patch.object(b.process,'run',side_effect=run):yield
    def stage(self):
        with self.pipeline():return self.builder.stage(self.key)
    def interrupted(self):
        real=self.builder.record
        def stop(path,status,**fields):
            if status=='COMPLETE':raise b.Error('Synthetic interruption after STAGING')
            return real(path,status,**fields)
        with self.pipeline(),patch.object(self.builder,'record',side_effect=stop):result=self.builder.stage(self.key)
        self.assertEqual(result['status'],'RECOVERY_REQUIRED');return result
    def test_complete_staged_pipeline_preserves_selected_candidate_and_labels_fixture(self):
        result=self.stage();self.assertEqual(result['status'],'COMPLETE');self.assertTrue(result['promotionDeferred'])
        self.assertFalse(result['actualBuildRun']);self.assertTrue(result['fixtureOnly'])
        self.assertEqual(set(result['phaseReceipts']),{'FRONTEND','PACKAGING','VERIFYING','SMOKE'})
        self.assertEqual(result['attempts'],1);self.assertEqual(self.builder.pointer.read_bytes(),self.before)
        b.retained(result['pointerAfter'],result);self.assertEqual(self.builder.once()['status'],'PAUSED')
    def test_default_pipeline_still_promotes_after_acceptance(self):
        b.save(self.builder.root/'control.json',{'mode':'running'})
        with self.pipeline():result=self.builder.once()
        self.assertEqual(result['status'],'COMPLETE');self.assertFalse(result['promotionDeferred'])
        self.assertEqual(json.loads(self.builder.pointer.read_text()),result['pointerAfter'])
    def test_staging_is_rejected_while_running_without_spending_attempt(self):
        b.save(self.builder.root/'control.json',{'mode':'running'})
        with self.assertRaisesRegex(b.Error,'paused'):self.builder.stage(self.key)
        self.assertEqual(self.builder.job(self.key)[1]['attempts'],0)
        self.assertEqual(self.builder.pointer.read_bytes(),self.before)
    def test_staged_result_cannot_be_reused_as_the_selected_candidate(self):
        self.stage();before=(self.path/'job.json').read_bytes()
        with self.assertRaises(b.ReuseRefused):self.builder.enqueue(self.case.commit)
        self.assertEqual((self.path/'job.json').read_bytes(),before);self.assertEqual(self.builder.pointer.read_bytes(),self.before)
    def test_staged_failure_preserves_original_and_required_siblings(self):
        sibling=self.builder.enqueue(self.case.change('app.py','VALUE=2\n'))
        def fail(phase):
            if phase=='PACKAGING':raise b.process.ProcessError('Synthetic packaging failure')
        with self.pipeline(fail):result=self.builder.stage(self.key)
        self.assertEqual(result['status'],'FAILED');self.assertEqual(result['attempts'],1)
        self.assertEqual(self.builder.job(sibling['buildKey'])[1]['status'],'QUEUED')
        self.assertEqual(self.builder.pointer.read_bytes(),self.before)
    def test_resource_deferral_does_not_spend_attempt_or_promote(self):
        with patch.object(b.Resources,'preflight',side_effect=b.Deferred('Synthetic memory preflight')):result=self.builder.stage(self.key)
        self.assertEqual(result['status'],'WAITING_RESOURCE');self.assertEqual(result['attempts'],0)
        with self.assertRaises(b.Error):self.builder.stage(self.key)
        self.assertEqual(self.builder.pointer.read_bytes(),self.before)
    def test_failed_ci_never_starts_staged_materialization(self):
        with patch.object(self.builder.manager,'ci',return_value={'status':'completed','conclusion':'failure'}),patch.object(b.material,'materialize',side_effect=AssertionError('Must not materialize')):result=self.builder.stage(self.key)
        self.assertEqual(result['status'],'FAILED');self.assertEqual(result['attempts'],0)
        self.assertEqual(self.builder.pointer.read_bytes(),self.before)
    def test_network_backoff_is_bounded_for_staging(self):
        with patch.object(self.builder,'accepted',side_effect=b.auto.NetworkError('Synthetic offline')):
            for i in range(8):
                with patch.object(b.time,'time',return_value=time.time()+100000+i*10000):result=self.builder.stage(self.key)
        self.assertEqual(result['status'],'BLOCKED');self.assertEqual(result['networkRetries'],8);self.assertEqual(result['attempts'],0)
    def test_cancellation_during_smoke_is_checked_before_staging(self):
        def cancel(phase):
            if phase=='SMOKE':b.save(self.path/'cancel.json',{'fixtureOnly':True})
        with self.pipeline(cancel):result=self.builder.stage(self.key)
        self.assertEqual(result['status'],'CANCELLED');self.assertEqual(self.builder.pointer.read_bytes(),self.before)
    def test_human_pointer_change_during_build_is_not_overwritten(self):
        changed={'human':'new selection'}
        def change(phase):
            if phase=='SMOKE':b.save(self.builder.pointer,changed)
        with self.pipeline(change):result=self.builder.stage(self.key)
        self.assertEqual(result['status'],'FAILED');self.assertEqual(json.loads(self.builder.pointer.read_text()),changed)
    def test_queue_resume_during_build_blocks_staging(self):
        def change(phase):
            if phase=='SMOKE':b.save(self.builder.root/'control.json',{'mode':'running'})
        with self.pipeline(change):result=self.builder.stage(self.key)
        self.assertEqual(result['status'],'FAILED');self.assertEqual(self.builder.pointer.read_bytes(),self.before)
    def test_interrupted_staging_recovers_evidence_without_selecting_artifact(self):
        self.interrupted();result=self.builder.recover(self.key)
        self.assertEqual(result['status'],'COMPLETE');self.assertTrue(result['promotionDeferred'])
        self.assertFalse(result['actualBuildRun']);self.assertEqual(self.builder.pointer.read_bytes(),self.before)
    def test_interrupted_staging_rejects_corrupt_artifact(self):
        result=self.interrupted();(Path(result['pointerAfter']['package'])/'Offline GPT.exe').write_bytes(b'Changed fixture')
        with self.assertRaises(b.Error):self.builder.recover(self.key)
        self.assertEqual(self.builder.job(self.key)[1]['status'],'RECOVERY_REQUIRED');self.assertEqual(self.builder.pointer.read_bytes(),self.before)
    def test_interrupted_staging_rejects_missing_owned_process_receipt(self):
        result=self.interrupted();phases=result['phaseReceipts'];phases.pop('SMOKE')
        self.builder.record(self.path,'RECOVERY_REQUIRED',phaseReceipts=phases)
        with self.assertRaisesRegex(b.Error,'owned-process'):self.builder.recover(self.key)
        self.assertEqual(self.builder.pointer.read_bytes(),self.before)
    def test_interrupted_staging_rejects_unknown_pointer_and_resumed_queue(self):
        self.interrupted();b.save(self.builder.pointer,{'unknown':'human'})
        with self.assertRaises(b.Error):self.builder.recover(self.key)
        self.assertEqual(json.loads(self.builder.pointer.read_text()),{'unknown':'human'})
        self.builder.pointer.write_bytes(self.before);b.save(self.builder.root/'control.json',{'mode':'running'})
        with self.assertRaises(b.Error):self.builder.recover(self.key)
    def test_completed_cancelled_and_exhausted_jobs_cannot_stage(self):
        for state,attempts in [('COMPLETE',1),('CANCELLED',0),('FAILED',2),('QUEUED',2)]:
            with self.subTest(state=state,attempts=attempts):
                self.builder.record(self.path,state,attempts=attempts)
                with self.assertRaises(b.Error):self.builder.stage(self.key)
        self.assertEqual(self.builder.pointer.read_bytes(),self.before)
    def test_fixture_receipts_cannot_be_used_as_genuine_acceptance(self):
        self.builder.fixture=False
        for smoke,verified,phases in [({'fixtureOnly':True},{},{}),({}, {'fixtureOnly':True},{}),({},{},{'SMOKE':{'fixtureOnly':True}})]:
            with self.subTest(smoke=smoke,verified=verified,phases=phases),self.assertRaisesRegex(b.Error,'Synthetic evidence'):self.builder.reject_fixture_evidence(smoke,verified,phases)
    def test_staging_lock_does_not_block_independent_source_publication(self):
        with b.auto.exclusive(self.builder.lock):
            with self.assertRaises(b.auto.Error):self.builder.stage(self.key)
            self.assertEqual(self.builder.manager.once()['status'],'IDLE')

if __name__=='__main__':unittest.main()
