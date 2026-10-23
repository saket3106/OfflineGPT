"""Journaled same-volume repository relocation primitive.

This is not a source publisher, runtime upgrader, installer or authorization
receipt. A reviewed adoption coordinator must supply its existing source/build
locks and independently validate exact accepted worker/profile/CI bindings at
prepare, apply, recovery and rollback. No live CLI or scheduler is registered.
"""
from contextlib import ExitStack
import configparser
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

class RelocationError(RuntimeError):pass

def sha(data):return hashlib.sha256(data).hexdigest()
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':')).encode('utf-8')
def ordinary(value):
    p=Path(value)
    if not p.is_absolute():raise RelocationError('Absolute path required')
    for node in (p,*p.parents):
        if node.exists() or node.is_symlink():
            if node.is_symlink() or getattr(node.lstat(),'st_file_attributes',0)&0x400:raise RelocationError('Linked/reparse paths are forbidden')
    return p.resolve()
def atomic(path,data):
    ordinary(path)
    temporary=path.with_name('.relocation-'+uuid.uuid4().hex)
    try:
        with temporary.open('xb') as out:out.write(data);out.flush();os.fsync(out.fileno())
        os.replace(temporary,path)
    finally:
        if temporary.exists():temporary.unlink()
def snapshot(root):
    root=ordinary(root);values={};seen=set()
    for base,dirs,names in os.walk(root,topdown=True,followlinks=False):
        for name in sorted(dirs+names):
            path=Path(base)/name;ordinary(path);rel=path.relative_to(root).as_posix()
            if rel.casefold() in seen:raise RelocationError('Case collision')
            seen.add(rel.casefold())
            if path.is_file():
                if path.stat().st_nlink!=1:raise RelocationError('Linked repository input')
                # The existing Windows byte mutex may be unreadable through a
                # second handle while locked. Preserve its actual file identity,
                # never treat a business/source file as an ignorable lock.
                if rel=='.offlinegpt-autosync-mutex':
                    info=path.stat()
                    if info.st_size!=1:raise RelocationError('Unexpected publication mutex size')
                    values[rel]='OS-MUTEX:'+str(info.st_dev)+':'+str(info.st_ino)+':'+str(info.st_size)
                else:values[rel]=sha(path.read_bytes())
            elif not path.is_dir():raise RelocationError('Non-ordinary repository input')
    return values

def git_state(root,git):
    config=configparser.ConfigParser(interpolation=None,strict=True)
    config.read(root/'.git/config',encoding='utf-8')
    if any(section.lower().startswith(('include','filter ')) for section in config.sections()):raise RelocationError('External Git configuration requires separate adoption')
    if any(config.has_option('core',option) for option in ('worktree','hookspath')) or config.get('core','fsmonitor',fallback='false').lower() not in {'false','0','no'}:raise RelocationError('Redirected or executable Git configuration requires separate adoption')
    if (root/'.git/objects/info/alternates').exists():raise RelocationError('External Git object stores cannot be moved implicitly')
    def command(*args):
        env={**os.environ,'GIT_OPTIONAL_LOCKS':'0'}
        p=subprocess.run([str(git),'--no-replace-objects','-C',str(root),*args],capture_output=True,timeout=30,env=env)
        if p.returncode:raise RelocationError('Repository identity read failed')
        return p.stdout
    if command('ls-files','--stage','--','.offlinegpt-autosync-mutex').strip():raise RelocationError('Publication mutex cannot be tracked source')
    if Path(command('rev-parse','--show-toplevel').decode().strip()).resolve()!=root or not (root/'.git').is_dir():raise RelocationError('Ordinary repository required')
    if Path(command('rev-parse','--absolute-git-dir').decode().strip()).resolve()!=root/'.git' or (root/'.git/commondir').exists():raise RelocationError('Shared or redirected Git metadata is unsupported')
    return {'head':command('rev-parse','HEAD').decode().strip(),'branch':command('branch','--show-current').decode().strip(),'index':sha(command('ls-files','--stage','-z')),'status':sha(command('status','--porcelain=v1','-z')),'config':sha((root/'.git/config').read_bytes())}

class Relocation:
    """Existing adoption coordinator supplies guards; this primitive never grants them."""
    def __init__(self,journal_root,git,adapter):
        self.root=ordinary(journal_root);self.git=git;self.adapter=adapter
        self.root.mkdir(parents=True,exist_ok=True)
    def locked(self):
        stack=ExitStack()
        try:
            # The adapter must acquire the EXISTING source and candidate locks.
            for context in self.adapter.existing_locks():stack.enter_context(context)
            return stack
        except BaseException:stack.close();raise
    def record(self,directory,value,status,**fields):
        value={**value,**fields,'status':status,'updated':time.time()}
        event=directory/'events';event.mkdir(exist_ok=True)
        atomic(event/(uuid.uuid4().hex+'.json'),canonical(value));atomic(directory/'journal.json',canonical(value));return value
    def prepare(self,repository,destination,consumers,bindings):
        with self.locked():
            repo=ordinary(repository);dest=ordinary(destination)
            if dest.exists() or not dest.parent.is_dir() or repo==dest or dest.is_relative_to(repo) or repo.is_relative_to(dest):raise RelocationError('Fresh disjoint destination required')
            if repo.stat().st_dev!=dest.parent.stat().st_dev:raise RelocationError('Same-volume rename required')
            if self.root.is_relative_to(repo) or self.root.is_relative_to(dest):raise RelocationError('Recovery journal must remain outside moved repository')
            if any(json.loads(p.read_text(encoding='utf-8')).get('status') not in {'COMPLETE','ROLLED_BACK'} for p in self.root.glob('*/journal.json')):raise RelocationError('Resolve existing relocation journal first')
            self.adapter.verify(bindings,repo,dest,phase='prepare')
            identity=git_state(repo,self.git);files=snapshot(repo);items=[];seen=set()
            for path,data in consumers:
                p=ordinary(path)
                if p.is_relative_to(repo) or p.is_relative_to(dest) or str(p).casefold() in seen:raise RelocationError('Duplicate or in-repository consumer')
                seen.add(str(p).casefold())
                if not p.is_file() or p.stat().st_nlink!=1 or not isinstance(data,bytes):raise RelocationError('Existing ordinary consumer and reviewed bytes required')
                self.adapter.verify_consumer(p,data)
                items.append({'path':str(p),'before':sha(p.read_bytes()),'after':sha(data),'newData':data.hex()})
            directory=self.root/uuid.uuid4().hex;directory.mkdir();(directory/'before').mkdir()
            for n,item in enumerate(items):
                with (directory/'before'/str(n)).open('xb') as out:out.write(Path(item['path']).read_bytes());out.flush();os.fsync(out.fileno())
            plan={'version':1,'repository':str(repo),'destination':str(dest),'git':str(self.git),'identity':identity,'files':files,'consumers':items,'bindings':bindings}
            with (directory/'plan.json').open('xb') as out:out.write(canonical(plan));out.flush();os.fsync(out.fileno())
            self.record(directory,{'planSha256':sha(canonical(plan))},'PREPARED');return directory
    def load(self,directory):
        directory=ordinary(directory)
        if directory.parent!=self.root:raise RelocationError('Unknown journal')
        value=json.loads(ordinary(directory/'journal.json').read_text(encoding='utf-8'));raw=ordinary(directory/'plan.json').read_bytes()
        if sha(raw)!=value['planSha256']:raise RelocationError('Plan identity changed')
        plan=json.loads(raw)
        if plan['git']!=str(self.git):raise RelocationError('Git executable selection changed')
        for n,item in enumerate(plan['consumers']):
            if sha(ordinary(directory/'before'/str(n)).read_bytes())!=item['before'] or sha(bytes.fromhex(item['newData']))!=item['after']:raise RelocationError('Recovery bytes changed')
        return directory,plan,value
    def location(self,plan):
        old=ordinary(plan['repository']);new=ordinary(plan['destination'])
        if old.exists()==new.exists():raise RelocationError('Ambiguous repository locations; no inferred success')
        actual=old if old.exists() else new
        # Absence alone NEVER establishes successful relocation.
        if snapshot(actual)!=plan['files'] or git_state(actual,self.git)!=plan['identity']:raise RelocationError('Repository, history or staged work changed')
        return old,new,actual
    def consumer_states(self,plan):
        states=[]
        for item in plan['consumers']:
            p=ordinary(item['path'])
            if not p.is_file() or p.stat().st_nlink!=1:raise RelocationError('Consumer missing or linked')
            digest=sha(p.read_bytes())
            if digest not in {item['before'],item['after']}:raise RelocationError('Consumer changed outside reviewed transition')
            states.append(digest)
        return states
    def apply(self,directory,*,recover=False):
        with self.locked():
            directory,plan,value=self.load(directory)
            if value['status'] in {'COMPLETE','ROLLED_BACK','ROLLBACK_PENDING'}:raise RelocationError('Terminal or rollback transition')
            if not recover and value['status']!='PREPARED':raise RelocationError('Explicit interrupted-operation recovery required')
            self.adapter.verify(plan['bindings'],Path(plan['repository']),Path(plan['destination']),phase='recover' if recover else 'apply')
            old,new,actual=self.location(plan);states=self.consumer_states(plan)
            if not recover and (actual!=old or any(state!=item['before'] for state,item in zip(states,plan['consumers']))):raise RelocationError('Prepared state changed')
            if actual==old:
                value=self.record(directory,value,'RENAME_PENDING');os.rename(old,new);self.adapter.checkpoint('after_rename')
                self.location(plan)
            value=self.record(directory,value,'CONSUMERS_PENDING')
            for index,item in enumerate(plan['consumers']):
                states=self.consumer_states(plan)
                if states[index]!=item['after']:atomic(Path(item['path']),bytes.fromhex(item['newData']))
                self.adapter.checkpoint('after_consumer_'+str(index))
            self.location(plan)
            if any(state!=item['after'] for state,item in zip(self.consumer_states(plan),plan['consumers'])):raise RelocationError('Consumer transition incomplete')
            self.adapter.verify_after(plan,new)
            return self.record(directory,value,'COMPLETE',repositoryVerified=True,stagedIndexPreserved=True,installed=False,tasksEnabled=False)
    def rollback(self,directory):
        with self.locked():
            directory,plan,value=self.load(directory)
            if value['status']=='ROLLED_BACK':raise RelocationError('Already rolled back')
            self.adapter.verify(plan['bindings'],Path(plan['repository']),Path(plan['destination']),phase='rollback')
            old,new,actual=self.location(plan);self.consumer_states(plan)
            value=self.record(directory,value,'ROLLBACK_PENDING')
            for n,item in enumerate(plan['consumers']):
                if sha(Path(item['path']).read_bytes())!=item['before']:atomic(Path(item['path']),(directory/'before'/str(n)).read_bytes())
                self.adapter.checkpoint('rollback_consumer_'+str(n))
            if actual==new:os.rename(new,old);self.adapter.checkpoint('rollback_rename')
            self.location(plan)
            if any(state!=item['before'] for state,item in zip(self.consumer_states(plan),plan['consumers'])):raise RelocationError('Rollback consumers incomplete')
            self.adapter.verify_rollback(plan,old)
            return self.record(directory,value,'ROLLED_BACK',repositoryVerified=True,stagedIndexPreserved=True)
