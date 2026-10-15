import copy
import hashlib
import unittest
from semantic_acceptance import assess_record


class SemanticAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.case={'id':'unseen-transfer','input':'Test input.','criteria':[{'id':'scope'},{'id':'retry'}]}
        self.record={'caseId':self.case['id'],'kind':'genuine_model','answer':'A bounded answer.',
                     'answerSha256':hashlib.sha256(b'A bounded answer.').hexdigest(),
                     'candidateSha256':hashlib.sha256(b'fixture-candidate').hexdigest(),'modelIdentity':'fixture-model','isolatedProfile':'synthetic',
                     'inputSha256':hashlib.sha256(b'Test input.').hexdigest(),'modelCalls':1,'firstVisibleSeconds':.1,'totalSeconds':.3,
                     'contextFitting':{'contextShortened':False},'resources':{'available':False,'reason':'Test fixture, not a benchmark.'},
                     'sourceEvidence':[],'independentReview':{'reviewer':'test-authored oracle','criteria':[
                         {'id':'scope','status':'PASS','basis':'Explicit state trace checked against independent oracle.'},
                         {'id':'retry','status':'PASS','basis':'Unknown outcome is kept unknown.'}]}}

    def assess(self):return assess_record(self.case,self.record)

    def test_complete_record(self):self.assertEqual(self.assess()['status'],'PASS')
    def test_runtime_pass_cannot_override_semantic_failure(self):
        self.record['runtimeValidation']='passed';self.record['independentReview']['criteria'][1]['status']='FAIL'
        self.assertEqual(self.assess()['status'],'FAIL')
    def test_missing_criterion_is_not_success(self):
        self.record['independentReview']['criteria'].pop()
        self.assertEqual(self.assess()['status'],'INCOMPLETE');self.assertEqual(self.assess()['missingCriteria'],['retry'])
    def test_unknown_criterion_is_not_success(self):
        self.record['independentReview']['criteria'][1]['status']='UNKNOWN'
        self.assertEqual(self.assess()['status'],'INCOMPLETE')
    def test_mocks_never_prove_genuine_inference(self):
        self.record['kind']='mock';self.assertEqual(self.assess()['status'],'INVALID')
    def test_wrong_answer_fingerprint_is_invalid(self):
        self.record['answer']='Replaced answer';self.assertEqual(self.assess()['status'],'INVALID')
    def test_wrong_input_or_candidate_fingerprint_is_invalid(self):
        self.case['input']='Different prompt';self.assertEqual(self.assess()['status'],'INVALID')
        self.case['input']='Test input.';self.record['candidateSha256']='not-a-hash';self.assertEqual(self.assess()['status'],'INVALID')
    def test_missing_answer_is_invalid(self):
        self.record['answer']='';self.assertEqual(self.assess()['status'],'INVALID')
    def test_negative_nan_boolean_or_reversed_timing_is_invalid(self):
        original=copy.deepcopy(self.record)
        for first,total in ((-1,1),(float('nan'),1),(.5,.2),(True,1)):
            self.record=copy.deepcopy(original);self.record.update(firstVisibleSeconds=first,totalSeconds=total)
            self.assertEqual(self.assess()['status'],'INVALID')
    def test_no_actual_calls_is_invalid(self):
        for value in (0,True,None):
            self.record['modelCalls']=value;self.assertEqual(self.assess()['status'],'INVALID')
    def test_duplicate_or_unknown_decisions_invalid(self):
        item=copy.deepcopy(self.record['independentReview']['criteria'][0]);self.record['independentReview']['criteria'].append(item)
        self.assertEqual(self.assess()['status'],'INVALID')
        item['id']='undeclared';self.assertEqual(self.assess()['status'],'INVALID')
    def test_unsubstantiated_pass_invalid(self):
        self.record['independentReview']['criteria'][0]['basis']=''
        self.assertEqual(self.assess()['status'],'INVALID')
    def test_safety_stop_cannot_be_a_pass(self):
        self.record['safetyStop']='RAM floor';self.assertEqual(self.assess()['status'],'SAFETY_STOP')
    def test_missing_context_and_resource_instrumentation_invalid(self):
        del self.record['contextFitting'];self.record['resources']={'available':False}
        self.assertEqual(self.assess()['status'],'INVALID')
    def test_resource_availability_without_samples_is_invalid(self):
        self.record['resources']={'available':True};self.assertEqual(self.assess()['status'],'INVALID')
        self.record['resources'].update(samples=1,minimumAvailableRamBytes=128*1024**2)
        self.assertEqual(self.assess()['status'],'SAFETY_STOP')
    def test_duplicate_expectations_invalid(self):
        self.case['criteria'].append({'id':'scope'});self.assertEqual(self.assess()['status'],'INVALID')
    def test_malformed_data_is_not_silently_accepted(self):
        self.assertEqual(assess_record([],self.record)['status'],'INVALID')
        self.record['independentReview']['criteria']=[{'id':[],'status':'PASS'}]
        self.assertEqual(self.assess()['status'],'INVALID')

if __name__=='__main__':unittest.main()
