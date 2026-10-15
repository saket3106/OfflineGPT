"""Recovery is per logical model call; shared workflow budgets remain global."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from app import ChatWorker
from intelligence import analyze_request
from integrated_runtime import EmptyVisibleResponseError


class ReviewPhaseIsolationTests(unittest.TestCase):
    def run_worker(self,mode):
        prompt='Design a local database recovery policy after interruption.'
        class Engine:
            cfg={}
            def __init__(self):self.requests=[];self.runtime=SimpleNamespace(last_generation_diagnostics={})
            def ensure_chat(self,*args,**kwargs):return 'local'
            def chat_completion(self,messages,**kwargs):
                self.requests.append((messages,kwargs))
                self.runtime.last_generation_diagnostics={'generated_token_count':35,'finish_reason':'stop'}
                if len(self.requests)==1:
                    if mode=='empty':raise EmptyVisibleResponseError('authored empty-answer fixture')
                    if mode=='truncated':
                        self.runtime.last_generation_diagnostics['finish_reason']='length'
                        return 'Use a transaction and then'
                return 'Handle failures explicitly and validate recovery evidence. Power-loss guarantees depend on the filesystem.'
        engine=Engine();analysis=analyze_request(prompt)
        analysis.update(selective_review=True,reasoning_effort='high',max_retries=0)
        worker=ChatWorker('local',[{'role':'system','content':'Answer directly.'},{'role':'user','content':prompt}],engine,analysis)
        done=[];failed=[];metrics=[];worker.done.connect(done.append);worker.failed.connect(failed.append);worker.metrics.connect(metrics.append);worker.run()
        self.assertFalse(failed);self.assertTrue(done)
        return engine.requests,metrics[-1]

    def test_empty_recovery_does_not_downgrade_reviewer(self):
        requests,metrics=self.run_worker('empty')
        self.assertEqual([kwargs['reasoning_effort'] for _,kwargs in requests],['high','low','medium'])
        self.assertIn('RUNTIME FINAL-ANSWER STAGE',requests[1][0][0]['content'])
        self.assertNotIn('RUNTIME FINAL-ANSWER STAGE',requests[2][0][0]['content'])
        self.assertIn('Review a proposed answer',requests[2][0][0]['content'])
        self.assertEqual(metrics['model_calls'],3);self.assertEqual(metrics['selective_review_calls'],1)

    def test_truncated_recovery_does_not_downgrade_reviewer(self):
        requests,_=self.run_worker('truncated')
        self.assertEqual([kwargs['reasoning_effort'] for _,kwargs in requests],['high','low','medium'])
        self.assertNotIn('previous draft hit the native output limit',requests[2][0][0]['content'])

    def test_normal_reviewer_does_not_add_unnecessary_recovery(self):
        requests,_=self.run_worker('normal')
        self.assertEqual([kwargs['reasoning_effort'] for _,kwargs in requests],['high','medium'])

    def test_shared_tool_stop_still_applies_to_later_calls(self):
        from tool_workflow import ToolWorkflowBudget
        def stopped(*args,**kwargs):
            budget=ToolWorkflowBudget(*args,**kwargs);budget.final_reason='tool_call_budget';return budget
        with patch('tool_workflow.ToolWorkflowBudget',side_effect=stopped):requests,_=self.run_worker('normal')
        self.assertEqual([kwargs['reasoning_effort'] for _,kwargs in requests],['low','medium'])
        for messages,kwargs in requests:
            self.assertEqual(kwargs['allowed_tools'],[])
            self.assertIn('tool_call_budget',messages[0]['content'])

    def test_cancelled_request_never_starts_reviewer(self):
        prompt='Design a database recovery protocol.'
        class Engine:
            cfg={}
            def __init__(self):self.calls=0
            def ensure_chat(self,*args,**kwargs):return 'local'
            def chat_completion(self,*args,**kwargs):self.calls+=1;return 'Draft.'
        engine=Engine();analysis=analyze_request(prompt);analysis['selective_review']=True
        worker=ChatWorker('local',[{'role':'user','content':prompt}],engine,analysis)
        worker.cancel_event.set();worker.run();self.assertEqual(engine.calls,0)


if __name__=='__main__':unittest.main()
