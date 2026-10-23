import hashlib,json,sqlite3,tempfile,threading,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from model_library import ModelLibrary,discover_models
from test_model_inspection import fixture

class ModelLibraryTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name).resolve()
  self.con=sqlite3.connect(self.root/'test.db',check_same_thread=False);self.addCleanup(self.con.close)
  self.con.execute('CREATE TABLE chats(id TEXT PRIMARY KEY,title TEXT,updated REAL)')
  self.con.execute('CREATE TABLE messages(id INTEGER PRIMARY KEY,chat_id TEXT,role TEXT,content TEXT,created REAL)');self.con.commit()
  self.ws=SimpleNamespace(con=self.con,lock=threading.RLock(),directory=self.root);self.library=ModelLibrary(self.ws)
  self.file=self.root/'model.gguf';self.write()
 def write(self,architecture='qwen3',split=1,template=True):
  rows=[('general.architecture',8,architecture),('general.name',8,'Synthetic local model'),('split.count',4,split),('tokenizer.ggml.model',8,'test')]
  if template:rows.append(('tokenizer.chat_template',8,'{{ messages }}'))
  self.file.write_bytes(fixture(rows))
 def import_model(self):return self.library.import_file(str(self.file))['id']
 def test_passive_list_does_not_migrate_database(self):
  before=self.con.execute('SELECT name,sql FROM sqlite_master').fetchall()
  self.assertEqual(self.library.list(),[]);self.assertEqual(self.con.execute('SELECT name,sql FROM sqlite_master').fetchall(),before)
 def test_import_keeps_original_pixels_weights_and_identity_and_no_native_claim(self):
  before=self.file.read_bytes();mid=self.import_model();row=self.library.list()[0]
  self.assertEqual(row['id'],mid);self.assertEqual(row['fileState'],'present');self.assertFalse(row['nativeCompatible']);self.assertEqual(row['executionVerified'],[])
  self.assertEqual(self.file.read_bytes(),before);self.assertTrue(row['selectable']);self.assertTrue((self.root/'backups').is_dir())
 def test_repeated_import_is_one_reference(self):
  first=self.import_model();self.assertEqual(self.import_model(),first);self.assertEqual(len(self.library.list()),1)
 def test_changed_or_missing_weight_is_not_silently_selected(self):
  mid=self.import_model();self.file.write_bytes(self.file.read_bytes()+b'x');self.assertEqual(self.library.list()[0]['fileState'],'changed')
  with self.assertRaisesRegex(ValueError,'changed'):self.library.selected(mid)
  self.file.unlink();self.assertEqual(self.library.list()[0]['fileState'],'missing-or-unsafe')
  with self.assertRaises(OSError):self.library.selected(mid)
 def test_specialist_unknown_split_and_template_missing_cannot_select(self):
  for arch,split,template in [('flux',1,True),('clip',1,True),('qwen3vl',1,True),('unknown',1,True),('qwen3',2,True),('qwen3',1,False)]:
   with self.subTest(architecture=arch,split=split,template=template):
    self.write(arch,split,template);mid=self.import_model();self.assertFalse(self.library.list()[0]['selectable'])
    with self.assertRaises(ValueError):self.library.selected(mid)
 def test_cancelled_import_leaves_no_catalog(self):
  cancel=threading.Event();cancel.set()
  with self.assertRaises(ValueError):self.library.import_file(str(self.file),cancel)
  self.assertEqual(self.library.list(),[])
 def test_reference_removal_never_deletes_weights_or_unrelated_rows(self):
  mid=self.import_model();self.con.execute('CREATE TABLE conversations(id,body)');self.con.execute("INSERT INTO conversations VALUES('keep','untouched')");self.con.commit()
  with self.assertRaises(ValueError):self.library.remove_reference(mid,str(self.file))
  self.assertFalse(self.library.remove_reference(mid)['weightFilesDeleted']);self.assertTrue(self.file.is_file());self.assertEqual(self.library.list(),[])
  self.assertEqual(self.con.execute('SELECT * FROM conversations').fetchall(),[('keep','untouched')])
 def test_selection_needs_review_and_exact_reviewed_header(self):
  mid=self.import_model();engine=Mock();persist=Mock();args={'operation':'select','id':mid}
  for extra in [{},{'reviewed':True,'reviewedHeader':'wrong'}]:
   with self.assertRaises(ValueError):self.library.action({**args,**extra},engine,threading.Event(),lambda _:None,save_settings=persist)
  persist.assert_not_called()
  row=self.library.list()[0];self.library.action({**args,'reviewed':True,'reviewedHeader':row['headerMetadataSha256']},engine,threading.Event(),lambda _:None,save_settings=persist)
  self.assertEqual(persist.call_args.args[0]['gpt_oss_gguf'],str(self.file))
 def test_load_unload_uses_existing_owner_and_cancel_token(self):
  engine=Mock();engine.text_runtime.load.return_value='Selected';cancel=threading.Event();progress=Mock()
  result=self.library.action({'operation':'load'},engine,cancel,progress);self.assertTrue(result['loaded']);engine.text_runtime.load.assert_called_once_with(progress=progress,cancel_event=cancel)
  self.library.action({'operation':'unload'},engine,cancel,progress);engine.text_runtime.unload.assert_called_once()
 def test_cancel_or_json_failure_does_not_create_partial_reference(self):
  self.file.write_bytes(b'not GGUF')
  with self.assertRaises(ValueError):self.import_model()
  self.assertEqual(self.library.list(),[])
 def test_readback_persistence_with_new_owner(self):
  mid=self.import_model();self.assertEqual(ModelLibrary(self.ws).list()[0]['id'],mid)
 def test_atomic_settings_failure_preserves_old_configuration(self):
  import integrated_runtime
  target=self.root/'settings.json';target.write_text('{"old":true}')
  with patch.object(integrated_runtime,'CONFIG_PATH',target),patch('integrated_runtime.os.replace',side_effect=OSError('simulated failure')):
   with self.assertRaises(OSError):integrated_runtime.save_config({'new':True})
  self.assertEqual(json.loads(target.read_text()),{'old':True});self.assertEqual(list(self.root.glob('*.tmp')),[])
 def test_cancel_before_reference_removal_preserves_reference(self):
  mid=self.import_model();event=threading.Event();event.set()
  with self.assertRaises(RuntimeError):self.library.remove_reference(mid,cancel=event)
  self.assertEqual(len(self.library.list()),1);self.assertTrue(self.file.is_file())
 def test_commit_acknowledgement_is_not_lost_to_late_cancel(self):
  import app
  engine=Mock();worker=app.WorkspaceTask('isolated','modelLibrary',{'operation':'import','path':str(self.file)},self.ws,engine)
  outcomes=[];worker.done.connect(lambda raw:outcomes.append(json.loads(raw)))
  original=ModelLibrary.action
  def commit_then_cancel(*args,**kwargs):
   result=original(*args,**kwargs);worker.cancel_event.set();return result
  with patch.object(ModelLibrary,'action',commit_then_cancel):worker.run()
  self.assertTrue(outcomes[0]['ok']);self.assertTrue(outcomes[0]['value']['committed']);self.assertTrue(outcomes[0]['value']['cancelAfterCommit'])
 def test_discovery_only_lists_explicit_bounded_root_and_never_imports(self):
  folder=self.root/'nested';folder.mkdir();(folder/'other.GGUF').write_bytes(b'not inspected')
  result=discover_models(str(self.root));self.assertEqual(len(result['paths']),2);self.assertFalse(result['nativeCompatible']);self.assertEqual(self.library.list(),[])
  event=threading.Event();event.set()
  with self.assertRaises(RuntimeError):discover_models(str(self.root),event)
  with self.assertRaises(ValueError):discover_models('relative')
 def test_unexpected_schema_fails_closed_without_migration(self):
  self.con.execute('CREATE TABLE local_model_references(id TEXT,path TEXT)');self.con.commit()
  schema=self.con.execute('SELECT sql FROM sqlite_master ORDER BY name').fetchall()
  with self.assertRaisesRegex(RuntimeError,'schema requires recovery'):self.library.list()
  with self.assertRaises(RuntimeError):self.import_model()
  self.assertEqual(self.con.execute('SELECT sql FROM sqlite_master ORDER BY name').fetchall(),schema)
 def test_unreviewed_trigger_cannot_mutate_conversations_during_registry_write(self):
  self.import_model();self.con.execute("INSERT INTO chats VALUES('keep','untouched',0)")
  self.con.execute("CREATE TRIGGER malicious_import AFTER UPDATE ON local_model_references BEGIN DELETE FROM chats; END");self.con.commit()
  with self.assertRaisesRegex(RuntimeError,'trigger'):self.import_model()
  self.assertEqual(self.con.execute('SELECT title FROM chats').fetchall(),[('untouched',)])
