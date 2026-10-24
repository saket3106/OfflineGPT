"""Explicit build-only inputs remain covered without inventing compiled-module matches."""
import copy
import hashlib
import importlib.util
from pathlib import Path
import unittest
ROOT=Path(__file__).resolve().parents[1]
def load(name):
 s=importlib.util.spec_from_file_location('role_test_'+name,ROOT/'scripts'/(name+'.py'));m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
i=load('candidate_inputs');b=load('candidate_builder')
class ProofTests(unittest.TestCase):
 def setUp(self):
  self.sources={'app.py':b'from model_runtime import Model\n','model_runtime.py':b'class Model: pass\n','agent_packaging.py':b'"""Build collector."""\nimport importlib.metadata\nfrom pathlib import Path\nclass PackagingError(RuntimeError): pass\ndef collect_agent_inputs(path, *, collectors=None): return [], [], []\n','Offline GPT.spec':b'from agent_packaging import collect_agent_inputs\ndata,binaries,hidden=collect_agent_inputs("requirements-agent.lock")\n'}
  self.refresh()
 def refresh(self):self.files={n:hashlib.sha256(i.normalized_bytes(n,data)).hexdigest() for n,data in self.sources.items()}
 def proof(self):return i.build_only_proof('agent_packaging.py',self.sources,self.files)
 def test_exact_helper_and_spec_have_a_typed_noncompiled_proof(self):self.assertEqual(self.proof(),i.build_only_descriptor('agent_packaging.py',self.files))
 def test_direct_dynamic_literal_and_hidden_consumers_block_exclusion(self):
  for data in (b'import agent_packaging\n',b'from agent_packaging import collect_agent_inputs\n',b'__import__("agent_packaging")\n'):
   self.sources['app.py']=data;self.refresh()
   with self.subTest(data=data):self.assertIsNone(self.proof())
  self.sources['app.py']=b'VALUE=1\n';self.sources['Offline GPT.spec']+=b'hiddenimports=["agent_packaging"]\n';self.refresh();self.assertIsNone(self.proof())
 def test_spec_import_and_actual_collector_call_are_both_required(self):
  for data in (b'VALUE=1\n',b'from agent_packaging import collect_agent_inputs\n',b'from different import collect_agent_inputs\na=collect_agent_inputs("lock")\n'):
   self.sources['Offline GPT.spec']=data;self.refresh()
   with self.subTest(data=data):self.assertIsNone(self.proof())
 def test_hash_mismatch_and_missing_source_never_form_a_proof(self):
  self.files['agent_packaging.py']='0'*64;self.assertIsNone(self.proof());self.refresh();self.files['Offline GPT.spec']='0'*64;self.assertIsNone(self.proof());self.refresh();self.sources.pop('agent_packaging.py');self.assertIsNone(self.proof())
 def test_top_level_execution_external_imports_and_unknown_modules_are_not_exempt(self):
  for tail in (b'collect_agent_inputs("lock")\n',b'import external_package\n',b'VALUE=1\n'):
   original=self.sources['agent_packaging.py'];self.sources['agent_packaging.py']+=tail;self.refresh();self.assertIsNone(self.proof());self.sources['agent_packaging.py']=original
  self.refresh();self.assertIsNone(i.build_only_proof('model_runtime.py',self.sources,self.files));self.assertIsNone(i.build_only_descriptor('model_runtime.py',self.files))
 def test_unparseable_runtime_source_fails_conservatively(self):self.sources['app.py']=b'def ???';self.refresh();self.assertIsNone(self.proof())
class CoverageTests(unittest.TestCase):
 def setUp(self):
  self.files={'app.py':'a'*64,'agent_packaging.py':'b'*64,'Offline GPT.spec':'c'*64};self.job={'graph':{'files':self.files,'applicationInputSha256':'d'*64},'sourceCommit':'e'*40};self.exe='f'*64
  self.smoke={'status':'PASSED','remainingOwnedPids':[],'modelRequestsIssued':0,'exeSha256':self.exe,'checks':dict.fromkeys(b.SMOKE_CHECKS,True)}
  self.verification={'sourceCommit':self.job['sourceCommit'],'candidateSha256':self.exe,'applicationInputSha256':'d'*64,'compiledModuleMatches':{'app.py':True},'buildOnlyInputMatches':{'agent_packaging.py':i.build_only_descriptor('agent_packaging.py',self.files)}}
 def check(self):return b.acceptance(self.smoke,self.verification,self.job,self.exe)
 def test_every_required_id_is_accounted_for_with_distinct_evidence(self):self.check();self.assertNotIn('agent_packaging.py',self.verification['compiledModuleMatches'])
 def test_missing_ordinary_runtime_output_never_counts_as_success(self):self.verification['compiledModuleMatches']={};self.assertRaises(b.Error,self.check)
 def test_unknown_forged_overlapping_or_malformed_roles_are_rejected(self):
  original=copy.deepcopy(self.verification)
  for value in ({'model_runtime.py':self.verification['buildOnlyInputMatches']['agent_packaging.py']},{'agent_packaging.py':{'contract':'unknown'}},[]):
   self.verification=copy.deepcopy(original);self.verification['buildOnlyInputMatches']=value;self.assertRaises(b.Error,self.check)
  self.verification=original;self.verification['compiledModuleMatches']['agent_packaging.py']=True;self.assertRaises(b.Error,self.check)
 def test_legacy_full_compiled_coverage_remains_valid(self):self.verification.pop('buildOnlyInputMatches');self.verification['compiledModuleMatches']['agent_packaging.py']=True;self.check()
if __name__=='__main__':unittest.main()
