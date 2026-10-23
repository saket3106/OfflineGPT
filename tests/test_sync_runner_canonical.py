"""Hash-pinned frozen canonical boundary, real Git, no model or network use."""
import importlib.util
import json
import os
from pathlib import Path
import py_compile
import shutil
import subprocess
import sys
import unittest
import test_auto_sync as fixtures
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('canonical_frozen_runner',ROOT/'scripts/sync_runner.py');runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
auto=fixtures.auto

class FrozenCanonicalRunnerTests(unittest.TestCase):
    def setUp(self):
        f=fixtures.AutoSyncTests('runTest');f.setUp();self.addCleanup(f.doCleanups);self.f=f
        self.runtime=f.private/'runtime'/('a'*40);(self.runtime/'scripts').mkdir(parents=True)
        for name in runner.SCRIPTS:shutil.copy2(ROOT/'scripts'/name,self.runtime/'scripts'/name)
        self.contract={'version':1,'epoch':1,'root':str(f.repo.resolve()),'repositoryConfigSha256':auto.sync.digest((f.repo/'.git/config').read_bytes()),'ancestorCommit':f.g('rev-parse','HEAD').decode().strip(),'branch':f.cfg['branch'],'remoteUrl':f.cfg['remoteUrl'],'policySha256':auto.sync.file_hash(f.repo,auto.sync.POLICY),'productionRoot':str((f.repo.parent/'protected-production').resolve())}
        self.cfg={**f.cfg,'version':3,'source':str(f.repo.resolve()),'layoutContract':self.contract};f.config.write_text(json.dumps(self.cfg),encoding='utf-8')
        self.control=f.private/'control.json';self.control.write_text(json.dumps({'mode':'paused','layoutEpoch':1,'layoutContractSha256':auto.sync.digest(auto.sync.canonical(self.contract))}),encoding='utf-8')
        self.manifest={'version':1,'commit':'a'*40,'privateRoot':str(f.private.resolve()),'runtimeRoot':str(self.runtime),'repository':str(f.repo.resolve()),'source':str(f.repo.resolve()),'configPath':str(f.config),'configSha256':runner.sha(f.config),'pythonPath':str(Path(sys.executable).resolve()),'pythonSha256':runner.sha(Path(sys.executable)),'scripts':{name:runner.sha(self.runtime/'scripts'/name) for name in runner.SCRIPTS}}
        self.path=f.private/'canonical-runtime.json';self.save()
    def save(self):self.path.write_text(json.dumps(self.manifest),encoding='utf-8')
    def config_changed(self):
        self.f.config.write_text(json.dumps(self.cfg),encoding='utf-8');self.manifest['configSha256']=runner.sha(self.f.config);self.save()
    def test_exact_canonical_config_and_epoch_validate_without_starting_controller(self):
        manifest,paths=runner.validate(self.path);self.assertEqual(paths['repository'],paths['source']);self.assertEqual(manifest['commit'],'a'*40)
        self.assertFalse((self.f.private/'last-run.json').exists());self.assertEqual(self.f.manager.head(),self.contract['ancestorCommit'])
    def test_legacy_overlap_nested_root_and_missing_contract_remain_rejected(self):
        self.cfg['version']=2;self.cfg.pop('layoutContract');self.config_changed()
        with self.assertRaisesRegex(runner.RunnerError,'overlap'):runner.validate(self.path)
        self.cfg.update(version=3,layoutContract=self.contract,source=str(self.f.repo/'nested'));(self.f.repo/'nested').mkdir();self.manifest['source']=self.cfg['source'];self.config_changed()
        with self.assertRaisesRegex(runner.RunnerError,'overlap'):runner.validate(self.path)
    def test_missing_boolean_and_stale_adoption_receipts_fail(self):
        for value in [{},{'layoutEpoch':True,'layoutContractSha256':auto.sync.digest(auto.sync.canonical(self.contract))},{'layoutEpoch':2,'layoutContractSha256':'0'*64}]:
            self.control.write_text(json.dumps(value),encoding='utf-8')
            with self.subTest(value=value),self.assertRaisesRegex(runner.RunnerError,'adoption receipt'):runner.validate(self.path)
    def test_canonical_validator_cannot_be_replaced_by_adjacent_timestamp_cache(self):
        helper=self.runtime/'scripts/sync_source.py';good=helper.read_bytes();stamp=helper.stat();fake=b"raise RuntimeError('POISONED_CANONICAL_CACHE')\n";self.assertGreater(len(good),len(fake));helper.write_bytes(fake+b' '*(len(good)-len(fake)));os.utime(helper,ns=(stamp.st_atime_ns,stamp.st_mtime_ns))
        cache=helper.parent/'__pycache__'/(helper.stem+'.'+sys.implementation.cache_tag+'.pyc');py_compile.compile(str(helper),cfile=str(cache),doraise=True)
        helper.write_bytes(good);os.utime(helper,ns=(stamp.st_atime_ns,stamp.st_mtime_ns))
        probe="import importlib.util; p="+repr(str(helper))+";s=importlib.util.spec_from_file_location('probe',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)"
        result=subprocess.run([sys.executable,'-I','-B','-c',probe],capture_output=True,timeout=15)
        self.assertNotEqual(result.returncode,0);self.assertIn(b'POISONED_CANONICAL_CACHE',result.stderr)
        runner.validate(self.path)

if __name__=='__main__':unittest.main()
