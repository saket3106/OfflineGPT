"""Real source Qt/React/bridge/catalog/config; synthetic GGUF, zero inference."""
import os,sys,json,time,argparse
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--report-dir',type=Path,required=True);p.add_argument('--qt-site',type=Path);a=p.parse_args();P=a.report_dir.resolve();P.mkdir(parents=True,exist_ok=False)
profile=P/'profile';profile.mkdir();S=Path(__file__).resolve().parents[1]
os.environ.update(OFFLINE_GPT_DATA_DIR=str(profile),OFFLINE_GPT_CAPTURE_FITTED_PROMPTS='0',OFFLINE_GPT_SAVE_TRAINING_CANDIDATES='0')
sys.path.insert(0,str(S));sys.path.insert(0,str(S/'tests'))
if a.qt_site:sys.path.append(str(a.qt_site.resolve()))
from test_model_inspection import fixture
file=P/'qwen-fixture.gguf';file.write_bytes(fixture([('general.architecture',8,'qwen3'),('general.name',8,'Synthetic Qwen reference'),('tokenizer.chat_template',8,'{{ messages }}'),('general.file_type',4,15)]))
(profile/'offline_gpt_config.json').write_text(json.dumps({'gpt_oss_gguf':'','chat_backend':'bundled','backup_schedule_enabled':False,'theme':'dark'}))
import app as offline
from PySide6.QtWidgets import QApplication,QFileDialog,QMessageBox
from PySide6.QtCore import QTimer,QEventLoop
from PySide6.QtTest import QTest
from unittest.mock import patch
application=QApplication([]);backend=offline.App();backend.hide();window=offline.DesktopWindow(backend);window.resize(1366,900);window.show();view=window.view
checks={};started=time.monotonic()
def js(code):
 result=[];loop=QEventLoop();view.page().runJavaScript('JSON.stringify((()=>{'+code+'})())',lambda value:(result.append(value),loop.quit()));QTimer.singleShot(5000,loop.quit);loop.exec()
 return json.loads(result[0]) if result and result[0] else None
def wait(code,seconds=12):
 end=time.monotonic()+seconds
 while time.monotonic()<end:
  if js('return '+code):return True
  QTest.qWait(60)
 return False
def check(name,value):
 checks[name]=bool(value)
 if not value:raise AssertionError(name)
def click(label):js("[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==="+json.dumps(label)+")?.click();return true")
def dialog_answer(button):
 def answer():
  modal=QApplication.activeModalWidget()
  if isinstance(modal,QMessageBox):modal.button(button).click()
  else:QTimer.singleShot(30,answer)
 QTimer.singleShot(60,answer)
try:
 check('react_ready',wait("!!document.querySelector('.sidebar-settings-btn')"));QTest.qWait(800)
 js("document.querySelector('.sidebar-settings-btn').click();return true");check('settings_visible',wait("!!document.querySelector('.settings-dialog')"));click('Model Manager')
 check('manager_empty',wait("document.querySelector('.model-manager-empty')?.textContent.includes('No model references')"))
 with patch.object(QFileDialog,'getOpenFileName',return_value=(str(file),'GGUF')):click('Import GGUF');check('actual_worker_imported',wait("!!document.querySelector('.model-manager-item')"))
 check('real_sqlite_reference',backend.store.con.execute('SELECT count(*) FROM local_model_references').fetchone()[0]==1)
 check('metadata_display',js("return document.querySelector('.model-manager-item').textContent.includes('Q4_K_M')"))
 click('Details');check('unverified_capabilities_clear',wait("document.querySelector('.model-manager-details')?.textContent.includes('None recorded')"))
 dialog_answer(QMessageBox.No);click('Select as default');check('cancel_selection_error',wait("document.querySelector('.model-manager [role=alert]')?.textContent.includes('Selection cancelled')"))
 check('cancel_keeps_old_selection',offline.load_config()['gpt_oss_gguf']=='')
 dialog_answer(QMessageBox.Yes);click('Select as default');check('confirmed_default_selected',wait("document.querySelector('.model-manager [role=status]')?.textContent.includes('Default model selected')"))
 check('config_readback',offline.load_config()['gpt_oss_gguf']==str(file));check('not_falsely_loaded',not backend.engines.chat_status()['loaded'])
 click('Hardware estimate');check('advisory_not_load_permission',wait("document.querySelector('.model-manager-fit')?.textContent.includes('does not authorize loading')"))
 check('unknown_allocator_is_not_fake_fit',js("return document.querySelector('.model-manager-fit').textContent.includes('unknown')"))
 # Independent original bytes and database isolation.
 check('weights_unchanged',file.read_bytes()==fixture([('general.architecture',8,'qwen3'),('general.name',8,'Synthetic Qwen reference'),('tokenizer.chat_template',8,'{{ messages }}'),('general.file_type',4,15)]))
 for theme in ['dark','light']:
  js('document.documentElement.dataset.theme='+json.dumps(theme)+';return true')
  for width,height in [(1366,900),(960,700)]:
   window.resize(width,height);QTest.qWait(100)
   check(theme+'_'+str(width)+'_fits',js("const e=document.querySelector('.model-manager'),r=e.getBoundingClientRect();return r.width>200&&r.left>=0&&r.right<=innerWidth+1&&e.scrollWidth<=e.clientWidth+1"))
  view.grab().save(str(P/('model-manager-'+theme+'.png')))
 # Save CPU preference through actual bridge, not merely load_config.
 window.bridge.saveSettings(json.dumps({'chat_gpu':'cpu'}));check('cpu_roundtrip',offline.load_config()['chat_gpu_layers']==0)
 check('conversation_store_preserved',backend.store.con.execute('SELECT count(*) FROM messages').fetchone()[0]==0)
finally:
 for _,worker in list(window.bridge.workspace_jobs.values()):worker.cancel_event.set()
 end=time.monotonic()+5
 while window.bridge.workspace_jobs and time.monotonic()<end:QTest.qWait(50)
 window.close();QTest.qWait(250);checks['normal_shutdown']=not window.isVisible()
 result={'scope':__doc__,'checks':checks,'failed':[n for n,v in checks.items() if not v],'seconds':time.monotonic()-started,'nativeModelCalls':0}
 (P/'model-manager-qt.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result,indent=2))
 if result['failed']:raise SystemExit(1)
