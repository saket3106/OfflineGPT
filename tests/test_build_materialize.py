import importlib.util,json,tempfile,unittest
from pathlib import Path
spec=importlib.util.spec_from_file_location('closed_materializer',Path(__file__).resolve().parents[1]/'scripts/build_materialize.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class MaterializeTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name).resolve()
        self.source=self.root/'source';self.repo=self.root/'repo';self.source.mkdir();self.repo.mkdir()
        self.roots={k:self.root/k for k in ('pythonBase','pythonEnvironment','frontendDependencies')}
        for p in self.roots.values():p.mkdir()
        (self.roots['frontendDependencies']/'package').mkdir();(self.roots['frontendDependencies']/'package/a.js').write_text('safe synthetic module')
        (self.roots['pythonBase']/'python.exe').write_bytes(b'not executable')
        self.node=self.root/'node.exe';self.node.write_bytes(b'not executable')
        self.receipt=self.root/'environment.json';self.dest=self.root/'private/dependencies';self.files={'frontendDependencies/package/a.js':m.inputs.sha(self.roots['frontendDependencies']/'package/a.js'),'pythonBase/python.exe':m.inputs.sha(self.roots['pythonBase']/'python.exe')}
        self.links={};self.save()
    def save(self):
        v={'version':1,'roots':{k:str(v) for k,v in self.roots.items()},'source':str(self.source),'repository':str(self.repo),'files':self.files,'internalLinks':self.links,'executables':{'node':str(self.node)},'executableHashes':{'node':m.inputs.sha(self.node)}}
        v['environmentSha256']=m.inputs.digest(v);self.receipt.write_text(json.dumps(v));self.sha=m.inputs.sha(self.receipt)
    def run_copy(self,**kwargs):return m.materialize(self.receipt,self.sha,self.dest,**kwargs)
    def test_copy_exact_bytes_without_modifying_originals(self):
        result=self.run_copy();self.assertEqual(result['files'],2);self.assertEqual((self.dest/'frontendDependencies/package/a.js').read_text(),'safe synthetic module')
        with self.assertRaises(m.Error):self.run_copy()
    def test_changed_input_and_unreviewed_pth_fail_closed(self):
        (self.roots['frontendDependencies']/'package/a.js').write_text('changed')
        with self.assertRaises(m.Error):self.run_copy()
    def test_executable_pth_is_never_run_and_requires_exact_review(self):
        file=self.roots['pythonEnvironment']/'danger.pth';file.write_text('import os; raise RuntimeError("must never execute")')
        self.files['pythonEnvironment/danger.pth']=m.inputs.sha(file);self.save()
        with self.assertRaisesRegex(m.Error,'pth'):self.run_copy()
    def test_escaping_alias_and_case_collision_are_blocked(self):
        self.links={'frontendDependencies/alias':'../outside'};self.save()
        with self.assertRaises(m.Error):self.run_copy()
    def test_internal_directory_alias_stays_inside_private_tree(self):
        self.links={'frontendDependencies/alias':'package'};self.save();self.run_copy()
        self.assertEqual((self.dest/'frontendDependencies/alias/a.js').read_text(),'safe synthetic module')
        self.assertTrue((self.dest/'frontendDependencies/alias').resolve().is_relative_to(self.dest))
    def test_real_case_collision_is_rejected(self):
        self.files['frontendDependencies/PACKAGE/a.js']=self.files['frontendDependencies/package/a.js'];self.save()
        with self.assertRaisesRegex(m.Error,'colliding'):self.run_copy()
    @unittest.skipUnless(__import__('os').name=='nt','Windows executable path boundary')
    def test_long_executable_launch_path_is_rejected_before_execution(self):
        self.dest=self.root/('a'*120)/('b'*120)/'dependencies'
        with self.assertRaisesRegex(m.Error,'launch path'):self.run_copy()
if __name__=='__main__':unittest.main()
