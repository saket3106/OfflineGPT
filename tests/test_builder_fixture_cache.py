"""Independent real-Git evidence for pristine builder setup reuse, never build mocks."""
from pathlib import Path
import json
import unittest
import controller_fixture_cache as cache
import test_candidate_builder as domain
class BuilderFixtureTests(unittest.TestCase):
    def setUp(self):cache.clear()
    def make(self):
        case=domain.BuilderTests('runTest');self.addCleanup(case.doCleanups);case.setUp();return case
    def test_real_git_index_objects_and_remote_remain_independent(self):
        first=self.make();second=self.make();head=second.commit
        self.assertNotEqual(first.f.repo,second.f.repo)
        for args in [('rev-parse','HEAD'),('ls-files','--stage','-z'),('status','--porcelain')]:self.assertEqual(first.f.g(*args),second.f.g(*args))
        second.f.g('fsck','--full')
        first.change('app.py','VALUE=73\n')
        self.assertEqual(second.f.manager.head(),head);self.assertEqual(second.f.manager.remote_head(),head)
        self.assertEqual((second.f.repo/'app.py').read_text(encoding='utf-8'),'VALUE=1\n')
        for path in second.f.f.root.rglob('*'):
            if path.is_file():self.assertEqual(path.stat().st_nlink,1)
    def test_private_profiles_jobs_and_candidate_pointers_are_not_cached(self):
        first=self.make();stats=cache.statistics();second=self.make()
        self.assertEqual(cache.statistics()['captured'],stats['captured'])
        self.assertEqual(cache.statistics()['restored'],stats['restored']+1)
        self.assertNotEqual(first.builder.profileHash,second.builder.profileHash)
        for case in (first,second):
            pointer=json.loads(case.builder.pointer.read_text(encoding='utf-8'))
            self.assertTrue(Path(pointer['package']).is_relative_to(case.private.resolve()))
            self.assertEqual(list((case.private/'jobs').iterdir()),[])
        for image in cache._images.values():
            self.assertFalse(any(name.startswith('build-private/') for name,_,_ in image[0]))
    def test_real_snapshot_preserves_raw_lock_bytes_and_shared_normalized_identity(self):
        import hashlib
        import subprocess
        import tempfile
        from unittest.mock import patch
        case=self.make();repo=case.f.repo;lock='requirements-agent-windows-py312.lock'
        with (repo/'.gitattributes').open('a',encoding='utf-8') as f:f.write('\n*.lock -text\n')
        raw=b'example==1 --hash=sha256:synthetic\r\n';(repo/lock).write_bytes(raw)
        case.f.g('add','.gitattributes',lock);case.f.g('commit','-qm','Synthetic raw dependency lock')
        commit=case.f.g('rev-parse','HEAD').decode().strip();graph=domain.b.inputs.inputs(repo,commit,case.f.f.git)
        self.assertEqual(case.f.g('show',commit+':'+lock),raw)
        with tempfile.TemporaryDirectory() as tmp:
            real=domain.b.inputs.subprocess.run
            with patch.object(domain.b.inputs.subprocess,'run',wraps=real) as calls:
                source,controller=case.builder.source_snapshot({'sourceCommit':commit,'graph':graph},Path(tmp).resolve())
            self.assertEqual(calls.call_count,4)
            self.assertEqual((source/lock).read_bytes(),raw)
            self.assertEqual(graph['files'][lock],hashlib.sha256(raw.replace(b'\r\n',b'\n')).hexdigest())
            for name,h in case.builder.adapters.items():self.assertEqual(hashlib.sha256((controller/name).read_bytes()).hexdigest(),h)
    def test_each_owned_fixture_cleanup_preserves_its_sibling(self):
        first=self.make();second=self.make();root=first.f.f.root
        first.doCleanups();self.assertFalse(root.exists());self.assertTrue(second.f.repo.exists())
        self.assertEqual(second.f.manager.remote_head(),second.commit)
if __name__=='__main__':unittest.main()
