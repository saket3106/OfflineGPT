"""CPU-only negative cases; these do not certify model answer quality."""
import json,sqlite3,tempfile,threading,unittest
from pathlib import Path
from conversation_memory import ConversationMemory,history_provenance_failures,memory_intent,retrieval_terms
from code_engine import engineering_guarantee_failures,engineering_review_focus


class EngineeringClaimsTests(unittest.TestCase):
    def test_unrelated_negation_cannot_exempt_guarantee(self):
        for text in ('This is not slow and guarantees exactly-once execution.','This is not an error, but atomic rename guarantees durability.','If a user retries politely, this guarantees exactly-once processing.'):
            with self.subTest(text=text):self.assertTrue(engineering_guarantee_failures('Review crash recovery.',text))
    def test_false_counterexamples_and_code_literals_are_not_endorsements(self):
        for text in ('False claim: Atomic rename guarantees durability.','Counterexample: File existence guarantees a valid result.','```python\nprint("This guarantees exactly-once execution.")\n```'):
            with self.subTest(text=text):self.assertEqual(engineering_guarantee_failures('Review crash recovery.',text),[])
    def test_negated_guarantees_remain_allowed(self):
        for text in ('Atomic rename does not guarantee durability.','File existence does not ensure a complete result.','This never guarantees exactly-once processing.'):
            with self.subTest(text=text):self.assertEqual(engineering_guarantee_failures('Review crash recovery.',text),[])
    def test_sqlite_unrelated_negation_not_a_cleanup_mechanism(self):
        from code_engine import transaction_guarantee_failures
        self.assertTrue(transaction_guarantee_failures('Review this SQLite transaction.','This is not slow and automatically rolls back.'))
        self.assertEqual(transaction_guarantee_failures('Review this SQLite transaction.','It is not automatically rolled back.'),[])
    def test_unqualified_exactly_once_rejected(self):
        self.assertTrue(engineering_guarantee_failures('Design crash recovery for concurrent exports.', 'This protocol guarantees that each artifact is written exactly once.'))
    def test_scope_and_uncertainty_do_not_become_guarantees(self):
        for text in ('This does not guarantee exactly-once execution.', 'Under a unique operation constraint this guarantees exactly-once publication within the database.', 'Retries may repeat execution; external side effects require their own idempotency mechanism.'):
            self.assertEqual(engineering_guarantee_failures('Design crash recovery.',text),[])
    def test_uncertainty_does_not_exempt_sibling_guarantee(self):
        self.assertTrue(engineering_guarantee_failures('Review crash recovery.', 'I cannot verify device durability. This guarantees exactly-once execution.'))
    def test_atomic_rename_is_not_power_loss_proof(self):
        self.assertTrue(engineering_guarantee_failures('Review durability.', 'Atomic rename guarantees durability.'))
    def test_file_existence_is_not_validity(self):
        self.assertTrue(engineering_guarantee_failures('Review recovery.', 'File existence guarantees a complete result.'))
    def test_routine_chat_not_gated(self):
        self.assertEqual(engineering_guarantee_failures('Define a word.', 'This guarantees exactly-once processing.'),[])
    def test_review_obligations_generalize_to_other_artifacts(self):
        focus=engineering_review_focus('Design a concurrent SQLite invoice export retry protocol after interruption.')
        self.assertIn('two competing workers',focus);self.assertIn('metadata commit',focus);self.assertNotIn('PNG',focus)
    def test_original_failed_draft_now_flagged_if_available(self):
        p=Path(__file__).resolve().parents[1]/'logs/tool-controller-repair/release-acceptance/packaged-intelligence.json'
        if not p.exists():self.skipTest('Historical model evidence is not shipped with source tests')
        rows=json.loads(p.read_text(encoding='utf-8'))['latencies']
        answer=next(r['answer'] for r in rows if r['name']=='eval-unseen')
        self.assertTrue(engineering_guarantee_failures('Design crash recovery for concurrent file exports.',answer))


class HistoryClaimsTests(unittest.TestCase):
    def test_unknown_citation_not_excused_by_uncertainty(self):
        self.assertTrue(history_provenance_failures('I cannot verify that claim [M999].',self.sources))
    def test_comparability_must_belong_to_cited_source(self):
        self.sources[1]['excerpt']+=' We used identical settings.'
        self.assertTrue(history_provenance_failures('User reported identical settings [M1].',self.sources))
        self.assertEqual(history_provenance_failures('User reported identical settings [M2].',self.sources),[])
    def test_user_report_is_not_independent_verification(self):
        self.sources[0]['excerpt']+=' We used identical settings.'
        self.assertTrue(history_provenance_failures('Settings were independently verified as identical settings [M1].',self.sources))
    def test_negative_source_does_not_verify_comparability(self):
        self.sources[0]['excerpt']+=' We did not use identical settings.'
        self.assertTrue(history_provenance_failures('User reported identical settings [M1].',self.sources))
    def test_misleading_source_link_is_repaired_to_original(self):
        from conversation_memory import validate_memory_citations
        self.sources[0]['url']='offlinegpt://chat/original?message=1'
        fixed,bad=validate_memory_citations('A result [M1](offlinegpt://chat/unrelated?message=9).',[self.sources[0]])
        self.assertFalse(bad);self.assertIn(self.sources[0]['url'],fixed);self.assertNotIn('unrelated',fixed)
    def setUp(self):
        self.sources=[{'id':'M1','date':'2024-02-12T10:00:00+00:00','excerpt':'We measured 17 seconds.','evidenceKind':'USER_STATEMENT'}, {'id':'M2','date':'2024-03-20T10:00:00+00:00','excerpt':'We measured 12 seconds.','evidenceKind':'USER_STATEMENT'}]
    def test_uncertainty_does_not_bypass_uncited_history(self):
        self.assertTrue(history_provenance_failures('I cannot verify the driver. We completed the release on 2025-01-01.',self.sources))
        self.assertTrue(history_provenance_failures('I cannot verify the driver, but we completed the release on 2025-01-01 [M1].',self.sources))
    def test_unknown_citation_rejected(self):
        self.assertTrue(history_provenance_failures('Recorded time [M1]; another finding [M99].',self.sources))
    def test_wrong_source_date_is_rejected_even_if_other_source_has_it(self):
        self.assertTrue(history_provenance_failures('The test ran on 2024-03-20 [M1].',self.sources))
    def test_pure_uncertainty_allowed(self):
        self.assertEqual(history_provenance_failures('I cannot verify that claim from these records.',self.sources),[])
    def test_valid_dates_and_qualified_comparisons_allowed(self):
        self.assertEqual(history_provenance_failures('The test ran on 2024-02-12 [M1]. Parameters are not established as identical.',self.sources),[])
    def test_setting_equality_not_inferred_from_durations(self):
        self.assertTrue(history_provenance_failures('There was no parameter variation [M1].',self.sources))
    def test_reported_equal_settings_remain_user_evidence(self):
        self.sources[0]['excerpt']+=' We used identical settings.'
        self.assertEqual(history_provenance_failures('User reported identical settings [M1].',self.sources),[])


class RetrievalRepairTests(unittest.TestCase):
    def test_original_source_exclusion_applies_to_cached_ids(self):
        self.add(1,'We measured dictation at 1 second.');self.mem.sync();self.mem.exclude('a')
        self.assertEqual(self.mem.search(source_ids=[1])['sources'],[])
    def test_deleted_source_not_reintroduced_by_cached_ids(self):
        self.add(1,'We measured dictation at 1 second.');self.mem.sync();self.db.execute('DELETE FROM messages WHERE id=1');self.db.commit()
        self.assertEqual(self.mem.search(source_ids=[1])['sources'],[])
    def test_anaphoric_followup_revalidates_deleted_original(self):
        from conversation_memory import prepare_memory
        self.add(1,'We tested transcription at 1 second.');source=self.mem.search('transcription')['sources'][0]
        recent=[{'role':'user','content':'What did we test earlier?'},{'role':'assistant','content':'It worked [M1].','metrics':{'memory_sources':[source]}}]
        self.db.execute('DELETE FROM messages WHERE id=1');self.db.commit()
        packet=prepare_memory(self.mem,None,'Did that actually work?','a',recent,threading.Event())
        self.assertEqual(packet['sources'],[])
    def test_edited_original_replaces_cached_excerpt(self):
        self.add(1,'We measured dictation at 1 second.');self.mem.sync();self.db.execute("UPDATE messages SET content='We measured dictation at 9 seconds.' WHERE id=1");self.db.commit()
        source=self.mem.search(source_ids=[1])['sources'][0];self.assertIn('9 seconds',source['excerpt']);self.assertNotIn('1 second',source['excerpt'])
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.db=sqlite3.connect(':memory:',check_same_thread=False)
        self.db.executescript("CREATE TABLE chats(id TEXT PRIMARY KEY,title TEXT,updated REAL);CREATE TABLE messages(id INTEGER PRIMARY KEY,chat_id TEXT,role TEXT,content TEXT,created REAL,attachment TEXT,metrics TEXT);CREATE TABLE chat_trash(chat_id TEXT PRIMARY KEY,deleted REAL);CREATE TABLE library_images(id TEXT,path TEXT,user_prompt TEXT,chat_id TEXT,deleted INTEGER);INSERT INTO chats VALUES('a','Orion',1);INSERT INTO chats VALUES('b','Other',2);")
        self.mem=ConversationMemory(self.db,threading.RLock(),self.root)
    def tearDown(self):self.db.close();self.temp.cleanup()
    def add(self,mid,body,cid='a'):
        self.db.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?)',(mid,cid,'user',body,1700000000+mid,'','{}'));self.db.commit()
    def test_paraphrase_retrieval_without_embedding_model(self):
        self.add(1,'We measured Whisper dictation at 1.2 seconds.')
        self.assertEqual(self.mem.search('transcription')['sources'][0]['id'],'M1')
    def test_semantic_expansion_respects_exclusions_and_projects(self):
        self.add(1,'We tested dictation.');self.mem.exclude('a')
        self.assertEqual(self.mem.search('transcription')['sources'],[])
    def test_named_anchor_not_expanded(self):
        self.add(1,'Project Orion dictation worked.');self.add(2,'Project Vega transcription worked.','b')
        self.assertEqual([s['id'] for s in self.mem.search('transcription',required_anchors=['Orion'])['sources']],['M1'])
    def test_timeline_not_broadened(self):
        self.add(1,'We tested dictation.')
        self.assertEqual(self.mem.search('transcription',timeline=True)['sources'],[])
    def test_expansion_is_bounded(self):
        self.assertLessEqual(len(retrieval_terms(' '.join(str(i) for i in range(10000)))),48)
    def test_continuity_phrases_reuse_original_sources(self):
        recent=[{'role':'user','content':'We tested an exporter.'},{'role':'assistant','content':'Reported result [M1].','metrics':{'memory_sources':[{'id':'M1'}]}}]
        for prompt in ('Did that actually work?','Which solution was faster?','What was the previous result?','Compare the first test with the latest test.','Continue the task that was left unfinished.'):
            with self.subTest(prompt=prompt):
                intent=memory_intent(prompt,recent);self.assertTrue(intent['retrieve']);self.assertTrue(intent['followup'])
    def test_continuity_not_invented_without_context(self):
        self.assertFalse(memory_intent('Which solution was faster?')['retrieve'])

if __name__=='__main__':unittest.main()
