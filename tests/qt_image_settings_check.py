"""Qt layout and event contracts on real built UI; synthetic bridge, no models."""
import os,sys,ast,json,time,argparse
from pathlib import Path
os.environ['QTWEBENGINE_CHROMIUM_FLAGS']='--disable-background-timer-throttling --disable-renderer-backgrounding --disable-backgrounding-occluded-windows'
from PySide6.QtCore import QObject,Signal,Slot,QUrl,QEventLoop,QTimer,QThread,Qt
from PySide6.QtWidgets import QApplication
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEngineProfile,QWebEnginePage
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtTest import QTest
S=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser();parser.add_argument('--report-dir',type=Path,required=True);parser.add_argument('--before',action='store_true');args=parser.parse_args();ROOT=args.report_dir.resolve();ROOT.mkdir(parents=True,exist_ok=True);(ROOT/'logs').mkdir(exist_ok=True)
before=args.before
UI=S.parent/'Offline GPT/ui' if before else S/'ui'
tree=ast.parse((S/'tests/qt_frontend_check.py').read_text(encoding='utf-8'));node=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Bridge');exec(compile(ast.Module(body=[node],type_ignores=[]),'existing_test_bridge','exec'))
class ProbeBridge(Bridge):
    def __init__(self):
        super().__init__();self.actions=[];self.copy_ok=True;self.settings={'theme':'dark','chat_backend':'bundled','gpt_oss_gguf':'fixture.gguf','chat_context':4096,'model_lifecycle_mode':'balanced','model_keepalive_seconds':90,'flux_quality_preset':'balanced','flux_steps':20,'flux_backend':'auto','flux_memory':'low'}
    @Slot(result=str)
    def getSettings(self):return json.dumps(self.settings)
    @Slot(str)
    def saveSettings(self,raw):self.settings=json.loads(raw);self.actions.append(['saveSettings']);self.statusChanged.emit('Settings saved locally')
    @Slot(result=str)
    def getSpeechStatus(self):return json.dumps({'state':'idle','provider':'sapi','speed':1,'kokoroInstalled':False,'voiceOptions':[]})
    @Slot(str,result=str)
    def speechSettings(self,raw):self.actions.append(['speechSettings',json.loads(raw)]);return '{"ok":true}'
    @Slot(str,result=str)
    def copyImage(self,path):self.actions.append(['copyImage',path]);return json.dumps({'ok':self.copy_ok,'error':'Synthetic denied clipboard write' if not self.copy_ok else ''})
    @Slot(str,result=str)
    def copyText(self,text):self.copies.append(text);return '{"ok":true}'
    @Slot(str)
    def saveImage(self,path):self.actions.append(['saveImage',path])
    @Slot(str)
    def revealFile(self,path):self.actions.append(['revealFile',path])
    @Slot()
    def retryLastMessage(self):self.actions.append(['retryLastMessage'])
app=QApplication([]);app.setQuitOnLastWindowClosed(False)
view=QWebEngineView();profile=QWebEngineProfile(view);profile.setHttpCacheType(QWebEngineProfile.NoCache);page=QWebEnginePage(profile,view);view.setPage(page)
bridge=ProbeBridge();channel=QWebChannel(page);channel.registerObject('offline',bridge);page.setWebChannel(channel)
view.resize(1366,768);view.setWindowTitle('OfflineGPT isolated visual regression — no models');view.load(QUrl.fromLocalFile(str(UI/'index.html')));view.show()
def wait(ms=120):QTest.qWait(ms)
def js(code):
    out=[];loop=QEventLoop();page.runJavaScript('JSON.stringify(eval('+json.dumps(code)+'))',lambda v:(out.append(v),loop.quit()));QTimer.singleShot(7000,loop.quit);loop.exec()
    if not out:raise RuntimeError('Qt JS timeout')
    return json.loads(out[0]) if out[0] else None
def until(code,seconds=4):
    end=time.perf_counter()+seconds
    while time.perf_counter()<end:
        if js(code):return True
        wait(40)
    return False
def snap(name):wait();view.grab().save(str(ROOT/(('before-' if before else 'after-')+name+'.png')))
checks={};observations={};start=time.perf_counter()
def check(name,value):checks[name]=bool(value)
try:
    check('react_ready',until("!!document.querySelector('.composer-shell')"))
    wait(1400)
    prompt='Preserve the original vehicle and scene. Make only the body paint red.\nUnicode: café & reflections.'
    bridge.emit({'type':'busy','value':False,'status':'Ready'})
    bridge.emit({'type':'chatChanged','id':'probe','messages':[{'id':'result-image','role':'assistant','type':'image','content':'Generated image.','attachment':str(bridge.fixture),'dimensions':'3840 × 2160','prompt':prompt,'userPrompt':'Change only the paint','duration':248,'metrics':{'image_performance':{'stages':{'SAMPLING_MS':900,'PNG_ENCODING_MS':12}}}}]})
    check('image_decoded',until("document.querySelector('.image-preview')?.naturalWidth===256"))
    wait(400);js("document.querySelector('.image-result-toolbar').scrollIntoView({block:'center'})");wait()
    observations['toolbar']=js("(()=>{const g=document.querySelector('.image-actions-group');return {display:getComputedStyle(g).display,gap:getComputedStyle(g).gap,buttonDisplay:getComputedStyle(g.querySelector('button')).display,labels:[...g.querySelectorAll('button')].map(e=>e.textContent.trim())}})()")
    snap('image-toolbar-dark')
    if before:
        observations['metadata_4k_claim_for_256_pixel_image']=js("[...document.querySelectorAll('.format-chip')].some(e=>e.textContent.includes('4K'))")
        js("document.querySelector('[title=\"Settings\"]').click()");wait(250)
        js("[...document.querySelectorAll('.settings-nav button')].find(e=>e.textContent.includes('Image generation')).click()");wait()
        observations['native_settings_selects']=js("document.querySelectorAll('.settings-dialog select').length")
        snap('settings-native-dropdown')
    else:
        check('intrinsic_file_dimensions_override_prompt',js("document.querySelector('[aria-label=\"Actual image dimensions\"]').textContent.includes('256 × 144')&&!document.querySelector('.image-result-card .format-chip')?.textContent.includes('4K')"))
        def layout():return js("(()=>{const g=document.querySelector('.image-actions-group'),r=g.getBoundingClientRect(),bs=[...g.querySelectorAll('button')].map(b=>b.getBoundingClientRect());return getComputedStyle(g).display==='flex'&&parseFloat(getComputedStyle(g).gap)>=6&&bs.every(b=>b.width>=30&&b.height>=32&&b.left>=r.left-.5&&b.right<=r.right+.5)&&bs.every((a,i)=>bs.every((b,j)=>i===j||a.right<=b.left+.5||b.right<=a.left+.5||a.bottom<=b.top+.5||b.bottom<=a.top+.5));})()")
        for theme in ['dark','light']:
            js("document.documentElement.dataset.theme="+json.dumps(theme));wait(50)
            for width,height in [(1366,768),(1600,900),(1920,1080),(2560,1440),(960,640),(640,600)]:
                view.resize(width,height);wait(100);check(f'toolbar_{theme}_{width}',layout())
            view.resize(1366,768);wait();snap('image-toolbar-'+theme)
        for zoom in [1.25,1.5,2]:
            view.setZoomFactor(zoom);wait(200);check('toolbar_zoom_'+str(zoom),layout())
        view.setZoomFactor(1);view.resize(1366,768);wait()
        js("document.querySelector('.image-action-btn[title=\"Copy Image to Clipboard\"]').click()")
        check('copy_image_real_handler_contract',until("document.querySelector('.image-actions-group').textContent.includes('Copied')") and ['copyImage',str(bridge.fixture)] in bridge.actions)
        bridge.copy_ok=False;js("document.querySelector('.image-action-btn[title=\"Copy Image to Clipboard\"]').click()")
        check('copy_failure_visible_not_success',until("document.querySelector('.image-result-error')?.textContent.includes('denied')") and not js("document.querySelector('.image-actions-group').textContent.includes('Copied')"))
        js("document.querySelector('[title=\"Save As…\"]').click();document.querySelector('[title=\"Show in Folder\"]').click();document.querySelector('[title=\"Regenerate image\"]').click()")
        check('save_folder_regenerate_handlers',[['saveImage',str(bridge.fixture)],['revealFile',str(bridge.fixture)],['retryLastMessage']]==bridge.actions[-3:])
        js("document.querySelector('.prompt-accordion-toggle').click()")
        check('details_expand_aria',js("document.querySelector('.prompt-accordion-toggle').getAttribute('aria-expanded')==='true'"))
        js("document.querySelector('.prompt-copy-btn').click()");wait()
        check('prompt_copy_exact_unicode',bridge.copies[-1:]==[prompt])
        js("document.querySelector('[aria-label=\"View full-resolution image\"]').click()")
        check('lightbox_real_modal',until("!!document.querySelector('.lightbox-dialog[role=dialog]')"))
        check('lightbox_geometry',js("(()=>{const r=document.querySelector('.lightbox-dialog').getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight&&r.width>innerWidth*.8})()"))
        snap('full-resolution-viewer');QTest.keyClick(view.focusProxy() or view,Qt.Key_Escape);wait(150)
        check('lightbox_escape_returns_to_chat',js("!document.querySelector('.lightbox-dialog')"))
        js("document.querySelector('[title=\"Create variation\"]').click()");wait(100)
        check('variation_keeps_source_attachment',js("!!document.querySelector('.attachment-strip .file-pill')&&document.querySelector('textarea').value.includes('variation')"))
        js("document.querySelector('[title=\"Settings\"]').click()");wait(250)
        check('no_native_settings_popup',js("document.querySelectorAll('.settings-dialog select').length===0"))
        sections=js("[...document.querySelectorAll('.settings-nav button')].map(e=>e.textContent.trim())")
        for theme in ['dark','light']:
            js("document.documentElement.dataset.theme="+json.dumps(theme))
            for index,title in enumerate(sections):
                js("document.querySelectorAll('.settings-nav button')["+str(index)+"].click()");wait(60)
                count=js("document.querySelectorAll('.settings-content .themed-select-trigger:not(:disabled)').length");observations[f'section_{theme}_{index}']={'title':title,'count':count,'dialog':js("!!document.querySelector('.settings-dialog')")}
                for n in range(count):
                    js("document.querySelectorAll('.settings-content .themed-select-trigger:not(:disabled)')["+str(n)+"].scrollIntoView({block:'center'})");wait(120);js("document.querySelectorAll('.settings-content .themed-select-trigger:not(:disabled)')["+str(n)+"].click()");wait(120)
                    ready=until("!!document.querySelector('.themed-select-popup:not([data-closed])')")
                    check(f'dropdown_{theme}_{index}_{n}_opens',ready)
                    if ready:
                        check(f'dropdown_{theme}_{index}_{n}_contrast_geometry',js("(()=>{const p=document.querySelector('.themed-select-popup:not([data-closed])'),r=p.getBoundingClientRect(),c=getComputedStyle(p),rgb=c.backgroundColor.match(/\\d+/g).slice(0,3).map(Number);return r.width>100&&r.height>20&&r.left>=0&&r.right<=innerWidth+1&&r.top>=0&&r.bottom<=innerHeight+1&&p.querySelectorAll('[role=option]').length>0&&(document.documentElement.dataset.theme==='dark'?Math.max(...rgb)<100:Math.min(...rgb)>200);})()"))
                        if 'Image generation' in title and n==1:snap('settings-quality-popup-'+theme)
                    QTest.keyClick(view.focusProxy() or view,Qt.Key_Escape);until("!document.querySelector('.themed-select-popup:not([data-closed])')",1)
                    if index==0 and n==0:observations['escape_'+theme]=js("({active:document.activeElement?.outerHTML,popups:[...document.querySelectorAll('.themed-select-popup:not([data-closed])')].map(e=>e.outerHTML.slice(0,1000)),triggers:[...document.querySelectorAll('.themed-select-trigger')].map(e=>e.outerHTML.slice(0,400))})")
                    wait(120)
                    check(f'dropdown_{theme}_{index}_{n}_escape_keeps_dialog',js("!!document.querySelector('.settings-dialog')&&!document.querySelector('.themed-select-popup:not([data-closed])')"))
            js("document.querySelectorAll('.settings-nav button')[0].click()");wait(80)
            check('no_numeric_gguf_inspector_'+theme,js("![...document.querySelectorAll('.setting-field')].find(e=>e.textContent.includes('Idle unload timeout'))?.querySelector('[title=\"Inspect GGUF metadata\"]')"))
        js("[...document.querySelectorAll('.settings-nav button')].find(e=>e.textContent.includes('Image generation')).click()");wait()
        js("[...document.querySelectorAll('.setting-field')].find(e=>e.querySelector('.field-label')?.textContent==='Generation quality preset').querySelector('[role=combobox]').click()")
        wait(100);QTest.keyClick(view.focusProxy() or view,Qt.Key_End);QTest.keyClick(view.focusProxy() or view,Qt.Key_Enter);wait(100)
        check('keyboard_select_updates_value',js("[...document.querySelectorAll('.setting-field')].find(e=>e.querySelector('.field-label')?.textContent==='Generation quality preset').textContent.includes('Fast')"))
        js("[...document.querySelectorAll('button')].find(e=>e.textContent==='Save settings').click()");wait(150)
        check('selected_quality_saved_by_existing_handler',bridge.settings.get('flux_quality_preset')=='fast')
        snap('settings-saved')
except Exception as e:
    observations['exception']=str(e)
finally:
    result={'scope':'Actual Qt WebEngine, built React, synthetic bridge; zero native inference','checks':checks,'observations':observations,'failures':[k for k,v in checks.items() if not v],'elapsedSeconds':time.perf_counter()-start}
    (ROOT/('baseline-qt.json' if before else 'phase-a-qt.json')).write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2));view.close();page.deleteLater()
    raise SystemExit(bool(result['failures'] or observations.get('exception')))







