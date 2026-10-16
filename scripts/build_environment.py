"""Private, read-only build-environment fingerprints; never a model downloader.

An observed local environment is not proof of licensing or clean-machine
reproducibility. Receipts contain machine paths and must remain outside Git.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import time
spec=importlib.util.spec_from_file_location('build_input_foundation',Path(__file__).with_name('candidate_inputs.py'))
foundation=importlib.util.module_from_spec(spec);spec.loader.exec_module(foundation)
CandidateError=foundation.CandidateError;ordinary=foundation.ordinary;relative=foundation.relative;digest=foundation.digest

IGNORED={'__pycache__','.vite','.vite-temp','.cache'}
MAX_FILES=80000
MAX_BYTES=4*1024**3
MAX_SECONDS=90
class EnvironmentError(CandidateError):pass
def stable_hash(path,deadline):
    before=path.stat();h=hashlib.sha256()
    with path.open('rb') as stream:
        while True:
            if time.monotonic()>deadline:raise EnvironmentError('Bounded environment scan timed out')
            block=stream.read(1024**2)
            if not block:break
            h.update(block)
    after=path.stat()
    if (before.st_size,before.st_mtime_ns,before.st_ino)!=(after.st_size,after.st_mtime_ns,after.st_ino):raise EnvironmentError('Build dependency changed while hashing')
    return h.hexdigest(),after.st_size
def inventory(roots,executables,source,repository):
    if set(roots)!={'pythonEnvironment','pythonBase','frontendDependencies'} or set(executables)!={'python','node'}:raise EnvironmentError('Fixed build environment roles required')
    source=ordinary(source);repository=ordinary(repository)
    roots={k:ordinary(v) for k,v in roots.items()};executables={k:ordinary(v) for k,v in executables.items()}
    if roots['frontendDependencies']!=source/'frontend-source/node_modules':raise EnvironmentError('Only existing frontend dependency cache is permitted')
    if executables['python']!=roots['pythonEnvironment']/'Scripts/python.exe':raise EnvironmentError('Expected the approved environment interpreter')
    venv_config=roots['pythonEnvironment']/'pyvenv.cfg'
    if not venv_config.is_file():raise EnvironmentError('An isolated venv configuration is required')
    fields=[tuple(value.strip() for value in line.split('=',1)) for line in venv_config.read_text(encoding='utf-8').splitlines() if '=' in line and not line.lstrip().startswith('#')]
    inherited=[value.lower() for key,value in fields if key.lower()=='include-system-site-packages']
    homes=[value for key,value in fields if key.lower()=='home']
    if inherited!=['false'] or len(homes)!=1 or ordinary(Path(homes[0]))!=roots['pythonBase']:raise EnvironmentError('Venv must disable global site packages and match its configured base runtime')
    for key in ('pythonEnvironment','pythonBase'):
        for tree in (source,repository):
            if roots[key].is_relative_to(tree) or tree.is_relative_to(roots[key]):raise EnvironmentError('Python environment overlaps source/publication')
    for root in roots.values():
        if not root.is_dir():raise EnvironmentError('Missing build environment root')
    if len(set(roots.values()))!=3:raise EnvironmentError('Environment roles overlap')
    files={};links={};sizes=0;count=0;topology={};started=time.monotonic();deadline=started+MAX_SECONDS
    pending=[];role_seconds={}
    pool=ThreadPoolExecutor(max_workers=4,thread_name_prefix='build-input-hash')
    def drain():
        for key,future in pending:files[key]=future.result()[0]
        pending.clear()
    def children(directory):
        result=[]
        for path in directory.iterdir():
            if path.name in IGNORED or path.name.endswith(('.pyc','.pyo')):continue
            is_link=path.is_symlink() or getattr(path.lstat(),'st_file_attributes',0)&0x400
            kind=('link:'+str(path.resolve(strict=True))) if is_link else 'directory' if path.is_dir() else 'file'
            result.append((path.name,kind))
        return sorted(result)
    with ExitStack() as cleanup:
      cleanup.callback(pool.shutdown,wait=True,cancel_futures=True)
      for role,root in sorted(roots.items()):
        role_started=time.monotonic()
        for directory,dirs,names in os.walk(root,followlinks=False):
            if time.monotonic()>deadline or len(topology)>MAX_FILES:raise EnvironmentError('Environment topology exceeded its bounded budget')
            topology[Path(directory)]=children(Path(directory))
            dirs[:]=sorted(d for d in dirs if d not in IGNORED)
            # The base runtime's unrelated tool packages are not imported by
            # this isolated venv. Include its stdlib/DLLs, not global packages.
            if role=='pythonBase' and Path(directory)==root/'Lib':dirs[:]=[d for d in dirs if d!='site-packages']
            entries=[*(Path(directory)/n for n in dirs),*(Path(directory)/n for n in sorted(names) if not n.endswith(('.pyc','.pyo')))]
            for path in entries:
                name=relative(path.relative_to(root).as_posix());key=role+'/'+name
                linked=path.is_symlink() or getattr(path.lstat(),'st_file_attributes',0)&0x400
                if linked:
                    target=path.resolve(strict=True)
                    if not target.is_relative_to(root):raise EnvironmentError('Dependency link escapes its approved cache')
                    if any(part in IGNORED for part in target.relative_to(root).parts) or target.name.endswith(('.pyc','.pyo')):raise EnvironmentError('Dependency link points into excluded transient inputs')
                    links[key]=target.relative_to(root).as_posix()
                    if path.name in dirs:dirs.remove(path.name)
                    continue
                if path.is_dir():continue
                if not path.is_file():raise EnvironmentError('Non-file dependency entry')
                if path.suffix.lower() in {'.gguf','.safetensors'}:raise EnvironmentError('Model weights are outside build-environment scope')
                count+=1;sizes+=path.stat().st_size
                if count>MAX_FILES or sizes>MAX_BYTES:raise EnvironmentError('Environment inventory exceeded its bounded budget')
                pending.append((key,pool.submit(stable_hash,path,deadline)))
                if len(pending)>=64:drain()
        drain();role_seconds[role]=time.monotonic()-role_started
    for directory,expected in topology.items():
        if time.monotonic()>deadline or children(directory)!=expected:raise EnvironmentError('Dependency topology changed during inventory')
    binaries={key:stable_hash(path,deadline)[0] for key,path in sorted(executables.items())}
    identity={'version':1,'source':str(source),'repository':str(repository),'roots':{k:str(v) for k,v in roots.items()},
        'executables':{k:str(v) for k,v in executables.items()},'executableHashes':binaries,'files':files,'internalLinks':links,
        'ignoredTransientNames':sorted(IGNORED),'ignoredSuffixes':['.pyc','.pyo'],'excludedBaseGlobalSitePackages':True}
    return {**identity,'environmentSha256':digest(identity),'fileCount':count,'fileBytes':sizes,'elapsedSeconds':time.monotonic()-started,'roleElapsedSeconds':role_seconds,'hashWorkers':4,
        'scope':'Observed existing local build inputs only; no dependency install, model execution, native-origin or redistribution claim'}
def capture(roots,executables,source,repository,output):
    output=ordinary(output)
    for tree in [ordinary(source),ordinary(repository),*(ordinary(p) for p in roots.values())]:
        if output.is_relative_to(tree):raise EnvironmentError('Machine-specific environment receipts must remain private outside inputs')
    if output.exists():raise EnvironmentError('Preserve previous environment receipt')
    value=inventory(roots,executables,source,repository)
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x',encoding='utf-8') as stream:json.dump(value,stream,indent=2)
    return value
def verify(receipt,expected_sha):
    receipt=ordinary(receipt)
    if hashlib.sha256(receipt.read_bytes()).hexdigest()!=expected_sha:raise EnvironmentError('Reviewed environment receipt changed')
    old=json.loads(receipt.read_text());current=inventory(old['roots'],old['executables'],old['source'],old['repository'])
    if current['environmentSha256']!=old['environmentSha256']:raise EnvironmentError('Build environment drift; review before building')
    return current
def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    create=sub.add_parser('capture')
    for key in ('python-env','python-base','frontend-deps','python','node','source','repository','output'):create.add_argument('--'+key,type=Path,required=True)
    check=sub.add_parser('verify');check.add_argument('--receipt',type=Path,required=True);check.add_argument('--sha256',required=True)
    args=parser.parse_args()
    if args.command=='capture':value=capture({'pythonEnvironment':args.python_env,'pythonBase':args.python_base,'frontendDependencies':args.frontend_deps},{'python':args.python,'node':args.node},args.source,args.repository,args.output)
    else:value=verify(args.receipt,args.sha256)
    print(json.dumps({k:v for k,v in value.items() if k not in {'files','internalLinks','roots','executables'}},indent=2))
if __name__=='__main__':main()
