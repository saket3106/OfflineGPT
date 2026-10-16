"""Disposable real Git repositories and bare remotes; no GitHub/model access."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
import test_source_sync as fixtures

spec=importlib.util.spec_from_file_location('manager_under_test',Path(__file__).resolve().parents[1]/'scripts/auto_sync.py')
auto=importlib.util.module_from_spec(spec);spec.loader.exec_module(auto)
sync=auto.sync
REVIEWS={'complete':True,'content':True,'dependencies':True,'sourceLicenses':True}

class AutoSyncTests(unittest.TestCase):
    def setUp(self):
        fixture=fixtures.SourceSyncTests('runTest');fixture.setUp();self.addCleanup(fixture.doCleanups)
        self.f=fixture;self.repo=fixture.repo;self.source=fixture.source
        self.private=fixture.root/'private';self.private.mkdir();self.config=self.private/'config.json'
        self.remote=fixture.root/'remote.git'
        self.g('config','user.name','Synthetic Test');self.g('config','user.email','test@example.invalid')
        self.g('switch','-c','codex/test')
        subprocess.run([fixture.git,'init','--bare','--quiet',str(self.remote)],check=True,capture_output=True)
        self.g('remote','add','origin',str(self.remote))
        with (self.repo/'.gitignore').open('a') as stream:stream.write('/.offlinegpt-autosync-mutex\n')
        (self.repo/'tests').mkdir();(self.repo/'tests/test_fixture.py').write_text('import unittest\nclass Fixture(unittest.TestCase):\n def test_ok(self): self.assertEqual(2+2,4)\n')
        (self.repo/sync.MANIFEST).write_bytes(sync.manifest_data(self.repo,fixture.git))
        self.g('add','--all');self.g('commit','-qm','Synthetic starting point');self.g('push','-q','origin','HEAD:refs/heads/codex/test')
        self.cfg={'version':1,'source':str(self.source.resolve()),'repository':str(self.repo.resolve()),'stateRoot':str(self.private.resolve()),
                  'python':sys.executable,'git':fixture.git,'branch':'codex/test','remoteUrl':str(self.remote),'debounceSeconds':0,'testProfile':'unit'}
        self.config.write_text(json.dumps(self.cfg));self.manager=auto.Manager(self.config,local_fixture=True)
    def g(self,*args):
        return subprocess.run([self.f.git,'-C',str(self.repo),*args],check=True,capture_output=True,timeout=15).stdout
    def prepare(self):
        (self.source/'app.py').write_text('VALUE=2\n');return self.manager.prepare('Verified synthetic maintenance batch')
    def accept(self,batch=None):
        batch=batch or self.prepare();return self.manager.accept(batch['id'],batch['snapshotDigest'],REVIEWS)
    def batch_path(self,batch):return self.private/'batches'/batch['id']
    def test_snapshot_tests_and_complete_real_local_publication(self):
        original=(self.repo/'README.md').read_bytes();batch=self.prepare();self.assertEqual(batch['status'],'AWAITING_REVIEW')
        self.assertEqual((self.repo/'app.py').read_text(),'VALUE=1\n');self.accept(batch)
        result=self.manager.once();self.assertEqual(result['status'],'COMPLETE');self.assertTrue(result['ci']['fixtureOnly'])
        self.assertEqual(self.manager.remote_head(),result['commit']);self.assertEqual((self.repo/'README.md').read_bytes(),original)
        self.assertFalse(self.g('status','--porcelain').strip());self.assertEqual(result['binaryPublication'],'BLOCKED_NATIVE_PROVENANCE_AND_LICENSES')
        count=self.g('rev-list','--count','HEAD');self.assertEqual(self.manager.once()['status'],'IDLE');self.assertEqual(count,self.g('rev-list','--count','HEAD'))
    def test_stability_is_not_completion(self):
        batch=self.prepare();self.assertEqual(self.manager.once()['status'],'IDLE')
        with self.assertRaises(auto.Error):self.manager.accept(batch['id'],batch['snapshotDigest'],{**REVIEWS,'complete':False})
        self.assertEqual(self.g('rev-list','--count','HEAD').strip(),b'1')
    def test_complete_unit_suite_uses_measured_bounded_budget_without_filtering(self):
        real=subprocess.run;calls=[]
        def capture(command,**kwargs):
            if '-m' in command and 'unittest' in command:calls.append((list(command),kwargs['timeout']))
            return real(command,**kwargs)
        with patch.object(auto.subprocess,'run',side_effect=capture):batch=self.prepare()
        self.assertEqual(batch['status'],'AWAITING_REVIEW');self.assertEqual(calls[0][1],600);self.assertNotIn('-p',calls[0][0]);self.assertEqual(batch['tests']['budgetSeconds'],600)
    def test_unit_timeout_retains_failure_and_requires_owned_process_review(self):
        real=subprocess.run
        def timeout(command,**kwargs):
            if '-m' in command and 'unittest' in command:raise subprocess.TimeoutExpired(command,kwargs['timeout'])
            return real(command,**kwargs)
        with patch.object(auto.subprocess,'run',side_effect=timeout):batch=self.prepare()
        self.assertEqual(batch['status'],'TEST_FAILED');self.assertEqual(batch['testBudgetSeconds'],600);self.assertTrue(batch['requiresProcessReview'])
        with self.assertRaises(auto.Error):self.accept(batch)
    def test_exact_snapshot_approval_and_live_source_drift(self):
        batch=self.prepare()
        with self.assertRaises(auto.Error):self.manager.accept(batch['id'],'0'*64,REVIEWS)
        (self.source/'app.py').write_text('VALUE=3\n')
        with self.assertRaises(auto.Error):self.accept(batch)
    def test_snapshot_tampering_is_blocked(self):
        batch=self.accept();(self.batch_path(batch)/'source/app.py').write_text('VALUE=99\n')
        self.assertEqual(self.manager.once()['status'],'BLOCKED');self.assertEqual((self.repo/'app.py').read_text(),'VALUE=1\n')
    def test_unknown_files_and_sensitive_content_block(self):
        (self.source/'unreviewed.py').write_text('VALUE=8\n')
        with self.assertRaises(auto.Error):self.prepare()
        (self.source/'unreviewed.py').unlink();(self.source/'app.py').write_text('TOKEN="'+'ghp_'+'a'*36+'"\n')
        result=self.manager.prepare('Unsafe synthetic example blocked');self.assertEqual(result['status'],'BLOCKED')
    def test_repository_dirty_and_staged_changes_preserved(self):
        (self.repo/'README.md').write_text('New human edit');self.g('add','README.md')
        before=self.g('diff','--cached')
        with self.assertRaises(auto.Error):self.prepare()
        self.assertEqual(self.g('diff','--cached'),before)
    def test_test_failure_never_reaches_acceptance(self):
        (self.repo/'tests/test_fixture.py').write_text('import unittest\nclass Fixture(unittest.TestCase):\n def test_fail(self): self.fail("independent failure")\n')
        self.g('add','tests/test_fixture.py');self.g('commit','-qm','Introduce failing synthetic test')
        result=self.prepare();self.assertEqual(result['status'],'TEST_FAILED')
        with self.assertRaises(auto.Error):self.accept(result)
    def test_tests_cannot_mutate_their_tested_source(self):
        (self.repo/'tests/test_fixture.py').write_text('import unittest\nfrom pathlib import Path\nclass Fixture(unittest.TestCase):\n def test_mutate(self): Path("app.py").write_text("VALUE=88")\n')
        self.g('add','tests/test_fixture.py');self.g('commit','-qm','Synthetic source mutation test')
        self.assertEqual(self.prepare()['status'],'STALE')
    def test_offline_before_copy_retains_batch_and_retries_without_duplicate_commit(self):
        batch=self.accept()
        with patch.object(self.manager,'remote_head',side_effect=auto.NetworkError('Synthetic network unavailable')):
            result=self.manager.once()
        self.assertEqual(result['status'],'OFFLINE_RETRY');self.assertEqual(result['resumeStatus'],'ACCEPTED')
        self.assertEqual((self.repo/'app.py').read_text(),'VALUE=1\n')
        self.manager.record(self.batch_path(batch),'OFFLINE_RETRY',retryAfter=0)
        self.assertEqual(self.manager.once()['status'],'COMPLETE');self.assertEqual(self.g('rev-list','--count','HEAD').strip(),b'2')
    def test_lost_push_acknowledgement_reconciles_actual_remote_commit(self):
        batch=self.accept();original=self.manager.git
        def lost(*args,**kwargs):
            result=original(*args,**kwargs)
            if args[0]=='push':raise auto.NetworkError('Synthetic lost acknowledgement')
            return result
        with patch.object(self.manager,'git',side_effect=lost):result=self.manager.once()
        self.assertEqual(result['status'],'OFFLINE_RETRY');commit=self.manager.head()
        self.manager.record(self.batch_path(batch),'OFFLINE_RETRY',retryAfter=0)
        self.assertEqual(self.manager.once()['commit'],commit);self.assertEqual(self.g('rev-list','--count','HEAD').strip(),b'2')
    def test_pending_and_failed_ci_are_not_success(self):
        self.accept()
        with patch.object(self.manager,'ci',return_value={'status':'in_progress'}):self.assertEqual(self.manager.once()['status'],'CI_PENDING')
        with patch.object(self.manager,'ci',return_value={'status':'completed','conclusion':'failure'}):self.assertEqual(self.manager.once()['status'],'CI_FAILED')
        self.assertEqual(self.g('rev-list','--count','HEAD').strip(),b'2')
    def test_interrupted_phases_require_evidence_and_do_not_reapply(self):
        batch=self.accept();path=self.batch_path(batch)
        for phase in ('APPLYING','STAGING','COMMITTING'):
            self.manager.record(path,phase);self.assertEqual(self.manager.once()['status'],'RECOVERY_REQUIRED')
        self.assertEqual((self.repo/'app.py').read_text(),'VALUE=1\n')
    def test_ci_clock_survives_polls_and_expired_batch_releases_sibling(self):
        first=self.accept()
        with patch.object(self.manager,'ci',return_value={'status':'queued'}):
            waiting=self.manager.once();started=waiting['ciWaitStarted']
            self.assertEqual(self.manager.once()['ciWaitStarted'],started)
        count=self.g('rev-list','--count','HEAD')
        # Prepare a second, independently reviewed batch against the now clean
        # committed tree, while the first one waits for CI.
        (self.source/'app.py').write_text('VALUE=3\n')
        second=self.manager.prepare('Second synthetic maintenance batch');self.accept(second)
        with patch.object(auto.time,'time',return_value=started+auto.CI_WAIT_SECONDS),patch.object(self.manager,'ci') as ci:
            result=self.manager.once()
        ci.assert_not_called();self.assertEqual(result['status'],'CI_TIMED_OUT')
        self.assertEqual(self.g('rev-list','--count','HEAD'),count)
        result=self.manager.once();self.assertEqual(result['status'],'COMPLETE');self.assertEqual(result['id'],second['id'])
        self.assertEqual(sync.load(self.batch_path(first)/'batch.json')['status'],'CI_TIMED_OUT')
    def test_ci_expiry_applies_during_network_backoff(self):
        batch=self.accept()
        with patch.object(self.manager,'ci',return_value={'status':'pending'}):waiting=self.manager.once()
        start=waiting['ciWaitStarted']
        self.manager.record(self.batch_path(batch),'OFFLINE_RETRY',resumeStatus='CI_PENDING',retryAfter=start+10000)
        with patch.object(auto.time,'time',return_value=start+auto.CI_WAIT_SECONDS):result=self.manager.once()
        self.assertEqual(result['status'],'CI_TIMED_OUT')
    def test_legacy_ci_clock_migrates_once_and_corrupt_clock_requires_review(self):
        batch=self.accept()
        with patch.object(self.manager,'ci',return_value={'status':'pending'}):waiting=self.manager.once()
        path=self.batch_path(batch);waiting.pop('ciWaitStarted');self.manager.write(path/'batch.json',waiting)
        with patch.object(self.manager,'ci',return_value={'status':'queued'}):migrated=self.manager.once()
        self.assertEqual(migrated['ciWaitStarted'],waiting['remoteVerified'])
        for invalid in (None,'yesterday',float('nan'),float('inf'),auto.time.time()+100):
            self.manager.record(path,'CI_PENDING',ciWaitStarted=invalid)
            self.assertEqual(self.manager.once()['status'],'RECOVERY_REQUIRED')
    def test_ci_success_after_deadline_is_not_accepted(self):
        batch=self.accept()
        with patch.object(self.manager,'ci',return_value={'status':'pending'}):waiting=self.manager.once()
        clock=[waiting['ciWaitStarted']+auto.CI_WAIT_SECONDS-1]
        def delayed(_):
            # CI observation can finish after the deadline, even if its result
            # is successful. Runtime expiry still owns the decision.
            clock[0]+=2
            return {'status':'completed','conclusion':'success'}
        with patch.object(self.manager,'ci',side_effect=delayed),patch.object(auto.time,'time',side_effect=lambda:clock[0]):
            self.assertEqual(self.manager.once()['status'],'CI_TIMED_OUT')
    def test_os_exclusive_lock_and_pause(self):
        with auto.exclusive(self.manager.lock):
            with self.assertRaises(auto.Error):self.manager.once()
        self.manager.write(self.private/'control.json',{'mode':'paused'});self.assertEqual(self.manager.once()['status'],'PAUSED')
    def test_unapproved_commands_branch_and_state_locations_rejected(self):
        for change in ({'testProfile':'powershell'},{'branch':'main'},{'stateRoot':str(self.repo/'private')},{'source':'relative'},{'arbitraryCommand':'execute'}):
            self.config.write_text(json.dumps({**self.cfg,**change}))
            with self.assertRaises(auto.Error):auto.Manager(self.config,local_fixture=True)
    def test_concurrent_remote_work_never_force_pushes(self):
        self.accept();before=self.manager.head()
        with patch.object(self.manager,'remote_head',return_value='f'*40):self.assertEqual(self.manager.once()['status'],'BLOCKED')
        self.assertEqual(self.manager.head(),before);self.assertEqual((self.repo/'app.py').read_text(),'VALUE=1\n')

    def test_nonrequired_source_deletion_keeps_recovery_backup(self):
        (self.source/'helper.py').unlink()
        batch=self.manager.prepare('Reviewed retired synthetic helper');self.accept(batch)
        result=self.manager.once();self.assertEqual(result['status'],'COMPLETE')
        self.assertFalse((self.repo/'helper.py').exists())
        journal=sync.load(Path(result['sync']['journal']));entry=next(e for e in journal['entries'] if e['file']=='helper.py')
        self.assertEqual((Path(result['sync']['journal']).parent/entry['backup']).read_text(),'HELPER=1\n')

    def test_separate_private_configs_share_one_repository_mutex(self):
        private=self.f.root/'other-private';private.mkdir();config=private/'config.json'
        config.write_text(json.dumps({**self.cfg,'stateRoot':str(private.resolve())}))
        other=auto.Manager(config,local_fixture=True)
        self.assertEqual(other.lock,self.manager.lock)
        with auto.exclusive(self.manager.lock):
            with self.assertRaises(auto.Error):other.once()

    def test_concurrent_staged_content_is_preserved_and_never_committed(self):
        batch=self.accept();original=self.manager.git
        def stage_other(*args,**kwargs):
            result=original(*args,**kwargs)
            if args[0]=='add':
                (self.repo/'README.md').write_text('Concurrent human edit')
                self.g('add','README.md')
            return result
        with patch.object(self.manager,'git',side_effect=stage_other):result=self.manager.once()
        self.assertEqual(result['status'],'RECOVERY_REQUIRED')
        self.assertEqual((self.repo/'README.md').read_text(),'Concurrent human edit')
        self.assertEqual(self.g('rev-list','--count','HEAD').strip(),b'1')

    def test_retry_budget_is_finite(self):
        batch=self.accept();path=self.batch_path(batch)
        self.manager.record(path,'OFFLINE_RETRY',resumeStatus='ACCEPTED',retryAfter=0,retries=8)
        with patch.object(self.manager,'remote_head',side_effect=auto.NetworkError('Synthetic offline')):result=self.manager.once()
        self.assertEqual(result['status'],'BLOCKED');self.assertEqual(result['retries'],9)
        self.assertEqual((self.repo/'app.py').read_text(),'VALUE=1\n')

    def test_incidental_fetch_metadata_does_not_discard_verified_source(self):
        original=subprocess.run
        def update_metadata(*args,**kwargs):
            result=original(*args,**kwargs)
            if '-m' in args[0] and 'unittest' in args[0]:(self.repo/'.git/FETCH_HEAD').write_text('Synthetic fetch bookkeeping\n')
            return result
        with patch.object(subprocess,'run',side_effect=update_metadata):batch=self.prepare()
        self.assertEqual(batch['status'],'AWAITING_REVIEW')
        self.accept(batch);(self.repo/'.git/FETCH_HEAD').write_text('Later synthetic fetch bookkeeping\n')
        result=self.manager.once();self.assertEqual(result['status'],'COMPLETE');self.assertTrue(result['incidentalGitMetadataRebound'])

    def test_core_git_config_change_invalidates_accepted_review(self):
        self.accept();self.g('config','offlinegpt.synthetic','changed')
        self.assertEqual(self.manager.once()['status'],'BLOCKED');self.assertEqual((self.repo/'app.py').read_text(),'VALUE=1\n')

    def test_private_repository_policy_is_fail_closed(self):
        self.manager.local_fixture=False;self.manager.cfg['remoteUrl']='https://github.com/example/fixture.git'
        good={'full_name':'example/fixture','private':True,'archived':False,'permissions':{'push':True}}
        with patch.object(self.manager,'github_api',return_value=good):self.assertTrue(self.manager.remote_policy()['privateVerified'])
        for change in ({'private':False},{'private':None},{'archived':True},{'full_name':'other/repository'},{'permissions':{'push':False}}):
            with patch.object(self.manager,'github_api',return_value={**good,**change}),self.assertRaises(auto.Error):self.manager.remote_policy()

    def test_repository_policy_failure_precedes_copy_and_commit(self):
        self.accept()
        with patch.object(self.manager,'remote_policy',side_effect=auto.Error('Synthetic repository no longer private')):result=self.manager.once()
        self.assertEqual(result['status'],'BLOCKED');self.assertEqual((self.repo/'app.py').read_text(),'VALUE=1\n')
        self.assertEqual(self.g('rev-list','--count','HEAD').strip(),b'1')

if __name__=='__main__':unittest.main()
