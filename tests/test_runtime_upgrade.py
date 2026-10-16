"""Disposable real Git + task adapter faults; never alter an actual Windows task."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import test_auto_sync as fixtures

spec=importlib.util.spec_from_file_location('runtime_upgrade_test',Path(__file__).resolve().parents[1]/'scripts/runtime_upgrade.py')
upgrade=importlib.util.module_from_spec(spec);spec.loader.exec_module(upgrade)

class Task:
    def __init__(self,action):self.current=copy.deepcopy(action);self.enabled=True;self.running=False;self.trigger='daily';self.result_code=0;self.last_run=0
    def read(self):
        return {'action':copy.deepcopy(self.current),'enabled':self.enabled,'running':self.running,
            'xml':'<Task><Triggers><Trigger><Enabled>true</Enabled><Type>'+self.trigger+'</Type></Trigger></Triggers><Principal>limited-user</Principal><Settings><Enabled>'+str(self.enabled)+'</Enabled><Limit>PT2M</Limit></Settings><Actions><Command>'+self.current['execute']+'</Command></Actions></Task>'}
    def enable(self,value):self.enabled=value
    def action(self,value):self.current=copy.deepcopy(value)
    def result(self):return {'running':self.running,'result':self.result_code,'lastRun':self.last_run}

class UpgradeTests(unittest.TestCase):
    def setUp(self):
        fixture=fixtures.AutoSyncTests('runTest');fixture.setUp();self.addCleanup(fixture.doCleanups);self.f=fixture
        self.private=fixture.private
        binary=self.private/'interpreter';binary.mkdir();python=binary/'python.exe';python.write_bytes(b'Synthetic interpreter - never executed')
        (binary/'pythonw.exe').write_bytes(b'Synthetic windowless interpreter - never executed')
        config=fixture.cfg.copy();config['python']=str(python);fixture.config.write_text(json.dumps(config))
        (fixture.repo/'scripts').mkdir(exist_ok=True)
        for name in upgrade.runner.SCRIPTS:(fixture.repo/'scripts'/name).write_text('# New accepted synthetic controller\n')
        fixture.g('add','scripts');fixture.g('commit','-qm','Synthetic controller revision');fixture.g('push','-q','origin','HEAD:refs/heads/codex/test')
        old=self.private/'runtime'/('c'*40)/'scripts';old.mkdir(parents=True)
        for name in upgrade.runner.SCRIPTS:(old/name).write_text('# Old synthetic controller\n')
        self.manifest={'version':1,'commit':'c'*40,'privateRoot':str(self.private),'runtimeRoot':str(old.parent),'repository':str(fixture.repo),'source':str(fixture.source),
          'configPath':str(fixture.config),'configSha256':upgrade.runner.sha(fixture.config),'pythonPath':str(python),'pythonSha256':upgrade.runner.sha(python),
          'scripts':{n:upgrade.runner.sha(old/n) for n in upgrade.runner.SCRIPTS}}
        self.pointer=self.private/'active-runtime.json';self.pointer.write_text(json.dumps(self.manifest,indent=4))
        self.old_bytes=self.pointer.read_bytes();self.u=upgrade.Upgrade(fixture.config,local_fixture=True)
        self.task=Task(self.u.action_for(self.manifest));self.u.task=self.task;self.old_action=copy.deepcopy(self.task.current)
    def prepare(self):return self.u.prepare()['id']
    def smoke(self,identifier):
        _,j=self.u.load(identifier)
        self.task.last_run=j['applied']+1
        upgrade.runner.atomic(self.private/'last-run.json',{'controllerCommit':j['commit'],'started':j['applied']+1,'returnCode':0,'status':'IDLE'})
    def test_repeatable_upgrade_retains_old_runtime_and_requires_scheduled_smoke(self):
        identifier=self.prepare();self.u.apply(identifier)
        with self.assertRaises((upgrade.Error,FileNotFoundError)):self.u.verify(identifier)
        self.smoke(identifier);self.assertEqual(self.u.verify(identifier)['status'],'COMPLETE')
        self.assertTrue((Path(self.manifest['runtimeRoot'])/'scripts/auto_sync.py').exists())
        with self.assertRaises(upgrade.Error):self.u.prepare() # Already at this commit.
        (self.f.repo/'scripts/auto_sync.py').write_text('# Next accepted synthetic revision\n');self.f.g('add','scripts/auto_sync.py');self.f.g('commit','-qm','Next synthetic controller');self.f.g('push','-q','origin','HEAD:refs/heads/codex/test')
        second=self.prepare();self.u.apply(second);self.smoke(second)
        self.assertEqual(self.u.verify(second)['status'],'COMPLETE');self.assertNotEqual(identifier,second)
    def test_failed_ci_and_remote_drift_never_mutate_task(self):
        with patch.object(self.u.manager,'ci',return_value={'status':'completed','conclusion':'failure'}),self.assertRaises(upgrade.Error):self.u.prepare()
        with patch.object(self.u.manager,'remote_head',return_value='d'*40),self.assertRaises(upgrade.Error):self.u.prepare()
        self.assertEqual(self.pointer.read_bytes(),self.old_bytes);self.assertEqual(self.task.current,self.old_action)
    def test_task_trigger_change_blocks_even_if_action_is_unchanged(self):
        identifier=self.prepare();self.task.trigger='human-change'
        with self.assertRaises(upgrade.Error):self.u.apply(identifier)
        self.assertEqual(self.pointer.read_bytes(),self.old_bytes)
    def test_trigger_enable_flag_is_part_of_protected_definition(self):
        a='<Task><Triggers><Enabled>true</Enabled></Triggers><Settings><Enabled>true</Enabled></Settings></Task>'
        b=a.replace('<Triggers><Enabled>true','<Triggers><Enabled>false')
        self.assertNotEqual(upgrade.definition(a),upgrade.definition(b))
    def test_running_task_blocks_without_force_termination(self):
        self.task.running=True
        with self.assertRaises(upgrade.Error):self.u.prepare()
        self.assertTrue(self.task.enabled)
    def test_interrupted_pointer_switch_rolls_back_exact_original_bytes(self):
        identifier=self.prepare()
        with patch.object(self.task,'action',side_effect=RuntimeError('Synthetic interruption')):
            with self.assertRaises(RuntimeError):self.u.apply(identifier)
        self.assertFalse(self.task.enabled);self.assertNotEqual(self.pointer.read_bytes(),self.old_bytes)
        with self.assertRaises(upgrade.Error):self.u.apply(identifier)
        result=self.u.rollback(identifier);self.assertEqual(result['status'],'ROLLED_BACK')
        self.assertEqual(self.pointer.read_bytes(),self.old_bytes);self.assertEqual(self.task.current,self.old_action);self.assertTrue(self.task.enabled)
    def test_unknown_pointer_or_action_is_never_overwritten_during_recovery(self):
        identifier=self.prepare();original=self.pointer.read_bytes();self.pointer.write_text('{}')
        with self.assertRaises(upgrade.Error):self.u.rollback(identifier)
        self.assertEqual(self.pointer.read_text(),'{}');self.pointer.write_bytes(original)
        self.task.current['arguments']='human change'
        with self.assertRaises(upgrade.Error):self.u.rollback(identifier)
        self.assertEqual(self.task.current['arguments'],'human change')
    def test_existing_runtime_content_is_not_overwritten(self):
        target=self.private/'runtime'/self.u.manager.head()/'scripts';target.mkdir(parents=True)
        (target/'auto_sync.py').write_text('UNREVIEWED=1')
        with self.assertRaises(upgrade.Error):self.u.prepare()
        self.assertEqual((target/'auto_sync.py').read_text(),'UNREVIEWED=1')
    def test_stale_or_failed_scheduled_receipt_cannot_complete_upgrade(self):
        identifier=self.prepare();self.u.apply(identifier);self.smoke(identifier)
        receipt=json.loads((self.private/'last-run.json').read_text())
        for changes in ({'started':0},{'controllerCommit':'e'*40},{'returnCode':2},{'status':'UNKNOWN'}):
            upgrade.runner.atomic(self.private/'last-run.json',{**receipt,**changes})
            with self.assertRaises(upgrade.Error):self.u.verify(identifier)
        self.smoke(identifier);self.task.result_code=1
        with self.assertRaises(upgrade.Error):self.u.verify(identifier)
    def test_backup_and_frozen_code_tampering_blocks_recovery(self):
        identifier=self.prepare();path,_=self.u.load(identifier)
        (path/'old-runtime.json').write_text('{}')
        with self.assertRaises((upgrade.Error,KeyError)):self.u.rollback(identifier)
    def test_pending_upgrade_blocks_new_prepare_and_lock_excludes_publication(self):
        self.prepare()
        with self.assertRaises(upgrade.Error):self.u.prepare()
        with upgrade.auto.exclusive(self.u.manager.lock),self.assertRaises(upgrade.auto.Error):self.u.prepare()
    def test_manual_runner_receipt_without_fresh_task_run_is_not_scheduled_proof(self):
        identifier=self.prepare();self.u.apply(identifier);self.smoke(identifier);self.task.last_run=0
        with self.assertRaises(upgrade.Error):self.u.verify(identifier)
    def test_interruption_after_action_switch_can_be_recovered(self):
        identifier=self.prepare();original=self.u.save
        def interrupted(path,status,**fields):
            if status=='VERIFY_PENDING':raise RuntimeError('Synthetic crash before final journal write')
            return original(path,status,**fields)
        with patch.object(self.u,'save',side_effect=interrupted),self.assertRaises(RuntimeError):self.u.apply(identifier)
        self.assertNotEqual(self.task.current,self.old_action);self.assertFalse(self.task.enabled)
        self.assertEqual(self.u.rollback(identifier)['status'],'ROLLED_BACK');self.assertEqual(self.pointer.read_bytes(),self.old_bytes)
    def test_original_disabled_state_is_restored(self):
        self.task.enabled=False;identifier=self.prepare();self.u.apply(identifier)
        self.assertFalse(self.task.enabled);self.u.rollback(identifier);self.assertFalse(self.task.enabled)

if __name__=='__main__':unittest.main()
