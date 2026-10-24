"""Disposable real Git/bare-remote checks for the existing-controller overlay."""
import importlib.util,json,os,sys,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
import test_auto_sync as legacy
spec=importlib.util.spec_from_file_location('canonical_test',ROOT/'scripts/auto_sync.py');auto=importlib.util.module_from_spec(spec);spec.loader.exec_module(auto)

class CanonicalModeTests(unittest.TestCase):
    g=legacy.AutoSyncTests.g
    def setUp(self):
        legacy.AutoSyncTests.setUp(self)
        self.cfg.update(version=3,source=str(self.repo.resolve()))
        contract={'version':1,'epoch':1,'root':str(self.repo.resolve()),'repositoryConfigSha256':auto.sync.digest((self.repo/'.git/config').read_bytes()),'ancestorCommit':self.g('rev-parse','HEAD').decode().strip(),'branch':self.cfg['branch'],'remoteUrl':self.cfg['remoteUrl'],'policySha256':auto.sync.file_hash(self.repo,auto.sync.POLICY),'productionRoot':str((self.repo.parent/'protected-production').resolve())}
        self.cfg['layoutContract']=contract
        self.config.write_text(json.dumps(self.cfg),encoding='utf-8')
        self.control={'mode':'running','layoutEpoch':1,'layoutContractSha256':auto.sync.digest(auto.sync.canonical(contract))}
        (self.private/'control.json').write_text(json.dumps(self.control),encoding='utf-8')
        self.manager=auto.Manager(self.config,local_fixture=True)
    def write_cfg(self):self.config.write_text(json.dumps(self.cfg),encoding='utf-8')
    def edit(self):(self.repo/'app.py').write_text('VALUE=2\n',encoding='utf-8')
    def test_real_working_tree_to_bare_remote_uses_existing_transaction(self):
        self.edit();batch=self.manager.prepare('Verified canonical synthetic batch')
        self.assertEqual(batch['status'],'AWAITING_REVIEW')
        self.manager.accept(batch['id'],batch['snapshotDigest'],legacy.REVIEWS)
        result=self.manager.once();self.assertEqual(result['status'],'COMPLETE')
        self.assertTrue(result['ci']['fixtureOnly']);self.assertFalse(self.g('status','--porcelain').strip())
        self.assertEqual(self.manager.once()['status'],'IDLE')
    def test_existing_partial_or_full_index_is_never_overwritten(self):
        self.edit();self.g('add','app.py');(self.repo/'app.py').write_text('VALUE=3\n',encoding='utf-8')
        before=self.g('ls-files','--stage','-z')
        with self.assertRaisesRegex(auto.Error,'staged work'):self.manager.prepare('Reject staged synthetic work')
        self.assertEqual(before,self.g('ls-files','--stage','-z'))
    def test_repository_owned_readme_and_unknown_paths_block(self):
        (self.repo/'README.md').write_text('Human edit',encoding='utf-8')
        with self.assertRaisesRegex(auto.Error,'unapproved'):self.manager.prepare('Reject owned synthetic changes')
        self.g('restore','README.md');(self.repo/'new_unknown.py').write_text('X=3',encoding='utf-8')
        with self.assertRaises(auto.Error):self.manager.prepare('Reject unknown synthetic changes')
    def test_live_source_edit_invalidates_accepted_snapshot(self):
        self.edit();batch=self.manager.prepare('Verified canonical synthetic batch')
        self.manager.accept(batch['id'],batch['snapshotDigest'],legacy.REVIEWS)
        (self.repo/'app.py').write_text('VALUE=4\n',encoding='utf-8')
        self.assertEqual(self.manager.once()['status'],'STALE')
    def test_explicit_epoch_receipt_required_and_downgrade_rejected(self):
        (self.private/'control.json').write_text('{"mode":"running"}',encoding='utf-8')
        with self.assertRaisesRegex(auto.Error,'epoch mismatch'):auto.Manager(self.config,local_fixture=True)
        (self.private/'control.json').write_text(json.dumps(self.control),encoding='utf-8')
        self.cfg.pop('layoutContract');self.cfg.update(version=1,source=str(self.source));self.write_cfg()
        with self.assertRaisesRegex(auto.Error,'downgrade'):auto.Manager(self.config,local_fixture=True)
    def test_boolean_versions_and_epoch_cannot_impersonate_integer_contract(self):
        self.cfg['layoutContract']['version']=True;self.write_cfg()
        with self.assertRaisesRegex(auto.Error,'Unsupported canonical'):auto.Manager(self.config,local_fixture=True)
        self.cfg['layoutContract']['version']=1;self.write_cfg()
        self.control['layoutEpoch']=True
        (self.private/'control.json').write_text(json.dumps(self.control),encoding='utf-8')
        with self.assertRaisesRegex(auto.Error,'epoch mismatch'):auto.Manager(self.config,local_fixture=True)
    def test_version_three_policy_requires_a_complete_layout_schema(self):
        self.assertIsNone(auto.publication_policy(self.cfg))
        with self.assertRaises(auto.Error):auto.publication_policy({'version':3})
        bad=dict(self.cfg);bad['publicationPolicy']={'repositoryId':1,'visibility':'public','scope':'binary'}
        with self.assertRaises(auto.Error):auto.publication_policy(bad)
    def test_noncanonical_production_alias_requires_explicit_resolution(self):
        alias=self.repo.parent/'uncreated-alias'/'..'/'protected-production'
        self.cfg['layoutContract']['productionRoot']=str(alias);self.write_cfg()
        with self.assertRaisesRegex(auto.Error,'exact absolute'):auto.Manager(self.config,local_fixture=True)
        self.cfg['layoutContract']['productionRoot']=str(alias.resolve());self.write_cfg()
        self.assertTrue(auto.Manager(self.config,local_fixture=True).canonical_mode)
    def test_explicit_production_boundary_cannot_overlap_repository(self):
        self.cfg['layoutContract']['productionRoot']=str(self.repo.resolve());self.write_cfg()
        with self.assertRaisesRegex(auto.Error,'overlaps production'):auto.Manager(self.config,local_fixture=True)
    def test_all_canonical_repository_overlap_relations_are_rejected(self):
        for target in (self.repo.resolve(),self.repo.parent.resolve(),(self.repo/'nested-production').resolve()):
            with self.subTest(target=target.name):
                self.cfg['layoutContract']['productionRoot']=str(target);self.write_cfg()
                with self.assertRaisesRegex(auto.Error,'overlaps production'):auto.Manager(self.config,local_fixture=True)
    def test_missing_production_boundary_is_not_inferred_from_new_parent(self):
        self.cfg['layoutContract'].pop('productionRoot');self.write_cfg()
        with self.assertRaisesRegex(auto.Error,'Unsupported canonical'):auto.Manager(self.config,local_fixture=True)
    def test_exact_root_and_nested_root_rejected(self):
        self.cfg['source']=str(self.repo/'nested');self.write_cfg()
        with self.assertRaisesRegex(auto.Error,'exact single'):auto.Manager(self.config,local_fixture=True)
    def test_contract_policy_and_repository_identity_pins(self):
        with (self.repo/'.git/config').open('a',encoding='utf-8') as f:f.write('\n[offlinegpt]\n changed = true\n')
        with self.assertRaisesRegex(auto.Error,'identity changed'):auto.Manager(self.config,local_fixture=True)
    def test_legacy_same_root_remains_forbidden(self):
        self.cfg.pop('layoutContract');self.cfg['version']=1;self.write_cfg()
        with self.assertRaisesRegex(auto.Error,'overlap'):auto.Manager(self.config,local_fixture=True)
        with self.assertRaisesRegex(auto.Error,'disjoint'):auto.sync.context(self.repo,self.repo,self.cfg['git'])
    def test_failed_ci_never_marks_candidate_accepted(self):
        self.edit();batch=self.manager.prepare('Verified canonical synthetic batch');self.manager.accept(batch['id'],batch['snapshotDigest'],legacy.REVIEWS)
        with patch.object(self.manager,'ci',return_value={'status':'completed','conclusion':'failure'}):result=self.manager.once()
        self.assertEqual(result['status'],'CI_FAILED');self.assertNotEqual(result.get('candidateBuild'),'QUEUED_FOR_SEPARATE_VERIFIED_BUILD_CONTROLLER')
    def test_links_and_traversal_never_satisfy_contract(self):
        self.cfg['layoutContract']['root']=str(self.repo/'..');self.write_cfg()
        with self.assertRaisesRegex(auto.Error,'exact single'):auto.Manager(self.config,local_fixture=True)
    def test_external_layout_gate_retains_exclusion_across_same_volume_rename(self):
        destination=self.repo.with_name('renamed-repository')
        with self.manager.layout_lock():
            with auto.exclusive(self.manager.lock,create_parent=False):pass
            self.repo.rename(destination)
            with self.assertRaises(auto.Error):self.manager.prepare('Blocked concurrent publication')
            self.assertFalse(self.repo.exists())
            destination.rename(self.repo)
    def test_stale_canonical_manager_never_recreates_the_old_repository_root(self):
        destination=self.repo.with_name('renamed-repository');self.repo.rename(destination)
        with self.assertRaisesRegex(auto.Error,'no longer exists'):self.manager.prepare('Reject stale root')
        self.assertFalse(self.repo.exists());self.assertTrue((destination/'.git').is_dir())
    def test_canonical_discovery_is_read_only_and_disjoint(self):
        self.edit();before=self.g('ls-files','--stage','-z');head=self.g('rev-parse','HEAD')
        result=self.manager.once();self.assertEqual(result['status'],'IDLE');self.assertEqual(result['pendingActions'],1)
        self.assertEqual(before,self.g('ls-files','--stage','-z'));self.assertEqual(head,self.g('rev-parse','HEAD'))
        self.assertFalse(list(self.private.glob('canonical-discovery-*')))

if __name__=='__main__':unittest.main(verbosity=2)
