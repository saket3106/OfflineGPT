import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from intelligence import analyze_request
from integrated_runtime import ToolCallCompletion
from tool_workflow import ToolWorkflowBudget, equivalent_key, synthesis_messages


def call(name='calculator', **arguments):
    return ToolCallCompletion([{'id': 'native-call', 'name': name, 'arguments': arguments}])


class ScriptedEngine:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.runtime = SimpleNamespace(last_generation_diagnostics={})

    def ensure_chat(self, *args, **kwargs):
        return 'local'

    def chat_completion(self, messages, **kwargs):
        self.requests.append((messages, kwargs))
        self.runtime.last_generation_diagnostics = {'generated_token_count': 30}
        return self.responses.pop(0) if self.responses else 'Final answer from existing evidence.'


class ToolWorkflowTests(unittest.TestCase):
    def test_bad_engineering_subtask_cannot_abort_arithmetic_sibling(self):
        bad='2. This protocol guarantees exactly-once execution.'
        prompt='1. Calculate 7 * 9.\n2. Design crash recovery for concurrent archive exports.'
        _,done,failed,metrics=self.run_worker(ScriptedEngine([bad,bad,bad]),(),prompt,retries=0)
        self.assertFalse(failed);self.assertTrue(done);self.assertIn('63',done[0])
        self.assertNotIn('guarantees exactly-once execution',done[0])
        self.assertIn("couldn't complete this task",done[0]);self.assertEqual(metrics[-1]['validation_status'],'partial')

    def test_unresolved_design_guarantee_is_not_returned_as_verified(self):
        bad='This protocol guarantees that each output is written exactly once.'
        _,done,failed,metrics=self.run_worker(ScriptedEngine([bad,bad]),(),'Design crash recovery for concurrent exports.',retries=1)
        self.assertFalse(failed);self.assertTrue(done)
        self.assertNotIn(bad,done[0]);self.assertIn('Checks still unresolved',done[0])
        self.assertEqual(metrics[-1]['validation_status'],'partial')

    def test_one_design_review_preserves_tool_budget_and_uses_medium_effort(self):
        from app import ChatWorker
        prompt='Design crash recovery for a concurrent SQLite archive exporter.'
        analysis=analyze_request(prompt);analysis.update(selective_review=True,max_retries=0,agent_tools=[])
        engine=ScriptedEngine(['Draft with proposed state transitions.','A conditional design; proposed tests have not been executed.'])
        worker=ChatWorker('local',[{'role':'user','content':prompt}],engine,analysis);done=[];worker.done.connect(done.append);worker.run()
        self.assertEqual(len(engine.requests),2);self.assertTrue(done)
        self.assertEqual(engine.requests[-1][1]['reasoning_effort'],'medium')
        self.assertEqual(engine.requests[-1][1]['allowed_tools'],[])
        self.assertIn('two competing workers',engine.requests[-1][0][0]['content'])

    def run_worker(self, engine, tools=('calculator',), prompt='Verify a computation with a local tool.', retries=0):
        from app import ChatWorker
        analysis=analyze_request(prompt)
        analysis.update(agent_tools=list(tools),max_retries=retries,reasoning_effort='high')
        worker=ChatWorker('local',[{'role':'user','content':prompt}],engine,analysis)
        done,failed,metrics=[],[],[]
        worker.done.connect(done.append);worker.failed.connect(failed.append);worker.metrics.connect(metrics.append)
        worker.run()
        return worker,done,failed,metrics

    def test_exact_four_calls_transition_to_tool_free_final(self):
        engine=ScriptedEngine([call(expression=f'{i}+7') for i in range(4)]+['The calculations completed.'])
        _,done,failed,metrics=self.run_worker(engine)
        self.assertFalse(failed);self.assertEqual(done,['The calculations completed.'])
        self.assertEqual(engine.requests[-1][1]['allowed_tools'],[])
        self.assertEqual(engine.requests[-1][1]['reasoning_effort'],'low')
        w=metrics[-1]['tool_workflow'];self.assertEqual(w['tool_calls'],4);self.assertEqual(w['tool_rounds'],4)
        self.assertEqual(w['finalization_reason'],'tool_round_budget')
        self.assertEqual(sum(m['role']=='tool' for m in engine.requests[-1][0]),4)

    def test_duplicate_search_runs_once_and_preserves_result(self):
        engine=ScriptedEngine([call('memory_search',query='Sampling LATENCY?'),call('memory_search',query='latency sampling'), 'Measured result [M3].'])
        with patch('tool_registry.ToolRegistry.execute_tool_call',return_value='{"sources":[{"id":"M3","excerpt":"176.3 seconds"}]}') as execute:
            _,done,failed,metrics=self.run_worker(engine,('memory_search',))
        self.assertFalse(failed);self.assertEqual(execute.call_count,1)
        self.assertIn('M3',done[0]);self.assertEqual(metrics[-1]['tool_workflow']['duplicate_requests'],1)
        self.assertEqual(engine.requests[-1][1]['allowed_tools'],[])

    def test_failed_equivalent_tool_does_not_repeat_execution(self):
        engine=ScriptedEngine([call(expression='9/0'),call(expression='9/0'),'The calculation failed because division by zero is undefined.'])
        with patch('tool_registry.ToolRegistry.execute_tool_call',return_value='Error: division by zero') as execute:
            _,done,failed,metrics=self.run_worker(engine)
        self.assertFalse(failed);self.assertTrue(done);self.assertEqual(execute.call_count,1)
        self.assertEqual(metrics[-1]['tool_workflow']['trace'][0]['status'],'failed')

    def test_oversized_batch_executes_nothing_and_synthesizes(self):
        oversized=ToolCallCompletion([{'id':str(i),'name':'calculator','arguments':{'expression':f'{i}+7'}} for i in range(5)])
        engine=ScriptedEngine([oversized,'I cannot verify the requested calculations from the available results.'])
        with patch('tool_registry.ToolRegistry.execute_tool_call') as execute:
            _,done,failed,metrics=self.run_worker(engine)
        execute.assert_not_called();self.assertFalse(failed);self.assertTrue(done)
        self.assertEqual(metrics[-1]['tool_workflow']['finalization_reason'],'tool_batch_budget')

    def test_original_sixteen_call_ceiling_is_preserved(self):
        batches=[ToolCallCompletion([{'id':str(i),'name':'calculator','arguments':{'expression':f'{i}+7'}} for i in range(start,start+4)]) for start in range(0,16,4)]
        engine=ScriptedEngine(batches+['All sixteen tool results are available.'])
        _,done,failed,metrics=self.run_worker(engine)
        self.assertFalse(failed);self.assertTrue(done)
        self.assertEqual(metrics[-1]['tool_workflow']['tool_calls'],16)
        self.assertEqual(metrics[-1]['tool_workflow']['tool_rounds'],4)
        self.assertEqual(engine.requests[-1][1]['allowed_tools'],[])

    def test_native_calculator_supported_function_and_error_telemetry(self):
        from tool_registry import ToolRegistry
        registry=ToolRegistry()
        self.assertEqual(registry.execute_tool_call('calculator',{'expression':'sqrt(1369)'}),'Result: 37')
        self.assertTrue(registry.execute_tool_call('calculator',{'expression':'sqrt(-1)'}).startswith('Error:'))
        self.assertTrue(registry.execute_tool_call('calculator',{'expression':'__import__("os")'}).startswith('Error:'))

    def test_fifth_requested_tool_never_executes_even_if_model_ignores_tools_none(self):
        engine=ScriptedEngine([call(expression=f'{i}+7') for i in range(6)]+['Available evidence is incomplete.'])
        with patch('tool_registry.ToolRegistry.execute_tool_call',return_value='Result: 7') as execute:
            _,done,failed,_=self.run_worker(engine)
        self.assertEqual(execute.call_count,4)
        self.assertTrue(failed);self.assertFalse(done)
        self.assertIn('Diagnostic: tools-',failed[0])

    def test_tool_free_empty_recovery_keeps_original_and_evidence(self):
        engine=ScriptedEngine([call(expression='17*23'),'','The result is 391.'])
        _,done,failed,_=self.run_worker(engine)
        self.assertFalse(failed);self.assertEqual(done,['The result is 391.'])
        self.assertEqual(engine.requests[-1][1]['allowed_tools'],[])
        final=engine.requests[-1][0]
        self.assertTrue(any(m['role']=='tool' and '391' in m['content'] for m in final))
        self.assertEqual(sum(m['role']=='user' for m in final),1)

    def test_ordinary_question_does_not_advertise_tools(self):
        engine=ScriptedEngine(['A generator yields values lazily. It resumes execution when requested. This avoids storing all values.'])
        _,done,failed,metrics=self.run_worker(engine,(), 'Explain what a Python generator is in three sentences.')
        self.assertFalse(failed);self.assertTrue(done);self.assertEqual(len(engine.requests),1)
        self.assertEqual(engine.requests[0][1]['allowed_tools'],[])
        self.assertEqual(metrics[-1]['tool_workflow']['tool_calls'],0)

    def test_budget_survives_validator_retry(self):
        engine=ScriptedEngine([call(expression=f'{i}+7') for i in range(4)]+['Too short.','This answer has exactly five.'])
        _,done,failed,metrics=self.run_worker(engine,prompt="Rewrite 'Hello world' in exactly 5 words.",retries=1)
        self.assertFalse(failed);self.assertEqual(done,['This answer has exactly five.'])
        self.assertEqual(metrics[-1]['tool_workflow']['tool_calls'],4)
        self.assertTrue(all(not kwargs['allowed_tools'] for _,kwargs in engine.requests[4:]))

    def test_cancellation_between_batch_tools(self):
        from app import ChatWorker
        engine=ScriptedEngine([ToolCallCompletion([{'id':str(i),'name':'calculator','arguments':{'expression':f'{i}+7'}} for i in range(2)])])
        analysis=analyze_request('Verify with a local tool.');analysis.update(agent_tools=['calculator'],max_retries=0)
        worker=ChatWorker('local',[{'role':'user','content':'Verify with a local tool.'}],engine,analysis)
        cancelled=[];worker.cancelled.connect(cancelled.append)
        def execute(*args):worker.cancel_event.set();return 'Result: 7'
        with patch('tool_registry.ToolRegistry.execute_tool_call',side_effect=execute) as mocked:worker.run()
        self.assertEqual(mocked.call_count,1);self.assertEqual(len(cancelled),1)

    def test_budget_reserves_time_and_tokens_and_does_not_reset(self):
        now=[100.0];budget=ToolWorkflowBudget(800,clock=lambda:now[0]);now[0]+=136
        self.assertEqual(budget.stop_reason(),'time_reserved_for_answer')
        self.assertEqual(budget.seconds_left,44)
        budget.finalize(budget.stop_reason());budget.calls=2;budget.rounds=2
        budget.record_generation({'generated_token_count':2600})
        self.assertEqual(budget.tokens_left,600);self.assertEqual(budget.calls,2)

    def test_exact_document_searches_not_conflated(self):
        self.assertNotEqual(equivalent_key('document_search',{'query':'Test A','exact':True}),equivalent_key('document_search',{'query':'test a','exact':True}))
        self.assertNotEqual(equivalent_key('memory_search',{'query':'latency','timeline':True}),equivalent_key('memory_search',{'query':'latency','timeline':False}))

    def test_final_instruction_does_not_create_a_new_user_turn(self):
        original=[{'role':'user','content':'Original task'},{'role':'tool','content':'Actual evidence'}]
        result=synthesis_messages(original,'tool_call_budget')
        self.assertEqual(result[1:],original);self.assertEqual(original[0]['role'],'user')

    def test_entire_history_request_is_timeline(self):
        from conversation_memory import memory_intent
        request='Summarize our entire OfflineGPT development history from the earliest available conversations. Include changes, tests, improvements and unresolved issues. Use actual sources.'
        self.assertTrue(memory_intent(request)['timeline'])

    def test_anaphoric_measurements_followup_keeps_subject(self):
        from conversation_memory import memory_intent
        value=memory_intent('Which of those improvements actually worked according to our measurements?', [{'role':'user','content':'Summarize our earlier image-generation optimizations.'}])
        self.assertIn('image-generation',value['query'])

    def test_deadline_does_not_set_user_cancellation(self):
        from tool_workflow import WorkflowCancellation
        now=[0.0];event=threading.Event();budget=ToolWorkflowBudget(clock=lambda:now[0]);signal=WorkflowCancellation(event,budget)
        self.assertFalse(signal.is_set());now[0]=181;self.assertTrue(signal.is_set());self.assertFalse(event.is_set())

    def test_budgeted_timeline_does_not_reopen_memory_tool(self):
        from app import ChatWorker
        analysis=analyze_request('Summarize our entire earlier project history.')
        analysis.update(memory_request=True,memory_context_sufficient=False,memory_retrieval_closed=True,agent_tools=['memory_search'],max_retries=0)
        engine=ScriptedEngine(['This is a partial account from available records.'])
        worker=ChatWorker('local',[{'role':'user','content':analysis['prompt']}],engine,analysis)
        worker.run();self.assertEqual(engine.requests[0][1]['allowed_tools'],[])

    def test_failed_history_provenance_returns_real_sources_not_fiction(self):
        from app import ChatWorker
        prompt='What did we test earlier?';analysis=analyze_request(prompt)
        source={'id':'M3','url':'offlinegpt://chat/test?message=3','date':'2026-10-08','evidenceKind':'USER_STATEMENT','excerpt':'I tested sampling: 176.3 seconds.','chatId':'test','created':1}
        analysis.update(memory_request=True,memory_sources=[source],max_retries=0)
        worker=ChatWorker('local',[{'role':'user','content':prompt}],ScriptedEngine(['2024-02-15: We built an old prototype.']),analysis)
        done=[];metrics=[];worker.done.connect(done.append);worker.metrics.connect(metrics.append);worker.run()
        self.assertTrue(done);self.assertIn('176.3',done[0]);self.assertNotIn('2024-02-15',done[0]);self.assertIn('[M3]',done[0]);self.assertEqual(metrics[-1]['validation_status'],'partial')

    def test_empty_synthesis_still_returns_available_history_evidence(self):
        from app import ChatWorker
        prompt='What did we test earlier?';analysis=analyze_request(prompt)
        source={'id':'M3','url':'offlinegpt://chat/test?message=3','date':'2026-10-08','evidenceKind':'USER_STATEMENT','excerpt':'I tested sampling: 176.3 seconds.','chatId':'test','created':1}
        analysis.update(memory_request=True,memory_sources=[source],max_retries=0)
        worker=ChatWorker('local',[{'role':'user','content':prompt}],ScriptedEngine(['','']),analysis)
        done=[];failed=[];worker.done.connect(done.append);worker.failed.connect(failed.append);worker.run()
        self.assertFalse(failed);self.assertIn('176.3',done[0]);self.assertIn('Diagnostic:',done[0])

    def test_calculator_claims_and_rounding_follow_actual_runtime(self):
        from tool_workflow import calculator_answer_failures
        self.assertTrue(calculator_answer_failures('Using the calculator, the answer is 27.9%.',[],required=True))
        actual=[{'tool':'calculator','status':'succeeded','calculatorExpression':'(9-7)/9*100','calculatorResult':'Result: 22.2222222222'}]
        self.assertFalse(calculator_answer_failures('The improvement is 22.2%.',actual,True))
        self.assertTrue(calculator_answer_failures('The improvement is 23.9%.',actual,True))
        self.assertFalse(calculator_answer_failures('The improvement is 22.22\\%.',actual,True))

    def test_stream_quarantines_self_instruction_before_recovery(self):
        from integrated_runtime import _VisibleChatStream,visible_assistant_text
        raw="We need to recall transaction semantics. Let's produce a final answer. The answer follows."
        stream=_VisibleChatStream();visible=''
        for char in raw:visible+=stream.feed(char)
        visible+=stream.finish();self.assertEqual(visible,'');self.assertEqual(visible_assistant_text(raw),'')
        normal=_VisibleChatStream();self.assertEqual(normal.feed('Hello!'),'Hello!')
        literal=_VisibleChatStream();self.assertEqual(literal.feed('We need to recall'),'');self.assertEqual(literal.finish(),'We need to recall')
        leading=_VisibleChatStream();visible=''
        for char in '\n\n'+raw:visible+=leading.feed(char)
        self.assertEqual(visible+leading.finish(),'')

    def test_history_calculation_intent_keeps_calculator_available(self):
        from router import get_relevant_tools
        self.assertIn('calculator',get_relevant_tools({'route_target':'DIRECT','category':'summarization'},'Find our previous timings, calculate the percentage improvement, and explain whether settings were comparable.'))
    def test_calculator_evidence_survives_a_later_failure_without_inventing_units(self):
        from tool_workflow import calculator_evidence
        text=calculator_evidence('Partial history.',[{'tool':'calculator','status':'succeeded','calculatorExpression':'250*100','calculatorResult':'Result: 25000'}])
        self.assertIn('25000',text);self.assertNotIn('25000%',text)

    def test_provenance_accepts_actual_iso_timestamp_date(self):
        from conversation_memory import history_provenance_failures
        sources=[{'id':'M3','date':'2026-10-08T09:30:00+05:30','evidenceKind':'USER_STATEMENT','excerpt':'I measured the run.'}]
        self.assertEqual(history_provenance_failures('2026-10-08: the user measured a run [M3].',sources),[])
        self.assertTrue(history_provenance_failures('2024-02-15: the user measured a run [M3].',sources))

    def test_transaction_comment_is_not_failure_control_flow(self):
        from code_engine import transaction_guarantee_failures
        prompt='A SQLite transaction statement fails; can the earlier debit commit?'
        bad='The transaction is automatically rolled back.\n```sql\nBEGIN;\nUPDATE accounts SET value=1;\n-- automatically rolled back on failure\nCOMMIT;\n```'
        self.assertTrue(transaction_guarantee_failures(prompt,bad))
        self.assertFalse(transaction_guarantee_failures(prompt,'The transaction is not automatically rolled back; the caller must handle the error.'))
        self.assertFalse(transaction_guarantee_failures(prompt,'A transaction context manager rolls back automatically if an exception escapes.'))
        self.assertFalse(transaction_guarantee_failures(prompt,'Cleanup rolls back automatically in this handler:\n```python\ntry:\n    conn.execute(sql)\nexcept Exception:\n    conn.rollback()\n    raise\n```'))

    def test_guarantee_checks_are_scoped_to_multi_task_leaves(self):
        from code_engine import transaction_guarantee_failures
        prompt='Answer each task independently.\n1. Explain SQLite transactions.\n2. Write a Python function returning its input.'
        engine=ScriptedEngine(['1. A transaction groups changes; handle errors explicitly.\n\n2. ```python\ndef identity(value):\n    return value\n```'])
        with patch('code_engine.transaction_guarantee_failures',wraps=transaction_guarantee_failures) as check:
            _,done,failed,_=self.run_worker(engine,(),prompt)
        self.assertFalse(failed);self.assertTrue(done);self.assertIn('def identity',done[0])
        self.assertTrue(check.call_args_list)
        self.assertTrue(all(call.args[0]!=prompt for call in check.call_args_list))

    def test_visible_length_cutoff_gets_one_tool_free_concise_recovery(self):
        class LengthEngine(ScriptedEngine):
            def chat_completion(self,messages,**kwargs):
                answer=super().chat_completion(messages,**kwargs)
                self.runtime.last_generation_diagnostics.update(finish_reason='length' if len(self.requests)==1 else 'stop',generated_token_count=800 if len(self.requests)==1 else 80)
                return answer
        engine=LengthEngine(['The output file is `unfinished_','Preserve inputs, make the change, and verify the saved result.'])
        _,done,failed,metrics=self.run_worker(engine,(),'Summarize a careful editing workflow.')
        self.assertFalse(failed);self.assertEqual(done,['Preserve inputs, make the change, and verify the saved result.'])
        self.assertEqual(len(engine.requests),2);self.assertEqual(engine.requests[-1][1]['allowed_tools'],[])
        self.assertGreaterEqual(engine.requests[-1][1]['max_tokens'],1024)
        self.assertEqual(metrics[-1]['tool_workflow']['finalization_reason'],'visible_answer_truncated')

    def test_repeated_visible_length_cutoff_is_never_marked_complete(self):
        class LengthEngine(ScriptedEngine):
            def chat_completion(self,messages,**kwargs):
                answer=super().chat_completion(messages,**kwargs)
                self.runtime.last_generation_diagnostics.update(finish_reason='length',generated_token_count=800)
                return answer
        engine=LengthEngine(['Unfinished fragment','Another unfinished fragment'])
        _,done,failed,_=self.run_worker(engine,(),'Summarize a careful editing workflow.')
        self.assertFalse(done);self.assertTrue(failed);self.assertEqual(len(engine.requests),2)
        self.assertIn('Diagnostic: tools-',failed[0])

    def test_exhausted_validation_formatting_returns_text_and_keeps_calculator(self):
        from app import ChatWorker
        prompt='Find our previous timings and calculate the percentage improvement.'
        analysis=analyze_request(prompt)
        source={'id':'M3','url':'offlinegpt://chat/test?message=3','date':'2026-10-08','evidenceKind':'USER_STATEMENT','excerpt':'I tested sampling: 9 seconds before and 7 seconds after.','chatId':'test','created':1}
        analysis.update(memory_request=True,memory_sources=[source],agent_tools=['calculator'],max_retries=0)
        engine=ScriptedEngine([call(expression='(9-7)/9*100'),'The improvement is 89.9% [M3].'])
        worker=ChatWorker('local',[{'role':'user','content':prompt}],engine,analysis)
        done=[];failed=[];metrics=[]
        worker.done.connect(done.append);worker.failed.connect(failed.append);worker.metrics.connect(metrics.append)
        worker.run()
        self.assertFalse(failed);self.assertTrue(done)
        self.assertIn('22.222',done[0]);self.assertNotIn('89.9%',done[0])
        self.assertEqual(metrics[-1]['validation_status'],'partial')
        self.assertNotIn('exception',metrics[-1])
        self.assertNotIn('synthesis_error_reference',metrics[-1])


if __name__=='__main__':unittest.main()
