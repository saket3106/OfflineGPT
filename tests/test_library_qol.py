"""Bounded isolated Library/quality-of-life regressions; no model inference."""
import json
import sqlite3
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from workspace_services import LocalWorkspace, migrate_database


class LibraryQolTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.db=sqlite3.connect(self.root/'test.sqlite3',check_same_thread=False)
        self.db.executescript("CREATE TABLE chats(id TEXT PRIMARY KEY,title TEXT,updated REAL);CREATE TABLE messages(id INTEGER PRIMARY KEY,chat_id TEXT,role TEXT,content TEXT,created REAL,attachment TEXT,metrics TEXT);CREATE TABLE library_images(id TEXT PRIMARY KEY,path TEXT,deleted INTEGER DEFAULT 0,user_prompt TEXT DEFAULT '');CREATE TABLE intelligence_meta(key TEXT PRIMARY KEY,value TEXT);CREATE TABLE chat_trash(chat_id TEXT PRIMARY KEY,deleted REAL);")
        migrate_database(self.db,self.root);self.lock=threading.RLock();self.ws=LocalWorkspace(self.db,self.lock,self.root)
    def tearDown(self):self.db.close();self.temp.cleanup()
    def add(self,iid,trash=False):
        p=self.root/(iid+'.png');p.write_bytes(b'synthetic bytes '+iid.encode())
        self.db.execute('INSERT INTO library_images VALUES(?,?,?,?)',(iid,str(p),int(trash),'Original prompt'));self.db.commit();return p
    def state(self,iid):return self.db.execute('SELECT deleted FROM library_images WHERE id=?',(iid,)).fetchone()[0]
    def group(self):
        a=self.add('a',True);b=self.add('b',True)
        self.db.execute("INSERT INTO image_versions VALUES('b','a','recolor','{}')");self.db.commit();return a,b
    def test_trash_restore_preserves_originals(self):
        a=self.add('a');b=self.add('unrelated');snapshot=b.read_bytes()
        self.assertEqual(self.ws.manage_library(['a'],'hide')['completed'],1);self.assertTrue(a.exists())
        self.assertEqual(self.ws.manage_library(['a'],'restore')['completed'],1);self.assertEqual(b.read_bytes(),snapshot)
    def test_trash_restore_rejects_replaced_original(self):
        a=self.add('a');self.ws.manage_library(['a'],'hide');a.write_bytes(b'replacement')
        result=self.ws.manage_library(['a'],'restore');self.assertEqual(result['completed'],0);self.assertEqual(self.state('a'),1)
    def test_permanent_requires_confirmation(self):
        a=self.add('a',True)
        with self.assertRaises(ValueError):self.ws.manage_library(['a'],'purge')
        self.assertTrue(a.exists())
    def test_permanent_requires_trash_first(self):
        a=self.add('a');r=self.ws.manage_library(['a'],'purge',True)
        self.assertEqual(r['completed'],0);self.assertTrue(a.exists());self.assertEqual(self.state('a'),0)
    def test_permanent_has_durable_receipt_and_tombstone(self):
        a=self.add('a',True);self.assertEqual(self.ws.manage_library(['a'],'purge',True)['completed'],1)
        self.assertFalse(a.exists());self.assertEqual(self.state('a'),2)
        self.assertEqual(self.db.execute("SELECT count(*) FROM library_file_events WHERE event='os_purge_success'").fetchone()[0],1)
        self.assertEqual(self.ws.manage_library(['a'],'restore')['completed'],0)
    def test_bulk_permanent_preserves_unrelated_image(self):
        a=self.add('a',True);b=self.add('b',True);u=self.add('u');before=u.read_bytes()
        r=self.ws.manage_library(['a','b'],'purge',True)
        self.assertEqual(r['completedIds'],['a','b']);self.assertFalse(a.exists());self.assertFalse(b.exists());self.assertEqual(u.read_bytes(),before);self.assertEqual(self.state('u'),0)
    def test_parent_requires_all_versions(self):
        a,b=self.group();r=self.ws.manage_library(['a'],'purge',True)
        self.assertEqual(r['completed'],0);self.assertTrue(a.exists());self.assertTrue(b.exists())
    def test_family_delete_retains_relationship_audit(self):
        a,b=self.group();r=self.ws.manage_library(['a','b'],'purge',True)
        self.assertEqual(r['completed'],2);self.assertEqual(self.db.execute('SELECT parent_id FROM image_versions').fetchone()[0],'a')
        self.assertEqual(self.db.execute('SELECT count(*) FROM library_images WHERE deleted=2').fetchone()[0],2)
    def test_family_preflight_blocks_shared_child_before_parent_deletion(self):
        a,b=self.group();self.db.execute('INSERT INTO messages VALUES(1,?,?,?,?,?,?)',('c','assistant','Saved',1,str(b),'{}'));self.db.commit()
        r=self.ws.manage_library(['a','b'],'purge',True);self.assertEqual(r['completed'],0);self.assertTrue(a.exists());self.assertTrue(b.exists())
    def test_saved_markdown_reference_protected(self):
        a=self.add('a',True);self.db.execute('INSERT INTO messages VALUES(1,?,?,?,?,?,?)',('c','assistant','![image]('+str(a)+')',1,'','{}'));self.db.commit()
        self.assertEqual(self.ws.manage_library(['a'],'purge',True)['completed'],0);self.assertTrue(a.exists())
    def test_missing_file_not_claimed_deleted(self):
        a=self.add('a',True);a.unlink();r=self.ws.manage_library(['a'],'purge',True)
        self.assertEqual(r['completed'],0);self.assertEqual(self.state('a'),1)
    def test_failed_unlink_keeps_trash_and_journal(self):
        a=self.add('a',True)
        with patch.object(Path,'unlink',side_effect=PermissionError('Locked fixture')):r=self.ws.manage_library(['a'],'purge',True)
        self.assertEqual(r['completed'],0);self.assertTrue(a.exists());self.assertEqual(self.state('a'),1)
        self.assertEqual(self.db.execute("SELECT state FROM library_file_operations WHERE action='purge'").fetchone()[0],'failed')
    def test_crash_after_unlink_before_receipt_is_unknown(self):
        a=self.add('a',True);original=Path.unlink
        def crash(p,*args,**kwargs):
            original(p,*args,**kwargs);raise SystemExit('Simulated process exit')
        with patch.object(Path,'unlink',crash),self.assertRaises(SystemExit):self.ws.manage_library(['a'],'purge',True)
        with patch.object(LocalWorkspace,'_library_owner_alive',return_value=False):self.ws.recover_library_operations()
        self.assertFalse(a.exists());self.assertEqual(self.state('a'),1)
        self.assertEqual(self.db.execute("SELECT state FROM library_file_operations WHERE action='purge'").fetchone()[0],'needs_review')
    def test_receipt_before_metadata_recovers_tombstone(self):
        a=self.add('a',True);self.ws._library_journal();a.unlink()
        self.db.execute('INSERT INTO library_file_operations VALUES(?,?,?,?,?,?,?,?,?)',('op','batch','a','purge',str(a),'purged','',1,1));self.db.commit()
        self.ws.recover_library_operations();self.assertEqual(self.state('a'),2)
    def test_pending_operation_blocks_hide_and_restore(self):
        a=self.add('a',True);self.ws._library_journal()
        self.db.execute('INSERT INTO library_file_operations VALUES(?,?,?,?,?,?,?,?,?)',('op','batch','a','purge',str(a),'needs_review','unknown',1,1));self.db.commit()
        for action in ['hide','restore','purge']:self.assertEqual(self.ws.manage_library(['a'],action,True)['completed'],0)
    def test_cancellation_before_purge_does_not_delete(self):
        a=self.add('a',True);cancel=threading.Event();cancel.set();r=self.ws.manage_library(['a'],'purge',True,cancel)
        self.assertEqual(r['completed'],0);self.assertTrue(a.exists());self.assertTrue(r['cancelled'])
    def test_rename_metadata_only_and_persists(self):
        a=self.add('a');before=a.read_bytes();r=self.ws.rename_library('a','My vehicle')
        self.assertEqual(r['name'],'My vehicle');self.assertEqual(a.read_bytes(),before)
        self.assertEqual(self.db.execute('SELECT name FROM library_item_names').fetchone()[0],'My vehicle')
    def test_rename_invalid_inputs(self):
        self.add('a')
        for name in ['','x'*161,'bad\nname']:
            with self.assertRaises(ValueError):self.ws.rename_library('a',name)
    def test_export_exact_original_bytes_metadata_and_versions(self):
        a,b=self.group();out=self.root/'bundle.zip';r=self.ws.export_library(['a','b'],out)
        self.assertEqual(r['count'],2)
        with zipfile.ZipFile(out) as z:
            self.assertEqual(z.testzip(),None);self.assertEqual(z.read('0001-a.png'),a.read_bytes());self.assertEqual(z.read('0002-b.png'),b.read_bytes())
            data=json.loads(z.read('manifest.json'));self.assertEqual(data['images'][1]['version']['parentId'],'a')
    def test_export_existing_destination_never_overwritten(self):
        self.add('a');out=self.root/'bundle.zip';out.write_bytes(b'keep')
        with self.assertRaises(ValueError):self.ws.export_library(['a'],out)
        self.assertEqual(out.read_bytes(),b'keep')
    def test_export_missing_item_cleans_partial_only(self):
        a=self.add('a');out=self.root/'bundle.zip'
        with self.assertRaises(ValueError):self.ws.export_library(['a','missing'],out)
        self.assertFalse(out.exists());self.assertEqual(list(self.root.glob('*.partial')),[]);self.assertTrue(a.exists())
    def test_export_cancel_preserves_original(self):
        a=self.add('a');cancel=threading.Event();cancel.set()
        with self.assertRaises(RuntimeError):self.ws.export_library(['a'],self.root/'bundle.zip',cancel)
        self.assertTrue(a.exists());self.assertEqual(list(self.root.glob('*.partial')),[])
    def test_duplicate_selection_processed_once(self):
        self.add('a',True);self.assertEqual(self.ws.manage_library(['a','a'],'purge',True)['expected'],1)
    def test_bookmark_hides_deleted_conversation(self):
        self.db.execute("INSERT INTO chats VALUES('c','Test chat',1)");self.db.execute("INSERT INTO messages VALUES(1,'c','assistant','Saved response',1,'','{}')");self.db.commit()
        self.ws.bookmark_message(1,True);self.assertEqual(len(self.ws.bookmarks()),1)
        self.db.execute("INSERT INTO chat_trash VALUES('c',2)");self.db.commit();self.assertEqual(self.ws.bookmarks(),[])
        self.db.execute("DELETE FROM chat_trash");self.db.commit();self.assertEqual(len(self.ws.bookmarks()),1)
        self.ws.bookmark_message(1,False);self.assertEqual(self.ws.bookmarks(),[])
    def test_bookmark_requires_saved_assistant(self):
        with self.assertRaises(ValueError):self.ws.bookmark_message(999,True)
    def test_prompt_draft_roundtrip_and_clear(self):
        from app import DesktopBridge
        self.db.execute("INSERT INTO chats VALUES('c','Draft chat',1)");self.db.commit()
        bridge=SimpleNamespace(backend=SimpleNamespace(store=SimpleNamespace(con=self.db,lock=self.lock)))
        payload=json.dumps({'text':'Unsent words','attachments':[{'path':str(self.root/'original.png'),'name':'original.png'}]})
        self.assertTrue(json.loads(DesktopBridge.savePromptDraft(bridge,'c',payload))['ok'])
        self.assertEqual(json.loads(DesktopBridge.getPromptDraft(bridge,'c'))['text'],'Unsent words')
        self.assertTrue(json.loads(DesktopBridge.savePromptDraft(bridge,'c','{"text":"","attachments":[]}'))['ok'])
        self.assertEqual(json.loads(DesktopBridge.getPromptDraft(bridge,'c'))['text'],'')
    def test_prompt_draft_rejects_invalid_or_missing_chat(self):
        from app import DesktopBridge
        bridge=SimpleNamespace(backend=SimpleNamespace(store=SimpleNamespace(con=self.db,lock=self.lock)))
        for cid,raw in [('missing','{"text":"Keep"}'),('', 'not json'),('', '[1]'),('', '{"text":7}'),('', '{"text":"ok","attachments":{}}')]:
            self.assertFalse(json.loads(DesktopBridge.savePromptDraft(bridge,cid,raw))['ok'])
    def test_source_bookmark_state_uses_database_message_id(self):
        from app import DesktopBridge
        self.db.execute("INSERT INTO chats VALUES('c','Bookmarks',1)");self.db.execute("INSERT INTO messages VALUES(1,'c','assistant','Saved response',1,'','{}')");self.db.commit()
        bridge=SimpleNamespace(backend=SimpleNamespace(store=SimpleNamespace(workspace=self.ws)))
        self.assertTrue(json.loads(DesktopBridge.bookmarkMessage(bridge,'1',True))['bookmarked'])
        self.assertFalse(json.loads(DesktopBridge.bookmarkMessage(bridge,'c:0',True))['ok'])
    def test_database_integrity_after_failures(self):
        self.add('a');self.ws.manage_library(['a'],'purge',True)
        self.assertEqual(self.db.execute('PRAGMA integrity_check').fetchone()[0],'ok')


if __name__=='__main__':unittest.main()
