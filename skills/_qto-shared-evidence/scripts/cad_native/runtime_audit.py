"""Inspect owned process modules and block vendor-runtime access in reader workers."""
import ctypes,os,sys
from ctypes import wintypes
from pathlib import Path

VENDOR_NAMES={'iextdbobject.grx','iaecconvert.grx','ihvacdbobject.grx','iplumbingdbobject.grx'}


def vendor_module(path):
    value=str(path).lower().replace('\\','/')
    base=value.rsplit('/',1)[-1]
    return (base.endswith('.grx') or base in VENDOR_NAMES or
        any(w in value for w in ('/tangent/','/tianzheng/','/天正/','/t20v','/tchvac/')))


def modules(pid=None):
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    psapi=ctypes.WinDLL('psapi',use_last_error=True)
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.CloseHandle.argtypes=[wintypes.HANDLE];kernel.CloseHandle.restype=wintypes.BOOL
    psapi.EnumProcessModulesEx.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.HMODULE),wintypes.DWORD,ctypes.POINTER(wintypes.DWORD),wintypes.DWORD]
    psapi.EnumProcessModulesEx.restype=wintypes.BOOL
    psapi.GetModuleFileNameExW.argtypes=[wintypes.HANDLE,wintypes.HMODULE,wintypes.LPWSTR,wintypes.DWORD]
    psapi.GetModuleFileNameExW.restype=wintypes.DWORD
    handle=kernel.OpenProcess(0x410,False,pid or os.getpid())
    if not handle:raise OSError(ctypes.get_last_error(),'OpenProcess')
    try:
        size=4096;buffer=(wintypes.HMODULE*size)();needed=wintypes.DWORD()
        if not psapi.EnumProcessModulesEx(handle,buffer,ctypes.sizeof(buffer),ctypes.byref(needed),3):raise OSError(ctypes.get_last_error(),'EnumProcessModulesEx')
        if needed.value>ctypes.sizeof(buffer):raise RuntimeError('module buffer bound exceeded')
        result=[]
        for i in range(needed.value//ctypes.sizeof(wintypes.HMODULE)):
            name=ctypes.create_unicode_buffer(32768)
            if not psapi.GetModuleFileNameExW(handle,buffer[i],name,len(name)):raise OSError(ctypes.get_last_error(),'GetModuleFileNameExW')
            result.append(name.value)
        return sorted(result)
    finally:kernel.CloseHandle(handle)


def install_reader_guard():
    state={'blocked_attempts':[],'vendor_file_reads':0,'subprocesses':0}
    def audit(event,args):
        reason=None
        if event=='subprocess.Popen':reason='reader must not invoke another process'
        elif event in ('open','ctypes.dlopen') and args:
            path=args[0]
            if isinstance(path,(str,bytes,os.PathLike)):
                value=os.fsdecode(path).lower()
                if value.endswith(('.grx','.arx','.dbx')) or vendor_module(value):reason='vendor runtime access blocked'
        if reason:
            state['blocked_attempts'].append({'event':event,'reason':reason})
            raise PermissionError(reason)
    sys.addaudithook(audit)
    return state
