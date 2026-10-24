"""Real immutable controller/source bytes and negative inputs, no native AI workloads."""
import hashlib
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch
import test_candidate_batch_inputs as fixtures
candidate=fixtures.candidate
class SelectionTests(unittest.TestCase):
    setUp=fixtures.GitBatchTests.setUp
    g=fixtures.GitBatchTests.g
    save=fixtures.GitBatchTests.save
    def test_raw_byte_equivalence_uses_only_two_real_git_commands(self):
        expected={name:self.g('show',self.commit+':'+name) for name in self.names}
        real=candidate.subprocess.run
        with patch.object(candidate.subprocess,'run',wraps=real) as calls:actual=candidate.immutable_files(self.repo,self.commit,fixtures.GIT,self.names)
        self.assertEqual(actual,expected);self.assertEqual(calls.call_count,2)
        self.assertTrue(all('--no-replace-objects' in call.args[0] for call in calls.call_args_list))
        self.assertIn(b'\r\n',actual['app.py'])
    def test_selection_is_bounded_canonical_unique_and_exact(self):
        for names in (['../app.py'],['app.py','APP.py'],['app.py','app.py'],['C:/app.py'],['frontend-source\\pixel.png'],['missing.py'],['app.py']*513,'app.py'):
            with self.subTest(names=str(names)[:70]),self.assertRaises(candidate.CandidateError):candidate.immutable_files(self.repo,self.commit,fixtures.GIT,names)
        with self.assertRaises(candidate.CandidateError):candidate.immutable_files(self.repo,self.commit[:7],fixtures.GIT,['app.py'])
        with patch.object(candidate.subprocess,'run',side_effect=AssertionError('Empty selection starts no process')):self.assertEqual(candidate.immutable_files(self.repo,self.commit,fixtures.GIT,[]),{})
    def test_link_and_gitlink_modes_are_never_materialized_as_code(self):
        oid=self.g('rev-parse',self.commit+':app.py').decode().strip()
        for mode,identity in [('120000',oid),('160000',self.commit)]:
            self.g('update-index','--cacheinfo',mode+','+identity+',app.py');self.g('commit','-qm','Synthetic unsafe source mode')
            with self.subTest(mode=mode),self.assertRaises(candidate.CandidateError):candidate.immutable_files(self.repo,self.g('rev-parse','HEAD').decode().strip(),fixtures.GIT,['app.py'])
    def test_replacements_and_uncommitted_work_cannot_change_accepted_bytes(self):
        expected=self.g('show',self.commit+':app.py');oid=self.g('rev-parse',self.commit+':app.py').decode().strip();replacement=self.g('hash-object','-w','--stdin',input=b'Replacement').decode().strip()
        self.g('replace',oid,replacement);(self.repo/'app.py').write_bytes(b'Human working edit')
        self.assertEqual(candidate.immutable_files(self.repo,self.commit,fixtures.GIT,['app.py']),{'app.py':expected})
    def test_lock_and_asset_normalization_have_distinct_exact_rules(self):
        raw=b'Exact\r\nbytes\x00'
        for name in ('requirements-agent-windows-py312.lock','module.py','frontend-source/index.css'):
            self.assertEqual(candidate.normalized_bytes(name,raw),b'Exact\nbytes\x00')
        for name in ('image.png','engine.dll','asset.bin'):
            self.assertEqual(candidate.normalized_bytes(name,raw),raw)
if __name__=='__main__':unittest.main()
