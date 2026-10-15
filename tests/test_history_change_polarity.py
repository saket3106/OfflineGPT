"""Source polarity regressions independent of model wording/benchmark names."""
import unittest
from conversation_memory import history_provenance_failures

def source(mid,text,kind='USER_STATEMENT'):
    return {'id':'M'+str(mid),'date':'2026-06-03','created':mid,'chatId':'synthetic',
            'evidenceKind':kind,'excerpt':text,'url':'offlinegpt://chat/synthetic?message='+str(mid)}

class HistoryChangePolarityTests(unittest.TestCase):
    def test_denied_change_does_not_support_affirmative_milestone(self):
        for text in ('The parser has not been fixed.','We never updated the parser.','The parser wasn’t fixed.','We have not actually implemented the parser fix.'):
            with self.subTest(text=text):
                self.assertTrue(history_provenance_failures('We fixed the parser [M1].',[source(1,text)]))

    def test_hypothetical_or_future_change_is_not_completed(self):
        for text in ('If we updated the parser, it could help.','We would have fixed the parser.','We will have implemented the parser by next week.'):
            with self.subTest(text=text):self.assertTrue(history_provenance_failures('We updated the parser [M1].',[source(1,text)]))

    def test_unrelated_positive_source_does_not_validate_denied_source(self):
        sources=[source(1,'The parser has not been fixed.'),source(2,'We updated the citation module.')]
        self.assertTrue(history_provenance_failures('We fixed the parser [M1].',sources))
        self.assertEqual(history_provenance_failures('The user reported an updated citation module [M2].',sources),[])

    def test_negative_claim_and_supported_sibling_are_allowed(self):
        sources=[source(1,'The parser has not been fixed.'),source(2,'We updated the citation module.')]
        self.assertEqual(history_provenance_failures('The parser has not been fixed [M1].\nThe user reported an updated citation module [M2].',sources),[])

    def test_unrelated_negation_does_not_cancel_affirmative_report(self):
        for text in ('The parser is not slow; we fixed its validation code.','We have not changed the UI; we updated the parser.','Not every task failed; we fixed the parser.'):
            with self.subTest(text=text):self.assertEqual(history_provenance_failures('The user reported the parser was fixed [M1].',[source(1,text)]),[])

    def test_counterexample_and_request_kinds_not_completion(self):
        for kind,text in (('USER_REQUEST','Please describe the updated parser.'),('ASSISTANT_CLAIM_UNVERIFIED','We fixed the parser.'),('USER_STATEMENT','Counterexample: we fixed the parser.')):
            with self.subTest(kind=kind,text=text):self.assertTrue(history_provenance_failures('We fixed the parser [M1].',[source(1,text,kind)]))

    def test_positive_software_report_remains_usable(self):
        self.assertEqual(history_provenance_failures('The user reported a fixed parser [M1].',[source(1,'We fixed the parser and tested the code.')]),[])

    def test_nonsoftware_update_does_not_support_software_improvement(self):
        self.assertTrue(history_provenance_failures('We updated the parser [M1].',[source(1,'We updated the car paint.')]))

    def run_worker(self,answer):
        from app import ChatWorker
        from intelligence import analyze_request
        from test_tool_workflow import ScriptedEngine
        prompt='Summarize the project improvements we implemented. Cite the original sources.'
        sources=[source(1,'The parser has not been fixed.'),source(2,'We updated the citation module.')]
        analysis=analyze_request(prompt);analysis.update(memory_request=True,memory_sources=sources,max_retries=0,selective_review=False,agent_tools=[])
        engine=ScriptedEngine([answer]);worker=ChatWorker('local',[{'role':'user','content':prompt}],engine,analysis)
        final=[];failed=[];metrics=[];worker.done.connect(final.append);worker.failed.connect(failed.append);worker.metrics.connect(metrics.append);worker.run()
        return final,failed,metrics,engine

    def test_controller_rejects_denied_milestone_even_when_other_change_exists(self):
        final,failed,metrics,engine=self.run_worker('We fixed the parser [M1]. The user reported an updated citation module [M2].')
        self.assertFalse(failed);self.assertTrue(final);self.assertNotIn('We fixed the parser [M1]',final[0])
        self.assertIn('M2',final[0]);self.assertEqual(metrics[-1]['validation_status'],'partial')
        self.assertEqual(len(engine.requests),1)

    def test_controller_preserves_valid_negative_and_positive_siblings(self):
        text='The parser has not been fixed [M1].\nThe user reported an updated citation module [M2].'
        final,failed,metrics,engine=self.run_worker(text)
        self.assertFalse(failed);self.assertTrue(final);self.assertIn('has not been fixed',final[0]);self.assertIn('updated citation module',final[0])
        self.assertEqual(metrics[-1]['validation_status'],'passed');self.assertEqual(len(engine.requests),1)

if __name__=='__main__':unittest.main()
