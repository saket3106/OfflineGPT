"""Synchronization acceptance on disposable repositories; never production data."""
import importlib.util
from contextlib import chdir
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('source_sync',Path(__file__).resolve().parents[1]/'scripts/sync_source.py')
sync=importlib.util.module_from_spec(spec);spec.loader.exec_module(sync)

class SourceSyncTests(unittest.TestCase):
    def setUp(self):
        self.git=os.environ.get('OFFLINEGPT_TEST_GIT') or shutil.which('git')
        if not self.git:self.skipTest('Git is required for synchronization regressions')
        self.temp=tempfile.TemporaryDirectory(prefix='offlinegpt-sync-tests-');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.repo=self.root/'repo';self.source=self.root/'source';self.reports=self.root/'reports'
        for p in (self.repo,self.source,self.reports):p.mkdir()
        (self.repo/'scripts').mkdir()
        subprocess.run([self.git,'init','--quiet',str(self.repo)],check=True,capture_output=True)
        (self.repo/'.gitignore').write_text('.source-sync.lock\n.offlinegpt-sync-*\n*.gguf\n.env\n')
        for rel,data in {'app.py':'VALUE=1\n','helper.py':'HELPER=1\n'}.items():
            (self.source/rel).write_text(data);(self.repo/rel).write_text(data)
        (self.source/'README.md').write_text('Private development notes')
        (self.repo/'README.md').write_text('Sanitized presentation')
        self.policy={'version':1,'managed':['app.py','helper.py'],'protected':['README.md'],'required':['app.py'],'excluded_candidates':[]}
        self.save_policy()
        self.state={'version':1,'managed':{rel:{'source':sync.file_hash(self.source,rel),'repository':sync.file_hash(self.repo,rel)} for rel in self.policy['managed']},'protected':{'README.md':sync.file_hash(self.source,'README.md')}}
        self.write(sync.STATE,self.state)
        (self.repo/sync.MANIFEST).write_bytes(sync.manifest_data(self.repo,self.git))
        self.git_before=sync.git_fingerprint(self.repo,self.git)

    def write(self,rel,value):
        (self.repo/rel).write_text(json.dumps(value))

    def save_policy(self):self.write(sync.POLICY,self.policy)

    def plan(self):return sync.build_plan(self.repo,self.source,self.git)

    def apply(self,plan=None,reviews=None):
        plan=plan or self.plan();path=self.reports/'plan.json';path.write_text(json.dumps(plan))
        return sync.apply_plan(self.repo,self.source,self.git,path,sync.digest(sync.canonical(plan)),reviews or {'content':True,'dependencies':True,'licenses':True},self.reports)

    def test_noop_is_read_only(self):
        before={p.relative_to(self.repo):p.read_bytes() for p in self.repo.rglob('*') if p.is_file()}
        self.assertEqual(self.plan()['actions'],[]);self.assertEqual(self.apply()['status'],'no_changes')
        self.assertEqual(before,{p.relative_to(self.repo):p.read_bytes() for p in self.repo.rglob('*') if p.is_file()})

    def test_modify_preserves_source_git_index_and_sanitized_readme(self):
        subprocess.run([self.git,'-C',str(self.repo),'add','app.py'],check=True)
        before=sync.git_fingerprint(self.repo,self.git)
        (self.source/'app.py').write_text('VALUE=2\n')
        result=self.apply();self.assertEqual(result['copied'],1)
        self.assertEqual((self.repo/'app.py').read_text(),'VALUE=2\n')
        self.assertEqual((self.source/'app.py').read_text(),'VALUE=2\n')
        self.assertEqual((self.repo/'README.md').read_text(),'Sanitized presentation')
        self.assertEqual(sync.git_fingerprint(self.repo,self.git),before)
        self.assertFalse((self.repo/sync.LOCK).exists())

    def test_unapproved_addition_is_detected_never_copied(self):
        (self.source/'new.py').write_text('NEW=1\n')
        self.assertIn('new.py',self.plan()['unapprovedCandidates']);self.apply()
        self.assertFalse((self.repo/'new.py').exists())

    def test_approved_addition_is_manifested(self):
        (self.source/'new.py').write_text('NEW=1\n');self.policy['managed'].append('new.py');self.save_policy()
        result=self.apply();self.assertEqual(result['copied'],1)
        self.assertIn('new.py',(self.repo/sync.MANIFEST).read_text())
        self.assertEqual((self.repo/sync.MANIFEST).read_bytes(),sync.manifest_data(self.repo,self.git))

    def test_approved_deletion_has_external_backup(self):
        (self.source/'helper.py').unlink();plan=self.plan()
        self.assertEqual(plan['actions'][0]['action'],'delete');result=self.apply(plan)
        self.assertFalse((self.repo/'helper.py').exists());self.assertFalse((self.source/'helper.py').exists())
        journal=sync.load(Path(result['journal']));entry=next(e for e in journal['entries'] if e['file']=='helper.py')
        self.assertEqual((Path(result['journal']).parent/entry['backup']).read_text(),'HELPER=1\n')

    def test_required_deletion_is_blocked(self):
        (self.source/'app.py').unlink();self.assertEqual(self.plan()['missingRequired'],['app.py'])
        with self.assertRaises(sync.SyncError):self.apply()
        self.assertTrue((self.repo/'app.py').exists())

    def test_repository_only_edits_require_reconciliation(self):
        (self.repo/'app.py').write_text('MANUAL=1\n')
        self.assertEqual(self.plan()['conflicts'][0]['kind'],'repository_changed_requires_reconciliation')
        with self.assertRaises(sync.SyncError):self.apply()

    def test_both_changed_conflict_never_overwrites(self):
        (self.source/'app.py').write_text('SOURCE=2\n');(self.repo/'app.py').write_text('REPOSITORY=3\n')
        with self.assertRaises(sync.SyncError):self.apply()
        self.assertEqual((self.repo/'app.py').read_text(),'REPOSITORY=3\n')

    def test_same_change_can_adopt_without_conflict(self):
        for root in (self.repo,self.source):(root/'app.py').write_text('VALUE=7\n')
        self.assertEqual(self.plan()['actions'][0]['action'],'adopt');self.apply()
        self.assertEqual(self.plan()['actions'],[])

    def test_protected_source_updates_are_manual_only(self):
        (self.source/'README.md').write_text('New private notes')
        self.assertEqual(self.plan()['protectedSourceChanges'],['README.md']);self.apply()
        self.assertEqual((self.repo/'README.md').read_text(),'Sanitized presentation')

    def test_credentials_block_before_any_copy(self):
        (self.source/'app.py').write_text('TOKEN="'+'ghp_'+'a'*36+'"\n')
        self.assertTrue(self.plan()['findings'])
        with self.assertRaises(sync.SyncError):self.apply()
        self.assertEqual((self.repo/'app.py').read_text(),'VALUE=1\n')

    def test_syntax_error_is_blocked(self):
        (self.source/'app.py').write_text('def broken(:')
        self.assertIn('python_syntax',{f['kind'] for f in self.plan()['findings']})

    def test_binary_asset_cannot_be_approved(self):
        (self.source/'weights.gguf').write_bytes(b'model');self.policy['managed'].append('weights.gguf');self.save_policy()
        self.assertIn('private_or_binary_asset',{f['kind'] for f in self.plan()['findings']})
        with self.assertRaises(sync.SyncError):self.apply()

    def test_oversized_source_is_rejected(self):
        with patch.object(sync,'MAX_FILE',10):
            with self.assertRaises(sync.SyncError):self.plan()

    def test_new_dependency_requires_declaration(self):
        (self.source/'app.py').write_text('import requests\n')
        self.assertEqual(self.plan()['dependencies']['undeclaredImports'],['requests'])
        with self.assertRaises(sync.SyncError):self.apply()

    def test_dependency_aliases_and_stdlib(self):
        (self.repo/'requirements.txt').write_text('Pillow>=12\n')
        (self.source/'app.py').write_text('import os\nfrom PIL import Image\n')
        self.assertFalse(self.plan()['dependencies']['undeclaredImports'])

    def test_review_acknowledgements_are_required(self):
        (self.source/'app.py').write_text('VALUE=2\n')
        with self.assertRaises(sync.SyncError):self.apply(reviews={'content':True,'dependencies':False,'licenses':True})

    def test_stale_source_plan_is_rejected(self):
        (self.source/'app.py').write_text('VALUE=2\n');plan=self.plan();(self.source/'app.py').write_text('VALUE=3\n')
        with self.assertRaises(sync.SyncError):self.apply(plan)

    def test_stale_repository_document_is_rejected(self):
        (self.source/'app.py').write_text('VALUE=2\n');plan=self.plan();(self.repo/'README.md').write_text('A newer approved presentation')
        with self.assertRaises(sync.SyncError):self.apply(plan)

    def test_parent_escape_and_git_targets_rejected(self):
        for rel in ('../outside.py','.git/config','C:/private.py','app.py/../helper.py','folder\\app.py','bad\nname.py','NUL.py','app.py.'):
            with self.subTest(rel=rel),self.assertRaises(sync.SyncError):sync.safe_path(self.repo,rel)

    def test_case_collision_policy_rejected(self):
        self.policy['managed'].append('APP.py');self.save_policy()
        with self.assertRaises(sync.SyncError):self.plan()

    def test_symlink_is_rejected(self):
        (self.source/'helper.py').unlink()
        try:(self.source/'helper.py').symlink_to(self.source/'app.py')
        except OSError:self.skipTest('Platform denies symlink creation')
        with self.assertRaises(sync.SyncError):self.plan()

    def test_partial_error_rolls_back(self):
        (self.source/'app.py').write_text('VALUE=2\n');(self.source/'helper.py').write_text('HELPER=2\n')
        real=sync.atomic_write;raised=[]
        def fail(path,data):
            if path==self.repo/'helper.py' and not raised:raised.append(True);raise OSError('injected disk write error')
            return real(path,data)
        with patch.object(sync,'atomic_write',side_effect=fail),self.assertRaises(OSError):self.apply()
        self.assertEqual((self.repo/'app.py').read_text(),'VALUE=1\n');self.assertFalse((self.repo/sync.LOCK).exists())
        self.assertEqual(sync.git_fingerprint(self.repo,self.git),self.git_before)

    def interrupt(self):
        (self.source/'app.py').write_text('VALUE=2\n');(self.source/'helper.py').write_text('HELPER=2\n')
        real=sync.atomic_write
        def crash(path,data):
            if path==self.repo/'helper.py':raise SystemExit('simulated process exit')
            return real(path,data)
        with patch.object(sync,'atomic_write',side_effect=crash),self.assertRaises(SystemExit):self.apply()
        return Path(sync.load(self.repo/sync.LOCK)['journal'])

    def test_interrupted_operation_requires_recovery(self):
        journal=self.interrupt()
        with self.assertRaises(sync.SyncError):self.plan()
        result=sync.recover(self.repo,journal,self.git);self.assertEqual(result['status'],'rolled_back')
        self.assertEqual((self.repo/'app.py').read_text(),'VALUE=1\n');self.assertFalse((self.repo/sync.LOCK).exists())

    def test_recovery_never_overwrites_later_user_changes(self):
        journal=self.interrupt();(self.repo/'app.py').write_text('LATER_USER_EDIT=9\n')
        with self.assertRaises(sync.SyncError):sync.recover(self.repo,journal,self.git)
        self.assertEqual((self.repo/'app.py').read_text(),'LATER_USER_EDIT=9\n');self.assertTrue((self.repo/sync.LOCK).exists())

    def test_reports_cannot_be_inside_either_tree(self):
        for p in (self.repo/'reports',self.source/'reports'):
            with self.assertRaises(sync.SyncError):sync.validate_external(p,self.repo,self.source)

    def test_external_boundaries_canonicalize_relative_roots(self):
        # Independent reproduction of mismatched canonical namespaces; does
        # not require Windows 8.3 names to be enabled on the developer disk.
        # CI checkout and Windows TEMP may be on different drives; create a
        # relative namespace on the fixture's own drive and restore cwd safely.
        with chdir(self.root):
            relative_repo=Path('repo');relative_source=Path('source')
            for target in (self.repo/'reports',self.source/'reports'):
                with self.assertRaises(sync.SyncError):sync.validate_external(target,relative_repo,relative_source)
            self.assertEqual(sync.validate_external(self.reports,relative_repo,relative_source),self.reports.resolve())

    def test_precommit_audits_index_even_when_working_file_clean(self):
        (self.repo/'app.py').write_text('TOKEN="'+'ghp_'+'a'*36+'"\n')
        subprocess.run([self.git,'-C',str(self.repo),'add','app.py'],check=True)
        (self.repo/'app.py').write_text('VALUE=1\n')
        before=sync.git_fingerprint(self.repo,self.git)
        review=sync.precommit(self.repo,self.git)
        self.assertIn('github_token',{f['kind'] for f in review['findings']})
        self.assertEqual(sync.git_fingerprint(self.repo,self.git),before)

    def test_approval_digest_cannot_be_substituted(self):
        (self.source/'app.py').write_text('VALUE=2\n');plan=self.plan();path=self.reports/'plan.json';path.write_text(json.dumps(plan))
        with self.assertRaises(sync.SyncError):sync.apply_plan(self.repo,self.source,self.git,path,'0'*64,{'content':True,'dependencies':True,'licenses':True},self.reports)

    def test_ignore_rules_cannot_silently_hide_new_source(self):
        (self.repo/'.gitignore').write_text((self.repo/'.gitignore').read_text()+'new.py\n')
        (self.source/'new.py').write_text('NEW=1\n');self.policy['managed'].append('new.py');self.save_policy()
        self.assertIn('approved_path_ignored_by_git',{f['kind'] for f in self.plan()['findings']})

    def test_manifest_is_stable_across_git_line_endings(self):
        self.assertEqual(sync.manifest_hash('app.py',b'VALUE=1\r\n'),sync.manifest_hash('app.py',b'VALUE=1\n'))
        self.assertNotEqual(sync.manifest_hash('picture.png',b'\r\n'),sync.manifest_hash('picture.png',b'\n'))

    def test_precommit_detects_missing_indexed_manifest(self):
        subprocess.run([self.git,'-C',str(self.repo),'add','app.py'],check=True)
        self.assertFalse(sync.precommit(self.repo,self.git)['indexedManifestMatches'])

    def test_precommit_validates_complete_index(self):
        (self.repo/sync.MANIFEST).write_bytes(sync.manifest_data(self.repo,self.git))
        subprocess.run([self.git,'-C',str(self.repo),'add','.'],check=True)
        review=sync.precommit(self.repo,self.git)
        self.assertTrue(review['manifestMatches']);self.assertTrue(review['indexedManifestMatches'])
        self.assertFalse(review['findings']);self.assertFalse(review['indexedMissingRequired'])

    def test_concurrent_document_edit_is_preserved_and_apply_rolls_back(self):
        (self.source/'app.py').write_text('VALUE=2\n');real=sync.atomic_write;changed=[]
        def edit(path,data):
            real(path,data)
            if path==self.repo/sync.STATE and not changed:
                changed.append(True);(self.repo/'README.md').write_text('New simultaneous user note')
        with patch.object(sync,'atomic_write',side_effect=edit),self.assertRaises(sync.SyncError):self.apply()
        self.assertEqual((self.repo/'README.md').read_text(),'New simultaneous user note')
        self.assertEqual((self.repo/'app.py').read_text(),'VALUE=1\n')

    @unittest.skipUnless(os.name=='nt','Windows junction regression')
    def test_real_windows_junction_is_rejected(self):
        target=self.root/'outside-assets';target.mkdir();link=self.source/'frontend-source'
        result=subprocess.run(['cmd.exe','/c','mklink','/J',str(link),str(target)],capture_output=True)
        if result.returncode:self.skipTest('Windows junction creation unavailable')
        self.assertTrue(sync.linked(link))
        with self.assertRaises(sync.SyncError):self.plan()

    @unittest.skipUnless(os.name=='nt','Windows root junction regression')
    def test_root_junction_is_rejected_before_resolving(self):
        link=self.root/'source-alias'
        result=subprocess.run(['cmd.exe','/c','mklink','/J',str(link),str(self.source)],capture_output=True)
        if result.returncode:self.skipTest('Windows junction creation unavailable')
        with self.assertRaises(sync.SyncError):sync.build_plan(self.repo,link,self.git)

    def test_discovery_includes_frontend_config_evals_and_new_harnesses(self):
        for rel in ('frontend-source/new.config.js','evals/datasets/new.jsonl','tests/qt_new_check.py','evals/reports/private.json'):
            p=self.source/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('{}\n')
        found=sync.source_candidates(self.source)
        self.assertTrue({'frontend-source/new.config.js','evals/datasets/new.jsonl','tests/qt_new_check.py'}<=found)
        self.assertNotIn('evals/reports/private.json',found)

    def test_manual_document_manifest_refresh_is_audited(self):
        (self.repo/'README.md').write_text('Reviewed new documentation\n')
        result=sync.refresh_manifest(self.repo,self.git);self.assertTrue(result['gitUntouched'])
        self.assertEqual((self.repo/sync.MANIFEST).read_bytes(),sync.manifest_data(self.repo,self.git))
        (self.repo/'README.md').write_text('ghp_'+'a'*36)
        with self.assertRaises(sync.SyncError):sync.refresh_manifest(self.repo,self.git)

    def test_test_fixtures_are_discovered_and_test_imports_are_scoped(self):
        (self.source/'tests').mkdir();(self.repo/'tests').mkdir()
        (self.source/'tests/cases.json').write_text('{"synthetic":true}')
        self.assertIn('tests/cases.json',sync.source_candidates(self.source))
        proposed={'tests/check.py':b'from helper_test import check\n','tests/helper_test.py':b'def check(): pass\n'}
        self.assertEqual(sync.dependencies(self.repo,proposed)['undeclaredImports'],[])
        proposed['app.py']=b'import helper_test\n'
        self.assertIn('helper_test',sync.dependencies(self.repo,proposed)['undeclaredImports'])

    def test_removed_test_helper_does_not_hide_missing_dependency(self):
        (self.repo/'tests').mkdir();(self.repo/'tests/helper_test.py').write_text('VALUE=1')
        result=sync.dependencies(self.repo,{'tests/check.py':b'import helper_test\n','tests/helper_test.py':None})
        self.assertIn('helper_test',result['undeclaredImports'])

if __name__=='__main__':unittest.main()
