"""Real disposable Git and filesystem transactions, synthetic evidence only."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import test_auto_sync as fixtures

spec=importlib.util.spec_from_file_location('milestones_under_test',Path(__file__).resolve().parents[1]/'scripts/milestone_docs.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class MilestoneTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.AutoSyncTests('runTest');self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.f=self.fixture;self.manager=m.auto.Manager(self.f.config,local_fixture=True);self.repo=self.manager.repo
        self.definition={'id':'synthetic-scope-v1','kind':'engineering_infrastructure','title':'Synthetic engineering milestone','summary':'Synthetic infrastructure proof for controller regression only.','limitation':'This fixture is not real application or model acceptance.','inputs':['app.py'],'evidenceKind':'windows_integration','checks':['actual-operation','preservation'],'minimumImprovement':0}
        self.commit_definition()
        self.state=self.manager.state/'milestones';self.controller=m.Milestones(self.manager,self.state)
        self.evidence=self.state/'review.json';self.ledger_path=self.manager.state/'CHANGE_LEDGER.json'
        self.write_evidence()
    def commit_definition(self):
        target=self.repo/m.CONTRACTS;target.parent.mkdir(exist_ok=True);target.write_text(json.dumps({'version':1,'contracts':[self.definition]}))
        self.f.g('add','--all');self.f.g('commit','-qm','Synthetic contract');self.f.g('push','-q','origin','HEAD:refs/heads/codex/test');self.commit=self.manager.head()
    def write_evidence(self):
        self.inputs={'app.py':m.s.digest(self.manager.git('show',self.commit+':app.py'))}
        self.ledger=[{'batch':'synthetic-reviewed-batch','commit':self.commit,'title':'Do not publish this private title','ci':{'head_sha':self.commit,'status':'completed','conclusion':'success'}}]
        self.ledger_path.write_text(json.dumps(self.ledger))
        artifact=self.state/'operation.json';artifact.write_text(json.dumps({'fixtureOnly':True,'operation':'synthetic-fs-test'}))
        self.proof={'version':1,'sourceCommit':self.commit,'inputs':self.inputs,'kind':self.definition['evidenceKind'],'execution':'actual','modelInvocations':0,'checks':[{'id':k,'passed':True,'independentlyReviewed':True} for k in self.definition['checks']],'measurements':{},'artifacts':[{'path':artifact.name,'sha256':m.s.digest(artifact.read_bytes())}]}
        self.cert={'version':1,'ledgerSha256':m.s.digest(self.ledger_path.read_bytes()),'entries':[{'batch':self.ledger[0]['batch'],'contract':self.definition['id'],'sourceCommit':self.commit,'inputs':self.inputs,'proof':'proof.json','proofSha256':'','reviews':dict.fromkeys(m.REVIEWS,True)}]}
        self.save_proof()
    def save_proof(self):
        path=self.state/'proof.json';path.write_text(json.dumps(self.proof));self.cert['entries'][0]['proofSha256']=m.s.digest(path.read_bytes());self.save_cert()
    def save_cert(self):self.evidence.write_text(json.dumps(self.cert))
    def prepare(self):return self.controller.prepare(self.evidence)
    def apply(self,plan):return self.controller.apply(plan['id'],plan['digest'],True)
    def test_maintenance_counts_and_ci_alone_do_not_change_readme(self):
        original=(self.repo/'README.md').read_bytes();self.cert['entries']=[];self.save_cert();plan=self.prepare()
        self.assertEqual(plan['status'],'NO_DOCUMENT_CHANGES');self.assertEqual(plan['classification']['maintenanceCount'],1)
        self.assertEqual(self.apply(plan)['status'],'NO_DOCUMENT_CHANGES');self.assertEqual((self.repo/'README.md').read_bytes(),original);self.assertFalse((self.repo/'CHANGELOG.md').exists())
    def test_synthetic_reviewed_infrastructure_writes_only_fixed_docs_without_commit(self):
        head=self.manager.head();index=self.manager.git_identity()['index'];plan=self.prepare();self.assertEqual(plan['status'],'AWAITING_PUBLIC_CONTENT_REVIEW')
        self.assertEqual(self.apply(plan)['paths'],['README.md','CHANGELOG.md'])
        for name in ('README.md','CHANGELOG.md'):
            value=(self.repo/name).read_text();self.assertIn('Engineering infrastructure (non-native)',value);self.assertNotIn(self.ledger[0]['title'],value);self.assertNotIn('operation.json',value)
        self.assertEqual(self.manager.head(),head);self.assertEqual(self.manager.git_identity()['index'],index)
    def test_exact_digest_and_review_required(self):
        plan=self.prepare()
        with self.assertRaises(m.Error):self.controller.apply(plan['id'],'0'*64,True)
        with self.assertRaises(m.Error):self.controller.apply(plan['id'],plan['digest'],False)
        self.assertFalse((self.repo/'CHANGELOG.md').exists())
    def test_source_ci_failure_or_remote_drift_blocks(self):
        with patch.object(self.manager,'ci',return_value={'status':'completed','conclusion':'failure'}),self.assertRaises(m.Error):self.prepare()
        with patch.object(self.manager,'remote_head',return_value='0'*40),self.assertRaises(m.Error):self.prepare()
    def test_scope_hash_change_and_missing_scope_are_rejected(self):
        self.cert['entries'][0]['inputs']={};self.save_cert()
        with self.assertRaisesRegex(m.Error,'scope'):self.prepare()
        self.cert['entries'][0]['inputs']=self.inputs;self.save_cert();(self.repo/'app.py').write_text('VALUE=3\n');self.f.g('add','app.py');self.f.g('commit','-qm','Synthetic changed scope');self.f.g('push','-q','origin','HEAD:refs/heads/codex/test')
        with self.assertRaisesRegex(m.Error,'stale'):self.prepare()
    def test_ledger_or_proof_mutation_invalidates_review(self):
        self.ledger_path.write_text(json.dumps(self.ledger+[{'batch':'new'}]))
        with self.assertRaisesRegex(m.Error,'ledger changed'):self.prepare()
        self.ledger_path.write_text(json.dumps(self.ledger));(self.state/'proof.json').write_text('{}')
        with self.assertRaisesRegex(m.Error,'digest'):self.prepare()
    def test_failed_duplicate_and_missing_check_never_certify(self):
        original=copy.deepcopy(self.proof)
        for checks in ([self.proof['checks'][0]], [self.proof['checks'][0]]*2, [{**c,'passed':False} for c in self.proof['checks']]):
            self.proof={**original,'checks':checks};self.save_proof()
            with self.assertRaises(m.Error):self.prepare()
    def test_mock_and_source_governance_are_not_actual_evidence(self):
        for kind,execution in [('windows_integration','mock'),('source_ci','actual')]:
            self.proof.update(kind=kind,execution=execution);self.save_proof()
            with self.assertRaises(m.Error):self.prepare()
    def test_native_claim_requires_genuine_model_invocation_and_review(self):
        self.definition.update(kind='application_capability',evidenceKind='native_semantic');self.commit_definition();self.write_evidence()
        with self.assertRaisesRegex(m.Error,'invoke'):self.prepare()
        self.proof['modelInvocations']=1;self.save_proof();self.cert['entries'][0]['reviews']['independentCorrectness']=False;self.save_cert()
        with self.assertRaisesRegex(m.Error,'review'):self.prepare()
    def test_performance_requires_comparable_meaningful_repeated_samples(self):
        self.definition.update(kind='application_performance',evidenceKind='performance_comparison',minimumImprovement=.2);self.commit_definition();self.write_evidence()
        self.proof['measurements']={'baselineMs':[100,110,100],'updatedMs':[50,60,50],'sameHardware':True,'sameWorkload':True,'qualityAccepted':True,'coldWarmSeparated':True};self.save_proof();self.assertEqual(self.prepare()['classification']['certifiedCount'],1)
        original=copy.deepcopy(self.proof['measurements'])
        for mutation in ({'updatedMs':[99,99,99]},{'updatedMs':[50]},{'updatedMs':[float('nan')]*3},{'sameWorkload':False},{'qualityAccepted':False}):
            self.proof['measurements']={**original,**mutation};self.save_proof()
            with self.assertRaises(m.Error):self.prepare()
    def test_artifact_changed_missing_or_traversal_is_blocked(self):
        (self.state/'operation.json').write_text('changed')
        with self.assertRaisesRegex(m.Error,'artifact'):self.prepare()
        self.proof['artifacts'][0]['path']='../operation.json';self.save_proof()
        with self.assertRaises(m.Error):self.prepare()
    def test_duplicate_batches_or_certifications_block(self):
        self.ledger_path.write_text(json.dumps(self.ledger*2));self.cert['ledgerSha256']=m.s.digest(self.ledger_path.read_bytes());self.save_cert()
        with self.assertRaisesRegex(m.Error,'Ambiguous'):self.prepare()
        self.ledger_path.write_text(json.dumps(self.ledger));self.cert['ledgerSha256']=m.s.digest(self.ledger_path.read_bytes());self.cert['entries']*=2;self.save_cert()
        with self.assertRaisesRegex(m.Error,'duplicate'):self.prepare()
    def test_sanitized_contract_only_no_private_prose_or_markup(self):
        self.definition['summary']='Unsafe <script>markup</script> for synthetic test';self.commit_definition();self.write_evidence()
        with self.assertRaisesRegex(m.Error,'markup'):self.prepare()
    def test_staged_human_edit_and_other_source_changes_survive(self):
        plan=self.prepare();(self.repo/'README.md').write_text('Human content');self.f.g('add','README.md');index=self.manager.git_identity()['index']
        with self.assertRaises(m.Error):self.apply(plan)
        self.assertEqual((self.repo/'README.md').read_text(),'Human content');self.assertEqual(self.manager.git_identity()['index'],index)
    def interrupt(self):
        plan=self.prepare();real=m.s.atomic_write
        def stop(path,data):
            if path.resolve()==(self.repo/'CHANGELOG.md').resolve():raise OSError('Synthetic interruption')
            real(path,data)
        with patch.object(m.s,'atomic_write',side_effect=stop),self.assertRaises(OSError):self.apply(plan)
        self.assertEqual(json.loads((self.state/plan['id']/'journal.json').read_text())['status'],'APPLYING');return plan
    def test_interrupted_two_file_write_recovers_without_assuming_absence(self):
        plan=self.interrupt();self.assertFalse((self.repo/'CHANGELOG.md').exists());self.assertTrue(self.controller.recover(plan['id'])['recovered'])
        self.assertEqual((self.repo/'README.md').read_text().count('<!-- milestone:'),1);self.assertTrue((self.repo/'CHANGELOG.md').exists())
    def test_unknown_recovery_bytes_preserved_not_overwritten(self):
        plan=self.interrupt();(self.repo/'CHANGELOG.md').write_text('Human rescue note')
        with self.assertRaisesRegex(m.Error,'Unknown document'):self.controller.recover(plan['id'])
        self.assertEqual((self.repo/'CHANGELOG.md').read_text(),'Human rescue note')
    def test_recovery_refuses_unrelated_changes(self):
        plan=self.interrupt();(self.repo/'app.py').write_text('VALUE=4\n')
        with self.assertRaisesRegex(m.Error,'Unrelated'):self.controller.recover(plan['id'])
        self.assertEqual((self.repo/'app.py').read_text(),'VALUE=4\n')
    def test_existing_accepted_milestone_is_not_written_twice(self):
        self.apply(self.prepare());self.f.g('add','README.md','CHANGELOG.md');self.f.g('commit','-qm','Synthetic reviewed docs');self.f.g('push','-q','origin','HEAD:refs/heads/codex/test')
        self.assertEqual(self.prepare()['status'],'NO_DOCUMENT_CHANGES')
    def test_ambiguous_managed_markers_block(self):
        (self.repo/'README.md').write_text(m.START+'\n'+m.START+'\n'+m.END+'\n'+m.END);self.f.g('add','README.md');self.f.g('commit','-qm','Synthetic broken markers');self.f.g('push','-q','origin','HEAD:refs/heads/codex/test')
        with self.assertRaisesRegex(m.Error,'markers'):self.prepare()
    def test_private_state_cannot_target_production_or_checkout(self):
        with self.assertRaises(m.Error):m.Milestones(self.manager,self.repo/'milestones')
        with self.assertRaises(m.Error):m.Milestones(self.manager,self.manager.source.parent/'Offline GPT')
    def test_forged_plan_targets_cannot_modify_application(self):
        plan=self.prepare();path=self.state/plan['id']/'plan.json';value=json.loads(path.read_text());value['before']['app.py']='VALUE=1\n';value['after']['app.py']='VALUE=0\n';value['digest']=m.s.digest(m.s.canonical({k:v for k,v in value.items() if k!='digest'}));path.write_bytes(m.s.canonical(value))
        with self.assertRaisesRegex(m.Error,'targets'):self.controller.load_plan(plan['id'])
    def test_historical_ci_regression_cannot_hide_behind_current_ci(self):
        plan=self.prepare();old=self.commit
        (self.repo/'NOTE.md').write_text('Synthetic unrelated documentation');self.f.g('add','NOTE.md');self.f.g('commit','-qm','Synthetic later source');self.f.g('push','-q','origin','HEAD:refs/heads/codex/test')
        real=self.manager.ci
        with patch.object(self.manager,'ci',side_effect=lambda commit: {'status':'completed','conclusion':'failure'} if commit==old else real(commit)),self.assertRaisesRegex(m.Error,'Historical'):self.prepare()
    def test_recovery_rechecks_artifact_and_rendered_contract(self):
        plan=self.interrupt();(self.state/'operation.json').write_text('tampered execution evidence')
        with self.assertRaisesRegex(m.Error,'artifact'):self.controller.recover(plan['id'])
        self.assertFalse((self.repo/'CHANGELOG.md').exists())

if __name__=='__main__':unittest.main()
