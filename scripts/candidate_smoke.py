"""Bounded real-EXE shell/bridge smoke, isolated empty data, no model requests.

This proves startup and the real Qt/React bridge, not inference or release fitness.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import time
import urllib.request

class SmokeError(RuntimeError):pass
def sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
def ordinary(path):
    path=Path(path)
    if not path.is_absolute():raise SmokeError('Absolute isolated paths required')
    for part in (path,*path.parents):
        if part.exists() and (part.is_symlink() or getattr(part.lstat(),'st_file_attributes',0)&0x400):raise SmokeError('Linked test paths are not accepted')
    return path.resolve()
def preflight(package,profile,expected):
    package=ordinary(package);profile=ordinary(profile)
    if profile.exists() or profile.is_relative_to(package) or package.is_relative_to(profile):raise SmokeError('A new disjoint test profile is required')
    if len(expected)!=64 or sha(package/'Offline GPT.exe')!=expected:raise SmokeError('Candidate identity differs from the approved EXE')
    if not (package/'ui/index.html').is_file():raise SmokeError('Candidate UI is missing')
    return package,profile
def run(package,profile,expected):
    package,profile=preflight(package,profile,expected)
    import psutil
    from PySide6.QtCore import QCoreApplication,QEventLoop,QTimer,QUrl
    from PySide6.QtWebSockets import QWebSocket
    from PySide6.QtNetwork import QAbstractSocket
    if psutil.virtual_memory().available<2*1024**3:raise SmokeError('Defer shell smoke until 2 GiB RAM is free')
    # Do not compete with a user app or native model; never close one.
    names={'offline gpt.exe','llama-server.exe','sd-cli.exe','whisper-server.exe'}
    if any((p.info['name'] or '').lower() in names for p in psutil.process_iter(['name'])):raise SmokeError('Existing app/model process present; defer')
    profile.mkdir(parents=True)
    cfg={'chat_backend':'bundled','gpt_oss_gguf':'','vision_model':'','vision_projector':'','lms_exe':'','sd_cli':'',
         'flux_model':'','flux_encoder':'','flux_vae':'','flux_upscaler':'','voice_server_path':'','voice_model_path':'',
         'kokoro_model_path':'','kokoro_voices_path':'','backup_schedule_enabled':False,'image_output_directory':str(profile/'generated')}
    (profile/'offline_gpt_config.json').write_text(json.dumps(cfg),encoding='utf-8')
    app=QCoreApplication.instance() or QCoreApplication([])
    def wait(ms):
        loop=QEventLoop();QTimer.singleShot(ms,loop.quit);loop.exec()
    with socket.socket() as reservation:reservation.bind(('127.0.0.1',0));port=reservation.getsockname()[1]
    env={**os.environ,'OFFLINE_GPT_DATA_DIR':str(profile),'OFFLINE_GPT_CAPTURE_FITTED_PROMPTS':'0','QT_OPENGL':'software',
         'QTWEBENGINE_REMOTE_DEBUGGING':'127.0.0.1:'+str(port),'QTWEBENGINE_CHROMIUM_FLAGS':'--disable-gpu --remote-allow-origins=http://localhost'}
    ws=QWebSocket('http://localhost');replies={};sequence=0;process=None;observed={}
    report={'started':time.time(),'exeSha256':expected,'scope':'Real isolated EXE shell and bridge only; no inference requested','checks':{},'modelRequestsIssued':0}
    started=time.monotonic();failure=None
    def monitor():
        if time.monotonic()-started>40:raise SmokeError('Bounded shell smoke timed out')
        if psutil.virtual_memory().available<512*1024**2:raise SmokeError('Free RAM crossed the 512 MiB stop boundary')
        if process is not None:
            try:
                for child in psutil.Process(process.pid).children(recursive=True):
                    observed[child.pid]=child.create_time()
                    if child.name().lower() in {'llama-server.exe','sd-cli.exe','whisper-server.exe'}:raise SmokeError('Unexpected native model process during shell smoke')
            except psutil.NoSuchProcess:pass
    def receive(raw):
        value=json.loads(raw)
        if 'id' in value:replies[value['id']]=value
        # Dialogs are deliberately not auto-confirmed.
    ws.textMessageReceived.connect(receive)
    def command(method,params=None):
        nonlocal sequence
        sequence+=1;number=sequence
        ws.sendTextMessage(json.dumps({'id':number,'method':method,'params':params or {}}))
        end=time.monotonic()+5
        while number not in replies and time.monotonic()<end:monitor();wait(20)
        if number not in replies:raise SmokeError('DevTools command did not return')
        value=replies.pop(number)
        if value.get('error'):raise SmokeError('DevTools command failed')
        return value.get('result',{})
    def js(expression):
        value=command('Runtime.evaluate',{'expression':expression,'returnByValue':True,'awaitPromise':True})
        if value.get('exceptionDetails'):raise SmokeError('Frontend probe raised an exception')
        return value.get('result',{}).get('value')
    def check(name,value):
        report['checks'][name]=bool(value)
        if not value:raise SmokeError('Failed shell assertion: '+name)
    try:
        process=subprocess.Popen([str(package/'Offline GPT.exe')],cwd=package,env=env)
        observed[process.pid]=psutil.Process(process.pid).create_time()
        target=None;end=time.monotonic()+20
        while time.monotonic()<end:
            monitor()
            if process.poll() is not None:raise SmokeError('Candidate exited before frontend startup')
            try:
                with urllib.request.urlopen('http://127.0.0.1:'+str(port)+'/json',timeout=.5) as stream:targets=json.load(stream)
                target=next(t for t in targets if t.get('type')=='page' and t.get('url')==(package/'ui/index.html').as_uri())
                break
            except (OSError,ValueError,StopIteration):wait(100)
        if not target:raise SmokeError('Expected candidate page was not exposed')
        ws.open(QUrl(target['webSocketDebuggerUrl']));end=time.monotonic()+5
        while ws.state()!=QAbstractSocket.ConnectedState and time.monotonic()<end:monitor();wait(20)
        check('devtools_connected',ws.state()==QAbstractSocket.ConnectedState)
        # Locate the existing bridge held by React without creating a second
        # QWebChannel or altering the application's transport callbacks.
        bridge="(()=>{const r=document.querySelector('#root');if(!r)return false;const f=r[Object.keys(r).find(k=>k.startsWith('__reactContainer$'))];const seen=new Set();function find(n){if(!n||seen.has(n))return null;seen.add(n);for(let h=n.memoizedState;h;h=h.next){if(h.memoizedState?.getChats&&h.memoizedState?.closeApp)return h.memoizedState;}return find(n.child)||find(n.sibling)||find(n.alternate);}window.candidateSmokeBridge=find(f);return !!window.candidateSmokeBridge;})()"
        ready=False;end=time.monotonic()+5
        while time.monotonic()<end:
            if js(bridge):ready=True;break
            monitor();wait(100)
        check('real_webchannel_bridge',ready)
        check('composer_visible',js("(()=>{const e=document.querySelector('textarea');return !!e&&e.getBoundingClientRect().width>100;})()"))
        chats=js('new Promise(resolve=>window.candidateSmokeBridge.getChats(raw=>resolve(JSON.parse(raw))))')
        check('chat_store_bridge',isinstance(chats,list))
        monitor();check('no_observed_native_model_child',True)
        js('window.candidateSmokeBridge.closeApp()')
        end=time.monotonic()+8
        while process.poll() is None and time.monotonic()<end:monitor();wait(50)
        check('normal_exit',process.poll()==0)
        with sqlite3.connect((profile/'offline_gpt.sqlite3').as_uri()+'?mode=ro',uri=True) as database:
            check('isolated_database_integrity',database.execute('PRAGMA integrity_check').fetchone()[0]=='ok')
            check('no_messages_in_fresh_profile',database.execute('SELECT COUNT(*) FROM messages').fetchone()[0]==0)
            check('bridge_database_chat_ids_match',{row[0] for row in database.execute('SELECT id FROM chats')}=={row['id'] for row in chats})
    except Exception as error:failure=str(error)
    finally:
        ws.close()
        # Only processes observed as descendants of this test launch qualify.
        # PID reuse is checked before terminating a failed/hung owned process.
        if process and process.poll() is None:
            report['forcedOwnedCleanup']=True
            for pid,created in reversed(list(observed.items())):
                try:
                    owned=psutil.Process(pid)
                    if owned.create_time()==created:owned.terminate()
                except psutil.Error:pass
            try:process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    owned=psutil.Process(process.pid)
                    if owned.create_time()==observed[process.pid]:owned.kill();process.wait(timeout=2)
                except (psutil.Error,subprocess.TimeoutExpired):pass
        end=time.monotonic()+3
        while time.monotonic()<end:
            alive=[]
            for pid,created in observed.items():
                try:
                    if psutil.Process(pid).create_time()==created:alive.append(pid)
                except psutil.Error:pass
            if not alive:break
            wait(50)
        report.update(status='FAILED' if failure or alive else 'PASSED',error=failure,remainingOwnedPids=alive,elapsedSeconds=time.monotonic()-started)
        (profile/'shell-smoke.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report
def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--package',type=Path,required=True);parser.add_argument('--profile',type=Path,required=True);parser.add_argument('--sha256',required=True)
    args=parser.parse_args();result=run(args.package,args.profile,args.sha256);print(json.dumps(result,indent=2));return 0 if result['status']=='PASSED' else 2
if __name__=='__main__':raise SystemExit(main())
