"""Real bounded Windows Job Object tests; synthetic processes, no AI models."""
import importlib.util,os,json,subprocess,sys,tempfile,time,unittest
from pathlib import Path
spec=importlib.util.spec_from_file_location('bounded_build_process',Path(__file__).resolve().parents[1]/'scripts/build_process.py')
process=importlib.util.module_from_spec(spec);spec.loader.exec_module(process)
@unittest.skipUnless(os.name=='nt','Windows Job Objects only')
class ProcessTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name).resolve()
        self.env={k:v for k,v in os.environ.items() if k.upper() in {'SYSTEMROOT','WINDIR','PATH','TEMP','TMP'}}
    def run_child(self,code,seconds=5,monitor=lambda:None):return process.run([sys.executable,'-I','-S','-c',code],self.root,self.env,self.root/'output.log',seconds,monitor)
    def test_real_normal_exit_and_tree_accounting(self):
        result=self.run_child('print("synthetic bounded child")')
        self.assertEqual(result['returnCode'],0);self.assertEqual(result['remainingOwnedProcesses'],0)
        self.assertIn('synthetic bounded child',(self.root/'output.log').read_text())
    def test_timeout_terminates_owned_descendant(self):
        marker=self.root/'child.pid'
        code='import subprocess,sys,time;from pathlib import Path;p=subprocess.Popen([sys.executable,"-I","-S","-c","import time;time.sleep(20)"]);Path('+repr(str(marker))+').write_text(str(p.pid));time.sleep(20)'
        with self.assertRaises(process.ProcessError):self.run_child(code,1)
        self.assertTrue(marker.exists());self.assert_gone(int(marker.read_text()))
    def assert_gone(self,pid):
        import ctypes as C
        from ctypes import wintypes as W
        k=C.WinDLL('kernel32',use_last_error=True);k.OpenProcess.argtypes=[W.DWORD,W.BOOL,W.DWORD];k.OpenProcess.restype=W.HANDLE;k.GetExitCodeProcess.argtypes=[W.HANDLE,C.POINTER(W.DWORD)];k.CloseHandle.argtypes=[W.HANDLE]
        h=k.OpenProcess(0x1000,False,pid)
        if h:
            try:
                value=W.DWORD();self.assertTrue(k.GetExitCodeProcess(h,C.byref(value)));self.assertNotEqual(value.value,259)
            finally:k.CloseHandle(h)
    def test_cancellation_callback_stops_owned_process(self):
        def cancel():raise process.ProcessError('Synthetic cancellation')
        with self.assertRaisesRegex(process.ProcessError,'Synthetic cancellation'):self.run_child('import time;time.sleep(20)',5,cancel)
    def test_controller_crash_closes_job_and_kills_child(self):
        marker=self.root/'owned.pid';worker=self.root/'worker.py'
        worker.write_text('import importlib.util,os,sys\nfrom pathlib import Path\ns=importlib.util.spec_from_file_location("p",'+repr(str(Path(process.__file__)))+');p=importlib.util.module_from_spec(s);s.loader.exec_module(p)\np.run([sys.executable,"-I","-S","-c",'+repr('import os,time;from pathlib import Path;Path('+repr(str(marker))+').write_text(str(os.getpid()));time.sleep(20)')+'],Path('+repr(str(self.root))+'),dict(os.environ),Path('+repr(str(self.root/'crash.log'))+'),25)\n')
        child=subprocess.Popen([sys.executable,'-I','-S',str(worker)],env=self.env)
        try:
            end=time.monotonic()+8
            while not marker.exists() and time.monotonic()<end:time.sleep(.05)
            self.assertTrue(marker.exists());pid=int(marker.read_text());child.terminate();child.wait(timeout=3);time.sleep(.2);self.assert_gone(pid)
        finally:
            if child.poll() is None:child.kill();child.wait(timeout=3)
if __name__=='__main__':unittest.main()
