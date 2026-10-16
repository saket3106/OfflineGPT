"""Independent source/coverage invariants; no model inference or real data."""
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from conversation_memory import (ConversationMemory, evidence_prompt,
    history_evidence_fallback, history_provenance_failures, prepare_memory,
    select_history_evidence, validate_memory_citations)


def source(mid,kind='USER_STATEMENT',excerpt=None):
    return dict(id='M'+str(mid), messageId=mid, chatId='sample', created=mid,
                date='2025-07-04', role='user', evidenceKind=kind,
                excerpt=excerpt or 'We tested the import workflow.',
                url='offlinegpt://chat/sample?message='+str(mid))


class CoverageTests(unittest.TestCase):
    def test_all_eight_records_survive_fallback(self):
        records=[source(i,'APPLICATION_IMAGE_TELEMETRY' if i<7 else 'USER_ERROR_REPORT',
                        'The image completed.' if i<7 else 'Error: decoding failed.') for i in range(1,9)]
        text=history_evidence_fallback(records)
        for item in records:self.assertIn('['+item['id']+']',text)
        self.assertIn('8 of 8',text);self.assertIn('partial evidence view',text)

    def test_sampling_retains_middle_failure_and_conflicting_reports(self):
        records=[source(i,'APPLICATION_IMAGE_TELEMETRY') for i in range(1,41)]
        records[18]=source(19,'USER_ERROR_REPORT','Error: the second run failed.')
        records[21]=source(22,excerpt='We measured 14 seconds.')
        records[27]=source(28,excerpt='We measured 27 seconds with different settings.')
        sampled=select_history_evidence(records,8)
        self.assertTrue({'M19','M22','M28','M1','M40'} <= {s['id'] for s in sampled})
        self.assertEqual(len(sampled),8)
        self.assertEqual([s['created'] for s in sampled],sorted(s['created'] for s in sampled))

    def test_fallback_discloses_omissions_instead_of_claiming_completion(self):
        text=history_evidence_fallback([source(i) for i in range(1,31)])
        self.assertIn('12 of 30',text);self.assertIn('not displayed',text)
        self.assertIn('not a complete search',text)

    def test_duplicate_sources_not_counted_twice(self):
        self.assertEqual(len(select_history_evidence([source(8),source(8)],8)),1)

    def test_prompt_records_exact_context_coverage(self):
        records=[source(1,excerpt='We measured '+'x'*1800),source(2,excerpt='We tested a short run.')]
        packet=dict(sources=records,total=2,returned=2,coverageComplete=True)
        text=evidence_prompt(packet,2200)
        self.assertLessEqual(len(text),2200)
        self.assertEqual(packet['contextSourceIds'],['M2'])
        self.assertEqual(packet['omittedContextSourceIds'],['M1'])
        self.assertFalse(packet['coverageComplete']);self.assertFalse(packet['contextCoverageComplete'])

    def test_corrections_respect_total_context_budget(self):
        packet=dict(sources=[source(1),source(2)],total=2,returned=2,coverageComplete=True,
                    userCorrections=[{'summary':'User supplied correction '*100}])
        for budget in (0,50,1500,2400,6500):
            with self.subTest(budget=budget):
                value=evidence_prompt(dict(packet),budget)
                self.assertLessEqual(len(value),budget)

    def test_complete_context_is_explicit_even_without_truncation(self):
        packet=dict(sources=[source(42),source(47)],returned=2,total=2,coverageComplete=True)
        evidence_prompt(packet)
        self.assertEqual(packet['contextSourceIds'],['M42','M47'])
        self.assertEqual(packet['omittedContextSourceIds'],[])
        self.assertTrue(packet['contextCoverageComplete']);self.assertTrue(packet['coverageComplete'])

    def test_complete_context_does_not_promote_partial_retrieval(self):
        packet=dict(sources=[source(1)],total=10,returned=1,coverageComplete=False)
        evidence_prompt(packet)
        self.assertTrue(packet['contextCoverageComplete']);self.assertFalse(packet['coverageComplete'])

    def test_external_citation_target_is_replaced(self):
        for url in ('https://misleading.example/claim','javascript:alert','offlinegpt://chat/other?message=999'):
            text,invalid=validate_memory_citations('Measured run [M42]('+url+').',[source(42)])
            self.assertEqual(invalid,[]);self.assertNotIn(url,text)
            self.assertIn('[M42](offlinegpt://chat/sample?message=42)',text)

    def test_unknown_source_target_cannot_survive_under_other_label(self):
        text,_=validate_memory_citations('[Original](offlinegpt://chat/other?message=999)',[source(42)])
        self.assertEqual(text,'[source unavailable]')

    def test_known_target_label_is_runtime_derived(self):
        text,_=validate_memory_citations('[Original](offlinegpt://chat/sample?message=42)',[source(42)])
        self.assertEqual(text,'[M42](offlinegpt://chat/sample?message=42)')

    def test_conflicting_dates_cannot_be_borrowed_from_other_source(self):
        a=source(1);b=source(2);b['date']='2025-07-05'
        self.assertTrue(history_provenance_failures('The test ran on 2025-07-05 [M1].',[a,b]))
        self.assertEqual(history_provenance_failures('The test ran on 2025-07-05 [M2].',[a,b]),[])

    def test_uncited_second_date_cannot_borrow_global_evidence(self):
        a=source(1);b=source(2);b['date']='2025-07-05'
        answer='The first trial ran on 2025-07-04 [M1]. The next trial ran on 2025-07-05.'
        self.assertTrue(history_provenance_failures(answer,[a,b]))
        self.assertEqual(history_provenance_failures(answer+' [M2]',[a,b]),[])

    def test_uncertainty_in_another_claim_cannot_exempt_uncited_date(self):
        a=source(1);b=source(2);b['date']='2025-07-05'
        answer='I cannot verify an unrelated announcement. The first trial ran on 2025-07-04 [M1]. The next trial ran on 2025-07-05.'
        self.assertTrue(history_provenance_failures(answer,[a,b]))

    def test_multiline_dates_require_their_own_original_binding(self):
        a=source(1);b=source(2);b['date']='2025-07-05'
        answer='First trial: 2025-07-04 [M1]\nSecond trial: 2025-07-05'
        self.assertTrue(history_provenance_failures(answer,[a,b]))
        self.assertEqual(history_provenance_failures(answer+' [M2]',[a,b]),[])

    def test_trailing_markdown_citation_binds_only_same_line(self):
        a=source(1);b=source(2);b['date']='2025-07-05'
        valid='The next trial ran on 2025-07-05. [M2](offlinegpt://chat/sample?message=2)'
        self.assertEqual(history_provenance_failures(valid,[a,b]),[])
        invalid='The first trial ran on 2025-07-04 [M1].\nThe next trial ran on 2025-07-05.\n[M2]'
        self.assertTrue(history_provenance_failures(invalid,[a,b]))

    def test_original_record_quote_keeps_verified_source_binding(self):
        a=source(1,excerpt='The trial ran on 2025-07-04.')
        self.assertEqual(history_provenance_failures(history_evidence_fallback([a]),[a]),[])

    def test_quote_cannot_borrow_other_record_or_bind_uncited_prose(self):
        a=source(1,excerpt='The trial ran on 2025-07-04.');b=source(2,excerpt='The trial ran on 2025-07-05.');b['date']='2025-07-05'
        self.assertTrue(history_provenance_failures('[M1]\n\n> The trial ran on 2025-07-05.',[a,b]))
        self.assertTrue(history_provenance_failures('[M1]\n\nThe trial ran on 2025-07-04.',[a,b]))

    def test_individual_operation_does_not_prove_identical_settings(self):
        a=source(1,'APPLICATION_IMAGE_TELEMETRY');a['runtimeFacts']={'duration_seconds':13.25}
        self.assertTrue(history_provenance_failures('Settings were identical [M1].',[a]))
        self.assertEqual(history_provenance_failures('The operation took 13.25 seconds [M1].',[a]),[])


class FollowupCoverageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.db=sqlite3.connect(self.root/'history.sqlite3',check_same_thread=False)
        self.db.executescript("CREATE TABLE chats(id TEXT PRIMARY KEY,title TEXT,updated REAL);CREATE TABLE messages(id INTEGER PRIMARY KEY,chat_id TEXT,role TEXT,content TEXT,created REAL,attachment TEXT,metrics TEXT);CREATE TABLE chat_trash(chat_id TEXT PRIMARY KEY,deleted REAL);CREATE TABLE library_images(id TEXT,path TEXT,user_prompt TEXT,chat_id TEXT,deleted INTEGER);INSERT INTO chats VALUES('sample','Experiment',1);INSERT INTO chats VALUES('other','Other experiment',2);")
        self.mem=ConversationMemory(self.db,threading.RLock(),self.root)
        for mid,cid in ((1,'sample'),(2,'other')):
            self.db.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?)',(mid,cid,'user','We measured the import workflow.',1700000000+mid,'','{}'))
        self.db.execute("INSERT INTO messages VALUES(3,'sample','user','Which of those changes worked?',1700000003,'','{}')");self.db.commit()
        self.originals=self.mem.search('',timeline=True,source_ids=[1,2])['sources']
        self.recent=[{'role':'user','content':'Summarize our earlier experiments.'},
                     {'role':'assistant','content':'Two measurements.',
                      'metrics':{'memory_sources':self.originals,'memory_coverage':{'coverageComplete':True}}}]

    def tearDown(self):self.db.close();self.temp.cleanup()

    def prepare(self):
        return prepare_memory(self.mem,None,'Which of those changes worked?','sample',self.recent,threading.Event())

    def test_deleted_original_invalidates_complete_followup(self):
        self.db.execute('DELETE FROM messages WHERE id=2');self.db.commit()
        packet=self.prepare()
        self.assertFalse(packet['coverageComplete']);self.assertEqual(packet['unavailablePreviousSourceIds'],['M2'])
        self.assertEqual([s['id'] for s in packet['sources']],['M1'])

    def test_excluded_original_invalidates_complete_followup(self):
        self.mem.exclude('other')
        packet=self.prepare()
        self.assertFalse(packet['coverageComplete']);self.assertEqual(packet['unavailablePreviousSourceIds'],['M2'])

    def test_unchanged_originals_preserve_complete_followup(self):
        packet=self.prepare()
        self.assertTrue(packet['coverageComplete']);self.assertEqual(packet['unavailablePreviousSourceIds'],[])
        self.assertEqual(packet['modelCalls'],0)

    def test_changed_original_reloaded_instead_of_stale_excerpt(self):
        self.db.execute("UPDATE messages SET content='We measured 31 seconds, correcting the earlier report.' WHERE id=1");self.db.commit()
        packet=self.prepare()
        self.assertIn('31 seconds',next(s['excerpt'] for s in packet['sources'] if s['id']=='M1'))


if __name__=='__main__':unittest.main()
