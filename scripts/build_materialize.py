"""Copy approved dependency bytes into private build inputs; never copy models."""
import concurrent.futures
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import time

def sibling(name):
    spec=importlib.util.spec_from_file_location('materialize_'+name,Path(__file__).with_name(name+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
inputs=sibling('candidate_inputs');Error=inputs.CandidateError
def powershell():return str(Path(os.environ.get('SystemRoot',r'C:\Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe')
def load(receipt,expected):
    receipt=inputs.ordinary(receipt)
    if inputs.sha(receipt)!=expected:raise Error('Reviewed environment receipt changed')
    value=json.loads(receipt.read_text());identity={k:v for k,v in value.items() if k not in {'environmentSha256','fileCount','fileBytes','elapsedSeconds','roleElapsedSeconds','hashWorkers','scope'}}
    if inputs.digest(identity)!=value['environmentSha256']:raise Error('Environment receipt integrity mismatch')
    if set(value['roots'])!={'pythonBase','pythonEnvironment','frontendDependencies'}:raise Error('Unknown dependency role')
    return value
def copy_verified(source,target,expected):
    source=inputs.ordinary(source);target=inputs.ordinary(target)
    if not source.is_file() or target.exists():raise Error('Missing source or existing materialization target')
    target.parent.mkdir(parents=True,exist_ok=True);h=hashlib.sha256()
    with source.open('rb') as src,target.open('xb') as dst:
        while block:=src.read(1024**2):dst.write(block);h.update(block)
        dst.flush();os.fsync(dst.fileno())
    if h.hexdigest()!=expected or inputs.sha(target)!=expected:raise Error('Materialized dependency differs from approved bytes')
def junction(path,target):
    if path.exists() or not target.is_dir():raise Error('Invalid internal dependency directory alias')
    path.parent.mkdir(parents=True,exist_ok=True)
    if os.name=='nt':
        quote=lambda p:"'"+str(p).replace("'","''")+"'"
        result=subprocess.run([powershell(),'-NoProfile','-NonInteractive','-Command',"$ErrorActionPreference='Stop';New-Item -ItemType Junction -Path "+quote(path)+' -Target '+quote(target)+'|Out-Null'],capture_output=True,timeout=15)
        if result.returncode:raise Error('Private dependency junction creation failed')
    else:path.symlink_to(target,target_is_directory=True)
    if path.resolve()!=target.resolve():raise Error('Internal dependency alias did not verify')
def materialize(receipt,expected,destination,monitor=lambda:None,frontend=None,allowed_pth=None):
    value=load(receipt,expected);destination=inputs.ordinary(destination)
    if destination.exists():raise Error('Never overwrite a dependency snapshot')
    roots={k:inputs.ordinary(Path(v)) for k,v in value['roots'].items()}
    for root in [*roots.values(),Path(value['source']),Path(value['repository'])]:
        if destination.is_relative_to(root) or root.is_relative_to(destination):raise Error('Dependency snapshot overlaps source inputs')
    targets={role:destination/role for role in roots}
    if frontend is not None:
        frontend=inputs.ordinary(frontend)
        if frontend.exists() or not frontend.is_relative_to(destination.parent):raise Error('Frontend cache must be new and inside this build attempt')
        targets['frontendDependencies']=frontend
    destination.mkdir(parents=True);seen=set();started=time.monotonic();pending=[]
    def budget():
        monitor()
        if time.monotonic()-started>180:raise Error('Dependency materialization budget exceeded')
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for name,h in value['files'].items():
            canonical=inputs.relative(name)
            if canonical!=name or canonical.casefold() in seen:raise Error('Noncanonical/colliding dependency path')
            seen.add(canonical.casefold());role,rel=name.split('/',1)
            if role not in roots or rel.endswith(('.pyc','.pyo')) or '__pycache__' in Path(rel).parts:raise Error('Unapproved cached executable input')
            if Path(rel).suffix.lower() in {'.gguf','.safetensors'}:raise Error('Model weights cannot enter build snapshots')
            if Path(rel).name in {'sitecustomize.py','usercustomize.py'}:raise Error('Unexpected Python startup customization')
            if rel.endswith('.pth') and (allowed_pth or {}).get(name)!=h:raise Error('Unreviewed .pth input; never process it silently')
            pending.append(pool.submit(copy_verified,roots[role]/rel,targets[role]/rel,h))
            if len(pending)>=64:
                for future in pending:future.result()
                pending.clear();budget()
        for future in pending:future.result()
    directory_links=[]
    for name,target in value['internalLinks'].items():
        if inputs.relative(name)!=name or name.casefold() in seen:raise Error('Colliding dependency alias')
        seen.add(name.casefold())
        budget();role,rel=inputs.relative(name).split('/',1);target=inputs.relative(target)
        if role!='frontendDependencies':raise Error('Python dependency aliases require explicit import-closure review')
        alias=targets[role]/rel;actual=inputs.ordinary(targets[role]/target)
        if not actual.is_relative_to(targets[role]):raise Error('Dependency alias escapes snapshot')
        if actual.is_file():copy_verified(actual,alias,inputs.sha(actual))
        else:directory_links.append((alias,actual))
    if os.name=='nt' and directory_links:
        mapping=destination/'internal-aliases.json';mapping.write_text(json.dumps([{'path':str(p),'target':str(t)} for p,t in directory_links]))
        for p,t in directory_links:
            if p.exists() or not t.is_dir():raise Error('Invalid internal alias')
            p.parent.mkdir(parents=True,exist_ok=True)
        quoted="'"+str(mapping).replace("'","''")+"'"
        script="$ErrorActionPreference='Stop';foreach($row in (Get-Content -LiteralPath "+quoted+" -Raw|ConvertFrom-Json)){New-Item -ItemType Junction -Path $row.path -Target $row.target|Out-Null}"
        r=subprocess.run([powershell(),'-NoProfile','-NonInteractive','-Command',script],capture_output=True,timeout=30)
        if r.returncode:raise Error('Closed internal alias materialization failed')
        for p,t in directory_links:
            if p.resolve()!=t.resolve():raise Error('Materialized alias identity changed')
    else:
        for p,t in directory_links:junction(p,t)
    for name,source in value['executables'].items():
        if name not in {'node','python'}:raise Error('Unapproved build executable')
        # Use the copied base interpreter with -I -S, not the venv launcher.
        if name=='node':copy_verified(Path(source),destination/'node.exe',value['executableHashes'][name])
    for root in destination.glob('python*'):
        if any(root.rglob('*.pyc')) or any(root.rglob('*.pyo')):raise Error('Bytecode cache entered clean build inputs')
    return {'environmentSha256':value['environmentSha256'],'files':len(value['files']),'aliases':len(value['internalLinks']),'elapsedSeconds':time.monotonic()-started,'python':str(destination/'pythonBase/python.exe'),'site':str(destination/'pythonEnvironment/Lib/site-packages'),'node':str(destination/'node.exe'),'frontend':str(targets['frontendDependencies'])}
