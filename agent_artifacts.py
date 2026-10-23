"""Durable approved-write intents in the existing application SQLite store.

The DB and workspace cannot share an atomic transaction. Persist intent before
dispatch and require an acknowledged result plus independent readback for
VERIFIED. Recovery observations never retroactively assert successful execution,
delete partial files, or replay writes. Content is not stored in this journal.
"""
from __future__ import annotations
import hashlib
import contextlib
import json
import os
import time
import uuid
from agent_permissions import argument_digest, canonical
from agent_workspace import WorkspaceAccess, parts_for, DOCUMENT_BYTES

SCHEMA='CREATE TABLE IF NOT EXISTS agent_artifacts(id TEXT PRIMARY KEY,task_id TEXT,chat_id TEXT,state TEXT,record TEXT,updated REAL,root_key TEXT,target_key TEXT,UNIQUE(root_key,target_key))'


def validate_schema(con):
    expected=['id','task_id','chat_id','state','record','updated','root_key','target_key']
    if [r[1] for r in con.execute('PRAGMA table_info(agent_artifacts)')]!=expected:
        raise RuntimeError('Artifact journal schema is incompatible; no source file operation was authorized.')
    unique=[]
    for row in con.execute('PRAGMA index_list(agent_artifacts)'):
        if row[2]:unique.append([r[2] for r in con.execute('PRAGMA index_info("'+row[1].replace('"','""')+'")')])
    if ['root_key','target_key'] not in unique:
        raise RuntimeError('Artifact journal lacks its unique destination guard; no write was dispatched.')


class ArtifactJournal:
    def __init__(self,workspace):self.workspace=workspace

    @contextlib.contextmanager
    def transaction(self):
        with self.workspace.lock:
            con=self.workspace.con
            if con.in_transaction:raise RuntimeError('Finish the existing database transaction before changing an artifact receipt.')
            with con:yield con

    def prepare(self,task,chat,tool,args,previews,*,expected_data=None):
        if tool not in {'write_local_file','patch_local_file','create_workspace_directory','create_workspace_document'}:
            raise PermissionError('This journal supports approved NEW artifacts only; no mutation was dispatched.')
        target=args.get('destination',args.get('path',''))
        parts_for(target)
        record={'workspace':str(previews.access.root),'rootIdentity':previews.access.root_identity(),
                'target':target,'kind':'directory' if tool=='create_workspace_directory' else 'file',
                'tool':tool,'argumentDigest':argument_digest(tool,args),'sourceSha256':args.get('expected_sha256','')}
        if record['kind']=='file':
            if tool=='create_workspace_document':
                if not isinstance(expected_data,bytes):raise PermissionError('Document bytes must be rendered and reviewed before dispatch.')
                data=expected_data
            else:
                content=previews.patch(args)[1] if tool=='patch_local_file' else args['content']
                data=content.encode('utf-8')
            if len(data)>(DOCUMENT_BYTES if tool=='create_workspace_document' else 65536):raise ValueError('New artifact exceeds the approved write budget.')
            record.update(expectedSha256=hashlib.sha256(data).hexdigest(),expectedBytes=len(data))
        identifier=uuid.uuid4().hex
        with self.transaction() as con:
            if con.execute('PRAGMA synchronous').fetchone()[0]<2:
                raise PermissionError('Durable approved-write intents require SQLite synchronous FULL or stronger; no write was dispatched.')
            # A retry must use a new destination. Never implicitly overwrite or
            # repeat an earlier possibly dispatched operation, even after restart.
            root_key=canonical(record['rootIdentity']);target_key=target.casefold()
            if con.execute('SELECT 1 FROM agent_artifacts WHERE root_key=? AND target_key=?',(root_key,target_key)).fetchone():
                raise PermissionError('This destination has an earlier approved-write record. Review it and choose a new destination; no operation was repeated.')
            con.execute('INSERT INTO agent_artifacts VALUES(?,?,?,?,?,?,?,?)',(identifier,task,chat,'DISPATCH_INTENT',canonical(record),time.time(),root_key,target_key))
        return identifier

    def _load(self,identifier,chat):
        with self.workspace.lock:
            row=self.workspace.con.execute('SELECT state,record FROM agent_artifacts WHERE id=? AND chat_id=?',(identifier,chat)).fetchone()
        if row is None:raise ValueError('No artifact journal belongs to this conversation.')
        return row[0],json.loads(row[1])

    def _update(self,identifier,state,record,previous_state,previous_record):
        with self.transaction() as con:
            cursor=con.execute('UPDATE agent_artifacts SET state=?,record=?,updated=? WHERE id=? AND state=? AND record=?',(state,canonical(record),time.time(),identifier,previous_state,previous_record))
            if cursor.rowcount!=1:raise RuntimeError('Artifact journal changed concurrently. Inspect it again; no operation was replayed.')

    @staticmethod
    def observe(access,record):
        if access.root_identity()!=record['rootIdentity']:raise PermissionError('Workspace identity changed; no destination was inspected.')
        directory=record['kind']=='directory'
        limit=DOCUMENT_BYTES if record.get('tool')=='create_workspace_document' else 65536
        with access.descriptor(record['target'],directory=directory) as fd:
            identity=access.object_identity(fd,directory)
            if directory:return {'fileIdentity':identity,'directoryPresent':True}
            info=os.fstat(fd)
            if info.st_size>limit:raise ValueError('Destination exceeds the artifact readback budget.')
            data=bytearray()
            while True:
                block=os.read(fd,8192)
                if not block:break
                data.extend(block)
                if len(data)>limit:raise ValueError('Destination changed or exceeds the readback budget.')
            after=os.fstat(fd)
            if (info.st_size,info.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise ValueError('Destination changed during inspection.')
            return {'fileIdentity':identity,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}

    def verify(self,identifier,chat,result,access):
        state,record=self._load(identifier,chat)
        previous=canonical(record)
        if state!='DISPATCH_INTENT':raise ValueError('Artifact acknowledgement is no longer pending.')
        observed=self.observe(access,record)
        valid=result.get('path')==record['target'] and result.get('fileIdentity')==observed['fileIdentity']
        if record['kind']=='file':
            valid=valid and result.get('originalOverwritten') is False and result.get('sha256')==observed['sha256']==record['expectedSha256'] and result.get('bytes')==observed['bytes']==record['expectedBytes']
        else:valid=valid and result.get('created') is True
        if not valid:raise ValueError('Artifact acknowledgement/readback mismatch. Inspect the recovery record before retrying.')
        record['observation']=observed
        self._update(identifier,'VERIFIED',record,state,previous)

    def inspect(self,identifier,chat,protected=()):
        state,record=self._load(identifier,chat)
        previous=canonical(record)
        access=None
        record.pop('inspectionError',None);record.pop('recoveryObservation',None)
        try:
            access=WorkspaceAccess(record['workspace'],protected)
            observed=self.observe(access,record)
            if record['kind']=='directory':status='DIRECTORY_PRESENT_UNCONFIRMED'
            else:status='EXPECTED_CONTENT_PRESENT_UNCONFIRMED' if (observed['sha256'],observed['bytes'])==(record['expectedSha256'],record['expectedBytes']) else 'PARTIAL_OR_CHANGED'
            record['recoveryObservation']=observed
        except (OSError,ValueError,PermissionError) as exc:
            # File absence, a sharing violation or a replacement root is not a
            # successful deletion, failed write, or permission to replay it.
            status='UNAVAILABLE_UNCONFIRMED'
            record['inspectionError']=str(exc)[:500]
        finally:
            if access:access.close()
        record['lastInspection']=time.time();record['inspectionStatus']=status
        self._update(identifier,state,record,state,previous)
        return {'id':identifier,'state':state,'inspectionStatus':status,'target':record['target'],
                'workspace':record['workspace'],'expectedSha256':record.get('expectedSha256',''),
                'observation':record.get('recoveryObservation',{}),'error':record.get('inspectionError',''),
                'replayed':False,'guidance':'Keep the existing artifact. Inspect it manually; choose a new filename for another approved attempt. No operation was replayed.'}

    def recent(self,chat):
        with self.workspace.lock:
            rows=self.workspace.con.execute('SELECT id,task_id,state,record,updated FROM agent_artifacts WHERE chat_id=? ORDER BY updated DESC LIMIT 30',(chat,)).fetchall()
        return [{'id':row[0],'taskId':row[1],'state':row[2],'updated':row[4],
                 **{k:v for k,v in json.loads(row[3]).items() if k in {'target','workspace','kind','expectedSha256','expectedBytes','inspectionStatus'}}} for row in rows]
