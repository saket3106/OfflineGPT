"""Read-only pre-upload check for a clean source export (standard library only).

Scan the clean staging folder before adding dependency/build/data folders. This
is a bounded heuristic, not proof that all proprietary or private text is safe.
Findings report locations and categories, never credential values.
"""
import ast
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {'.py', '.jsx', '.js', '.css', '.json', '.jsonl', '.yaml', '.yml', '.md', '.txt', '.html', '.spec'}
FORBIDDEN_SUFFIXES = {'.sqlite', '.sqlite3', '.db', '.gguf', '.onnx', '.safetensors', '.pth', '.pt', '.bin', '.exe', '.dll', '.pyd', '.zip', '.7z', '.pem', '.key', '.pfx', '.p12', '.wav', '.mp3', '.mp4'}
FORBIDDEN_DIRS = {'node_modules', '.venv', 'venv', 'generated', 'thumbnails', 'backups', 'logs', 'profiles', 'attachments', 'models', 'build', 'dist', 'ui', '__pycache__'}
PATTERNS = {
    'private_key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'github_token': re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b'),
    'service_token': re.compile(r'\b(?:hf_[A-Za-z0-9]{20,}|sk-(?:proj-)?[A-Za-z0-9_-]{24,}|AKIA[A-Z0-9]{16})\b'),
    'personal_windows_path': re.compile(r'(?i)[A-Z]:[\\/]+Users[\\/]+(?!Public\b|Default\b|<)[A-Za-z0-9_. -]+[\\/]'),
    'url_with_credentials': re.compile(r'https?://[^\s/:]+:[^\s/@]+@'),
}

def audit(root):
    findings = []
    files = []
    for p in sorted(root.rglob('*')):
        rel = p.relative_to(root)
        if '.git' in rel.parts:
            continue
        if p.is_symlink():
            findings.append({'file':rel.as_posix(),'kind':'link_requires_review'})
            continue
        if not p.is_file():
            continue
        size = p.stat().st_size
        files.append((rel.as_posix(),size))
        if (any(part in FORBIDDEN_DIRS or 'rollback' in part.lower() for part in rel.parts[:-1])
                or p.suffix.lower() in FORBIDDEN_SUFFIXES
                or p.name in {'.env','.npmrc','.pypirc','.netrc','_netrc','.git-credentials','id_rsa','id_ed25519'}
                or re.search(r'\.(?:sqlite3?|db)(?:-|$)',p.name,re.I)
                or p.name.startswith('offline_gpt_config') and p.name!='offline_gpt_config.example.json'
                or p.name.startswith('.env.') and p.name!='.env.example'):
            findings.append({'file':rel.as_posix(),'kind':'excluded_asset_or_private_state'})
        if size >= 50*1024*1024:
            findings.append({'file':rel.as_posix(),'kind':'ordinary_git_size_warning','bytes':size})
        if p.suffix in TEXT_SUFFIXES or p.name.startswith('.'):
            if size>2*1024*1024:
                findings.append({'file':rel.as_posix(),'kind':'text_too_large_for_this_audit'})
                continue
            try:
                text=p.read_text(encoding='utf-8')
            except UnicodeError:
                findings.append({'file':rel.as_posix(),'kind':'non_utf8_text'})
                continue
            for kind,pattern in PATTERNS.items():
                for match in pattern.finditer(text):
                    findings.append({'file':rel.as_posix(),'kind':kind,'line':text.count('\n',0,match.start())+1})
            if p.suffix=='.py':
                try:ast.parse(text,filename=rel.as_posix())
                except SyntaxError as error:findings.append({'file':rel.as_posix(),'kind':'python_syntax','line':error.lineno})
    return {'files':len(files),'bytes':sum(s for _,s in files),'largestFile':max(files,key=lambda x:x[1],default=None),'findings':findings,'heuristicOnly':True}

if __name__=='__main__':
    result=audit(ROOT)
    print(json.dumps(result,indent=2))
    sys.exit(bool(result['findings']))
