"""Journaled control-only candidate-worker upgrades; no application installation.

Reuse requires identical application inputs and a fresh complete payload check.
Pending builds defer upgrades. No compiled artifact is relabeled as a new build.
"""
import argparse,hashlib,importlib.util,json,os,re,sys,time,uuid
from pathlib import Path
ADMIN={'candidate_upgrade.py','candidate_builder.py','candidate_runner.py','candidate_inputs.py','candidate_smoke.py','build_materialize.py','build_bootstrap.py','build_process.py','auto_sync.py','sync_source.py','audit_repository.py','runtime_upgrade.py','sync_runner.py'}
if __name__=='__main__':
    # Validate the accepted immutable admin snapshot before any sibling import.
    directory=Path(__file__).resolve().parent;manifest_path=directory.parent/'admin-manifest.json'
    try:
        manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
        if not sys.flags.isolated or not sys.flags.no_site or not sys.dont_write_bytecode or directory.parent.name!=manifest['commit'] or not re.fullmatch('[a-f0-9]{40}',manifest['commit']) or set(manifest['scripts'])!=ADMIN:raise ValueError('Invalid frozen admin snapshot')
        for name,expected in manifest['scripts'].items():
            path=directory/name
            if path.is_symlink() or getattr(path.lstat(),'st_file_attributes',0)&0x400 or path.stat().st_nlink!=1 or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:raise ValueError('Admin snapshot changed')
        sys.pycache_prefix=str(directory.parent/('unused-bytecode-'+uuid.uuid4().hex));sys.dont_write_bytecode=True
    except (OSError,ValueError,KeyError,TypeError):raise SystemExit('Use a reviewed exact-CI frozen admin snapshot with -I -S -B')
def sibling(name):
    s=importlib.util.spec_from_file_location('candidate_upgrade_'+name,Path(__file__).with_name(name+'.py'));m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
b=sibling('candidate_builder');r=sibling('candidate_runner');base=sibling('runtime_upgrade');Error=r.Error
DONE={'COMPLETE','ROLLED_BACK'}
def module_at(path,name):
    s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
class Upgrade:
    def __init__(self,manifest,task=None,*,fixture=False):
        self.path=r.ordinary(Path(manifest));self.old,self.paths=r.validate(self.path);self.root=self.paths['privateRoot'];self.directory=self.root/'runtime-upgrades';self.directory.mkdir(exist_ok=True)
        self.fixture=fixture
        if fixture and (task is None or isinstance(task,base.WindowsTask)):raise Error('Fixtures require a disposable task adapter')
        self.task=task or base.WindowsTask('OfflineGPT-VerifiedCandidateBuild')
        self.previous=module_at(self.paths['runtimeRoot']/'scripts/candidate_builder.py','previous_candidate_upgrade')
        self.controller=self.previous.Builder(self.paths['configPath'],fixture=fixture);self.manager=self.controller.manager
    def action(self,manifest):return {'execute':str(Path(manifest['pythonPath']).with_name('pythonw.exe')),'arguments':'-I -S -B "'+str(Path(manifest['runtimeRoot'])/'scripts/candidate_runner.py')+'" --manifest "'+str(self.path)+'"','workingDirectory':str(self.root)}
    def accepted(self,commit):
        self.manager.guard()
        if self.manager.head()!=commit or self.manager.remote_head()!=commit:raise Error('Accepted upgrade head/remote drift')
        self.controller.accepted(commit)
        if not self.fixture:
            if Path(__file__).resolve().parent.parent!=self.root/'admin-runtime'/commit:raise Error('An immutable accepted admin runtime is required')
            for name in ADMIN:
                if hashlib.sha256(self.manager.git('show',commit+':scripts/'+name)).hexdigest()!=r.sha(Path(__file__).with_name(name)):raise Error('Upgrade helper dependency differs from accepted source: '+name)
    def record(self,path,status,**fields):
        value=json.loads((path/'journal.json').read_text(encoding='utf-8')) if (path/'journal.json').exists() else {}
        value.update(status=status,updated=time.time(),**fields);b.save(path/'events'/(uuid.uuid4().hex+'.json'),value);b.save(path/'journal.json',value);return value
    def prepare(self):
        with self.manager.publication_lock(),b.auto.exclusive(self.controller.lock):
            if any(json.loads(p.read_text())['status'] not in DONE for p in self.directory.glob('*/journal.json')):raise Error('Resolve the existing candidate-runtime journal')
            commit=self.manager.head();self.accepted(commit)
            if commit==self.old['commit']:raise Error('Candidate worker already uses this revision')
            task=self.task.read()
            if task['running'] or task['action']!=self.action(self.old):raise Error('Candidate task is active or its action changed')
            history={}
            for path in (self.root/'jobs').iterdir():
                try:
                    _,job=self.controller.job(path.name)
                    if job['status'] not in b.TERMINAL-{'RECOVERY_REQUIRED'}:raise Error('Finish/reconcile pending candidate work before upgrading')
                    history[path.name]={'sha256':r.sha(path/'job.json'),'profileSha256':job['profileSha256'],'status':job['status']}
                except (OSError,ValueError,KeyError,TypeError):continue # Preserve corrupt bytes; the new worker reports attention.
            pointer=json.loads(self.controller.pointer.read_text(encoding='utf-8'));_,built=self.controller.job(pointer['buildKey'])
            if built['status']!='COMPLETE' or (not self.fixture and built.get('actualBuildRun') is not True):raise Error('A genuine completed artifact is required')
            verified=b.retained(pointer,built);graph=b.inputs.inputs(self.manager.repo,commit,self.manager.cfg['git'])
            if verified['files']!=graph['files']:raise Error('Application inputs changed; build/validate the accepted version before a control-only upgrade')
            runtime=self.root/'runtime'/commit;hashes={}
            for name in sorted(r.SCRIPTS):
                data=self.manager.git('show',commit+':scripts/'+name);target=r.ordinary(runtime/'scripts'/name);target.parent.mkdir(parents=True,exist_ok=True)
                if target.exists():
                    if target.read_bytes()!=data:raise Error('Never overwrite a changed frozen runtime')
                else:
                    with target.open('xb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
                hashes[name]=r.sha(target)
            config={**self.controller.cfg,'version':2,'controllerCommit':commit,'historicalJobs':history,'equivalentBaseline':{'applicationInputSha256':graph['applicationInputSha256'],'environmentSha256':self.controller.environment['environmentSha256'],'pointerSha256':r.sha(self.controller.pointer)}}
            profile=self.root/'profiles'/(commit+'.json');r.ordinary(profile)
            if profile.exists():
                if json.loads(profile.read_text())!=config:raise Error('Existing upgrade profile differs')
            else:b.save(profile,config)
            new={**self.old,'commit':commit,'runtimeRoot':str(runtime),'configPath':str(profile),'configSha256':r.sha(profile),'scripts':hashes}
            path=self.directory/uuid.uuid4().hex;path.mkdir();(path/'old-worker.json').write_bytes(self.path.read_bytes());b.save(path/'new-worker.json',new);r.validate(path/'new-worker.json')
            (path/'task-before.xml').write_bytes(task['xml'].encode('utf-8'))
            return self.record(path,'PREPARED',id=path.name,commit=commit,oldSha256=r.sha(path/'old-worker.json'),newSha256=r.sha(path/'new-worker.json'),oldAction=self.action(self.old),newAction=self.action(new),definition=base.definition(task['xml']),wasEnabled=task['enabled'],candidatePointerSha256=r.sha(self.controller.pointer),historicalJobs=history,applicationInputSha256=graph['applicationInputSha256'],actualBuildRun=False,artifactReused=True,fixtureOnly=self.fixture)
    def load(self,identifier):
        if not re.fullmatch('[a-f0-9]{32}',identifier):raise Error('Invalid candidate upgrade ID')
        path=r.ordinary(self.directory/identifier);value=json.loads((path/'journal.json').read_text(encoding='utf-8'))
        for label in ('old','new'):
            p=path/(label+'-worker.json');r.validate(p)
            if r.sha(p)!=value[label+'Sha256']:raise Error('Retained worker manifest changed')
        if r.sha(self.controller.pointer)!=value['candidatePointerSha256']:raise Error('Validated candidate pointer changed; reconcile upgrade')
        for key,expected in value['historicalJobs'].items():
            if r.sha(self.root/'jobs'/key/'job.json')!=expected['sha256']:raise Error('Retained historical job changed')
        if base.definition((path/'task-before.xml').read_text(encoding='utf-8'))!=value['definition']:raise Error('Task backup changed')
        snapshot=self.task.read()
        if base.definition(snapshot['xml'])!=value['definition'] or snapshot['running']:raise Error('Candidate task is active or its settings changed')
        return path,value,snapshot
    def apply(self,identifier):
        with self.manager.publication_lock(),b.auto.exclusive(self.controller.lock):
            path,value,task=self.load(identifier);self.accepted(value['commit'])
            if value['status']!='PREPARED' or r.sha(self.path)!=value['oldSha256'] or task['action']!=value['oldAction'] or task['enabled']!=value['wasEnabled']:raise Error('Old candidate worker no longer matches its reviewed plan')
            self.record(path,'DISABLING');self.task.enable(False)
            if self.task.read()['running']:raise Error('Candidate runner became active; keep disabled for review')
            self.record(path,'POINTER_PENDING');b.auto.sync.atomic_write(self.path,(path/'new-worker.json').read_bytes())
            self.record(path,'ACTION_PENDING');self.task.action(value['newAction']);self.record(path,'VERIFY_PENDING',applied=time.time());self.task.enable(value['wasEnabled'])
            return json.loads((path/'journal.json').read_text())
    def verify(self,identifier):
        with self.manager.publication_lock(),b.auto.exclusive(self.controller.lock):
            path,value,task=self.load(identifier);receipt=json.loads((self.root/'last-worker-run.json').read_text());result=self.task.result()
            if value['status']!='VERIFY_PENDING' or r.sha(self.path)!=value['newSha256'] or task['action']!=value['newAction'] or task['enabled']!=value['wasEnabled'] or result['result']!=0 or result['running'] or receipt['controllerCommit']!=value['commit'] or receipt['returnCode']!=0 or receipt['status'] not in {'IDLE','PAUSED'} or receipt['started']<value['applied'] or not result['lastRun']<=receipt['started']<=result['lastRun']+10:raise Error('Fresh successful scheduled candidate receipt required')
            return self.record(path,'COMPLETE',scheduledReceipt=receipt,verified=time.time(),actualBuildRun=False,artifactReused=True)
    def rollback(self,identifier):
        with self.manager.publication_lock(),b.auto.exclusive(self.controller.lock):
            path,value,task=self.load(identifier)
            if value['status'] in DONE or r.sha(self.path) not in {value['oldSha256'],value['newSha256']} or task['action'] not in (value['oldAction'],value['newAction']):raise Error('Unknown/terminal worker state; do not overwrite it')
            self.record(path,'ROLLBACK_PENDING');self.task.enable(False);b.auto.sync.atomic_write(self.path,(path/'old-worker.json').read_bytes());self.task.action(value['oldAction']);self.task.enable(value['wasEnabled'])
            return self.record(path,'ROLLED_BACK',candidateUnchanged=True)
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--manifest',type=Path,required=True);p.add_argument('command',choices=['prepare','apply','verify','rollback']);p.add_argument('--upgrade');a=p.parse_args();u=Upgrade(a.manifest)
    value=u.prepare() if a.command=='prepare' else getattr(u,a.command)(a.upgrade);print(json.dumps(value,indent=2))
if __name__=='__main__':
    try:main()
    except (RuntimeError,OSError,ValueError,KeyError,TypeError) as error:raise SystemExit('Candidate worker upgrade stopped safely: '+str(error))
