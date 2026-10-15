"""Native fitter observability/privacy; synthetic token counters only."""
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from integrated_runtime import _LocalLlamaServer,APP_DIR
from conversation_memory import native_history_coverage


class PromptFitTests(unittest.TestCase):
    def runtime(self):
        runtime=_LocalLlamaServer(Path.cwd());runtime.context=2048
        return runtime

    def test_fitted_fingerprint_and_unshortened_metadata(self):
        runtime=self.runtime();messages=[{'role':'system','content':'Context {"id":"M7"}'},{'role':'user','content':'Hello.'}]
        with patch.object(runtime,'_prompt_tokens',return_value=44):fitted,count,output,shortened=runtime._fit_messages(messages,800,lambda _:None,None)
        fit=runtime.last_prompt_fit
        self.assertFalse(shortened);self.assertEqual(fit['prompt_tokens'],44)
        self.assertEqual(fit['source_json_ids_after'],['M7'])
        self.assertTrue(fit['source_ids_are_presence_only'])
        self.assertEqual(fit['fitted_messages_sha256'],hashlib.sha256(json.dumps(fitted,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest())
        self.assertNotIn('messages',fit);self.assertEqual(fit['transformations'],[])

    def test_dropped_turn_is_observed_without_mutating_input(self):
        runtime=self.runtime();messages=[{'role':'system','content':'Rules.'},{'role':'user','content':'Old.'},{'role':'assistant','content':'Old answer.'},{'role':'user','content':'Current.'}]
        original=json.loads(json.dumps(messages))
        with patch.object(runtime,'_prompt_tokens',side_effect=lambda x:3000 if len(x)>2 else 100):runtime._fit_messages(messages,800,lambda _:None,None)
        self.assertEqual(messages,original)
        self.assertEqual(runtime.last_prompt_fit['transformations'],[{'kind':'older_turns_dropped','message_count':2}])

    def test_system_trimming_records_real_source_id_loss(self):
        runtime=self.runtime();messages=[{'role':'system','content':'x'*700+'\n{"id":"M71","excerpt":"source"}\n'+'y'*700},{'role':'user','content':'Question.'}]
        with patch.object(runtime,'_prompt_tokens',side_effect=lambda x:len(x[0]['content'])+100):runtime._fit_messages(messages,800,lambda _:None,None)
        fit=runtime.last_prompt_fit
        self.assertTrue(fit['context_shortened']);self.assertEqual(fit['source_json_ids_before'],['M71'])
        self.assertEqual(fit['source_json_ids_after'],[])
        self.assertTrue(any(t['kind']=='system_shortened' for t in fit['transformations']))

    def test_failed_fit_does_not_leave_previous_fit_metadata(self):
        runtime=self.runtime();runtime.last_prompt_fit={'stale':True}
        with self.assertRaises(RuntimeError):runtime._fit_messages([],800,lambda _:None,None)
        self.assertEqual(runtime.last_prompt_fit,{})


class CapturePrivacyTests(unittest.TestCase):
    def test_raw_capture_disabled_by_default(self):
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{'OFFLINE_GPT_DATA_DIR':temp,'OFFLINE_GPT_CAPTURE_FITTED_PROMPTS':'0'}):
            runtime=_LocalLlamaServer(Path(temp));runtime.last_prompt_fit={}
            runtime._capture_fitted_prompt([{'role':'user','content':'private text'}])
            self.assertFalse((Path(temp)/'logs').exists())

    def test_enabled_capture_uses_only_external_profile(self):
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{'OFFLINE_GPT_DATA_DIR':temp,'OFFLINE_GPT_CAPTURE_FITTED_PROMPTS':'1'}):
            runtime=_LocalLlamaServer(Path(temp));runtime.last_prompt_fit={'context_shortened':True}
            runtime._capture_fitted_prompt([{'role':'user','content':'synthetic input'}])
            value=json.loads((Path(temp)/'logs/fitted-prompts.jsonl').read_text())
            self.assertEqual(value['messages'][0]['content'],'synthetic input')
            self.assertNotIn('api_key',value)
            with patch.dict(os.environ,{'OFFLINE_GPT_DATA_DIR':str(APP_DIR)}):
                with patch('pathlib.Path.open',side_effect=AssertionError('Never write raw traces in the application directory')):
                    runtime._capture_fitted_prompt([])

    def test_capture_has_entry_and_file_limits(self):
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{'OFFLINE_GPT_DATA_DIR':temp,'OFFLINE_GPT_CAPTURE_FITTED_PROMPTS':'1'}):
            runtime=_LocalLlamaServer(Path(temp));runtime.last_prompt_fit={}
            runtime._capture_fitted_prompt([{'role':'user','content':'x'*70000}])
            path=Path(temp)/'logs/fitted-prompts.jsonl';self.assertFalse(path.exists())
            path.write_bytes(b'x'*(2*1024**2))
            runtime._capture_fitted_prompt([]);self.assertEqual(path.stat().st_size,2*1024**2)

    def test_optional_capture_failure_does_not_stop_inference(self):
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{'OFFLINE_GPT_DATA_DIR':temp,'OFFLINE_GPT_CAPTURE_FITTED_PROMPTS':'1'}):
            runtime=_LocalLlamaServer(Path(temp));runtime.last_prompt_fit={}
            with patch('pathlib.Path.mkdir',side_effect=PermissionError):runtime._capture_fitted_prompt([])


class NativeCoverageTests(unittest.TestCase):
    def test_id_presence_cannot_certify_complete_quote_after_trim(self):
        original={'coverageComplete':True,'contextCoverageComplete':True,'contextSourceIds':['M7']}
        result=native_history_coverage(original,[{'prompt_fit':{'context_shortened':True,'source_json_ids_after':['M7']}}])
        self.assertFalse(result['coverageComplete']);self.assertFalse(result['contextCoverageComplete'])
        self.assertTrue(result['nativeContextShortened']);self.assertTrue(original['coverageComplete'])
        self.assertEqual(result['contextSourceIds'],['M7']);self.assertTrue(result['nativeSourceIdsArePresenceOnly'])

    def test_unshortened_fit_does_not_promote_partial_retrieval(self):
        result=native_history_coverage({'coverageComplete':False},[{'prompt_fit':{'context_shortened':False}}])
        self.assertFalse(result['coverageComplete']);self.assertFalse(result['nativeContextShortened'])

    def test_no_native_evidence_does_not_invent_fit_results(self):
        original={'coverageComplete':True}
        self.assertEqual(native_history_coverage(original,[{}]),original)
        self.assertEqual(native_history_coverage({},[{'prompt_fit':{'context_shortened':True}}]),{})

    def test_worker_persists_native_coverage_and_fit_metadata(self):
        from app import ChatWorker
        from intelligence import analyze_request
        prompt='What did we test previously?'
        class Engine:
            cfg={}
            runtime=SimpleNamespace(last_generation_diagnostics={})
            def ensure_chat(self,*args,**kwargs):return 'local'
            def chat_completion(self,*args,**kwargs):
                self.runtime.last_generation_diagnostics={'generated_token_count':12,'prompt_fit':{'context_shortened':True,'source_json_ids_after':['M7']}}
                return 'The user measured 11 seconds [M7].'
        analysis=analyze_request(prompt)
        analysis.update(memory_request=True,memory_coverage={'coverageComplete':True,'contextCoverageComplete':True},
                        memory_sources=[{'id':'M7','url':'offlinegpt://chat/test?message=7','date':'2025-01-02','evidenceKind':'USER_STATEMENT','excerpt':'I measured 11 seconds.','chatId':'test','created':1700000000}],max_retries=0)
        worker=ChatWorker('local',[{'role':'user','content':prompt}],Engine(),analysis)
        outputs=[];metrics=[];worker.done.connect(outputs.append);worker.metrics.connect(metrics.append);worker.run()
        self.assertTrue(outputs)
        self.assertFalse(metrics[-1]['memory_coverage']['coverageComplete'])
        self.assertTrue(metrics[-1]['prompt_fits'][0]['context_shortened'])


if __name__=='__main__':unittest.main()
