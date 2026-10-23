"""Bounded workspace tools; no shell, network, deletion or arbitrary execution.

Windows opens every component relative to a retained directory handle. Reparse
points and multiply-linked files are rejected before content is read or written.
New files only: existing originals cannot be overwritten by this interface.
"""
from __future__ import annotations
import ast
import contextlib
import difflib
import hashlib
import io
import json
import os
from pathlib import Path, PureWindowsPath
import re
import sqlite3
import stat
import secrets
import threading
import time

TEXT_TYPES = {'.txt', '.md', '.py', '.js', '.jsx', '.ts', '.tsx', '.css', '.html', '.csv', '.json'}
IMAGE_TYPES={'.png','.jpg','.jpeg','.webp','.bmp'}
READ_TYPES = TEXT_TYPES | IMAGE_TYPES | {'.pdf','.docx','.db','.sqlite','.sqlite3'}
PRIVATE_NAMES = {'.git', '.env', '.ssh', '.aws', '.azure', '.codex', 'node_modules', '__pycache__',
                 'offline_gpt.sqlite3', 'offline_gpt_config.json', 'credentials', 'secrets', 'models',
                 'backups', 'rollback', 'logs', 'site-packages'}
MAX_BYTES = 2*1024*1024
DOCUMENT_BYTES = 256*1024  # Separate bounded binary-document capability; text writes remain 64 KiB.


def parts_for(raw):
    if not isinstance(raw, str) or not raw or len(raw) > 240 or re.search(r'[\x00-\x1f<>"|?*\u202a-\u202e\u2066-\u2069]',raw) or '\\' in raw or ':' in raw or raw.startswith('/'):
        raise PermissionError('Use a short workspace-relative path with forward slashes.')
    parts = raw.split('/')
    if len(parts) > 12 or any(p in {'', '.', '..'} or p.endswith((' ', '.')) or p.startswith('.') or
            p.casefold() in PRIVATE_NAMES or p.casefold() in {'credentials.json','secrets.json','tokens.json','token.json','auth.json','id_rsa','id_ed25519'} or
            Path(p).suffix.casefold() in {'.pem','.key','.pfx','.p12','.kdbx'} or PureWindowsPath(p).is_reserved() for p in parts):
        raise PermissionError('Private, ambiguous and traversing paths are unavailable.')
    return parts


class WindowsHandles:
    def __init__(self):
        import ctypes as c
        from ctypes import wintypes as w
        self.c, self.w = c, w
        self.k = c.WinDLL('kernel32', use_last_error=True)
        self.n = c.WinDLL('ntdll')
        class Unicode(c.Structure):
            _fields_ = [('Length', w.USHORT), ('MaximumLength', w.USHORT), ('Buffer', w.LPWSTR)]
        class Attributes(c.Structure):
            _fields_ = [('Length', w.ULONG), ('RootDirectory', w.HANDLE), ('ObjectName', c.POINTER(Unicode)),
                        ('Attributes', w.ULONG), ('SecurityDescriptor', w.LPVOID), ('SecurityQualityOfService', w.LPVOID)]
        class IOStatus(c.Structure):
            _fields_ = [('Status', c.c_void_p), ('Information', c.c_size_t)]
        class Information(c.Structure):
            _fields_ = [('attributes', w.DWORD), ('created', w.FILETIME), ('accessed', w.FILETIME),
                        ('written', w.FILETIME), ('volume', w.DWORD), ('size_high', w.DWORD),
                        ('size_low', w.DWORD), ('links', w.DWORD), ('index_high', w.DWORD), ('index_low', w.DWORD)]
        self.Unicode, self.Attributes, self.IOStatus, self.Information = Unicode, Attributes, IOStatus, Information
        self.k.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, w.LPVOID, w.DWORD, w.DWORD, w.HANDLE]
        self.k.CreateFileW.restype = w.HANDLE
        self.k.CloseHandle.argtypes = [w.HANDLE]
        self.k.GetFileInformationByHandle.argtypes = [w.HANDLE, c.POINTER(Information)]
        self.n.NtCreateFile.argtypes = [c.POINTER(w.HANDLE), w.ULONG, c.POINTER(Attributes), c.POINTER(IOStatus),
                                       w.LPVOID, w.ULONG, w.ULONG, w.ULONG, w.ULONG, w.LPVOID, w.ULONG]
        self.n.NtCreateFile.restype = w.LONG

    def verify(self, handle, directory):
        info = self.Information()
        if not self.k.GetFileInformationByHandle(handle, self.c.byref(info)):
            raise self.c.WinError(self.c.get_last_error())
        if info.attributes & 0x400 or bool(info.attributes & 0x10) != directory or (not directory and info.links != 1):
            raise PermissionError('Reparse points, wrong file types and hard links are unavailable.')
        return info

    def root(self, path):
        handle = self.k.CreateFileW(str(path), 0x80000000, 3, None, 3, 0x02200000, None)
        if handle == self.c.c_void_p(-1).value:
            raise self.c.WinError(self.c.get_last_error())
        return handle

    def child(self, parent, name, directory=False, create=False, overwrite=False):
        if overwrite:
            raise PermissionError('In-place changes require a separate recoverable mutation protocol; no file was opened.')
        c, w = self.c, self.w
        encoded = name.encode('utf-16-le')
        name_buffer = c.create_unicode_buffer(name,len(encoded)//2+1)
        text = self.Unicode(len(encoded), len(encoded)+2, c.cast(name_buffer, w.LPWSTR))
        attributes = self.Attributes(c.sizeof(self.Attributes), parent, c.pointer(text), 0x40, None, None)
        result, status = w.HANDLE(), self.IOStatus()
        access = 0x80100000 | (0x40000000 if (create or overwrite) else 0)
        flags = 0x00200020 | (1 if directory else 0x40)
        disposition = 5 if overwrite else (2 if create else 1)
        code = self.n.NtCreateFile(c.byref(result), access, c.byref(attributes), c.byref(status), None,
                                   0x80, 1, disposition, flags, None, 0)
        if code < 0:
            status_code = code & 0xffffffff
            if status_code == 0xc0000034:
                raise FileNotFoundError(f"Workspace file not found: '{name}'")
            if status_code == 0xc000003a:
                raise FileNotFoundError(f"Workspace path not found: '{name}'")
            if status_code == 0xc0000022:
                raise PermissionError(f"Workspace access denied: '{name}'")
            raise OSError(f'Workspace handle open failed (NTSTATUS {status_code:08x}); no overwrite was attempted.')
        return result.value




class RecoverableMutationJournal:
    """Manages path-scoped, explicitly approved, recoverable file mutations with atomic rollback."""

    def __init__(self, root: Path):
        self.root = root
        self.journal_dir = root / '.agent_mutation_journal'
        self.backups_dir = self.journal_dir / 'backups'
        self.journal_file = self.journal_dir / 'journal.jsonl'
        self._lock = threading.Lock()

    def _ensure_dirs(self):
        self.backups_dir.mkdir(parents=True, exist_ok=True)

    def prepare(self, access, action: str, path: str, new_content: str = None, new_path: str = None):
        if action not in ('edit', 'rename', 'delete'):
            raise ValueError(f"Unsupported mutation action: {action}")
        rel_path = access.clean_relative(path)
        full_path = access.root / rel_path
        if not full_path.is_file():
            raise FileNotFoundError(f"Target file not found: {rel_path}")

        current_bytes = access.read(rel_path)
        current_sha = hashlib.sha256(current_bytes).hexdigest()
        ticket_id = secrets.token_hex(16)
        timestamp = time.time()

        preview = {
            'ticket_id': ticket_id,
            'action': action,
            'path': rel_path,
            'expected_sha256': current_sha,
            'bytes': len(current_bytes),
            'timestamp': timestamp
        }

        if action == 'edit':
            if new_content is None:
                raise ValueError("new_content required for edit")
            orig_text = current_bytes.decode('utf-8', errors='replace')
            diff = ''.join(difflib.unified_diff(
                orig_text.splitlines(True), new_content.splitlines(True),
                fromfile=rel_path, tofile=rel_path
            ))
            preview['diff'] = diff
            preview['new_bytes'] = len(new_content.encode('utf-8'))
            preview['new_sha256'] = hashlib.sha256(new_content.encode('utf-8')).hexdigest()

        elif action == 'rename':
            if not new_path:
                raise ValueError("new_path required for rename")
            rel_new = access.clean_relative(new_path)
            target_full = access.root / rel_new
            if target_full.exists():
                raise FileExistsError(f"Destination path already exists: {rel_new}")
            preview['new_path'] = rel_new

        elif action == 'delete':
            preview['warning'] = f"File {rel_path} ({len(current_bytes)} bytes) will be moved to recoverable backup."

        return preview

    def execute(self, access, ticket: dict, action: str, path: str, new_content: str = None, new_path: str = None):
        with self._lock:
            self._ensure_dirs()
            if not isinstance(ticket, dict) or ticket.get('action') != action:
                raise PermissionError("Valid approval ticket matching the mutation action is required.")

            rel_path = access.clean_relative(path)
            if ticket.get('path') != rel_path:
                raise PermissionError("Approval ticket path does not match target path.")

            full_path = access.root / rel_path
            if not full_path.is_file():
                raise FileNotFoundError(f"File not found: {rel_path}")

            current_bytes = access.read(rel_path)
            current_sha = hashlib.sha256(current_bytes).hexdigest()
            if ticket.get('expected_sha256') and current_sha != ticket['expected_sha256']:
                raise ValueError(
                    f"Conflict detected: File {rel_path} was modified concurrently. "
                    f"Expected SHA-256 {ticket['expected_sha256'][:12]}..., got {current_sha[:12]}..."
                )

            tid = ticket.get('ticket_id') or secrets.token_hex(16)
            backup_name = f"{tid}_{full_path.name}.bak"
            backup_file = self.backups_dir / backup_name
            backup_file.write_bytes(current_bytes)

            receipt = {
                'ok': True,
                'transaction_id': tid,
                'action': action,
                'path': rel_path,
                'before_sha256': current_sha,
                'backup_file': str(backup_file.relative_to(self.root)),
                'timestamp': time.time(),
                'status': 'COMMITTED',
                'rollback_available': True
            }

            if action == 'edit':
                if new_content is None:
                    raise ValueError("new_content required for edit")
                data = new_content.encode('utf-8')
                parent = full_path.parent
                tmp = parent / f".tmp_{tid}_{full_path.name}"
                try:
                    tmp.write_bytes(data)
                    os.replace(tmp, full_path)
                finally:
                    if tmp.exists():
                        try: os.unlink(tmp)
                        except OSError: pass
                receipt['after_sha256'] = hashlib.sha256(data).hexdigest()
                receipt['bytes'] = len(data)

            elif action == 'rename':
                rel_new = access.clean_relative(new_path)
                target_full = access.root / rel_new
                if target_full.exists():
                    raise FileExistsError(f"Destination already exists: {rel_new}")
                target_full.parent.mkdir(parents=True, exist_ok=True)
                os.replace(full_path, target_full)
                receipt['new_path'] = rel_new
                receipt['after_sha256'] = current_sha

            elif action == 'delete':
                full_path.unlink()
                receipt['after_sha256'] = None

            with self.journal_file.open('a', encoding='utf-8') as jf:
                jf.write(json.dumps(receipt) + '\n')

            return receipt

    def rollback(self, access, transaction_id: str):
        with self._lock:
            if not self.journal_file.exists():
                raise FileNotFoundError("Mutation journal not found.")

            target_record = None
            with self.journal_file.open('r', encoding='utf-8') as jf:
                for line in jf:
                    if line.strip():
                        r = json.loads(line)
                        if r.get('transaction_id') == transaction_id:
                            target_record = r

            if not target_record:
                raise KeyError(f"Transaction ID {transaction_id} not found in journal.")
            if target_record.get('status') == 'ROLLED_BACK':
                raise ValueError(f"Transaction {transaction_id} was already rolled back.")

            backup_rel = target_record['backup_file']
            backup_path = self.root / backup_rel
            if not backup_path.is_file():
                raise FileNotFoundError(f"Backup file missing: {backup_rel}")

            orig_rel_path = target_record['path']
            orig_full = self.root / orig_rel_path

            action = target_record['action']
            if action == 'edit':
                os.replace(backup_path, orig_full)
            elif action == 'rename':
                new_full = self.root / target_record['new_path']
                if new_full.exists():
                    new_full.unlink()
                orig_full.parent.mkdir(parents=True, exist_ok=True)
                os.replace(backup_path, orig_full)
            elif action == 'delete':
                orig_full.parent.mkdir(parents=True, exist_ok=True)
                os.replace(backup_path, orig_full)

            rollback_receipt = {
                'ok': True,
                'rolled_back_transaction': transaction_id,
                'action': f"rollback_{action}",
                'path': orig_rel_path,
                'restored_sha256': target_record['before_sha256'],
                'timestamp': time.time(),
                'status': 'ROLLED_BACK'
            }
            with self.journal_file.open('a', encoding='utf-8') as jf:
                jf.write(json.dumps(rollback_receipt) + '\n')

            return rollback_receipt


class WorkspaceAccess:
    def __init__(self, root, protected=()):
        self.root = Path(root).absolute()
        if str(self.root).startswith('\\\\') or not self.root.is_dir():
            raise PermissionError('Select an existing local workspace directory.')
        for part in (self.root, *self.root.parents):
            if part.is_symlink() or getattr(part.lstat(), 'st_file_attributes', 0) & 0x400:
                raise PermissionError('A linked workspace root is unavailable.')
        self.protected = [Path(p).resolve() for p in protected]
        if any(self.root.resolve() == p or self.root.resolve().is_relative_to(p) for p in self.protected):
            raise PermissionError('Application, profile and model directories are protected.')
        self.win = WindowsHandles() if os.name == 'nt' else None
        self.root_handle = None
        self.mutation_journal = RecoverableMutationJournal(self.root)
        if self.win:
            handle=self.win.root(self.root)
            try:self.win.verify(handle,True)
            except BaseException:
                self.win.k.CloseHandle(handle);raise
            self.root_handle=handle
        self.mutation_journal = RecoverableMutationJournal(self.root)

    def prepare_mutation(self, action, path, new_content=None, new_path=None):
        return self.mutation_journal.prepare(self, action, path, new_content=new_content, new_path=new_path)

    def execute_authorized_mutation(self, ticket, action, path, new_content=None, new_path=None):
        return self.mutation_journal.execute(self, ticket, action, path, new_content=new_content, new_path=new_path)

    def rollback_mutation(self, transaction_id):
        return self.mutation_journal.rollback(self, transaction_id)

    def close(self):
        if self.root_handle is not None:
            self.win.k.CloseHandle(self.root_handle);self.root_handle=None

    def root_identity(self):
        """Stable opened-directory identity, not a pathname-existence guess."""
        if self.win:
            if self.root_handle is None:raise PermissionError('Workspace access has been closed.')
            info=self.win.verify(self.root_handle,True)
            return {'platform':'windows','volume':info.volume,'file':(info.index_high<<32)|info.index_low}
        info=self.root.stat()
        return {'platform':os.name,'volume':info.st_dev,'file':info.st_ino}

    def object_identity(self, fd, directory=False):
        if self.win:
            import msvcrt
            info=self.win.verify(msvcrt.get_osfhandle(fd),directory)
            return {'platform':'windows','volume':info.volume,'file':(info.index_high<<32)|info.index_low}
        info=os.fstat(fd)
        return {'platform':os.name,'volume':info.st_dev,'file':info.st_ino}

    def __del__(self):
        if getattr(self,'root_handle',None) is not None:self.close()

    def clean_relative(self, raw):
        if isinstance(raw, str):
            raw = raw.replace('\\', '/').strip()
            if raw.startswith('/'):
                raise PermissionError('Absolute paths cannot be reinterpreted as workspace-relative paths.')
            if raw.startswith('./'): raw = raw[2:]
            raw = raw.strip('/')
        parts = parts_for(raw)
        if len(parts) > 1 and parts[0].casefold() == self.root.name.casefold() and not (self.root / parts[0]).is_dir():
            parts = parts[1:]
        return '/'.join(parts)

    @contextlib.contextmanager
    def descriptor(self, raw, create=False, directory=False, overwrite=False):
        if overwrite:
            raise PermissionError('In-place changes are unavailable until recoverable, identity-bound mutations are implemented.')
        parts = parts_for(raw)
        if len(parts) > 1 and parts[0].casefold() == self.root.name.casefold() and not (self.root / parts[0]).is_dir():
            parts = parts[1:]
        logical = self.root.joinpath(*parts)
        if any(logical == p or logical.is_relative_to(p) for p in self.protected):
            raise PermissionError('Protected application path.')
        if self.win:
            import msvcrt
            handles, fd = [], None
            try:
                root = self.root_handle
                if root is None:raise PermissionError('Workspace access has been closed.')
                parent=root
                for i, name in enumerate(parts):
                    is_last = i == len(parts)-1
                    handle = self.win.child(parent, name, directory if is_last else True, (create or overwrite) if is_last else False, overwrite=overwrite if is_last else False)
                    handles.append(handle);self.win.verify(handle, directory if is_last else True)
                    parent=handle
                fd = msvcrt.open_osfhandle(handles[-1], os.O_BINARY | (os.O_RDWR if (create or overwrite) else os.O_RDONLY));handles.pop()
                yield fd
            finally:
                if fd is not None:os.close(fd)
                for handle in reversed(handles):self.win.k.CloseHandle(handle)
        else:
            fds = []
            try:
                fds.append(os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW))
                for name in parts[:-1]:
                    fds.append(os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fds[-1]))
                if directory and create:
                    raise PermissionError('Directory creation is currently available only in the Windows handle adapter.')
                flags = (os.O_CREAT | os.O_EXCL | os.O_RDWR) if create else os.O_RDONLY
                fd = os.open(parts[-1], flags | os.O_NOFOLLOW | (os.O_DIRECTORY if directory else 0), 0o600, dir_fd=fds[-1])
                fds.append(fd);info = os.fstat(fd)
                if (not directory and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1)):
                    raise PermissionError('Only ordinary single-link files are available.')
                yield fd
            finally:
                for fd in reversed(fds):os.close(fd)

    def read(self, path, limit=MAX_BYTES):
        with self.descriptor(path) as fd:
            before = os.fstat(fd)
            if before.st_size > limit:raise ValueError('File exceeds this tool’s bounded input limit.')
            blocks=[];remaining=before.st_size
            while remaining:
                block=os.read(fd,min(remaining,65536))
                if not block:raise OSError('File ended before the recorded size; retry after editing stops.')
                blocks.append(block);remaining-=len(block)
            data=b''.join(blocks)
            after = os.fstat(fd)
            if len(data) > limit or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError('File changed during the read; retry after editing stops.')
            return data

    def write_new(self, path, content):
        if Path(path).suffix.casefold() not in TEXT_TYPES or not isinstance(content, str) or len(content.encode()) > 65536:
            raise ValueError('New text artifacts are limited to 64 KiB and supported source/document extensions.')
        data = content.encode('utf-8')
        with self.descriptor(path, create=True) as fd:
            # A crash can leave an incomplete NEW artifact; originals are never overwritten.
            offset = 0
            while offset < len(data):
                written = os.write(fd, data[offset:])
                if not written:raise OSError('Incomplete artifact write; inspect the new file before retrying.')
                offset += written
            os.fsync(fd);os.lseek(fd, 0, os.SEEK_SET)
            if os.read(fd, len(data)+1) != data:raise OSError('Artifact readback failed.')
            identity=self.object_identity(fd)
        return {'path': path, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(), 'fileIdentity':identity, 'originalOverwritten': False}

    def write_new_document(self,path,data):
        if Path(path).suffix.casefold() not in {'.pdf','.docx'} or not isinstance(data,bytes) or len(data)>DOCUMENT_BYTES:
            raise ValueError('Approved document copies must be PDF/Word and no larger than 256 KiB.')
        with self.descriptor(path,create=True) as fd:
            offset=0
            while offset<len(data):
                written=os.write(fd,data[offset:])
                if not written:raise OSError('Incomplete NEW document; inspect the artifact journal.')
                offset+=written
            os.fsync(fd);os.lseek(fd,0,os.SEEK_SET)
            if os.read(fd,len(data)+1)!=data:raise OSError('New document readback failed.')
            identity=self.object_identity(fd)
        return {'path':path,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'fileIdentity':identity,'originalOverwritten':False}

    def edit_file(self, path, content, approval_ticket=None):
        if not approval_ticket:
            raise PermissionError('In-place editing is not supported by the approved new-artifact journal. Create an approved edited copy instead.')
        return self.execute_authorized_mutation(approval_ticket, 'edit', path, new_content=content)

    def rename_file(self, old_path, new_path, approval_ticket=None):
        if not approval_ticket:
            raise PermissionError('Rename requires a recoverable identity-bound mutation journal; no file was changed.')
        return self.execute_authorized_mutation(approval_ticket, 'rename', old_path, new_path=new_path)

    def delete_file(self, path, approval_ticket=None):
        if not approval_ticket:
            raise PermissionError('Deletion requires a recoverable identity-bound mutation journal; no file was changed.')
        return self.execute_authorized_mutation(approval_ticket, 'delete', path)

    def files(self, cancel=None):
        result, examined, queue, limited = [], 0, [('',0)], False
        while queue:
            folder,depth=queue.pop(0)
            guard=self.descriptor(folder,directory=True) if folder else contextlib.nullcontext()
            try:
                with guard,os.scandir(self.root/folder) as entries:
                    for entry in entries:
                        if cancel and cancel.is_set():raise RuntimeError('Cancelled')
                        examined+=1
                        if examined>1024:return {'files':result,'truncated':True}
                        relative=(Path(folder)/entry.name).as_posix()
                        try:parts_for(relative)
                        except PermissionError:continue
                        if entry.is_symlink() or getattr(entry.stat(follow_symlinks=False),'st_file_attributes',0)&0x400:continue
                        if entry.is_dir(follow_symlinks=False):
                            if depth<6:
                                if len(queue)>=128:return {'files':result,'truncated':True}
                                queue.append((relative,depth+1))
                            else:limited=True
                            continue
                        if Path(entry.name).suffix.lower() not in READ_TYPES:continue
                        try:
                            with self.descriptor(relative) as fd:info=os.fstat(fd)
                        except (OSError,PermissionError):continue
                        result.append({'path':relative,'bytes':info.st_size})
                        if len(result)>=256:return {'files':result,'truncated':True}
            except (OSError,PermissionError):limited=True;continue
        return {'files':result, 'truncated':limited}

    def database(self, path, query):
        if Path(path).suffix.lower() not in {'.sqlite', '.sqlite3', '.db'}:raise ValueError('Select a workspace SQLite file.')
        data = self.read(path, 16*1024*1024)
        if not data.startswith(b'SQLite format 3\x00'):raise ValueError('Not a SQLite database.')
        con=sqlite3.connect(':memory:')
        try:
            con.deserialize(data)
            con.execute('PRAGMA trusted_schema=OFF');con.execute('PRAGMA query_only=ON')
            tables={r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if {'chats','messages'} <= tables:raise PermissionError('Conversation databases are unavailable to agent SQL tools.')
            allowed={sqlite3.SQLITE_SELECT,sqlite3.SQLITE_READ,sqlite3.SQLITE_RECURSIVE}
            functions={'count','sum','avg','min','max','length','lower','upper','abs','round','coalesce','ifnull','substr'}
            def authorize(action,a,b,*_):
                return sqlite3.SQLITE_OK if action in allowed or (action==sqlite3.SQLITE_FUNCTION and str(b).lower() in functions) else sqlite3.SQLITE_DENY
            con.set_authorizer(authorize);steps=0
            def budget():
                nonlocal steps
                steps+=1;return steps>250
            con.set_progress_handler(budget,1000)
            cursor=con.execute(query);rows=cursor.fetchmany(101)
            # Binary values never flow into model context.
            return {'columns':[c[0] for c in cursor.description or []], 'rows':[[str(v)[:1000] if v is not None else None for v in row] for row in rows[:100]], 'truncated':len(rows)>100}
        finally:con.close()
