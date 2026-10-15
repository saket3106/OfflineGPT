"""Language and persistence counterexamples independent of model keyword scores."""
import sqlite3
import unittest
from code_engine import engineering_guarantee_failures,engineering_review_focus,python_default_timing_failures


class DefaultTimingTests(unittest.TestCase):
    def test_definition_evaluates_default_before_generator_call(self):
        # Authored fixture, never model-generated execution.
        events=[]
        def fresh():events.append('default');return []
        def stream(items,cache=fresh()):
            events.append('body')
            yield from items
        self.assertEqual(events,['default'])
        pending=stream([3])
        self.assertEqual(events,['default'])
        self.assertEqual(next(pending),3)
        self.assertEqual(events,['default','body'])

    def test_first_call_default_claim_rejected_for_unseen_signatures(self):
        for signature in ('def batch(values, collected=[]):','def memo(key, mapping=dict()):','def collect(*, data=set()):'):
            with self.subTest(signature=signature):
                prompt='Debug this Python function: '+signature+'\n    return data'
                self.assertTrue(python_default_timing_failures(prompt,'The default argument is created on the first call.'))
                self.assertTrue(engineering_guarantee_failures(prompt,'The default value is evaluated when first called.'))
                self.assertTrue(engineering_guarantee_failures(prompt,'When the function is called the first time, the default list is created and used.'))

    def test_correct_explanations_and_negation_allowed(self):
        prompt='Explain Python: def gather(items, buffer=[]):\n    yield from items'
        for answer in ('The default is created at definition time, not the first call.',
                       'The default is not created on the first call.',
                       'Incorrect claim: The default argument is evaluated on the first call.',
                       'With None, the list is created on the first call in the corrected non-generator version.',
                       'The default expression runs at definition time; the generator body starts when iterated.'):
            self.assertEqual(python_default_timing_failures(prompt,answer),[])

    def test_constant_default_and_unrelated_claim_not_gated(self):
        self.assertEqual(python_default_timing_failures('def keep(item, cache=None):\n    return item','The list is created on the first call.'),[])
        self.assertEqual(python_default_timing_failures('Explain a list.','The list is created on the first call.'),[])

    def test_code_literals_do_not_become_endorsed_claims(self):
        self.assertEqual(python_default_timing_failures('def keep(item, cache=[]):','```python\nprint("The default is created on the first call.")\n```'),[])

    def test_unrelated_negation_does_not_exempt_false_timing(self):
        self.assertTrue(python_default_timing_failures('def keep(item, cache=[]):','The default is created on the first call and is not cleared later.'))

    def test_runtime_retries_false_timing_without_discarding_request(self):
        from app import ChatWorker
        from intelligence import analyze_request
        prompt='Explain this Python function: def collect(items, cache=[]):\n    yield from items'
        class Engine:
            cfg={}
            def __init__(self):self.calls=0
            def ensure_chat(self,*args,**kwargs):return 'local'
            def chat_completion(self,*args,**kwargs):
                self.calls+=1
                return ('The default argument is created on the first call.' if self.calls==1 else
                        'The default expression runs when the function definition executes. Its generator body waits for iteration.')
        engine=Engine();analysis=analyze_request(prompt);analysis['max_retries']=1
        worker=ChatWorker('local',[{'role':'user','content':prompt}],engine,analysis)
        done=[];failed=[];worker.done.connect(done.append);worker.failed.connect(failed.append);worker.run()
        self.assertEqual(failed,[]);self.assertEqual(engine.calls,2)
        self.assertIn('definition executes',done[0])


class PersistenceCounterexampleTests(unittest.TestCase):
    def test_savepoint_scope_position_changes_surviving_state(self):
        # A general invariant exercised using different tables/values, no
        # execution of SQL from a model answer or any user database.
        for checkpoint_first in (False,True):
            with self.subTest(checkpoint_first=checkpoint_first),sqlite3.connect(':memory:') as db:
                db.executescript('CREATE TABLE stock(q INTEGER); INSERT INTO stock VALUES(53); CREATE TABLE orders(id INTEGER UNIQUE); INSERT INTO orders VALUES(1);')
                db.execute('BEGIN')
                if checkpoint_first:db.execute('SAVEPOINT scope')
                db.execute('UPDATE stock SET q=q-7')
                if not checkpoint_first:db.execute('SAVEPOINT scope')
                with self.assertRaises(sqlite3.IntegrityError):db.execute('INSERT INTO orders VALUES(1)')
                db.execute('ROLLBACK TO scope');db.commit()
                self.assertEqual(db.execute('SELECT q FROM stock').fetchone()[0],53 if checkpoint_first else 46)

    def test_review_demands_operation_scope_and_publication_enforcement(self):
        focus=engineering_review_focus('Review SQLite SAVEPOINT behavior and stale workers after lease expiry.')
        self.assertIn('relative to every required write',focus)
        self.assertIn('publication itself',focus)
        self.assertNotIn('wallet',focus)

    def test_multi_artifact_review_considers_old_readers(self):
        focus=engineering_review_focus('Design a concurrent manifest backup protocol.')
        self.assertIn('old manifest',focus)
        self.assertIn('one validated generation',focus)


if __name__=='__main__':unittest.main()
