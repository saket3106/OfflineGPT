"""Independent bounded trace criteria; never execute model SQL/Python."""
import sqlite3
import unittest
from unittest.mock import patch

from code_engine import (verify_transaction_trace, savepoint_scope_failures,
                         ambiguous_commit_failures, engineering_guarantee_failures)

def op(operation, **fields):return dict(op=operation, **fields)
def write(name, outcome='applied'):return op('write',effect=name,outcome=outcome)
def commit(outcome='success'):return op('commit',outcome=outcome)

PROMPT='Explain a safe SQLite debit and credit transaction failure policy using a SAVEPOINT.'
BAD='''1. Begin transaction.
2. Debit the source.
3. SAVEPOINT unit_of_work.
4. Attempt credit.
5. If credit fails, ROLLBACK TO unit_of_work.
6. Commit the outer transaction.

The transfer remains consistent with the debit.'''
GOOD='''1. Begin transaction.
2. SAVEPOINT unit_of_work.
3. Debit the source.
4. Attempt credit.
5. If credit fails, ROLLBACK TO unit_of_work.
6. Commit the outer transaction.

The transfer remains consistent: both writes are undone.'''

class TransactionTraceTests(unittest.TestCase):
    def verify(self, events, groups=None):return verify_transaction_trace(events,groups or [['left','right']])

    def test_late_savepoint_exposes_incomplete_pair(self):
        r=self.verify([op('begin'),write('left'),op('savepoint',name='local'),write('right','statement_aborted'),op('rollback_to',name='local'),commit()])
        self.assertEqual(r['status'],'PROVEN_INCONSISTENT');self.assertEqual(r['committedEffects'],['left'])
        self.assertEqual(r['violations'][0]['incompleteGroups'],[['left','right']])

    def test_early_savepoint_restores_entire_pair(self):
        r=self.verify([op('begin'),op('savepoint',name='whole'),write('left'),write('right','statement_aborted'),op('rollback_to',name='whole'),commit()])
        self.assertEqual(r['status'],'PROVEN_CONSISTENT');self.assertEqual(r['committedEffects'],[])

    def test_whole_rollback_and_success(self):
        for events in ([op('begin'),write('left'),write('right','statement_aborted'),op('rollback')], [op('begin'),write('left'),write('right'),commit()]):
            self.assertEqual(self.verify(events)['status'],'PROVEN_CONSISTENT')

    def test_statement_abort_does_not_undo_earlier_write(self):
        self.assertEqual(self.verify([op('begin'),write('left'),write('right','statement_aborted'),commit()])['status'],'PROVEN_INCONSISTENT')

    def test_explicit_transaction_rollback_outcome(self):
        self.assertEqual(self.verify([op('begin'),write('left'),write('right','transaction_rolled_back')])['status'],'PROVEN_CONSISTENT')

    def test_inner_release_is_not_commit(self):
        r=self.verify([op('begin'),op('savepoint',name='inner'),write('left'),op('release',name='inner'),op('rollback')])
        self.assertEqual(r['status'],'PROVEN_CONSISTENT');self.assertEqual(r['boundaries'],[])

    def test_outermost_release_is_commit(self):
        r=self.verify([op('savepoint',name='root'),write('left'),op('release',name='root',outcome='success')])
        self.assertEqual(r['status'],'PROVEN_INCONSISTENT')

    def test_duplicate_names_use_innermost_mark(self):
        r=self.verify([op('begin'),op('savepoint',name='same'),write('left'),op('savepoint',name='SAME'),write('right'),op('rollback_to',name='same'),commit()])
        self.assertEqual(r['committedEffects'],['left']);self.assertEqual(r['status'],'PROVEN_INCONSISTENT')

    def test_rollback_to_keeps_target_and_discards_younger_marks(self):
        r=self.verify([op('begin'),op('savepoint',name='older'),write('left'),op('savepoint',name='younger'),write('right'),op('rollback_to',name='older'),op('rollback_to',name='older'),commit()])
        self.assertEqual(r['status'],'PROVEN_CONSISTENT')
        r=self.verify([op('begin'),op('savepoint',name='older'),op('savepoint',name='younger'),op('rollback_to',name='older'),op('rollback_to',name='younger')])
        self.assertEqual(r['status'],'UNKNOWN')

    def test_busy_commit_can_be_retried_without_replaying_writes(self):
        self.assertEqual(self.verify([op('begin'),write('left'),write('right'),commit('busy'),commit()])['status'],'PROVEN_CONSISTENT')
        self.assertEqual(self.verify([op('begin'),write('left'),write('right'),commit('busy')])['status'],'UNKNOWN')

    def test_unknown_commit_preserves_both_possible_durable_outcomes(self):
        r=self.verify([op('begin'),write('left'),write('right'),commit('unknown')])
        self.assertEqual(r['status'],'UNKNOWN');self.assertTrue(r['retryRequiresReconciliation'])
        self.assertEqual(r['possibleCommittedEffects'],[[],['left','right']])

    def test_rollback_after_unknown_cannot_prove_commit_did_not_happen(self):
        r=self.verify([op('begin'),write('left'),write('right'),commit('unknown'),op('rollback')])
        self.assertEqual(r['status'],'UNKNOWN');self.assertEqual(len(r['possibleCommittedEffects']),2)

    def test_no_outcome_or_unknown_failure_scope_is_unknown(self):
        for event in (op('write',effect='left'),write('left','io_error'),op('commit'),commit('error')):
            with self.subTest(event=event):self.assertEqual(self.verify([op('begin'),event])['status'],'UNKNOWN')

    def test_no_automatic_idempotency_inference(self):
        self.assertEqual(self.verify([op('begin'),write('left'),write('left')])['status'],'UNKNOWN')

    def test_control_errors_are_not_success(self):
        for events in ([op('begin'),op('begin')],[op('begin'),op('rollback_to',name='absent')],[write('left')], [op('savepoint',name='root'),op('release',name='root')]):
            with self.subTest(events=events):self.assertEqual(self.verify(events)['status'],'UNKNOWN')

    def test_multiple_commits_check_every_visibility_boundary(self):
        r=self.verify([op('begin'),write('left'),commit(),op('begin'),write('right'),commit()])
        self.assertEqual(r['status'],'PROVEN_INCONSISTENT');self.assertEqual(len(r['violations']),1)

    def test_unrelated_effects_and_multiple_groups(self):
        r=self.verify([op('begin'),write('unrelated'),write('alpha'),write('beta'),commit()],[['alpha','beta'],['gamma','delta']])
        self.assertEqual(r['status'],'PROVEN_CONSISTENT')

    def test_adversarial_payloads_and_bounds_are_not_executed(self):
        with patch('builtins.eval',side_effect=AssertionError('eval forbidden')),patch('builtins.exec',side_effect=AssertionError('exec forbidden')):
            for events,groups in (([op('execute',sql='DROP TABLE private')],[['left','right']]),([op('begin',connection='private')],[['left','right']]),([op('begin')]*129,[['left','right']]),([op('begin')],[]),([op('begin')],[['left','left']]),([op('begin')],[['left',object()]]),([{'op':[]}],[['left','right']])):
                self.assertEqual(verify_transaction_trace(events,groups)['status'],'UNKNOWN')

    def test_trusted_sqlite_oracle_independent_of_trace_implementation(self):
        # Only test-authored fixed SQL and bound parameters, never model output.
        cases=[
            [op('begin'),write('left'),op('savepoint',name='s'),write('right','statement_aborted'),op('rollback_to',name='s'),commit()],
            [op('begin'),op('savepoint',name='s'),write('left'),write('right','statement_aborted'),op('rollback_to',name='s'),commit()],
            [op('begin'),op('savepoint',name='s'),write('left'),op('savepoint',name='s'),write('right'),op('rollback_to',name='s'),commit()],
            [op('begin'),op('savepoint',name='s'),write('left'),op('release',name='s'),op('rollback')],
            [op('savepoint',name='s'),write('left'),write('right'),op('release',name='s',outcome='success')],
        ]
        for events in cases:
            with self.subTest(events=events),sqlite3.connect(':memory:',isolation_level=None) as db:
                db.execute('CREATE TABLE effects (id TEXT PRIMARY KEY)');db.execute("INSERT INTO effects VALUES ('sentinel')")
                for event in events:
                    name=event['op']
                    if name=='write':
                        value=event['effect'] if event['outcome']=='applied' else 'sentinel'
                        if event['outcome']=='applied':db.execute('INSERT INTO effects VALUES (?)',(value,))
                        else:
                            with self.assertRaises(sqlite3.IntegrityError):db.execute('INSERT INTO effects VALUES (?)',(value,))
                    else:
                        # The fixture uses only the single fixed identifier s.
                        commands={'begin':'BEGIN','savepoint':'SAVEPOINT s','rollback_to':'ROLLBACK TO s','release':'RELEASE s','rollback':'ROLLBACK','commit':'COMMIT'}
                        db.execute(commands[name])
                actual={row[0] for row in db.execute("SELECT id FROM effects WHERE id <> 'sentinel'")}
                r=self.verify(events)
                self.assertEqual(set(r['committedEffects']),actual)
                expected='PROVEN_INCONSISTENT' if len(actual & {'left','right'})==1 else 'PROVEN_CONSISTENT'
                self.assertEqual(r['status'],expected)


class ExplanationScopeTests(unittest.TestCase):
    def test_bad_policy_rejected_and_fixed_policy_allowed(self):
        self.assertTrue(savepoint_scope_failures(PROMPT,BAD));self.assertEqual(savepoint_scope_failures(PROMPT,GOOD),[])

    def test_no_blanket_savepoint_or_partial_policy_rejection(self):
        for answer in ('ROLLBACK TO preserves earlier writes. The partial commit is inconsistent.',BAD.replace('remains consistent','is inconsistent'),BAD.replace('remains consistent','is not consistent')):
            self.assertEqual(savepoint_scope_failures(PROMPT,answer),[])
        self.assertEqual(savepoint_scope_failures('Explain savepoints.',BAD),[])
        self.assertEqual(savepoint_scope_failures(PROMPT+' Explicitly allow a partial commit.',BAD),[])

    def test_unknown_step_is_never_certified(self):
        self.assertEqual(savepoint_scope_failures(PROMPT,BAD.replace('Debit the source.','Call the external payment provider.')),[])

    def test_positive_claim_in_other_policy_does_not_leak(self):
        answer=BAD.replace('The transfer remains consistent with the debit.','The partial operation is inconsistent.')+'\n\nAlternative policy:\n'+GOOD
        self.assertEqual(savepoint_scope_failures(PROMPT,answer),[])

    def test_code_literals_and_ordinary_answers_unaffected(self):
        self.assertEqual(savepoint_scope_failures(PROMPT,'Example text:\n```text\n'+BAD+'\n```'),[])
        self.assertEqual(engineering_guarantee_failures('What is TCP?',BAD),[])

    def test_unrelated_negation_does_not_exempt_positive_safety_claim(self):
        self.assertTrue(savepoint_scope_failures(PROMPT,BAD.replace('The transfer remains consistent','This is not slow; the transfer remains consistent')))
        prompt='A SQLite transaction COMMIT timed out with unknown acknowledgement.'
        self.assertTrue(ambiguous_commit_failures(prompt,'This is not expensive and safely retry the entire transaction.'))
        self.assertTrue(ambiguous_commit_failures(prompt,'The caller is unknown but COMMIT definitely failed.'))

    def test_ambiguous_commit_replay_overclaim_rejected(self):
        prompt='A SQLite transaction COMMIT timed out with an unknown acknowledgement.'
        self.assertTrue(ambiguous_commit_failures(prompt,'The COMMIT definitely failed and rolled back.'))
        self.assertTrue(ambiguous_commit_failures(prompt,'Safely retry the entire transaction.'))
        self.assertFalse(ambiguous_commit_failures(prompt,'Do not replay blindly; reconcile the durable operation ID and idempotency policy.'))

    def test_busy_and_explicitly_conditional_retry_are_distinct(self):
        self.assertEqual(ambiguous_commit_failures('SQLite COMMIT returned SQLITE_BUSY.','The transaction is still active; retry COMMIT later.'),[])
        prompt='SQLite COMMIT has an unknown outcome.'
        self.assertEqual(ambiguous_commit_failures(prompt,'We cannot assume the COMMIT definitely failed.'),[])
        self.assertEqual(ambiguous_commit_failures(prompt,'If verified durable evidence shows no commit, safely retry the entire transaction.'),[])

    def test_existing_engineering_hook_includes_new_check(self):
        self.assertTrue(engineering_guarantee_failures(PROMPT,BAD))


class ControllerScopeTests(unittest.TestCase):
    def test_bounded_correction_reuses_existing_controller(self):
        from test_tool_workflow import ToolWorkflowTests,ScriptedEngine
        engine=ScriptedEngine([BAD,GOOD])
        _,done,failed,metrics=ToolWorkflowTests().run_worker(engine,(),PROMPT,retries=1)
        self.assertFalse(failed);self.assertTrue(done);self.assertIn('both writes are undone',done[0])
        self.assertEqual(len(engine.requests),2);self.assertEqual(metrics[-1]['validation_status'],'passed')

    def test_unresolved_policy_is_partial_not_a_success_certificate(self):
        from test_tool_workflow import ToolWorkflowTests,ScriptedEngine
        _,done,failed,metrics=ToolWorkflowTests().run_worker(ScriptedEngine([BAD]),(),PROMPT,retries=0)
        self.assertFalse(failed);self.assertTrue(done);self.assertNotIn('remains consistent',done[0])
        self.assertEqual(metrics[-1]['validation_status'],'partial')

    def test_arithmetic_sibling_survives_bad_policy(self):
        from test_tool_workflow import ToolWorkflowTests,ScriptedEngine
        prompt='1. Calculate 8 * 7.\n2. '+PROMPT
        bad='2. Proposed failure path:\n'+ '\n'.join('   '+line for line in BAD.splitlines())
        engine=ScriptedEngine([bad]*6)
        _,done,failed,metrics=ToolWorkflowTests().run_worker(engine,(),prompt,retries=0)
        self.assertFalse(failed);self.assertTrue(done);self.assertIn('56',done[0])
        self.assertEqual(metrics[-1]['validation_status'],'partial')
        self.assertLessEqual(len(engine.requests),4)

    def test_nested_response_steps_do_not_steal_sibling_ids(self):
        from validators import parse_numbered_response_sections
        text='2. Policy:\n   1. First write.\n   2. Second write.\n\n1. Arithmetic result.'
        sections=parse_numbered_response_sections(text,[1,2])
        self.assertEqual(sections[1]['body'],'Arithmetic result.')
        self.assertIn('Second write.',sections[2]['body'])

    def test_dotted_leaf_ids_survive_indentation(self):
        from validators import parse_numbered_response_sections
        sections=parse_numbered_response_sections('1. Root answer.\n  2.A. Leaf A.\n  2.B. Leaf B.',[1,'2.A','2.B'])
        self.assertEqual(set(sections),{1,'2.A','2.B'})

if __name__=='__main__':unittest.main()
