"""Desktop regression probe: built React UI inside the application's Qt WebEngine.

Run manually with --real to include the installed local GPT-OSS engine.

It uses an isolated bridge and never writes chats/settings/model files.

"""

import json

import sys

import time
import os

# Exercise animation/ResizeObserver frames even when Codex covers the test
# window. Chromium can suspend those frames for an occluded native window.
os.environ['QTWEBENGINE_CHROMIUM_FLAGS']=(os.environ.get('QTWEBENGINE_CHROMIUM_FLAGS','')+' --disable-background-timer-throttling --disable-renderer-backgrounding --disable-backgrounding-occluded-windows').strip()

from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QObject,Signal,Slot,QUrl,QEventLoop,QTimer,QThread

from PySide6.QtWidgets import QApplication

from PySide6.QtWebEngineWidgets import QWebEngineView

from PySide6.QtWebChannel import QWebChannel

from PySide6.QtTest import QTest

ROOT=Path(__file__).resolve().parents[1]



class Bridge(QObject):

    event=Signal(str);historyChanged=Signal();statusChanged=Signal(str);filesDropped=Signal(str);fileDragState=Signal(bool)

    def __init__(self):

        super().__init__();self.copies=[];self.renderings=[];self.stop_requested=False
        from PIL import Image,ImageDraw
        folder=ROOT/'logs/library-recovery';folder.mkdir(parents=True,exist_ok=True)
        self.fixture=folder/'frontend-fixture.png';Image.new('RGB',(256,144),(30,30,30)).save(self.fixture)
        self.mask=folder/'frontend-mask.png';mask=Image.new('L',(256,144));ImageDraw.Draw(mask).rectangle((40,40,200,110),fill=255);mask.save(self.mask)
        self.library=[{'id':'fixture-a','path':str(self.fixture),'userPrompt':'Alpha isolated fixture','created':1,'displayName':'Alpha isolated fixture','operation':'generate_image','model':'Model Alpha','fileSize':30,'finalDimensions':'400 Ã— 200'},{'id':'fixture-b','path':str(self.fixture),'userPrompt':'Beta isolated fixture','created':2,'displayName':'Beta isolated fixture','parentId':'fixture-a','operation':'recolor','model':'Model Beta','fileSize':50,'finalDimensions':'200 Ã— 100'}];self.library_calls=[];self.drafts={}
        self.hidden=[{'id':'fixture-missing','path':str(folder/'missing-original.png'),'userPrompt':'Missing original fixture','hidden':True,'fileExists':False,'created':0}]
        self.recovery=[{'id':'pending-ui','imageId':'fixture-missing','action':'recycle','path':str(folder/'missing-original.png'),'title':'Missing original fixture','state':'needs_review','outcome':'unknown','fileExists':False,'ownerMatches':True,'hasOriginalIdentity':False}]
        self.recovery.append({'id':'changed-owner-ui','imageId':'fixture-b','action':'recycle','path':str(folder/'previous-path.png'),'title':'Changed reference fixture','state':'needs_review','outcome':'unknown','fileExists':False,'ownerMatches':False,'hasOriginalIdentity':False})

    @Slot(str,result=str)
    def getPromptDraft(self,cid):return json.dumps(self.drafts.get(cid,{'text':'','attachments':[]}))
    @Slot(str,str,result=str)
    def savePromptDraft(self,cid,raw):self.drafts[cid]=json.loads(raw);return '{"ok":true}'
    @Slot(str,result=str)
    def searchConversations(self,query):return '[]'
    @Slot(str,result=str)
    def getLibraryVersions(self,image_id):return json.dumps({'ok':True,'images':self.library+self.hidden})
    @Slot(result=str)
    def getLibraryImages(self):return json.dumps(self.library)

    @Slot(str,result=str)
    def getLibraryView(self,visibility):return json.dumps({'ok':True,'images':self.hidden if visibility=='hidden' else self.library,'recovery':{'operations':self.recovery,'pendingCount':sum(o['state']=='needs_review' for o in self.recovery)}})

    @Slot(str,str,result=str)
    def workspaceRequest(self,action,raw):
        args=json.loads(raw);self.library_calls.append((action,args));rid='fixture-library-job'
        def finish():
            ids=args.get('ids',[])
            if action=='libraryReview':
                next(operation for operation in self.recovery if operation['id']==args['id'])['state']='closed_unknown';value={'state':'closed_unknown','physicalFilesChanged':False}
            else:
                if args.get('action')=='restore':
                    self.library.extend({**i,'hidden':False} for i in self.hidden if i['id'] in ids);self.hidden=[i for i in self.hidden if i['id'] not in ids]
                else:
                    self.hidden.extend({**i,'hidden':True,'fileExists':True} for i in self.library if i['id'] in ids);self.library=[i for i in self.library if i['id'] not in ids]
                value={'completedIds':ids,'completed':len(ids),'expected':len(ids),'results':[{'id':iid,'status':'completed'} for iid in ids]}
            self.emit({'type':'workspaceResult','requestId':rid,'ok':True,'value':value})
        QTimer.singleShot(60,finish)
        return json.dumps({'ok':True,'requestId':rid})

    def emit(self,event):

        event.setdefault('chatId','probe');event.update(emittedAt=time.time()*1000)

        self.event.emit(json.dumps(event))

    @Slot(result=str)

    def getChats(self):return json.dumps([{'id':'probe','title':'Desktop regression probe','updated':time.time()}])

    @Slot(result=str)

    def currentChat(self):return 'probe'

    @Slot(str,result=str)

    def loadChat(self,cid):return '[]'

    @Slot(result=str)

    def getSettings(self):return '{"theme":"dark","model_lifecycle_mode":"fast"}'

    @Slot(result=str)

    def getModelStatus(self):return '{"state":"ready","model":"GPT-OSS 20B","loaded":true,"backend":"bundled"}'

    @Slot(str,result=str)

    def copyText(self,text):self.copies.append(text);QApplication.clipboard().setText(text);return '{"ok":true}'

    @Slot(str)

    def recordRendering(self,raw):self.renderings.append(json.loads(raw))

    @Slot()

    def stop(self):

        self.stop_requested=True

        if getattr(self,"active_worker",None):self.active_worker.cancel()

    @Slot(str,str,float)

    def sendMessage(self,text,paths,client_time):pass

    @Slot(str)

    def saveSettings(self,raw):pass



app=QApplication(sys.argv)

view=QWebEngineView();view.resize(1280,860)

bridge=Bridge();channel=QWebChannel(view.page());channel.registerObject('offline',bridge);view.page().setWebChannel(channel)

view.load(QUrl.fromLocalFile(str(ROOT/'ui'/'index.html')));view.show()



def wait(ms):QTest.qWait(ms)

def eventually(code, timeout=2500):
    deadline=time.perf_counter()+timeout/1000
    while time.perf_counter()<deadline:
        if js(code):return True
        wait(30)
    return bool(js(code))

def js(code):

    result=[];loop=QEventLoop()

    def received(value):result.append(value);loop.quit()

    view.page().runJavaScript("JSON.stringify(eval("+json.dumps(code)+"))",received);QTimer.singleShot(10000,loop.quit);loop.exec()

    if not result:raise AssertionError('Qt JavaScript callback timed out')

    return json.loads(result[0]) if result[0] else None



wait(1800)

report={'qt':{},'real_requests':[]}

def capture_documentation():
    js('window.dispatchEvent(new KeyboardEvent("keydown",{key:"Escape",bubbles:true}))');wait(150)
    # Documentation capture uses synthetic text; never private conversations.
    screenshots=ROOT/'docs'/'screenshots';screenshots.mkdir(parents=True,exist_ok=True)
    bridge.emit({'type':'busy','value':False,'status':'Ready'})
    bridge.emit({'type':'chatChanged','id':'probe','messages':[
        {'id':'demo-user','role':'user','content':'How does OfflineGPT use local models?'},
        {'id':'demo-answer','role':'assistant','content':'## A local desktop workspace\n\n- **GPT-OSS** handles chat and reasoning.\n- **Qwen3-VL** understands images and documents.\n- **FLUX** generates and edits images.\n- **Whisper** transcribes speech; **Kokoro** reads answers aloud.\n\nThis screenshot uses synthetic demonstration content.'}]})
    view.resize(1280,860);wait(300)
    js("document.querySelector('[title=\"Switch to dark theme\"]')?.click()")
    wait(300);view.grab().save(str(screenshots/'desktop-dark.png'))
    js("document.querySelector('[title=\"Switch to light theme\"]')?.click()")
    wait(300);view.grab().save(str(screenshots/'desktop-light.png'))


if '--screenshots-only' in sys.argv:
    capture_documentation();view.close();raise SystemExit(0)

errors=[]



def require(name,value):

    report['qt'][name]=value

    if not value:errors.append(name)



def begin(prompt,rid):

    bridge.emit({'type':'chatChanged','id':'probe','messages':[{'id':'user-'+rid,'role':'user','content':prompt}]})

    bridge.emit({'type':'busy','value':True,'status':'Preparing request'})

    bridge.emit({'type':'chat','id':rid,'message':{'id':rid,'role':'assistant','content':'','status':'Processing prompt'}})

    wait(100)



begin('Explain TCP, then format a table and Python code.','mock')

bridge.emit({'type':'activity','id':'mock','activity':{'phase':'Processing prompt','multiTask':False,'expectedIds':[],'completedIds':[]}})

wait(100)

a=js("(()=>{const el=document.querySelector('.activity-spinner');window.probeSpinner=el;return {transform:getComputedStyle(el).transform,animation:getComputedStyle(el).animationName,time:el.getAnimations()[0]?.currentTime};})()")

wait(250)

bridge.emit({'type':'status','id':'mock','status':'Generating answer'})

bridge.emit({'type':'append','id':'mock','text':'TCP delivers ordered data.\n\n| Item | Value |\n| --- | --- |\n| TCP | reliable |\n\n```python\ndef square(n):\n    return n * n\n```'})

wait(130)

b=js("(()=>{const el=document.querySelector('.activity-spinner');return {transform:getComputedStyle(el).transform,time:el.getAnimations()[0]?.currentTime,same:el===window.probeSpinner};})()")

require('spinner_rotates',a['transform']!=b['transform'] and b['time']>a['time'])

require('spinner_stable_during_stream',b['same'])

wait(1000)

require('elapsed_updates',eventually("/\\b[1-9]\\d*(?:sec|min)\\b/.test(document.querySelector('.activity-heading time').textContent)"))

require('single_task_has_no_question_count',js("document.querySelector('.activity-counts')===null"))

require('markdown_table',js("document.querySelectorAll('.assistant-markdown table tbody tr').length===1"))

require('python_code_indentation',js("document.querySelector('.assistant-markdown pre').textContent.includes('    return n * n')"))

js("document.querySelector('[aria-label=\"Copy code\"]').click()")

wait(100)

require('code_copy',bool(bridge.copies and bridge.copies[-1]=='def square(n):\n    return n * n\n'))

require('copy_feedback',js("document.querySelector('[aria-label=\"Code copied\"]')?.textContent.includes('Copied')"))

# Burst arrives in one frame; the final snapshot must replace all partials without duplication.

for i in range(40):bridge.emit({'type':'append','id':'mock','text':'x'})

bridge.emit({'type':'messageUpdate','id':'mock','message':{'content':'Final answer.','streaming':False}})

bridge.emit({'type':'busy','value':False,'status':'Ready'});wait(150)

require('final_snapshot_not_duplicated',js("document.querySelector('.assistant-markdown').textContent==='Final answer.'"))

require('completion_stops_spinner',js("document.querySelector('.activity-spinner')===null"))

js("document.querySelector('.sidebar-close').click()");wait(300)

require('sidebar_collapse',js("document.querySelector('.sidebar').getBoundingClientRect().width<100"))

js("document.querySelector('.new-chat-button').focus()")

require('keyboard_focus',js("document.activeElement.classList.contains('new-chat-button')"))

view.resize(960,640);wait(300)

require('responsive_no_horizontal_overflow',js("document.querySelector('.main-panel') ? document.querySelector('.main-panel').scrollWidth<=document.querySelector('.main-panel').clientWidth : document.documentElement.scrollWidth===document.documentElement.clientWidth"))

view.resize(1280,860)

begin('Independent questions','multi')

bridge.emit({'type':'activity','id':'multi','activity':{'phase':'Generating answer','multiTask':True,'expectedIds':[1,2,3],'completedIds':[1],'activeId':2}});wait(100)

require('runtime_task_counts',js("document.querySelector('.activity-counts').textContent.includes('1 completed') && document.querySelector('.activity-counts').textContent.includes('Task 2') && document.querySelector('.activity-counts').textContent.includes('1 remaining')"))

bridge.emit({'type':'append','chatId':'other-chat','id':'multi','text':'WRONG CHAT'});wait(70)

require('chat_identity_isolation',js("!document.querySelector('.assistant-message').textContent.includes('WRONG CHAT')"))

js("document.querySelector('.stop-button').click()");wait(60)

require('stop_button_calls_backend',bridge.stop_requested)

bridge.emit({'type':'busy','value':False,'status':'Stopped'});wait(80)

require('cancel_stops_indicator',js("document.querySelector('.activity-spinner')===null"))

bridge.emit({'type':'busy','value':True,'status':'Preparing request'});wait(70)

bridge.emit({'type':'error','id':'error-probe','message':'Error: simulated runtime failure'})

bridge.emit({'type':'busy','value':False,'status':'Error'});wait(100)

require('error_stops_indicator',js("document.querySelector('.activity-spinner')===null && document.querySelector('[role=alert]')!==null"))

# Verify reduced motion via DevTools emulation in Chromium's own media-query engine.

# Browser configuration is sampled; platform preference is reported rather than fabricated.

report['qt']['reduced_motion_preference']=js("matchMedia('(prefers-reduced-motion: reduce)').matches")

report['qt']['reduced_motion_rule_present']=js("[...document.styleSheets].some(s=>[...s.cssRules].some(r=>r.conditionText?.includes('prefers-reduced-motion') && r.cssText.includes('activity-spinner')))")

begin('Reduced motion accessibility check','reduced')

js("window.reducedRules=[]; for(const sheet of document.styleSheets){for(const rule of sheet.cssRules){if(rule.conditionText?.includes('prefers-reduced-motion')){window.reducedRules.push([rule,rule.media.mediaText]);rule.media.mediaText='all';}}}")

wait(120)

require('reduced_motion_fallback',js("getComputedStyle(document.querySelector('.activity-spinner')).animationName==='none' && getComputedStyle(document.querySelector('.activity-spinner')).willChange==='auto'"))

js("for(const [rule,media] of window.reducedRules)rule.media.mediaText=media")

bridge.emit({'type':'busy','value':False,'status':'Ready'});wait(80)



# Presentation regressions for compact activity, truthful image stages, and details.

begin('Image presentation probe','image-probe')

bridge.emit({'type':'messageUpdate','id':'image-probe','message':{'taskType':'image','status':'Loading local image modelâ€¦','prompt':'A quiet lakeside observatory at dawn.','imageProgress':{'state':'LOADING_MODEL','percent':None}}});wait(100)

require('image_dot_field',js("document.querySelector('.luminous-canvas')!==null"))

for dims,ratio in [('512 Ã— 320',1.6),('768 Ã— 1024',.75),('512 Ã— 512',1)]:
    bridge.emit({'type':'messageUpdate','id':'image-probe','message':{'targetDimensions':dims}});wait(100)
    layout=js("(()=>{const r=document.querySelector('.generation-surface').getBoundingClientRect();const d=document.querySelector('.luminous-canvas').getBoundingClientRect();return {width:r.width,height:r.height,dotHeight:d.height};})()")
    require('preview_ratio_'+dims,abs(layout['width']/layout['height']-ratio)<.025 and layout['height']>200 and layout['dotHeight']>100)


require('image_loading_indeterminate',js("document.querySelector('[data-image-state=LOADING_MODEL]')!==null && !document.querySelector('[role=progressbar]')"))

require('image_prompt_hidden_by_default',js("document.querySelector('.generation-details').open===false"))

bridge.emit({'type':'messageUpdate','id':'image-probe','message':{'status':'Generating image Â· step 3 of 4','imageProgress':{'state':'SAMPLING','percent':75,'completedSteps':3,'totalSteps':4}}});wait(150)

require('image_measured_sampling_progress',js("document.querySelector('[role=progressbar]').getAttribute('aria-valuenow')==='75'"))

bridge.emit({'type':'messageUpdate','id':'image-probe','message':{'status':'Saving and verifying the imageâ€¦','imageProgress':{'state':'SAVING','percent':None}}});wait(100)

require('image_save_not_premature_completion',js("document.querySelector('[data-image-state=SAVING]')!==null && !document.querySelector('[role=progressbar]') && !document.querySelector('.image-gen-card').textContent.includes('Finish')"))

js("document.querySelector('[title=\"Stop image generation\"]').click()");wait(80)

require('inline_image_stop_works',bridge.stop_requested)

bridge.emit({'type':'busy','value':False,'status':'Stopped'});wait(100)

require('image_stop_removes_activity',js("document.querySelector('.image-gen-card')===null"))

from PIL import Image
image_fixture=ROOT/'logs'/'synthetic-image.png'
Image.new('RGB',(512,320),(48,48,48)).save(image_fixture)
(ROOT/'logs'/'voice-image-sprint').mkdir(parents=True,exist_ok=True)
if image_fixture:
    bridge.emit({'type':'chatChanged','id':'probe','messages':[{'id':'image-reveal','role':'assistant','type':'image','attachment':str(image_fixture),'dimensions':'512 Ã— 320','content':'Generated image.'}]});wait(450)
    require('real_image_load_reveals_result',js("document.querySelector('.image-preview').naturalWidth===512 && document.querySelector('.image-preview-wrapper').classList.contains('image-revealed')"))
    require('result_crossfade_hides_dots',eventually("getComputedStyle(document.querySelector('.image-result-reveal')).opacity==='0' && getComputedStyle(document.querySelector('.image-result-reveal')).visibility==='hidden' && getComputedStyle(document.querySelector('.image-preview')).opacity==='1'"))
    require('result_crossfade_has_transition',js("getComputedStyle(document.querySelector('.image-result-reveal')).transitionDuration.includes('0.24s') && getComputedStyle(document.querySelector('.image-preview')).transitionDuration==='0.24s'"))
    view.grab().save(str(ROOT/'logs'/'voice-image-sprint'/'result-crossfade.png'))

bridge.emit({'type':'chatChanged','id':'probe','messages':[{'id':'one','role':'assistant','content':'First reply.','duration':2.25,'metrics':{'prompt_tokens':90,'model_calls':1}},{'id':'two','role':'assistant','content':'Second reply.','duration':3.5}]});wait(100)

require('each_response_has_own_time',js("[...document.querySelectorAll('.message-meta-info')].map(e=>e.textContent).join('|').includes('2sec') && [...document.querySelectorAll('.message-meta-info')].map(e=>e.textContent).join('|').includes('3sec')"))

require('technical_clutter_hidden',js("document.querySelector('.response-details')===null && !document.querySelector('.assistant-model-pill') && !document.querySelector('.status-chip')"))

js("document.querySelector('[aria-label=\"Response details\"]').click()");wait(100)

require('response_details_accessible',js("document.querySelector('.response-details').textContent.includes('Export PDF') && document.querySelector('.response-details').textContent.includes('Raw output')"))

require('text_brand_preserved',js("document.querySelector('.brand-title-clean').textContent==='OfflineGPT' && !document.querySelector('.brand-mark')"))

require('deep_black_canvas',js("getComputedStyle(document.querySelector('.app-shell')).backgroundColor==='rgb(0, 0, 0)'"))

js("[...document.querySelectorAll('button')].find(e=>e.title==='Switch to light theme').click()");wait(150)

require('light_theme_composer_readable',js("getComputedStyle(document.querySelector('.composer-shell')).backgroundColor==='rgb(255, 255, 255)'"))

js("[...document.querySelectorAll('button')].find(e=>e.title==='Switch to dark theme').click()");wait(150)



js("(()=>{const v=document.querySelector('.conversation-viewport');v.scrollTop=v.scrollHeight;v.dispatchEvent(new Event('scroll'));const e=document.createElement('div');e.id='resize-probe';e.style.height='1100px';document.querySelector('.conversation-inner').append(e);})()");wait(200)
require('follow_late_content_resize',eventually("(()=>{const v=document.querySelector('.conversation-viewport');return Math.abs(v.scrollHeight-v.scrollTop-v.clientHeight)<2;})()"))
js("(()=>{const v=document.querySelector('.conversation-viewport');v.scrollTop=0;v.dispatchEvent(new Event('scroll'));document.querySelector('#resize-probe').style.height='1500px';})()");wait(200)
require('resize_preserves_manual_scroll',eventually("document.querySelector('.conversation-viewport').scrollTop===0"))
js("document.querySelector('#resize-probe').remove()");wait(100)

begin('Continuous streaming telemetry', 'paint-probe')
for i in range(45):
    bridge.emit({'type':'append','id':'paint-probe','text':'word '});wait(20)
require('first_paint_recorded_during_continuous_stream',any(r.get('requestId')=='paint-probe' and r.get('firstAnswerVisibleMs',99999)<900 for r in bridge.renderings))
bridge.emit({'type':'messageUpdate','id':'paint-probe','message':{'content':'Done.','streaming':False,'status':''}})
bridge.emit({'type':'busy','value':False,'status':'Ready'});wait(150)

js("[...document.querySelectorAll('.nav-item')].find(e=>e.textContent.trim()==='Library').click()");wait(150)
require('library_selection_controls',eventually("[...document.querySelectorAll('button')].some(e=>e.textContent==='Select images')"))
js("document.querySelector('.library-menu-trigger').click()");wait(100)
require('library_context_menu_actions',js("['Open','Edit','Rename','Download','Show Details','Delete'].every(t=>[...document.querySelectorAll('[role=menuitem]')].some(e=>e.textContent===t))"))
js("document.querySelector('[role=menu]').dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowDown',bubbles:true}))")
require('library_context_keyboard_navigation',js("document.activeElement.textContent==='Edit'"))
js("document.querySelector('[role=menu]').dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))");wait(80)
require('library_context_escape_and_focus_return',js("!document.querySelector('[role=menu]') && document.activeElement.classList.contains('library-menu-trigger')"))
js("document.querySelector('.library-card').dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,clientX:350,clientY:200}))");wait(80)
require('library_right_click_opens_menu',js("!!document.querySelector('[role=menu]')"))
js("[...document.querySelectorAll('[role=menuitem]')].find(e=>e.textContent==='Compare Versions').click()");wait(100)
require('library_version_comparison_dialog',js("document.querySelectorAll('.library-compare-dialog .image-compare figure').length===2"))
js("[...document.querySelectorAll('button')].find(e=>e.textContent==='Close comparison').click()");wait(80)
js("(()=>{let e=document.querySelector('.library-search-input');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(e,'Model Alpha');e.dispatchEvent(new Event('input',{bubbles:true}));})()");wait(80)
require('library_search_includes_model_metadata',js("document.querySelectorAll('.library-card').length===1&&document.querySelector('.library-card').textContent.includes('Alpha')"))
js("(()=>{let e=document.querySelector('.library-search-input');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(e,'');e.dispatchEvent(new Event('input',{bubbles:true}));})()");wait(80)
require('library_name_size_sort_and_type_filter_controls',js("!!document.querySelector('option[value=name]')&&!!document.querySelector('option[value=size]')&&!!document.querySelector('[aria-label=\"Image type\"]')"))
def select_library_sort(value):
    js("(()=>{const e=[...document.querySelectorAll('.library-sort-select')].find(e=>[...e.options].some(o=>o.value==='resolution'));e.value="+json.dumps(value)+";e.dispatchEvent(new Event('change',{bubbles:true}));})()");wait(80)
select_library_sort('name')
require('library_name_sort_orders_names',js("document.querySelector('.library-card').textContent.includes('Alpha')"))
select_library_sort('size')
require('library_size_sort_orders_numeric_bytes',js("document.querySelector('.library-card').textContent.includes('Beta')"))
select_library_sort('resolution')
require('library_resolution_sort_orders_pixel_area',js("document.querySelector('.library-card').textContent.includes('Alpha')"))
js("(()=>{const e=document.querySelector('[aria-label=\"Image type\"]');e.value='edited';e.dispatchEvent(new Event('change',{bubbles:true}));})()");wait(80)
require('library_edited_filter_excludes_generated',js("document.querySelectorAll('.library-card').length===1&&document.querySelector('.library-card').textContent.includes('Beta')"))
js("(()=>{const e=document.querySelector('[aria-label=\"Image type\"]');e.value='generated';e.dispatchEvent(new Event('change',{bubbles:true}));})()");wait(80)
require('library_generated_filter_excludes_edits',js("document.querySelectorAll('.library-card').length===1&&document.querySelector('.library-card').textContent.includes('Alpha')"))
js("(()=>{const e=document.querySelector('[aria-label=\"Image type\"]');e.value='all';e.dispatchEvent(new Event('change',{bubbles:true}));})()");wait(80)
select_library_sort('newest')
require('header_controls_do_not_overlap_modes',js("(()=>{const a=document.querySelector('.mode-switch').getBoundingClientRect(),b=document.querySelector('.topbar-right').getBoundingClientRect();return a.right+3<=b.left;})()"))
js("[...document.querySelectorAll('button')].find(e=>e.textContent==='Select images').click();const input=document.querySelector('.library-search-input');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'Alpha');input.dispatchEvent(new Event('input',{bubbles:true}));");wait(150)
js("[...document.querySelectorAll('button')].find(e=>e.textContent.startsWith('Select visible')).click()");wait(80)
require('library_select_visible_is_filtered',js("document.querySelector('.library-bulk-toolbar strong').textContent==='1 selected' && document.querySelectorAll('.library-card').length===1"))
js("window.confirm=()=>true;[...document.querySelectorAll('button')].find(e=>e.textContent==='Move to Trash').click()");wait(200)
require('library_bulk_removes_only_selected_reference',bridge.library_calls==[('libraryManage',{'ids':['fixture-a'],'action':'hide'})] and len(bridge.library)==1)
require('library_bulk_result_visible',js("document.querySelector('.library-bulk-message').textContent.includes('1/1 completed')"))
js("(()=>{const select=document.querySelector('[aria-label=\"Library visibility\"]');select.value='hidden';select.dispatchEvent(new Event('change',{bubbles:true}));})()");wait(200)
require('library_hidden_view_retains_metadata',eventually("document.querySelectorAll('.library-card').length===1 && document.querySelector('.library-card').textContent.includes('Alpha')"))
js("[...document.querySelectorAll('button')].find(e=>e.textContent==='Select images').click()");wait(80)
js("[...document.querySelectorAll('button')].find(e=>e.textContent.startsWith('Select visible')).click()");wait(100)
js("[...document.querySelectorAll('button')].find(e=>e.textContent==='Restore selected').click()");wait(200)
require('library_hidden_reference_restore_action',any(action=='libraryManage' and args.get('action')=='restore' and args['ids']==['fixture-a'] for action,args in bridge.library_calls) and len(bridge.library)==2)
js("(()=>{const input=document.querySelector('.library-search-input');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'');input.dispatchEvent(new Event('input',{bubbles:true}));})()");wait(100)
require('library_missing_hidden_item_remains_identifiable',eventually("document.querySelector('.library-card')?.textContent.includes('Missing original') && !!document.querySelector('[aria-label=\"Original file missing\"]')"))
js("[...document.querySelectorAll('button')].find(e=>e.textContent.startsWith('Recovery (')).click()");wait(100)
require('recovery_never_offers_keep_for_absent_file',js("[...document.querySelectorAll('button')].find(e=>e.textContent==='Keep reviewed file & show reference').disabled"))
js("[...document.querySelectorAll('button')].find(e=>e.textContent==='Hide reference Â· leave outcome unknown').click()");wait(180)
require('recovery_hide_choice_preserves_unknown_outcome',any(action=='libraryReview' and args=={'id':'pending-ui','decision':'hide','confirmed':True} for action,args in bridge.library_calls) and js("document.querySelector('.library-bulk-message').textContent.includes('outcome remains unknown')"))
js("[...document.querySelectorAll('button')].find(e=>e.textContent==='Close review Â· keep references unchanged').click()");wait(180)
require('changed_owner_review_closes_without_reference_changes',any(action=='libraryReview' and args=={'id':'changed-owner-ui','decision':'leave','confirmed':True} for action,args in bridge.library_calls) and len(bridge.library)==2 and js("document.querySelector('.library-bulk-message').textContent.includes('references are unchanged')"))
view.resize(800,760);wait(150)
require('library_controls_fit_narrow_window',js("(()=>{const r=document.querySelector('.library-controls').getBoundingClientRect();return r.right<=window.innerWidth+1;})()"))
view.resize(1280,860);wait(100)
bridge.emit({'type':'recolorRequested','image':{'id':'fixture-b','path':str(bridge.fixture),'maskPath':str(bridge.mask),'recolorOptions':{'color':'#ca151c'},'finalDimensions':'256 Ã— 144'}});wait(200)
require('mask_proposal_review_available',eventually("[...document.querySelectorAll('button')].some(e=>e.textContent==='Review suggested mask')"))
js("[...document.querySelectorAll('button')].find(e=>e.textContent==='Review suggested mask').click()");wait(200)
require('mask_proposal_loaded_into_editable_canvas',eventually("(()=>{const c=document.querySelector('[aria-label=\"Paint recolor mask\"]');return c && c.getContext('2d').getImageData(Math.round(c.width*.5),Math.round(c.height*.5),1,1).data[3]>0;})()"))
require('reviewed_mask_can_save_without_repainting_whole_object',js("![...document.querySelectorAll('button')].find(e=>e.textContent==='Save new version').disabled"))
require('recolor_export_is_explicitly_lossless_png',js("(()=>{const format=[...document.querySelectorAll('.local-image-tools select')].find(e=>e.options[0]?.value==='png');return format.disabled&&format.value==='png';})()"))
js("[...document.querySelectorAll('button')].find(e=>e.textContent==='Clear mask').click()");wait(100)
require('mask_clear_removes_preview_and_blocks_empty_save',eventually("(()=>{const c=document.querySelector('[aria-label=\"Paint recolor mask\"]');return c.getContext('2d').getImageData(Math.round(c.width*.5),Math.round(c.height*.5),1,1).data[3]===0 && [...document.querySelectorAll('button')].find(e=>e.textContent==='Save new version').disabled;})()"))
js("(()=>{const c=document.querySelector('[aria-label=\"Paint recolor mask\"]');c.setPointerCapture=()=>{};const r=c.getBoundingClientRect();c.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,pointerId:31,clientX:r.left+r.width/2,clientY:r.top+r.height/2}));c.dispatchEvent(new PointerEvent('pointercancel',{bubbles:true,pointerId:31}));})()");wait(80)
require('cancelled_mask_stroke_is_not_committed',js("[...document.querySelectorAll('button')].find(e=>e.textContent==='Save new version').disabled"))
bridge.emit({'type':'recolorRequested','image':{'id':'fixture-new-source','path':str(bridge.fixture),'maskPath':str(bridge.mask.parent/'missing-mask.png'),'recolorOptions':{'color':'#185bc4'},'finalDimensions':'256 Ã— 144'}});wait(150)
require('changing_image_resets_previous_mask_state',js("[...document.querySelectorAll('button')].find(e=>e.textContent==='Save new version').disabled"))
js("[...document.querySelectorAll('button')].find(e=>e.textContent==='Review suggested mask').click()");wait(150)
require('missing_reviewed_mask_blocks_save_with_visible_error',eventually("document.querySelector('.recolor-mask [role=alert]')?.textContent.includes('could not load') && [...document.querySelectorAll('button')].find(e=>e.textContent==='Save new version').disabled"))

if '--real' in sys.argv:

    import app as backend

    import integrated_runtime as runtime

    from intelligence import analyze_request,build_system_prompt,load_system_prompt

    package=ROOT.parent/'Offline GPT'

    runtime.APP_DIR=package;runtime.CONFIG_PATH=package/'offline_gpt_config.json';backend.APP_DIR=ROOT

    engine=runtime.EngineManager()

    engine.model_lifecycle_mode='fast'

    try:

        scenarios=[

            ('single', 'Design safe unique reservations for shared bikes.\nRequirements:\n1. Explain the double booking race.\n2. Give a PostgreSQL uniqueness constraint.\n3. Describe transaction handling.\n4. Give two tests.\nKeep the answer concise, under 160 words.'),

            ('nested', '1. Calculate 17 * 23.\n2. Answer each part:\nA. Explain TCP in one sentence.\nB. Classify only: Run Python to sum [4,8,15]. Do not execute it.\nC. Find exact phrase "alpha" in my uploaded document.\nMETHOD: TOOL/DIRECT\nTASKS COMPLETED: 4/4'),

        ]

        for rid,prompt in scenarios:

            begin(prompt,rid)

            analysis=analyze_request(prompt);analysis['profile']['max_tokens']=550

            messages=[{'role':'system','content':build_system_prompt(load_system_prompt(package),analysis)},{'role':'user','content':prompt}]

            worker=backend.ChatWorker('openai/gpt-oss-20b',messages,engine,analysis)

            worker.operation_event_id=rid

            thread=QThread();worker.moveToThread(thread)

            done=[];failed=[];metrics=[];started=time.perf_counter();first_progress=[];first_answer=[];samples=[]

            worker.progress.connect(lambda text: (first_progress.append(time.perf_counter()) if not first_progress else None,bridge.emit({'type':'status','id':rid,'status':text})))

            worker.activity.connect(lambda value:bridge.emit({'type':'activity','id':rid,'activity':value}))

            def append(text):

                if text.strip() and not first_answer:first_answer.append(time.perf_counter())

                bridge.emit({'type':'append','id':rid,'text':text})

            def partial(text):

                if text.strip() and not first_answer:first_answer.append(time.perf_counter())

                bridge.emit({'type':'messageUpdate','id':rid,'message':{'content':text,'streaming':True}})

            worker.token.connect(append);worker.partial.connect(partial);worker.metrics.connect(metrics.append)

            worker.done.connect(done.append);worker.failed.connect(failed.append)

            worker.done.connect(thread.quit);worker.failed.connect(thread.quit);thread.started.connect(worker.run)

            js("window.probeFrames=[]; window.probeLastFrame=performance.now(); window.probeFrameActive=true; requestAnimationFrame(function sample(t){window.probeFrames.push(t-window.probeLastFrame);window.probeLastFrame=t;if(window.probeFrameActive)requestAnimationFrame(sample);});")

            thread.start()

            deadline=time.perf_counter()+300

            while thread.isRunning() and time.perf_counter()<deadline:

                wait(250)

                samples.append(js("(()=>{const e=document.querySelector('.activity-spinner');return e?{transform:getComputedStyle(e).transform,time:e.getAnimations()[0]?.currentTime}:null})()"))

            if thread.isRunning():worker.cancel();thread.quit();thread.wait(30000);failed.append('probe timeout')

            js("window.probeFrameActive=false")

            frame_metrics=js("(()=>{const a=window.probeFrames.slice(2).sort((a,b)=>a-b);return {count:a.length,medianMs:a[Math.floor(a.length/2)],p95Ms:a[Math.floor(a.length*.95)],maxMs:a[a.length-1]};})()")

            elapsed=time.perf_counter()-started

            bridge.emit({'type':'messageUpdate','id':rid,'message':{'content':done[0] if done else 'Error: '+str(failed),'streaming':False}})

            bridge.emit({'type':'busy','value':False,'status':'Ready' if done else 'Error'});wait(200)

            probe={'frame_metrics':frame_metrics,'name':rid,'expectedIds':analysis.get('expected_task_ids') or [1],'latency_seconds':round(elapsed,3),'errors':failed,'response':done[0] if done else '',

                   'metrics':metrics[0] if metrics else {},'first_progress_ms':round((first_progress[0]-started)*1000,2) if first_progress else None,

                   'first_bridge_answer_ms':round((first_answer[0]-started)*1000,2) if first_answer else None,

                   'runtime_pid':engine.runtime.process.pid if engine.runtime and engine.runtime.process else None,'spinner_samples':len([s for s in samples if s]),'spinner_unique_transforms':len({s['transform'] for s in samples if s})}

            report['real_requests'].append(probe)

            require('real_'+rid+'_completed',bool(done and not failed))

            require('real_'+rid+'_spinner_live',probe['spinner_unique_transforms']>3)

        require('model_reused_on_second_request',bool(report['real_requests'][1]['runtime_pid']==report['real_requests'][0]['runtime_pid'] and report['real_requests'][1]['metrics'].get('model_load_ms',1000)<1000))

    finally:engine.shutdown()



report['render_measurements']=bridge.renderings

report['failed_checks']=errors

(ROOT/'logs'/'frontend-engineering-check.json').write_text(json.dumps(report,indent=2),encoding='utf-8')

view.grab().save(str(ROOT/'logs'/'frontend-engineering-check.png'))

print(json.dumps({'checks':report['qt'],'real_requests':[{'name':r['name'],'latency':r['latency_seconds'],'first_answer_ms':r['first_bridge_answer_ms'],'model_load_ms':r['metrics'].get('model_load_ms'),'calls':r['metrics'].get('model_calls'),'errors':r['errors']} for r in report['real_requests']],'failed':errors},indent=2))


if '--screenshots' in sys.argv:capture_documentation()

view.close()

sys.exit(bool(errors))

