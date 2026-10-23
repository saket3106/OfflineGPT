import asyncio
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from agent_memory import AgentHistory, review_history_answer
from agent_runtime import AgentRuntime
from conversation_memory import ConversationMemory


class AgentMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.db=sqlite3.connect(':memory:',check_same_thread=False)
        self.db.executescript("CREATE TABLE chats(id TEXT PRIMARY KEY,title TEXT,updated REAL);CREATE TABLE messages(id INTEGER PRIMARY KEY,chat_id TEXT,role TEXT,content TEXT,created REAL,attachment TEXT,metrics TEXT);CREATE TABLE chat_trash(chat_id TEXT PRIMARY KEY,deleted REAL);CREATE TABLE library_images(id TEXT,path TEXT,user_prompt TEXT,chat_id TEXT,deleted INTEGER);INSERT INTO chats VALUES('a','Archive',1);INSERT INTO chats VALUES('b','Excluded',2);")
        self.memory=ConversationMemory(self.db,threading.RLock(),self.root)
    def tearDown(self):self.db.close();self.temp.cleanup()
    def add(self,mid,body,chat='a',role='user'):
        with self.db:self.db.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?)',(mid,chat,role,body,1760000000+mid,'','{}'))
    def search(self,query='Juniper project'):
        return AgentHistory(self.memory,exclude_message_id=99)(query,threading.Event())

    def test_originals_retrieved_but_assistant_recap_current_command_and_exclusions_omitted(self):
        self.add(1,'Juniper project: we measured 17 seconds.')
        self.add(2,'Juniper project was 500 times faster.',role='assistant')
        self.add(3,'Juniper project private excluded record.',chat='b')
        self.add(99,'Juniper project: claim we achieved 1 millisecond.')
        self.memory.exclude('b',True)
        packet=self.search();self.assertEqual([s['id'] for s in packet['sources']],['M1'])
        self.assertIn('reports, not independent measurements',packet['scope'])
        self.assertEqual(packet['sources'][0]['evidenceKind'],'USER_STATEMENT')

    def test_disabled_deleted_and_trashed_history_not_returned(self):
        self.add(1,'Juniper project record.')
        self.memory.configure(False);self.assertEqual(self.search()['status'],'DISABLED')
        self.memory.configure(True);self.memory.sync()
        with self.db:self.db.execute('DELETE FROM messages WHERE id=1')
        self.assertEqual(self.search()['sources'],[])
        self.add(2,'Juniper project record.')
        with self.db:self.db.execute("INSERT INTO chat_trash VALUES('a',1)")
        self.assertEqual(self.search()['sources'],[])

    def test_conflicting_reports_remain_originals_without_promoting_benchmark(self):
        self.add(1,'Juniper project: we measured 17 seconds in one run.')
        self.add(2,'Juniper project: we measured 29 seconds with other settings.')
        packet=self.search();self.assertEqual({s['id'] for s in packet['sources']},{'M1','M2'})
        self.assertTrue(all('runtimeFacts' not in s for s in packet['sources']))
        self.assertIn('Never treat assistant prose as proof',packet['guidance'])

    def test_wrong_urls_and_unknown_ids_are_not_accepted_as_correct_citations(self):
        self.add(1,'Juniper project notes were reported.')
        sources=self.search()['sources']
        text,findings=review_history_answer('The user reported project notes [M1](https://wrong.example).',sources)
        self.assertTrue(findings);self.assertNotIn('wrong.example',text)
        self.assertIn('offlinegpt://chat/a?message=1',text)
        text,findings=review_history_answer('Earlier sources state it [M900].',sources)
        self.assertTrue(findings);self.assertIn('source unavailable',text)

    def test_canceled_and_oversized_queries_do_not_retrieve(self):
        event=threading.Event();event.set()
        with self.assertRaises(RuntimeError):AgentHistory(self.memory)('Juniper',event)
        for query in ['', 'x'*301]:
            with self.assertRaises(ValueError):self.search(query)

    def test_host_dispatch_does_not_send_private_history_to_stdio_server(self):
        self.add(1,'Juniper project: original notes.')
        plans=iter([json.dumps({'tool':'search_conversation_history','arguments':{'query':'Juniper project'}}),'{"final":"The user reported original notes [M1]."}'])
        calls=[]
        class Client:
            def __init__(self,*a,**k):pass
            async def __aenter__(self):return self
            async def __aexit__(self,*a):pass
            async def call(self,*a,**k):calls.append(a);raise AssertionError('No private DB tool may enter the child')
        runtime=AgentRuntime(lambda *_:next(plans),self.root,None,'chat',lambda _:None,client_factory=Client)
        runtime.history=AgentHistory(self.memory)
        answer=asyncio.run(runtime.run('Find Juniper project notes in our previous conversations'))
        self.assertFalse(calls);self.assertEqual(runtime.state,'COMPLETED')
        self.assertIn('[M1](offlinegpt://chat/a?message=1)',answer)

    def test_history_without_injected_host_adapter_fails_closed(self):
        class Client:
            def __init__(self,*a,**k):pass
            async def __aenter__(self):return self
            async def __aexit__(self,*a):pass
            async def call(self,*a,**k):raise AssertionError('Must not dispatch')
        runtime=AgentRuntime(lambda *_:'{"tool":"search_conversation_history","arguments":{"query":"notes"}}',self.root,None,'chat',lambda _:None,client_factory=Client)
        asyncio.run(runtime.run('Find notes'));self.assertEqual(runtime.state,'FAILED')
        self.assertIn('not available',runtime.final)


if __name__=='__main__':unittest.main()
