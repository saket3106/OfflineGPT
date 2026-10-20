"""Real bounded Windows Job Object tests; synthetic processes, no AI models."""
import importlib.util,os,json,subprocess,sys,tempfile,time,unittest
from unittest.mock import Mock,patch
from pathlib import Path
spec=importlib.util.spec_from_file_location('bounded_build_process',Path(__file__).resolve().parents[1]/'scripts/build_process.py')
process=importlib.util.module_from_spec(spec);spec.loader.exec_module(process)

class CleanupContractTests(unittest.TestCase):
    def kernel(self,active):
        kernel=Mock();kernel.TerminateJobObject.return_value=1
        kernel.WaitForSingleObject.return_value=0
        observed=iter(active)
        def query(job,kind,accounting,size,reserved):
            accounting._obj.active=next(observed);return 1
        kernel.QueryInformationJobObject.side_effect=query
        return kernel
    def clock(self):
        now=[0.0]
        return lambda:now[0],lambda delay:now.__setitem__(0,now[0]+delay)
    def test_root_exit_does_not_prove_descendant_cleanup(self):
        kernel=self.kernel([2,1,1,0]);clock,sleep=self.clock()
        process._terminate_owned_job(kernel,11,22,clock=clock,sleep=sleep)
        self.assertEqual(kernel.QueryInformationJobObject.call_count,4)
        kernel.TerminateJobObject.assert_called_once_with(11,2)
    def test_drain_timeout_is_explicit_failure(self):
        kernel=self.kernel([1]*200);clock,sleep=self.clock()
        with self.assertRaisesRegex(process.ProcessError,'cleanup.*deadline'):
            process._terminate_owned_job(kernel,11,22,clock=clock,sleep=sleep)
        self.assertLessEqual(clock(),5.1)
    def test_termination_failure_is_not_hidden(self):
        kernel=self.kernel([0]);kernel.TerminateJobObject.return_value=0
        with self.assertRaisesRegex(process.ProcessError,'TerminateJobObject'):
            process._terminate_owned_job(kernel,11,22)
        kernel.QueryInformationJobObject.assert_not_called()
    def test_failed_accounting_cannot_prove_cleanup(self):
        kernel=self.kernel([0]);kernel.QueryInformationJobObject.side_effect=None
        kernel.QueryInformationJobObject.return_value=0
        with self.assertRaisesRegex(process.ProcessError,'cleanup accounting'):
            process._terminate_owned_job(kernel,11,22)
    def test_failed_wait_cannot_prove_cleanup(self):
        kernel=self.kernel([0]);kernel.WaitForSingleObject.return_value=0xFFFFFFFF
        with self.assertRaisesRegex(process.ProcessError,'cleanup wait'):
            process._terminate_owned_job(kernel,11,22)
        kernel.QueryInformationJobObject.assert_not_called()
    def test_wait_timeout_is_not_reported_as_cleanup(self):
        kernel=self.kernel([0]);kernel.WaitForSingleObject.return_value=0x102
        with self.assertRaisesRegex(process.ProcessError,'cleanup wait'):
            process._terminate_owned_job(kernel,11,22)
        kernel.QueryInformationJobObject.assert_not_called()
    def test_empty_job_after_creation_failure_is_safe(self):
        kernel=self.kernel([0])
        process._terminate_owned_job(kernel,11,None)
        kernel.WaitForSingleObject.assert_not_called()

@unittest.skipUnless(os.name=='nt','Windows API boundary fixture')
class CleanupBoundaryTests(unittest.TestCase):
    def test_cancel_public_run_waits_for_descendants_and_closes_handles(self):
        # Controlled Win32 boundary: root is signalled first; the descendant
        # remains active until the second accounting observation. No processes
        # are launched here, and no fixed sleep stands in for completion.
        kernel=Mock();kernel.CreateJobObjectW.return_value=11
        kernel.SetInformationJobObject.return_value=1
        def initialize(attributes,count,flags,size):size._obj.value=128;return 1
        kernel.InitializeProcThreadAttributeList.side_effect=initialize
        kernel.UpdateProcThreadAttribute.return_value=1
        def create(*args):
            info=args[-1]._obj;info.process=22;info.thread=33;info.pid=44;info.tid=55
            return 1
        kernel.CreateProcessW.side_effect=create;kernel.ResumeThread.return_value=1
        kernel.WaitForSingleObject.side_effect=[0x102,0]
        state={'active':1,'queries':0}
        kernel.TerminateJobObject.return_value=1
        def query(job,kind,accounting,size,reserved):
            state['queries']+=1
            if state['queries']>=2:state['active']=0
            accounting._obj.active=state['active'];return 1
        kernel.QueryInformationJobObject.side_effect=query
        def cancel():raise process.ProcessError('Boundary cancellation')
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with patch.object(process.C,'WinDLL',return_value=kernel),patch.object(process.time,'sleep'):
                with self.assertRaisesRegex(process.ProcessError,'Boundary cancellation'):
                    process.run([sys.executable,'-I','-S','-c','pass'],root,{},root/'boundary.log',5,cancel)
            self.assertEqual(state['active'],0,'Root exit alone left an active descendant')
            self.assertEqual(state['queries'],2)
            self.assertEqual([call.args[0] for call in kernel.CloseHandle.call_args_list],[33,22,11])

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
    def test_cancelled_descendant_releases_inherited_log_before_return(self):
        marker=self.root/'cancel-tree.json'
        code='import subprocess,sys,time,os,json;from pathlib import Path;p=subprocess.Popen([sys.executable,"-I","-S","-c","import time;time.sleep(20)"]);Path('+repr(str(marker))+').write_text(json.dumps([os.getpid(),p.pid]));time.sleep(20)'
        def cancel_after_spawn():
            if marker.exists() and marker.stat().st_size:
                raise process.ProcessError('Synthetic tree cancellation')
        with self.assertRaisesRegex(process.ProcessError,'Synthetic tree cancellation'):
            self.run_child(code,5,cancel_after_spawn)
        for pid in json.loads(marker.read_text()):self.assert_gone(pid)
        # The old failure only waited for the root: Windows could still have
        # a descendant holding this inherited file, failing temporary cleanup.
        log=self.root/'output.log';renamed=self.root/'released.log'
        log.rename(renamed);renamed.unlink()
    def test_controller_crash_closes_job_and_kills_child(self):
        marker=self.root/'owned.pid';worker=self.root/'worker.py'
        worker.write_text('import importlib.util,os,sys\nfrom pathlib import Path\ns=importlib.util.spec_from_file_location("p",'+repr(str(Path(process.__file__)))+');p=importlib.util.module_from_spec(s);s.loader.exec_module(p)\np.run([sys.executable,"-I","-S","-c",'+repr('import os,time;from pathlib import Path;Path('+repr(str(marker))+').write_text(str(os.getpid()));time.sleep(20)')+'],Path('+repr(str(self.root))+'),dict(os.environ),Path('+repr(str(self.root/'crash.log'))+'),25)\n')
        worker.write_text(worker.read_text().replace('import importlib.util,os,sys\n','import importlib.util,os,sys\nassert sys.dont_write_bytecode, "Explicit -B is required under isolated Python"\n'))
        child=subprocess.Popen([sys.executable,'-I','-S','-B',str(worker)],env=self.env)
        try:
            end=time.monotonic()+8
            while not marker.exists() and time.monotonic()<end:time.sleep(.05)
            self.assertTrue(marker.exists());pid=int(marker.read_text());child.terminate();child.wait(timeout=3);time.sleep(.2);self.assert_gone(pid)
        finally:
            if child.poll() is None:child.kill();child.wait(timeout=3)
if __name__=='__main__':unittest.main()
