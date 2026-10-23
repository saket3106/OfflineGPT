"""Reviewed source synchronization. Never stages, commits, pushes or writes .git.

Reports/backups are private external artifacts. File approvals are exact paths,
not recursive copy rules. Imported application code is never executed.
"""
from __future__ import annotations
import argparse
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile
import uuid
from functools import lru_cache

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
POLICY = 'scripts/source_sync_policy.json'
STATE = 'scripts/source_sync_state.json'
MANIFEST = 'SOURCE_MANIFEST.sha256'
LOCK = '.source-sync.lock'
MAX_FILE = 10 * 1024 * 1024
DENIED_DIRS = {'.git', 'node_modules', '.venv', 'venv', 'logs', 'reports', 'diagnostics', 'backups', 'generated', 'thumbnails', 'models', 'profiles', 'build', 'dist', 'ui', '__pycache__', 'attachments'}
TEXT = {'.py', '.spec', '.md', '.txt', '.json', '.jsonl', '.jsx', '.js', '.css', '.html', '.yaml', '.yml', '.sha256'}
DEPENDENCIES = {'requirements-agent.txt', 'requirements.txt', 'requirements-voice.txt', 'requirements-build.txt', 'constraints-windows.txt', 'frontend-source/package.json', 'frontend-source/pnpm-lock.yaml', 'Offline GPT.spec'}

class SyncError(RuntimeError):
    pass

def digest(data):
    return hashlib.sha256(data).hexdigest()

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()

def load(path):
    return json.loads(path.read_text(encoding='utf-8'))

def linked(path):
    return path.is_symlink() or bool(getattr(path.lstat(), 'st_file_attributes', 0) & 0x400)

def safe_path(root, rel):
    if not isinstance(rel, str) or '\\' in rel or ':' in rel or any(ord(c)<32 for c in rel):
        raise SyncError('Non-portable relative path')
    parts = PurePosixPath(rel).parts
    if not parts or rel != '/'.join(parts) or any(p in {'.', '..'} or p.casefold() in DENIED_DIRS for p in parts) or PurePosixPath(rel).is_absolute():
        raise SyncError('Forbidden path: ' + rel)
    reserved={'con','prn','aux','nul'}|{'com'+str(i) for i in range(1,10)}|{'lpt'+str(i) for i in range(1,10)}
    if any(p.endswith((' ','.')) or p.split('.')[0].casefold() in reserved for p in parts):raise SyncError('Non-portable Windows path')
    path = root
    for part in parts:
        path = path / part
        if path.exists() or path.is_symlink():
            if linked(path):
                raise SyncError('Link/reparse point requires manual handling: ' + rel)
    if not path.resolve().is_relative_to(root.resolve()):
        raise SyncError('Path escapes root')
    return path

def file_bytes(root, rel):
    p = safe_path(root, rel)
    if not p.exists():
        return None
    if not p.is_file() or p.stat().st_size > MAX_FILE:
        raise SyncError('Not a bounded source file: ' + rel)
    return p.read_bytes()

def file_hash(root, rel):
    data = file_bytes(root, rel)
    return digest(data) if data is not None else None

def git(repo, executable, *args, check=True):
    env = {**os.environ, 'GIT_OPTIONAL_LOCKS': '0'}
    result = subprocess.run([str(executable), '-C', str(repo), *args], capture_output=True, env=env, timeout=30)
    if check and result.returncode:
        raise SyncError('Read-only Git command failed: ' + args[0])
    return result

def git_fingerprint(repo, executable):
    top = git(repo, executable, 'rev-parse', '--show-toplevel').stdout.decode().strip()
    if Path(top).resolve() != repo.resolve() or not (repo / '.git').is_dir() or linked(repo / '.git'):
        raise SyncError('Expected an ordinary existing repository at the target root')
    files = {}
    for base, dirs, names in os.walk(repo / '.git', followlinks=False):
        for name in dirs + names:
            if linked(Path(base) / name):raise SyncError('Git metadata contains a link')
        for name in names:
            p = Path(base) / name
            if name.endswith('.lock'):raise SyncError('Git operation in progress')
            h=hashlib.sha256()
            with p.open('rb') as stream:
                for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
            files[p.relative_to(repo / '.git').as_posix()] = h.hexdigest()
    return digest(canonical(files))

def publication_files(repo, executable):
    raw = git(repo, executable, 'ls-files', '--cached', '--others', '--exclude-standard', '-z').stdout
    names = sorted(set(raw.decode('utf-8').split('\0')) - {''})
    return names

@lru_cache(maxsize=1)
def audit_module():
    spec = importlib.util.spec_from_file_location('publication_audit', Path(__file__).with_name('audit_repository.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def scan_blob(rel, data):
    module = audit_module()
    name = PurePosixPath(rel).name
    suffix = PurePosixPath(rel).suffix.lower()
    problems = []
    if suffix in module.FORBIDDEN_SUFFIXES or re.search(r'\.(?:sqlite3?|db)(?:-|$)', name, re.I) or name in {'.env', '.npmrc', '.pypirc', '.netrc', '_netrc', '.git-credentials', 'id_rsa', 'id_ed25519'} or name.startswith('.env.') or name.startswith('offline_gpt_config') and name != 'offline_gpt_config.example.json':
        problems.append({'file': rel, 'kind': 'private_or_binary_asset'})
    if len(data) > MAX_FILE:
        problems.append({'file': rel, 'kind': 'source_size_limit'})
    if suffix in TEXT or name.startswith('.') or rel == 'requirements-agent-windows-py312.lock':
        try:
            text = data.decode('utf-8')
        except UnicodeError:
            return problems + [{'file': rel, 'kind': 'non_utf8_text'}]
        for kind, pattern in module.PATTERNS.items():
            for match in pattern.finditer(text):
                problems.append({'file': rel, 'kind': kind, 'line': text.count('\n', 0, match.start()) + 1})
        if rel == 'requirements-agent-windows-py312.lock':
            pins=set()
            for line_number,line in enumerate(text.splitlines(),1):
                line=line.strip()
                if not line or line.startswith('#'):continue
                match=re.fullmatch(r'([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+!-]+) --hash=sha256:([a-f0-9]{64})',line)
                key=re.sub(r'[-_.]+','-',match[1]).lower() if match else None
                if not match or key in pins:problems.append({'file':rel,'kind':'invalid_agent_lock','line':line_number})
                if key:pins.add(key)
        if suffix in {'.py', '.spec'}:
            try:ast.parse(text, filename=rel)
            except SyntaxError as error:problems.append({'file': rel, 'kind': 'python_syntax', 'line': error.lineno})
        if suffix == '.json':
            try:json.loads(text)
            except ValueError:problems.append({'file': rel, 'kind': 'json_syntax'})
    elif suffix in {'.png', '.ico', '.svg'}:
        # Source assets remain manually approved; extension alone is insufficient.
        if suffix == '.png' and not data.startswith(b'\x89PNG\r\n\x1a\n'):problems.append({'file': rel, 'kind': 'invalid_png_header'})
        if suffix == '.ico' and not data.startswith(b'\x00\x00\x01\x00'):problems.append({'file': rel, 'kind': 'invalid_icon_header'})
        if suffix == '.svg' and (b'<svg' not in data or re.search(rb'<script|https?://(?!www\.w3\.org)', data, re.I)):problems.append({'file': rel, 'kind': 'svg_requires_review'})
    else:
        problems.append({'file': rel, 'kind': 'unapproved_file_type'})
    return problems

def source_candidates(source):
    result = {p.name for p in source.glob('*.py') if p.is_file()}
    for prefix in ('frontend-source/src', 'frontend-source/public', 'evals', 'docs'):
        folder = safe_path(source, prefix)
        if not folder.exists():continue
        if linked(folder):raise SyncError('Candidate directory is a link')
        count = 0
        for base, dirs, files in os.walk(folder, followlinks=False):
            dirs[:] = [d for d in dirs if d.casefold() not in DENIED_DIRS and not linked(Path(base) / d)]
            for name in files:
                count += 1
                if count > 2000:raise SyncError('Candidate discovery limit reached')
                p = Path(base) / name
                if p.suffix.lower() in TEXT | {'.svg', '.png', '.ico'} and '.bak' not in name:
                    result.add(p.relative_to(source).as_posix())
    result.update(p.relative_to(source).as_posix() for p in safe_path(source,'tests').glob('*') if p.is_file() and p.suffix.lower() in TEXT)
    result.update(p.relative_to(source).as_posix() for p in safe_path(source,'frontend-source').glob('*') if p.is_file() and p.suffix.lower() in TEXT)
    return result

def validate_policy(policy, repo):
    if policy.get('version') != 1:raise SyncError('Unsupported policy')
    managed = policy['managed'];protected = policy['protected']
    if set(managed) & set(protected):raise SyncError('Managed/protected path overlap')
    all_paths = managed + protected
    if len({p.casefold() for p in all_paths}) != len(all_paths):raise SyncError('Duplicate/case-colliding policy paths')
    if any(p in {POLICY, STATE, MANIFEST, '.gitignore', '.gitattributes'} or p.startswith('scripts/') for p in managed):raise SyncError('Sync controls cannot mirror themselves')
    for rel in all_paths:safe_path(repo, rel)

def context(repo, source, executable):
    if linked(repo) or linked(source):raise SyncError('Root links are unsupported')
    repo = repo.resolve();source = source.resolve()
    if repo == source or repo.is_relative_to(source) or source.is_relative_to(repo):raise SyncError('Source and repository must be disjoint')
    policy = load(repo / POLICY);state = load(repo / STATE);validate_policy(policy, repo)
    if state.get('version') != 1:raise SyncError('Unsupported state')
    if (repo/LOCK).exists():raise SyncError('Interrupted/concurrent synchronization; review the external journal before recovery')
    return repo, source, policy, state

def tree_hashes(repo, executable):
    return {rel: file_hash(repo, rel) for rel in publication_files(repo, executable)}

def dependencies(repo, proposed):
    declared = set()
    for name in DEPENDENCIES:
        data = proposed.get(name) if name in proposed else file_bytes(repo, name)
        if data is None:continue
        if name.startswith('requirements'):
            declared.update(re.findall(r'^([A-Za-z0-9_.-]+)', data.decode(), re.M))
    local={PurePosixPath(p).stem for p,data in proposed.items() if data is not None and PurePosixPath(p).parent==PurePosixPath('.') and p.endswith('.py')}
    local.update(p.stem for p in repo.glob('*.py') if proposed.get(p.name,b'') is not None)
    aliases={'PIL':'Pillow','docx':'python-docx','pptx':'python-pptx','fitz':'PyMuPDF','pymupdf':'PyMuPDF','kokoro_onnx':'kokoro-onnx','espeakng_loader':'espeakng-loader','imageio_ffmpeg':'imageio-ffmpeg','win32api':'pywin32','win32job':'pywin32','pywintypes':'pywin32'}
    test_local={p.stem for p in (repo/'tests').glob('*.py') if proposed.get('tests/'+p.name, b'') is not None}
    test_local.update(PurePosixPath(p).stem for p,data in proposed.items() if data is not None and PurePosixPath(p).parent==PurePosixPath('tests') and p.endswith('.py'))
    imports=set()
    for rel,data in proposed.items():
        if not rel.endswith('.py') or data is None:continue
        try:tree=ast.parse(data.decode())
        except (SyntaxError,UnicodeError):continue
        file_imports=set()
        for node in ast.walk(tree):
            if isinstance(node,ast.Import):file_imports.update(alias.name.split('.')[0] for alias in node.names)
            elif isinstance(node,ast.ImportFrom) and node.module and node.level==0:file_imports.add(node.module.split('.')[0])
        # unittest discovery deliberately places tests/ on its import path.
        # That does not make test-only helpers available to application code.
        imports.update(file_imports-test_local if PurePosixPath(rel).parent==PurePosixPath('tests') else file_imports)
    external=imports-set(sys.stdlib_module_names)-local
    normalize=lambda n:re.sub(r'[-_.]+','-',n).lower()
    declared_normal={normalize(n) for n in declared}
    missing=sorted(n for n in external if normalize(aliases.get(n,n)) not in declared_normal)
    return {'files': sorted(DEPENDENCIES), 'declaredPythonPackages': sorted(declared),'changedFileExternalImports':sorted(external),'undeclaredImports':missing,'licensing': 'Existing Qt/PyMuPDF/GPL speech/FLUX/native provenance decisions remain unresolved; manual review required.', 'changes': sorted(set(proposed) & DEPENDENCIES)}

def build_plan(repo, source, executable):
    repo, source, policy, state = context(repo, source, executable)
    before = git_fingerprint(repo, executable)
    actions = [];conflicts = [];source_hashes = {};proposed = {};protected_changed = []
    for rel in policy['managed']:
        src = file_hash(source, rel);dst = file_hash(repo, rel)
        source_hashes[rel] = src
        base = state['managed'].get(rel)
        if base is None:
            if src is None:conflicts.append({'file': rel, 'kind': 'new_approval_missing_source'})
            elif dst is None:actions.append({'file':rel,'action':'add','before':None,'after':src})
            elif dst != src:conflicts.append({'file':rel,'kind':'unbased_repository_file'})
            else:actions.append({'file':rel,'action':'adopt','before':dst,'after':src})
        elif src != base['source']:
            if dst not in {base['repository'], src}:conflicts.append({'file':rel,'kind':'both_source_and_repository_changed'})
            else:actions.append({'file':rel,'action':('adopt' if dst==src else 'delete' if src is None else 'add' if dst is None else 'modify'),'before':dst,'after':src})
        elif dst != base['repository']:
            conflicts.append({'file':rel,'kind':'repository_changed_requires_reconciliation'})
    for rel in set(state['managed']) - set(policy['managed']):conflicts.append({'file':rel,'kind':'managed_path_removed_from_policy'})
    for rel in policy['protected']:
        source_hashes[rel] = file_hash(source, rel)
        if source_hashes[rel] != state.get('protected', {}).get(rel):protected_changed.append(rel)
    for action in actions:
        if action['action'] != 'delete':proposed[action['file']] = file_bytes(source, action['file'])
        else:proposed[action['file']] = None
    findings = []
    if proposed:
        env={**os.environ,'GIT_OPTIONAL_LOCKS':'0'}
        checked=subprocess.run([str(executable),'-C',str(repo),'check-ignore','--no-index','--stdin','-z'],input=('\0'.join(proposed)+'\0').encode(),capture_output=True,env=env,timeout=30)
        if checked.returncode not in (0,1):raise SyncError('Git ignore validation failed')
        for rel in filter(None,checked.stdout.decode().split('\0')):
            if proposed[rel] is not None:findings.append({'file':rel,'kind':'approved_path_ignored_by_git'})
    names = set(publication_files(repo, executable)) | set(proposed)
    for rel in sorted(names):
        data = proposed[rel] if rel in proposed else file_bytes(repo, rel)
        if data is not None:findings.extend(scan_blob(rel, data))
    missing = []
    for rel in policy['required']:
        if (proposed[rel] if rel in proposed else file_bytes(repo, rel)) is None:missing.append(rel)
    unapproved = sorted(source_candidates(source) - set(policy['managed']) - set(policy['protected']) - set(policy.get('excluded_candidates', [])))
    if git_fingerprint(repo, executable) != before:raise SyncError('Git changed during preview')
    dependency_review=dependencies(repo,proposed)
    findings.extend({'file':'changed Python source','kind':'undeclared_dependency','import':name} for name in dependency_review['undeclaredImports'])
    plan = {'version':1,'sourceRoot':str(source),'repositoryRoot':str(repo),'gitMetadataDigest':before,'repositoryFiles':tree_hashes(repo,executable),'policySha256':file_hash(repo,POLICY),'stateSha256':file_hash(repo,STATE),'sourceFiles':source_hashes,'actions':actions,'conflicts':conflicts,'findings':findings,'missingRequired':missing,'unapprovedCandidates':unapproved,'protectedSourceChanges':protected_changed,'dependencies':dependency_review,'heuristicAuditOnly':True,'testingRequiredBeforeCommit':True}
    return plan

def manifest_data(repo, executable):
    rows=['# OfflineGPT manifest v2: UTF-8 text uses LF; binary assets use exact bytes.\n']
    for rel in publication_files(repo, executable):
        if rel in {MANIFEST,LOCK}:continue
        data=file_bytes(repo,rel)
        if data is not None:rows.append(manifest_hash(rel,data)+'  '+rel+'\n')
    return ''.join(rows).encode()

def manifest_hash(rel, data):
    if PurePosixPath(rel).suffix.lower() in TEXT|{'.svg'} or PurePosixPath(rel).name.startswith('.') or rel == 'requirements-agent-windows-py312.lock':
        try:data.decode('utf-8');data=data.replace(b'\r\n',b'\n')
        except UnicodeError:pass
    return digest(data)

def atomic_write(path, data):
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.offlinegpt-sync-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)

def validate_external(path, repo, source):
    resolved=path.resolve()
    # All operands need the same canonical namespace (relative paths and
    # Windows short/long temp-directory aliases otherwise evade containment).
    if resolved.is_relative_to(repo.resolve()) or resolved.is_relative_to(source.resolve()):raise SyncError('Reports/backups must stay outside both roots')
    if path.exists() and linked(path):raise SyncError('External artifact path is a link')
    return resolved

def recover(repo, journal_path, executable):
    journal=load(journal_path)
    validate_external(journal_path,repo,repo.parent/'unused-source')
    policy=load(repo/POLICY);validate_policy(policy,repo)
    if Path(journal['repositoryRoot']).resolve()!=repo.resolve():raise SyncError('Journal belongs to another repository')
    marker=repo/LOCK
    if marker.exists() and load(marker).get('journal')!=str(journal_path.resolve()):raise SyncError('Another transaction owns the lock')
    if journal['status'] in {'complete','rolled_back'}:
        field='after' if journal['status']=='complete' else 'before'
        if any(file_hash(repo,e['file'])!=e[field] for e in journal['entries']):raise SyncError('Completed journal no longer matches working files')
        if marker.exists():marker.unlink()
        return {'status':journal['status'],'recoveredFiles':0}
    # Preflight all targets before restoring any: preserve post-crash user edits.
    for entry in journal['entries']:
        if entry['file'] not in set(policy['managed'])|{STATE,MANIFEST} or not re.fullmatch(r'backup-\d{4}',entry['backup']):raise SyncError('Invalid recovery entry')
        current=file_hash(repo,entry['file'])
        if current not in {entry['before'],entry['after']}:raise SyncError('Recovery conflict: '+entry['file'])
        backup=journal_path.parent/entry['backup']
        if entry['before'] is not None and (not backup.is_file() or linked(backup) or backup.stat().st_size>MAX_FILE or digest(backup.read_bytes())!=entry['before']):raise SyncError('Invalid recovery backup')
    for entry in reversed(journal['entries']):
        target=safe_path(repo,entry['file'])
        if entry['before'] is None:
            if target.exists():target.unlink()
        else:atomic_write(target,(journal_path.parent/entry['backup']).read_bytes())
    journal['status']='rolled_back';atomic_write(journal_path,canonical(journal))
    marker=repo/LOCK
    if marker.exists() and load(marker).get('journal')==str(journal_path.resolve()):marker.unlink()
    return {'status':'rolled_back','recoveredFiles':len(journal['entries'])}

def apply_plan(repo, source, executable, plan_path, approved_digest, reviews, report_dir):
    plan=load(plan_path)
    if digest(canonical(plan))!=approved_digest:raise SyncError('Approval does not match exact reviewed plan')
    if not all(reviews.values()):raise SyncError('Explicit content/dependency/license review acknowledgements required')
    current=build_plan(repo,source,executable)
    if current!=plan:raise SyncError('Plan is stale; preview again')
    if any(plan[k] for k in ('conflicts','findings','missingRequired')):raise SyncError('Plan has blocking findings')
    actions=plan['actions']
    if not actions:return {'status':'no_changes','gitUntouched':True,'copied':0}
    report_dir=validate_external(report_dir,repo,source);report_dir.mkdir(parents=True,exist_ok=True)
    job=report_dir/('transaction-'+uuid.uuid4().hex);job.mkdir()
    entries=[];payloads={}
    new_state=load(repo/STATE)
    for action in actions:
        rel=action['file'];payloads[rel]=file_bytes(source,rel)
        if (digest(payloads[rel]) if payloads[rel] is not None else None)!=action['after']:raise SyncError('Source changed before copy')
        new_state['managed'][rel]={'source':action['after'],'repository':action['after']}
    payloads[STATE]=json.dumps(new_state,indent=2).encode()+b'\n'
    # Manifest is generated after copied paths become visible to Git ls-files.
    for rel,data in list(payloads.items())+[(MANIFEST,None)]:
        before=file_bytes(repo,rel);backup=f'backup-{len(entries):04d}'
        if before is not None:atomic_write(job/backup,before)
        entries.append({'file':rel,'before':digest(before) if before is not None else None,'after':digest(data) if data is not None else None,'backup':backup})
    journal={'repositoryRoot':str(repo),'status':'prepared','entries':entries,'reviews':reviews,'planDigest':approved_digest}
    journal_path=job/'journal.json';atomic_write(journal_path,canonical(journal))
    marker=repo/LOCK
    descriptor=os.open(marker,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(descriptor,'wb') as stream:stream.write(canonical({'journal':str(journal_path.resolve())}));stream.flush();os.fsync(stream.fileno())
    try:
        for entry in entries:
            rel=entry['file'];target=safe_path(repo,rel)
            if file_hash(repo,rel)!=entry['before']:raise SyncError('Repository file changed during apply')
            data=manifest_data(repo,executable) if rel==MANIFEST else payloads[rel]
            if rel==MANIFEST:
                entry['after']=digest(data);atomic_write(journal_path,canonical(journal))
            journal['status']='applying';atomic_write(journal_path,canonical(journal))
            if data is None:
                if target.exists():target.unlink()
            else:atomic_write(target,data)
        expected=dict(plan['repositoryFiles'])
        expected.update({entry['file']:entry['after'] for entry in entries})
        actual=tree_hashes(repo,executable)
        if {k:v for k,v in expected.items() if v is not None}!={k:v for k,v in actual.items() if v is not None}:raise SyncError('Concurrent working-tree edit detected')
        if git_fingerprint(repo,executable)!=plan['gitMetadataDigest']:raise SyncError('Concurrent Git operation detected')
        journal['status']='complete';atomic_write(journal_path,canonical(journal))
        marker.unlink()
        return {'status':'applied','copied':sum(a['action'] in {'add','modify'} for a in actions),'deletedStagingFiles':sum(a['action']=='delete' for a in actions),'journal':str(journal_path),'gitUntouched':True,'testsRequiredBeforeCommit':True}
    except Exception:
        try:recover(repo,journal_path,executable)
        except Exception:journal['status']='needs_review';atomic_write(journal_path,canonical(journal))
        raise

def precommit(repo, executable):
    if (repo/LOCK).exists():raise SyncError('Unresolved synchronization journal; recover before commit review')
    before=git_fingerprint(repo,executable);findings=[];working=tree_hashes(repo,executable)
    for rel in working:
        data=file_bytes(repo,rel)
        if data is not None:findings.extend(scan_blob(rel,data))
    indexed=git(repo,executable,'ls-files','-z').stdout.decode().split('\0');indexed_rows=[];indexed_manifest=None
    for rel in filter(None,indexed):
        safe_path(repo,rel)
        size=int(git(repo,executable,'cat-file','-s',':'+rel).stdout)
        if size>MAX_FILE:findings.append({'file':rel,'kind':'indexed_source_size_limit'})
        else:
            data=git(repo,executable,'show',':'+rel).stdout
            findings.extend(scan_blob(rel,data))
            if rel==MANIFEST:indexed_manifest=data.replace(b'\r\n',b'\n')
            elif rel!=LOCK:indexed_rows.append(manifest_hash(rel,data)+'  '+rel+'\n')
    missing=[rel for rel in load(repo/POLICY)['required'] if file_bytes(repo,rel) is None]
    manifest_ok=file_bytes(repo,MANIFEST)==manifest_data(repo,executable)
    expected_index=('# OfflineGPT manifest v2: UTF-8 text uses LF; binary assets use exact bytes.\n'+''.join(sorted(indexed_rows,key=lambda row:row.split('  ',1)[1]))).encode()
    indexed_manifest_ok=indexed_manifest==expected_index
    indexed_missing=sorted(set(load(repo/POLICY)['required'])-set(indexed))
    if git_fingerprint(repo,executable)!=before:raise SyncError('Git changed during precommit review')
    return {'findings':findings,'missingRequired':missing,'manifestMatches':manifest_ok,'indexedManifestMatches':indexed_manifest_ok,'indexedMissingRequired':indexed_missing,'changes':git(repo,executable,'status','--short').stdout.decode(),'stagedDiffSummary':git(repo,executable,'diff','--cached','--stat').stdout.decode(),'workingDiffSummary':git(repo,executable,'diff','--stat').stdout.decode(),'testsMustBeReviewed':True,'noCommitPerformed':True}

def refresh_manifest(repo, executable):
    if (repo/LOCK).exists():raise SyncError('Recover interrupted synchronization first')
    before=git_fingerprint(repo,executable);snapshot=tree_hashes(repo,executable);findings=[]
    for rel in snapshot:
        data=file_bytes(repo,rel)
        if data is not None:findings.extend(scan_blob(rel,data))
    missing=[rel for rel in load(repo/POLICY)['required'] if file_bytes(repo,rel) is None]
    if findings or missing:raise SyncError('Manifest refresh blocked by safety/completeness findings')
    data=manifest_data(repo,executable)
    if snapshot!=tree_hashes(repo,executable) or before!=git_fingerprint(repo,executable):raise SyncError('Repository changed during manifest review')
    atomic_write(safe_path(repo,MANIFEST),data)
    return {'status':'manifest_refreshed','manifestSha256':digest(data),'noCommitPerformed':True,'gitUntouched':before==git_fingerprint(repo,executable)}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['preview','apply','precommit','recover','refresh-manifest'])
    parser.add_argument('--source',type=Path)
    parser.add_argument('--repo',type=Path,default=ROOT)
    parser.add_argument('--git',default='git')
    parser.add_argument('--report-dir',type=Path,required=True)
    parser.add_argument('--plan',type=Path)
    parser.add_argument('--approve-plan')
    parser.add_argument('--journal',type=Path)
    parser.add_argument('--reviewed-content',action='store_true')
    parser.add_argument('--reviewed-dependencies',action='store_true')
    parser.add_argument('--reviewed-licenses',action='store_true')
    args=parser.parse_args()
    if linked(args.repo) or args.source and linked(args.source):raise SyncError('Root links are unsupported')
    repo=args.repo.resolve();source=(args.source or repo.parent/'unused-source').resolve()
    external=validate_external(args.report_dir,repo,source);external.mkdir(parents=True,exist_ok=True)
    if args.command=='preview':
        if not args.source:parser.error('--source is required')
        plan=build_plan(repo,source,args.git);identifier=digest(canonical(plan));job=external/('preview-'+uuid.uuid4().hex);job.mkdir()
        atomic_write(job/'plan.json',json.dumps(plan,indent=2).encode())
        summary={'plan':str(job/'plan.json'),'approvalDigest':identifier,'actions':plan['actions'],'conflicts':plan['conflicts'],'findings':plan['findings'],'missingRequired':plan['missingRequired'],'unapprovedCandidates':plan['unapprovedCandidates'],'protectedSourceChanges':plan['protectedSourceChanges'],'dependencies':plan['dependencies']}
        atomic_write(job/'SUMMARY.json',json.dumps(summary,indent=2).encode())
        print(json.dumps(summary,indent=2));return int(any(plan[k] for k in ('conflicts','findings','missingRequired')))
    if args.command=='apply':
        if not args.source or not args.plan or not args.approve_plan:parser.error('apply requires --source, --plan and --approve-plan')
        result=apply_plan(repo,source,args.git,args.plan,args.approve_plan,{'content':args.reviewed_content,'dependencies':args.reviewed_dependencies,'licenses':args.reviewed_licenses},external)
    elif args.command=='recover':
        if not args.journal or args.approve_plan!=digest(canonical(load(args.journal))):parser.error('recover requires --journal and its exact current canonical SHA-256 in --approve-plan')
        validate_external(args.journal,repo,source);result=recover(repo,args.journal,args.git);result['noGitCommandsWritten']=True
    elif args.command=='refresh-manifest':
        if not args.reviewed_content:parser.error('Review manual repository changes and pass --reviewed-content before refreshing the manifest')
        result=refresh_manifest(repo,args.git)
    else:result=precommit(repo,args.git)
    atomic_write(external/('summary-'+uuid.uuid4().hex+'.json'),json.dumps(result,indent=2).encode());print(json.dumps(result,indent=2))
    return int(bool(result.get('findings') or result.get('missingRequired') or result.get('indexedMissingRequired') or result.get('manifestMatches') is False or result.get('indexedManifestMatches') is False))

if __name__=='__main__':
    try:raise SystemExit(main())
    except (SyncError, OSError, ValueError, KeyError) as error:
        print('Synchronization stopped: '+str(error),file=sys.stderr);raise SystemExit(2)


def canonical_contract_schema(contract):
    keys={'version','epoch','root','repositoryConfigSha256','ancestorCommit','branch','remoteUrl','policySha256','productionRoot'}
    if not isinstance(contract,dict) or set(contract)!=keys or type(contract['version']) is not int or contract['version']!=1 or type(contract['epoch']) is not int or contract['epoch']<1:
        raise SyncError('Unsupported canonical path contract')
    return dict(contract)

def validate_canonical_contract(repo, source, contract, executable):
    """Explicit same-repository layout; legacy context() stays disjoint-only.

    This validates an already adopted layout, never grants path adoption. The
    caller also requires the exact contract/epoch in external control state.
    """
    canonical_contract_schema(contract)
    for path in (repo,source):
        if not path.is_absolute():raise SyncError('Canonical paths must be absolute')
        for part in (path,*path.parents):
            if part.exists() and linked(part):raise SyncError('Canonical link/reparse ancestor')
    if repo.resolve()!=source.resolve() or str(repo.resolve())!=contract['root']:
        raise SyncError('Canonical contract requires the exact single repository root')
    if not isinstance(contract['productionRoot'],str):raise SyncError('Explicit canonical production boundary required')
    production=Path(contract['productionRoot'])
    if not production.is_absolute() or str(production.resolve())!=contract['productionRoot']:raise SyncError('Canonical production boundary must be an exact absolute path')
    for part in (production,*production.parents):
        if part.exists() and linked(part):raise SyncError('Canonical production boundary is linked')
    if repo.resolve().is_relative_to(production) or production.is_relative_to(repo.resolve()):raise SyncError('Canonical repository overlaps production boundary')
    git_fingerprint(repo,executable)
    if digest((repo/'.git/config').read_bytes())!=contract['repositoryConfigSha256'] or file_hash(repo,POLICY)!=contract['policySha256']:
        raise SyncError('Canonical repository/policy identity changed')
    if not re.fullmatch('[0-9a-f]{40,64}',str(contract['ancestorCommit'])):raise SyncError('Invalid canonical adoption commit')
    if subprocess.run([str(executable),'-C',str(repo),'merge-base','--is-ancestor',contract['ancestorCommit'],'HEAD'],capture_output=True,timeout=30).returncode:
        raise SyncError('Canonical adoption history diverged')
    if git(repo,executable,'branch','--show-current').stdout.decode().strip()!=contract['branch'] or git(repo,executable,'remote','get-url','origin').stdout.decode().strip()!=contract['remoteUrl']:
        raise SyncError('Canonical branch/remote identity changed')
    return dict(contract)
