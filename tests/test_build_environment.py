"""Synthetic files only: build locks must detect real dependency changes."""
import importlib.util
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS=Path(__file__).resolve().parents[1]/'scripts'
spec=importlib.util.spec_from_file_location('candidate_inputs',SCRIPTS/'candidate_inputs.py');inputs=importlib.util.module_from_spec(spec);spec.loader.exec_module(inputs)
with patch.dict(sys.modules,{'candidate_inputs':inputs}):
    spec=importlib.util.spec_from_file_location('build_environment_test',SCRIPTS/'build_environment.py');environment=importlib.util.module_from_spec(spec);spec.loader.exec_module(environment)
class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name).resolve();self.source=self.root/'source';self.repo=self.root/'repo'
        self.roots={'pythonEnvironment':self.root/'env','pythonBase':self.root/'base','frontendDependencies':self.source/'frontend-source/node_modules'}
        for p in [self.repo,*self.roots.values()]:p.mkdir(parents=True)
        (self.roots['pythonEnvironment']/'Scripts').mkdir();self.python=self.roots['pythonEnvironment']/'Scripts/python.exe';self.python.write_bytes(b'Synthetic interpreter; never executed')
        (self.roots['pythonEnvironment']/'pyvenv.cfg').write_text('home = '+str(self.roots['pythonBase'])+'\ninclude-system-site-packages = false\n')
        self.node=self.root/'node.exe';self.node.write_bytes(b'Synthetic node; never executed')
        self.dependency=self.roots['frontendDependencies']/'dependency.js';self.dependency.write_text('export const value=1;')
        self.exes={'python':self.python,'node':self.node};self.output=self.root/'private/environment.json'
    def capture(self):return environment.capture(self.roots,self.exes,self.source,self.repo,self.output)
    def test_inventory_file_set_and_hashes_match_independent_source_bytes(self):
        nested=self.roots['frontendDependencies']/'nested';nested.mkdir();(nested/'detail.js').write_bytes(b'Exact dependency bytes')
        expected={}
        for role,root in self.roots.items():
            for path in root.rglob('*'):
                if path.is_file():expected[role+'/'+path.relative_to(root).as_posix()]=hashlib.sha256(path.read_bytes()).hexdigest()
        value=self.capture()
        self.assertEqual(value['files'],expected)
        self.assertEqual(value['fileCount'],len(expected))
        self.assertEqual(value['executableHashes'],{name:hashlib.sha256(path.read_bytes()).hexdigest() for name,path in self.exes.items()})
    def test_capture_and_exact_verification_are_read_only(self):
        before=self.dependency.read_bytes();value=self.capture();h=hashlib.sha256(self.output.read_bytes()).hexdigest()
        self.assertEqual(value['environmentSha256'],environment.verify(self.output,h)['environmentSha256']);self.assertEqual(self.dependency.read_bytes(),before)
    def test_changed_added_and_removed_dependencies_invalidate_lock(self):
        self.capture();h=hashlib.sha256(self.output.read_bytes()).hexdigest();original=self.dependency.read_bytes()
        self.dependency.write_text('Changed')
        with self.assertRaises(environment.EnvironmentError):environment.verify(self.output,h)
        self.dependency.write_bytes(original);extra=self.dependency.with_name('unreviewed.js');extra.write_text('New')
        with self.assertRaises(environment.EnvironmentError):environment.verify(self.output,h)
        extra.unlink();self.dependency.unlink()
        with self.assertRaises(environment.EnvironmentError):environment.verify(self.output,h)
    def test_transient_bytecode_does_not_invalidate_environment(self):
        value=self.capture();cache=self.roots['pythonEnvironment']/'__pycache__';cache.mkdir();(cache/'test.pyc').write_bytes(b'Transient')
        self.assertEqual(value['environmentSha256'],environment.inventory(self.roots,self.exes,self.source,self.repo)['environmentSha256'])
    def test_private_receipts_never_enter_source_or_repository(self):
        for target in (self.source/'receipt.json',self.repo/'receipt.json'):
            with self.assertRaises(environment.EnvironmentError):environment.capture(self.roots,self.exes,self.source,self.repo,target)
            self.assertFalse(target.exists())
    def test_wrong_roles_weights_and_budget_fail_before_execution(self):
        with self.assertRaises(environment.EnvironmentError):environment.inventory({},self.exes,self.source,self.repo)
        weight=self.roots['pythonBase']/'model.gguf';weight.write_bytes(b'Synthetic - not loaded')
        with self.assertRaises(environment.EnvironmentError):self.capture()
        weight.unlink()
        with patch.object(environment,'MAX_FILES',0),self.assertRaises(environment.EnvironmentError):self.capture()
    def test_existing_receipt_is_preserved(self):
        self.capture();before=self.output.read_bytes()
        with self.assertRaises(environment.EnvironmentError):self.capture()
        self.assertEqual(self.output.read_bytes(),before)
    def test_concurrent_new_dependency_aborts_capture(self):
        original=environment.stable_hash
        def racing(path,deadline):
            value=original(path,deadline)
            if path==self.dependency:self.dependency.with_name('racing.js').write_text('Unreviewed')
            return value
        with patch.object(environment,'stable_hash',side_effect=racing),self.assertRaises(environment.EnvironmentError):self.capture()
        self.assertFalse(self.output.exists())
    def test_unrelated_base_global_packages_are_excluded_only_for_isolated_venv(self):
        global_packages=self.roots['pythonBase']/'Lib/site-packages';global_packages.mkdir(parents=True);(global_packages/'unrelated.py').write_text('No import')
        value=self.capture();self.assertFalse(any('site-packages' in key for key in value['files']))
        (self.roots['pythonEnvironment']/'pyvenv.cfg').write_text('include-system-site-packages = true\n')
        with self.assertRaises(environment.EnvironmentError):environment.inventory(self.roots,self.exes,self.source,self.repo)
if __name__=='__main__':unittest.main()
