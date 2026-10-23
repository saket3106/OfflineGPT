"""Independent immutable Git batch-input checks; no application/native model import."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('batch_candidate_inputs',Path(__file__).resolve().parents[1]/'scripts/candidate_inputs.py')
candidate=importlib.util.module_from_spec(spec);spec.loader.exec_module(candidate)
GIT=shutil.which('git')

def framed(data):
    oid=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
    return oid,oid.encode()+b' blob '+str(len(data)).encode()+b'\n'+data+b'\n'

class BatchProtocolTests(unittest.TestCase):
    def invoke(self,oids,raw,code=0):
        with patch.object(candidate.subprocess,'run',return_value=subprocess.CompletedProcess([],code,raw,b'')):
            return candidate.batch_blobs(Path('unused'),GIT,oids)
    def test_empty_duplicate_binary_blobs_and_empty_input(self):
        data=b'\x00\r\n\xffbinary';oid,raw=framed(data);empty,empty_raw=framed(b'')
        self.assertEqual(self.invoke([oid,empty,oid],raw+empty_raw+raw),[data,b'',data])
        with patch.object(candidate.subprocess,'run',side_effect=AssertionError('No process required')):
            self.assertEqual(candidate.batch_blobs(Path('unused'),GIT,[]),[])
    def test_malformed_truncated_wrong_type_identity_and_trailing_output_fail_closed(self):
        oid,raw=framed(b'abc')
        bad=[b'',raw[:-1],raw[:-3],raw.replace(b' blob ',b' tree '),raw.replace(b' 3\n',b' -1\n'),raw.replace(b'abc',b'xyz'),raw+b'garbage',b'0'*40+raw[40:]]
        for response in bad:
            with self.subTest(response=response),self.assertRaises(candidate.CandidateError):self.invoke([oid],response)
        with self.assertRaises(candidate.CandidateError):self.invoke([oid],raw,1)
        with self.assertRaises(candidate.CandidateError):self.invoke(['HEAD:app.py'],raw)
    def test_timeout_is_not_retried_or_hidden(self):
        oid,_=framed(b'abc')
        with patch.object(candidate.subprocess,'run',side_effect=subprocess.TimeoutExpired('git',30)) as run:
            with self.assertRaises(subprocess.TimeoutExpired):candidate.batch_blobs(Path('unused'),GIT,[oid])
            self.assertEqual(run.call_count,1)

@unittest.skipUnless(GIT,'Git is required for immutable-source integration tests')
class GitBatchTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.repo=Path(temp.name).resolve()
        self.g('init','-q');self.g('config','user.name','Synthetic tests');self.g('config','user.email','synthetic@example.invalid');self.g('config','core.autocrlf','false')
        self.names=['app.py','Offline GPT.spec','assistant_system_prompt.md','frontend-source/package.json','frontend-source/pnpm-lock.yaml','frontend-source/vite.config.js','frontend-source/pixel.png']
        for name in self.names:
            path=self.repo/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'\x00\r\n\xffimage' if name.endswith('.png') else b'Identical\r\ntext\n')
        path=self.repo/'scripts/source_sync_policy.json';path.parent.mkdir();path.write_text(json.dumps({'managed':self.names}),encoding='utf-8')
        self.commit=self.save()
    def g(self,*args,input=None):
        result=subprocess.run([GIT,'--no-replace-objects','-C',str(self.repo),*args],input=input,capture_output=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'));return result.stdout
    def save(self):
        self.g('add','--all');self.g('commit','-qm','Synthetic immutable build fixture');return self.g('rev-parse','HEAD').decode().strip()
    def test_equivalence_to_independent_per_blob_reference_and_exactly_three_processes(self):
        expected={}
        for name in self.names:
            data=self.g('show',self.commit+':'+name)
            if not name.endswith('.png'):data=data.replace(b'\r\n',b'\n')
            expected[name]=hashlib.sha256(data).hexdigest()
        original=candidate.subprocess.run
        with patch.object(candidate.subprocess,'run',wraps=original) as run:graph=candidate.inputs(self.repo,self.commit,GIT)
        self.assertEqual(graph['files'],expected);self.assertEqual(graph['applicationInputSha256'],candidate.digest(expected));self.assertEqual(run.call_count,3)
        self.assertTrue(all('--no-replace-objects' in call.args[0] for call in run.call_args_list))
        (self.repo/'app.py').write_bytes(b'Uncommitted human edit')
        self.assertEqual(candidate.inputs(self.repo,self.commit,GIT),graph)
    def test_lock_changes_always_change_the_application_input_identity(self):
        name='requirements-agent-windows-py312.lock';path=self.repo/name;path.write_bytes(b'example==1 --hash=sha256:abc\r\n')
        first=self.save();one=candidate.inputs(self.repo,first,GIT)
        self.assertEqual(one['files'][name],hashlib.sha256(b'example==1 --hash=sha256:abc\n').hexdigest())
        path.write_text('example==2 --hash=sha256:def\n',encoding='utf-8');two=candidate.inputs(self.repo,self.save(),GIT)
        self.assertNotEqual(one['applicationInputSha256'],two['applicationInputSha256'])
    def test_replace_refs_cannot_change_an_accepted_blob_identity(self):
        original=self.g('rev-parse',self.commit+':app.py').decode().strip();replacement=self.g('hash-object','-w','--stdin',input=b'Replacement').decode().strip()
        self.g('replace',original,replacement)
        graph=candidate.inputs(self.repo,self.commit,GIT)
        self.assertEqual(graph['files']['app.py'],hashlib.sha256(b'Identical\ntext\n').hexdigest())
    def test_symlink_and_submodule_modes_are_rejected_without_following_them(self):
        oid=self.g('rev-parse',self.commit+':app.py').decode().strip()
        for mode,object_id in [('120000',oid),('160000',self.commit)]:
            self.g('update-index','--cacheinfo',mode+','+object_id+',app.py');self.g('commit','-qm','Synthetic unsafe mode')
            with self.subTest(mode=mode),self.assertRaises(candidate.CandidateError):candidate.inputs(self.repo,self.g('rev-parse','HEAD').decode().strip(),GIT)
    def test_malformed_tree_and_case_collision_are_explicit_errors(self):
        actual=candidate.git
        def corrupted(repo,executable,*args):
            return b'bad tree\0' if args[0]=='ls-tree' else actual(repo,executable,*args)
        with patch.object(candidate,'git',side_effect=corrupted),self.assertRaises(candidate.CandidateError):candidate.inputs(self.repo,self.commit,GIT)
        policy=self.repo/'scripts/source_sync_policy.json';policy.write_text(json.dumps({'managed':self.names+['frontend-source/PIXEL.png']}),encoding='utf-8')
        with self.assertRaisesRegex(candidate.CandidateError,'Colliding'):candidate.inputs(self.repo,self.save(),GIT)
    def test_missing_graph_unsafe_paths_and_short_commit_remain_rejected(self):
        with self.assertRaises(candidate.CandidateError):candidate.inputs(self.repo,self.commit[:7],GIT)
        (self.repo/'app.py').unlink()
        with self.assertRaises(candidate.CandidateError):candidate.inputs(self.repo,self.save(),GIT)
        for name in ['../app.py','C:/app.py','frontend-source/a:stream','frontend-source/CON.png','frontend-source/a\\b.png']:
            policy=self.repo/'scripts/source_sync_policy.json';policy.write_text(json.dumps({'managed':self.names+[name]}),encoding='utf-8')
            with self.subTest(name=name),self.assertRaises(candidate.CandidateError):candidate.inputs(self.repo,self.save(),GIT)

if __name__=='__main__':unittest.main()
