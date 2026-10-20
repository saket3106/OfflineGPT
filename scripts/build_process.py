"""Bounded Windows build process trees, atomically assigned to a Job Object.

Internal adapter only. The builder chooses fixed commands; this is not a user
shell, generated-code executor, network sandbox or production process manager.
"""
import ctypes as C
from ctypes import wintypes as W
import os
from pathlib import Path
import subprocess
import time

class ProcessError(RuntimeError):pass
class LIMITS(C.Structure):
    _fields_=[('processTime',C.c_longlong),('jobTime',C.c_longlong),('flags',W.DWORD),('minWS',C.c_size_t),('maxWS',C.c_size_t),('activeLimit',W.DWORD),('affinity',C.c_size_t),('priority',W.DWORD),('scheduling',W.DWORD)]
class IO(C.Structure):_fields_=[(n,C.c_ulonglong) for n in ('readOps','writeOps','otherOps','readBytes','writeBytes','otherBytes')]
class EXTENDED(C.Structure):_fields_=[('basic',LIMITS),('io',IO),('processMemory',C.c_size_t),('jobMemory',C.c_size_t),('peakProcess',C.c_size_t),('peakJob',C.c_size_t)]
class ACCOUNTING(C.Structure):_fields_=[(n,C.c_longlong) for n in ('user','kernel','periodUser','periodKernel')]+[(n,W.DWORD) for n in ('faults','total','active','terminated')]
class STARTUP(C.Structure):
    _fields_=[('cb',W.DWORD),('reserved',W.LPWSTR),('desktop',W.LPWSTR),('title',W.LPWSTR)]+[(n,W.DWORD) for n in ('x','y','xSize','ySize','xChars','yChars','fill','flags')]+[('show',W.WORD),('reserved2Size',W.WORD),('reserved2',C.POINTER(C.c_byte)),('stdin',W.HANDLE),('stdout',W.HANDLE),('stderr',W.HANDLE)]
class STARTUPEX(C.Structure):_fields_=[('startup',STARTUP),('attributes',C.c_void_p)]
class INFO(C.Structure):_fields_=[('process',W.HANDLE),('thread',W.HANDLE),('pid',W.DWORD),('tid',W.DWORD)]

def _terminate_owned_job(kernel,job,root_process,*,clock=None,sleep=None):
    """Wait for the entire owned job, not merely its root, within five seconds.

    Windows termination can return before descendants release inherited files.
    Keep the Job Object open until accounting proves the tree is empty. The
    caller must close handles even when cleanup fails, retaining KILL_ON_CLOSE.
    This helper never calls the cancelled monitor or operates on unrelated PIDs.
    """
    clock=clock or time.monotonic;sleep=sleep or time.sleep
    deadline=clock()+5.0
    if not kernel.TerminateJobObject(job,2):
        raise ProcessError('TerminateJobObject failed during cleanup: '+str(C.get_last_error()))
    if root_process:
        wait=kernel.WaitForSingleObject(root_process,max(0,int((deadline-clock())*1000)))
        if wait!=0:
            raise ProcessError('Owned root cleanup wait failed or exceeded deadline: '+str(wait))
    accounting=ACCOUNTING()
    while True:
        if not kernel.QueryInformationJobObject(job,1,C.byref(accounting),C.sizeof(accounting),None):
            raise ProcessError('Owned job cleanup accounting failed: '+str(C.get_last_error()))
        if accounting.active==0:return
        remaining=deadline-clock()
        if remaining<=0:
            raise ProcessError('Owned descendants remained after cleanup deadline; recovery required')
        sleep(min(.05,remaining))


def run(command,cwd,env,log,seconds,monitor=lambda:None):
    if os.name!='nt':raise ProcessError('Windows Job Object adapter required')
    if not 0<seconds<=600:raise ProcessError('Invalid bounded process budget')
    import msvcrt
    k=C.WinDLL('kernel32',use_last_error=True)
    signatures={
      'CreateJobObjectW':([C.c_void_p,W.LPCWSTR],W.HANDLE),
      'SetInformationJobObject':([W.HANDLE,C.c_int,C.c_void_p,W.DWORD],W.BOOL),
      'QueryInformationJobObject':([W.HANDLE,C.c_int,C.c_void_p,W.DWORD,C.c_void_p],W.BOOL),
      'InitializeProcThreadAttributeList':([C.c_void_p,W.DWORD,W.DWORD,C.POINTER(C.c_size_t)],W.BOOL),
      'UpdateProcThreadAttribute':([C.c_void_p,W.DWORD,C.c_size_t,C.c_void_p,C.c_size_t,C.c_void_p,C.c_void_p],W.BOOL),
      'DeleteProcThreadAttributeList':([C.c_void_p],None),
      'CreateProcessW':([W.LPCWSTR,W.LPWSTR,C.c_void_p,C.c_void_p,W.BOOL,W.DWORD,C.c_void_p,W.LPCWSTR,C.c_void_p,C.POINTER(INFO)],W.BOOL),
      'ResumeThread':([W.HANDLE],W.DWORD),'WaitForSingleObject':([W.HANDLE,W.DWORD],W.DWORD),
      'GetExitCodeProcess':([W.HANDLE,C.POINTER(W.DWORD)],W.BOOL),'TerminateJobObject':([W.HANDLE,W.UINT],W.BOOL),'CloseHandle':([W.HANDLE],W.BOOL)}
    for name,(args,result) in signatures.items():getattr(k,name).argtypes=args;getattr(k,name).restype=result
    def require(value,label):
        if not value:raise ProcessError(label+' failed: '+str(C.get_last_error()))
    job=k.CreateJobObjectW(None,None);require(job,'CreateJobObject')
    info=INFO();attributes=None;started=time.monotonic();success=False;result={}
    try:
        limits=EXTENDED();limits.basic.flags=0x2000 # KILL_ON_JOB_CLOSE; no breakaway
        require(k.SetInformationJobObject(job,9,C.byref(limits),C.sizeof(limits)),'Job limits')
        size=C.c_size_t();k.InitializeProcThreadAttributeList(None,2,0,C.byref(size))
        attributes=C.create_string_buffer(size.value)
        require(k.InitializeProcThreadAttributeList(attributes,2,0,C.byref(size)),'Attribute list')
        jobs=(W.HANDLE*1)(job)
        require(k.UpdateProcThreadAttribute(attributes,0,0x2000D,C.byref(jobs),C.sizeof(jobs),None,None),'Atomic job assignment')
        Path(log).parent.mkdir(parents=True,exist_ok=True)
        with open(log,'xb') as output,open(os.devnull,'rb') as null:
            handles=(W.HANDLE*2)(msvcrt.get_osfhandle(output.fileno()),msvcrt.get_osfhandle(null.fileno()))
            require(k.UpdateProcThreadAttribute(attributes,0,0x20002,C.byref(handles),C.sizeof(handles),None,None),'Explicit inherited handles')
            si=STARTUPEX();si.startup.cb=C.sizeof(si);si.startup.flags=0x100
            si.startup.stdout=handles[0];si.startup.stderr=handles[0];si.startup.stdin=handles[1];si.attributes=C.cast(attributes,C.c_void_p)
            environment=C.create_unicode_buffer('\0'.join(key+'='+str(value) for key,value in sorted(env.items()))+'\0\0')
            cmd=C.create_unicode_buffer(subprocess.list2cmdline([str(v) for v in command]))
            for handle in handles:os.set_handle_inheritable(handle,True)
            try:
                # JOB_LIST attaches before the suspended child can execute.
                require(k.CreateProcessW(str(command[0]),cmd,None,None,True,0x80000|0x8000000|0x400|0x4,environment,str(cwd),C.byref(si),C.byref(info)),'Create bounded process')
            finally:
                for handle in handles:os.set_handle_inheritable(handle,False)
            if k.ResumeThread(info.thread)==0xFFFFFFFF:raise ProcessError('Could not resume owned process')
            while True:
                waited=k.WaitForSingleObject(info.process,100)
                if waited==0:break
                if waited!=0x102:raise ProcessError('Owned process wait failed: '+str(waited))
                monitor()
                if time.monotonic()-started>seconds:raise ProcessError('Build phase exceeded its time budget')
            code=W.DWORD();require(k.GetExitCodeProcess(info.process,C.byref(code)),'Exit status')
            end=time.monotonic()+3
            accounting=ACCOUNTING()
            while True:
                require(k.QueryInformationJobObject(job,1,C.byref(accounting),C.sizeof(accounting),None),'Process-tree accounting')
                if accounting.active==0 or time.monotonic()>=end:break
                monitor();time.sleep(.05)
            if accounting.active:raise ProcessError('Owned descendants remained after parent exit')
            result={'returnCode':code.value,'elapsedSeconds':time.monotonic()-started,'jobProcesses':accounting.total,'remainingOwnedProcesses':accounting.active,'cpuSeconds':(accounting.user+accounting.kernel)/10**7}
            success=True
    finally:
        try:
            if not success:_terminate_owned_job(k,job,info.process)
        finally:
            if attributes:k.DeleteProcThreadAttributeList(attributes)
            for handle in (info.thread,info.process,job):
                if handle:k.CloseHandle(handle)
    return result
