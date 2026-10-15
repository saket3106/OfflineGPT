"""Synthetic-only Library filesystem/DB failure injection."""
import json,sqlite3,tempfile,threading,unittest
from unittest.mock import patch
from pathlib import Path
from workspace_services import LocalWorkspace,migrate_database

class LibrarySafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.db=sqlite3.connect(self.root/'test.sqlite3',check_same_thread=False)
        self.db.executescript("CREATE TABLE chats(id TEXT PRIMARY KEY,title TEXT,updated REAL);CREATE TABLE messages(id INTEGER PRIMARY KEY,chat_id TEXT,role TEXT,content TEXT,created REAL,attachment TEXT,metrics TEXT);CREATE TABLE library_images(id TEXT PRIMARY KEY,path TEXT,deleted INTEGER DEFAULT 0,user_prompt TEXT DEFAULT '');")
        migrate_database(self.db,self.root);self.lock=threading.RLock();self.ws=LocalWorkspace(self.db,self.lock,self.root)
        self.trash=self.root/'fake-trash';self.trash.mkdir()
    def tearDown(self):self.doCleanups();self.db.close();self.temp.cleanup()
    def add(self,iid):
        path=self.root/(iid+'.png');path.write_bytes(b'synthetic isolated image fixture')
        self.db.execute('INSERT INTO library_images(id,path,deleted) VALUES(?,?,0)',(iid,str(path)));self.db.commit();return path
    def recycle(self,path):path.rename(self.trash/path.name);return True
    def deleted(self,iid):return self.db.execute('SELECT deleted FROM library_images WHERE id=?',(iid,)).fetchone()[0]
    def test_hide_keeps_pixels_and_versions_and_restore(self):
        p=self.add('a');before=p.read_bytes()
        self.assertEqual(self.ws.manage_library(['a'],'hide')['completed'],1)
        self.assertEqual(p.read_bytes(),before);self.assertEqual(self.deleted('a'),1)
        self.assertEqual(self.ws.manage_library(['a'],'restore')['completed'],1);self.assertEqual(self.deleted('a'),0)
    def test_confirmation_before_any_fs_action(self):
        p=self.add('a')
        with self.assertRaises(ValueError):self.ws.manage_library(['a'],'recycle',recycler=self.recycle)
        self.assertTrue(p.exists());self.assertEqual(self.deleted('a'),0)
    def test_success_retains_tombstone_and_audit(self):
        p=self.add('a');r=self.ws.manage_library(['a'],'recycle',True,recycler=self.recycle)
        self.assertEqual(r['completed'],1);self.assertFalse(p.exists());self.assertTrue((self.trash/p.name).exists())
        self.assertEqual(self.deleted('a'),1);self.assertEqual(self.db.execute('SELECT state FROM library_file_operations').fetchone()[0],'completed')
        self.assertTrue(list((self.root/'backups').glob('pre-library-journal-*.sqlite3')))
    def test_recycle_failure_keeps_record_visible(self):
        p=self.add('a');r=self.ws.manage_library(['a'],'recycle',True,recycler=lambda p:False)
        self.assertEqual(r['completed'],0);self.assertTrue(p.exists());self.assertEqual(self.deleted('a'),0)
    def test_partial_failure_does_not_terminate_siblings(self):
        self.add('a');self.add('b');self.add('c')
        r=self.ws.manage_library(['a','b','c'],'recycle',True,recycler=lambda p:False if p.stem=='b' else self.recycle(p))
        self.assertEqual(r['completedIds'],['a','c']);self.assertEqual(self.deleted('b'),0)
    def test_cancellation_between_items(self):
        self.add('a');self.add('b');cancel=threading.Event()
        def recycle(p):self.recycle(p);cancel.set();return True
        r=self.ws.manage_library(['a','b'],'recycle',True,cancel,recycle)
        self.assertEqual(r['completedIds'],['a']);self.assertEqual(r['unprocessedIds'],['b']);self.assertTrue(r['cancelled'])
    def test_shared_chat_attachment_protected(self):
        p=self.add('a');self.db.execute('INSERT INTO messages VALUES(1,?,?,?,?,?,?)',('chat','assistant','image',1,json.dumps([str(p)]),'{}'));self.db.commit()
        r=self.ws.manage_library(['a'],'recycle',True,recycler=self.recycle)
        self.assertEqual(r['completed'],0);self.assertTrue(p.exists());self.assertIn('conversation',r['results'][0]['error'])
    def test_unselected_child_version_protected(self):
        p=self.add('a');self.add('b');self.db.execute("INSERT INTO image_versions VALUES('b','a','edit','{}')");self.db.commit()
        self.assertEqual(self.ws.manage_library(['a'],'recycle',True,recycler=self.recycle)['completed'],0);self.assertTrue(p.exists())
    def test_metadata_reference_protected(self):
        p=self.add('a');self.add('b');self.db.execute('INSERT INTO image_versions VALUES(?,?,?,?)',('b',None,'edit',json.dumps({'source_path':str(p)})));self.db.commit()
        self.assertEqual(self.ws.manage_library(['a'],'recycle',True,recycler=self.recycle)['completed'],0);self.assertTrue(p.exists())
    def test_outside_data_folder_protected(self):
        p=self.add('a');self.db.execute("UPDATE library_images SET path=? WHERE id='a'",(str(self.root.parent/'unrelated.png'),));self.db.commit()
        self.assertEqual(self.ws.manage_library(['a'],'recycle',True,recycler=self.recycle)['completed'],0);self.assertTrue(p.exists())
    def test_ambiguous_interruption_never_retried(self):
        p=self.add('a')
        def interrupted(p):self.recycle(p);raise SystemExit('simulated abrupt process exit')
        with self.assertRaises(SystemExit):self.ws.manage_library(['a'],'recycle',True,recycler=interrupted)
        with patch.object(LocalWorkspace,'_library_owner_alive',return_value=False):LocalWorkspace(self.db,self.lock,self.root)
        self.assertEqual(self.deleted('a'),0);self.assertEqual(self.db.execute('SELECT state FROM library_file_operations').fetchone()[0],'needs_review')
        self.assertEqual(self.ws.manage_library(['a'],'recycle',True,recycler=lambda p:self.fail('Must not retry ambiguous IO'))['completed'],0)
    def test_recorded_recycle_before_metadata_commit_recovers(self):
        p=self.add('a');self.ws._library_journal();self.recycle(p)
        self.db.execute('INSERT INTO library_file_operations VALUES(?,?,?,?,?,?,?,?,?)',('op','batch','a','recycle',str(p),'recycled','',1,1));self.db.commit()
        self.ws.recover_library_operations();self.assertEqual(self.deleted('a'),1)
        self.assertEqual(self.db.execute('SELECT state FROM library_file_operations').fetchone()[0],'completed')
    def test_restore_missing_recycled_file_fails(self):
        self.add('a');self.ws.manage_library(['a'],'recycle',True,recycler=self.recycle)
        self.assertEqual(self.ws.manage_library(['a'],'restore')['completed'],0)
    def test_no_database_corruption_after_failures(self):
        self.add('a');self.ws.manage_library(['a'],'recycle',True,recycler=lambda p:False)
        self.assertEqual(self.db.execute('PRAGMA integrity_check').fetchone()[0],'ok')

    def pending(self,iid='a',identity=True,missing=False):
        p=self.add(iid);self.ws._library_journal()
        fingerprint=self.ws._library_fingerprint(p)
        self.db.execute('INSERT INTO library_file_operations VALUES(?,?,?,?,?,?,?,?,?)',('pending-'+iid,'batch',iid,'recycle',str(p),'needs_review','Original interruption evidence',1,1))
        if identity:self.ws._library_event('pending-'+iid,'prepared_identity',{'fingerprint':fingerprint,'processId':-1,'processStarted':1})
        self.db.commit()
        if missing:self.recycle(p)
        return p,'pending-'+iid

    def test_unknown_hide_never_claims_recycle_success(self):
        p,op=self.pending(missing=True);before=list(self.trash.iterdir())
        result=self.ws.review_library_operation(op,'hide',True)
        self.assertEqual(result['state'],'closed_unknown');self.assertFalse(result['physicalFilesChanged'])
        self.assertEqual(self.deleted('a'),1);self.assertEqual(list(self.trash.iterdir()),before)
        self.assertEqual(self.db.execute('SELECT error FROM library_file_operations WHERE id=?',(op,)).fetchone()[0],'Original interruption evidence')
        self.assertEqual(self.db.execute("SELECT count(*) FROM library_file_events WHERE operation_id=? AND event='user_review'",(op,)).fetchone()[0],1)
    def test_keep_requires_restored_bytes_matching_identity(self):
        p,op=self.pending(missing=True)
        with self.assertRaisesRegex(ValueError,'Recycle Bin'):self.ws.review_library_operation(op,'keep',True)
        self.trash.joinpath(p.name).rename(p);p.write_bytes(b'different file')
        with self.assertRaisesRegex(ValueError,'original bytes'):self.ws.review_library_operation(op,'keep',True)
        self.assertEqual(self.db.execute('SELECT state FROM library_file_operations WHERE id=?',(op,)).fetchone()[0],'needs_review')
    def test_matching_restored_file_can_be_kept_without_fs_operation(self):
        p,op=self.pending(missing=True);self.trash.joinpath(p.name).rename(p);before=p.read_bytes()
        result=self.ws.review_library_operation(op,'keep',True)
        self.assertEqual(result['state'],'reviewed_keep');self.assertIn('match',result['identity']);self.assertEqual(p.read_bytes(),before)
        self.assertEqual(self.deleted('a'),0)
    def test_legacy_identity_is_explicitly_unverified(self):
        p,op=self.pending(identity=False)
        result=self.ws.review_library_operation(op,'keep',True)
        self.assertIn('legacy identity unavailable',result['identity']);self.assertIn('unknown',result['recycleOutcome'])
    def test_review_requires_confirmation(self):
        p,op=self.pending()
        with self.assertRaises(ValueError):self.ws.review_library_operation(op,'hide')
        self.assertTrue(p.exists());self.assertEqual(self.deleted('a'),0)
    def test_review_rejects_changed_owner(self):
        p,op=self.pending();other=self.add('b');self.db.execute("UPDATE library_images SET path=? WHERE id='a'",(str(other),));self.db.commit()
        with self.assertRaisesRegex(ValueError,'path changed'):self.ws.review_library_operation(op,'hide',True)
        self.assertTrue(p.exists());self.assertTrue(other.exists());self.assertEqual(self.deleted('a'),0)
    def test_changed_owner_can_close_review_without_touching_library(self):
        p,op=self.pending();other=self.add('b');self.db.execute("UPDATE library_images SET path=? WHERE id='a'",(str(other),));self.db.commit()
        before=self.db.execute('SELECT * FROM library_images ORDER BY id').fetchall()
        result=self.ws.review_library_operation(op,'leave',True)
        self.assertFalse(result['libraryVisibilityChanged']);self.assertFalse(result['physicalFilesChanged']);self.assertEqual(result['state'],'closed_unknown')
        self.assertEqual(self.db.execute('SELECT * FROM library_images ORDER BY id').fetchall(),before);self.assertTrue(p.exists());self.assertTrue(other.exists())
    def test_restart_never_hides_new_path_for_old_recycled_operation(self):
        p,op=self.pending(missing=True);other=self.add('b')
        self.db.execute("UPDATE library_file_operations SET state='recycled' WHERE id=?",(op,));self.db.execute("UPDATE library_images SET path=? WHERE id='a'",(str(other),));self.db.commit()
        self.ws.recover_library_operations();self.assertEqual(self.deleted('a'),0)
        self.assertEqual(self.db.execute('SELECT state FROM library_file_operations WHERE id=?',(op,)).fetchone()[0],'needs_review')
    def test_recovery_locks_sqlite_ownership_and_preserves_error(self):
        p,op=self.pending(missing=True);self.db.execute("UPDATE library_file_operations SET state='recycled' WHERE id=?",(op,));self.db.commit()
        second=sqlite3.connect(self.root/'test.sqlite3',timeout=0);self.addCleanup(second.close)
        def owner_alive(operation):
            with self.assertRaises(sqlite3.OperationalError):second.execute("UPDATE library_images SET path='unrelated' WHERE id='a'")
            second.rollback();return False
        with patch.object(self.ws,'_library_owner_alive',side_effect=owner_alive):self.ws.recover_library_operations()
        self.assertEqual(self.deleted('a'),1)
        self.assertEqual(self.db.execute('SELECT error FROM library_file_operations WHERE id=?',(op,)).fetchone()[0],'Original interruption evidence')
    def test_live_owner_not_recovered_by_another_instance(self):
        import os,psutil
        p,op=self.pending();self.db.execute("UPDATE library_file_operations SET state='prepared' WHERE id=?",(op,))
        details={'fingerprint':self.ws._library_fingerprint(p),'processId':os.getpid(),'processStarted':psutil.Process().create_time()}
        self.db.execute("UPDATE library_file_events SET details=? WHERE operation_id=?",(json.dumps(details),op));self.db.commit()
        second=sqlite3.connect(self.root/'test.sqlite3');self.addCleanup(second.close)
        other=LocalWorkspace(second,threading.RLock(),self.root)
        self.assertEqual(second.execute('SELECT state FROM library_file_operations WHERE id=?',(op,)).fetchone()[0],'prepared')
        result=other.manage_library(['a'],'recycle',True,recycler=lambda p:self.fail('A live operation must not be repeated'))
        self.assertEqual(result['completed'],0)
        with self.assertRaisesRegex(ValueError,'active'):other.review_library_operation(op,'hide',True)
    def test_other_sqlite_writers_blocked_during_os_operation(self):
        self.add('a');second=sqlite3.connect(self.root/'test.sqlite3',timeout=0);self.addCleanup(second.close)
        def recycle(p):
            with self.assertRaises(sqlite3.OperationalError):second.execute("UPDATE library_images SET deleted=1 WHERE id='a'")
            second.rollback();return self.recycle(p)
        self.assertEqual(self.ws.manage_library(['a'],'recycle',True,recycler=recycle)['completed'],1)
    def test_unrelated_open_transaction_is_not_committed(self):
        self.add('a');self.db.execute("UPDATE library_images SET deleted=1 WHERE id='a'")
        with self.assertRaisesRegex(RuntimeError,'transaction'):self.ws.manage_library(['a'],'hide')
        self.db.rollback();self.assertEqual(self.deleted('a'),0)
    def test_workspace_initialization_does_not_commit_callers_transaction(self):
        self.add('a');self.db.execute("UPDATE library_images SET deleted=1 WHERE id='a'")
        with self.assertRaisesRegex(RuntimeError,'transaction'):LocalWorkspace(self.db,self.lock,self.root)
        self.db.rollback();self.assertEqual(self.deleted('a'),0)
    def test_metadata_failure_after_os_success_preserves_evidence(self):
        p=self.add('a');self.ws._library_journal()
        self.db.execute("CREATE TRIGGER fail_hide BEFORE UPDATE OF deleted ON library_images BEGIN SELECT RAISE(ABORT,'injected metadata failure'); END");self.db.commit()
        result=self.ws.manage_library(['a'],'recycle',True,recycler=self.recycle)
        self.assertEqual(result['completed'],0);self.assertEqual(self.deleted('a'),0);self.assertTrue((self.trash/p.name).exists())
        self.assertEqual(self.db.execute("SELECT event FROM library_file_events WHERE event='os_recycle_success'").fetchone()[0],'os_recycle_success')
        self.assertEqual(self.db.execute('SELECT state FROM library_file_operations').fetchone()[0],'needs_review')
    def test_recovery_status_includes_missing_original_and_identity(self):
        p,op=self.pending(missing=True);status=self.ws.library_recovery_status()
        self.assertEqual(status['pendingCount'],1);self.assertFalse(status['operations'][0]['fileExists']);self.assertTrue(status['operations'][0]['hasOriginalIdentity'])
        self.assertEqual(status['operations'][0]['outcome'],'unknown')
    def test_restore_does_not_bypass_pending_review(self):
        p,op=self.pending();self.db.execute("UPDATE library_images SET deleted=1 WHERE id='a'");self.db.commit()
        result=self.ws.manage_library(['a'],'restore');self.assertEqual(result['completed'],0);self.assertEqual(self.deleted('a'),1)

if __name__=='__main__':unittest.main()
