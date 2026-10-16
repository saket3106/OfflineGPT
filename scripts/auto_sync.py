"""Completed-batch publication through sync_source, never a blind folder mirror.

Private config/receipts stay outside both source trees. Only fixed test commands
and non-force Git publication to the configured development branch are exposed.
Agent completion is explicit: stable files alone never authorize publication.
"""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time
import urllib.parse
import urllib.request
import uuid

spec=importlib.util.spec_from_file_location('sync_foundation',Path(__file__).with_name('sync_source.py'))
sync=importlib.util.module_from_spec(spec);spec.loader.exec_module(sync)
Error=sync.SyncError
class NetworkError(Error):pass
TERMINAL={'COMPLETE','NO_CHANGES','BLOCKED','TEST_FAILED','CI_FAILED','CI_TIMED_OUT','RECOVERY_REQUIRED','STALE'}
# The source workflow itself is limited to ten minutes. Allow queue/network
# headroom, but never hold later accepted batches indefinitely.
CI_WAIT_SECONDS=30*60

@contextmanager
def exclusive(path):
    """OS lock is released on crash; the durable journal is never erased."""
    path.parent.mkdir(parents=True,exist_ok=True)
    stream=path.open('a+b')
    try:
        # Reading a byte already locked by another Windows process can itself
        # fail before locking. Stat instead, and close on every failure path.
        if os.fstat(stream.fileno()).st_size==0:stream.write(b'0');stream.flush()
        stream.seek(0)
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    except OSError:
        stream.close();raise Error('Another publication runner owns the lock')
    try:yield
    finally:stream.close()

class Manager:
    def __init__(self,config, *, local_fixture=False):
        self.config_path=Path(config).resolve();self.cfg=sync.load(self.config_path)
        expected={'version','source','repository','stateRoot','python','git','branch','remoteUrl','debounceSeconds','testProfile'}
        if set(self.cfg)!=expected or self.cfg['version']!=1:raise Error('Unsupported or incomplete private configuration')
        for key in ('source','repository','stateRoot','python','git'):
            path=Path(self.cfg[key])
            if not path.is_absolute():raise Error('Configured paths must be absolute')
            for part in [path,*path.parents]:
                if part.exists() and sync.linked(part):raise Error('Configured path has a reparse/link ancestor')
        self.source=Path(self.cfg['source']).resolve();self.repo=Path(self.cfg['repository']).resolve()
        self.state=sync.validate_external(Path(self.cfg['stateRoot']),self.repo,self.source)
        if self.source.is_relative_to(self.repo) or self.repo.is_relative_to(self.source):raise Error('Source and publication trees overlap')
        if not self.config_path.is_relative_to(self.state):raise Error('Configuration must live in private state root')
        if not re.fullmatch(r'codex/[a-z0-9][a-z0-9/-]{1,70}',self.cfg['branch']):raise Error('Only an explicit codex development branch is allowed')
        if self.cfg['testProfile'] not in {'publication','unit'}:raise Error('Unapproved test command profile')
        if type(self.cfg['debounceSeconds']) not in (int,float) or not 0<=self.cfg['debounceSeconds']<=5:raise Error('Invalid bounded debounce')
        if not local_fixture and not re.fullmatch(r'https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\.git',self.cfg['remoteUrl']):raise Error('Expected credential-free GitHub HTTPS remote')
        sync.git_fingerprint(self.repo,self.cfg['git'])
        # One ignored mutex per repository, even with different private state
        # roots. Keep it out of Git metadata: Windows locks prevent hash reads.
        self.lock=self.repo/'.offlinegpt-autosync-mutex'
        if not self.git('check-ignore','--no-index',self.lock.name,check=False).strip():raise Error('Repository mutex must be explicitly ignored')
        if self.lock.exists() and sync.linked(self.lock):raise Error('Repository mutex is a link')
        self.local_fixture=local_fixture;self.state.mkdir(parents=True,exist_ok=True)
        (self.state/'batches').mkdir(exist_ok=True)
        if not (self.state/'control.json').exists():self.write(self.state/'control.json',{'mode':'running'})
    def write(self,path,value):sync.atomic_write(path,sync.canonical(value))
    def git(self,*args,check=True,input=None):
        env={**os.environ,'GIT_OPTIONAL_LOCKS':'0','GIT_TERMINAL_PROMPT':'0','GCM_INTERACTIVE':'Never'}
        try:r=subprocess.run([self.cfg['git'],'-C',str(self.repo),*args],input=input,capture_output=True,env=env,timeout=30)
        except subprocess.TimeoutExpired:raise (NetworkError if args[0] in {'ls-remote','push','credential'} else Error)('Bounded Git operation timed out: '+args[0]) from None
        if check and r.returncode:raise (NetworkError if args[0] in {'ls-remote','push','credential'} else Error)('Git operation failed: '+args[0])
        return r.stdout
    def head(self):return self.git('rev-parse','HEAD').decode().strip()
    def git_identity(self):
        return {'head':self.head(),'index':sync.digest(self.git('ls-files','--stage','-z')),
                'config':sync.digest((self.repo/'.git/config').read_bytes()),
                'headFile':sync.digest((self.repo/'.git/HEAD').read_bytes())}
    def semantic_plan_digest(self,plan):
        # Rebind incidental fetch/object metadata only after all meaningful
        # source/repository/index/HEAD/config identities have been rechecked.
        return sync.digest(sync.canonical({k:v for k,v in plan.items() if k!='gitMetadataDigest'}))
    def guard(self,clean=True):
        if sync.load(self.state/'control.json')['mode']!='running':raise Error('Manager is paused/stopped')
        sync.git_fingerprint(self.repo,self.cfg['git'])
        if self.git('branch','--show-current').decode().strip()!=self.cfg['branch']:raise Error('Publication branch differs from configured development branch')
        if self.git('remote','get-url','origin').decode().strip()!=self.cfg['remoteUrl']:raise Error('Remote identity changed')
        if self.git('config','--get-all','remote.origin.pushurl',check=False).strip():raise Error('Separate push URL requires explicit reconciliation')
        if clean and self.git('status','--porcelain=v1').strip():raise Error('Preserve/reconcile existing repository or index changes first')
        if self.git('config','--get','core.hooksPath',check=False).strip():raise Error('Custom Git hooks require independent review')
        if any(p.is_file() and not p.name.endswith('.sample') for p in (self.repo/'.git/hooks').glob('*')):raise Error('Active Git hooks require independent review')
        for name in ('MERGE_HEAD','CHERRY_PICK_HEAD','REVERT_HEAD','rebase-merge','rebase-apply'):
            if (self.repo/'.git'/name).exists():raise Error('Concurrent Git history operation')
    def source_identity(self):
        policy=sync.load(self.repo/sync.POLICY);sync.validate_policy(policy,self.repo)
        unknown=sorted(sync.source_candidates(self.source)-set(policy['managed'])-set(policy['protected'])-set(policy.get('excluded_candidates',[])))
        if unknown:raise Error('Unclassified source files: '+', '.join(unknown))
        return {p:sync.file_hash(self.source,p) for p in sorted(policy['managed']+policy['protected'])}
    def batch_path(self,identifier):
        if not re.fullmatch('[0-9a-f]{32}',identifier):raise Error('Invalid batch ID')
        path=self.state/'batches'/identifier
        if not path.is_dir() or sync.linked(path):raise Error('Missing or linked batch')
        return path
    def record(self,path,status,**fields):
        p=path/'batch.json';value=sync.load(p) if p.exists() else {}
        value.update(status=status,updated=time.time(),**fields)
        # Individual atomic event records retain failed/ambiguous transitions;
        # batch.json is only the current view, never the sole recovery evidence.
        self.write(path/'events'/(str(time.time_ns())+'-'+uuid.uuid4().hex+'.json'),value)
        self.write(p,value)
        if status=='COMPLETE':
            ledger_path=self.state/'CHANGE_LEDGER.json';ledger=sync.load(ledger_path) if ledger_path.exists() else []
            if not any(item['batch']==value['id'] for item in ledger):
                ledger.append({'batch':value['id'],'title':value['title'],'commit':value['commit'],'ci':value['ci'],
                               'tests':value['tests'],'kind':'maintenance','milestoneCertification':False,'timestamp':time.time()})
                self.write(ledger_path,ledger)
        return value
    def prepare(self,title):
        if not isinstance(title,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 .,():/-]{5,100}',title):raise Error('Use a bounded sanitized batch title')
        with exclusive(self.lock):
            self.guard();before=self.source_identity();repo_before=sync.tree_hashes(self.repo,self.cfg['git']);git_before=self.git_identity()
            time.sleep(self.cfg['debounceSeconds'])
            if before!=self.source_identity():raise Error('Source is still being written')
            path=self.state/'batches'/uuid.uuid4().hex;path.mkdir();snapshot=path/'source';snapshot.mkdir()
            for rel,h in before.items():
                if h is not None:
                    data=sync.file_bytes(self.source,rel)
                    if sync.digest(data)!=h:raise Error('Source changed while snapshotting')
                    sync.atomic_write(sync.safe_path(snapshot,rel),data)
            identity=sync.digest(sync.canonical({'source':before,'repository':repo_before,'head':self.head(),'config':self.cfg}))
            self.record(path,'PREPARING',id=path.name,title=title,snapshotDigest=identity,sourceFiles=before,repositoryFiles=repo_before,
                        parent=self.head(),gitCore=git_before,configSha256=sync.digest(sync.canonical(self.cfg)),created=time.time())
            plan=sync.build_plan(self.repo,snapshot,self.cfg['git']);self.write(path/'plan.json',plan)
            if any(plan[k] for k in ('conflicts','findings','missingRequired','unapprovedCandidates')):
                return self.record(path,'BLOCKED',reason='Existing sync preview has blocking findings',planDigest=sync.digest(sync.canonical(plan)))
            if not plan['actions']:return self.record(path,'NO_CHANGES',reason='No managed application source changed')
            verification=path/'verification';verification.mkdir()
            for rel in repo_before:
                data=sync.file_bytes(self.repo,rel)
                if data is not None:sync.atomic_write(sync.safe_path(verification,rel),data)
            for action in plan['actions']:
                target=sync.safe_path(verification,action['file']);data=sync.file_bytes(snapshot,action['file'])
                if data is None:
                    if target.exists():target.unlink()
                else:sync.atomic_write(target,data)
            command=[self.cfg['python'],'-m','unittest','discover','-s','tests','-q']
            tested={p:sync.file_hash(verification,p) for p in repo_before}
            tested.update({a['file']:sync.file_hash(verification,a['file']) for a in plan['actions']})
            docs_only=all(a['file'].startswith('docs/') for a in plan['actions'])
            profile='publication' if docs_only else self.cfg['testProfile']
            if profile=='publication' and not docs_only:return self.record(path,'BLOCKED',reason='Application/test changes require the unit test profile')
            if profile=='publication':command+=['-p','test_source_sync.py']
            env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','PYTHONUTF8':'1','OFFLINEGPT_TEST_GIT':self.cfg['git']}
            started=time.monotonic()
            try:
                with (path/'tests.log').open('wb') as log:
                    result=subprocess.run(command,cwd=verification,stdout=log,stderr=subprocess.STDOUT,env=env,timeout=180)
                receipt={'returnCode':result.returncode,'elapsedSeconds':time.monotonic()-started,'logSha256':sync.digest((path/'tests.log').read_bytes()),'profile':profile}
            except subprocess.TimeoutExpired:return self.record(path,'TEST_FAILED',reason='Bounded test command exceeded 180 seconds')
            if result.returncode:return self.record(path,'TEST_FAILED',tests=receipt)
            if tested!={p:sync.file_hash(verification,p) for p in tested}:return self.record(path,'STALE',reason='Tests modified their own source inputs',tests=receipt)
            if before!=self.source_identity() or repo_before!=sync.tree_hashes(self.repo,self.cfg['git']) or git_before!=self.git_identity():
                return self.record(path,'STALE',reason='Source/repository/Git changed during verification',tests=receipt)
            refreshed=sync.build_plan(self.repo,snapshot,self.cfg['git'])
            if self.semantic_plan_digest(refreshed)!=self.semantic_plan_digest(plan):return self.record(path,'STALE',reason='Publication projection changed during tests',tests=receipt)
            self.write(path/'plan.json',refreshed)
            return self.record(path,'AWAITING_REVIEW',tests=receipt,planDigest=sync.digest(sync.canonical(refreshed)),semanticPlanDigest=self.semantic_plan_digest(refreshed))
    def accept(self,identifier,identity,reviews):
        with exclusive(self.lock):
            self.guard();path=self.batch_path(identifier);batch=sync.load(path/'batch.json')
            if batch['status']!='AWAITING_REVIEW' or identity!=batch['snapshotDigest'] or set(reviews)!={'complete','content','dependencies','sourceLicenses'} or not all(v is True for v in reviews.values()):raise Error('Exact completed-batch review is required')
            if batch['sourceFiles']!=self.source_identity() or batch['repositoryFiles']!=sync.tree_hashes(self.repo,self.cfg['git']):raise Error('Review is stale')
            if self.git_identity()!=batch['gitCore'] or batch['configSha256']!=sync.digest(sync.canonical(self.cfg)):raise Error('Project identity changed')
            return self.record(path,'ACCEPTED',reviews=reviews,accepted=time.time())
    def remote_head(self):
        raw=self.git('ls-remote','--heads','origin','refs/heads/'+self.cfg['branch']).decode().strip()
        if not raw:return None
        parts=raw.split()
        if len(parts)!=2 or not re.fullmatch('[0-9a-f]{40,64}',parts[0]) or parts[1]!='refs/heads/'+self.cfg['branch']:raise Error('Unexpected remote reference response')
        return parts[0]
    def github_api(self,suffix):
        # Credentials are obtained from the existing Git credential helper,
        # held only in memory, and never written to receipts or error messages.
        credential=self.git('credential','fill',input=('url='+self.cfg['remoteUrl']+'\n\n').encode())
        values=dict(line.split('=',1) for line in credential.decode().splitlines() if '=' in line)
        token=values.get('password')
        if not token:raise NetworkError('CI authentication unavailable')
        slug=self.cfg['remoteUrl'].removeprefix('https://github.com/').removesuffix('.git')
        request=urllib.request.Request('https://api.github.com/repos/'+slug+suffix,headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json','User-Agent':'OfflineGPT-AutoSync'})
        try:
            with urllib.request.urlopen(request,timeout=20) as response:return json.load(response)
        except Exception:raise NetworkError('GitHub policy/CI query unavailable; publication remains unaccepted') from None
    def remote_policy(self):
        if self.local_fixture:return {'fixtureOnly':True}
        repository=self.github_api('')
        slug=self.cfg['remoteUrl'].removeprefix('https://github.com/').removesuffix('.git')
        if repository.get('full_name','').casefold()!=slug.casefold() or repository.get('private') is not True or repository.get('archived') is not False or repository.get('permissions',{}).get('push') is not True:
            raise Error('Private writable repository policy is not established; publication blocked')
        return {'privateVerified':True,'repository':slug,'verified':time.time()}
    def ci(self,commit):
        if self.local_fixture:return {'status':'completed','conclusion':'success','fixtureOnly':True}
        payload=self.github_api('/actions/runs?head_sha='+commit+'&per_page=100')
        runs=[r for r in payload.get('workflow_runs',[]) if r.get('head_sha')==commit and r.get('head_branch')==self.cfg['branch'] and r.get('path')=='.github/workflows/source-safety.yml' and r.get('event')=='push']
        if not runs:return {'status':'pending','reason':'No matching push workflow observed'}
        run=max(runs,key=lambda r:r['id']);return {k:run.get(k) for k in ('id','status','conclusion','html_url','head_sha')}
    def ci_expiry(self,path,batch):
        phase=batch.get('resumeStatus') if batch['status']=='OFFLINE_RETRY' else batch['status']
        if phase!='CI_PENDING':return None
        # Legacy accepted controllers recorded remoteVerified. Migrate that
        # existing observation once; subsequent polls must not reset the age.
        started=batch.get('ciWaitStarted',batch.get('remoteVerified'))
        now=time.time()
        if type(started) not in (int,float) or not math.isfinite(started) or started>now:
            return self.record(path,'RECOVERY_REQUIRED',reason='Missing/invalid CI wait clock; inspect journal before resuming')
        if now-started>=CI_WAIT_SECONDS:
            return self.record(path,'CI_TIMED_OUT',ciWaitStarted=started,ciWaitSeconds=now-started,
                reason='Exact remote workflow did not pass within the bounded CI wait; review remote evidence, never infer success')
        return None
    def publish(self,path):
        batch=sync.load(path/'batch.json')
        expired=self.ci_expiry(path,batch)
        if expired:return expired
        if batch['status']=='OFFLINE_RETRY':batch['status']=batch['resumeStatus']
        self.guard(clean=batch['status'] in {'ACCEPTED','COMMITTED','CI_PENDING'})
        if batch['status'] in {'APPLYING','STAGING','COMMITTING'}:
            # Never guess success from absent files or a disappeared lock.
            return self.record(path,'RECOVERY_REQUIRED',reason='Interrupted publication phase; retain foundation journal/index and inspect exact Git tree before recovery')
        remote_policy=self.remote_policy()
        if batch['status']=='ACCEPTED':
            if batch['sourceFiles']!=self.source_identity() or batch['repositoryFiles']!=sync.tree_hashes(self.repo,self.cfg['git']) or self.head()!=batch['parent']:return self.record(path,'STALE',reason='Accepted snapshot no longer matches live inputs')
            if batch['configSha256']!=sync.digest(sync.canonical(self.cfg)):raise Error('Accepted configuration changed')
            snapshot=path/'source'
            if {p:sync.file_hash(snapshot,p) for p in batch['sourceFiles']}!=batch['sourceFiles']:raise Error('Immutable source snapshot was altered')
            plan=sync.build_plan(self.repo,snapshot,self.cfg['git'])
            if self.git_identity()!=batch['gitCore'] or self.semantic_plan_digest(plan)!=batch['semanticPlanDigest']:raise Error('Reviewed preview/index/config changed')
            exact_digest=sync.digest(sync.canonical(plan))
            if exact_digest!=batch['planDigest']:
                self.write(path/'plan.json',plan)
                batch=self.record(path,'ACCEPTED',planDigest=exact_digest,incidentalGitMetadataRebound=True)
            remote=self.remote_head()
            if remote not in {None,batch['parent']}:return self.record(path,'BLOCKED',reason='Remote branch has concurrent/unreconciled work')
            self.record(path,'APPLYING',remoteBefore=remote)
            result=sync.apply_plan(self.repo,snapshot,self.cfg['git'],path/'plan.json',batch['planDigest'],{'content':True,'dependencies':True,'licenses':True},path/'sync')
            journal=sync.load(Path(result['journal']))
            expected=dict(plan['repositoryFiles']);expected.update({e['file']:e['after'] for e in journal['entries']})
            expected={k:v for k,v in expected.items() if v is not None}
            if {k:v for k,v in sync.tree_hashes(self.repo,self.cfg['git']).items() if v is not None}!=expected:raise Error('Repository changed after transactional apply')
            expected_index={p:sync.manifest_hash(p,sync.file_bytes(self.repo,p)) for p in expected}
            paths=sorted({a['file'] for a in plan['actions']}|{sync.STATE,sync.MANIFEST})
            self.record(path,'STAGING',sync=result,stagedPaths=paths)
            self.git('add','--',*paths)
            receipt=sync.precommit(self.repo,self.cfg['git']);self.write(path/'precommit.json',receipt)
            if receipt['findings'] or receipt['missingRequired'] or receipt['indexedMissingRequired'] or not receipt['manifestMatches'] or not receipt['indexedManifestMatches']:return self.record(path,'RECOVERY_REQUIRED',reason='Actual index validation failed; preserve index for review')
            # Prevent another editor's unrelated staged path joining this commit.
            staged=set(filter(None,self.git('diff','--cached','--name-only','-z').decode().split('\0')))
            if not staged<=set(paths):return self.record(path,'RECOVERY_REQUIRED',reason='Unexpected staged files')
            indexed={p:sync.manifest_hash(p,self.git('show',':'+p)) for p in filter(None,self.git('ls-files','-z').decode().split('\0'))}
            if indexed!=expected_index:return self.record(path,'RECOVERY_REQUIRED',reason='Index differs from the reviewed publication projection')
            if self.head()!=batch['parent'] or self.git('diff','--name-only').strip():return self.record(path,'RECOVERY_REQUIRED',reason='Concurrent edit before commit')
            tree=self.git('write-tree').decode().strip();self.record(path,'COMMITTING',tree=tree)
            self.git('commit','-m',batch['title'],'-m','OfflineGPT-Batch: '+batch['id'])
            commit=self.head()
            if self.git('rev-parse','HEAD^{tree}').decode().strip()!=tree:raise Error('Committed tree differs from verified index')
            batch=self.record(path,'COMMITTED',commit=commit,tree=tree)
        commit=batch['commit']
        if self.head()!=commit or self.git('rev-parse','HEAD^{tree}').decode().strip()!=batch['tree']:raise Error('Accepted commit/branch changed')
        remote=self.remote_head()
        if remote!=commit:
            if remote!=batch.get('remoteBefore'):return self.record(path,'BLOCKED',reason='Concurrent remote update; never force-pushing')
            self.git('push','origin',commit+':refs/heads/'+self.cfg['branch'])
            if self.remote_head()!=commit:raise Error('Remote did not confirm exact commit')
        started=batch.get('ciWaitStarted',batch.get('remoteVerified',time.time()))
        current=self.record(path,'CI_PENDING',remoteCommit=commit,remoteVerified=time.time(),remotePolicy=remote_policy,ciWaitStarted=started)
        ci=self.ci(commit)
        expired=self.ci_expiry(path,current)
        if expired:return expired
        if ci['status']!='completed':return self.record(path,'CI_PENDING',ci=ci)
        if ci.get('conclusion')!='success':return self.record(path,'CI_FAILED',ci=ci,reason='Matching remote source-safety workflow did not pass')
        app_changed=any(not p.startswith(('tests/','docs/','evals/')) for p in batch.get('stagedPaths',[]) if p not in {sync.STATE,sync.MANIFEST})
        return self.record(path,'COMPLETE',ci=ci,completed=time.time(),binaryPublication='BLOCKED_NATIVE_PROVENANCE_AND_LICENSES',candidateBuild='QUEUED_FOR_SEPARATE_VERIFIED_BUILD_CONTROLLER' if app_changed else 'SKIPPED_NO_APPLICATION_CODE_CHANGE')
    def once(self):
        with exclusive(self.lock):
            if sync.load(self.state/'control.json')['mode']!='running':return {'status':'PAUSED'}
            active=[]
            for path in sorted((self.state/'batches').iterdir()):
                if (path/'batch.json').is_file():
                    batch=sync.load(path/'batch.json')
                    if batch['status'] in {'ACCEPTED','APPLYING','STAGING','COMMITTING','COMMITTED','OFFLINE_RETRY','CI_PENDING'}:active.append((batch.get('created',0),path,batch))
            if not active:
                self.guard();plan=sync.build_plan(self.repo,self.source,self.cfg['git'])
                discovery={k:plan[k] for k in ('actions','conflicts','findings','missingRequired','unapprovedCandidates','protectedSourceChanges')}
                discovery.update(observed=time.time(),publicationRequiresCompletedBatch=True)
                self.write(self.state/'discovery.json',discovery)
                return {'status':'IDLE','publicationRequiresCompletedBatch':True,'pendingActions':len(plan['actions']),'unclassified':plan['unapprovedCandidates']}
            _,path,batch=min(active,key=lambda row:row[0])
            expired=self.ci_expiry(path,batch)
            if expired:return expired
            if batch.get('retryAfter',0)>time.time():return {'status':'WAITING_RETRY','batch':path.name}
            try:return self.publish(path)
            except Error as error:
                current=sync.load(path/'batch.json');phase=current['status']
                if isinstance(error,NetworkError) and phase in {'ACCEPTED','COMMITTED','CI_PENDING','OFFLINE_RETRY'}:
                    retries=current.get('retries',0)+1
                    return self.record(path,'OFFLINE_RETRY' if retries<=8 else 'BLOCKED',resumeStatus=current.get('resumeStatus',phase) if phase=='OFFLINE_RETRY' else phase,retries=retries,retryAfter=time.time()+min(1800,60*2**min(retries,5)),reason=str(error))
                return self.record(path,'RECOVERY_REQUIRED' if phase in {'APPLYING','STAGING','COMMITTING'} else 'BLOCKED',reason=str(error))
    def status(self):
        return {'control':sync.load(self.state/'control.json'),'discovery':sync.load(self.state/'discovery.json') if (self.state/'discovery.json').exists() else None,'batches':[sync.load(p/'batch.json') for p in sorted((self.state/'batches').iterdir()) if (p/'batch.json').exists()]}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('command',choices=['status','prepare','accept','run-once','start','pause','stop'])
    parser.add_argument('--title');parser.add_argument('--batch');parser.add_argument('--snapshot')
    for flag in ('complete','content','dependencies','source-licenses'):parser.add_argument('--reviewed-'+flag,action='store_true')
    args=parser.parse_args();manager=Manager(args.config)
    if args.command=='prepare':result=manager.prepare(args.title)
    elif args.command=='accept':result=manager.accept(args.batch,args.snapshot,{'complete':args.reviewed_complete,'content':args.reviewed_content,'dependencies':args.reviewed_dependencies,'sourceLicenses':args.reviewed_source_licenses})
    elif args.command=='run-once':result=manager.once()
    elif args.command=='status':result=manager.status()
    else:
        manager.write(manager.state/'control.json',{'mode':'running' if args.command=='start' else args.command+'ped' if args.command=='stop' else 'paused'});result=manager.status()
    print(json.dumps(result,indent=2))
if __name__=='__main__':
    try:main()
    except (Error,OSError,ValueError,KeyError) as error:raise SystemExit('Auto-Sync stopped safely: '+str(error))
