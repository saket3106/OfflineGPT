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


def verify_transaction_trace(events, required_groups):
    """Verify all-or-none effect groups at each acknowledged commit boundary.

    Restricted symbolic contract, NOT a SQL/Python evaluator. A caller supplies
    one connection's ordered, successfully executed control operations and
    explicit write/commit outcomes. Effects are unique opaque IDs, not balances.
    No concurrency, triggers, external effects, compensation or durability proof.
    Unrecognized input, unfinished transactions and ambiguous commits are UNKNOWN.
    An empty failure list is never a certificate for an arbitrary program.
    """
    pending=[];committed=[];marks=[];active=False;explicit=False;violations=[]
    seen=set();states=[]
    def result(status, reason='', supported=True, possible=None):
        return {'status':status,'supported':supported,'reason':reason,
                'committedEffects':list(committed),'pendingEffects':list(pending),
                'violations':list(violations),'boundaries':list(states),
                'possibleCommittedEffects':possible or [],
                'retryRequiresReconciliation':status=='UNKNOWN'}
    def unknown(reason):return result('UNKNOWN',reason,False)
    def valid_id(value):return isinstance(value,str) and bool(re.fullmatch(r'[A-Za-z0-9_.:-]{1,80}',value))
    if not isinstance(events,list) or not 1<=len(events)<=128:return unknown('Trace must contain 1–128 explicit events.')
    if not isinstance(required_groups,list) or not 1<=len(required_groups)<=16:return unknown('Explicit required effect groups are missing or oversized.')
    groups=[]
    for group in required_groups:
        if not isinstance(group,list) or not 2<=len(group)<=32 or not all(valid_id(v) for v in group) or len(set(group))!=len(group):return unknown('Invalid all-or-none effect group.')
        groups.append(set(group))
    def publish(index):
        nonlocal pending,active,explicit,marks
        committed.extend(pending);pending=[];active=False;explicit=False;marks=[]
        state=set(committed)
        broken=[sorted(g) for g in groups if state & g and not g<=state]
        if broken:violations.append({'event':index,'incompleteGroups':broken})
        states.append({'event':index,'committedEffects':list(committed),'incompleteGroups':broken})
    for index,event in enumerate(events):
        if not isinstance(event,dict):return unknown('Each event must be an object.')
        op=event.get('op')
        keys={'begin':{'op'},'write':{'op','effect','outcome'},'savepoint':{'op','name'},
              'rollback_to':{'op','name'},'release':{'op','name','outcome'},
              'rollback':{'op'},'commit':{'op','outcome'}}
        if not isinstance(op,str) or op not in keys or set(event)-keys[op]:return unknown('Unsupported operation or extra semantics.')
        if op=='begin':
            if active:return unknown('Nested BEGIN is not a successful control operation.')
            active=True;explicit=True
        elif op=='savepoint':
            if not valid_id(event.get('name')):return unknown('Invalid savepoint name.')
            active=True;marks.append((event['name'].casefold(),list(pending)))
        elif not active:return unknown('Explicit transaction/savepoint required before this operation.')
        elif op=='write':
            effect=event.get('effect');outcome=event.get('outcome')
            if not valid_id(effect) or effect in seen:return unknown('Write effects must have unique IDs; retry/idempotency cannot be inferred.')
            seen.add(effect)
            if outcome=='applied':pending.append(effect)
            elif outcome=='statement_aborted':pass
            elif outcome=='transaction_rolled_back':pending=[];marks=[];active=False;explicit=False
            else:return unknown('Write failure scope is not known.')
        elif op in {'rollback_to','release'}:
            if not valid_id(event.get('name')):return unknown('Invalid savepoint name.')
            matches=[n for n,(name,_) in enumerate(marks) if name==event['name'].casefold()]
            if not matches:return unknown('Successful operation references no live savepoint.')
            mark=matches[-1]
            if op=='rollback_to':pending=list(marks[mark][1]);marks=marks[:mark+1]
            else:
                if mark==0 and not explicit:
                    if event.get('outcome')=='success':publish(index)
                    elif event.get('outcome')=='unknown':
                        return result('UNKNOWN','Outermost RELEASE has an unacknowledged commit outcome.',possible=[list(committed),committed+pending])
                    else:return unknown('Outermost RELEASE needs an explicit commit outcome.')
                else:
                    if 'outcome' in event:return unknown('Inner RELEASE has no commit outcome in this contract.')
                    marks=marks[:mark]
        elif op=='rollback':pending=[];marks=[];active=False;explicit=False
        elif op=='commit':
            outcome=event.get('outcome')
            if outcome=='success':publish(index)
            elif outcome=='busy':pass  # SQLite BUSY leaves the transaction active.
            elif outcome=='unknown':
                return result('UNKNOWN','Commit acknowledgement is ambiguous; reconcile durable operation identity before retrying.',possible=[list(committed),committed+pending])
            else:return unknown('Commit failure scope is unspecified.')
    if violations:return result('PROVEN_INCONSISTENT','An acknowledged commit exposed an incomplete required group.')
    if active:return result('UNKNOWN','Transaction still active; no final outcome established.')
    return result('PROVEN_CONSISTENT','All acknowledged commit boundaries satisfy the declared all-or-none groups.')


def savepoint_scope_failures(prompt, answer):
    """Recognize one narrow contradictory transfer-policy explanation.

    Numbered, explicit begin/write/savepoint/failed-write/rollback/commit steps
    are analyzed as a hypothetical failure path. Unrecognized prose is not
    certified. The invariant is scoped to a requested safe debit/credit pair;
    unrelated partial-work policies and counterexamples are not rejected.
    """
    prompt=str(prompt);answer=str(answer)
    if len(prompt)>24000 or len(answer)>60000:return []
    if not (re.search(r'\bsqlite\b',prompt,re.I) and re.search(r'\bdebit\w*\b',prompt,re.I) and re.search(r'\bcredit\w*\b',prompt,re.I) and re.search(r'\bsafe\b|all.or.nothing|\batomic\w*\b',prompt,re.I)):return []
    if re.search(r'\b(?:allow|permit|require)\w*\s+(?:a\s+)?partial\s+commit\b',prompt,re.I):return []
    prose=re.sub(r'```.*?```','',answer,flags=re.S).replace('**','').replace('`','')
    matches=list(re.finditer(r'(?m)^\s*(\d{1,2})[.)]\s+([^\n]+)',prose))
    groups=[]
    for match in matches:
        if not groups or int(match[1])!=int(groups[-1][-1][1])+1:groups.append([])
        groups[-1].append(match)
    for group in groups:
        if len(group)<5 or int(group[0][1])!=1:continue
        end=group[-1].end();next_start=min([m.start() for m in matches if m.start()>end] or [len(prose)])
        tail=prose[end:next_start].split('\n#',1)[0][:800]
        # A later heading introduces a new policy and cannot endorse this one.
        tail=re.split(r'\n\s*(?:Using|Safe.failure|Alternative|Counterexample)\b',tail,flags=re.I)[0]
        claims=re.split(r'(?<=[.!?])\s+|\n+',tail)
        positive=False
        for clause in claims:
            if re.match(r'\s*(?:unsafe|counterexample|incorrect|false)\s*(?:claim)?\s*:',clause,re.I):continue
            claim=re.search(r'\b(?:ledger|operation|transfer|result|balance|invariant|transaction)\b.{0,40}\b(?P<property>consistent|atomic|all.or.nothing)\b',clause,re.I)
            if not claim:continue
            prefix=clause[max(0,claim.start('property')-45):claim.start('property')]
            negated=re.search(r'\b(?:not|never|cannot|can.t|isn.t)\s+(?:(?:be|remain|remains|necessarily|still)\s+)*$',prefix,re.I)
            if not negated:positive=True
        if not positive:continue
        events=[];writes=[];failed_attempt=False;recognized=True
        for match in group:
            step=match[2].strip()
            if re.match(r'Begin\s+(?:the\s+)?transaction\b',step,re.I):events.append({'op':'begin'})
            elif re.match(r'(?:Debit|Credit|Insert|Update|Delete)\b',step,re.I):
                effect='step-'+match[1];writes.append(effect);events.append({'op':'write','effect':effect,'outcome':'applied'})
            elif re.match(r'Attempt\s+(?:the\s+)?(?:credit|insert|update|delete)\b',step,re.I):
                effect='step-'+match[1];writes.append(effect);failed_attempt=True;events.append({'op':'write','effect':effect,'outcome':'statement_aborted'})
            elif re.match(r'SAVEPOINT\s+\w+',step,re.I):events.append({'op':'savepoint','name':re.match(r'SAVEPOINT\s+(\w+)',step,re.I)[1]})
            elif failed_attempt and (m:=re.match(r'If\b[^,]{1,80}\bfails?\b,\s*ROLLBACK\s+TO\s+(?:SAVEPOINT\s+)?(\w+)',step,re.I)):
                events.append({'op':'rollback_to','name':m[1]})
            elif failed_attempt and re.match(r'If\b[^,]{1,80}\bfails?\b,\s*ROLLBACK\s+(?:the\s+)?(?:entire|whole)\s+transaction',step,re.I):events.append({'op':'rollback'})
            elif re.match(r'Commit\s+(?:the\s+)?outer\s+transaction\b',step,re.I):events.append({'op':'commit','outcome':'success'})
            else:recognized=False;break
        if recognized and failed_attempt and len(writes)==2:
            verdict=verify_transaction_trace(events,[writes])
            if verdict['status']=='PROVEN_INCONSISTENT':
                return ['The stated savepoint failure path commits only part of the requested safe debit/credit operation, yet calls it consistent. ROLLBACK TO retains writes before the savepoint. Put the mark before all coupled writes, roll back the whole transaction, or explicitly justify an allowed partial policy; do not call isolation of one failed statement proof of the business invariant.']
    return []


def ambiguous_commit_failures(prompt, answer):
    """Reject explicit certainty/replay claims after a lost COMMIT acknowledgement.

    Only an explicitly ambiguous outcome qualifies. Ordinary SQLite BUSY is
    distinct and is not automatically treated as ambiguous. No omission check.
    """
    if not re.search(r'\b(?:sqlite|transaction)\b',str(prompt),re.I):return []
    if not re.search(r'\bcommit\b.{0,65}\b(?:unknown|ambiguous|timeout|timed out|lost acknowledgement|acknowledgement.*lost)\b|\b(?:unknown|ambiguous)\b.{0,40}\bcommit\b',str(prompt),re.I):return []
    prose=re.sub(r'```.*?```','',str(answer),flags=re.S).replace('**','')
    for clause in re.split(r'\n+|(?<=[.!?])\s+',prose):
        if re.match(r'\s*(?:counterexample|incorrect|false)\s*(?:claim)?\s*:',clause,re.I):continue
        if re.match(r'\s*(?:if|only (?:if|after))\b.{0,100}\b(?:verified|confirmed|reconciled)\b.{0,100}\b(?:not committed|no commit|rollback|idempotent)\b',clause,re.I):continue
        claim=re.search(r'\bcommit\b.{0,45}\b(?P<certainty>definitely|certainly|always)\b.{0,30}\b(?:failed|rolled back)\b|\b(?P<safety>safe(?:ly)?)\b.{0,30}\b(?:replay|retry)\b.{0,35}\b(?:whole|entire)\b.{0,20}\btransaction\b',clause,re.I)
        if claim:
            anchor=claim.start('certainty') if claim.group('certainty') else claim.start('safety')
            prefix=clause[max(0,anchor-70):anchor]
            denial=re.search(r'\b(?:not|never|cannot|can.t)\s+(?:(?:assume|say|claim|infer|conclude|that|the|a|commit|is|was|be|necessarily)\s+)*$',prefix,re.I)
            if denial:continue
            return ['An unacknowledged COMMIT does not prove rollback or safe replay of the entire transaction. Distinguish known SQLite BUSY (transaction still active) from an unknown outcome; reconcile durable operation identity and specify idempotency before retrying.']
    return []


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
    failures=(python_default_timing_failures(prompt,answer)+python_generator_timing_failures(prompt,answer)
              +savepoint_scope_failures(prompt,answer)+ambiguous_commit_failures(prompt,answer))
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
