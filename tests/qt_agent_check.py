"""Real source Qt/QWebChannel/SQLite/MCP pipeline; scripted planner, zero inference."""
import os,sys,json,time,argparse
from pathlib import Path
parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--report-dir',type=Path,required=True);parser.add_argument('--qt-site',type=Path);args=parser.parse_args();P=args.report_dir.resolve();P.mkdir(parents=True,exist_ok=True);S=Path(__file__).resolve().parents[1]
profile=P/'profile';profile.mkdir(exist_ok=False);workspace=P/'workspace';workspace.mkdir(exist_ok=False)
(workspace/'source.txt').write_bytes(b'The measured value is 73.\n')
(profile/'offline_gpt_config.json').write_text(json.dumps({'chat_backend':'bundled','gpt_oss_gguf':'','lms_exe':'','vision_model':'','vision_projector':'','sd_cli':'','flux_model':'','flux_encoder':'','flux_vae':'','voice_model_path':'','backup_schedule_enabled':False,'theme':'dark'}))
os.environ.update(OFFLINE_GPT_DATA_DIR=str(profile),OFFLINE_GPT_CAPTURE_FITTED_PROMPTS='0',OFFLINE_GPT_SAVE_TRAINING_CANDIDATES='0',QTWEBENGINE_CHROMIUM_FLAGS='--disable-background-timer-throttling --disable-renderer-backgrounding --disable-backgrounding-occluded-windows')
sys.path.insert(0,str(S))
if args.qt_site:sys.path.append(str(args.qt_site.resolve()))
import app as offline  # Exercise the application's real pre-Qt dependency bootstrap.
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer,QEventLoop,Qt
from PySide6.QtTest import QTest
import agent_desktop
class ScriptedPlanner:
 def __init__(self,engine,model_target=None):self.model_target=model_target
 def __call__(self,messages,cancel,deadline):
  prompt=messages[1]['content']
  if prompt=='DIRECT':return json.dumps({'final':'A general answer without a workspace operation.'})
  if prompt=='Find Juniper project notes in our previous conversations':
   if len(messages)>2:
    packet=json.loads(messages[-1]['content'].split('UNTRUSTED TOOL OBSERVATION: ')[-1])['result']
    source=packet['sources'][0]
    return json.dumps({'final':'The user reported original Juniper notes ['+source['id']+'].'})
   return json.dumps({'tool':'search_conversation_history','arguments':{'query':'Juniper project notes'}})
  if len(messages)>2:return json.dumps({'final':'Tool returned: '+messages[-1]['content'].split('UNTRUSTED TOOL OBSERVATION: ')[-1]})
  if prompt=='READ':return json.dumps({'tool':'read_local_file','arguments':{'path':'source.txt'}})
  return json.dumps({'tool':'write_local_file','arguments':{'path':prompt.lower()+'.txt','content':'Approved content for '+prompt}})
agent_desktop.LocalAgentModel=ScriptedPlanner
application=QApplication([]);backend=offline.App();backend.hide();window=offline.DesktopWindow(backend);window.show();view=window.view;window.bridge.agent.root=str(workspace)
checks={};start=time.monotonic()
def wait(ms=100):QTest.qWait(ms)
def js(code):
 out=[];loop=QEventLoop();view.page().runJavaScript('JSON.stringify((()=>{try{return {ok:true,value:eval('+json.dumps(code)+')}}catch(e){return {ok:false,error:String(e)}}})())',lambda v:(out.append(v),loop.quit()));QTimer.singleShot(5000,loop.quit);loop.exec()
 if not out:raise RuntimeError('JS timeout')
 result=json.loads(out[0]) if out[0] else {}
 if not result.get('ok'):raise RuntimeError('Frontend probe error: '+str(result.get('error')))
 return result.get('value')
def until(code,seconds=35):
 end=time.monotonic()+seconds
 while time.monotonic()<end:
  if js(code):return True
  wait(40)
 return False
def check(name,value):
 checks[name]=bool(value)
 if not value:
  import faulthandler;faulthandler.dump_traceback(file=sys.stderr,all_threads=True)
  raise AssertionError(name)
def submit(text):
 js("(()=>{const e=document.querySelector('textarea');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(e,"+json.dumps(text)+");e.dispatchEvent(new Event('input',{bubbles:true}));})()");wait(80);js("document.querySelector('[aria-label=\"Send message\"]').click()")
try:
 check('react_ready',until("!!document.querySelector('.agent-mode-toggle')"));wait(1200)
 check('isolated_library_starts_empty',backend.store.con.execute('SELECT count(*) FROM library_images').fetchone()[0]==0)
 js("document.querySelector('.agent-mode-toggle').click()");check('mode_connected_to_real_bridge',until("document.querySelector('.agent-workspace-path')?.textContent.includes('workspace')"))
 submit('READ');check('real_mcp_read_result',until("[...document.querySelectorAll('.assistant-message')].some(e=>e.textContent.includes('measured value is 73'))"));check('read_task_released',until("!document.querySelector('[aria-label=\"Stop generation\"]')"))
 submit('WRITE');check('approval_visible',until("!!document.querySelector('.agent-approval-dialog')"));check('not_written_before_approval',not(workspace/'write.txt').exists());check('exact_preview',js("document.querySelector('.agent-approval-dialog').textContent.includes('write.txt')&&document.querySelector('.agent-approval-dialog pre').textContent==='Approved content for WRITE'"))
 for theme in ['dark','light']:
  js('document.documentElement.dataset.theme='+json.dumps(theme))
  for width,height in [(1366,768),(1600,900),(1920,1080),(2560,1440),(960,640)]:
   window.resize(width,height);wait(100);check(f'approval_{theme}_{width}',js("(()=>{const p=document.querySelector('.agent-approval-dialog'),r=p.getBoundingClientRect();return r.left>=0&&r.top>=0&&r.right<=innerWidth+1&&r.bottom<=innerHeight+1&&p.querySelector('pre').textContent==='Approved content for WRITE'})()"))
  view.grab().save(str(P/('agent-approval-'+theme+'.png')))
 window.resize(1366,768);wait(100)
 js("[...document.querySelectorAll('.agent-approval-dialog button')].find(e=>e.textContent==='Allow once').click()")
 check('approved_real_write',until("!document.querySelector('.agent-approval-dialog')&&!document.querySelector('[aria-label=\"Stop generation\"]')") and (workspace/'write.txt').read_bytes()==b'Approved content for WRITE')
 submit('DENY');check('deny_prompt',until("!!document.querySelector('.agent-approval-dialog')"));js("[...document.querySelectorAll('.agent-approval-dialog button')].find(e=>e.textContent==='Deny').click()")
 check('denied_write_absent',until("!document.querySelector('[aria-label=\"Stop generation\"]')") and not(workspace/'deny.txt').exists())
 submit('CANCEL');check('cancel_prompt',until("!!document.querySelector('.agent-approval-dialog')"));js("[...document.querySelectorAll('.agent-approval-dialog button')].find(e=>e.textContent==='Cancel task').click()");check('cancelled_write_absent',until("!document.querySelector('[aria-label=\"Stop generation\"]')") and not(workspace/'cancel.txt').exists())
 check('no_agent_thread_left',window.bridge.agent.worker is None)
 rows=json.loads(window.bridge.getAgentHistory());check('persistent_task_count',len(rows)==4);check('two_completed',sum(r['state']=='COMPLETED' for r in rows)==2);check('cancellation_has_correct_state',sum(r['state']=='CANCELED' for r in rows)==1);check('denial_has_reason',any('denied' in r['outcome'] for r in rows))
 check('source_preserved',(workspace/'source.txt').read_bytes()==b'The measured value is 73.\n')
 check('workspace_controls_reenabled',js("!document.querySelector('.agent-workspace-heading button').disabled"))
 # Real host-only historical retrieval, separate synthetic prior conversation.
 cid=backend.store.new_chat('Earlier synthetic notes')
 backend.store.add(cid,'user','Juniper project: original notes were recorded.')
 submit('Find Juniper project notes in our previous conversations');check('host_history_finished',until("!document.querySelector('[aria-label=\"Stop generation\"]')&&[...document.querySelectorAll('.assistant-message')].some(e=>e.textContent.includes('original Juniper notes'))"))
 check('history_has_original_link',js("!!document.querySelector('.assistant-message .citation-link')"))
 active_chat=backend.current
 js("document.querySelector('.assistant-message .citation-link').click()")
 check('citation_navigates_to_original',until("document.querySelector('.user-message')?.textContent.includes('Juniper project: original notes')") and backend.current==cid)
 active_title=backend.store.con.execute('SELECT title FROM chats WHERE id=?',(active_chat,)).fetchone()[0]
 js("[...document.querySelectorAll('.history-item')].find(e=>e.textContent.trim()==="+json.dumps(active_title)+").click()")
 check('sidebar_returns_to_agent_conversation',until("[...document.querySelectorAll('.assistant-message')].some(e=>e.textContent.includes('original Juniper notes'))") and backend.current==active_chat)
 submit('DIRECT');check('no_action_answer_visible',until("!document.querySelector('[aria-label=\"Stop generation\"]')&&[...document.querySelectorAll('.assistant-message')].some(e=>e.textContent.includes('No tools were executed'))"))
 check('no_action_not_completed',any(row['state']=='ANSWERED' for row in json.loads(window.bridge.getAgentHistory())))
 check('answered_state_hides_cancel',js("!document.querySelector('.agent-current button')"))
 # Simulate a lost acknowledgement after a partial NEW write. Do not replay.
 from agent_artifacts import ArtifactJournal
 from agent_mcp import WorkspaceTools
 from agent_workspace import WorkspaceAccess
 from agent_permissions import GrantAuthority
 journal=ArtifactJournal(backend.store.workspace);access=WorkspaceAccess(workspace)
 try:recovery_id=journal.prepare('interrupted-fixture',backend.current,'write_local_file',{'path':'partial.txt','content':'complete expected content'},WorkspaceTools(access,GrantAuthority(b'a'*32)))
 finally:access.close()
 (workspace/'partial.txt').write_bytes(b'complete')
 # Reopening the Agent panel reloads persisted recovery records via QWebChannel.
 js("document.querySelector('.agent-mode-toggle').click()");wait(80);js("document.querySelector('.agent-mode-toggle').click()")
 check('artifact_journal_visible',until("!!document.querySelector('.agent-artifact-journal')"))
 js("document.querySelector('.agent-artifact-journal').open=true")
 check('verified_write_present',js("document.querySelector('.agent-artifact-journal').textContent.includes('Write acknowledged and verified')"))
 js("[...document.querySelectorAll('.agent-artifact-journal li')].find(e=>e.textContent.includes('partial.txt')).querySelector('button').click()")
 check('partial_recovery_explained',until("document.querySelector('.agent-artifact-journal').textContent.includes('PARTIAL_OR_CHANGED')"))
 check('recovery_does_not_replay',(workspace/'partial.txt').read_bytes()==b'complete')
 check('journal_in_existing_db',backend.store.con.execute('SELECT count(*) FROM agent_artifacts').fetchone()[0]==2)
 wait(300)
 check('artifact_recovery_is_visibly_rendered',js("(()=>{const e=document.querySelector('.agent-artifact-journal'),r=e.getBoundingClientRect();return r.height>50&&r.top<innerHeight&&r.bottom>0&&getComputedStyle(e).visibility==='visible';})()"))
 view.grab().save(str(P/'artifact-recovery.png'))
 js("document.querySelector('.agent-artifact-journal').open=false")
 js("document.querySelector('[title=\"Switch to light theme\"]').click()");wait(100);view.grab().save(str(P/'agent-activity-light.png'))
 for width,height in [(1366,768),(1600,900),(1920,1080),(2560,1440),(960,640)]:
  window.resize(width,height);wait(100);check('agent_panel_'+str(width),js("(()=>{const r=document.querySelector('.agent-workspace-panel').getBoundingClientRect();return r.left>=0&&r.right<=innerWidth+1&&r.height<innerHeight*.4})()"))
finally:
 if window.bridge.agent.worker:window.bridge.agent.cancel()
 end=time.monotonic()+8
 while window.bridge.agent.worker and time.monotonic()<end:wait(50)
 window.close();end=time.monotonic()+5
 while window.isVisible() and time.monotonic()<end:wait(50)
 checks['normal_shutdown']=not window.isVisible()
 result={'scope':__doc__,'checks':checks,'failed':[k for k,v in checks.items() if not v],'seconds':time.monotonic()-start,'sdkStartupMs':window.bridge.agent.status()['sdkStartupMs'],'nativeModelCalls':0,'taskRows':backend.store.con.execute('SELECT id,state,step FROM agent_tasks').fetchall() if checks.get('persistent_task_count') else []}
 (P/'agent-qt-integration.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result,indent=2))
 if result['failed']:raise SystemExit(1)




