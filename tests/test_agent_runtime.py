import asyncio
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from agent_permissions import ApprovalBroker, GrantAuthority, argument_digest
from agent_workspace import WorkspaceAccess, parts_for
from agent_mcp import WorkspaceTools, validate_arguments
from agent_runtime import AgentRuntime, AgentTaskStore, LocalAgentModel, parse_plan,preserve_calculator_results


class PermissionTests(unittest.TestCase):
    def test_bound_grant_rejects_replay_and_substitution(self):
        authority=GrantAuthority(b'x'*32);args={'path':'new.txt','content':'one'}
        ticket=authority.issue('write_local_file',args)
        with self.assertRaises(PermissionError):authority.consume(ticket,'write_local_file',dict(args,content='two'))
        authority.consume(ticket,'write_local_file',args)
        with self.assertRaises(PermissionError):authority.consume(ticket,'write_local_file',args)

    def test_expired_wrong_key_and_forged_grants(self):
        first=GrantAuthority(b'a'*32,clock=lambda:10);ticket=first.issue('x',{})
        for other in [GrantAuthority(b'b'*32,clock=lambda:10),GrantAuthority(b'a'*32,clock=lambda:100)]:
            with self.assertRaises(PermissionError):other.consume(ticket,'x',{})
        with self.assertRaises(PermissionError):first.consume('{}','x',{})

    def test_approval_identity_denial_and_replay(self):
        broker=ApprovalBroker();cancel=threading.Event();seen=[];out=[]
        def notify(item):seen.append(item)
        def work():
            try:out.append(broker.request('task','write_local_file',{'path':'a.txt','content':'a'},{},notify,cancel,1))
            except PermissionError:out.append('denied')
        thread=threading.Thread(target=work);thread.start()
        for _ in range(100):
            if seen:break
            time.sleep(.005)
        request=seen[0]
        self.assertFalse(broker.decide('other',request['requestId'],request['digest'],True))
        self.assertFalse(broker.decide('task',request['requestId'],'wrong',True))
        self.assertTrue(broker.decide('task',request['requestId'],request['digest'],False))
        self.assertFalse(broker.decide('task',request['requestId'],request['digest'],True))
        thread.join(2);self.assertFalse(thread.is_alive());self.assertEqual(out,['denied']);self.assertFalse(broker.pending)

    def test_cancellation_and_timeout_never_authorize(self):
        for cancel in [threading.Event(),threading.Event()]:
            cancel.set();broker=ApprovalBroker()
            with self.assertRaises(PermissionError):broker.request('t','x',{}, {},lambda _:None,cancel,.01)
            self.assertFalse(broker.pending)
        with self.assertRaises(PermissionError):ApprovalBroker().request('t','x',{}, {},lambda _:None,threading.Event(),.01)

    def test_concurrent_approvals_do_not_cross_and_arguments_are_frozen(self):
        broker=ApprovalBroker();requests={};out={};args={'path':'a.txt','content':'original'}
        def work(name):
            try:out[name]=broker.request(name,'write_local_file',args,{},lambda r:requests.update({name:r}),threading.Event(),2)
            except PermissionError:out[name]='denied'
        threads=[threading.Thread(target=work,args=(name,)) for name in ['a','b']]
        for thread in threads:thread.start()
        for _ in range(100):
            if len(requests)==2:break
            time.sleep(.005)
        args['content']='substituted'
        a,b=requests['a'],requests['b']
        self.assertFalse(broker.decide('b',a['requestId'],a['digest'],True))
        self.assertTrue(broker.decide('a',a['requestId'],a['digest'],True))
        self.assertTrue(broker.decide('b',b['requestId'],b['digest'],False))
        for thread in threads:thread.join(2);self.assertFalse(thread.is_alive())
        self.assertEqual(out,{'a':{'path':'a.txt','content':'original'},'b':'denied'})


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        (self.root/'original.py').write_bytes(b'import os\nvalue = 1\n')
        self.access=WorkspaceAccess(self.root);self.authority=GrantAuthority(b'a'*32)
        self.tools=WorkspaceTools(self.access,self.authority)
    def tearDown(self):self.access.close();self.temp.cleanup()

    def test_paths_fail_closed(self):
        for path in ['../x','/x','C:/x','a\\b','x:stream','a/../b','a//b','.env','NUL.txt','folder./x','models/a.txt','offline_gpt.sqlite3','a\x00b','new\nname.txt','what?.txt','misleading\u202ename.txt']:
            with self.subTest(path=path),self.assertRaises(PermissionError):parts_for(path)

    def test_normal_handle_read_and_file_list(self):
        self.assertEqual(self.access.read('original.py'),b'import os\nvalue = 1\n')
        self.assertEqual(self.access.files()['files'][0]['path'],'original.py')
        self.assertEqual(self.tools.call('inspect_source_code',{'path':'original.py'})['imports'],['import os'])

    def test_approved_creation_preserves_original_and_cannot_overwrite(self):
        original=self.access.read('original.py');args={'path':'result.txt','content':'hello'}
        with self.assertRaises(PermissionError):self.tools.call('write_local_file',args)
        result=self.tools.call('write_local_file',args,self.authority.issue('write_local_file',args))
        self.assertEqual((self.root/'result.txt').read_text(),'hello');self.assertFalse(result['originalOverwritten'])
        with self.assertRaises(OSError):self.tools.call('write_local_file',args,self.authority.issue('write_local_file',args))
        self.assertEqual(original,self.access.read('original.py'))

    def test_patch_copy_and_changed_source_rejection(self):
        original=self.access.read('original.py');args={'path':'original.py','old':'value = 1','new':'value = 2','destination':'edited.py','expected_sha256':hashlib.sha256(original).hexdigest()}
        self.assertIn('+value = 2',self.tools.preview('patch_local_file',args)['diff'])
        self.tools.call('patch_local_file',args,self.authority.issue('patch_local_file',args))
        self.assertIn('value = 2',self.access.read('edited.py').decode());self.assertEqual(original,self.access.read('original.py'))
        (self.root/'original.py').write_text('changed')
        with self.assertRaises(ValueError):self.tools.call('patch_local_file',args,self.authority.issue('patch_local_file',args))

    def test_hardlink_and_protected_path_rejected(self):
        self.access.close()
        os.link(self.root/'original.py',self.root/'alias.py')
        self.access=WorkspaceAccess(self.root)
        with self.assertRaises(PermissionError):self.access.read('alias.py')
        with self.assertRaises(PermissionError):WorkspaceAccess(self.root,[self.root])

    def test_symlink_escape_rejected(self):
        with tempfile.TemporaryDirectory() as outside:
            (Path(outside)/'secret.txt').write_text('private')
            try:(self.root/'escape').symlink_to(outside,target_is_directory=True)
            except OSError:self.skipTest('Windows symlink privilege unavailable; junction escape is covered in Windows integration')
            with self.assertRaises((PermissionError,OSError)):self.access.read('escape/secret.txt')

    @unittest.skipUnless(os.name=='nt','Windows junction/handle contract')
    def test_junction_escape_and_directory_swap_do_not_change_outside(self):
        import subprocess
        with tempfile.TemporaryDirectory() as outside:
            (Path(outside)/'secret.txt').write_bytes(b'private')
            link=self.root/'escape'
            result=subprocess.run(['cmd.exe','/d','/c','mklink','/J',str(link),outside],capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW,timeout=5)
            self.assertEqual(result.returncode,0,result.stderr)
            try:
                with self.assertRaises(PermissionError):self.access.read('escape/secret.txt')
                args={'path':'escape/new.txt','content':'denied'}
                with self.assertRaises(PermissionError):self.tools.call('write_local_file',args,self.authority.issue('write_local_file',args))
                self.assertFalse((Path(outside)/'new.txt').exists())
                self.assertEqual((Path(outside)/'secret.txt').read_bytes(),b'private')
            finally:os.rmdir(link)
        (self.root/'nested').mkdir();(self.root/'nested/a.txt').write_bytes(b'original')
        with self.access.descriptor('nested/a.txt'):
            with self.assertRaises(OSError):os.rename(self.root/'nested',self.root/'swapped')
        with self.assertRaises(OSError):os.rename(self.root,self.root.with_name(self.root.name+'-swap'))

    def test_sql_is_read_only_bounded_and_conversations_protected(self):
        path=self.root/'sample.db';con=sqlite3.connect(path);con.executescript('CREATE TABLE items(value); INSERT INTO items VALUES(42);');con.close()
        self.assertEqual(self.access.database('sample.db','SELECT value FROM items')['rows'],[['42']])
        for query in ["DELETE FROM items", "ATTACH DATABASE 'outside.db' AS x", "PRAGMA writable_schema=ON", "SELECT load_extension('x')", 'SELECT 1; SELECT 2']:
            with self.subTest(query=query),self.assertRaises(sqlite3.DatabaseError):self.access.database('sample.db',query)
        con=sqlite3.connect(self.root/'private.db');con.executescript('CREATE TABLE chats(id);CREATE TABLE messages(id);');con.close()
        with self.assertRaises(PermissionError):self.access.database('private.db','SELECT * FROM messages')

    def test_argument_and_code_execution_restrictions(self):
        for tool,args in [('execute_restricted_python',{'code':'print(1)'}),('read_local_file',{'path':'original.py','approval':'yes'}),('get_system_status',{'unexpected':'x'})]:
            with self.assertRaises(ValueError):validate_arguments(tool,args)
        self.assertFalse(self.tools.call('inspect_source_code',{'path':'original.py'})['executed'])

    def test_file_and_sql_resource_budgets(self):
        (self.root/'large.txt').write_bytes(b'x'*(2*1024*1024+1))
        with self.assertRaises(ValueError):self.access.read('large.txt')
        with self.assertRaises(ValueError):self.access.write_new('large-copy.txt','x'*65537)
        con=sqlite3.connect(self.root/'numbers.db');con.execute('CREATE TABLE numbers(value)');con.commit();con.close()
        with self.assertRaises(sqlite3.OperationalError):
            self.access.database('numbers.db','WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<10000000) SELECT sum(x) FROM n')
        self.assertFalse((self.root/'large-copy.txt').exists())


class PersistenceTests(unittest.TestCase):
    def test_restart_preserves_journal_without_replaying_or_resetting_other_owners(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as folder:
            con=sqlite3.connect(Path(folder)/'test.db',check_same_thread=False)
            con.executescript('CREATE TABLE chats(id,title,updated);CREATE TABLE messages(id,chat_id,role,content,created);')
            workspace=SimpleNamespace(con=con,lock=threading.RLock(),directory=Path(folder))
            store=AgentTaskStore(workspace);store.save('task','chat','EXECUTING_TOOL',2,tool='write_local_file')
            before=con.execute('SELECT * FROM agent_tasks').fetchall()
            reopened=AgentTaskStore(workspace);recent=reopened.recent('chat')
            self.assertEqual(recent[0]['state'],'INTERRUPTED');self.assertIn('no operation',recent[0]['outcome'])
            self.assertEqual(before,con.execute('SELECT * FROM agent_tasks').fetchall())
            self.assertEqual(len(list((Path(folder)/'backups').glob('*.sqlite3'))),1)
            self.assertEqual(store.recent('chat','task')[0]['state'],'EXECUTING_TOOL')
            con.close()


class AgentTests(unittest.TestCase):
    def test_existing_model_adapter_context_and_backend_gates(self):
        from contextlib import nullcontext
        from types import SimpleNamespace
        calls=[]
        engine=SimpleNamespace(cfg={'chat_backend':'bundled','chat_context':4096},resource_lease=lambda _:nullcontext(),
              ensure_chat=lambda **_:None,runtime=SimpleNamespace(_prompt_tokens=lambda _:400),
              chat_completion=lambda *a,**k:(calls.append(k) or '{"final":"answer"}'))
        model=LocalAgentModel(engine)
        self.assertEqual(model([],threading.Event(),time.monotonic()+5),'{"final":"answer"}')
        self.assertEqual(calls[0]['allowed_tools'],[]);self.assertEqual(calls[0]['max_tokens'],700)
        self.assertEqual(engine.runtime._active_template_tools,[])
        engine.runtime._prompt_tokens=lambda _:3900
        with self.assertRaises(ValueError):model([],threading.Event(),time.monotonic()+5)
        self.assertEqual(len(calls),1)
        engine.cfg['chat_backend']='lmstudio'
        with self.assertRaises(RuntimeError):model([],threading.Event(),time.monotonic()+5)

    def test_model_cancellation_does_not_block_gui_for_socket_close(self):
        release=threading.Event();closed=threading.Event()
        class Connection:
            sock=None
            def close(self):closed.set();release.wait(1)
        model=LocalAgentModel(None);model.connection_changed(Connection())
        started=time.monotonic();model.cancel()
        self.assertLess(time.monotonic()-started,.1);self.assertTrue(closed.wait(.5));release.set()

    def test_model_adapter_never_discards_extra_actions_or_final_answers(self):
        from contextlib import nullcontext
        from types import SimpleNamespace
        answers = ['{"tool":"calculate","arguments":{"expression":"9*7"}}\n{"final":"done"}',
                   '{"final":"first"}\n{"final":"contradictory second"}']
        for answer in answers:
            engine=SimpleNamespace(cfg={'chat_backend':'bundled'},resource_lease=lambda _:nullcontext(),
                ensure_chat=lambda **_:None,runtime=SimpleNamespace(context=4096,_prompt_tokens=lambda _:100),
                chat_completion=lambda *a,**k:answer)
            actual=LocalAgentModel(engine)([],threading.Event(),time.monotonic()+5)
            self.assertEqual(actual,answer)
            with self.assertRaises(ValueError):parse_plan(actual)

    def test_non_harmony_plans_request_host_owned_schema_without_native_tools(self):
        from contextlib import nullcontext
        from types import SimpleNamespace
        from model_runtime import ModelBehavior,Family
        calls=[]
        engine=SimpleNamespace(cfg={'chat_backend':'bundled'},resource_lease=lambda _:nullcontext(),
            ensure_chat=lambda **_:None,runtime=SimpleNamespace(context=4096,_prompt_tokens=lambda _:100,behavior=ModelBehavior(Family.QWEN3)),
            chat_completion=lambda *a,**k:(calls.append(k) or '{"final":"done"}'))
        LocalAgentModel(engine)([],threading.Event(),time.monotonic()+5)
        from agent_mcp import TOOL_FIELDS,TOOL_OPTIONAL
        schema=calls[0]['response_schema'];self.assertEqual(len(schema['oneOf']),len(TOOL_FIELDS)+1)
        self.assertEqual(schema['oneOf'][0]['required'],['final'])
        tools={v['properties']['tool']['enum'][0]:v['properties']['arguments'] for v in schema['oneOf'][1:]}
        self.assertEqual(set(tools),set(TOOL_FIELDS))
        self.assertNotIn('shell',tools)
        for name,args in tools.items():
            self.assertEqual(set(args['required']),set(TOOL_FIELDS[name]))
            self.assertEqual(set(args['properties']),set(TOOL_FIELDS[name])|set(TOOL_OPTIONAL.get(name,{})))
            self.assertFalse(args['additionalProperties'])
        self.assertEqual(tools['inspect_workspace']['properties'],{})
        self.assertNotIn('offset',tools['search_images']['required'])
        self.assertEqual(calls[0]['allowed_tools'],[])
        engine.runtime.behavior=ModelBehavior();calls.clear()
        LocalAgentModel(engine)([],threading.Event(),time.monotonic()+5)
        self.assertNotIn('response_schema',calls[0])

    def test_loaded_model_context_overrides_larger_configured_agent_budget(self):
        from contextlib import nullcontext
        from types import SimpleNamespace
        engine=SimpleNamespace(cfg={'chat_backend':'bundled','chat_context':4096},resource_lease=lambda _:nullcontext(),
            ensure_chat=lambda **_:None,runtime=SimpleNamespace(context=1024,_prompt_tokens=lambda _:900),
            chat_completion=lambda *a,**k:(_ for _ in ()).throw(AssertionError('Context overflow must not generate')))
        with self.assertRaisesRegex(ValueError,'context budget exhausted'):LocalAgentModel(engine)([],threading.Event(),time.monotonic()+5)

    def test_parser_rejects_fragments_duplicate_keys_and_unavailable_tools(self):
        for value in ['```json\n{"final":"x"}\n```','{"final":"a","final":"b"}','[{"final":"a"}]','{"tool":"shell","arguments":{}}','{"final":"a"} extra','{"final":NaN}']:
            with self.subTest(value=value),self.assertRaises(ValueError):parse_plan(value)
        self.assertEqual(parse_plan('{"final":"done"}'),{'final':'done'})

    def test_task_loop_real_tools_and_only_requested_call(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder)/'a.txt').write_text('answer42')
            tools=WorkspaceTools(WorkspaceAccess(folder),GrantAuthority(b'a'*32));calls=[]
            class Client:
                def __init__(self,*_,**__):pass
                async def __aenter__(self):return self
                async def __aexit__(self,*_):pass
                async def call(self,tool,args,approved=False):calls.append(tool);return tools.call(tool,args)
            plans=iter(['{"tool":"read_local_file","arguments":{"path":"a.txt"}}','{"final":"answer42"}'])
            events=[];task=AgentRuntime(lambda *_:next(plans),folder,None,'chat',events.append,client_factory=Client)
            self.assertEqual(asyncio.run(task.run('Read a.txt')),'answer42');self.assertEqual(calls,['read_local_file']);self.assertEqual(task.state,'COMPLETED')
            self.assertFalse(any('answer42' in str(e) for e in events[:-1]))
            tools.access.close()

    def test_repeated_failed_operation_is_not_reexecuted(self):
        with tempfile.TemporaryDirectory() as folder:
            calls=[]
            class Client:
                def __init__(self,*_,**__):pass
                async def __aenter__(self):return self
                async def __aexit__(self,*_):pass
                async def call(self,*_,**__):calls.append(1);raise ValueError('missing file')
            task=AgentRuntime(lambda *_:'{"tool":"read_local_file","arguments":{"path":"missing.txt"}}',folder,None,'chat',lambda _:None,client_factory=Client)
            asyncio.run(task.run('read missing.txt'));self.assertEqual(calls,[1]);self.assertEqual(task.state,'FAILED')

    def test_single_syntax_repair_preserves_completed_tool_without_replaying_it(self):
        with tempfile.TemporaryDirectory() as folder:
            calls=[];prompts=[];events=[]
            class Client:
                def __init__(self,*_,**__):pass
                async def __aenter__(self):return self
                async def __aexit__(self,*_):pass
                async def call(self,tool,args,approved=False):calls.append(tool);return {'expression':'9*7','value':63}
            plans=iter(['{"tool":"calculate","arguments":{"expression":"9*7"}}',
                        '{"final":"answer":63}', '{"final":"The result is 63."}'])
            def model(messages,*_):prompts.append(messages);return next(plans)
            task=AgentRuntime(model,folder,None,'chat',events.append,client_factory=Client)
            self.assertEqual(asyncio.run(task.run('Calculate 9*7')),'The result is 63.\n\nLocal calculator: `9*7` = 63')
            self.assertEqual(calls,['calculate']);self.assertEqual(task.state,'COMPLETED')
            self.assertEqual(len(prompts),3)
            self.assertIn('untrusted data',prompts[-1][-1]['content'])
            self.assertEqual(sum(e['state']=='REPAIRING_FORMAT' for e in events),1)

    def test_syntax_repair_is_bounded_and_does_not_authorize_unsafe_actions(self):
        for responses in [['{"final":', '{"final":'],
                          ['{"final":', '{"tool":"shell","arguments":{}}'],
                          ['{"final":"one","final":"two"}'],
                          ['{"final":"one"}\n{"final":"two"}']]:
            with self.subTest(responses=responses),tempfile.TemporaryDirectory() as folder:
                attempts=[];dispatched=[];plans=iter(responses)
                class Client:
                    def __init__(self,*_,**__):pass
                    async def __aenter__(self):return self
                    async def __aexit__(self,*_):pass
                    async def call(self,*_,**__):dispatched.append(True);raise AssertionError('Rejected action must never execute')
                def model(*_):attempts.append(1);return next(plans)
                task=AgentRuntime(model,folder,None,'chat',lambda _:None,client_factory=Client)
                asyncio.run(task.run('Answer'));self.assertEqual(task.state,'FAILED')
                self.assertEqual(len(attempts),len(responses))
                self.assertEqual(dispatched,[])

    def test_arithmetic_receipts_are_runtime_derived_and_missing_results_are_partial(self):
        receipt=[{'tool':'calculate','result':{'expression':'91*73','value':6643}}]
        for answer in ['answer','The result is 6000.','I used a calculator.']:
            text,findings=preserve_calculator_results(answer,receipt)
            self.assertTrue(findings);self.assertIn('`91*73` = 6643',text)
        text,findings=preserve_calculator_results('The total is 6,643.',receipt)
        self.assertEqual(findings,[]);self.assertIn('Local calculator',text)
        self.assertEqual(preserve_calculator_results('Calculator: 6643',[]),('Calculator: 6643',[]))
        _,findings=preserve_calculator_results('True',[{'tool':'calculate','result':{'expression':'1','value':True}}])
        self.assertTrue(findings)
        _,findings=preserve_calculator_results('The result is 0.3.',[{'tool':'calculate','result':{'expression':'0.1+0.2','value':0.1+0.2}}])
        self.assertEqual(findings,[])
        _,findings=preserve_calculator_results('999999999999999999',[{'tool':'calculate','result':{'expression':'10**18','value':10**18}}])
        self.assertTrue(findings)

    def test_missing_arithmetic_answer_never_counts_as_completed(self):
        with tempfile.TemporaryDirectory() as folder:
            class Client:
                def __init__(self,*_,**__):pass
                async def __aenter__(self):return self
                async def __aexit__(self,*_):pass
                async def call(self,*_,**__):return {'expression':'11*13','value':143}
            plans=iter(['{"tool":"calculate","arguments":{"expression":"11*13"}}','{"final":"answer"}'])
            runtime=AgentRuntime(lambda *_:next(plans),folder,None,'chat',lambda _:None,client_factory=Client)
            text=asyncio.run(runtime.run('Calculate 11*13'))
            self.assertEqual(runtime.state,'PARTIAL')
            self.assertIn('`11*13` = 143',text)

    def test_no_operation_is_answered_without_claiming_completed_workspace_work(self):
        with tempfile.TemporaryDirectory() as folder:
            class Client:
                def __init__(self,*_,**__):pass
                async def __aenter__(self):return self
                async def __aexit__(self,*_):pass
            events=[];runtime=AgentRuntime(lambda *_:'{"final":"I cannot inspect those files."}',folder,None,'chat',events.append,client_factory=Client)
            text=asyncio.run(runtime.run('Read two workspace files'))
            self.assertEqual(runtime.state,'ANSWERED');self.assertIn('No tools were executed',text)
            self.assertEqual(events[-1]['toolCalls'],0)
            self.assertFalse(any(event['state']=='COMPLETED' for event in events))

    def test_cancel_before_tool_dispatch_and_step_budget(self):
        with tempfile.TemporaryDirectory() as folder:
            class Client:
                def __init__(self,*_,**__):pass
                async def __aenter__(self):return self
                async def __aexit__(self,*_):pass
                async def call(self,*_,**__):return {'files':[]}
            task=AgentRuntime(lambda *_:'{"tool":"list_workspace_files","arguments":{}}',folder,None,'chat',lambda _:None,client_factory=Client,steps=2)
            asyncio.run(task.run('list'));self.assertEqual(task.step,2);self.assertEqual(task.state,'BUDGET_EXHAUSTED')
            task=AgentRuntime(lambda *_:'{"final":"not run"}',folder,None,'chat',lambda _:None,client_factory=Client);task.cancel()
            asyncio.run(task.run('list'));self.assertEqual(task.state,'CANCELED')

    def test_sdk_exception_group_cancellation_is_not_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            class Client:
                def __init__(self,*_,**__):pass
                async def __aenter__(self):return self
                async def __aexit__(self,*_):
                    raise BaseExceptionGroup('SDK cleanup',[asyncio.CancelledError()])
                async def call(self,*_,**__):raise AssertionError('No tools expected')
            events=[]
            task=AgentRuntime(lambda *_:'{"final":"answer"}',folder,None,'chat',events.append,client_factory=Client)
            asyncio.run(task.run('answer'));self.assertEqual(task.state,'CANCELED')
            self.assertFalse(any(event['state']=='COMPLETED' for event in events))

    def test_cancellation_while_awaiting_transport_startup_is_bounded(self):
        with tempfile.TemporaryDirectory() as folder:
            entered=asyncio.Event();events=[]
            class Client:
                def __init__(self,*_,**__):pass
                async def __aenter__(self):entered.set();await asyncio.Event().wait()
                async def __aexit__(self,*_):pass
            runtime=AgentRuntime(lambda *_:(_ for _ in ()).throw(AssertionError('Model must not run')),folder,None,'chat',events.append,client_factory=Client)
            async def scenario():
                task=asyncio.create_task(runtime.run('Read the workspace'))
                await asyncio.wait_for(entered.wait(),1)
                started=time.monotonic();runtime.cancel()
                await asyncio.wait_for(task,1)
                return time.monotonic()-started
            self.assertLess(asyncio.run(scenario()),1);self.assertEqual(runtime.state,'CANCELED')
            self.assertFalse(any(event['state']=='COMPLETED' for event in events))

    def test_existing_task_completeness_is_not_bypassed_in_agent_mode(self):
        with tempfile.TemporaryDirectory() as folder:
            class Client:
                def __init__(self,*_,**__):pass
                async def __aenter__(self):return self
                async def __aexit__(self,*_):pass
            task=AgentRuntime(lambda *_:'{"final":"1. First answer only."}',folder,None,'chat',lambda _:None,client_factory=Client)
            asyncio.run(task.run('1. Explain the first file.\n2. Explain the second file.'))
            self.assertEqual(task.state,'PARTIAL');self.assertIn('Missing required IDs: 2',task.final)


if __name__=='__main__':unittest.main()
