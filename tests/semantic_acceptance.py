"""Evidence gates for independently specified, genuine model evaluations.

This validates records, not arbitrary answer semantics. An independent review
must supply criterion decisions; runtime validator success is never substituted.
No model, generated program, SQL, network or subprocess is invoked here.
"""
import hashlib
import math
import re


def assess_record(case, record):
    issues=[]
    if not isinstance(case,dict) or not isinstance(record,dict):
        return {'status':'INVALID','issues':['Case and record must be objects.']}
    criteria=case.get('criteria',[])
    ids=[c.get('id') for c in criteria if isinstance(c,dict)] if isinstance(criteria,list) else []
    if not ids or len(ids)!=len(criteria) or not all(isinstance(v,str) and v for v in ids) or len(set(ids))!=len(ids):
        return {'status':'INVALID','issues':['Expected criteria must have unique, nonempty IDs.']}
    if record.get('caseId')!=case.get('id'):issues.append('Case identity mismatch.')
    prompt=case.get('input')
    if not isinstance(prompt,str) or not prompt.strip():issues.append('Original evaluation input is missing.')
    elif hashlib.sha256(prompt.encode('utf-8')).hexdigest()!=record.get('inputSha256'):issues.append('Input fingerprint mismatch.')
    if record.get('kind')!='genuine_model':issues.append('Record is not genuine model inference.')
    answer=record.get('answer')
    if not isinstance(answer,str) or not answer.strip():issues.append('Actual model answer is missing.')
    elif hashlib.sha256(answer.encode('utf-8')).hexdigest()!=record.get('answerSha256'):issues.append('Answer fingerprint mismatch.')
    for name in ('candidateSha256','modelIdentity','isolatedProfile','inputSha256'):
        if not isinstance(record.get(name),str) or not record[name]:issues.append(name+' is missing.')
    if not isinstance(record.get('candidateSha256'),str) or not re.fullmatch('[0-9a-f]{64}',record['candidateSha256']):issues.append('Candidate SHA-256 must be an actual fingerprint.')
    calls=record.get('modelCalls')
    if type(calls) is not int or calls<1:issues.append('Actual model call count must be positive.')
    first=record.get('firstVisibleSeconds');total=record.get('totalSeconds')
    valid_time=lambda v:type(v) in (int,float) and math.isfinite(v) and v>=0
    if not valid_time(first) or not valid_time(total) or first>total:issues.append('Comparable visible-output timing is invalid.')
    fitting=record.get('contextFitting')
    if not isinstance(fitting,dict) or type(fitting.get('contextShortened')) is not bool:issues.append('Context-fitting observation is missing.')
    resources=record.get('resources')
    if not isinstance(resources,dict) or type(resources.get('available')) is not bool:issues.append('Resource observation/availability is missing.')
    elif not resources['available'] and not resources.get('reason'):issues.append('Unavailable resources need an explicit reason.')
    elif resources['available']:
        if type(resources.get('samples')) is not int or resources['samples']<1:issues.append('Available resource telemetry needs actual samples.')
        if not valid_time(resources.get('minimumAvailableRamBytes')):issues.append('Minimum RAM observation is missing.')
    if not isinstance(record.get('sourceEvidence'),list):issues.append('Source evidence list is missing (empty is allowed when not applicable).')
    review=record.get('independentReview')
    decisions={}
    if not isinstance(review,dict) or not review.get('reviewer') or not isinstance(review.get('criteria'),list):issues.append('Independent criterion review is missing.')
    else:
        for decision in review['criteria']:
            if not isinstance(decision,dict):issues.append('Invalid criterion decision.');continue
            cid=decision.get('id')
            if not isinstance(cid,str) or cid not in ids or cid in decisions:issues.append('Unknown/duplicate criterion decision.');continue
            state=decision.get('status')
            if state not in ('PASS','FAIL','UNKNOWN') or not isinstance(decision.get('basis'),str) or not decision['basis'].strip():issues.append('Each decision needs a state and independent evidence basis.')
            decisions[cid]=state
    missing=[cid for cid in ids if cid not in decisions]
    if issues:status='INVALID'
    elif record.get('safetyStop') or resources.get('available') and resources['minimumAvailableRamBytes']<512*1024**2:status='SAFETY_STOP'
    elif any(s=='FAIL' for s in decisions.values()):status='FAIL'
    elif missing or any(s=='UNKNOWN' for s in decisions.values()):status='INCOMPLETE'
    else:status='PASS'
    return {'status':status,'issues':issues,'missingCriteria':missing,
            'criterionResults':decisions,'runtimeValidationIsSemanticProof':False}
