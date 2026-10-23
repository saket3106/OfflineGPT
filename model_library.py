"""Local model references in the existing database; never copy/delete weights.

Metadata is untrusted and cannot grant native capabilities. Inspection is bounded
and import/selection happen in the existing asynchronous workspace worker.
"""
import json,time,uuid,os,stat,contextlib
from pathlib import Path
from model_inspection import inspect_gguf,ordinary_file,identity
from model_runtime import ModelInfo,Family,estimate_memory,gguf_quantization_label,model_display_name

SCHEMA='CREATE TABLE local_model_references(id TEXT PRIMARY KEY,path TEXT COLLATE NOCASE UNIQUE NOT NULL,observation TEXT NOT NULL,details TEXT NOT NULL,created REAL NOT NULL)'


class ModelLibrary:
    def __init__(self,workspace):self.ws=workspace

    def _exists(self):
        row=self.ws.con.execute("SELECT type,sql FROM sqlite_master WHERE name='local_model_references'").fetchone()
        if not row:return False
        if row[0]!='table' or ' '.join(str(row[1]).split()).casefold()!=' '.join(SCHEMA.split()).casefold():
            raise RuntimeError('Model reference schema requires recovery review; no migration or write was attempted.')
        if self.ws.con.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND tbl_name='local_model_references'").fetchone():
            raise RuntimeError('Unreviewed model reference trigger; registry access stopped without changes.')
        return True

    def _initialize(self):
        with self.ws.lock:
            if self._exists():return
            if self.ws.con.in_transaction:raise RuntimeError('Finish the database transaction before importing models.')
            from workspace_services import online_backup
            online_backup(self.ws.con,self.ws.directory/'backups','pre-model-library')
            with self.ws.con:self.ws.con.execute(SCHEMA)

    def list(self):
        with self.ws.lock:
            if not self._exists():return []
            rows=self.ws.con.execute('SELECT id,path,observation,details,created FROM local_model_references ORDER BY created DESC').fetchall()
        result=[]
        for mid,path,observation,details,created in rows:
            row=json.loads(details);row.update(id=mid,path=path,created=created)
            declared=row.get('declared',{})
            row['name']=model_display_name(declared.get('name') or row.get('name','Local model'),
                declared.get('quantizationFileType',row.get('quantizationFileType')))
            try:_,st=ordinary_file(Path(path));row['fileState']='present' if list(identity(st))==json.loads(observation) else 'changed'
            except (OSError,ValueError):row['fileState']='missing-or-unsafe'
            result.append(row)
        return result

    def import_file(self,path,cancel=None):
        selected=Path(path)
        if selected.suffix.lower()!='.gguf':raise ValueError('Import an existing local GGUF file.')
        inspection=inspect_gguf(selected,cancel_event=cancel);model=ModelInfo.from_inspection(inspection)
        details=model.capability_report()
        quant=model.metadata.get('general.file_type')
        details['quantizationLabel']=gguf_quantization_label(quant)
        split=model.metadata.get('split.count',1)
        text_candidate=model.family is not Family.OTHER and type(split) is int and split==1 and bool(model.template_sha256)
        details.update(selectable=text_candidate,selectionWarning=('Native compatibility is unverified. Selection is experimental and requires confirmation.' if text_candidate else 'This reference cannot be selected for chat: unknown/specialist architecture, missing template or incomplete split model.'),
                       templateSha256=model.template_sha256,headerMetadataSha256=model.header_sha256,
                       quantizationFileType=model.metadata.get('general.file_type'))
        if cancel and cancel.is_set():raise RuntimeError('Model import cancelled before database write.')
        self._initialize()
        with self.ws.lock,self.ws.con:
            if cancel and cancel.is_set():raise RuntimeError('Model import cancelled before commit.')
            path=str(selected);existing=self.ws.con.execute('SELECT id FROM local_model_references WHERE path=?',(path,)).fetchone()
            mid=existing[0] if existing else uuid.uuid4().hex
            self.ws.con.execute('INSERT INTO local_model_references VALUES(?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET observation=excluded.observation,details=excluded.details',
                                (mid,path,json.dumps(model.observation),json.dumps(details),time.time()))
        return {'id':mid,'details':details,'weightsCopied':False,'committed':True}

    def selected(self,mid,cancel=None):
        with self.ws.lock:
            if not self._exists():raise ValueError('No imported models.')
            row=self.ws.con.execute('SELECT path,observation,details FROM local_model_references WHERE id=?',(mid,)).fetchone()
        if not row:raise ValueError('Model reference no longer exists.')
        path,observation,details=row;details=json.loads(details)
        if not details.get('selectable'):raise ValueError(details['selectionWarning'])
        current=inspect_gguf(Path(path),cancel_event=cancel)
        if list(current['fileObservation'])!=json.loads(observation) or current['headerMetadataSha256']!=details['headerMetadataSha256']:
            raise ValueError('The model changed. Import and review it again; nothing was loaded.')
        return path,ModelInfo.from_inspection(current)

    def remove_reference(self,mid,active_path='',cancel=None):
        with self.ws.lock,self.ws.con:
            if cancel and cancel.is_set():raise RuntimeError('Reference removal cancelled before commit.')
            if not self._exists():raise ValueError('No imported models.')
            row=self.ws.con.execute('SELECT path FROM local_model_references WHERE id=?',(mid,)).fetchone()
            if not row:raise ValueError('Model reference no longer exists.')
            if active_path and Path(active_path)==Path(row[0]):raise ValueError('Select another model before removing the active/default reference.')
            self.ws.con.execute('DELETE FROM local_model_references WHERE id=?',(mid,))
        return {'removedReference':True,'weightFilesDeleted':False,'committed':True}

    def action(self,args,engine,cancel,progress,*,save_settings=None):
        op=args.get('operation')
        if op=='discover':return discover_models(args.get('directory',''),cancel)
        if op=='list':return {'models':self.list(),'status':engine.chat_status()}
        if op=='import':return self.import_file(args.get('path',''),cancel)
        if op=='remove':return self.remove_reference(args.get('id',''),engine.cfg.get('gpt_oss_gguf',''),cancel)
        if op=='select':
            if args.get('reviewed') is not True:raise ValueError('Review and confirm experimental model selection first.')
            path,model=self.selected(args.get('id',''),cancel)
            if args.get('reviewedHeader')!=model.header_sha256:raise ValueError('Model review identity changed; review again.')
            if not callable(save_settings):raise RuntimeError('Settings persistence unavailable.')
            if cancel and cancel.is_set():raise RuntimeError('Selection cancelled before settings commit.')
            # Existing owner serializes this action with model operations.
            save_settings({'chat_backend':'bundled','gpt_oss_gguf':path,'chat_model':model.display_name})
            return {'selected':True,'modelName':model.display_name,'nativeVerified':False,'committed':True}
        if op=='load':
            name=engine.text_runtime.load(progress=progress,cancel_event=cancel)
            return {'loaded':True,'modelName':name,'nativeCompatibilityAccepted':False}
        if op=='unload':engine.text_runtime.unload();return {'unloaded':True}
        if op=='estimate':
            path,model=self.selected(args.get('id',''),cancel)
            from integrated_runtime import physical_memory_status,available_cuda_memory
            memory=physical_memory_status()
            return estimate_memory(model,int(args.get('context',2048)),available_ram=memory['available'],total_ram=memory['total'],available_vram=available_cuda_memory() or 0,profile=args.get('profile','balanced'))
        raise ValueError('Unsupported model library operation.')


def discover_models(directory,cancel=None):
    """Bounded filename discovery in an explicit root; no import/load approval."""
    root=Path(directory)
    if not root.is_absolute() or '..' in root.parts or str(root).startswith(('\\\\','//')):raise ValueError('Choose an absolute local model directory.')
    for part in (root,*root.parents):
        st=part.lstat()
        if stat.S_ISLNK(st.st_mode) or getattr(st,'st_file_attributes',0)&0x400:raise ValueError('Linked/reparse model directories require review.')
    if not root.is_dir():raise ValueError('Model directory is missing.')
    pending=[(root,0)];paths=[];examined=0;started=time.monotonic();truncated=False
    from agent_workspace import WorkspaceAccess
    access=WorkspaceAccess(root)
    try:
        while pending and not truncated:
            parent,depth=pending.pop()
            guard=access.descriptor(parent.relative_to(root).as_posix(),directory=True) if parent!=root else contextlib.nullcontext()
            # On Windows the existing handle adapter denies rename/deletion
            # of the opened root and ancestors while enumerating this path.
            if os.name!='nt':raise ValueError('Pinned directory discovery currently requires the Windows handle adapter; import files individually.')
            with guard,os.scandir(parent) as entries:
                for entry in entries:
                    if cancel and cancel.is_set():raise RuntimeError('Model discovery cancelled.')
                    examined+=1
                    if examined>2048 or len(paths)>=64 or time.monotonic()-started>3:truncated=True;break
                    st=entry.stat(follow_symlinks=False)
                    if stat.S_ISLNK(st.st_mode) or getattr(st,'st_file_attributes',0)&0x400:continue
                    if stat.S_ISDIR(st.st_mode) and depth<3:pending.append((Path(entry.path),depth+1))
                    elif stat.S_ISREG(st.st_mode) and entry.name.lower().endswith('.gguf'):paths.append(entry.path)
    finally:access.close()
    return {'paths':paths,'examined':examined,'truncated':truncated,'nativeCompatible':False,'scope':'Filename discovery only. Import individually for metadata inspection.'}
