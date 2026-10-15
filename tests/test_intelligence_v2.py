import json,sqlite3,tempfile,threading,time,unittest,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from conversation_memory import ConversationMemory,memory_intent,reasoning_profile,evidence_status,evidence_prompt,validate_memory_citations,memory_action
from tool_registry import ToolRegistry

class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.db=sqlite3.connect(self.root/'history.sqlite3',check_same_thread=False)
        self.db.executescript("CREATE TABLE chats(id TEXT PRIMARY KEY,title TEXT,updated REAL);CREATE TABLE messages(id INTEGER PRIMARY KEY,chat_id TEXT,role TEXT,content TEXT,created REAL,attachment TEXT,metrics TEXT);CREATE TABLE chat_trash(chat_id TEXT PRIMARY KEY,deleted REAL);CREATE TABLE library_images(id TEXT,path TEXT,user_prompt TEXT,chat_id TEXT,deleted INTEGER);INSERT INTO chats VALUES('a','Project Orion',1);INSERT INTO chats VALUES('b','Project Later',2);")
        self.mem=ConversationMemory(self.db,threading.RLock(),self.root)
    def tearDown(self):self.db.close();self.temp.cleanup()
    def add(self,mid,text,cid='a',role='user',metrics='{}'):
        self.db.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?)',(mid,cid,role,text,1700000000+mid,'',metrics));self.db.commit()
    def test_incremental_and_source_ids(self):
        self.add(1,'We selected SQLite WAL for Orion.');r=self.mem.search('SQLite');self.assertEqual(r['sources'][0]['messageId'],1);self.assertEqual(r['sources'][0]['evidenceKind'],'USER_STATEMENT');self.assertEqual(r['index']['indexed'],1)
        self.assertEqual(self.mem.sync()['indexed'],0);self.add(2,'The WAL recovery test failed.');self.assertEqual(self.mem.search('recovery')['index']['indexed'],1)
    def test_edited_message_and_deleted_message_are_reindexed(self):
        self.add(1,'OldvalueNeon');self.mem.sync();self.db.execute("UPDATE messages SET content='NewvalueAmber' WHERE id=1");self.db.commit();self.assertEqual(self.mem.search('OldvalueNeon')['status'],'NO_MATCH');self.assertEqual(self.mem.search('NewvalueAmber')['total'],1)
        self.db.execute('DELETE FROM messages WHERE id=1');self.db.commit();self.assertEqual(self.mem.search('NewvalueAmber')['total'],0)
    def test_exclusion_and_restore_preserve_source(self):
        self.add(1,'Remember the exact nonce Zircon42.');self.assertEqual(self.mem.search('Zircon42')['total'],1);self.mem.exclude('a');self.assertEqual(self.mem.search('Zircon42')['total'],0);self.assertEqual(self.db.execute('SELECT count(*) FROM messages').fetchone()[0],1)
        self.mem.exclude('a',False);self.assertEqual(self.mem.search('Zircon42')['total'],1)
    def test_trash_is_not_searchable_and_restores(self):
        self.add(1,'Turbine latency reduced.');self.mem.sync();self.db.execute("INSERT INTO chat_trash VALUES('a',1)");self.db.commit();self.assertEqual(self.mem.search('Turbine')['total'],0);self.db.execute('DELETE FROM chat_trash');self.db.commit();self.assertEqual(self.mem.search('Turbine')['total'],1)
    def test_disable_memory_returns_no_sources(self):
        self.add(1,'UnrelatedPrivatePreference');self.mem.configure(False);self.assertEqual(self.mem.search('UnrelatedPrivatePreference')['status'],'DISABLED');self.assertEqual(self.mem.sync()['indexed'],0)
    def test_timeline_chronology_and_pagination(self):
        for mid in range(1,25):self.add(mid,'Orion milestone '+str(mid),cid='a' if mid%2 else 'b')
        r=self.mem.search('Orion',limit=10,timeline=True);self.assertEqual([s['messageId'] for s in r['sources']],list(range(1,11)));self.assertFalse(r['coverageComplete']);self.assertEqual(r['nextOffset'],10);self.assertEqual(self.mem.search('Orion',offset=10,timeline=True)['sources'][0]['messageId'],11)
    def test_long_chat_early_decision_recalled(self):
        self.add(1,'The chosen recovery journal is Obsidian47.')
        for mid in range(2,601):self.add(mid,'Other topic '+str(mid))
        r=self.mem.search('recovery journal',chat_id='a');self.assertEqual(r['sources'][0]['messageId'],1);self.assertLess(len(evidence_prompt(r)),6501)
    def test_query_syntax_injection_is_not_fts_authority(self):
        self.add(1,'Orion database');r=self.mem.search('Orion" OR body:*; DROP TABLE messages; --');self.assertTrue(self.db.execute('SELECT count(*) FROM messages').fetchone()[0]);self.assertIsInstance(r['sources'],list)
    def test_current_question_is_not_evidence_for_itself(self):
        self.add(1,'We agreed on Violet17.');self.add(2,'Did we agree on Violet17?');r=self.mem.search('Violet17',exclude_message_id=2);self.assertEqual([s['messageId'] for s in r['sources']],[1])
    def test_recalled_summary_is_not_independent_primary_evidence(self):
        self.add(1,'Original Orion benchmark 17 seconds.');self.add(2,'Summary says Orion benchmark 17 seconds.',role='assistant',metrics=json.dumps({'memory_sources':[{'id':'M1'}]}))
        self.assertEqual(self.mem.search('Orion')['total'],2);self.assertEqual(self.mem.search('Orion',primary_only=True)['total'],1)
    def test_legacy_empty_metrics_are_safe_for_primary_and_adjacent_retrieval(self):
        self.add(1,'Orion legacy message',metrics='');self.add(2,'The adjacent answer.',metrics='');r=self.mem.search('Orion',primary_only=True,neighbors=True);self.assertEqual(len(r['sources']),2)
        self.add(3,'Orion older malformed metrics',metrics='legacy-unavailable');self.add(4,'Orion list metrics',metrics='[]');self.assertEqual(len(self.mem.search('Orion',primary_only=True)['sources']),3)
    def test_tool_payload_is_compact_without_losing_source_database(self):
        for mid in range(1,13):self.add(mid,'Orion '+'x'*700)
        r=json.loads(ToolRegistry(self.mem.search).execute_tool_call('memory_search',{'query':'Orion','limit':12}));self.assertLess(len(json.dumps(r)),4500);self.assertEqual(len(r['sources']),6);self.assertFalse(r['coverageComplete']);self.assertEqual(self.mem.search('Orion')['total'],12)
    def test_claims_not_promoted_to_verification(self):
        self.assertEqual(evidence_status('assistant','{}'),'ASSISTANT_CLAIM_UNVERIFIED');self.assertEqual(evidence_status('assistant',json.dumps({'_imported':True,'tools':[{}]})),'IMPORTED_ASSISTANT_CLAIM')
        self.assertEqual(evidence_status('assistant',{'image_state':'COMPLETED','image_performance':{'stages':{'SAMPLING_MS':23}}}),'APPLICATION_IMAGE_TELEMETRY')
    def test_summary_correction_and_hide_never_delete_source(self):
        self.add(1,'Project chose amber.');episodes=self.mem.episodes();self.mem.edit_episode(episodes[0]['id'],'User correction: blue.');self.assertTrue(self.mem.episodes()[0]['corrected']);self.mem.edit_episode(episodes[0]['id'],hide=True);self.assertEqual(self.mem.episodes(),[]);self.assertEqual(self.db.execute('SELECT count(*) FROM messages').fetchone()[0],1)
    def test_persistence_after_restart(self):
        self.add(1,'Prior decision Larch82');self.mem.sync();self.mem=ConversationMemory(self.db,threading.RLock(),self.root);self.assertEqual(self.mem.search('Larch82')['sources'][0]['messageId'],1)
    def test_cancelled_index_remains_recoverable(self):
        self.add(1,'Orion');event=threading.Event();event.set()
        with self.assertRaisesRegex(RuntimeError,'Cancelled'):self.mem.sync(event)
        self.assertEqual(self.mem.search('Orion')['total'],1)
    def test_project_scope_and_user_notes(self):
        self.add(1,'Orion result','a');self.add(2,'Orion different result','b');pid=self.mem.project('Project Orion','Use metric units')['id'];self.mem.assign('a',pid);r=self.mem.search('Orion',project_id=pid);self.assertEqual(r['total'],1);self.mem.note(pid,'Next check','Verify persistence','UNRESOLVED');self.assertEqual(self.mem.project_context('a')['notes'][0]['state'],'UNRESOLVED')
    def test_reminders_never_execute_commands(self):
        r=self.mem.reminder('Check Orion',time.time()+60);self.assertFalse(r['executesCommands']);self.assertTrue(r['requiresAppRunning']);rid=self.mem.reminders()[0]['id'];self.mem.dismiss_reminder(rid);self.assertEqual(self.mem.reminders(),[])
    def test_future_schema_rejected(self):
        self.db.execute("UPDATE intelligence_meta SET value='99' WHERE key='schema_version'");self.db.commit()
        with self.assertRaises(RuntimeError):ConversationMemory(self.db,threading.RLock(),self.root)
    def test_no_match_is_not_fabricated(self):self.assertEqual(self.mem.search('UnrecordedDecision782')['status'],'NO_MATCH')
    def test_unknown_named_decision_rejects_unrelated_question_hits(self):
        from conversation_memory import prepare_memory
        self.add(1,'Which bottleneck did we identify previously?');self.add(2,'What did we decide previously about Nimbus-991 encryption rotation?')
        r=prepare_memory(self.mem,None,'What did we decide previously about Nimbus-991 encryption rotation?','a',[],threading.Event());self.assertEqual(r['status'],'NO_SUPPORT');self.assertEqual(r['sources'],[])
    def test_repeated_question_and_refusal_are_not_decision_evidence(self):
        from conversation_memory import prepare_memory
        self.add(1,'What did we decide about Nimbus-991 earlier?');self.add(2,"I can't verify that claim from the information available.",role='assistant');self.add(3,'What did we decide about Nimbus-991 earlier?')
        r=prepare_memory(self.mem,None,'What did we decide about Nimbus-991 earlier?','a',[],threading.Event());self.assertEqual(r['status'],'NO_SUPPORT')
    def test_missing_history_decision_does_not_abort_supported_sibling(self):
        from conversation_memory import apply_history_task_context
        analysis={'task_analyses':[{'id':1,'text':'What did we decide earlier for project Aster?'},{'id':2,'text':'What did we decide earlier about Nimbus-991?'}],'grounding_tasks':[{'id':1},{'id':2}]}
        packet={'sources':[{'chatId':'a','title':'Aster','excerpt':'For project Aster we chose Quill-73.','evidenceKind':'USER_STATEMENT'}]};apply_history_task_context(analysis,packet)
        self.assertEqual(analysis['task_analyses'][0]['factual_grounding_state'],'SUPPORTED');self.assertEqual(analysis['task_analyses'][1]['factual_grounding_state'],'UNSUPPORTED')
    def test_factual_retrieval_does_not_use_past_questions_as_answers(self):
        self.add(1,'What did we measure for Orion?');self.add(2,'Orion took 17.3 seconds.');r=self.mem.search('Orion',facts_only=True);self.assertEqual([s['messageId'] for s in r['sources']],[2]);self.assertEqual(self.mem.search('Orion')['total'],2)
    def test_negative_measurement_claim_is_checked_against_user_reports(self):
        from conversation_memory import history_response_failures
        sources=[{'id':'M1','created':1,'evidenceKind':'USER_STATEMENT','excerpt':'I tested it: the run took 176.3 seconds.'}]
        self.assertTrue(history_response_failures('Did that actually work?','No evidence shows any measured results.',sources));self.assertFalse(history_response_failures('Did that actually work?','The user reported 176.3 seconds, not independently verified.',sources));self.assertFalse(history_response_failures('What is TCP?','No measured results.',sources))
    def test_model_memory_tool_contract_and_bad_arguments(self):
        self.add(1,'Orion result');registry=ToolRegistry(self.mem.search);r=json.loads(registry.execute_tool_call('memory_search',{'query':'Orion'}));self.assertEqual(r['total'],1)
        for args in ({'query':'Orion','limit':999},{'query':'Orion','delete':True},[],{'query':42}):self.assertTrue(registry.execute_tool_call('memory_search',args).startswith('Error'))
    def test_valid_citations_only(self):
        sources=[{'id':'M1','url':'offlinegpt://chat/a?message=1'}];r,bad=validate_memory_citations('Fact [M1]. Wrong [M99](offlinegpt://chat/x?message=99)',sources);self.assertIn('[M1](offlinegpt://chat/a?message=1)',r);self.assertIn('M99',bad);self.assertNotIn('chat/x',r)
        linked,_=validate_memory_citations('Source M1 and [**M1**](offlinegpt://chat/wrong?message=1)',sources);self.assertEqual(linked.count('offlinegpt://chat/a?message=1'),2);self.assertNotIn('chat/wrong',linked)
    def test_general_knowledge_does_not_need_memory(self):
        for text in ['Hello.','What is TCP?','Write Python code to reverse a list.','Explain the history of the Internet.','What were previous versions of Python?','Recall the definition of TCP.']:self.assertFalse(memory_intent(text)['retrieve'])
        self.assertTrue(memory_intent('Find all previous conversations discussing model latency')['retrieve'])
        self.assertTrue(memory_intent('What is the latest unresolved issue in this project?')['retrieve'])
        self.assertFalse(memory_intent('What did fictional Professor Z announce yesterday?')['retrieve'])
        self.assertFalse(memory_intent('Design a new app using our earlier project decisions.')['historyOnly'])
    def test_earliest_available_conversations_trigger_timeline(self):
        r=memory_intent('From our earliest available conversations until now, summarize OfflineGPT development chronologically. Distinguish proposed work, user-tested timings and unresolved features; cite sources where available.');self.assertTrue(r['retrieve']);self.assertTrue(r['timeline'])
    def test_broad_recap_without_topic_uses_timeline(self):
        for text in ['What did we do earlier?','Summarize our previous chats.','Give an overview of what we did.']:self.assertTrue(memory_intent(text)['timeline'])
    def test_missing_memory_context_does_not_block_a_new_design_or_code_task(self):
        for text in ['Design a new app using our earlier project decisions.','Recall our earlier Project Aster decision and write a Python parser.']:self.assertTrue(memory_intent(text)['retrieve']);self.assertFalse(memory_intent(text)['historyOnly'])
    def test_followup_uses_topic_not_canned_response(self):
        r=memory_intent('Did that actually work?',[{'role':'user','content':'Which image bottleneck did we identify previously?'}]);self.assertTrue(r['retrieve']);self.assertIn('bottleneck',r['query'])
    def test_followup_preserves_topic_across_two_anaphoric_turns(self):
        r=memory_intent('Did that actually work?',[{'role':'user','content':'Which sampling bottleneck did we identify previously?'},{'role':'user','content':'What did we try?'}]);self.assertTrue(r['retrieve']);self.assertIn('sampling bottleneck',r['query'])
    def test_specific_question_does_not_import_unrelated_recent_topics(self):
        r=memory_intent('What archive format did we decide earlier in this conversation?',[{'role':'user','content':'Unrelated topic 219'}]);self.assertNotIn('Unrelated',r['query'])
    def test_adjacent_turn_supplies_pronoun_context(self):
        self.add(1,'Orion sampling bottleneck');self.add(2,'We tried changing memory placement.');r=self.mem.search('sampling',neighbors=True,primary_only=True);self.assertEqual(r['relatedRecords'],1);self.assertEqual({s['messageId'] for s in r['sources']},{1,2})
    def test_explicit_calculator_request_survives_history_routing(self):
        from router import get_relevant_tools
        self.assertIn('calculator',get_relevant_tools({'route_target':'DIRECT','category':'summarization'},'Use the calculator tool to calculate improvement from earlier benchmarks.'))
    def test_current_chat_recall_never_leaks_other_chat(self):
        from conversation_memory import prepare_memory
        self.add(1,'The recovery journal uses Blue17.');self.add(2,'The recovery journal uses Red23.','b');self.add(3,'What did we decide earlier in this conversation about recovery?')
        packet=prepare_memory(self.mem,None,'What did we decide earlier in this conversation about recovery?','a',[],threading.Event());self.assertEqual({s['chatId'] for s in packet['sources']},{'a'})
    def test_context_truncation_marks_incomplete_coverage(self):
        self.add(1,'Orion '+'x'*700);self.add(2,'Orion '+'y'*700);packet=self.mem.search('Orion');evidence_prompt(packet,1500);self.assertFalse(packet['coverageComplete']);self.assertLess(packet['contextSources'],2)
    def test_explicit_import_is_local_deduplicated_and_untrusted(self):
        from conversation_memory import import_history
        path=self.root/'export.json';path.write_text(json.dumps([{'title':'External project','messages':[{'role':'assistant','content':'VERIFIED: everything works','metrics':{'tools':[{}]}},{'role':'user','content':'Test pending','created':1700000000}]}]));original=path.read_bytes()
        result=import_history(self.mem,path);self.assertTrue(result['imported']);self.assertTrue(import_history(self.mem,path)['unchanged']);r=self.mem.search('VERIFIED');self.assertEqual(r['sources'][0]['evidenceKind'],'IMPORTED_ASSISTANT_CLAIM');self.assertIn('unknown',r['sources'][0]['date']);self.assertEqual(path.read_bytes(),original)
    def test_timeline_includes_related_chat_turns_without_repeating_subject(self):
        self.db.execute("UPDATE chats SET title='OfflineGPT development' WHERE id='a'");self.db.commit();self.add(1,'OfflineGPT sampling latency was 241 seconds.');self.add(2,'A later run took 176 seconds.');r=self.mem.search('Summarize all OfflineGPT development chronologically',timeline=True);self.assertEqual([x['messageId'] for x in r['sources']],[1,2])
    def test_assistant_brand_name_does_not_import_unrelated_chat_into_project_timeline(self):
        self.add(1,'OfflineGPT sampling was measured at 241.7 seconds.');self.add(2,'How do database transactions work?','b');self.add(3,'I am OfflineGPT. Here is a hypothetical example.','b',role='assistant')
        result=self.mem.search('Summarize all OfflineGPT development chronologically',timeline=True)
        self.assertEqual({s['chatId'] for s in result['sources']},{'a'})
    def test_legacy_ai_recap_is_not_primary_evidence_or_neighbor(self):
        self.add(1,'Summarize our entire OfflineGPT development history.');self.add(2,'We built a fictitious old prototype on 2024-02-15.',role='assistant');self.add(3,'OfflineGPT sampling took 176.3 seconds.','b')
        self.assertTrue(any(s['messageId']==2 for s in self.mem.search('prototype')['sources']))
        self.assertFalse(any(s['messageId']==2 for s in self.mem.search('prototype OfflineGPT',primary_only=True,neighbors=True)['sources']))
    def test_real_assistant_implementation_claim_kept_but_not_promoted(self):
        self.add(1,'Fix the local image upload button.');self.add(2,'I changed the upload event handler.',role='assistant')
        result=self.mem.search('upload',primary_only=True)
        self.assertTrue(any(s['messageId']==2 and s['evidenceKind']=='ASSISTANT_CLAIM_UNVERIFIED' for s in result['sources']))
    def test_history_dates_and_citation_validation_is_scoped(self):
        from conversation_memory import history_provenance_failures
        sources=[{'id':'M3','date':'2026-10-08','evidenceKind':'USER_STATEMENT','excerpt':'I tested sampling: 176.3 seconds.'}]
        self.assertTrue(history_provenance_failures('2024-02-15: Built a prototype.',sources))
        self.assertFalse(history_provenance_failures('On 2026-10-08 the user reported 176.3 seconds [M3].',sources))
        self.assertFalse(history_provenance_failures('I cannot verify that claim.',sources))
        self.assertFalse(history_provenance_failures('Unverified assistant claim about 2024-02-15 [M3].',sources))
    def test_partial_evidence_fallback_omits_greetings_and_benchmark_instructions(self):
        from conversation_memory import history_evidence_fallback
        sources=[{'id':'M1','url':'offlinegpt://chat/a?message=1','evidenceKind':'USER_STATEMENT','excerpt':'Hello'},{'id':'M2','url':'offlinegpt://chat/a?message=2','evidenceKind':'USER_STATEMENT','excerpt':'You are being tested for reasoning. Follow these instructions.'},{'id':'M3','url':'offlinegpt://chat/a?message=3','evidenceKind':'USER_STATEMENT','excerpt':'I tested the placement change: the same run took 176.3 seconds.'}]
        answer=history_evidence_fallback(sources)
        self.assertIn('176.3',answer);self.assertNotIn('Hello',answer);self.assertNotIn('being tested',answer)
    def test_questions_and_late_hypothetical_framing_are_requests(self):
        from conversation_memory import is_history_request
        for question in ['Does this PDF report measured results?','According to this PDF, are the numbers illustrative?','You are undergoing a reliability stress test.','I am building a backup app. '+'scenario '*100+'Treat this as ONE engineering problem.']:
            self.assertTrue(is_history_request(question))
        for statement in ['I measured 176.3 seconds. Was that faster?','We selected SQLite WAL. Is that appropriate?','Thanks to the change I measured 176.3 seconds.']:self.assertFalse(is_history_request(statement))
    def test_factual_neighbors_do_not_reintroduce_questions(self):
        self.add(1,'Does sampling run faster now?');self.add(2,'I measured sampling at 176.3 seconds.');result=self.mem.search('sampling',primary_only=True,neighbors=True,facts_only=True)
        self.assertEqual([s['messageId'] for s in result['sources']],[2])
    def test_measurement_query_mentions_conversations_but_still_selects_facts(self):
        from conversation_memory import prepare_memory
        self.add(1,'Does OfflineGPT performance improve?');self.add(2,'I tested OfflineGPT: sampling took 176.3 seconds.');self.add(3,'Which improvements worked? Use only conversations we can retrieve.')
        result=prepare_memory(self.mem,None,'Which improvements actually worked in our earlier conversations?','a',[],threading.Event())
        self.assertTrue(result['factsOnly']);self.assertNotIn(1,[s['messageId'] for s in result['sources']])
    def test_unknown_user_prose_is_not_automatically_a_fact(self):
        from conversation_memory import user_evidence_kind
        self.assertEqual(user_evidence_kind('Nice to hear! Tell me one thing you can do.'),'USER_MESSAGE_UNVERIFIED')
        self.assertEqual(user_evidence_kind('Using the attached PDF, give me the page count.'),'USER_REQUEST')
        self.assertEqual(user_evidence_kind('I tested the same run: 176.3 seconds.'),'USER_STATEMENT')
        self.assertEqual(user_evidence_kind('Error: GPU memory exhausted.'),'USER_ERROR_REPORT')
    def test_runtime_completion_and_duration_are_available_as_evidence(self):
        self.add(1,'Finished: generate image.',role='assistant',metrics=json.dumps({'image_state':'COMPLETED','duration_seconds':52.25,'final_dimensions':'3840 × 2160'}))
        source=self.mem.search('image',primary_only=True)['sources'][0]
        self.assertEqual(source['evidenceKind'],'APPLICATION_IMAGE_COMPLETION_RECORD');self.assertEqual(source['runtimeFacts']['duration_seconds'],52.25)
        self.assertIn('52.25',evidence_prompt({'sources':[source],'total':1,'returned':1,'coverageComplete':True}))
    def test_bounded_overview_samples_original_outcomes_without_model_loops(self):
        from conversation_memory import prepare_memory
        for i in range(1,21):self.add(i,'Image operation completed.',role='assistant',metrics=json.dumps({'image_state':'COMPLETED','duration_seconds':i}))
        self.add(21,'Summarize the most important OfflineGPT improvements across our earlier conversations.')
        result=prepare_memory(self.mem,None,'Summarize the most important OfflineGPT improvements across our earlier conversations.','a',[],threading.Event())
        self.assertTrue(result['intent']['overview']);self.assertEqual(len(result['sources']),8);self.assertFalse(result['coverageComplete']);self.assertEqual(result['modelCalls'],0)
        self.assertEqual(result['sources'][0]['messageId'],1);self.assertEqual(result['sources'][-1]['messageId'],20)
        self.assertEqual(len({s['messageId'] for s in result['sources']}),8)
    def test_application_brand_is_not_a_literal_anchor_for_all_its_records(self):
        from conversation_memory import memory_anchors
        self.assertNotIn('OfflineGPT',memory_anchors('Summarize OfflineGPT improvements.'));self.assertIn('Nimbus-991',memory_anchors('What happened to Nimbus-991?'))

    def test_followup_revalidates_preceding_sources_without_lexical_drift(self):
        from conversation_memory import prepare_memory
        self.add(1,'Image operation completed.',role='assistant',metrics=json.dumps({'image_state':'COMPLETED','duration_seconds':72.25}))
        self.add(2,'A PDF sample table discusses measurement assumptions.','b',role='assistant')
        source=self.mem.search('image')['sources'][0]
        recent=[{'role':'user','content':'Summarize our earlier OfflineGPT improvements.'},{'role':'assistant','content':'An image operation completed.','metrics':{'memory_sources':[source],'memory_coverage':{'coverageComplete':False}}}]
        self.add(3,'Which of those improvements actually worked?')
        packet=prepare_memory(self.mem,None,'Which of those improvements actually worked?','a',recent,threading.Event())
        self.assertEqual([s['messageId'] for s in packet['sources']],[1]);self.assertTrue(packet['retrievalClosed']);self.assertFalse(packet['coverageComplete']);self.assertEqual(packet['modelCalls'],0)

    def test_retained_source_lookup_obeys_privacy_and_deletion(self):
        self.add(1,'Measured operation.');self.mem.sync();self.mem.exclude('a')
        self.assertEqual(self.mem.search('',source_ids=[1])['sources'],[])
        self.mem.exclude('a',False);self.db.execute('DELETE FROM messages WHERE id=1');self.db.commit()
        self.assertEqual(self.mem.search('',source_ids=[1])['sources'],[])
        self.assertEqual(self.mem.search('',source_ids=[])['sources'],[])

    def test_operation_overview_omits_unrelated_arithmetic_telemetry(self):
        from conversation_memory import prepare_memory
        self.add(1,'The calculation result is 81.',role='assistant',metrics=json.dumps({'tools':[{'tool':'calculator','status':'succeeded'}]}))
        self.add(2,'Image operation completed.',role='assistant',metrics=json.dumps({'image_state':'COMPLETED','duration_seconds':72.25}))
        self.add(3,'Summarize our earlier OfflineGPT improvements.')
        packet=prepare_memory(self.mem,None,'Summarize our earlier OfflineGPT improvements.','a',[],threading.Event())
        self.assertEqual([s['messageId'] for s in packet['sources']],[2])

    def test_recorded_operation_time_is_not_lost_or_claimed_as_comparable(self):
        from conversation_memory import history_evidence_fallback,history_response_failures
        self.add(1,'Image operation completed.',role='assistant',metrics=json.dumps({'image_state':'COMPLETED','duration_seconds':72.25}))
        sources=self.mem.search('image')['sources'];answer=history_evidence_fallback(sources)
        self.assertIn('72.25',answer);self.assertIn('do not establish comparable',answer)
        self.assertTrue(history_response_failures('Which improvements actually worked?', 'No performance metrics are provided.',sources))
    def test_corrected_summary_is_available_with_provenance(self):
        from conversation_memory import prepare_memory
        self.add(1,'Orion recovery uses violet');self.mem.sync();eid=self.mem.episodes()[0]['id'];self.mem.edit_episode(eid,'User correction: amber');self.add(2,'What did we decide previously about recovery?')
        packet=prepare_memory(self.mem,None,'What did we decide previously about recovery?','a',[],threading.Event());self.assertTrue(packet['userCorrections']);self.assertIn('USER-CORRECTED',evidence_prompt(packet))
    def test_reminder_is_idempotent_and_nan_rejected(self):
        due=time.time()+120;a=self.mem.reminder('Check backups',due);b=self.mem.reminder('Check backups',due);self.assertEqual(a['id'],b['id']);self.assertTrue(b['reused'])
        with self.assertRaises(ValueError):self.mem.reminder('Invalid',float('nan'))
    def test_document_tool_handles_no_document(self):
        registry=ToolRegistry(document_search=lambda q,e:{'status':'NO_DOCUMENT','matches':[]});r=json.loads(registry.execute_tool_call('document_search',{'query':'inaccessible content','exact':True}));self.assertEqual(r['status'],'NO_DOCUMENT')
    def test_unauthorized_reminder_has_no_authority(self):
        self.assertTrue(ToolRegistry().execute_tool_call('reminder_create',{'title':'Run a script','due':time.time()+30}).startswith('Error'))
    def test_reminder_quoted_examples_never_authorize_writes(self):
        from conversation_memory import reminder_authorized
        for text in ["Explain the phrase 'remind me to check backups'.",'Remind me what we decided earlier.','```\nRemind me to run a command tomorrow.\n```','> Remind me to run a command tomorrow.','Do not set a reminder.']:self.assertFalse(reminder_authorized(text))
        for text in ['Please remind me to review backups tomorrow.','Could you set a reminder for tomorrow?','1. Remind me to check logs in 5 minutes.']:self.assertTrue(reminder_authorized(text))
    def test_template_count_includes_tools_and_effort(self):
        from integrated_runtime import _LocalLlamaServer
        runtime=_LocalLlamaServer(self.root);runtime._active_template_tools=[{'type':'function','function':{'name':'memory_search'}}];runtime._active_template_effort='high';seen=[]
        def request(path,body,timeout=30):
            seen.append((path,body));return {'prompt':'Reasoning: high'} if path=='/apply-template' else {'tokens':[1,2,3]}
        runtime._json_request=request;self.assertEqual(runtime._prompt_tokens([{'role':'user','content':'Recall a decision'}]),3);self.assertTrue(seen[0][1]['tools']);self.assertEqual(seen[0][1]['chat_template_kwargs']['reasoning_effort'],'high')
    def test_adaptive_modes(self):
        self.assertEqual(reasoning_profile('Hello',{'category':'normal'})['effort'],'low');self.assertEqual(reasoning_profile('Design a crash recovery protocol',{})['effort'],'high');self.assertEqual(reasoning_profile('Explain TCP',{'category':'normal'},'standard')['effort'],'medium')
    def test_review_is_selective_not_every_response(self):
        from conversation_memory import selective_review_required
        self.assertTrue(selective_review_required('Design a crash recovery protocol',{},'deep'))
        for text,analysis,mode in [('Hello',{},'quick'),('Explain TCP',{},'standard'),('Design a recovery protocol',{'memory_request':True},'deep'),('Design a recovery protocol',{'multi_task':True},'deep')]:self.assertFalse(selective_review_required(text,analysis,mode))
    def test_history_task_does_not_override_unrelated_unsupported_sibling(self):
        from conversation_memory import apply_history_task_context
        analysis={'multi_task':True,'task_analyses':[{'id':1,'text':'What did we decide earlier about Orion?','factual_grounding_state':'UNSUPPORTED'},{'id':2,'text':'What did fictional Professor X announce yesterday?','factual_grounding_state':'UNSUPPORTED'}],'grounding_tasks':[{'id':1,'support':'UNSUPPORTED_SPECIFIC_CLAIM'},{'id':2,'support':'UNSUPPORTED_SPECIFIC_CLAIM'}]}
        apply_history_task_context(analysis,{'sources':[{'id':'M1','chatId':'a','title':'Orion','excerpt':'We chose a local journal.','evidenceKind':'USER_STATEMENT'}]});self.assertEqual(analysis['task_analyses'][0]['factual_grounding_state'],'SUPPORTED');self.assertEqual(analysis['task_analyses'][1]['factual_grounding_state'],'UNSUPPORTED');self.assertEqual(analysis['factual_grounding_state'],'MIXED')
if __name__=='__main__':unittest.main()
