"""Immutable application input and existing onedir association, without inference.

This is a candidate-build foundation, not an installer or a claim of native
provenance. A no-change association is explicitly distinct from a new build.
"""
import argparse
import hashlib
import json
import marshal
from pathlib import Path,PurePosixPath,PureWindowsPath
import re
import subprocess
import types

class CandidateError(RuntimeError):pass
def sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def relative(value):
    if not isinstance(value,str) or not value or any(ord(c)<32 for c in value):raise CandidateError('Invalid relative input path')
    windows=PureWindowsPath(value);parts=PurePosixPath(value.replace('\\','/')).parts
    if windows.drive or windows.root or not parts or any(p in {'.','..'} or ':' in p or p.endswith(('.',' ')) or re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?',p) for p in parts):raise CandidateError('Unsafe relative input path')
    return '/'.join(parts)
def ordinary(path):
    path=Path(path)
    if not path.is_absolute():raise CandidateError('Absolute roots required')
    for part in (path,*path.parents):
        if part.exists() and (part.is_symlink() or getattr(part.lstat(),'st_file_attributes',0)&0x400):raise CandidateError('Linked/reparse input path')
    return path.resolve()
def git(repo,executable,*args):
    result=subprocess.run([str(executable),'--no-replace-objects','-C',str(repo),*args],capture_output=True,timeout=30)
    if result.returncode:raise CandidateError('Bounded Git input read failed: '+args[0])
    return result.stdout
def batch_blobs(repo,executable,oids):
    if not oids:return []
    if any(not re.fullmatch('[0-9a-f]{40}',oid) for oid in oids):raise CandidateError('Invalid immutable blob identity')
    proc=subprocess.run([str(executable),'--no-replace-objects','-C',str(repo),'cat-file','--batch'],input=('\n'.join(oids)+'\n').encode(),capture_output=True,timeout=30)
    if proc.returncode:raise CandidateError('Bounded Git batch read failed')
    raw=proc.stdout;offset=0;result=[]
    for oid in oids:
        end=raw.find(b'\n',offset)
        if end<0:raise CandidateError('Truncated Git batch header')
        fields=raw[offset:end].split()
        if len(fields)!=3 or fields[:2]!=[oid.encode(),b'blob'] or not fields[2].isdigit():raise CandidateError('Invalid Git batch blob header')
        size=int(fields[2]);offset=end+1;data=raw[offset:offset+size];offset+=size
        if len(data)!=size or raw[offset:offset+1]!=b'\n':raise CandidateError('Truncated Git batch content')
        if hashlib.sha1(b'blob '+str(size).encode()+b'\0'+data).hexdigest()!=oid:raise CandidateError('Git batch blob identity mismatch')
        result.append(data);offset+=1
    if offset!=len(raw):raise CandidateError('Unexpected Git batch trailing content')
    return result

def inputs(repo,commit,executable):
    repo=ordinary(repo)
    if not re.fullmatch('[0-9a-f]{40}',commit):raise CandidateError('Exact source commit required')
    policy=json.loads(git(repo,executable,'show',commit+':scripts/source_sync_policy.json'))
    names=sorted(p for p in policy['managed'] if (p.endswith('.py') and '/' not in p) or p.startswith('frontend-source/') or p in {'Offline GPT.spec','assistant_system_prompt.md'})
    tree={}
    for entry in git(repo,executable,'ls-tree','-r','-z',commit).split(b'\0'):
        if not entry:continue
        try:
            metadata,raw_name=entry.split(b'\t',1)
            name=raw_name.decode('utf-8');fields=metadata.decode('ascii').split()
        except (ValueError,UnicodeError) as exc:raise CandidateError('Malformed immutable Git tree') from exc
        if name in tree or len(fields)!=3 or not re.fullmatch('[0-9a-f]{40}',fields[2]):raise CandidateError('Malformed or duplicate immutable Git tree entry')
        tree[name]=fields
    tracked=sorted(tree)
    declarations=[p for p in tracked if '/' not in p and re.fullmatch(r'(?:requirements|constraints)(?:[-_.][A-Za-z0-9_-]+)?\.(?:txt|lock)',p)]
    names=sorted(set(names+declarations))
    if len({name.casefold() for name in names})!=len(names):raise CandidateError('Colliding Windows source input paths')
    if not {'app.py','Offline GPT.spec','assistant_system_prompt.md','frontend-source/package.json','frontend-source/pnpm-lock.yaml','frontend-source/vite.config.js'}<=set(names):raise CandidateError('Required build inputs omitted')
    files={};oids=[]
    for name in names:
        if relative(name)!=name:raise CandidateError('Non-canonical source path')
        metadata=tree.get(name,[])
        if len(metadata)<3 or metadata[0] not in {'100644','100755'} or metadata[1]!='blob':raise CandidateError('Source input is missing, linked or not a blob: '+name)
        oids.append(metadata[2])
    for name,data in zip(names,batch_blobs(repo,executable,oids)):
        # Text normalization matches the publication manifest. Asset bytes are
        # never normalized, even if they happen to contain a CRLF sequence.
        text_suffixes={'.py','.spec','.js','.jsx','.ts','.tsx','.css','.html','.json','.md','.svg','.yaml','.yml','.toml','.txt','.lock'}
        normalized=data.replace(b'\r\n',b'\n') if Path(name).suffix.lower() in text_suffixes else data
        files[name]=hashlib.sha256(normalized).hexdigest()
    return {'sourceCommit':commit,'files':files,'applicationInputSha256':digest(files)}
def structure(code):
    if not isinstance(code,types.CodeType):raise CandidateError('Expected compiled application module')
    fields=('co_argcount','co_posonlyargcount','co_kwonlyargcount','co_nlocals','co_stacksize','co_flags','co_code','co_names','co_varnames','co_freevars','co_cellvars','co_firstlineno','co_linetable','co_exceptiontable','co_name','co_qualname')
    constants=tuple(structure(v) if isinstance(v,types.CodeType) else (type(v).__qualname__,v) for v in code.co_consts)
    return tuple((field,getattr(code,field)) for field in fields)+(('co_consts',constants),)
def payload(package,manifest,expected_manifest_sha):
    package=ordinary(package);manifest=ordinary(manifest)
    if sha(manifest)!=expected_manifest_sha:raise CandidateError('Approved payload manifest identity changed')
    expected=json.loads(manifest.read_text());actual={};seen=set()
    if not isinstance(expected,dict) or not expected or 'Offline GPT.exe' not in expected:raise CandidateError('Complete onedir payload manifest required')
    for name,h in expected.items():
        name=relative(name)
        if name.casefold() in seen:raise CandidateError('Colliding Windows payload paths')
        seen.add(name.casefold())
        if not isinstance(h,str) or not re.fullmatch('[0-9a-f]{64}',h):raise CandidateError('Invalid payload digest')
        path=ordinary(package/name)
        if not path.is_relative_to(package) or not path.is_file() or path.stat().st_nlink!=1:raise CandidateError('Missing/unsafe payload: '+name)
        actual[name]=sha(path)
        if actual[name]!=h:raise CandidateError('Candidate payload differs: '+name)
    physical=set()
    for path in package.rglob('*'):
        ordinary(path)
        if path.is_file():physical.add(path.relative_to(package).as_posix())
    if physical!=set(actual):raise CandidateError('Unmanifested or stale candidate files')
    return {'payloadFiles':len(actual),'payloadSha256':digest(actual),'payloadManifestSha256':expected_manifest_sha}
def associate(repo,commit,executable,package,manifest,manifest_sha):
    graph=inputs(repo,commit,executable);verified=payload(package,manifest,manifest_sha)
    from PyInstaller.archive.readers import CArchiveReader
    archive=CArchiveReader(str(Path(package)/'Offline GPT.exe'));pyz=archive.open_embedded_archive('PYZ.pyz');modules={}
    for name in graph['files']:
        if not name.endswith('.py') or '/' in name:continue
        source=git(repo,executable,'show',commit+':'+name)
        embedded=marshal.loads(archive.extract('app')) if name=='app.py' else pyz.extract(Path(name).stem)
        modules[name]=structure(embedded)==structure(compile(source,'<accepted-source>','exec',dont_inherit=True,optimize=0))
    if not modules or not all(modules.values()):raise CandidateError('Embedded application differs from accepted source')
    return {**graph,**verified,'compiledModuleMatches':modules,'candidateSha256':sha(Path(package)/'Offline GPT.exe'),
        'actualBuildRun':False,'scope':'Application-subset and approved full COLLECT payload association only; UI derivation, native provenance, clean-machine and model acceptance are separate gates',
        'binaryPublication':'BLOCKED_NATIVE_PROVENANCE_AND_LICENSES'}
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('repo','git','package','manifest','output'):parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--commit',required=True);parser.add_argument('--manifest-sha256',required=True);args=parser.parse_args()
    output=ordinary(args.output)
    for root in (ordinary(args.repo),ordinary(args.package)):
        if output.is_relative_to(root):raise CandidateError('Evidence must remain outside repository/candidate')
    if output.exists():raise CandidateError('Preserve existing evidence')
    value=associate(args.repo,args.commit,args.git,args.package,args.manifest,args.manifest_sha256)
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x',encoding='utf-8') as stream:json.dump(value,stream,indent=2)
    print(json.dumps({k:v for k,v in value.items() if k not in {'files','compiledModuleMatches'}},indent=2))
if __name__=='__main__':main()
