"""Static code analysis and bounded deterministic checks. No arbitrary execution."""
from __future__ import annotations

import ast
from contextlib import redirect_stdout
from dataclasses import dataclass
import io
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
from typing import Optional


def transaction_guarantee_failures(prompt, answer):
    """Reject an unsupported SQLite automatic transaction-cleanup claim.

    Static claim checking only: no model-supplied SQL or Python is executed.
    It is not a general transaction verifier.
    """
    if not re.search(r'\bsqlite\b',prompt,re.I) or not re.search(r'\btransaction\b',prompt,re.I):return []
    positive=False
    for match in re.finditer(r'automatically\s+(?:be\s+)?(?:rolled?\s*back|rolls?\s*back)|(?:rolled?\s*back|rolls?\s*back)\s+automatically',answer,re.I):
        before=answer[max(0,match.start()-65):match.start()]
        if not re.search(r'\b(?:not|never|doesn.t|won.t|isn.t|cannot|can.t)\s+(?:(?:be|get|necessarily|implicitly)\s+|guaranteed to (?:be )?)*$',before,re.I):positive=True
    if not positive:return []
    code='\n'.join(re.findall(r'```[^\n]*\n(.*?)```',answer,re.S))
    code=re.sub(r'(?m)^\s*#.*$|--[^\n]*','',code)
    mechanism=bool(re.search(r'\bROLLBACK\s*(?:;|TO\b)|\.rollback\s*\(|\bwith\s+(?:conn\w*|connection\w*|db)\s*:',code,re.I) or re.search(r'\bON\s+CONFLICT\s+ROLLBACK\b|\btransaction context manager\b',answer,re.I))
    if not mechanism:
        return ['The automatic transaction-rollback guarantee lacks a concrete failure-control mechanism. Recheck statement versus transaction scope and show executable failure cleanup or a specified rollback policy. Comments and BEGIN/COMMIT alone do not implement cleanup. Do not claim an unshown guarantee.']
    return []


def engineering_review_focus(prompt):
    """Domain-specific review obligations, never an answer or success verdict.

    These checks guide the existing single bounded review. They do not execute
    generated code or certify a protocol by matching technical vocabulary.
    """
    text=str(prompt)
    focus=[]
    if re.search(r'\b(?:crash|recovery|durability|interruption|atomicity)\b',text,re.I):
        focus.append('Separate process interruption from power-loss durability. Enumerate externally observable states at each persistence boundary, including between filesystem publication and metadata commit. Validate content and operation ownership before recovery promotes an artifact; existence alone is not completeness. State platform-specific flush assumptions and admit unknown guarantees.')
    if re.search(r'\b(?:concurren\w*|duplicate\w*|idempoten\w*|exactly.once|retry|retries)\b',text,re.I):
        focus.append('Trace two competing workers and retry after an ambiguous outcome. Distinguish atomic ownership or uniqueness enforcement from check-then-act. State the scope of idempotency and external side effects; do not infer exactly-once execution from a unique filename or a database flag.')
    if re.search(r'\b(?:sqlite|database|transaction\w*)\b',text,re.I):
        focus.append('Identify which operations participate in the same transaction and which cannot. Trace statement failure, exception cleanup, commit failure and retry; distinguish a failed statement from rollback of the whole transaction. Never credit comments or a BEGIN/COMMIT pair with missing failure cleanup.')
    if re.search(r'\bsavepoint\b',text,re.I):
        focus.append('Trace the position of every savepoint relative to every required write. ROLLBACK TO preserves changes made before its mark. Test each proposed alternative against the same user-required invariant; isolating a failed statement is not evidence that the enclosing business operation remains consistent.')
    if re.search(r'\b(?:lease|stale|fenc\w*|ownership|two workers)\b',text,re.I):
        focus.append('Construct the interleaving where a worker pauses after its last ownership check, another owner publishes, then the first resumes. The publication itself must reject stale ownership atomically or use a coherent immutable-generation protocol; a preceding check or lease expiry alone does not fence a writer.')
    if re.search(r'\b(?:manifest|wal|backup)\b',text,re.I):
        focus.append('Bind readers and restore operations to one validated generation. Independent copies or replacements do not constitute one atomic snapshot. Consider readers that opened the old manifest before publication and do not overwrite artifacts they still reference.')
    return '\n'.join(focus)


def python_default_timing_failures(prompt,answer):
    """Check a language invariant against parsed function defaults, never exec.

    Narrow explanation check, not general code correctness. Only recognized
    non-constant defaults and an explicit first-call evaluation claim qualify.
    """
    if len(str(prompt))>24000 or len(str(answer))>60000:return []
    signatures=re.findall(r'\b(?:async\s+)?def\s+\w+\([^\n]*\)\s*(?:->[^\n:]+)?\s*:',str(prompt))[:12]
    has_default=False
    for signature in signatures:
        try:tree=ast.parse(signature+'\n    pass')
        except (SyntaxError,ValueError,RecursionError):continue
        node=tree.body[0]
        defaults=list(node.args.defaults)+[d for d in node.args.kw_defaults if d is not None]
        if any(isinstance(d,(ast.Call,ast.List,ast.Dict,ast.Set)) for d in defaults):has_default=True;break
    if not has_default:return []
    prose=re.sub(r'```.*?```','',str(answer),flags=re.S)
    for clause in re.split(r'\n+|;|(?<=[.!?])\s+|,?\s+(?:but|however)\s+',prose,flags=re.I):
        if re.match(r'\s*(?:false|incorrect|counterexample|myth)\s*(?:claim)?\s*:',clause,re.I):continue
        if re.search(r'\b(?:corrected|replacement|with None|inside the (?:function )?body)\b',clause,re.I):continue
        subject=r'\b(?:default(?: (?:argument|parameter|value|object))?|(?:the )?(?:set|list|dict|dictionary))\b'
        action=r'(?P<action>\b(?:created|evaluated|initialized|constructed|allocated|built)\b)'
        timing=r'(?P<timing>\b(?:first (?:function )?call|first invocation|first called|called (?:for )?the first time)\b)'
        match=(re.search(subject+r'.{0,45}'+action+r'.{0,65}'+timing,clause,re.I) or
               re.search(r'\b(?:when|on)\b.{0,60}'+timing+r'.{0,50}'+subject+r'.{0,45}'+action,clause,re.I))
        before=clause[max(0,match.start('action')-40):match.start('action')] if match else ''
        before_timing=clause[max(0,match.start('timing')-40):match.start('timing')] if match else ''
        negated=bool(re.search(r'\b(?:not|never|isn.t|wasn.t)\s+(?:necessarily\s+|actually\s+)?$',before,re.I) or re.search(r'\bnot\s+(?:on |at |during |when |its |the )*$',before_timing,re.I))
        if match and not negated:
            return ['The explanation contradicts Python default-argument evaluation: the parsed default expression is evaluated when the function definition executes, not on its first call. Distinguish default creation, function invocation, and delayed generator-body execution; do not claim these were tested without runtime evidence.']
    return []


def python_function_facts(text):
    """Bounded static function scopes; never evaluate defaults or run code.

    Recognizes fenced snippets or one contiguous function listing embedded in
    prose. Incomplete/unrecognized snippets yield no facts, not a correctness
    verdict. Nested bodies cannot turn their enclosing function into a generator.
    """
    text=str(text)
    if len(text)>24000:return []
    fenced=re.findall(r'```(?:python|py)?\s*\n(.*?)```',text,re.S|re.I)[:8]
    if fenced:candidates=fenced
    else:
        start=re.search(r'\b(?:async\s+)?def\s+\w+\s*\(',text)
        candidates=[text[start.start():]] if start else []
    facts={}
    class OwnYield(ast.NodeVisitor):
        found=False
        def visit_Yield(self,node):self.found=True
        def visit_YieldFrom(self,node):self.found=True
        def visit_FunctionDef(self,node):
            # Definition-time expressions belong to the enclosing scope, but
            # a nested function body does not.
            for value in node.decorator_list+node.args.defaults+[d for d in node.args.kw_defaults if d is not None]:self.visit(value)
            annotations=[arg.annotation for arg in node.args.posonlyargs+node.args.args+node.args.kwonlyargs if arg.annotation is not None]
            annotations += [arg.annotation for arg in (node.args.vararg,node.args.kwarg) if arg is not None and arg.annotation is not None]
            if node.returns is not None:annotations.append(node.returns)
            for value in annotations:self.visit(value)
        visit_AsyncFunctionDef=visit_FunctionDef
        def visit_Lambda(self,node):
            for value in node.args.defaults+[d for d in node.args.kw_defaults if d is not None]:self.visit(value)
        def visit_ClassDef(self,node):
            for value in node.bases+node.decorator_list+[k.value for k in node.keywords]:self.visit(value)
        def visit_GeneratorExp(self,node):
            if node.generators:self.visit(node.generators[0].iter)
    for candidate in candidates:
        code=textwrap.dedent(candidate).strip()
        tree=None
        for _ in range(3):
            try:
                tree=ast.parse(code)
                if sum(1 for _ in ast.walk(tree))>6000:tree=None;break
                # Compilation validates context restrictions without executing
                # imports, decorators, default expressions or function bodies.
                compile(tree,'<static function inspection>','exec',dont_inherit=True)
                break
            except (SyntaxError,ValueError,RecursionError) as exc:
                line=getattr(exc,'lineno',None)
                if not line or line<=1:tree=None;break
                lines=code.splitlines()
                trailing=lines[line-1] if line<=len(lines) else ''
                # Only remove clearly separate trailing request prose, never
                # truncate a broken statement into an apparently valid program.
                if trailing[:1].isspace() or not re.match(r'(?:Explain|Discuss|Describe|What|How|Do not|Keep|Return|Please|Answer|Note|Also)\b',trailing):tree=None;break
                code='\n'.join(code.splitlines()[:line-1])
                tree=None
        if tree is None:continue
        primary={id(node) for node in tree.body if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef))}
        for node in ast.walk(tree):
            if not isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):continue
            visitor=OwnYield()
            for statement in node.body:visitor.visit(statement)
            kind=('async_generator' if isinstance(node,ast.AsyncFunctionDef) else 'generator') if visitor.found else ('coroutine' if isinstance(node,ast.AsyncFunctionDef) else 'ordinary')
            fact={'name':node.name,'kind':kind,'primary':id(node) in primary}
            key=(node.name,kind,ast.dump(node,include_attributes=False))
            if key in facts:fact['primary']=facts[key]['primary'] or fact['primary']
            facts[key]=fact
    return list(facts.values())


def python_generator_timing_failures(prompt,answer):
    """Reject explicit call/iteration contradictions for recognized generators.

    Checks narrow observable assertions, not unrestricted semantic entailment.
    Argument/default evaluation and calls which drive an iterator are distinct
    from invoking a generator function. Unknown/mixed function scopes fail open
    for this checker and are never certified as correct.
    """
    if len(str(answer))>60000:return []
    facts=python_function_facts(prompt)
    generators={f['name'] for f in facts if f['kind'] in {'generator','async_generator'}}
    ambiguous={f['name'] for f in facts if f['kind'] not in {'generator','async_generator'}} & generators
    generators-=ambiguous
    if not generators:return []
    primary=[f for f in facts if f['primary']]
    single_generator=len(primary)==1 and primary[0]['name'] in generators
    prose=re.sub(r'```.*?```','',str(answer),flags=re.S).replace('`','')
    # Split independent claims so an unrelated denial cannot exempt a false
    # execution claim later in the same answer.
    clauses=re.split(r'\n+|;|(?<=[.!?])\s+|,?\s+(?:but|however|and yet)\s+',prose,flags=re.I)
    timing=r'(?P<timing>\b(?:on|at|during)\s+(?:the\s+)?(?:first\s+)?(?:function\s+)?call\b|\bwhen\b.{0,60}\b(?:called|invoked)\b|\bcalling\b.{0,40})'
    action=r'(?P<action>\b(?:runs?|executes?|iterates?|starts?|yields?)\b)'
    for clause in clauses:
        if re.match(r'\s*(?:false|incorrect|counterexample|myth)\s*(?:claim)?\s*:',clause,re.I):continue
        call_names=re.findall(r'\b(?:calling\s+|call\s+to\s+)(\w+)\b|\b(\w+)\s*\(',clause,re.I)
        named={a or b for a,b in call_names}
        targets=set(re.findall(r'\b(?:calling\s+|call\s+to\s+)(\w+)\b',clause,re.I))-{'the','a','it','function','generator'}
        if targets-generators:continue
        ordinary={f['name'] for f in facts if f['kind']=='ordinary'}
        if named & ordinary and not named & generators:continue
        named_generator=any(re.search(r'\b'+re.escape(name)+r'\b',clause) for name in generators)
        explicit_generator=bool(re.search(r'\b(?:async(?:hronous)?\s+)?generator\b',clause,re.I))
        if not (explicit_generator or named_generator or single_generator and re.search(r'\b(?:function|body|loop)\b',clause,re.I)):continue
        match=(re.search(timing+r'.{0,100}'+action,clause,re.I) or re.search(action+r'.{0,100}'+timing,clause,re.I))
        if not match:continue
        # next/send/throw and asynchronous counterparts drive execution rather
        # than merely construct a generator object.
        call_window=clause[max(0,match.start('timing')-20):min(len(clause),match.end('timing')+60)]
        drivers=r'(?:next|anext|send|asend|throw|athrow|__next__|__anext__)'
        if re.search(r'\b(?:calling|call(?:\s+to)?|when)\s+(?:(?:the|its|their)\s+)?'+drivers+r'\b|\b'+drivers+r'\s+(?:method\s+)?(?:is\s+)?(?:called|invoked)\b',call_window,re.I):continue
        if re.search(r'\b(?:iteration|iterated|iterator advancement)\b',call_window,re.I):continue
        before_action=clause[max(0,match.start('action')-45):match.start('action')]
        before_timing=clause[max(0,match.start('timing')-35):match.start('timing')]
        if re.search(r'\b(?:not|never|doesn.t|won.t|cannot|can.t)\s+(?:immediately\s+|actually\s+|necessarily\s+)?$',before_action,re.I):continue
        if re.search(r'\bnot\s+(?:merely\s+|just\s+)?$',before_timing,re.I):continue
        after=clause[match.start('action'):]
        if re.match(r'starts?\s+without\s+(?:executing|running)\s+(?:its\s+|the\s+)?body\b',after,re.I):continue
        body=bool(re.search(r'\b(?:body|loop|iterates?|iteration|statements?|yields?)\b',after+' '+before_action[-30:],re.I))
        if not body:continue
        if re.match(r'yields?\s+(?:an?\s+)?(?:generator|iterator|awaitable|coroutine)\b',after,re.I):continue
        if re.search(r'\b(?:argument|default|decorator)\b',before_action[-30:],re.I) and not re.search(r'\b(?:body|loop)\b',before_action[-30:],re.I):continue
        return ['The explanation contradicts the parsed generator scope: calling its generator function constructs an iterator (an async iterator for an async generator), but does not execute its body or loop. Distinguish function invocation and argument/default evaluation from iteration or iterator advancement. Correct all contradictory sentences, even if another sentence already describes laziness correctly.']
    return []


def engineering_guarantee_failures(prompt, answer):
    """Narrow observable overclaim checks, not a general correctness oracle.

    Only positive guarantees tied to technical recovery requests are checked.
    Lack of a keyword is not proof of a defective design. Unrelated subtasks
    and ordinary explanations are not refused by this function.
    """
    failures=python_default_timing_failures(prompt,answer)+python_generator_timing_failures(prompt,answer)
    if not engineering_review_focus(prompt):return failures
    # Code literals and explicitly labelled counterexamples are not endorsed
    # prose guarantees. This checker does not certify generated implementations.
    prose=re.sub(r'```.*?```','',str(answer),flags=re.S)
    for clause in re.split(r'\n+|;|(?<=[.!?])\s+|,?\s+(?:but|however|and yet)\s+',prose,flags=re.I):
        if re.match(r'\s*(?:incorrect|false|unsafe|unsupported|counterexample|myth)\s*(?:claim)?\s*:',clause,re.I):continue
        guarantee=re.search(r'\b(?:guarantees?|ensures?|achieves?|provides?)\b',clause,re.I)
        prefix=clause[max(0,guarantee.start()-45):guarantee.start()] if guarantee else ''
        negated=bool(re.search(r'\b(?:not|cannot|can.t|never|no|doesn.t|fails? to)\s+(?:necessarily\s+|automatically\s+)?$',prefix,re.I))
        if negated:continue
        publication=bool(re.search(r'\bexactly.once\s+(?:publication|committed (?:result|effect|row)|database effect)\b',clause,re.I))
        scoped=publication and bool(re.search(r'\b(?:under|if|assuming|within)\b',clause,re.I)) and bool(re.search(r'\b(?:unique|idempotency key|atomic transaction|conditional update|compare.and.swap)\b',clause,re.I))
        if re.search(r'\b(?:guarantees?|ensures?|achieves?|provides?)\b.{0,60}\bexactly.once\b|\bexactly.once\s+(?:processing|execution)\s+is\s+guaranteed',clause,re.I) and not scoped:
            failures.append('An unqualified exactly-once guarantee is unsupported. State its scope, assumptions and atomic enforcement; distinguish one committed result from one execution or one external side effect.')
        if re.search(r'\b(?:rename|os\.replace|atomic replace)\b.{0,55}\b(?:guarantees?|ensures?)\b.{0,30}\b(?:durability|durable|power.loss)',clause,re.I):
            failures.append('Atomic namespace replacement alone does not establish power-loss durability. State the actual persistence boundaries and platform/filesystem assumptions or remove the guarantee.')
        if re.search(r'\b(?:file|artifact|output)\s+exist\w*\b.{0,35}\b(?:proves?|guarantees?|ensures?)\b.{0,35}\b(?:complete|valid|finished|safe)',clause,re.I):
            failures.append('Artifact existence alone does not prove a complete, valid result. Define content validation and operation identity checks before promotion or reuse.')
    return list(dict.fromkeys(failures))


@dataclass
class CodeExecutionResult:
    success: bool
    stdout: str = ""
    stderr: str = ""
    error_message: str = ""
    returncode: int = 0
    timed_out: bool = False


class _ReturnValue(Exception):
    def __init__(self, value): self.value = value


class _BoundedInterpreter:
    """Deterministic AST evaluator. No Python exec/eval, imports, attributes or I/O.

    This is a deliberately small language, not a general Python sandbox.
    Every node/iteration consumes a shared budget; values, call depth and output
    are bounded before expensive operations. Unsupported Python is rejected.
    """
    def __init__(self, timeout, max_chars):
        import time
        self.deadline = time.monotonic() + min(5., max(.01, timeout))
        self.budget = 20000; self.depth = 0; self.output = ''; self.max_chars = max_chars
        self.functions = {}; self.globals = {}

    def tick(self):
        import time
        self.budget -= 1
        if self.budget < 0 or time.monotonic() > self.deadline:
            raise TimeoutError('Bounded check exceeded its time or instruction budget')

    def bounded(self, value):
        stack=[(value,0)]; count=0
        while stack:
            item,depth=stack.pop();count+=1
            if count>8192 or depth>12: raise ValueError('Nested value limit exceeded')
            if isinstance(item,(list,tuple,set,dict)):
                if len(item)>4096: raise ValueError('Value size limit exceeded')
                stack.extend((x,depth+1) for x in (list(item.keys())+list(item.values()) if isinstance(item,dict) else item))
        if isinstance(value, int) and value.bit_length() > 4096:
            raise ValueError('Integer size limit exceeded')
        if isinstance(value, (str, list, tuple, dict, set, range)) and len(value) > 4096:
            raise ValueError('Value size limit exceeded')
        return value

    def expr(self, n, env):
        self.tick()
        if isinstance(n, ast.Constant): return self.bounded(n.value)
        if isinstance(n, ast.Name):
            if n.id in env: return env[n.id]
            if n.id in self.globals: return self.globals[n.id]
            raise NameError(n.id)
        if isinstance(n, (ast.List, ast.Tuple, ast.Set)):
            self.bounded(range(len(n.elts)))
            items = [self.expr(x, env) for x in n.elts]
            return items if isinstance(n, ast.List) else tuple(items) if isinstance(n, ast.Tuple) else set(items)
        if isinstance(n, ast.Dict): return self.bounded({self.expr(k,env):self.expr(v,env) for k,v in zip(n.keys,n.values)})
        if isinstance(n, ast.UnaryOp):
            v=self.expr(n.operand,env)
            if isinstance(n.op,ast.Not): return not v
            if isinstance(n.op,ast.USub): return self.bounded(-v)
            if isinstance(n.op,ast.UAdd): return self.bounded(+v)
        if isinstance(n, ast.BinOp):
            import operator
            a,b=self.expr(n.left,env),self.expr(n.right,env)
            if isinstance(n.op,ast.Mult):
                if isinstance(a,(str,list,tuple)) and isinstance(b,int) and len(a)*max(0,b)>4096: raise ValueError('Value size limit exceeded')
                if isinstance(b,(str,list,tuple)) and isinstance(a,int) and len(b)*max(0,a)>4096: raise ValueError('Value size limit exceeded')
            if isinstance(n.op,ast.Pow) and (not isinstance(b,(int,float)) or abs(b)>64 or abs(a)>1000000): raise ValueError('Power limit exceeded')
            ops={ast.Add:operator.add,ast.Sub:operator.sub,ast.Mult:operator.mul,ast.Div:operator.truediv,ast.FloorDiv:operator.floordiv,ast.Mod:operator.mod,ast.Pow:operator.pow}
            if type(n.op) not in ops: raise ValueError('Operator unavailable in bounded checks')
            if isinstance(a,str) and isinstance(n.op,ast.Mod): raise ValueError('String formatting unavailable in bounded checks')
            return self.bounded(ops[type(n.op)](a,b))
        if isinstance(n,ast.Compare):
            import operator
            a=self.expr(n.left,env)
            ops={ast.Eq:operator.eq,ast.NotEq:operator.ne,ast.Lt:operator.lt,ast.LtE:operator.le,ast.Gt:operator.gt,ast.GtE:operator.ge,ast.In:lambda a,b:a in b,ast.NotIn:lambda a,b:a not in b}
            for op,right in zip(n.ops,n.comparators):
                b=self.expr(right,env)
                if type(op) not in ops or not ops[type(op)](a,b): return False
                a=b
            return True
        if isinstance(n,ast.BoolOp):
            value=self.expr(n.values[0],env)
            for item in n.values[1:]:
                if isinstance(n.op,ast.And) and not value: return value
                if isinstance(n.op,ast.Or) and value: return value
                value=self.expr(item,env)
            return value
        if isinstance(n,ast.IfExp): return self.expr(n.body if self.expr(n.test,env) else n.orelse,env)
        if isinstance(n,ast.Subscript): return self.bounded(self.expr(n.value,env)[self.expr(n.slice,env)])
        if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and not n.keywords:
            name=n.func.id; args=[self.expr(a,env) for a in n.args]
            if name=='print':
                text=' '.join(str(a) for a in args)+'\n'
                if len(self.output)+len(text)>self.max_chars: raise ValueError('Output limit exceeded')
                self.output+=text; return None
            if name in self.functions:
                fn=self.functions[name]
                if len(args)!=len(fn.args.args): raise ValueError('Argument count mismatch')
                self.depth+=1
                if self.depth>20: raise ValueError('Call depth limit exceeded')
                local=dict(zip([a.arg for a in fn.args.args],args))
                try: self.block(fn.body,local)
                except _ReturnValue as r: return r.value
                finally: self.depth-=1
                return None
            allowed={'len':len,'range':range,'sum':sum,'min':min,'max':max,'abs':abs,'round':round,'int':int,'float':float,'str':str,'bool':bool,'sorted':sorted,'list':list,'tuple':tuple,'all':all,'any':any,'enumerate':lambda v:list(enumerate(v)),'zip':lambda *v:list(zip(*v))}
            if name not in allowed: raise ValueError('Call unavailable in bounded checks: '+name)
            return self.bounded(allowed[name](*args))
        raise ValueError('Python construct unavailable in bounded checks: '+type(n).__name__)

    def assign(self,n,value,env):
        if not isinstance(n,ast.Name): raise ValueError('Only local variable assignment is supported')
        env[n.id]=self.bounded(value)

    def block(self, nodes, env):
        for n in nodes:
            self.tick()
            if isinstance(n,ast.FunctionDef):
                if n.decorator_list or n.args.defaults or n.args.vararg or n.args.kwarg: raise ValueError('Advanced function constructs unavailable')
                self.functions[n.name]=n
            elif isinstance(n,ast.Assign):
                value=self.expr(n.value,env)
                for target in n.targets: self.assign(target,value,env)
            elif isinstance(n,ast.AugAssign):
                self.assign(n.target,self.expr(ast.BinOp(left=n.target,op=n.op,right=n.value),env),env)
            elif isinstance(n,ast.Expr): self.expr(n.value,env)
            elif isinstance(n,ast.Return): raise _ReturnValue(self.expr(n.value,env) if n.value else None)
            elif isinstance(n,ast.If): self.block(n.body if self.expr(n.test,env) else n.orelse,env)
            elif isinstance(n,ast.For):
                for value in self.expr(n.iter,env):
                    self.tick();self.assign(n.target,value,env);self.block(n.body,env)
                self.block(n.orelse,env)
            elif isinstance(n,ast.While):
                while self.expr(n.test,env): self.tick();self.block(n.body,env)
                self.block(n.orelse,env)
            elif isinstance(n,ast.Assert):
                if not self.expr(n.test,env): raise AssertionError('Bounded assertion failed')
            elif not isinstance(n,ast.Pass): raise ValueError('Python construct unavailable in bounded checks: '+type(n).__name__)


def safe_execute_in_process(code: str, max_chars: int = 4000, timeout_seconds: float = 3.) -> CodeExecutionResult:
    """Evaluate the restricted deterministic subset; never execute arbitrary Python."""
    evaluator=_BoundedInterpreter(timeout_seconds,max_chars)
    try:
        if len(code)>100000: raise ValueError('Code size limit exceeded')
        tree=ast.parse(code)
        if sum(1 for _ in ast.walk(tree))>5000: raise ValueError('AST size limit exceeded')
        evaluator.block(tree.body,evaluator.globals)
        return CodeExecutionResult(True,stdout=evaluator.output)
    except Exception as exc:
        return CodeExecutionResult(False,stdout=evaluator.output,error_message=str(exc),returncode=1,timed_out=isinstance(exc,TimeoutError))


def inspect_python(code: str, kind='executable') -> dict:
    """Static checks only. Warnings are advisory, not proof of program correctness."""
    if len(code)>100000:return {'valid':False,'executed':False,'error':'Code size limit exceeded','warnings':[]}
    if kind=='pseudocode': return {'valid':None,'executed':False,'kind':kind,'warnings':['Pseudocode: executable syntax checks skipped.']}
    valid,error=validate_python_syntax(code)
    if not valid: return {'valid':False,'executed':False,'error':error,'warnings':[]}
    tree=ast.parse(code); warnings=[]
    bound={n.id for n in ast.walk(tree) if isinstance(n,ast.Name) and isinstance(n.ctx,(ast.Store,ast.Param))}
    bound.update(n.arg for n in ast.walk(tree) if isinstance(n,ast.arg))
    bound.update(n.name for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.ClassDef)))
    bound.update(a.asname or a.name.split('.')[0] for n in ast.walk(tree) if isinstance(n,(ast.Import,ast.ImportFrom)) for a in n.names)
    import builtins
    for name in sorted({n.id for n in ast.walk(tree) if isinstance(n,ast.Name) and isinstance(n.ctx,ast.Load)}-bound-set(dir(builtins))):
        warnings.append('Possibly undefined name or missing import: '+name)
    if any(isinstance(n,ast.Constant) and n.value is Ellipsis for n in ast.walk(tree)): warnings.append('Ellipsis placeholder: implementation may be illustrative or incomplete.')
    return {'valid':True,'executed':False,'kind':kind,'warnings':warnings,'limitation':'Static checks do not verify behavior, dependencies or platform compatibility.'}


def extract_code_blocks(text: str, language: Optional[str] = "python") -> list[str]:
    """Extract code blocks from markdown fences or raw code lines in text."""
    if not text:
        return []

    blocks: list[str] = []
    # Match markdown code fences: ``` or ~~~ (3 or more)
    # Allows leading whitespace, indentation, or list markers like '2. ```'
    fence_pattern = re.compile(
        r"^[ \t]*(?:[0-9]+[.)]\s*)?(`{3,}|~{3,})[ \t]*([a-zA-Z0-9_-]*)[ \t]*\r?\n(.*?)(?:\r?\n[ \t]*\1[ \t]*(?:\r?\n|\Z)|\Z)",
        re.MULTILINE | re.DOTALL,
    )

    for m in fence_pattern.finditer(text):
        lang = (m.group(2) or "").strip().lower()
        code = m.group(3)
        dedented = textwrap.dedent(code).strip()
        if not dedented:
            continue
        if language:
            target_lang = language.lower()
            allowed_langs = {target_lang}
            if target_lang == "python":
                allowed_langs.add("py")
            if lang in allowed_langs:
                blocks.append(dedented)
            elif not lang:
                # If untagged fence, check if it looks like python or parses
                if target_lang == "python":
                    try:
                        ast.parse(dedented)
                        blocks.append(dedented)
                    except SyntaxError:
                        if any(l.strip().startswith(("def ", "class ", "import ", "from ", "return ")) for l in dedented.splitlines()):
                            blocks.append(dedented)
                else:
                    blocks.append(dedented)
        else:
            blocks.append(dedented)

    if blocks:
        return blocks

    # Fallback: scan for contiguous python code (e.g. def/class/import statements) without English paragraphs
    lines = text.splitlines()
    code_lines: list[str] = []
    in_code = False
    for line in lines:
        stripped = line.strip()
        if not in_code:
            if stripped.startswith(("def ", "class ", "import ", "from ")):
                in_code = True
                code_lines.append(line)
        else:
            if not stripped or line.startswith((" ", "\t")) or stripped.startswith(("return ", "else:", "elif ", "except", "finally:")):
                code_lines.append(line)
            elif stripped.startswith(("def ", "class ", "import ", "from ", "#")):
                code_lines.append(line)
            else:
                break
    if code_lines:
        extracted = textwrap.dedent("\n".join(code_lines)).strip()
        if extracted:
            return [extracted]

    return []


def validate_python_syntax(code: str) -> tuple[bool, str]:
    """Check Python code syntax using AST parsing without executing it."""
    if not code or not code.strip():
        return True, ""
    try:
        ast.parse(code)
        return True, ""
    except SyntaxError as exc:
        line = exc.lineno or 1
        offset = exc.offset or 0
        msg = exc.msg or "Syntax error"
        return False, f"Python SyntaxError at line {line}, col {offset}: {msg}"
    except Exception as exc:
        return False, f"Python parse error: {exc}"


class SafeCodeRunner:
    """Opt-in bounded AST checker for a small pure Python subset.

    SECURITY NOTE:
    Execution is strictly disabled by default (enabled=False).
    When enabled, unsupported constructs are rejected. No Python subprocess,
    filesystem, network, imports, attributes or arbitrary bytecode execution.
    """

    def __init__(self, enabled: bool = False, timeout_seconds: float = 5.0, max_output_chars: int = 4000):
        self.enabled = enabled
        self.timeout_seconds = timeout_seconds
        self.max_output_chars = max_output_chars

    def run(self, code: str) -> CodeExecutionResult:
        if not self.enabled:
            # When disabled, only static syntax validation is performed
            is_valid, error = validate_python_syntax(code)
            if not is_valid:
                return CodeExecutionResult(
                    success=False,
                    error_message=f"Syntax check failed: {error}",
                    returncode=1,
                )
            return CodeExecutionResult(
                success=True,
                stdout="[Code execution disabled by security policy; syntax verified successfully]",
                returncode=0,
            )

        # First verify syntax before launching any execution
        is_valid, error = validate_python_syntax(code)
        if not is_valid:
            return CodeExecutionResult(
                success=False,
                error_message=f"Syntax validation failed before execution: {error}",
                returncode=1,
            )

        return safe_execute_in_process(code, self.max_output_chars, self.timeout_seconds)
