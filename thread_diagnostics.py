"""GIL-held bounded diagnostic sampler; no native timed traceback watchdog."""
import sys
import threading
import time
import traceback

class StackSampler:
    def __init__(self,stream,delay=450,repeat=False):
        if delay<=0:raise ValueError('Positive sampling interval required')
        self.stream=stream;self.delay=delay;self.repeat=repeat;self.stop_event=threading.Event();self.samples=0;self.error=None;self.thread=None
    def _run(self):
        try:
            while not self.stop_event.wait(self.delay):
                frames=sys._current_frames()
                try:
                    self.stream.write('PYTHON STACK SNAPSHOT '+str(time.monotonic())+'\n')
                    for tid,frame in list(frames.items())[:32]:
                        if tid!=threading.get_ident():
                            self.stream.write('Thread '+str(tid)+'\n');traceback.print_stack(frame,limit=40,file=self.stream)
                    self.stream.flush();self.samples+=1
                finally:del frames
                if not self.repeat:break
        except BaseException as exc:self.error=repr(exc)
    def __enter__(self):
        if self.thread is not None:raise RuntimeError('Sampler cannot be restarted')
        self.thread=threading.Thread(target=self._run,name='OfflineGPT-owned-stack-sampler',daemon=True)
        try:self.thread.start()
        except BaseException:self.thread=None;raise
        return self
    def __exit__(self,*exc):
        self.stop_event.set();self.thread.join(5)
        if self.thread.is_alive():raise RuntimeError('Owned diagnostic sampler did not stop; retain its output stream')


class ThreadDiagnostics:
    """Own diagnostic file and sampler, preserving an existing fatal handler."""
    def __init__(self,path,interval=20):
        from pathlib import Path
        self.path=Path(path);self.interval=interval;self.stream=None;self.sampler=None;self.owns_fatal=False;self.closed=False
    def start(self):
        import atexit
        import faulthandler
        if self.stream is not None or self.closed:raise RuntimeError('Diagnostic session cannot restart')
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.stream=self.path.open('a',encoding='utf-8')
        try:
            self.owns_fatal=not faulthandler.is_enabled()
            if self.owns_fatal:faulthandler.enable(file=self.stream)
            self.sampler=StackSampler(self.stream,self.interval,True)
            self.sampler.__enter__();atexit.register(self.close)
            return self
        except BaseException:
            self.close();raise
    def close(self):
        import atexit
        import faulthandler
        if self.closed:return
        if self.sampler and self.sampler.thread:self.sampler.__exit__(None,None,None)
        if self.owns_fatal:faulthandler.disable();self.owns_fatal=False
        if self.stream:self.stream.close()
        atexit.unregister(self.close);self.closed=True
