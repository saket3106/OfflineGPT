"""Evidence-gated documentation proposals; never commit, push, release or build.

Private ledger/receipts are inputs, not publication text. A separate reviewed
contract supplies bounded public wording. Hashes detect drift, not dishonesty
by the same local account. Semantic correctness still needs independent review.
"""
from __future__ import annotations
import argparse
import importlib.util
import json
import math
from pathlib import Path
import re
import time
import uuid

spec=importlib.util.spec_from_file_location('milestone_sync',Path(__file__).with_name('auto_sync.py'))
auto=importlib.util.module_from_spec(spec);spec.loader.exec_module(auto)
s=auto.sync;Error=s.SyncError
CONTRACTS='docs/milestone-contracts.json'
START='<!-- offlinegpt:verified-milestones:start -->'
END='<!-- offlinegpt:verified-milestones:end -->'
KINDS={'engineering_infrastructure':'Engineering infrastructure (non-native)',
       'application_capability':'Application capability',
       'application_performance':'Measured application performance',
       'compatibility':'Compatibility'}
REVIEWS={'independentCorrectness','publicContent','dependenciesAndLicenses','limitations'}

def require(condition,message):
    if not condition:raise Error(message)

def read(path):
    require(path.is_file() and path.stat().st_size<=2*1024*1024,'Missing or oversized evidence')
    try:return s.load(path)
    except (ValueError,UnicodeError):raise Error('Malformed private evidence') from None

def ordinary(path):
    path=Path(path)
    require(path.is_absolute(),'Private paths must be absolute')
    for part in [path,*path.parents]:
        if part.exists() or part.is_symlink():require(not s.linked(part),'Link/reparse path rejected')
    return path.resolve()

def text(value):
    require(isinstance(value,str) and 5<=len(value)<=500 and '\n' not in value,'Invalid public milestone text')
    # Public prose is deliberately plain: no embedded links, markup, paths or
    # receipt values. Fixed scope labels keep infrastructure distinct from AI.
    require(not re.search(r'[<>`\[\]\\]|https?://|\b[A-Za-z]:',value),'Public text contains markup or paths')
    require(not s.scan_blob('docs/milestone-prose.md',value.encode()),'Unsafe public milestone text')
    return value

def contracts(repo):
    raw=s.file_bytes(repo,CONTRACTS);require(raw is not None,'Missing reviewed contracts')
    value=json.loads(raw);require(set(value)=={'version','contracts'} and value['version']==1,'Invalid contract schema')
    result={}
    for item in value['contracts']:
        require(set(item)=={'id','kind','title','summary','limitation','inputs','evidenceKind','checks','minimumImprovement'},'Invalid contract fields')
        identifier=item['id'];require(isinstance(identifier,str) and re.fullmatch('[a-z][a-z0-9-]{4,70}',identifier) and identifier not in result,'Duplicate/invalid contract')
        require(item['kind'] in KINDS,'Unknown milestone kind')
        for field in ('title','summary','limitation'):text(item[field])
        for field in ('inputs','checks'):
            names=item[field];require(isinstance(names,list) and 1<=len(names)<=100 and len(set(names))==len(names),'Missing/duplicate contract criteria')
            require(all(isinstance(n,str) and re.fullmatch('[A-Za-z0-9_./ -]{1,120}',n) for n in names),'Invalid contract criteria')
        for name in item['inputs']:s.safe_path(repo,name)
        allowed={'engineering_infrastructure':{'windows_integration'},'application_capability':{'packaged_e2e','native_semantic'},'application_performance':{'performance_comparison'},'compatibility':{'clean_windows'}}
        require(item['evidenceKind'] in allowed[item['kind']],'Evidence scope does not support milestone kind')
        threshold=item['minimumImprovement'];require(type(threshold) in (int,float) and math.isfinite(threshold) and 0<=threshold<1,'Invalid performance threshold')
        if item['kind']=='application_performance':require(threshold>=.15,'Performance claims require meaningful improvement')
        result[identifier]=item
    return result

def check_proof(proof,contract,commit,inputs):
    require(set(proof)=={'version','sourceCommit','inputs','kind','execution','modelInvocations','checks','measurements','artifacts'},'Invalid proof fields')
    require(proof['version']==1 and proof['sourceCommit']==commit and proof['inputs']==inputs,'Proof source/input identity mismatch')
    require(proof['kind']==contract['evidenceKind'] and proof['execution']=='actual','Mock, source CI or incompatible proof cannot certify a milestone')
    calls=proof['modelInvocations'];require(type(calls) is int and calls>=0,'Invalid inference telemetry')
    if contract['evidenceKind']=='native_semantic':require(calls>0,'Native semantic proof must actually invoke a model')
    checks=proof['checks'];require(isinstance(checks,list) and len(checks)==len(contract['checks']),'Missing or extra acceptance checks')
    actual={}
    for check in checks:
        require(isinstance(check,dict) and set(check)=={'id','passed','independentlyReviewed'},'Invalid acceptance check')
        require(check['id'] not in actual and check['id'] in contract['checks'],'Duplicate/unknown check')
        require(check['passed'] is True and check['independentlyReviewed'] is True,'Failed or unreviewed correctness criterion')
        actual[check['id']]=check
    require(set(actual)==set(contract['checks']),'Acceptance check coverage incomplete')
    if contract['kind']=='application_performance':
        m=proof['measurements'];require(set(m)=={'baselineMs','updatedMs','sameHardware','sameWorkload','qualityAccepted','coldWarmSeparated'},'Invalid comparative measurements')
        for name in ('sameHardware','sameWorkload','qualityAccepted','coldWarmSeparated'):require(m[name] is True,'Incomparable performance evidence')
        for name in ('baselineMs','updatedMs'):
            require(isinstance(m[name],list) and len(m[name])>=3 and len(m[name])<=30,'Insufficient bounded samples')
            require(all(type(x) in (int,float) and math.isfinite(x) and x>0 for x in m[name]),'Invalid timing measurement')
        import statistics
        improvement=1-statistics.median(m['updatedMs'])/statistics.median(m['baselineMs'])
        require(improvement>=contract['minimumImprovement'],'Performance change below reviewed threshold')
    else:require(proof['measurements']=={},'Unexpected performance claim')

class Milestones:
    def __init__(self,manager,state):
        self.manager=manager;self.repo=manager.repo;self.state=ordinary(state)
        for protected in (manager.source,manager.repo):
            require(not self.state.is_relative_to(protected) and not protected.is_relative_to(self.state),'Milestone state overlaps application/source')
        # A dedicated child of the existing private manager root prevents a
        # mistaken production/model/profile destination without new config.
        require(self.state.parent==manager.state and self.state.name=='milestones','Use private manager milestones child only')
        self.state.mkdir(exist_ok=True)
    def accepted(self):
        self.manager.guard();commit=self.manager.head()
        self.manager.remote_policy();require(self.manager.remote_head()==commit,'Remote drift')
        ci=self.manager.ci(commit);require(ci.get('status')=='completed' and ci.get('conclusion')=='success','Exact remote source CI has not passed')
        return commit,ci
    def evaluate(self,evidence):
        evidence=ordinary(evidence);require(evidence.is_relative_to(self.state),'Evidence must remain in private milestone state')
        ledger_path=ordinary(self.manager.state/'CHANGE_LEDGER.json');ledger=read(ledger_path)
        require(isinstance(ledger,list) and len(ledger)<=10000,'Invalid/oversized maintenance ledger')
        batches={}
        for row in ledger:
            require(isinstance(row,dict) and isinstance(row.get('batch'),str) and row['batch'] not in batches,'Ambiguous ledger batch')
            batches[row['batch']]=row
        cert=read(evidence);require(set(cert)=={'version','ledgerSha256','entries'} and cert['version']==1,'Invalid certification schema')
        require(cert['ledgerSha256']==s.digest(ledger_path.read_bytes()),'Maintenance ledger changed after independent review')
        require(isinstance(cert['entries'],list) and len(cert['entries'])<=100,'Too many milestone certifications')
        definitions=contracts(self.repo);eligible=[];seen=set()
        for item in cert['entries']:
            require(set(item)=={'batch','contract','sourceCommit','inputs','proof','proofSha256','reviews'},'Invalid milestone certification')
            require(item['batch'] in batches and item['contract'] in definitions and item['contract'] not in seen,'Unknown or duplicate milestone')
            seen.add(item['contract']);row=batches[item['batch']];definition=definitions[item['contract']]
            commit=item['sourceCommit'];require(isinstance(commit,str) and re.fullmatch('[0-9a-f]{40}',commit) and row.get('commit')==commit,'Ledger source identity mismatch')
            ci=row.get('ci',{});require(ci.get('head_sha')==commit and ci.get('status')=='completed' and ci.get('conclusion')=='success','Ledger does not establish accepted source')
            observed=self.manager.ci(commit);require(observed.get('status')=='completed' and observed.get('conclusion')=='success','Historical source CI acceptance is not independently established')
            require(set(item['reviews'])==REVIEWS and all(item['reviews'][k] is True for k in REVIEWS),'Independent correctness/publication review is required')
            require(set(item['inputs'])==set(definition['inputs']),'Incomplete source-scope binding')
            # Check exit status: empty stdout alone never proves ancestry.
            self.manager.git('merge-base','--is-ancestor',commit,'HEAD')
            for name,expected in item['inputs'].items():
                data=self.manager.git('show',commit+':'+name)
                require(s.digest(data)==expected and self.manager.git('show','HEAD:'+name)==data,'Milestone source scope changed or evidence is stale')
            proof_path=s.safe_path(self.state,item['proof']);proof=read(proof_path)
            require(s.digest(proof_path.read_bytes())==item['proofSha256'],'Proof digest mismatch')
            check_proof(proof,definition,commit,item['inputs'])
            require(isinstance(proof['artifacts'],list) and 1<=len(proof['artifacts'])<=30,'Actual execution artifacts required')
            paths=set()
            for artifact in proof['artifacts']:
                require(set(artifact)=={'path','sha256'} and artifact['path'] not in paths,'Invalid/duplicate execution artifact')
                paths.add(artifact['path']);path=s.safe_path(self.state,artifact['path'])
                require(path.is_file() and path.stat().st_size<=10*1024*1024 and s.digest(path.read_bytes())==artifact['sha256'],'Execution artifact absent or changed')
            eligible.append({'id':definition['id'],'kind':definition['kind'],'title':definition['title'],'summary':definition['summary'],'limitation':definition['limitation'],'sourceCommit':commit})
        return {'eligible':sorted(eligible,key=lambda x:x['id']),'maintenanceCount':len(ledger),'certifiedCount':len(eligible),'ledgerSha256':cert['ledgerSha256'],'evidenceSha256':s.digest(evidence.read_bytes())}
    def render(self,selection):
        before={name:s.file_bytes(self.repo,name) for name in ('README.md','CHANGELOG.md')}
        return self.render_bytes(before,selection)
    def render_bytes(self,before,selection):
        require(before['README.md'] is not None,'README missing')
        readme=before['README.md'].decode('utf-8');changelog=(before['CHANGELOG.md'] or b'# Verified milestones\n\nThis records independently reviewed milestones, not releases.\n').decode('utf-8')
        for value in (readme,changelog):require(value.count(START)==value.count(END) and value.count(START)<=1 and (START not in value or value.index(START)<value.index(END)),'Ambiguous managed documentation markers')
        # Append-only per contract identity. Existing public records remain
        # byte-preserved; source-scope changes require a new reviewed contract.
        additions=[]
        for row in selection:
            token='<!-- milestone:'+row['id']+' -->'
            require((token in readme)==(token in changelog),'README/changelog milestone history disagrees')
            if token in readme:continue
            additions.append(token+'\n### '+row['title']+'\n\n'+KINDS[row['kind']]+'. '+row['summary']+'\n\nLimitations: '+row['limitation']+'\n\nAccepted source: `'+row['sourceCommit']+'`.\n')
        if not additions:return before,before
        block='\n\n'.join(additions)+'\n'
        def append(value):
            if START in value:return value.replace(END,block+END,1)
            return value.rstrip()+'\n\n'+START+'\n'+block+END+'\n'
        return before,{'README.md':append(readme).encode(),'CHANGELOG.md':append(changelog).encode()}
    def prepare(self,evidence):
        with self.manager.publication_lock():
            commit,ci=self.accepted();identity=self.manager.git_identity();tree=s.tree_hashes(self.repo,self.manager.cfg['git'])
            result=self.evaluate(evidence);before,after=self.render(result['eligible'])
            plan={'version':1,'id':uuid.uuid4().hex,'status':'PREPARED','head':commit,'ci':ci,'gitIdentity':identity,'tree':tree,'classification':result,
                  'evidence':str(ordinary(evidence)),'before':{k:v.decode() if v is not None else None for k,v in before.items()},'after':{k:v.decode() if v is not None else None for k,v in after.items()}}
            plan['digest']=s.digest(s.canonical(plan));folder=self.state/plan['id'];folder.mkdir();s.atomic_write(folder/'plan.json',s.canonical(plan))
            return {'id':plan['id'],'digest':plan['digest'],'status':'NO_DOCUMENT_CHANGES' if before==after else 'AWAITING_PUBLIC_CONTENT_REVIEW','classification':result}
    def load_plan(self,identifier):
        require(isinstance(identifier,str) and re.fullmatch('[0-9a-f]{32}',identifier),'Invalid plan ID')
        folder=s.safe_path(self.state,identifier);plan=read(folder/'plan.json');digest=plan.get('digest');require(digest==s.digest(s.canonical({k:v for k,v in plan.items() if k!='digest'})),'Plan was altered')
        require(plan['id']==identifier and plan['status']=='PREPARED','Invalid plan lifecycle')
        require(set(plan['before'])==set(plan['after'])=={'README.md','CHANGELOG.md'},'Unexpected documentation targets')
        for side in ('before','after'):
            require(all(value is None or isinstance(value,str) for value in plan[side].values()),'Invalid document bytes')
        return folder,plan
    def apply(self,identifier,digest,reviewed):
        with self.manager.publication_lock():
            folder,plan=self.load_plan(identifier);require(reviewed is True and digest==plan['digest'],'Exact public-content approval required')
            commit,_=self.accepted();require(commit==plan['head'] and self.manager.git_identity()==plan['gitIdentity'] and s.tree_hashes(self.repo,self.manager.cfg['git'])==plan['tree'],'Repository/index changed after proposal')
            current=self.evaluate(plan['evidence']);require(current==plan['classification'],'Evidence changed after proposal')
            before,after=self.render(current['eligible']);require({k:v.decode() if v is not None else None for k,v in after.items()}==plan['after'],'Rendered contract text changed')
            if before==after:return {'status':'NO_DOCUMENT_CHANGES','paths':[],'staged':False,'committed':False,'pushed':False}
            for name,data in after.items():require(not s.scan_blob(name,data),'Public documentation safety check failed')
            journal={'status':'APPLYING','planDigest':digest,'head':commit,'before':plan['before'],'after':plan['after']};s.atomic_write(folder/'journal.json',s.canonical(journal))
            for name,data in after.items():
                if data!=before[name]:s.atomic_write(s.safe_path(self.repo,name),data)
            require({k:s.file_bytes(self.repo,k) for k in after}==after,'Document write verification failed')
            journal.update(status='APPLIED',applied=time.time(),staged=False,committed=False,pushed=False);s.atomic_write(folder/'journal.json',s.canonical(journal))
            return {'status':'APPLIED','paths':[k for k in after if before[k]!=after[k]],'staged':False,'committed':False,'pushed':False}
    def recover(self,identifier):
        with self.manager.publication_lock():
            folder,plan=self.load_plan(identifier);journal=read(folder/'journal.json')
            require(journal.get('planDigest')==plan['digest'] and journal.get('before')==plan['before'] and journal.get('after')==plan['after'],'Recovery journal mismatch')
            require(journal.get('status') in {'APPLYING','APPLIED'},'Unknown recovery lifecycle')
            require(self.evaluate(plan['evidence'])==plan['classification'],'Recovery evidence changed; retain for review')
            original={k:v.encode() if v is not None else None for k,v in plan['before'].items()}
            _,rendered=self.render_bytes(original,plan['classification']['eligible'])
            require({k:v.decode() if v is not None else None for k,v in rendered.items()}==plan['after'],'Recovery output is not the reviewed rendering')
            for name,data in rendered.items():
                if data is not None:require(not s.scan_blob(name,data),'Unsafe recovery output')
            # No assumption from absence: compare both complete old/new bytes
            # and unchanged Git HEAD/index before completing the fixed writes.
            identity=self.manager.git_identity();require(identity==plan['gitIdentity'],'Git changed; recovery needs review')
            self.manager.guard(clean=False)
            allowed=set(plan['before'])
            actual=s.tree_hashes(self.repo,self.manager.cfg['git']);require({k:v for k,v in actual.items() if k not in allowed}=={k:v for k,v in plan['tree'].items() if k not in allowed},'Unrelated files changed')
            for name in allowed:
                value=s.file_bytes(self.repo,name);require(value in [x.encode() if x is not None else None for x in (plan['before'][name],plan['after'][name])],'Unknown document bytes; preserve for review')
            self.accepted_remote_only(plan['head'])
            for name,value in plan['after'].items():
                if value is not None:s.atomic_write(s.safe_path(self.repo,name),value.encode())
            journal.update(status='APPLIED',recovered=time.time(),staged=False,committed=False,pushed=False);s.atomic_write(folder/'journal.json',s.canonical(journal));return {'status':'APPLIED','recovered':True,'pushed':False}
    def accepted_remote_only(self,head):
        self.manager.remote_policy();require(self.manager.remote_head()==head,'Remote drift during recovery')
        ci=self.manager.ci(head);require(ci.get('status')=='completed' and ci.get('conclusion')=='success','CI acceptance changed')

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',required=True);p.add_argument('command',choices=['prepare','apply','recover']);p.add_argument('--evidence');p.add_argument('--plan');p.add_argument('--digest');p.add_argument('--reviewed-public-content',action='store_true');args=p.parse_args()
    try:
        manager=auto.Manager(args.config);controller=Milestones(manager,manager.state/'milestones')
        if args.command=='prepare':require(args.evidence is not None,'Evidence path required');result=controller.prepare(args.evidence)
        elif args.command=='apply':result=controller.apply(args.plan,args.digest,args.reviewed_public_content)
        else:result=controller.recover(args.plan)
        print(json.dumps(result,indent=2))
    except (Error,OSError,ValueError,KeyError,TypeError) as exc:
        # Private proof payloads may contain secrets; do not echo parser values.
        print(json.dumps({'status':'BLOCKED','reason':str(exc) if isinstance(exc,Error) else 'Invalid or unavailable bounded milestone input'}));raise SystemExit(1)
if __name__=='__main__':main()
