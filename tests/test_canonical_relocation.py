"""Real disposable Git relocation/recovery; acceptance adapter is explicitly synthetic."""
from contextlib import contextmanager
import importlib.util,json,os
from pathlib import Path
import shutil,subprocess,sys,tempfile,unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('relocation_under_test',ROOT/'scripts/canonical_relocation.py');move=importlib.util.module_from_spec(spec);spec.loader.exec_module(move)
spec=importlib.util.spec_from_file_location('canonical_autosync',ROOT/'scripts/auto_sync.py');auto=importlib.util.module_from_spec(spec);spec.loader.exec_module(auto)

class Adapter:
    def __init__(self,root,repository=None,destination=None):self.repository=Path(repository).resolve() if repository is not None else None;self.destination=Path(destination).resolve() if destination is not None else None;self.root=Path(root).resolve();self.disabled=True;self.accepted=True;self.trip=None;self.verified=[]
    def existing_locks(self):
        return [auto.exclusive(self.root/'canonical-layout.lock'),auto.exclusive(self.root/'discovery.lock'),auto.exclusive(self.root/'builder.lock')]
    def verify(self,bindings,old,new,*,phase):
        if not self.disabled or not self.accepted or bindings!={'fixtureOnly':True,'reviewedEpoch':1}:raise move.RelocationError('Synthetic acceptance or disabled-task gate failed')
        repository=old if old.is_dir() else new
        if not repository.is_dir():raise move.RelocationError('Ambiguous repository locations')
        with auto.exclusive(repository/'.offlinegpt-autosync-mutex',create_parent=False):pass
        self.verified.append(phase)
    def verify_consumer(self,path,data):
        if path.parent!=self.root or path.suffix!='.json':raise move.RelocationError('Unreviewed consumer')
        json.loads(data)
    def checkpoint(self,name):
        if self.trip==name:raise InterruptedError('Synthetic interruption '+name)
    def verify_after(self,plan,new):
        if not self.disabled:raise move.RelocationError('Unexpected task activation')
        for item in plan['consumers']:
            if json.loads(Path(item['path']).read_text(encoding='utf-8'))['repository']!=str(new):raise move.RelocationError('Wrong new consumer root')
    def verify_rollback(self,plan,old):
        for item in plan['consumers']:
            if json.loads(Path(item['path']).read_text(encoding='utf-8'))['repository']!=str(old):raise move.RelocationError('Wrong rollback root')

class RelocationTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name).resolve();self.repo=self.root/'repository';self.repo.mkdir();self.dest=self.root/'OfflineGPT';self.git=shutil.which('git')
        self.g('init','-q');self.g('config','user.name','Synthetic relocation');self.g('config','user.email','fixture@example.invalid');self.g('config','core.autocrlf','false')
        (self.repo/'app.py').write_text('VALUE=1\n',encoding='utf-8');(self.repo/'.gitignore').write_text('/.offlinegpt-autosync-mutex\n',encoding='utf-8');self.g('add','app.py','.gitignore');self.g('commit','-qm','Synthetic origin')
        (self.repo/'app.py').write_text('VALUE=2\n',encoding='utf-8');self.g('add','app.py');(self.repo/'app.py').write_text('VALUE=3\n',encoding='utf-8')
        self.adapter=Adapter(self.root,self.repo,self.dest);self.controller=move.Relocation(self.root/'journal',self.git,self.adapter);self.bindings={'fixtureOnly':True,'reviewedEpoch':1}
        self.consumers=[]
        for name in ['source-profile.json','candidate-profile.json']:
            p=self.root/name;p.write_text(json.dumps({'repository':str(self.repo)}),encoding='utf-8');self.consumers.append((p,move.canonical({'repository':str(self.dest)})))
        with auto.exclusive(self.repo/'.offlinegpt-autosync-mutex',create_parent=False):pass
        self.before=move.snapshot(self.repo);self.identity=move.git_state(self.repo,self.git)
    def g(self,*args):
        p=subprocess.run([self.git,'-C',str(self.repo),*args],capture_output=True,timeout=10);self.assertEqual(p.returncode,0,p.stderr);return p.stdout
    def prepare(self):return self.controller.prepare(self.repo,self.dest,self.consumers,self.bindings)
    def test_real_rename_preserves_partial_index_history_and_supports_complete_rollback(self):
        journal=self.prepare();result=self.controller.apply(journal)
        self.assertEqual(result['status'],'COMPLETE');self.assertFalse(self.repo.exists());self.assertEqual(move.snapshot(self.dest),self.before);self.assertEqual(move.git_state(self.dest,self.git),self.identity)
        result=self.controller.rollback(journal);self.assertEqual(result['status'],'ROLLED_BACK');self.assertEqual(move.snapshot(self.repo),self.before);self.assertFalse(self.dest.exists())
    def test_interruption_after_rename_requires_explicit_recovery_and_evidence(self):
        journal=self.prepare();self.adapter.trip='after_rename'
        with self.assertRaises(InterruptedError):self.controller.apply(journal)
        with self.assertRaisesRegex(move.RelocationError,'Explicit'):self.controller.apply(journal)
        self.adapter.trip=None;self.assertEqual(self.controller.apply(journal,recover=True)['status'],'COMPLETE')
    def test_interruption_between_consumers_resumes_remaining_transition(self):
        journal=self.prepare();self.adapter.trip='after_consumer_0'
        with self.assertRaises(InterruptedError):self.controller.apply(journal)
        self.assertNotEqual(Path(self.consumers[0][0]).read_bytes(),Path(self.consumers[1][0]).read_bytes())
        self.adapter.trip=None;self.assertEqual(self.controller.apply(journal,recover=True)['status'],'COMPLETE')
    def test_rollback_interruption_is_resumable(self):
        journal=self.prepare();self.controller.apply(journal);self.adapter.trip='rollback_consumer_0'
        with self.assertRaises(InterruptedError):self.controller.rollback(journal)
        self.adapter.trip=None;self.assertEqual(self.controller.rollback(journal)['status'],'ROLLED_BACK');self.assertEqual(move.snapshot(self.repo),self.before)
    def test_external_consumer_change_never_gets_overwritten(self):
        journal=self.prepare();p=self.consumers[0][0];p.write_text('{"human":"work"}',encoding='utf-8')
        with self.assertRaisesRegex(move.RelocationError,'outside'):self.controller.apply(journal)
        self.assertTrue(self.repo.exists());self.assertIn('human',p.read_text(encoding='utf-8'))
    def test_repository_or_staged_index_drift_is_not_overwritten(self):
        journal=self.prepare();self.g('add','app.py')
        with self.assertRaisesRegex(move.RelocationError,'changed'):self.controller.apply(journal)
        self.assertTrue(self.repo.exists())
    def test_destination_or_both_absent_never_implies_success(self):
        journal=self.prepare();self.dest.mkdir()
        with self.assertRaisesRegex(move.RelocationError,'Ambiguous'):self.controller.apply(journal,recover=True)
        self.dest.rmdir();self.repo.rename(self.root/'retained-repository')
        with self.assertRaisesRegex(move.RelocationError,'Ambiguous'):self.controller.apply(journal,recover=True)
    def test_backup_or_plan_tampering_blocks_actions(self):
        journal=self.prepare();p=journal/'before/0';p.write_bytes(b'corrupt')
        with self.assertRaisesRegex(move.RelocationError,'Recovery bytes'):self.controller.apply(journal)
        self.assertTrue(self.repo.exists())
    def test_active_tasks_unaccepted_bindings_and_same_volume_guards(self):
        self.adapter.disabled=False
        with self.assertRaises(move.RelocationError):self.prepare()
        self.adapter.disabled=True;self.adapter.accepted=False
        with self.assertRaises(move.RelocationError):self.prepare()
        self.adapter.accepted=True
        with self.assertRaises(move.RelocationError):self.controller.prepare(self.repo,self.repo/'inside',self.consumers,self.bindings)
        with self.assertRaises(move.RelocationError):self.controller.prepare(self.repo,self.dest,[(self.repo/'app.py',b'{}')],self.bindings)
    def test_external_objects_and_executable_git_config_fail_before_status(self):
        path=self.repo/'.git/objects/info/alternates';path.write_text(str(self.root/'outside'),encoding='utf-8')
        with self.assertRaisesRegex(move.RelocationError,'External Git object'):self.prepare()
        path.unlink();self.g('config','core.fsmonitor','unapproved-helper')
        with self.assertRaisesRegex(move.RelocationError,'executable Git'):self.prepare()
        self.assertTrue(self.repo.exists());self.assertFalse(self.dest.exists())
    def test_existing_repository_mutex_blocks_quiescence_without_moving_files(self):
        journal=self.prepare()
        with auto.exclusive(self.repo/'.offlinegpt-autosync-mutex',create_parent=False):
            with self.assertRaises(auto.Error):self.controller.apply(journal)
        self.assertTrue(self.repo.exists());self.assertFalse(self.dest.exists());self.assertEqual(move.snapshot(self.repo),self.before)
    def test_lock_contention_and_unfinished_journal_preserve_current_state(self):
        journal=self.prepare()
        with self.assertRaisesRegex(move.RelocationError,'Resolve existing'):self.prepare()
        with auto.exclusive(self.root/'canonical-layout.lock'):
            with self.assertRaises(auto.Error):self.controller.apply(journal)
        self.assertEqual(move.snapshot(self.repo),self.before)

class CanonicalAdoptionIntegrationTests(unittest.TestCase):
    def test_relocated_config_and_epoch_load_through_actual_canonical_manager(self):
        import test_auto_sync as fixture_module
        fixture=fixture_module.AutoSyncTests('runTest');fixture.setUp();self.addCleanup(fixture.doCleanups)
        old=fixture.repo.resolve();dest=old.with_name('OfflineGPT');cfg=dict(fixture.cfg)
        contract={'version':1,'epoch':1,'root':str(dest),'repositoryConfigSha256':auto.sync.digest((old/'.git/config').read_bytes()),'ancestorCommit':fixture.g('rev-parse','HEAD').decode().strip(),'branch':cfg['branch'],'remoteUrl':cfg['remoteUrl'],'policySha256':auto.sync.file_hash(old,auto.sync.POLICY),'productionRoot':str((old.parent/'protected-production').resolve())}
        cfg.update(version=3,repo=str(dest),source=str(dest),layoutContract=contract)
        # Actual configuration key is repository, not the internal Manager.repo field.
        cfg.pop('repo',None);cfg['repository']=str(dest)
        control=fixture.private/'control.json'
        original_control=json.loads(control.read_text(encoding='utf-8'))
        adopted_control={**original_control,'layoutEpoch':1,'layoutContractSha256':auto.sync.digest(auto.sync.canonical(contract))}
        adapter=Adapter(fixture.private,old,dest)
        adapter.verify_after=lambda plan,new: auto.Manager(fixture.config,local_fixture=True).guard(clean=False)
        adapter.verify_rollback=lambda plan,old: auto.Manager(fixture.config,local_fixture=True).guard(clean=False)
        coordinator=move.Relocation(fixture.private/'relocation-journal',fixture.f.git,adapter)
        plan=coordinator.prepare(old,dest,[(fixture.config,move.canonical(cfg)),(control,move.canonical(adopted_control))],{'fixtureOnly':True,'reviewedEpoch':1})
        before=move.git_state(old,fixture.f.git)
        self.assertEqual(coordinator.apply(plan)['status'],'COMPLETE')
        manager=auto.Manager(fixture.config,local_fixture=True);self.assertTrue(manager.canonical_mode);self.assertEqual(manager.repo,dest)
        self.assertEqual(move.git_state(dest,fixture.f.git),before)
        self.assertEqual(coordinator.rollback(plan)['status'],'ROLLED_BACK')
        self.assertFalse(auto.Manager(fixture.config,local_fixture=True).canonical_mode)

if __name__=='__main__':unittest.main(verbosity=2)
