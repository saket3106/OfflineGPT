"""General generator scope and timing checks; no model-generated execution."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from code_engine import python_function_facts,python_generator_timing_failures,engineering_guarantee_failures


class FunctionScopeTests(unittest.TestCase):
    def kinds(self,text):return {f['name']:f['kind'] for f in python_function_facts(text)}

    def test_generator_and_ordinary_function_scopes(self):
        self.assertEqual(self.kinds('```python\ndef produce(items):\n    yield from items\ndef materialize(items):\n    return list(items)\n```'),{'produce':'generator','materialize':'ordinary'})

    def test_nested_generator_does_not_promote_outer(self):
        text='def wrap(items):\n    def inner():\n        yield from items\n    return inner()'
        self.assertEqual(self.kinds(text),{'wrap':'ordinary','inner':'generator'})
        self.assertFalse(python_generator_timing_failures(text,'Calling wrap executes its body and returns an iterator.'))

    def test_generator_expression_does_not_promote_ordinary_function(self):
        self.assertEqual(self.kinds('def mapped(items):\n    return (x*x for x in items)'),{'mapped':'ordinary'})

    def test_lambda_and_class_bodies_do_not_leak_yield(self):
        text='def wrap():\n    callback = lambda: (yield 2)\n    class Local:\n        def stream(self):\n            yield 3\n    return callback'
        self.assertEqual(self.kinds(text),{'wrap':'ordinary','stream':'generator'})

    def test_nested_default_yield_belongs_to_enclosing_scope(self):
        self.assertEqual(self.kinds('def outer():\n    def inner(value=(yield 1)):\n        return value\n    return inner'),{'outer':'generator','inner':'ordinary'})

    def test_definition_time_annotation_does_not_inherit_checker_future_flags(self):
        # Authored static compiler cross-check; no code object is executed.
        import inspect,types
        text='def outer():\n    def inner(value: (yield 1)):\n        return value\n    return inner'
        try:code=compile(text,'<authored annotation fixture>','exec',dont_inherit=True)
        except SyntaxError:self.skipTest('Runtime uses deferred annotations with different restrictions')
        outer=next(c for c in code.co_consts if isinstance(c,types.CodeType) and c.co_name=='outer')
        self.assertTrue(outer.co_flags & inspect.CO_GENERATOR)
        self.assertEqual(self.kinds(text),{'outer':'generator','inner':'ordinary'})

    def test_async_generator_vs_coroutine(self):
        text='```python\nasync def stream(items):\n    for item in items:\n        yield item\nasync def load():\n    return 4\n```'
        self.assertEqual(self.kinds(text),{'stream':'async_generator','load':'coroutine'})

    def test_multiline_signature_and_sentinel_default(self):
        text='def records(\n    items,\n    seen=None,\n):\n    if seen is None:\n        seen=set()\n    yield from items'
        self.assertEqual(self.kinds(text),{'records':'generator'})

    def test_trailing_instruction_separate_from_inline_listing(self):
        text='Explain this: def records(items):\n    yield from items\nExplain two calls and delayed execution.'
        self.assertEqual(self.kinds(text),{'records':'generator'})

    def test_broken_code_is_not_silently_truncated(self):
        self.assertEqual(self.kinds('def records():\n    yield 1\n    return !!!'),{})
        self.assertEqual(self.kinds('async def records():\n    yield 1\n    return 2'),{})

    def test_dead_yield_still_creates_generator_function(self):
        self.assertEqual(self.kinds('def record():\n    if False:\n        yield 1\n    return 2'),{'record':'generator'})

    def test_comments_and_strings_are_not_yield_expressions(self):
        self.assertEqual(self.kinds('def plain():\n    "yield is a word"\n    # yield 3\n    return 1'),{'plain':'ordinary'})

    def test_parsing_never_evaluates_defaults_or_decorators(self):
        with tempfile.TemporaryDirectory() as temp:
            target=Path(temp)/'must-not-exist'
            code='def sample(arg=__import__("pathlib").Path('+repr(str(target))+').write_text("bad")):\n    yield arg'
            self.assertEqual(self.kinds(code),{'sample':'generator'})
            self.assertFalse(target.exists())

    def test_budgeted_unsupported_snippet_returns_no_facts(self):
        self.assertEqual(python_function_facts('x'*24001),[])
        self.assertEqual(python_function_facts('def unfinished('),[])


class GeneratorTimingTests(unittest.TestCase):
    prompt='Debug Python: def chunks(items, cache=None):\n    if cache is None:\n        cache=[]\n    for value in items:\n        cache.append(value)\n        yield value'

    def test_original_failure_wording_and_unseen_variants(self):
        for answer in ('On the first call, the generator iterates over items.',
                       'When chunks is called, its body runs immediately.',
                       'Calling chunks executes its loop.',
                       'The generator body runs on the first call.',
                       'The generator yields its first item on the first call.'):
            with self.subTest(answer=answer):self.assertTrue(python_generator_timing_failures(self.prompt,answer))

    def test_correct_laziness_sentence_does_not_exempt_false_sibling(self):
        answer='On the first call the generator iterates over items. The body runs only when next is requested.'
        self.assertTrue(python_generator_timing_failures(self.prompt,answer))

    def test_unrelated_negation_does_not_exempt_false_claim(self):
        self.assertTrue(python_generator_timing_failures(self.prompt,'On the first call, the generator body executes and is not cleared later.'))

    def test_denied_iterator_driver_does_not_exempt_false_claim(self):
        for answer in ('On the first call the generator body runs without next being called.',
                       'The generator body runs when chunks is called, without any next call.'):
            with self.subTest(answer=answer):self.assertTrue(python_generator_timing_failures(self.prompt,answer))

    def test_negated_execution_and_counterexamples_remain_allowed(self):
        for answer in ('On the first call the generator body does not execute.',
                       'The generator body does not run when chunks is called.',
                       'Incorrect claim: Calling chunks runs its body.',
                       'The generator body executes on iteration, not on the first call.'):
            with self.subTest(answer=answer):self.assertFalse(python_generator_timing_failures(self.prompt,answer))

    def test_iterator_drivers_are_not_constructor_calls(self):
        for answer in ('On the first call to next, the generator body runs.',
                       'Calling next(chunks(items)) executes the loop until a yield.',
                       'The generator body runs when its send method is called.',
                       'On the first iteration the body starts.'):
            with self.subTest(answer=answer):self.assertFalse(python_generator_timing_failures(self.prompt,answer))

    def test_argument_and_default_evaluation_are_not_body_execution(self):
        for answer in ('On the first call the argument expression runs before the generator object is returned.',
                       'Calling chunks yields a generator object.',
                       'On the first call the generator starts without executing its body.',
                       'Calling chunks constructs an iterator. Its body waits for iteration.'):
            with self.subTest(answer=answer):self.assertFalse(python_generator_timing_failures(self.prompt,answer))

    def test_unrecognized_outer_consumer_not_treated_as_generator_function(self):
        self.assertFalse(python_generator_timing_failures(self.prompt,'Calling consume(chunks(items)) executes the consumer body and iterates the generator.'))

    def test_ordinary_function_with_same_words_not_gated(self):
        self.assertFalse(python_generator_timing_failures('def chunks(items):\n    return list(items)','On the first call the function body executes.'))

    def test_async_generator_call_vs_asynchronous_advancement(self):
        prompt='async def events(items):\n    for item in items:\n        yield item'
        self.assertTrue(python_generator_timing_failures(prompt,'When events is called, the generator body runs.'))
        self.assertFalse(python_generator_timing_failures(prompt,'Calling anext on the async generator returns an awaitable; awaiting it advances the body.'))

    def test_same_name_ambiguous_definitions_not_certified(self):
        text='```python\ndef chunks():\n    yield 1\n```\n```python\ndef chunks():\n    return [1]\n```'
        self.assertFalse(python_generator_timing_failures(text,'Calling chunks runs the body.'))

    def test_other_named_ordinary_function_not_gated(self):
        text='```python\ndef chunks():\n    yield 1\ndef total():\n    return 1\n```'
        self.assertFalse(python_generator_timing_failures(text,'Calling total runs its body.'))

    def test_scope_rule_is_wired_into_runtime_answer_guard(self):
        self.assertTrue(engineering_guarantee_failures(self.prompt,'On the first call the generator iterates over items.'))


class IndependentExecutionFixtures(unittest.TestCase):
    def test_worker_retries_generator_contradiction(self):
        from app import ChatWorker
        from intelligence import analyze_request
        prompt='Explain Python: def batches(items):\n    yield from items'
        class Engine:
            cfg={}
            def __init__(self):self.calls=0
            def ensure_chat(self,*args,**kwargs):return 'local'
            def chat_completion(self,*args,**kwargs):
                self.calls+=1
                return 'On the first call the generator body runs.' if self.calls==1 else 'Calling batches constructs a generator. Its body runs when iterated.'
        engine=Engine();analysis=analyze_request(prompt);analysis['max_retries']=1
        worker=ChatWorker('local',[{'role':'user','content':prompt}],engine,analysis)
        done=[];failed=[];worker.done.connect(done.append);worker.failed.connect(failed.append);worker.run()
        self.assertFalse(failed);self.assertEqual(engine.calls,2);self.assertIn('when iterated',done[0])

    def test_persistent_contradiction_not_returned_as_reliable_design(self):
        from app import ChatWorker
        from intelligence import analyze_request
        prompt='Explain Python: def batches(items):\n    yield from items'
        class Engine:
            cfg={}
            def ensure_chat(self,*args,**kwargs):return 'local'
            def chat_completion(self,*args,**kwargs):return 'On the first call the generator body runs.'
        analysis=analyze_request(prompt);analysis['max_retries']=0
        worker=ChatWorker('local',[{'role':'user','content':prompt}],Engine(),analysis)
        done=[];worker.done.connect(done.append);worker.run()
        self.assertTrue(done);self.assertIn('cannot present it as a reliable',done[0])
        self.assertNotIn('On the first call the generator body runs.',done[0])

    def test_authored_sync_generator_construct_then_advance(self):
        trace=[]
        def produce():
            trace.append('body')
            yield 17
        iterator=produce();self.assertEqual(trace,[])
        self.assertEqual(next(iterator),17);self.assertEqual(trace,['body'])

    def test_authored_async_generator_construct_then_advance(self):
        trace=[]
        async def produce():
            trace.append('body')
            yield 23
        async def run():
            iterator=produce();self.assertEqual(trace,[])
            self.assertEqual(await anext(iterator),23);self.assertEqual(trace,['body'])
            await iterator.aclose()
        asyncio.run(run())


if __name__=='__main__':unittest.main()
