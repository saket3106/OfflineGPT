"""Explicit production protection under a moved canonical repository profile."""
import importlib.util
import json
from pathlib import Path
import unittest
import test_candidate_builder as fixtures
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('canonical_builder_boundary',ROOT/'scripts/candidate_builder.py');b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)

class CanonicalBuildBoundaryTests(unittest.TestCase):
    def setUp(self):
        f=fixtures.BuilderTests('runTest');f.setUp();self.addCleanup(f.doCleanups);self.f=f
        source=f.f;cfg=dict(source.cfg);self.production=source.repo.parent/'protected-production';self.production.mkdir();(self.production/'user-data.txt').write_text('Preserve synthetic user data',encoding='utf-8')
        contract={'version':1,'epoch':1,'root':str(source.repo.resolve()),'repositoryConfigSha256':b.auto.sync.digest((source.repo/'.git/config').read_bytes()),'ancestorCommit':source.g('rev-parse','HEAD').decode().strip(),'branch':cfg['branch'],'remoteUrl':cfg['remoteUrl'],'policySha256':b.auto.sync.file_hash(source.repo,b.auto.sync.POLICY),'productionRoot':str(self.production.resolve())}
        cfg.update(version=3,source=str(source.repo.resolve()),layoutContract=contract);source.config.write_text(json.dumps(cfg),encoding='utf-8')
        control=source.private/'control.json';v=json.loads(control.read_text(encoding='utf-8'));v.update(layoutEpoch=1,layoutContractSha256=b.auto.sync.digest(b.auto.sync.canonical(contract)));control.write_text(json.dumps(v),encoding='utf-8')
        self.before=(self.production/'user-data.txt').read_bytes()
    def test_declared_production_overlap_fails_before_any_candidate_write(self):
        cfg={**self.f.cfg,'privateRoot':str(self.production)};self.f.config.write_text(json.dumps(cfg),encoding='utf-8')
        with self.assertRaisesRegex(b.Error,'production application'):b.Builder(self.f.config,fixture=True)
        self.assertEqual((self.production/'user-data.txt').read_bytes(),self.before);self.assertEqual({p.name for p in self.production.iterdir()},{'user-data.txt'})
    def test_canonical_profile_can_validate_separate_private_candidate_inputs(self):
        cfg=dict(self.f.cfg);receipt=Path(cfg['environmentReceipt']);env=json.loads(receipt.read_text(encoding='utf-8'));env['source']=str(self.f.f.repo.resolve());env['environmentSha256']=b.inputs.digest({k:v for k,v in env.items() if k!='environmentSha256'});receipt.write_text(json.dumps(env),encoding='utf-8');cfg['environmentReceiptSha256']=b.inputs.sha(receipt);self.f.config.write_text(json.dumps(cfg),encoding='utf-8')
        builder=b.Builder(self.f.config,fixture=True);self.assertTrue(builder.manager.canonical_mode);self.assertEqual(builder.root,self.f.private);self.assertEqual((self.production/'user-data.txt').read_bytes(),self.before)

if __name__=='__main__':unittest.main()
