"""Deterministic orchestration counterexamples; not native-model acceptance."""
import asyncio
from contextlib import nullcontext
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from agent_runtime import AgentRuntime,LocalAgentModel

class Client:
    result={}
    calls=0
    def __init__(self,*a,**k):pass
    async def __aenter__(self):return self
    async def __aexit__(self,*a):pass
    async def call(self,*a,**k):type(self).calls+=1;return self.result

class CompletionSafetyTests(unittest.TestCase):
    def test_omitted_observation_never_counts_as_completed(self):
        class Large(Client):result={'text':'unseen content '*600};calls=0
        plans=iter([{'tool':'read_local_file','arguments':{'path':'large.txt'}},{'final':'I read and verified every fact.'}]);events=[]
        with tempfile.TemporaryDirectory() as root:
            task=AgentRuntime(lambda *_:json.dumps(next(plans)),root,None,'c',events.append,client_factory=Large)
            answer=asyncio.run(task.run('Inspect large.txt'))
        self.assertEqual(task.state,'PARTIAL');self.assertIn('partial',answer.lower())
        self.assertTrue(next(e for e in events if e['state']=='PROCESSING_RESULT')['failed'])

    def test_successful_tool_repeat_has_independent_budget(self):
        class Repeated(Client):result={'files':[]};calls=0
        with tempfile.TemporaryDirectory() as root:
            task=AgentRuntime(lambda *_:'{"tool":"list_workspace_files","arguments":{}}',root,None,'c',lambda _:None,client_factory=Repeated,steps=8)
            asyncio.run(task.run('Inspect this workspace'))
        self.assertEqual(task.state,'BUDGET_EXHAUSTED');self.assertEqual(Repeated.calls,2)

    def test_deadline_interrupts_blocked_model_connection(self):
        stopped=threading.Event()
        class Model:
            def __call__(self,*_):
                stopped.wait(2);return '{"final":"too late"}'
            def cancel(self):stopped.set()
        with tempfile.TemporaryDirectory() as root:
            task=AgentRuntime(Model(),root,None,'c',lambda _:None,client_factory=Client,seconds=.1)
            start=time.monotonic();asyncio.run(task.run('A bounded task'))
        self.assertEqual(task.state,'BUDGET_EXHAUSTED');self.assertTrue(stopped.is_set());self.assertLess(time.monotonic()-start,1)

    def test_planning_identity_is_a_snapshot_not_new_selection(self):
        identity={'architecture':'qwen3','name':'selected-model','modelSha256':'a'*64}
        engine=SimpleNamespace(cfg={'chat_backend':'bundled'},resource_lease=lambda _:nullcontext(),
            ensure_chat=lambda **_:None,runtime=SimpleNamespace(context=4096,_prompt_tokens=lambda _:100),
            chat_completion=lambda *a,**k:'{"final":"A genuine response is required in native acceptance."}',last_completion_identity=identity)
        model=LocalAgentModel(engine);model([],threading.Event(),time.monotonic()+2)
        identity['name']='a later model';engine.last_completion_identity={}
        self.assertEqual(model.completion_identity['name'],'selected-model')

    def test_normal_small_observation_still_completes(self):
        class Small(Client):result={'text':'evidence'};calls=0
        plans=iter(['{"tool":"read_local_file","arguments":{"path":"a.txt"}}','{"final":"evidence from a.txt"}'])
        with tempfile.TemporaryDirectory() as root:
            task=AgentRuntime(lambda *_:next(plans),root,None,'c',lambda _:None,client_factory=Small)
            asyncio.run(task.run('Inspect a.txt'))
        self.assertEqual(task.state,'COMPLETED');self.assertEqual(Small.calls,1)
