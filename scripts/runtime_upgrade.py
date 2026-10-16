"""Journaled upgrades of the source-sync task, never the application installer.

Only exact private-remote CI-accepted Git blobs may become a new frozen runtime.
An interrupted upgrade stays disabled until its retained journal is reviewed.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import time
import uuid
import xml.etree.ElementTree as ET

def sibling(name):
    spec=importlib.util.spec_from_file_location('upgrade_'+name,Path(__file__).with_name(name+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
runner=sibling('sync_runner');auto=sibling('auto_sync')
Error=runner.RunnerError
TASK='OfflineGPT-VerifiedSourceSync'
DONE={'COMPLETE','ROLLED_BACK','ABANDONED'}
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def ps_quote(value):return "'"+str(value).replace("'","''")+"'"

class WindowsTask:
    """Fixed user task operations only; no arbitrary command or task selector."""
    def call(self,body):
        script="$ErrorActionPreference='Stop';$name='"+TASK+"';"+body
        result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],capture_output=True,timeout=25,encoding='utf-8')
        if result.returncode:raise Error('Scheduled source-task operation failed; inspect retained journal')
        return result.stdout
    def read(self):
        return json.loads(self.call("$t=Get-ScheduledTask -TaskName $name;if(@($t.Actions).Count -ne 1){throw 'Expected one action'};$a=$t.Actions[0];@{xml=(Export-ScheduledTask -TaskName $name);action=@{execute=$a.Execute;arguments=$a.Arguments;workingDirectory=$a.WorkingDirectory};enabled=[bool]$t.Settings.Enabled;running=($t.State -eq 'Running')}|ConvertTo-Json -Depth 6 -Compress"))
    def enable(self,value):self.call(('Enable' if value else 'Disable')+'-ScheduledTask -TaskName $name|Out-Null')
    def action(self,value):
        self.call('$a=New-ScheduledTaskAction -Execute '+ps_quote(value['execute'])+' -Argument '+ps_quote(value['arguments'])+' -WorkingDirectory '+ps_quote(value['workingDirectory'])+';Set-ScheduledTask -TaskName $name -Action $a|Out-Null')
    def start(self):self.call('Start-ScheduledTask -TaskName $name')
    def result(self):return json.loads(self.call('$i=Get-ScheduledTaskInfo -TaskName $name;@{result=$i.LastTaskResult;lastRun=([DateTimeOffset]$i.LastRunTime.ToUniversalTime()).ToUnixTimeMilliseconds()/1000;running=((Get-ScheduledTask -TaskName $name).State -eq "Running")}|ConvertTo-Json -Compress'))

def definition(xml):
    """Every setting except the intentionally changed action/enabled flag."""
    root=ET.fromstring(xml)
    for child in list(root):
        tag=child.tag.rsplit('}',1)[-1]
        if tag=='Actions':root.remove(child)
        elif tag=='Settings':
            for setting in list(child):
                if setting.tag.rsplit('}',1)[-1]=='Enabled':child.remove(setting)
    return hashlib.sha256(ET.tostring(root)).hexdigest()

class Upgrade:
    def __init__(self,config,task=None,*,local_fixture=False):
        self.manager=auto.Manager(config,local_fixture=local_fixture)
        self.private=self.manager.state;self.pointer=self.private/'active-runtime.json'
        self.task=task or WindowsTask();self.directory=self.private/'upgrades'
        runner.ordinary(self.directory);self.directory.mkdir(exist_ok=True)
    def action_for(self,manifest):
        pythonw=runner.ordinary(Path(manifest['pythonPath']).with_name('pythonw.exe'))
        if not pythonw.is_file():raise Error('Approved windowless interpreter is missing')
        supervisor=Path(manifest['runtimeRoot'])/'scripts/sync_runner.py'
        return {'execute':str(pythonw),'arguments':'-I "'+str(supervisor)+'" --manifest "'+str(self.pointer)+'"','workingDirectory':str(self.private)}
    def accepted(self,commit):
        self.manager.guard()
        if self.manager.head()!=commit or self.manager.remote_head()!=commit:raise Error('Accepted local/remote commit changed')
        policy=self.manager.remote_policy();ci=self.manager.ci(commit)
        if ci.get('status')!='completed' or ci.get('conclusion')!='success':raise Error('Exact remote CI has not passed')
        if ci.get('fixtureOnly') and not self.manager.local_fixture:raise Error('Fixture CI cannot authorize a real runtime')
        return {'policy':policy,'ci':ci}
    def save(self,path,status,**fields):
        value=json.loads((path/'journal.json').read_text()) if (path/'journal.json').exists() else {}
        value.update(status=status,updated=time.time(),**fields)
        events=path/'events';events.mkdir(exist_ok=True)
        runner.atomic(events/(uuid.uuid4().hex+'.json'),value);runner.atomic(path/'journal.json',value)
        return value
    def pending(self):
        result=[]
        for p in self.directory.iterdir():
            runner.ordinary(p);runner.ordinary(p/'journal.json')
            if (p/'journal.json').is_file() and json.loads((p/'journal.json').read_text())['status'] not in DONE:result.append(p)
        return result
    def prepare(self):
        with auto.exclusive(self.manager.lock):
            if self.pending():raise Error('Resolve the existing upgrade journal before preparing another')
            old,_=runner.validate(self.pointer);commit=self.manager.head()
            if commit==old['commit']:raise Error('Accepted runtime already uses this commit')
            evidence=self.accepted(commit);snapshot=self.task.read()
            if snapshot['running'] or snapshot['action']!=self.action_for(old):raise Error('Task is running or differs from the accepted old action')
            runtime=self.private/'runtime'/commit;runner.ordinary(runtime)
            scripts={}
            for name in sorted(runner.SCRIPTS):
                content=self.manager.git('show',commit+':scripts/'+name)
                target=runtime/'scripts'/name;runner.ordinary(target);target.parent.mkdir(parents=True,exist_ok=True)
                if target.exists():
                    if target.read_bytes()!=content:raise Error('Existing frozen runtime differs; never overwrite it')
                else:
                    with target.open('xb') as stream:stream.write(content);stream.flush();os.fsync(stream.fileno())
                scripts[name]=runner.sha(target)
            new={**old,'commit':commit,'runtimeRoot':str(runtime),'scripts':scripts}
            path=self.directory/uuid.uuid4().hex;path.mkdir()
            (path/'old-runtime.json').write_bytes(self.pointer.read_bytes())
            runner.atomic(path/'new-runtime.json',new);runner.validate(path/'new-runtime.json')
            # Export-ScheduledTask already uses CRLF on Windows. Text-mode
            # translation would turn it into CRCRLF and alter the XML tree.
            (path/'task-before.xml').write_bytes(snapshot['xml'].encode('utf-8'))
            return self.save(path,'PREPARED',id=path.name,oldSha256=runner.sha(path/'old-runtime.json'),newSha256=runner.sha(path/'new-runtime.json'),
                oldAction=self.action_for(old),newAction=self.action_for(new),taskDefinition=definition(snapshot['xml']),
                wasEnabled=snapshot['enabled'],pythonwSha256=runner.sha(Path(self.action_for(new)['execute'])),accepted=evidence,commit=commit)
    def load(self,identifier,*,check_backup=True):
        if not re.fullmatch('[0-9a-f]{32}',identifier):raise Error('Invalid upgrade ID')
        path=runner.ordinary(self.directory/identifier)
        value=json.loads((runner.ordinary(path/'journal.json')).read_text())
        if value['id']!=identifier:raise Error('Upgrade journal identity mismatch')
        old,_=runner.validate(path/'old-runtime.json');new,_=runner.validate(path/'new-runtime.json')
        for label in ('old','new'):
            if runner.sha(path/(label+'-runtime.json'))!=value[label+'Sha256']:raise Error('Retained pointer evidence changed')
        if old['commit']==new['commit'] or value['commit']!=new['commit']:raise Error('Invalid upgrade revision')
        if any(old[k]!=new[k] for k in set(old)-{'commit','runtimeRoot','scripts'}):raise Error('Upgrade may not change approved roots/config/interpreter')
        if value['oldAction']!=self.action_for(old) or value['newAction']!=self.action_for(new):raise Error('Journal task actions differ from validated manifests')
        if runner.sha(Path(value['newAction']['execute']))!=value['pythonwSha256']:raise Error('Windowless interpreter changed')
        if check_backup and definition((path/'task-before.xml').read_text())!=value['taskDefinition']:raise Error('Task backup evidence changed')
        return path,value
    def checked_task(self,value):
        snapshot=self.task.read()
        if definition(snapshot['xml'])!=value['taskDefinition']:raise Error('Task settings/triggers/principal changed; reconcile manually')
        return snapshot
    def apply(self,identifier):
        with auto.exclusive(self.manager.lock):
            path,value=self.load(identifier)
            if value['status']!='PREPARED':raise Error('Interrupted upgrade requires explicit recovery')
            self.accepted(value['commit']);snapshot=self.checked_task(value)
            if snapshot['running'] or snapshot['enabled']!=value['wasEnabled'] or snapshot['action']!=value['oldAction'] or runner.sha(self.pointer)!=value['oldSha256']:raise Error('Old runtime/task no longer matches the reviewed plan')
            self.save(path,'DISABLING');self.task.enable(False)
            if self.checked_task(value)['running']:raise Error('Existing source runner still active; task stays disabled for review')
            self.save(path,'POINTER_PENDING')
            auto.sync.atomic_write(self.pointer,(path/'new-runtime.json').read_bytes())
            self.save(path,'ACTION_PENDING');self.task.action(value['newAction'])
            current=self.checked_task(value)
            if current['action']!=value['newAction'] or current['enabled']:raise Error('New disabled task action did not verify')
            self.save(path,'VERIFY_PENDING',applied=time.time())
            self.task.enable(value['wasEnabled'])
            return json.loads((path/'journal.json').read_text())
    def verify(self,identifier):
        with auto.exclusive(self.manager.lock):
            path,value=self.load(identifier);snapshot=self.checked_task(value)
            if value['status']!='VERIFY_PENDING':raise Error('Upgrade is not waiting for scheduled smoke verification')
            if runner.sha(self.pointer)!=value['newSha256'] or snapshot['action']!=value['newAction'] or snapshot['enabled']!=value['wasEnabled']:raise Error('New runtime/task identity drift')
            receipt=json.loads((self.private/'last-run.json').read_text());result=self.task.result()
            if result['running'] or result['result']!=0 or result.get('lastRun',0)<value['applied']-1 or not result['lastRun']<=receipt.get('started',0)<=result['lastRun']+10 or receipt.get('started',0)<value['applied'] or receipt.get('controllerCommit')!=value['commit'] or receipt.get('returnCode')!=0 or receipt.get('status') not in {'IDLE','COMPLETE','CI_PENDING','WAITING_RETRY','PAUSED'}:raise Error('Fresh successful scheduled receipt required; upgrade remains unverified')
            return self.save(path,'COMPLETE',scheduledReceipt=receipt,verified=time.time())
    def rollback(self,identifier):
        with auto.exclusive(self.manager.lock):
            path,value=self.load(identifier);snapshot=self.checked_task(value)
            if value['status'] in DONE:raise Error('Terminal upgrade cannot be blindly replayed')
            if snapshot['running']:raise Error('Do not interrupt an active source runner')
            if snapshot['action'] not in (value['oldAction'],value['newAction']) or runner.sha(self.pointer) not in (value['oldSha256'],value['newSha256']):raise Error('Unknown task/pointer; preserve evidence for manual recovery')
            self.save(path,'ROLLBACK_PENDING');self.task.enable(False)
            if self.checked_task(value)['running']:raise Error('Source runner started concurrently; keep task disabled')
            auto.sync.atomic_write(self.pointer,(path/'old-runtime.json').read_bytes())
            self.task.action(value['oldAction']);current=self.checked_task(value)
            if current['action']!=value['oldAction'] or runner.sha(self.pointer)!=value['oldSha256']:raise Error('Rollback identity did not verify')
            self.task.enable(value['wasEnabled'])
            return self.save(path,'ROLLED_BACK',rolledBack=time.time())
    def abandon(self,identifier):
        """Cancel only an untouched prepared plan; never repair bad evidence."""
        with auto.exclusive(self.manager.lock):
            path,value=self.load(identifier,check_backup=False);snapshot=self.checked_task(value)
            if value['status']!='PREPARED' or snapshot['running'] or snapshot['enabled']!=value['wasEnabled'] or snapshot['action']!=value['oldAction'] or runner.sha(self.pointer)!=value['oldSha256']:raise Error('Only an unchanged, unapplied prepared upgrade can be abandoned')
            return self.save(path,'ABANDONED',reason='Prepared upgrade explicitly cancelled; no pointer/task mutation; all backup evidence retained')

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('command',choices=['prepare','apply','verify','rollback','abandon','status']);parser.add_argument('--upgrade')
    args=parser.parse_args();upgrade=Upgrade(args.config)
    if args.command=='prepare':result=upgrade.prepare()
    elif args.command=='status':result={'pending':[p.name for p in upgrade.pending()]}
    else:result=getattr(upgrade,args.command)(args.upgrade)
    print(json.dumps(result,indent=2))
if __name__=='__main__':
    try:main()
    except (Error,auto.Error,OSError,ValueError,KeyError) as error:raise SystemExit('Source-runtime upgrade stopped safely: '+str(error))
