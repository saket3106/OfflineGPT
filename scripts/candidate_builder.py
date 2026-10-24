"""Private persistent candidate queue and fixed isolated Windows build executor.

Source publication is independent. No production installation, model execution,
downloads, binary upload or automatic permanent-file cleanup is exposed.
"""
from __future__ import annotations
import argparse,ast,ctypes,datetime,hashlib,importlib.util,json,os
from pathlib import Path
import math,re,shutil,subprocess,time,uuid

def sibling(name):
    spec=importlib.util.spec_from_file_location('builder_'+name,Path(__file__).with_name(name+'.py'))
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
auto=sibling('auto_sync');inputs=sibling('candidate_inputs');material=sibling('build_materialize');process=sibling('build_process')
Error=inputs.CandidateError
class ReuseRefused(Error):pass
class Deferred(Error):pass
SCRIPTS={'candidate_runner.py','candidate_builder.py','candidate_inputs.py','candidate_smoke.py','build_materialize.py','build_bootstrap.py','build_process.py','auto_sync.py','sync_source.py','audit_repository.py'}
ACTIVE={'PREPARING','MATERIALIZING','FRONTEND','PACKAGING','VERIFYING','SMOKE','STAGING','PROMOTING'}
TERMINAL={'COMPLETE','ASSOCIATED','FAILED','BLOCKED','CANCELLED','SUPERSEDED','RECOVERY_REQUIRED'}
def save(path,value):auto.sync.atomic_write(path,json.dumps(value,sort_keys=True,indent=2).encode())
def digest_file(path):return inputs.sha(path)

def genuine_completion(job,key,*,fixture=False):
    """A historical hash seals bytes; it does not establish build acceptance."""
    if job.get('status')!='COMPLETE' or fixture:return
    phases=job.get('phaseReceipts')
    if (job.get('actualBuildRun') is not True or job.get('fixtureOnly') is True
        or not isinstance(job.get('pointerAfter'),dict) or job['pointerAfter'].get('buildKey')!=key
        or not isinstance(phases,dict) or set(phases)!={'FRONTEND','PACKAGING','VERIFYING','SMOKE'}
        or any(not isinstance(row,dict) or row.get('fixtureOnly') is True
               or type(row.get('returnCode')) is not int or row['returnCode']!=0
               or type(row.get('remainingOwnedProcesses')) is not int or row['remainingOwnedProcesses']!=0
               for row in phases.values())):
        raise Error('Job marked complete without genuine build evidence')
def powershell():return str(Path(os.environ.get('SystemRoot',r'C:\Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe')
def powershell_environment():return {**os.environ,'PSModulePath':str(Path(powershell()).parent/'Modules')}
SMOKE_CHECKS={'devtools_connected','real_webchannel_bridge','composer_visible','chat_store_bridge','synthetic_sqlite_bridge_write','synthetic_sqlite_bridge_read','no_observed_native_model_child','normal_exit','isolated_database_integrity','no_messages_in_fresh_profile','bridge_database_chat_ids_match'}
def acceptance(smoke,verification,job,exe_hash):
    checks=smoke.get('checks',{});modules=verification.get('compiledModuleMatches',{});build_only=verification.get('buildOnlyInputMatches',{})
    roles_valid=isinstance(build_only,dict) and all(inputs.build_only_descriptor(name,job['graph']['files']) is not None and value==inputs.build_only_descriptor(name,job['graph']['files']) for name,value in build_only.items())
    expected={name for name in job['graph']['files'] if '/' not in name and name.endswith('.py')}
    if (smoke.get('status')!='PASSED' or smoke.get('remainingOwnedPids')!=[] or smoke.get('modelRequestsIssued')!=0
        or smoke.get('exeSha256')!=exe_hash or set(checks)!=SMOKE_CHECKS or any(v is not True for v in checks.values())
        or not expected or not roles_valid or not isinstance(modules,dict) or bool(set(modules)&set(build_only)) or set(modules)|set(build_only)!=expected or any(v is not True for v in modules.values())
        or verification.get('candidateSha256')!=exe_hash or verification.get('sourceCommit')!=job['sourceCommit']
        or verification.get('applicationInputSha256')!=job['graph']['applicationInputSha256']):
        raise Error('Actual packaged acceptance evidence is incomplete or inconsistent')
def retained(pointer,job):
    inputs.payload(Path(pointer['package']),Path(pointer['payloadManifest']),pointer['payloadManifestSha256'])
    if inputs.sha(Path(pointer['package'])/'Offline GPT.exe')!=pointer['exeSha256'] or inputs.sha(Path(pointer['verification']))!=pointer['verificationSha256'] or inputs.sha(Path(pointer['smokeReceipt']))!=pointer['smokeSha256']:raise Error('Retained candidate/evidence identity changed')
    verification=json.loads(Path(pointer['verification']).read_text(encoding='utf-8'))
    acceptance(json.loads(Path(pointer['smokeReceipt']).read_text(encoding='utf-8')),verification,job,pointer['exeSha256'])
    return verification

class Resources:
    def __init__(self):self.started=time.time();self.deadline=time.monotonic()+900;self.last=0;self.samples=[]
    def check(self,minimum=512*1024**2):
        if time.monotonic()>self.deadline:raise Error('Candidate build exceeded the overall fifteen-minute budget')
        class MEMORY(ctypes.Structure):_fields_=[('length',ctypes.c_ulong),('load',ctypes.c_ulong)]+[(n,ctypes.c_ulonglong) for n in ('total','available','pageTotal','pageAvailable','virtualTotal','virtualAvailable','extended')]
        state=MEMORY();state.length=ctypes.sizeof(state)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(state)):raise Error('Memory instrumentation unavailable')
        if state.available<minimum or state.pageAvailable<512*1024**2:raise (Deferred if minimum>512*1024**2 else Error)('Build stopped before memory headroom fell below its safety limit')
        if time.monotonic()-self.last>=2:
            self.last=time.monotonic()
            nvidia=Path(os.environ.get('SystemRoot',r'C:\Windows'))/'System32/nvidia-smi.exe'
            r=subprocess.run([str(nvidia),'--query-gpu=temperature.gpu,memory.used,power.draw','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=3,creationflags=0x8000000)
            if r.returncode:raise Error('GPU/driver monitoring unavailable; defer the build')
            values=r.stdout.strip().splitlines()[0].split(',');gpu=float(values[0]);self.samples.append({'time':time.time(),'availableRAM':state.available,'gpuC':gpu,'vramMiB':float(values[1]),'powerW':float(values[2])})
            if gpu>=82:raise Error('GPU reached the conservative 82 C stop limit')
    def preflight(self):
        self.check(2*1024**3)
        if self.samples[-1]['gpuC']>=70:raise Deferred('Defer build until GPU cool-start is below 70 C')
        script="$ErrorActionPreference='Stop';@(Get-Process -Name 'Offline GPT','llama-server','sd-cli','whisper-server' -ErrorAction SilentlyContinue).Count"
        r=subprocess.run([powershell(),'-NoProfile','-NonInteractive','-Command',script],capture_output=True,text=True,timeout=5,env=powershell_environment())
        if r.returncode:raise Error('Application-process preflight is unavailable')
        if int(r.stdout.strip())!=0:raise Deferred('User app/model is active; defer without closing it')
    def incidents(self):
        stamp=datetime.datetime.fromtimestamp(self.started,datetime.timezone.utc).isoformat()
        script="$ErrorActionPreference='Stop';try{$rows=@(Get-WinEvent -FilterHashtable @{LogName='System';StartTime=[datetime]'"+stamp+"';Level=1,2,3} -ErrorAction Stop)}catch{if($_.FullyQualifiedErrorId -like 'NoMatchingEventsFound*'){$rows=@()}else{throw}};@($rows|Where-Object {$_.ProviderName -match 'WHEA|nvlddmkm|^Display$'}).Count"
        r=subprocess.run([powershell(),'-NoProfile','-NonInteractive','-Command',script],capture_output=True,text=True,timeout=5,env=powershell_environment())
        if r.returncode or int(r.stdout.strip())!=0:raise Error('Hardware/driver incident or unavailable incident check')

class Builder:
    def __init__(self,config,*,fixture=False):
        self.config=inputs.ordinary(Path(config));self.cfg=json.loads(self.config.read_text())
        fields={'version','syncConfig','privateRoot','controllerCommit','environmentReceipt','environmentReceiptSha256','executableInputs','allowedPth','systemInputs','baselinePackage','baselineExeSha256'}
        if self.cfg.get('version')==2:fields.update({'historicalJobs','equivalentBaseline'})
        if set(self.cfg)!=fields or self.cfg['version'] not in {1,2}:raise Error('Invalid build configuration')
        if not isinstance(self.cfg.get('historicalJobs',{}),dict):raise Error('Invalid historical-job registry')
        for key,row in self.cfg.get('historicalJobs',{}).items():
            if not re.fullmatch('[a-f0-9]{64}',key) or set(row)!={'sha256','profileSha256','status'} or row['status'] not in TERMINAL-{'RECOVERY_REQUIRED'} or not all(re.fullmatch('[a-f0-9]{64}',row[n]) for n in ('sha256','profileSha256')):raise Error('Invalid immutable historical-job registry')
        proof=self.cfg.get('equivalentBaseline')
        if proof is not None and (not isinstance(proof,dict) or set(proof)!={'applicationInputSha256','environmentSha256','pointerSha256'} or not all(re.fullmatch('[a-f0-9]{64}',v) for v in proof.values())):raise Error('Invalid reviewed candidate equivalence binding')
        self.manager=auto.Manager(self.cfg['syncConfig'],local_fixture=fixture);self.fixture=fixture
        self.root=auto.sync.validate_external(inputs.ordinary(Path(self.cfg['privateRoot'])),self.manager.repo,self.manager.source)
        production=inputs.ordinary(Path(self.manager.cfg['layoutContract']['productionRoot'])) if self.manager.canonical_mode else inputs.ordinary(self.manager.source.parent/'Offline GPT')
        if self.root.is_relative_to(production) or production.is_relative_to(self.root):raise Error('Private candidate state must never overlap the production application')
        if not self.config.is_relative_to(self.root):raise Error('Build configuration must remain private')
        if not re.fullmatch('[0-9a-f]{40}',self.cfg['controllerCommit']):raise Error('Exact accepted build controller revision required')
        self.profileHash=digest_file(self.config);self.root.mkdir(parents=True,exist_ok=True)
        (self.root/'jobs').mkdir(exist_ok=True);self.lock=self.root/'builder.lock';self.pointer=self.root/'current-candidate.json'
        if not (self.root/'control.json').exists():save(self.root/'control.json',{'mode':'running'})
        self.environment=material.load(Path(self.cfg['environmentReceipt']),self.cfg['environmentReceiptSha256'])
        adapter_bytes=inputs.immutable_files(self.manager.repo,self.cfg['controllerCommit'],self.manager.cfg['git'],['scripts/'+name for name in sorted(SCRIPTS)])
        self.adapters={name:hashlib.sha256(adapter_bytes['scripts/'+name]).hexdigest() for name in sorted(SCRIPTS)}
        if not fixture:
            for name,h in self.adapters.items():
                if inputs.sha(Path(__file__).with_name(name))!=h:raise Error('Build controller differs from accepted frozen source: '+name)
        if not self.pointer.exists():
            baseline=inputs.ordinary(Path(self.cfg['baselinePackage']))
            if baseline==self.manager.source.parent/'Offline GPT' or inputs.sha(baseline/'Offline GPT.exe')!=self.cfg['baselineExeSha256']:raise Error('Invalid baseline candidate; production is not a build target')
            save(self.pointer,{'package':str(baseline),'exeSha256':self.cfg['baselineExeSha256'],'kind':'PREVIOUSLY_VERIFIED_BASELINE','buildKey':None})
    def record(self,path,status,**fields):
        p=path/'job.json';value=json.loads(p.read_text()) if p.exists() else {}
        value.update(status=status,updated=time.time(),**fields);save(path/'events'/(uuid.uuid4().hex+'.json'),value);save(p,value);return value
    def accepted(self,commit):
        self.manager.guard(clean=False)
        if self.manager.remote_head()!=self.manager.head():raise Error('Remote/local development branch drift')
        self.manager.git('merge-base','--is-ancestor',commit,self.manager.head())
        policy=self.manager.remote_policy();ci=self.manager.ci(commit)
        if ci.get('status')!='completed' or ci.get('conclusion')!='success' or (ci.get('fixtureOnly') and not self.fixture):raise Error('Exact accepted remote source CI required')
        return {'ci':ci,'repositoryPolicy':policy}
    def job(self,key):
        if not re.fullmatch('[0-9a-f]{64}',key):raise Error('Invalid build job ID')
        path=inputs.ordinary(self.root/'jobs'/key)
        if not path.is_dir():raise Error('Unknown build job')
        journal=inputs.ordinary(path/'job.json')
        if not journal.is_file() or journal.stat().st_size>8*1024**2:raise Error('Missing or oversized job journal; preserve for review')
        value=json.loads(journal.read_text(encoding='utf-8'))
        if value['buildKey']!=key:raise Error('Build job identity changed')
        if value['profileSha256']!=self.profileHash:
            history=self.cfg.get('historicalJobs',{}).get(key)
            if not history or history['sha256']!=inputs.sha(journal) or value['profileSha256']!=history['profileSha256'] or value['status']!=history['status']:raise Error('Build job/config identity changed')
            genuine_completion(value,key,fixture=self.fixture)
            return path,value
        if value['status'] not in ACTIVE|TERMINAL|{'QUEUED','WAITING_RESOURCE','WAITING_NETWORK'} or type(value['attempts']) is not int or not 0<=value['attempts']<=2 or type(value['created']) not in (int,float) or not math.isfinite(value['created']) or value['created']<=0:raise Error('Invalid job lifecycle fields')
        if not re.fullmatch('[a-f0-9]{40}',value['sourceCommit']) or not isinstance(value['graph'],dict) or value['graph'].get('sourceCommit')!=value['sourceCommit'] or not isinstance(value['graph'].get('files'),dict) or not value['graph']['files'] or value['adapters']!=self.adapters or not isinstance(value['acceptedSources'],list) or not value['acceptedSources']:raise Error('Incomplete immutable job identity')
        genuine_completion(value,key,fixture=self.fixture)
        return path,value
    def anomaly(self,path,error):
        identity={'name':path.name,'reason':str(error)}
        try:
            journal=inputs.ordinary(path/'job.json')
            if journal.is_file() and journal.stat().st_size<=8*1024**2:identity['originalSha256']=inputs.sha(journal)
        except (Error,OSError):pass
        receipt=self.root/'queue-anomalies'/(inputs.digest(identity)+'.json')
        if not receipt.exists():save(receipt,{**identity,'status':'RECOVERY_REQUIRED','observed':time.time(),'originalPreserved':True})
    def enqueue(self,commit,required=True):
        with auto.exclusive(self.lock):return self._enqueue(commit,required)
    def checked_reuse(self,old,graph,commit):
        """Terminal status is history, never evidence that current bytes match."""
        try:
            pointer_path=inputs.ordinary(self.pointer)
            if not pointer_path.is_file() or pointer_path.stat().st_size>8*1024**2:raise Error('Missing or oversized candidate pointer')
            pointer=json.loads(pointer_path.read_text(encoding='utf-8'))
            if not isinstance(pointer,dict):raise Error('Malformed candidate pointer')
            if old['status']=='COMPLETE':
                if pointer.get('buildKey')!=old['buildKey'] or pointer!=old.get('pointerAfter'):raise Error('Current candidate differs from the completed build; retain both identities for review')
                original=old
            else:
                association=old.get('association',{})
                if not isinstance(association,dict):raise Error('Malformed association evidence')
                if pointer.get('buildKey')!=association.get('buildKey') or pointer.get('exeSha256')!=association.get('candidateSha256'):raise Error('Associated candidate pointer changed; recovery review required')
                _,original=self.job(pointer['buildKey'])
            if original['status']!='COMPLETE' or not self.fixture and original.get('actualBuildRun') is not True:raise Error('Reuse requires retained genuine build evidence')
            verification=retained(pointer,original)
            if verification.get('files')!=graph['files']:raise Error('Current candidate source inputs differ from requested accepted source')
            return {'requestedSourceCommit':commit,'artifactBuildKey':pointer['buildKey'],'artifactSourceCommit':original['sourceCommit'],'candidateSha256':pointer['exeSha256'],
                    'payloadManifestSha256':pointer['payloadManifestSha256'],'verificationSha256':pointer['verificationSha256'],'smokeSha256':pointer['smokeSha256'],
                    'observed':time.time(),'scope':'Fresh static payload and source verification; retained prior smoke, no new build or native inference'}
        except (Error,OSError,ValueError,KeyError,TypeError,AttributeError) as error:
            save(self.root/'reuse-refusals'/(uuid.uuid4().hex+'.json'),{'status':'REUSE_REFUSED','requestedSourceCommit':commit,'historicalBuildKey':old['buildKey'],'reason':str(error),'observed':time.time(),'historicalJournalPreserved':True})
            raise ReuseRefused('Retained candidate reuse refused; inspect private reuse-refusals before recovery') from None
    def _enqueue(self,commit,required):
        evidence=self.accepted(commit);graph=inputs.inputs(self.manager.repo,commit,self.manager.cfg['git'])
        for name,h in self.cfg['executableInputs'].items():
            if graph['files'].get(name)!=h:raise Error('Executable build input requires renewed review: '+name)
        executable_names={name for name in graph['files'] if name=='Offline GPT.spec' or ('/' not in name and name.endswith('.txt')) or (name.startswith('frontend-source/') and not name.startswith(('frontend-source/src/','frontend-source/public/')))}
        if executable_names!=set(self.cfg['executableInputs']):raise Error('Unexpected/missing executable packaging input')
        key=inputs.digest({'application':graph['applicationInputSha256'],'environment':self.environment['environmentSha256'],'adapters':self.adapters,'profile':self.profileHash})
        path=self.root/'jobs'/key
        if path.exists():
            _,old=self.job(key)
            reuse=self.checked_reuse(old,graph,commit) if old['status'] in {'COMPLETE','ASSOCIATED'} else None
            sources=old['acceptedSources']
            if not any(v['commit']==commit for v in sources):sources.append({'commit':commit,**evidence})
            extra={'lastReuseVerification':reuse} if reuse else {}
            return self.record(path,old['status'],acceptedSources=sources,required=old['required'] or required,**extra)
        path.mkdir()
        result=self.record(path,'QUEUED',buildKey=key,sourceCommit=commit,graph=graph,profileSha256=self.profileHash,adapters=self.adapters,acceptedSources=[{'commit':commit,**evidence}],required=required,attempts=0,created=time.time())
        proof=self.cfg.get('equivalentBaseline')
        if proof and graph['applicationInputSha256']==proof['applicationInputSha256'] and self.environment['environmentSha256']==proof['environmentSha256'] and inputs.sha(self.pointer)==proof['pointerSha256']:
            try:
                pointer=json.loads(self.pointer.read_text(encoding='utf-8'));_,original=self.job(pointer['buildKey'])
                if original['status']!='COMPLETE' or (not self.fixture and original.get('actualBuildRun') is not True):raise Error('A genuine retained build is required for source association')
                verification=retained(pointer,original)
                if verification['files']!=graph['files']:raise Error('Application inputs differ from retained compiled source')
                result=self.record(path,'ASSOCIATED',actualBuildRun=False,association={'builtSourceCommit':pointer['sourceCommit'],'buildKey':pointer['buildKey'],'candidateSha256':pointer['exeSha256'],'scope':'Existing genuine non-native artifact, identical application inputs; no rebuild or inference'})
            except (Error,OSError,ValueError,KeyError,TypeError) as error:
                self.record(path,'FAILED',reason='Retained artifact association refused: '+str(error));raise
        # Only explicitly optional pending revisions may be superseded.
        for previous in (self.root/'jobs').iterdir():
            if previous==path or not (previous/'job.json').exists():continue
            try:_,old=self.job(previous.name)
            except (Error,OSError,ValueError,KeyError,TypeError) as error:self.anomaly(previous,error);continue
            if old['status']=='QUEUED' and not old['required']:self.record(previous,'SUPERSEDED',supersededBy=key)
        return result
    def discover(self):
        with auto.exclusive(self.root/'discovery.lock'):
            results=[];receipt=self.root/'source-discovery.json';seen=json.loads(receipt.read_text()) if receipt.exists() else {}
            for path in (self.manager.state/'batches').iterdir():
                previous=seen.get(path.name,{})
                if previous.get('status')=='ATTENTION':results.append(previous);continue
                try:
                    journal=inputs.ordinary(path/'batch.json')
                    if not journal.is_file() or journal.stat().st_size>8*1024**2:raise Error('Missing/oversized source batch journal')
                    value=json.loads(journal.read_text(encoding='utf-8'))
                    if not isinstance(value,dict) or not isinstance(value.get('status'),str):raise Error('Malformed source batch lifecycle')
                except (Error,OSError,ValueError,KeyError,TypeError) as error:
                    seen[path.name]={'status':'ATTENTION','sourceBatch':path.name,'reason':str(error),'originalPreserved':True};save(receipt,seen);results.append(seen[path.name]);continue
                if value.get('status')!='COMPLETE' or value.get('candidateBuild')!='QUEUED_FOR_SEPARATE_VERIFIED_BUILD_CONTROLLER' or previous.get('status') in {'QUEUED','ATTENTION'}:continue
                if previous.get('retryAfter',0)>time.time():continue
                try:
                    job=self.enqueue(value['commit'],True);results.append(job);seen[path.name]={'status':'QUEUED','commit':value['commit'],'buildKey':job['buildKey']}
                except ReuseRefused as error:
                    # Integrity/pointer ambiguity is not an offline retry.
                    # Make it immediately visible to the frozen supervisor;
                    # preserve historical success and continue healthy siblings.
                    seen[path.name]={'status':'ATTENTION','sourceBatch':path.name,'reason':str(error),'observed':time.time()}
                    results.append(seen[path.name])
                except (auto.Error,Error) as error:
                    count=previous.get('attempts',0)+1
                    seen[path.name]={'status':'ATTENTION' if count>=8 else 'DISCOVERY_BLOCKED','sourceBatch':path.name,'reason':str(error),'attempts':count,'retryAfter':time.time()+min(1800,30*2**count)}
                    results.append(seen[path.name])
                save(receipt,seen)
            return results
    def retry_discovery(self,identifier):
        if not isinstance(identifier,str) or not re.fullmatch('[a-f0-9]{32}',identifier):raise Error('Exact source batch ID required')
        with auto.exclusive(inputs.ordinary(self.root/'discovery.lock')):
            receipt=inputs.ordinary(self.root/'source-discovery.json')
            if not receipt.is_file() or receipt.stat().st_size>8*1024**2:raise Error('Missing or oversized discovery journal')
            seen=json.loads(receipt.read_text())
            if not isinstance(seen,dict):raise Error('Invalid discovery journal')
            before=seen.get(identifier)
            if not isinstance(before,dict) or before.get('status')!='ATTENTION':raise Error('Only an explicit attention record can be reviewed for retry')
            journal=inputs.ordinary(self.manager.state/'batches'/identifier/'batch.json')
            if not journal.is_file() or journal.stat().st_size>8*1024**2:raise Error('Missing or oversized source batch journal')
            value=json.loads(journal.read_text())
            if not isinstance(value,dict) or value.get('status')!='COMPLETE' or value.get('candidateBuild')!='QUEUED_FOR_SEPARATE_VERIFIED_BUILD_CONTROLLER' or not re.fullmatch('[a-f0-9]{40}',str(value.get('commit',''))):raise Error('Source batch is not complete and eligible')
            # Keep attention untouched unless exact CI and any retained
            # artifact pass again. No manual success flag or file deletion.
            job=self.enqueue(value['commit'],True)
            after={'status':'QUEUED','commit':value['commit'],'buildKey':job['buildKey']}
            recovery=self.root/'discovery-recovery'/(uuid.uuid4().hex+'.json')
            record={'status':'VERIFIED','sourceBatch':identifier,'sourceJournalSha256':inputs.sha(journal),'before':before,'after':after,'observed':time.time()};save(recovery,record)
            seen[identifier]=after;save(receipt,seen);save(recovery,{**record,'status':'APPLIED'})
            return {'status':'DISCOVERY_RETRY_VERIFIED','sourceBatch':identifier,'buildKey':job['buildKey'],'recoveryReceipt':str(recovery)}
    def source_snapshot(self,job,attempt,monitor=lambda:None):
        source=attempt/'source';source.mkdir()
        monitor();source_bytes=inputs.immutable_files(self.manager.repo,job['sourceCommit'],self.manager.cfg['git'],list(job['graph']['files']))
        for name,h in job['graph']['files'].items():
            monitor()
            target=source/inputs.relative(name);target.parent.mkdir(parents=True,exist_ok=True)
            data=source_bytes[name];target.write_bytes(data)
            normalized=inputs.normalized_bytes(name,data)
            if hashlib.sha256(normalized).hexdigest()!=h:raise Error('Immutable source snapshot differs')
        controller=attempt/'controller';controller.mkdir()
        monitor();adapter_bytes=inputs.immutable_files(self.manager.repo,self.cfg['controllerCommit'],self.manager.cfg['git'],['scripts/'+name for name in self.adapters])
        for name,h in self.adapters.items():
            data=adapter_bytes['scripts/'+name]
            if hashlib.sha256(data).hexdigest()!=h:raise Error('Accepted adapter source changed')
            (controller/name).write_bytes(data)
        return source,controller
    def child_environment(self,attempt,deps):
        home=attempt/'home';home.mkdir();temp=attempt/'temp';temp.mkdir()
        windows=os.environ.get('SystemRoot',r'C:\Windows')
        return {'SystemRoot':windows,'WINDIR':windows,'PATH':str(Path(deps['python']).parent)+os.pathsep+str(Path(windows)/'System32'),
          'TEMP':str(temp),'TMP':str(temp),'USERPROFILE':str(home),'APPDATA':str(home/'AppData'),'LOCALAPPDATA':str(home/'Local'),
          'PYTHONDONTWRITEBYTECODE':'1','PYTHONNOUSERSITE':'1','PYTHONUTF8':'1','PIP_NO_INDEX':'1','NPM_CONFIG_OFFLINE':'true','SETUPTOOLS_USE_DISTUTILS':'local'}
    def collected(self,source,attempt,package):
        toc=ast.literal_eval((source/'build/Offline GPT/COLLECT-00.toc').read_text())
        if not isinstance(toc,tuple) or len(toc)!=1 or not isinstance(toc[0],list):raise Error('Unexpected COLLECT format')
        hashes={};origins={};seen=set()
        for name,origin,kind in toc[0]:
            name=inputs.relative(name);original=inputs.ordinary(Path(origin));target=inputs.ordinary(package/name)
            if name.casefold() in seen or not target.is_relative_to(package):raise Error('Colliding/escaping collected payload')
            seen.add(name.casefold())
            if not original.is_relative_to(attempt):
                if self.cfg['systemInputs'].get(str(original))!=inputs.sha(original):raise Error('Unapproved external native dependency: '+original.name)
            if Path(name).suffix.lower() in {'.gguf','.safetensors'}:raise Error('Model weight entered packaged output')
            h=inputs.sha(original)
            if not target.is_file() or inputs.sha(target)!=h:raise Error('Collected package differs from its actual source')
            hashes[name]=h;origins[name]={'origin':str(original),'kind':kind,'sha256':h}
        actual={p.relative_to(package).as_posix() for p in package.rglob('*') if p.is_file()}
        if actual!=set(hashes):raise Error('Missing or stale files in complete onedir output')
        manifest=attempt/'payload.json';save(manifest,hashes);save(attempt/'origins.json',origins)
        return manifest
    def execute(self,path,job,*,promote=True):
        # An explicit bootstrap may validate an artifact without selecting it.
        # Both modes use the same input, resource, process and acceptance gates.
        if type(promote) is not bool:raise Error('Explicit boolean promotion mode required')
        if not promote and json.loads((self.root/'control.json').read_text())['mode']!='pause':raise Error('Staged builds require the existing candidate queue to remain paused')
        pointer_before=self.pointer.read_bytes()
        old=json.loads(pointer_before)
        if digest_file(self.config)!=job['profileSha256']:raise Error('Build configuration changed')
        actual=inputs.inputs(self.manager.repo,job['sourceCommit'],self.manager.cfg['git'])
        expected=inputs.digest({'application':actual['applicationInputSha256'],'environment':self.environment['environmentSha256'],'adapters':self.adapters,'profile':self.profileHash})
        if actual!=job['graph'] or expected!=job['buildKey']:raise Error('Queued immutable input graph changed')
        if shutil.disk_usage(self.root).free<8*1024**3:raise Deferred('Defer candidate build until at least 8 GiB of disk space is free')
        self.accepted(job['sourceCommit']);resources=Resources();resources.preflight();resources.incidents()
        self.record(path,'PREPARING',attempts=job['attempts']+1,started=time.time())
        # CreateProcessW/Node still reject some >MAX_PATH executable names
        # even when filesystem long paths are enabled. Journals retain the
        # exact attempt path; do not nest physical work under the 64-byte key.
        attempt=inputs.ordinary(self.root/'w'/uuid.uuid4().hex);attempt.mkdir(parents=True)
        self.record(path,'MATERIALIZING',attempt=str(attempt),attempts=job['attempts']+1,started=time.time())
        def monitor():
            if (path/'cancel.json').exists():raise Error('Build cancellation requested')
            count=len(resources.samples)
            try:resources.check()
            finally:
                if len(resources.samples)!=count:save(attempt/'resources.json',resources.samples)
        source,controller=self.source_snapshot(job,attempt,monitor)
        deps=material.materialize(Path(self.cfg['environmentReceipt']),self.cfg['environmentReceiptSha256'],attempt/'dependencies',monitor,source/'frontend-source/node_modules',self.cfg['allowedPth'])
        save(attempt/'materialization.json',deps);env=self.child_environment(attempt,deps);phase_receipts={}
        def phase(name,command,seconds):
            self.record(path,name,attempt=str(attempt));monitor();resources.incidents()
            r=process.run(command,source,env,attempt/(name.lower()+'.log'),seconds,monitor);phase_receipts[name]=r;save(attempt/'process-receipts.json',phase_receipts)
            if r['returnCode']!=0:raise Error(name+' returned an error; inspect private phase log')
        phase('FRONTEND',[deps['node'],source/'frontend-source/node_modules/vite/bin/vite.js','build','--config',source/'frontend-source/vite.config.js'],120)
        if not (source/'ui/index.html').is_file():raise Error('Frontend output missing')
        base=[deps['python'],'-I','-S','-B',controller/'build_bootstrap.py']
        phase('PACKAGING',base+['package',deps['site'],source,controller],300)
        package=source/'dist/Offline GPT';manifest=self.collected(source,attempt,package)
        verification=attempt/'verification.json'
        phase('VERIFYING',base+['verify',deps['site'],source,controller,self.manager.repo,self.manager.cfg['git'],job['sourceCommit'],package,manifest,inputs.sha(manifest),verification],90)
        profile=attempt/'smoke-profile';exe_hash=inputs.sha(package/'Offline GPT.exe')
        phase('SMOKE',base+['smoke',deps['site'],source,controller,package,profile,exe_hash],50)
        smoke=json.loads((profile/'shell-smoke.json').read_text())
        verified=json.loads(verification.read_text())
        self.reject_fixture_evidence(smoke,verified,phase_receipts)
        acceptance(smoke,verified,job,exe_hash)
        self.accepted(job['sourceCommit']);monitor();resources.incidents();save(attempt/'resources.json',resources.samples)
        # Smoke writes its own profile only. Recheck all immutable payload bytes.
        inputs.payload(package,manifest,inputs.sha(manifest))
        pointer={'package':str(package),'exeSha256':exe_hash,'sourceCommit':job['sourceCommit'],'buildKey':job['buildKey'],'payloadManifest':str(manifest),'payloadManifestSha256':inputs.sha(manifest),
          'verification':str(verification),'verificationSha256':inputs.sha(verification),'smokeReceipt':str(profile/'shell-smoke.json'),'smokeSha256':inputs.sha(profile/'shell-smoke.json'),'kind':'VALIDATED_NON_NATIVE_CANDIDATE','binaryPublication':'BLOCKED_NATIVE_PROVENANCE_AND_LICENSES'}
        if self.pointer.read_bytes()!=pointer_before:raise Error('Candidate pointer changed during execution; retain the artifact without promotion')
        if not promote and json.loads((self.root/'control.json').read_text())['mode']!='pause':raise Error('Candidate queue was resumed during staged execution; adoption requires review')
        self.record(path,'PROMOTING' if promote else 'STAGING',pointerBefore=old,pointerAfter=pointer,phaseReceipts=phase_receipts,promotionDeferred=not promote)
        if promote:save(self.pointer,pointer)
        return self.record(path,'COMPLETE',accepted=time.time(),pointerAfter=pointer,actualBuildRun=not self.fixture,fixtureOnly=self.fixture)
    def reject_fixture_evidence(self,smoke,verification,phases):
        if not self.fixture and any(row.get('fixtureOnly') is True for row in (smoke,verification,*phases.values())):raise Error('Synthetic evidence cannot establish genuine packaged acceptance')
    def run_job(self,path,job,*,staged=False):
        """Shared bounded failure handling; a staged failure never promotes."""
        try:return self.execute(path,job,promote=False) if staged else self.execute(path,job)
        except Deferred as error:return self.record(path,'WAITING_RESOURCE',reason=str(error),retryAfter=time.time()+300)
        except auto.NetworkError as error:
            current=json.loads((path/'job.json').read_text());count=current.get('networkRetries',0)+1
            if current['status'] not in {'QUEUED','WAITING_RESOURCE','WAITING_NETWORK'}:return self.record(path,'FAILED',reason=str(error))
            return self.record(path,'WAITING_NETWORK' if count<8 else 'BLOCKED',reason=str(error),networkRetries=count,retryAfter=time.time()+min(1800,30*2**count))
        except (Error,auto.Error,process.ProcessError,material.Error,OSError,ValueError,subprocess.SubprocessError) as error:
            current=json.loads((path/'job.json').read_text());status='RECOVERY_REQUIRED' if current['status'] in {'PROMOTING','STAGING'} else 'CANCELLED' if (path/'cancel.json').exists() else 'FAILED'
            return self.record(path,status,reason=str(error))
    def stage(self,key):
        """Build one exact job while paused, preserving the selected candidate.

        This creates acceptance evidence only. Worker/profile adoption remains a
        separate journaled operation; no task, discovery or old job is rewritten.
        """
        with auto.exclusive(self.lock):
            if json.loads((self.root/'control.json').read_text())['mode']!='pause':raise Error('Staged builds require the existing candidate queue to remain paused')
            path,job=self.job(key)
            if job['profileSha256']!=self.profileHash or job['status'] not in {'QUEUED','WAITING_RESOURCE','WAITING_NETWORK'} or job['attempts']>=2 or job.get('retryAfter',0)>time.time() or (path/'cancel.json').exists():raise Error('Staging requires an eligible current-profile job with remaining bounded attempts')
            return self.run_job(path,job,staged=True)
    def once(self):
        with auto.exclusive(self.lock):
            if json.loads((self.root/'control.json').read_text())['mode']!='running':return {'status':'PAUSED'}
            jobs=[];anomalies=0
            for path in (self.root/'jobs').iterdir():
                try:_,value=self.job(path.name)
                except (Error,OSError,ValueError,KeyError,TypeError) as error:
                    self.anomaly(path,error);anomalies+=1;continue
                if value['status'] in ACTIVE:return self.record(path,'RECOVERY_REQUIRED',reason='Interrupted build phase; retain outputs and explicitly recover/retry')
                if value['status']=='QUEUED' or (value['status'] in {'WAITING_RESOURCE','WAITING_NETWORK'} and value.get('retryAfter',0)<=time.time()):jobs.append((value['created'],path,value))
            if not jobs:return {'status':'ATTENTION' if anomalies else 'IDLE','queueAnomalyCount':anomalies}
            _,path,job=min(jobs,key=lambda row:row[0])
            return {**self.run_job(path,job),'queueAnomalyCount':anomalies}
    def cancel(self,key):
        path,job=self.job(key)
        if job['status'] in TERMINAL or job['profileSha256']!=self.profileHash:raise Error('Terminal/historical work cannot be cancelled')
        save(path/'cancel.json',{'requested':time.time()})
        if job['status'] in {'QUEUED','WAITING_RESOURCE','WAITING_NETWORK'}:return self.record(path,'CANCELLED',reason='Cancelled before execution')
        return {'status':'CANCELLATION_REQUESTED','buildKey':key}
    def retry(self,key):
        with auto.exclusive(self.lock):
            path,job=self.job(key)
            if job['profileSha256']!=self.profileHash:raise Error('Historical jobs are immutable; review and enqueue their accepted source under the current profile')
            if job['status'] not in {'FAILED','CANCELLED','RECOVERY_REQUIRED'} or job['attempts']>=2 or job.get('pointerAfter'):raise Error('Retry requires a recoverable failure, retained pointer and remaining budget')
            if (path/'cancel.json').exists():(path/'cancel.json').rename(path/('cancel-retained-'+uuid.uuid4().hex+'.json'))
            return self.record(path,'QUEUED',retryReviewed=time.time())
    def recover(self,key):
        with auto.exclusive(self.lock):
            path,job=self.job(key);pointer=json.loads(self.pointer.read_text())
            if job['profileSha256']!=self.profileHash:raise Error('Historical jobs cannot be recovered under a different execution profile')
            if job['status']!='RECOVERY_REQUIRED' or 'pointerAfter' not in job:raise Error('No interrupted promotion to recover')
            if job.get('promotionDeferred') is True:
                if json.loads((self.root/'control.json').read_text())['mode']!='pause' or pointer!=job['pointerBefore']:raise Error('Staged recovery requires the original candidate and paused queue')
                if set(job.get('phaseReceipts',{}))!={'FRONTEND','PACKAGING','VERIFYING','SMOKE'} or any(row.get('returnCode')!=0 or row.get('remainingOwnedProcesses')!=0 for row in job['phaseReceipts'].values()):raise Error('Staged recovery lacks complete owned-process evidence')
                verification=retained(job['pointerAfter'],job)
                self.reject_fixture_evidence(json.loads(Path(job['pointerAfter']['smokeReceipt']).read_text()),verification,job['phaseReceipts'])
                if verification.get('files')!=job['graph']['files'] or inputs.inputs(self.manager.repo,job['sourceCommit'],self.manager.cfg['git'])!=job['graph']:raise Error('Staged recovery source inputs changed')
                self.accepted(job['sourceCommit'])
                return self.record(path,'COMPLETE',recovered=time.time(),actualBuildRun=not self.fixture,fixtureOnly=self.fixture)
            if pointer==job['pointerBefore']:return self.record(path,'FAILED',retainedPromotion=job['pointerAfter'],pointerAfter=None,reason='Promotion did not advance; original candidate retained')
            if pointer!=job['pointerAfter']:raise Error('Unknown candidate pointer; manual reconciliation required')
            inputs.payload(Path(pointer['package']),Path(pointer['payloadManifest']),pointer['payloadManifestSha256'])
            if inputs.sha(Path(pointer['verification']))!=pointer['verificationSha256'] or inputs.sha(Path(pointer['smokeReceipt']))!=pointer['smokeSha256']:raise Error('Retained acceptance evidence changed')
            smoke=json.loads(Path(pointer['smokeReceipt']).read_text());verification=json.loads(Path(pointer['verification']).read_text())
            if inputs.sha(Path(pointer['package'])/'Offline GPT.exe')!=pointer['exeSha256']:raise Error('Retained EXE identity changed')
            acceptance(smoke,verification,job,pointer['exeSha256'])
            self.accepted(job['sourceCommit'])
            return self.record(path,'COMPLETE',recovered=time.time(),actualBuildRun=not self.fixture,fixtureOnly=self.fixture)
    def status(self):
        jobs=[];issues=[]
        for path in (self.root/'jobs').iterdir():
            try:jobs.append(self.job(path.name)[1])
            except (Error,OSError,ValueError,KeyError,TypeError) as error:issues.append({'name':path.name,'status':'RECOVERY_REQUIRED','reason':str(error)})
        discovery=self.root/'source-discovery.json'
        return {'control':json.loads((self.root/'control.json').read_text()),'candidate':json.loads(self.pointer.read_text()),'jobs':jobs,'queueIssues':issues,
                'sourceDiscovery':json.loads(discovery.read_text()) if discovery.exists() else {},'integrityNote':'Historical job status is not a fresh payload health check'}

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--config',type=Path,required=True);parser.add_argument('command',choices=['enqueue','discover','run-once','stage','status','cancel','retry','recover','retry-discovery','start','pause','stop']);parser.add_argument('--commit');parser.add_argument('--job');parser.add_argument('--batch');parser.add_argument('--optional',action='store_true');args=parser.parse_args()
    builder=Builder(args.config)
    if args.command=='enqueue':result=builder.enqueue(args.commit,not args.optional)
    elif args.command=='discover':result=builder.discover()
    elif args.command=='run-once':builder.discover();result=builder.once()
    elif args.command in {'cancel','retry','recover','stage'}:result=getattr(builder,args.command)(args.job)
    elif args.command=='retry-discovery':result=builder.retry_discovery(args.batch)
    elif args.command=='status':result=builder.status()
    else:save(builder.root/'control.json',{'mode':'running' if args.command=='start' else args.command});result=builder.status()
    print(json.dumps(result,indent=2))
if __name__=='__main__':
    try:main()
    except (Error,auto.Error,OSError,ValueError,KeyError) as error:raise SystemExit('Candidate builder stopped safely: '+str(error))
