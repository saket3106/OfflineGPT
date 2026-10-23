"""Process-local pristine controller fixtures, copied without shared file links.

Only the expensive initial disposable Git setup is reused. Test bodies, Git
transactions, privacy scans, CI checks and assertions still execute normally.
There is no disk cache, pickle, imported cache code or production input.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time

_images={}
_guard=threading.Lock()
_counts={'captured':0,'restored':0}

def _key(auto,fixtures):
    if hasattr(subprocess.run,'mock_calls') or hasattr(time.time,'mock_calls'):return None
    executable=os.environ.get('OFFLINEGPT_TEST_GIT') or shutil.which('git')
    if not executable:return None
    for name in ('GIT_DIR','GIT_WORK_TREE','GIT_INDEX_FILE','GIT_OBJECT_DIRECTORY','GIT_ALTERNATE_OBJECT_DIRECTORIES'):
        if os.environ.get(name):raise RuntimeError('Controller fixtures require isolated Git environment: '+name)
    p=Path(executable).resolve();s=p.stat()
    return (str(p),s.st_size,s.st_mtime_ns,id(auto),id(fixtures.SourceSyncTests.setUp),sys.executable,id(time.time),id(subprocess.run),tuple(sorted((k,v) for k,v in os.environ.items() if k.startswith('GIT_'))))

def _seal(entries,dirs,cfg,before):
    values={'files':[(name,hashlib.sha256(data).hexdigest(),mode) for name,data,mode in entries],'dirs':dirs,'configuration':cfg.decode('utf-8'),'sourceGitBefore':before}
    return hashlib.sha256(json.dumps(values,sort_keys=True,separators=(',',':')).encode('utf-8')).hexdigest()

def remember(case,auto,fixtures):
    key=_key(auto,fixtures)
    if key is None:return
    with _guard:
        if key in _images:return
        root=case.f.root.resolve()
        if root.parent!=Path(tempfile.gettempdir()).resolve() or not root.name.startswith('offlinegpt-sync-tests-'):raise RuntimeError('Only owned disposable controller fixtures may be reused')
        entries=[];dirs=[];total=0
        roots={case.f.root,root}
        tokens={value for base_root in roots for value in (str(base_root).encode(),base_root.as_posix().encode(),str(base_root).replace('\\','\\\\').encode())}
        if os.name=='nt':tokens={value.lower() for value in tokens}
        for base,folders,names in os.walk(root,topdown=True,followlinks=False):
            for name in sorted(folders+names):
                path=Path(base)/name;info=path.lstat();rel=path.relative_to(root).as_posix()
                if path.is_symlink() or getattr(info,'st_file_attributes',0)&0x400:raise RuntimeError('Linked fixture input')
                if path.is_dir():dirs.append((rel,stat.S_IMODE(info.st_mode)));continue
                if not path.is_file() or info.st_nlink!=1:raise RuntimeError('Non-ordinary fixture input')
                data=path.read_bytes();total+=len(data)
                if total>2*1024**2 or len(entries)>=500:raise RuntimeError('Pristine fixture exceeds its bounded cache scope')
                if rel not in {'repo/.git/config','private/config.json'} and any(token in (data.lower() if os.name=='nt' else data) for token in tokens):raise RuntimeError('Unrecognized absolute fixture reference: '+rel)
                entries.append((rel,data,stat.S_IMODE(info.st_mode)))
        entries=tuple(entries);dirs=tuple(dirs);cfg=json.dumps(case.cfg,sort_keys=True).encode('utf-8');before=case.f.git_before
        _images[key]=(entries,dirs,cfg,before,_seal(entries,dirs,cfg,before));_counts['captured']+=1

def restore(case,auto,fixtures):
    key=_key(auto,fixtures)
    with _guard:image=_images.get(key)
    if image is None:return False
    entries,dirs,cfg_bytes,before,expected=image
    if _seal(entries,dirs,cfg_bytes,before)!=expected:raise RuntimeError('Process-local pristine fixture integrity failure')
    f=fixtures.SourceSyncTests('runTest');f.git=key[0]
    f.temp=tempfile.TemporaryDirectory(prefix='offlinegpt-sync-tests-');f.addCleanup(f.temp.cleanup);case.addCleanup(f.doCleanups)
    f.root=Path(f.temp.name);f.repo=f.root/'repo';f.source=f.root/'source';f.reports=f.root/'reports'
    for rel,mode in dirs:
        path=f.root/rel;path.mkdir(parents=True,exist_ok=True);os.chmod(path,mode)
    for rel,data,mode in entries:
        path=f.root/rel
        if path.is_absolute() and not path.is_relative_to(f.root):raise RuntimeError('Fixture path escaped its fresh root')
        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data);os.chmod(path,mode)
    case.f=f;case.repo=f.repo;case.source=f.source;case.private=f.root/'private';case.config=case.private/'config.json';case.remote=f.root/'remote.git'
    cfg=json.loads(cfg_bytes)
    cfg.update(source=str(f.source.resolve()),repository=str(f.repo.resolve()),stateRoot=str(case.private.resolve()),remoteUrl=str(case.remote))
    case.cfg=cfg;case.config.write_text(json.dumps(cfg),encoding='utf-8')
    # Use real Git to rewrite its config; never edit Git objects or index bytes.
    result=subprocess.run([f.git,'-C',str(f.repo),'config','remote.origin.url',str(case.remote)],capture_output=True,timeout=15)
    if result.returncode:raise RuntimeError('Pristine fixture origin rebinding failed')
    f.policy=json.loads((f.repo/auto.sync.POLICY).read_text(encoding='utf-8'));f.state=json.loads((f.repo/auto.sync.STATE).read_text(encoding='utf-8'));f.git_before=before
    case.manager=auto.Manager(case.config,local_fixture=True)
    with _guard:_counts['restored']+=1
    return True

def clear():
    with _guard:_images.clear();_counts.update(captured=0,restored=0)
def statistics():
    with _guard:return {**_counts,'images':len(_images)}
