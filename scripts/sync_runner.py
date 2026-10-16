"""Launch only an accepted, frozen source-publication controller (no coding agent)."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

SCRIPTS={'auto_sync.py','sync_source.py','audit_repository.py','sync_runner.py'}
class RunnerError(RuntimeError):pass
def sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
def ordinary(path):
    if not path.is_absolute():raise RunnerError('Absolute configured paths required')
    for p in (path,*path.parents):
        if p.exists() and (p.is_symlink() or getattr(p.lstat(),'st_file_attributes',0)&0x400):raise RunnerError('Linked/reparse runtime path')
    if path.is_file() and path.stat().st_nlink!=1:raise RunnerError('Runtime hard links are not accepted')
    return path.resolve()
def validate(manifest_path):
    manifest_path=ordinary(Path(manifest_path));manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    fields={'version','commit','privateRoot','runtimeRoot','repository','source','configPath','configSha256','pythonPath','pythonSha256','scripts'}
    if set(manifest)!=fields or manifest['version']!=1 or not re.fullmatch('[0-9a-f]{40}',manifest['commit']):raise RunnerError('Invalid accepted runtime manifest')
    paths={key:ordinary(Path(manifest[key])) for key in ('privateRoot','runtimeRoot','repository','source','configPath','pythonPath')}
    private=paths['privateRoot'];runtime=paths['runtimeRoot']
    if private.is_relative_to(paths['repository']) or private.is_relative_to(paths['source']):raise RunnerError('Private runtime overlaps project trees')
    if not runtime.is_relative_to(private/'runtime') or runtime.name!=manifest['commit']:raise RunnerError('Runtime must be in its accepted commit directory')
    if not manifest_path.is_relative_to(private) or not paths['configPath'].is_relative_to(private):raise RunnerError('Manifest/config must remain private')
    if paths['repository']==paths['source'] or paths['repository'].is_relative_to(paths['source']) or paths['source'].is_relative_to(paths['repository']):raise RunnerError('Project roots overlap')
    if set(manifest['scripts'])!=SCRIPTS:raise RunnerError('Complete fixed controller script set is required')
    for name,expected in manifest['scripts'].items():
        if not isinstance(expected,str) or not re.fullmatch('[0-9a-f]{64}',expected):raise RunnerError('Invalid script identity')
        path=ordinary(runtime/'scripts'/name)
        if not path.is_file() or sha(path)!=expected:raise RunnerError('Accepted controller content changed: '+name)
    if sha(paths['configPath'])!=manifest['configSha256'] or sha(paths['pythonPath'])!=manifest['pythonSha256']:raise RunnerError('Approved configuration/interpreter identity changed')
    config=json.loads(paths['configPath'].read_text(encoding='utf-8'))
    if ordinary(Path(config['repository']))!=paths['repository'] or ordinary(Path(config['source']))!=paths['source'] or ordinary(Path(config['stateRoot']))!=private or ordinary(Path(config['python']))!=paths['pythonPath']:raise RunnerError('Configured project/runtime identity mismatch')
    return manifest,paths
def atomic(path,value):
    descriptor,temporary=tempfile.mkstemp(prefix='.runner-',dir=path.parent)
    try:
        with os.fdopen(descriptor,'w',encoding='utf-8') as stream:json.dump(value,stream);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)
def run(manifest_path):
    manifest,paths=validate(manifest_path)
    started=time.monotonic();command=[str(paths['pythonPath']),'-I',str(paths['runtimeRoot']/'scripts/auto_sync.py'),'--config',str(paths['configPath']),'run-once']
    env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','PYTHONUTF8':'1','GIT_TERMINAL_PROMPT':'0','GCM_INTERACTIVE':'Never'}
    receipt={'started':time.time(),'controllerCommit':manifest['commit'],'scope':'Accepted source publication only'}
    try:
        child=subprocess.run(command,cwd=paths['privateRoot'],capture_output=True,env=env,timeout=110)
        if len(child.stdout)>8*1024**2:raise RunnerError('Controller output exceeded its bounded receipt size')
        receipt.update(returnCode=child.returncode,stdoutSha256=hashlib.sha256(child.stdout).hexdigest(),stderrSha256=hashlib.sha256(child.stderr).hexdigest())
        if child.returncode==0:
            output=json.loads(child.stdout)
            receipt.update(status=output.get('status','UNKNOWN'),batch=output.get('id'),sourceCommit=output.get('commit'))
            if receipt['status'] not in {'IDLE','PAUSED','WAITING_RETRY','COMPLETE','CI_PENDING','OFFLINE_RETRY'}:
                receipt.update(returnCode=2,requiresReview=True)
        else:receipt.update(status='CONTROLLER_STOPPED',requiresReview=True)
    except subprocess.TimeoutExpired:
        receipt.update(status='TIMEOUT',requiresReview=True,returnCode=2)
    except (ValueError,RunnerError):
        receipt.update(status='OUTPUT_INVALID',requiresReview=True,returnCode=2)
    receipt['elapsedSeconds']=time.monotonic()-started
    atomic(paths['privateRoot']/'last-run.json',receipt)
    return receipt
def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--manifest',type=Path,required=True);args=parser.parse_args()
    result=run(args.manifest);print(json.dumps(result,indent=2));return result['returnCode']
if __name__=='__main__':
    try:raise SystemExit(main())
    except (RunnerError,OSError,ValueError,KeyError) as error:raise SystemExit('Frozen source runner stopped safely: '+str(error))
