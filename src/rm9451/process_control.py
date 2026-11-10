# SPDX-License-Identifier: GPL-3.0-only
"""Per-session singleton and explicit background stop, without a UI window."""
import ctypes,threading
from ctypes import wintypes as W

NAME='Local\\RemoteMic-9451-public-v1'

class ProcessControl:
    def __init__(self,name=NAME):
        self.name=name;self.mutex=None;self.event=None;self.thread=None
        k=self.k=ctypes.WinDLL('kernel32',use_last_error=True)
        k.CreateMutexW.argtypes=(W.LPVOID,W.BOOL,W.LPCWSTR);k.CreateMutexW.restype=W.HANDLE
        k.CreateEventW.argtypes=(W.LPVOID,W.BOOL,W.BOOL,W.LPCWSTR);k.CreateEventW.restype=W.HANDLE
        k.OpenEventW.argtypes=(W.DWORD,W.BOOL,W.LPCWSTR);k.OpenEventW.restype=W.HANDLE
        k.SetEvent.argtypes=(W.HANDLE,);k.SetEvent.restype=W.BOOL
        k.WaitForSingleObject.argtypes=(W.HANDLE,W.DWORD);k.WaitForSingleObject.restype=W.DWORD
        k.CloseHandle.argtypes=(W.HANDLE,);k.CloseHandle.restype=W.BOOL
    def start(self):
        ctypes.set_last_error(0)
        handle=self.k.CreateMutexW(None,False,self.name)
        if not handle:raise ctypes.WinError(ctypes.get_last_error())
        if ctypes.get_last_error()==183:
            self.k.CloseHandle(handle);return False
        self.mutex=handle
        self.event=self.k.CreateEventW(None,True,False,self.name+'-stop')
        if not self.event:self.close();raise OSError('Cannot create stop event')
        return True
    def watch(self,stop):
        def wait():
            if self.k.WaitForSingleObject(self.event,0xffffffff)==0:stop.set()
        self.thread=threading.Thread(target=wait,daemon=True,name='remote-stop');self.thread.start()
    def request_stop(self):
        event=self.k.OpenEventW(2,False,self.name+'-stop')
        if not event:return False
        try:return bool(self.k.SetEvent(event))
        finally:self.k.CloseHandle(event)
    def close(self):
        if self.event:
            self.k.SetEvent(self.event)
            if self.thread:self.thread.join(1)
            self.k.CloseHandle(self.event);self.event=None
        if self.mutex:self.k.CloseHandle(self.mutex);self.mutex=None
