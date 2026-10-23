"""Qt adapter for the in-application local agent. All inference is off the GUI thread."""
import asyncio
import json
import os
import sqlite3
import tempfile
import re
from pathlib import Path
import time
from PySide6.QtCore import QObject,QThread,Signal,Slot
from PySide6.QtWidgets import QFileDialog
from agent_mcp import sdk_ready,SDK_VERSION
from agent_runtime import AgentRuntime,AgentTaskStore,LocalAgentModel
from agent_workspace import WorkspaceAccess


class AgentWorker(QThread):
    activity=Signal(str)
    def __init__(self,backend,root,protected,prompt,parent=None,resources=None):
        super().__init__(parent);self.backend=backend;self.root=root;self.protected=protected;self.prompt=prompt
        self.runtime=None;self.answer='';self.cancelled=False;self.error='';self.chat=backend.current;self.started=time.monotonic();self.model_identity={}
        self.resources=resources;self.files=[];self.evidence=[];self.visual_evidence=[]
    def cancel(self):
        self.cancelled=True
        if self.runtime:self.runtime.cancel()
    def run(self):
        temporary=None;access=None
        try:
            from agent_resources import AgentSpecialists,AttachmentRegistry
            if not self.root:temporary=tempfile.TemporaryDirectory(prefix='offlinegpt-agent-readonly-')
            root=self.root or temporary.name
            chat_model = ''
            if hasattr(self.backend, 'store') and hasattr(self.backend.store, 'get_chat_model'):
                chat_model = self.backend.store.get_chat_model(self.chat)
            if not chat_model and hasattr(self.backend, 'model'):
                chat_model = getattr(self.backend, 'model', '')
            if not chat_model and hasattr(self.backend, 'engines') and hasattr(self.backend.engines, 'cfg'):
                chat_model = self.backend.engines.cfg.get('chat_model', '')
            store=AgentTaskStore(self.backend.store.workspace)
            planner=LocalAgentModel(self.backend.engines, model_target=chat_model or None)
            runtime=AgentRuntime(planner,root,store,self.chat,
                         lambda e:self.activity.emit(json.dumps(e,ensure_ascii=False)),protected=self.protected,
                         interpreter=os.environ.get('OFFLINE_GPT_MCP_PYTHON') or None)
            self.runtime=runtime
            access=WorkspaceAccess(root,self.protected)
            def progress(event):
                if not runtime.cancel_event.is_set() and runtime.state not in {'COMPLETED','FAILED','CANCELED','PARTIAL','BUDGET_EXHAUSTED','ANSWERED'}:
                    runtime.emit('SPECIALIST',event.get('summary','Running local specialist'))
            runtime.workspace_authorized=bool(self.root)
            runtime.specialists=AgentSpecialists(self.backend.engines,access,self.resources or AttachmentRegistry(),progress,bool(self.root))
            runtime.resources=(self.resources.public() if self.resources else [])
            from agent_memory import AgentHistory
            # Exclude the current command itself: an agent request is not
            # historical corroboration of whatever it asks the model to claim.
            with self.backend.store.lock:
                row=self.backend.store.con.execute("SELECT id FROM messages WHERE chat_id=? AND role='user' ORDER BY id DESC LIMIT 1",(self.chat,)).fetchone()
            runtime.history=AgentHistory(self.backend.store.recall,row[0] if row else None)
            if self.cancelled:runtime.cancel()
            self.answer=asyncio.run(runtime.run(self.prompt))
            self.model_identity=dict(getattr(planner,'completion_identity',{}))
            self.files=runtime.specialists.completed_files;self.evidence=runtime.evidence
            self.visual_evidence=runtime.specialists.visual_evidence
        except Exception as exc:
            self.error=str(exc)[:1000]
            if self.runtime:self.runtime.state='FAILED'
            self.answer='Agent runtime stopped: '+self.error+'. Inspect any separate artifacts before retrying; no operation will be replayed automatically.'
        finally:
            if access:access.close()
            if temporary:temporary.cleanup()


class DesktopAgentController(QObject):
    def __init__(self,bridge,app_dir,data_dir):
        super().__init__(bridge);self.bridge=bridge;self.backend=bridge.backend;self.worker=None;self.root=''
        self.protected=[str(app_dir),str(data_dir)]
        self.final_error=''
        self.attachment_grants={}
        self.attachment_grant_errors={}
    def status(self):
        import agent_mcp
        from agent_mcp import capability_catalog
        vision=self.backend.engines.vision_model_status()
        return {'available':sdk_ready(),'sdkVersion':SDK_VERSION,'workspace':self.root,'active':self.worker is not None,
                'capabilityCatalog':capability_catalog(vision),
                'capabilities':{'transport':'stdio','planner':'bounded local-model JSON actions; acceptance is model-specific',
                                'fileWrites':'new approved copies only','shell':False,'arbitraryPython':False,'networkTools':False,
                                'nativeImageAgentTools':bool(vision.get('visionAvailable')),'nativeVoiceAgentTools':False},
                'sdkStartupMs':agent_mcp.SDK_STARTUP_MS,
                'reason':agent_mcp.SDK_STARTUP_ERROR or ('' if sdk_ready() else f'Agent Mode needs the reviewed mcp=={SDK_VERSION} dependency set in this build. Ordinary chat remains available.')}
    def pick(self):
        if self.backend.busy or self.worker:return {'ok':False,'error':'Finish or cancel the active task first.'}
        root=QFileDialog.getExistingDirectory(self.bridge.window,'Choose a local Agent workspace',options=QFileDialog.Option.DontUseNativeDialog)
        if not root:return {'ok':False,'cancelled':True}
        return self.authorize_workspace(root)
    def authorize_workspace(self,root):
        if self.backend.busy or self.worker:return {'ok':False,'error':'Finish or cancel the active task first.'}
        try:
            if not isinstance(root,str) or not Path(root).is_absolute():raise ValueError('Enter the exact absolute folder to authorize.')
            access=WorkspaceAccess(root,self.protected);access.close();self.root=str(Path(root).resolve())
            return {'ok':True,'workspace':self.root}
        except (OSError,ValueError,PermissionError) as exc:return {'ok':False,'error':str(exc)}
    def grant_attachments(self,paths):
        from agent_resources import AttachmentRegistry
        registry=AttachmentRegistry()
        # An optional Agent intake limit must not break ordinary document chat.
        for path in paths[:4]:
            try:
                for record in registry.register([path],self.backend.current or 'draft','native file selection'):
                    self.attachment_grants[record['path']]=record;self.attachment_grant_errors.pop(record['path'],None)
            except (OSError,ValueError,PermissionError) as exc:self.attachment_grant_errors[str(Path(path).absolute())]=str(exc)
    def start(self,prompt,attachments=None):
        try:
            if self.worker or self.backend.busy or self.bridge.workspace_jobs:raise ValueError('A local task is already running.')
            if not sdk_ready():raise ValueError(self.status()['reason'])
            if not isinstance(prompt,str) or not prompt.strip() or len(prompt.encode())>8000:raise ValueError('Use a nonempty task under8KiB.')
            if not self.backend.current:self.backend.new_chat()
            from agent_resources import AttachmentRegistry
            def attachment_paths(raw):
                if not raw:return []
                try:value=json.loads(raw) if isinstance(raw,str) and raw.startswith('[') else [raw]
                except ValueError:return []
                return [p for p in value if isinstance(p,str)] if isinstance(value,list) else []
            resources=AttachmentRegistry();current_paths=attachments or []
            if not isinstance(current_paths,list) or len(current_paths)>4 or any(not isinstance(p,str) for p in current_paths):raise ValueError('Choose up to four actual attachment files.')
            prior=[]
            for row in self.backend.store.messages(self.backend.current)[-12:]:prior+=attachment_paths(row[2])
            for path in current_paths:
                absolute=str(Path(path).absolute());grant=self.attachment_grants.get(absolute)
                if grant is None and absolute in self.attachment_grant_errors:raise ValueError(self.attachment_grant_errors[absolute])
                if grant is None and path not in prior:raise PermissionError('Choose or drop this attachment through the application before using it.')
                loaded=resources.register([path],self.backend.current)
                if grant and loaded[0]['sha256']!=grant['sha256']:raise ValueError('Attachment changed after selection. Select it again.')
            if not current_paths and re.search(r'\b(?:image|photo|picture|attachment|uploaded|document|file|it|earlier)\b',prompt,re.I):
                from agent_workspace import READ_TYPES
                existing=[p for p in dict.fromkeys(reversed(prior)) if Path(p).suffix.casefold() in READ_TYPES-{'.sqlite','.sqlite3','.db'}][:4]
                resources.register(existing,self.backend.current,'prior attachment in this conversation')
            if not self.root and not resources.entries:raise ValueError('Authorize a workspace or attach a supported file before starting Agent Mode.')
            if self.root:
                access=WorkspaceAccess(self.root,self.protected);access.close()
            self.bridge._cancel_pending_speech()
            self.backend.cancel_voice_input()
            if self.bridge.speech is not None:self.bridge.speech.shutdown()
            worker=AgentWorker(self.backend,self.root,self.protected,prompt,self,resources);self.worker=worker
            worker.activity.connect(self.bridge.event.emit);worker.finished.connect(self.finished)
            self.backend.store.add(self.backend.current,'user',prompt,attachment=json.dumps(current_paths) if current_paths else '',metrics={'agent_resources':resources.public()})
            self.backend.operation_event_id=''
            self.backend.push_messages();self.backend.set_busy(True,'Preparing local Agent task')
            self.backend.active_worker=worker;self.backend.active_thread=worker;self.backend.active_kind='agent'
            worker.start();return {'ok':True,'chatId':self.backend.current}
        except Exception as exc:return {'ok':False,'error':str(exc)}
    def cancel(self):
        if self.worker:self.worker.cancel()
    def approve(self,task,rid,digest,allow):
        worker=self.worker;runtime=worker.runtime if worker else None
        return bool(runtime and runtime.id==task and runtime.broker.decide(task,rid,digest,allow))
    def artifacts(self):
        workspace=self.backend.store.workspace
        try:
            with workspace.lock:
                exists=workspace.con.execute("SELECT 1 FROM sqlite_master WHERE name='agent_artifacts'").fetchone()
            if not exists:return []
            from agent_artifacts import ArtifactJournal
            return ArtifactJournal(workspace).recent(self.backend.current)
        except (sqlite3.DatabaseError,ValueError,KeyError) as exc:return {'ok':False,'error':'Artifact history could not be read: '+str(exc)[:300]}
    def inspect_artifact(self,identifier):
        if self.backend.busy or self.worker:return {'ok':False,'error':'Finish or cancel the current task before inspecting an artifact.'}
        from agent_artifacts import ArtifactJournal
        try:return {'ok':True,**ArtifactJournal(self.backend.store.workspace).inspect(identifier,self.backend.current,self.protected)}
        except (OSError,ValueError,PermissionError,RuntimeError,sqlite3.DatabaseError) as exc:return {'ok':False,'error':str(exc)}
    @Slot()
    def finished(self):
        worker=self.worker
        if worker is None:return
        try:
            self.backend.store.add(worker.chat,'assistant',worker.answer,attachment=json.dumps(worker.files) if worker.files else '',metrics={'agent_task_id':worker.runtime.id if worker.runtime else '',
                                  'agent_state':worker.runtime.state if worker.runtime else 'FAILED',
                                  'model_identity':worker.model_identity,
                                  'agent_evidence':worker.evidence,
                                  'agent_specialist_evidence':worker.visual_evidence,
                                  'agent_traces':getattr(worker.runtime,'traces',[]) if worker.runtime else [],
                                  'duration_seconds':round(time.monotonic()-worker.started,3)})
            self.backend.refresh_history();self.backend.push_messages()
        except Exception as exc:
            self.final_error='Agent result could not be saved: '+str(exc)[:500]
            self.bridge.event.emit(json.dumps({'type':'agentStatus','state':'FAILED','summary':self.final_error,'chatId':worker.chat}))
        finally:
            self.worker=None;self.backend.set_busy(False,'Agent stopped' if worker.error else 'Ready');worker.deleteLater()


    def get_mcp_servers(self):
        from agent_mcp import MCPRegistry
        return {'ok': True, 'servers': MCPRegistry.get_default().get_catalog_manifest()}

    def toggle_mcp_tool(self, tool, enabled):
        from agent_mcp import MCPRegistry
        MCPRegistry.get_default().toggle_tool(tool, bool(enabled))
        return {'ok': True, 'tool': tool, 'enabled': bool(enabled)}

    def toggle_mcp_server(self, server, enabled):
        from agent_mcp import MCPRegistry
        MCPRegistry.get_default().toggle_server(server, bool(enabled))
        return {'ok': True, 'server': server, 'enabled': bool(enabled)}
