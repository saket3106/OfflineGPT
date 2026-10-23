import asyncio
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from agent_artifacts import ArtifactJournal
from agent_mcp import WorkspaceTools
from agent_permissions import GrantAuthority, argument_digest
from agent_runtime import AgentTaskStore, AgentRuntime
from agent_workspace import WorkspaceAccess


def workspace(folder):
    con=sqlite3.connect(Path(folder)/'app.db',check_same_thread=False)
    con.executescript('CREATE TABLE IF NOT EXISTS chats(id,title,updated);CREATE TABLE IF NOT EXISTS messages(id,chat_id,role,content,created);')
    return SimpleNamespace(con=con,lock=threading.RLock(),directory=Path(folder))


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.folder=Path(self.temp.name)
        self.root=self.folder/'workspace';self.root.mkdir()
        self.workspace=workspace(self.folder);self.store=AgentTaskStore(self.workspace)
        self.journal=ArtifactJournal(self.workspace)
        self.tools=WorkspaceTools(WorkspaceAccess(self.root),GrantAuthority(b'a'*32))
        self.args={'path':'result.txt','content':'approved text'}
    def tearDown(self):
        self.tools.access.close();self.workspace.con.close();self.temp.cleanup()
    def prepare(self,args=None,tool='write_local_file'):
        return self.journal.prepare('task','chat',tool,args or self.args,self.tools)
    def call(self,args=None,tool='write_local_file'):
        args=args or self.args
        return self.tools.call(tool,args,self.tools.grants.issue(tool,args))

    def test_acknowledgement_and_independent_readback_are_required(self):
        identifier=self.prepare();result=self.call()
        self.journal.verify(identifier,'chat',result,self.tools.access)
        row=self.journal.recent('chat')[0];self.assertEqual(row['state'],'VERIFIED')
        self.assertEqual(row['expectedSha256'],hashlib.sha256(b'approved text').hexdigest())
        self.assertEqual(self.journal.recent('other'),[])
        with self.assertRaises(ValueError):self.journal.inspect(identifier,'other')

    def test_successful_disk_write_without_acknowledgement_is_unconfirmed(self):
        identifier=self.prepare();self.call()
        result=self.journal.inspect(identifier,'chat')
        self.assertEqual(result['inspectionStatus'],'EXPECTED_CONTENT_PRESENT_UNCONFIRMED')
        self.assertEqual(result['state'],'DISPATCH_INTENT');self.assertFalse(result['replayed'])
        with self.assertRaises(PermissionError):self.prepare()
        self.assertEqual((self.root/'result.txt').read_bytes(),b'approved text')

    def test_missing_destination_is_not_inferred_failure_or_deletion(self):
        identifier=self.prepare();result=self.journal.inspect(identifier,'chat')
        self.assertEqual(result['inspectionStatus'],'UNAVAILABLE_UNCONFIRMED')
        self.assertEqual(result['state'],'DISPATCH_INTENT');self.assertFalse((self.root/'result.txt').exists())
        with self.assertRaises(PermissionError):self.prepare()

    def test_partial_content_retained_and_no_auto_repair(self):
        identifier=self.prepare();(self.root/'result.txt').write_bytes(b'app')
        result=self.journal.inspect(identifier,'chat')
        self.assertEqual(result['inspectionStatus'],'PARTIAL_OR_CHANGED')
        self.assertEqual(result['observation']['bytes'],3)
        self.assertEqual((self.root/'result.txt').read_bytes(),b'app')

    def test_tampered_or_replaced_acknowledgement_never_verifies(self):
        identifier=self.prepare();result=self.call();result['fileIdentity']={}
        with self.assertRaises(ValueError):self.journal.verify(identifier,'chat',result,self.tools.access)
        self.assertEqual(self.journal.recent('chat')[0]['state'],'DISPATCH_INTENT')

    def test_original_patch_hash_and_destination_are_journaled_without_content(self):
        original=b'answer = 1\n';(self.root/'original.py').write_bytes(original)
        args={'path':'original.py','old':'1','new':'2','destination':'copy.py','expected_sha256':hashlib.sha256(original).hexdigest()}
        identifier=self.prepare(args,'patch_local_file');result=self.call(args,'patch_local_file')
        self.journal.verify(identifier,'chat',result,self.tools.access)
        record=json.loads(self.workspace.con.execute('SELECT record FROM agent_artifacts').fetchone()[0])
        self.assertEqual(record['sourceSha256'],args['expected_sha256'])
        self.assertEqual(record['argumentDigest'],argument_digest('patch_local_file',args))
        self.assertEqual(record['target'],'copy.py');self.assertNotIn('content',record)
        self.assertEqual((self.root/'original.py').read_bytes(),original)

    @unittest.skipUnless(os.name=='nt','Windows directory creation')
    def test_directory_acknowledgement_and_recovery(self):
        args={'path':'new-directory'};identifier=self.prepare(args,'create_workspace_directory')
        result=self.call(args,'create_workspace_directory')
        self.journal.verify(identifier,'chat',result,self.tools.access)
        self.assertEqual(self.journal.inspect(identifier,'chat')['inspectionStatus'],'DIRECTORY_PRESENT_UNCONFIRMED')
        self.assertEqual(list((self.root/'new-directory').iterdir()),[])

    def test_replaced_workspace_is_unavailable_and_untouched(self):
        identifier=self.prepare();self.tools.access.close()
        self.root.rename(self.folder/'retained');self.root.mkdir();(self.root/'result.txt').write_bytes(b'private replacement')
        self.assertEqual(self.journal.inspect(identifier,'chat')['inspectionStatus'],'UNAVAILABLE_UNCONFIRMED')
        self.assertEqual((self.root/'result.txt').read_bytes(),b'private replacement')

    def test_case_alias_cannot_create_second_intent(self):
        self.prepare()
        with self.assertRaises(PermissionError):self.prepare(dict(self.args,path='RESULT.txt'))
        self.assertEqual(len(self.journal.recent('chat')),1)

    def test_weak_sqlite_durability_never_authorizes_dispatch(self):
        self.workspace.con.execute('PRAGMA synchronous=NORMAL')
        with self.assertRaises(PermissionError):self.prepare()
        self.assertEqual(self.journal.recent('chat'),[])

    def test_unknown_transaction_is_not_committed_by_preparation(self):
        self.workspace.con.execute('BEGIN IMMEDIATE')
        try:
            with self.assertRaises(RuntimeError):self.prepare()
            self.assertTrue(self.workspace.con.in_transaction)
            self.assertEqual(self.journal.recent('chat'),[])
        finally:self.workspace.con.rollback()

    def test_malformed_existing_schema_fails_closed(self):
        with self.workspace.con:
            self.workspace.con.execute('DROP TABLE agent_artifacts')
            self.workspace.con.execute('CREATE TABLE agent_artifacts(id TEXT)')
        with self.assertRaises(RuntimeError):AgentTaskStore(self.workspace)

    def test_stale_recovery_cannot_downgrade_verified_receipt(self):
        identifier=self.prepare();state,old=self.journal._load(identifier,'chat')
        result=self.call();self.journal.verify(identifier,'chat',result,self.tools.access)
        from agent_permissions import canonical
        with self.assertRaises(RuntimeError):self.journal._update(identifier,state,old,state,canonical(old))
        self.assertEqual(self.journal.recent('chat')[0]['state'],'VERIFIED')

    def test_database_failure_prevents_write_dispatch(self):
        calls=[];plans=iter([json.dumps({'tool':'write_local_file','arguments':self.args}),'{"final":"done"}'])
        class Client:
            def __init__(self,*a,**k):pass
            async def __aenter__(self):return self
            async def __aexit__(self,*a):pass
            async def call(self,*a,**k):calls.append(1);raise AssertionError('Must not dispatch')
        events=[];runtime=AgentRuntime(lambda *_:next(plans),self.root,self.store,'chat',events.append,client_factory=Client)
        def notify(event):
            events.append(event)
            if event['state']=='AWAITING_APPROVAL':
                a=event['approval'];runtime.broker.decide(runtime.id,a['requestId'],a['digest'],True)
        runtime.notify=notify
        with patch.object(ArtifactJournal,'prepare',side_effect=sqlite3.OperationalError('disk full')):
            asyncio.run(runtime.run('Write a new file'))
        self.assertEqual(runtime.state,'FAILED');self.assertFalse(calls)
        self.assertFalse((self.root/'result.txt').exists())

    def test_cancel_during_approved_dispatch_leaves_recoverable_intent(self):
        tools=self.tools;args=self.args
        class Client:
            def __init__(self,*a,**k):pass
            async def __aenter__(self):return self
            async def __aexit__(self,*a):pass
            async def call(self,*a,**k):
                tools.access.write_new(args['path'],args['content']);raise asyncio.CancelledError()
        runtime=AgentRuntime(lambda *_:json.dumps({'tool':'write_local_file','arguments':args}),self.root,self.store,'chat',lambda _:None,client_factory=Client)
        def notify(event):
            if event['state']=='AWAITING_APPROVAL':
                a=event['approval'];runtime.broker.decide(runtime.id,a['requestId'],a['digest'],True)
        runtime.notify=notify;asyncio.run(runtime.run('Write a new file'))
        self.assertEqual(runtime.state,'CANCELED');self.assertIn('may have created',runtime.final)
        row=self.journal.recent('chat')[0]
        self.assertEqual(self.journal.inspect(row['id'],'chat')['inspectionStatus'],'EXPECTED_CONTENT_PRESENT_UNCONFIRMED')

    def test_real_child_exit_after_partial_new_write_survives_restart(self):
        # Owned deterministic failure injection, synthetic data only. No model.
        code='''import os,sqlite3,sys,threading
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,sys.argv[1])
from agent_runtime import AgentTaskStore
from agent_artifacts import ArtifactJournal
from agent_mcp import WorkspaceTools
from agent_permissions import GrantAuthority
from agent_workspace import WorkspaceAccess
folder=Path(sys.argv[2]);root=folder/'workspace'
w=SimpleNamespace(con=sqlite3.connect(folder/'app.db'),lock=threading.RLock(),directory=folder)
AgentTaskStore(w);tools=WorkspaceTools(WorkspaceAccess(root),GrantAuthority(b'a'*32))
ArtifactJournal(w).prepare('crashed-task','chat','write_local_file',{'path':'result.txt','content':'complete expected text'},tools)
with tools.access.descriptor('result.txt',create=True) as fd:
 os.write(fd,b'complete');os.fsync(fd);os._exit(23)
'''
        result=subprocess.run([sys.executable,'-I','-B','-c',code,str(Path(__file__).resolve().parents[1]),str(self.folder)],capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,23,result.stderr)
        self.workspace.con.close();self.workspace=workspace(self.folder);self.journal=ArtifactJournal(self.workspace)
        row=self.journal.recent('chat')[0]
        self.assertEqual(row['taskId'],'crashed-task')
        self.assertEqual(self.journal.inspect(row['id'],'chat')['inspectionStatus'],'PARTIAL_OR_CHANGED')
        self.assertEqual((self.root/'result.txt').read_bytes(),b'complete')
        self.assertEqual(self.workspace.con.execute('PRAGMA quick_check').fetchone()[0],'ok')


if __name__=='__main__':unittest.main()
