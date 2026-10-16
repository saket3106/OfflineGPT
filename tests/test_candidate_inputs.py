"""Independent build-input/payload negative tests; no Qt or native models."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import test_auto_sync as fixtures

def load(name):
    spec=importlib.util.spec_from_file_location('test_'+name,Path(__file__).resolve().parents[1]/'scripts'/(name+'.py'))
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
candidate=load('candidate_inputs');smoke=load('candidate_smoke')
class InputTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name).resolve()
        self.package=self.root/'package';self.package.mkdir();(self.package/'Offline GPT.exe').write_bytes(b'Synthetic EXE - never run')
        (self.package/'QtCore.dll').write_bytes(b'Synthetic runtime - never loaded')
        self.manifest=self.root/'payload.json';self.save({'Offline GPT.exe':candidate.sha(self.package/'Offline GPT.exe'),'QtCore.dll':candidate.sha(self.package/'QtCore.dll')})
    def save(self,value):self.manifest.write_text(json.dumps(value));self.approved=candidate.sha(self.manifest)
    def test_full_approved_payload_is_hashed(self):self.assertEqual(candidate.payload(self.package,self.manifest,self.approved)['payloadFiles'],2)
    def test_manifest_or_dependency_tampering_never_passes(self):
        (self.package/'QtCore.dll').write_bytes(b'Changed runtime')
        with self.assertRaises(candidate.CandidateError):candidate.payload(self.package,self.manifest,self.approved)
        self.manifest.write_text('{}')
        with self.assertRaises(candidate.CandidateError):candidate.payload(self.package,self.manifest,self.approved)
    def test_missing_dependencies_do_not_count_as_complete(self):
        (self.package/'QtCore.dll').unlink()
        with self.assertRaises(candidate.CandidateError):candidate.payload(self.package,self.manifest,self.approved)
    def test_traversal_absolute_drive_reserved_and_case_collisions_are_blocked(self):
        for value in ('../outside','C:/outside','/outside','\\\\host\\share','file:stream','CON.txt','folder/../outside','trailing.','bad\x00path'):
            with self.subTest(path=value),self.assertRaises(candidate.CandidateError):candidate.relative(value)
        values=json.loads(self.manifest.read_text());values['qtcore.DLL']=values['QtCore.dll'];self.save(values)
        with self.assertRaises(candidate.CandidateError):candidate.payload(self.package,self.manifest,self.approved)
    def test_compiled_comparison_ignores_only_source_filename(self):
        a=compile('def add(x,y):\n return x+y\n','first','exec');b=compile('def add(x,y):\n return x+y\n','second','exec')
        self.assertEqual(candidate.structure(a),candidate.structure(b))
        self.assertNotEqual(candidate.structure(a),candidate.structure(compile('def add(x,y):\n return x-y\n','first','exec')))
        self.assertNotEqual(candidate.structure(a),candidate.structure(a.replace(co_firstlineno=9)))
        with self.assertRaises(candidate.CandidateError):candidate.structure('not code')
    def test_smoke_rejects_existing_profiles_and_wrong_exe_before_importing_qt(self):
        (self.package/'ui').mkdir();(self.package/'ui/index.html').write_text('Synthetic')
        existing=self.root/'existing';existing.mkdir();(existing/'user-data').write_text('Preserve')
        for profile,expected in ((existing,candidate.sha(self.package/'Offline GPT.exe')),(self.root/'new','0'*64),(self.package/'profile',candidate.sha(self.package/'Offline GPT.exe'))):
            with self.assertRaises(smoke.SmokeError):smoke.preflight(self.package,profile,expected)
        self.assertEqual((existing/'user-data').read_text(),'Preserve')
    def test_smoke_requires_ui_and_fresh_disjoint_profile(self):
        with self.assertRaises(smoke.SmokeError):smoke.preflight(self.package,self.root/'new',candidate.sha(self.package/'Offline GPT.exe'))
        (self.package/'ui').mkdir();(self.package/'ui/index.html').write_text('Synthetic')
        package,profile=smoke.preflight(self.package,self.root/'new',candidate.sha(self.package/'Offline GPT.exe'))
        self.assertEqual(package,self.package);self.assertFalse(profile.exists())

class GitInputTests(unittest.TestCase):
    def setUp(self):
        fixture=fixtures.AutoSyncTests('runTest');fixture.setUp();self.addCleanup(fixture.doCleanups);self.f=fixture
        names=['app.py','Offline GPT.spec','assistant_system_prompt.md','frontend-source/package.json','frontend-source/pnpm-lock.yaml','frontend-source/vite.config.js','frontend-source/photo.jpg']
        for name in names:
            p=fixture.repo/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'Synthetic\r\ninput')
        with (fixture.repo/'.gitattributes').open('a') as stream:stream.write('\n*.jpg -text\n')
        p=fixture.repo/'scripts/source_sync_policy.json';value=json.loads(p.read_text());value['managed']=names;p.write_text(json.dumps(value))
        fixture.g('add','--all');fixture.g('commit','-qm','Synthetic build inputs');self.commit=fixture.g('rev-parse','HEAD').decode().strip()
    def test_input_identity_uses_immutable_git_blobs_and_preserves_binary_bytes(self):
        graph=candidate.inputs(self.f.repo,self.commit,self.f.f.git)
        (self.f.repo/'app.py').write_text('Concurrent human work preserved')
        self.assertEqual(graph,candidate.inputs(self.f.repo,self.commit,self.f.f.git))
        import hashlib
        self.assertEqual(graph['files']['frontend-source/photo.jpg'],hashlib.sha256(b'Synthetic\r\ninput').hexdigest())
        self.assertEqual(graph['files']['app.py'],hashlib.sha256(b'Synthetic\ninput').hexdigest())
    def test_short_commit_and_incomplete_graph_are_not_accepted(self):
        with self.assertRaises(candidate.CandidateError):candidate.inputs(self.f.repo,self.commit[:7],self.f.f.git)
        (self.f.repo/'assistant_system_prompt.md').unlink();self.f.g('add','--all');self.f.g('commit','-qm','Synthetic missing required input')
        missing=self.f.g('rev-parse','HEAD').decode().strip()
        with self.assertRaises(candidate.CandidateError):candidate.inputs(self.f.repo,missing,self.f.f.git)

if __name__=='__main__':unittest.main()
