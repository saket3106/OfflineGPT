"""Frozen scheduled candidate worker; no agent, installer or model invocation."""
import argparse,hashlib,importlib.util,json,os,re,sys,time
from pathlib import Path
SCRIPTS={'candidate_runner.py','candidate_builder.py','candidate_inputs.py','candidate_smoke.py','build_materialize.py','build_bootstrap.py','build_process.py','auto_sync.py','sync_source.py','audit_repository.py'}
class Error(RuntimeError):pass
def sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
def ordinary(path):
    if not path.is_absolute():raise Error('Absolute frozen worker paths required')
    for part in (path,*path.parents):
        if part.exists() and (part.is_symlink() or getattr(part.lstat(),'st_file_attributes',0)&0x400):raise Error('Linked frozen worker path')
    if path.is_file() and path.stat().st_nlink!=1:raise Error('Hard-linked frozen worker input')
    return path.resolve()
def validate(path):
    path=ordinary(Path(path));v=json.loads(path.read_text())
    if set(v)!={'version','commit','privateRoot','runtimeRoot','configPath','configSha256','pythonPath','pythonSha256','pythonwSha256','scripts'} or v['version']!=1 or not re.fullmatch('[a-f0-9]{40}',v['commit']):raise Error('Invalid candidate-worker manifest')
    roots={k:ordinary(Path(v[k])) for k in ('privateRoot','runtimeRoot','configPath','pythonPath')};private=roots['privateRoot'];runtime=roots['runtimeRoot']
    if runtime!=private/'runtime'/v['commit'] or not roots['configPath'].is_relative_to(private) or not path.is_relative_to(private):raise Error('Worker roots are not bound to a private accepted revision')
    if set(v['scripts'])!=SCRIPTS:raise Error('Incomplete frozen worker scripts')
    for name,h in v['scripts'].items():
        if not re.fullmatch('[a-f0-9]{64}',h) or sha(ordinary(runtime/'scripts'/name))!=h:raise Error('Frozen worker changed: '+name)
    if sha(roots['configPath'])!=v['configSha256'] or sha(roots['pythonPath'])!=v['pythonSha256'] or sha(ordinary(roots['pythonPath'].with_name('pythonw.exe')))!=v['pythonwSha256']:raise Error('Worker profile/interpreter changed')
    cfg=json.loads(roots['configPath'].read_text())
    if cfg['controllerCommit']!=v['commit'] or ordinary(Path(cfg['privateRoot']))!=private:raise Error('Worker profile binding differs')
    return v,roots
def run(path):
    v,roots=validate(path);started=time.monotonic();receipt={'started':time.time(),'controllerCommit':v['commit'],'scope':'Private isolated non-native candidate building only'}
    spec=importlib.util.spec_from_file_location('frozen_candidate_builder',roots['runtimeRoot']/'scripts/candidate_builder.py')
    builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
    try:
        controller=builder.Builder(roots['configPath']);discovery=controller.discover();result=controller.once()
        receipt.update(status=result['status'],buildKey=result.get('buildKey'),actualBuildRun=result.get('actualBuildRun',False),discoveryCount=len(discovery),returnCode=0 if result['status'] in {'IDLE','PAUSED','COMPLETE'} else 2)
    except (builder.Error,builder.auto.Error,OSError,ValueError,KeyError) as error:receipt.update(status='STOPPED',reason=str(error),returnCode=2)
    receipt['elapsedSeconds']=time.monotonic()-started
    builder.save(roots['privateRoot']/'last-worker-run.json',receipt)
    return receipt
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--manifest',type=Path,required=True);a=p.parse_args()
    if not sys.flags.isolated or not sys.flags.no_site:raise Error('Scheduled worker requires -I -S')
    result=run(a.manifest);print(json.dumps(result,indent=2));return result['returnCode']
if __name__=='__main__':
    try:raise SystemExit(main())
    except (Error,OSError,ValueError,KeyError) as error:raise SystemExit('Frozen candidate worker stopped safely: '+str(error))
