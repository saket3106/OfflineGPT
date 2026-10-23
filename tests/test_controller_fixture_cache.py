"""Independent cache isolation checks using real temporary Git/bare remotes."""
import importlib.util
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch
import test_auto_sync as fixtures
import test_source_sync as source_fixtures
import controller_fixture_cache as cache

class FixtureCacheTests(unittest.TestCase):
    def setUp(self):
        cache.clear();self.original=fixtures.AutoSyncTests('runTest')
        # The dedicated original initializer always remains independently callable.
        setup=getattr(fixtures.AutoSyncTests,'uncached_setUp',fixtures.AutoSyncTests.setUp)
        setup(self.original);self.addCleanup(self.original.doCleanups)
        cache.remember(self.original,fixtures.auto,source_fixtures)
    def make(self):
        case=fixtures.AutoSyncTests('runTest');self.addCleanup(case.doCleanups)
        self.assertTrue(cache.restore(case,fixtures.auto,source_fixtures));return case
    def test_restored_git_objects_index_and_clean_status_match_original(self):
        second=self.make()
        self.assertNotEqual(second.f.root,self.original.f.root)
        for args in [('rev-parse','HEAD'),('ls-files','--stage','-z'),('status','--porcelain')]:self.assertEqual(self.original.g(*args),second.g(*args))
        second.g('fsck','--full');self.assertEqual(second.g('remote','get-url','origin').decode().strip(),str(second.remote))
        self.assertEqual(second.manager.remote_head(),second.manager.head())
        for path in second.f.root.rglob('*'):
            if path.is_file():self.assertEqual(path.stat().st_nlink,1)
    def test_working_index_commit_remote_and_metadata_mutations_are_isolated(self):
        second=self.make();third=self.make();head=third.manager.head()
        (second.repo/'app.py').write_text('VALUE=100\n',encoding='utf-8');second.g('add','app.py');second.g('commit','-qm','Independent synthetic mutation');second.g('push','-q','origin','HEAD:refs/heads/codex/test')
        (second.source/'helper.py').write_text('HELPER=9\n',encoding='utf-8');second.cfg['source']='changed';second.f.policy['managed'].append('synthetic.py')
        self.assertEqual(third.manager.head(),head);self.assertEqual(third.manager.remote_head(),head);self.assertEqual(self.original.manager.head(),head)
        self.assertEqual((third.source/'helper.py').read_text(encoding='utf-8'),'HELPER=1\n');self.assertNotIn('synthetic.py',third.f.policy['managed'])
    def test_corrupt_memory_image_never_materializes_a_fixture(self):
        key=cache._key(fixtures.auto,source_fixtures);entry=cache._images[key];files=list(entry[0]);name,data,mode=files[0];files[0]=(name,data+b'corrupt',mode);cache._images[key]=(tuple(files),*entry[1:])
        case=fixtures.AutoSyncTests('runTest');self.addCleanup(case.doCleanups)
        with patch.object(cache.tempfile,'TemporaryDirectory',side_effect=AssertionError('No partial fixture')):
            with self.assertRaisesRegex(RuntimeError,'integrity'):cache.restore(case,fixtures.auto,source_fixtures)
    def test_git_environment_changes_do_not_reuse_incompatible_state(self):
        with patch.dict(os.environ,{'GIT_CONFIG_NOSYSTEM':'cache-key-change'}):self.assertFalse(cache.restore(fixtures.AutoSyncTests('runTest'),fixtures.auto,source_fixtures))
        with patch.dict(os.environ,{'GIT_INDEX_FILE':str(self.original.f.root/'unapproved-index')}):
            with self.assertRaisesRegex(RuntimeError,'isolated Git'):cache.restore(fixtures.AutoSyncTests('runTest'),fixtures.auto,source_fixtures)
    def test_mocked_clock_and_subprocess_initialization_are_never_reused(self):
        with patch.object(cache.subprocess,'run'):
            self.assertFalse(cache.restore(fixtures.AutoSyncTests('runTest'),fixtures.auto,source_fixtures))
        with patch.object(cache.time,'time'):
            self.assertFalse(cache.restore(fixtures.AutoSyncTests('runTest'),fixtures.auto,source_fixtures))
    def test_unrecognized_absolute_reference_is_rejected(self):
        cache.clear();(self.original.repo/'unexpected.txt').write_text(str(self.original.f.root),encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError,'Unrecognized absolute'):cache.remember(self.original,fixtures.auto,source_fixtures)
        self.assertEqual(cache.statistics()['images'],0)
    def test_shared_file_links_are_rejected_and_cleanup_remains_owned(self):
        cache.clear();os.link(self.original.repo/'app.py',self.original.repo/'linked.py')
        with self.assertRaisesRegex(RuntimeError,'Non-ordinary'):cache.remember(self.original,fixtures.auto,source_fixtures)
        self.assertEqual(cache.statistics()['images'],0)
    def test_cached_fixture_cleanup_removes_only_its_own_directory(self):
        second=self.make();root=second.f.root;second.doCleanups();self.assertFalse(root.exists());self.assertTrue(self.original.repo.exists())
    def test_fixture_size_limit_prevents_unbounded_process_memory_cache(self):
        cache.clear();(self.original.repo/'synthetic-large.dat').write_bytes(b'x'*(2*1024**2+1))
        with self.assertRaisesRegex(RuntimeError,'bounded cache'):cache.remember(self.original,fixtures.auto,source_fixtures)
        self.assertEqual(cache.statistics()['images'],0)

if __name__=='__main__':unittest.main()
