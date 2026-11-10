# SPDX-License-Identifier: GPL-3.0-only
"""Raw Input for this physical remote's consumer collection only."""
import ctypes,sys,threading,time
from ctypes import wintypes as W
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'vendor'))
from ovb_rc003.raw_input_windows import RAWINPUTDEVICELIST,_get_device_name,RawInputButtonListener
from ovb_rc003.hid_identity import parse_rawinput_hid_payload
from ovb_rc003.remote_selection import raw_path_key
from .output import _Input,_InputUnion,_KeyBdInput,INPUT_KEYBOARD,KEYEVENTF_SCANCODE,KEYEVENTF_EXTENDEDKEY,KEYEVENTF_KEYUP

from .config import device_identity
CONTAINER=device_identity()[1]
# Actual one-at-a-time capture on this device: 02 03 00=left, etc.
SCANS={1:(0x48,True),2:(0x50,True),3:(0x4b,True),4:(0x4d,True),5:(0x1c,False)}
LABELS={1:'up',2:'down',3:'left',4:'right',5:'enter'}

def decode_selectors(report):
    if len(report)!=3 or report[0]!=2:
        raise ValueError('Unsupported remote report')
    return frozenset(v for v in report[1:] if v in SCANS)

def selected_paths():
    u=ctypes.WinDLL('user32',use_last_error=True)
    u.GetRawInputDeviceList.argtypes=(ctypes.POINTER(RAWINPUTDEVICELIST),ctypes.POINTER(W.UINT),W.UINT)
    u.GetRawInputDeviceList.restype=W.UINT
    n=W.UINT()
    if u.GetRawInputDeviceList(None,ctypes.byref(n),ctypes.sizeof(RAWINPUTDEVICELIST))!=0:
        raise OSError('Raw Input device enumeration failed')
    rows=(RAWINPUTDEVICELIST*n.value)()
    count=u.GetRawInputDeviceList(rows,ctypes.byref(n),ctypes.sizeof(RAWINPUTDEVICELIST))
    if count==0xffffffff:raise OSError('Raw Input device enumeration failed')
    paths=[]
    for row in rows[:count]:
        if row.dwType!=2:continue
        path=_get_device_name(u,row.hDevice,0x20000007)
        if not path or '18d1' not in path.lower() or '9451' not in path.lower():continue
        try:matched=raw_path_key(path)==CONTAINER
        except Exception:matched=False
        if matched:paths.append(path)
    return paths

def send_edge(selector,up):
    scan,extended=SCANS[selector]
    flags=KEYEVENTF_SCANCODE|(KEYEVENTF_EXTENDEDKEY if extended else 0)|(KEYEVENTF_KEYUP if up else 0)
    event=_Input(type=INPUT_KEYBOARD,u=_InputUnion(ki=_KeyBdInput(
        wVk=0,wScan=scan,dwFlags=flags,time=0,dwExtraInfo=0x524d4e4156)))
    u=ctypes.WinDLL('user32',use_last_error=True)
    u.SendInput.argtypes=(W.UINT,ctypes.POINTER(_Input),ctypes.c_int)
    u.SendInput.restype=W.UINT
    return u.SendInput(1,ctypes.byref(event),ctypes.sizeof(_Input))==1

class SelectorState:
    def __init__(self,sender=send_edge,log=lambda *_:None):
        self.down=set();self.sender=sender;self.log=log;self.lock=threading.RLock()
    def update(self,report):
        current=decode_selectors(report)
        with self.lock:
            for key in sorted(self.down-current):
                if self.sender(key,True):self.down.remove(key)
            for key in sorted(current-self.down):
                if self.sender(key,False):self.down.add(key)
            self.log('navigation',{'keys':[LABELS[k] for k in sorted(current)]})
    def release(self):
        with self.lock:
            for key in list(self.down):
                if self.sender(key,True):self.down.remove(key)

class Navigation:
    def __init__(self,log):
        self.log=log;self.state=SelectorState(log=log);self.stop_event=threading.Event();self.thread=None
    def start(self):
        self.thread=threading.Thread(target=self._run,daemon=True,name='remote-navigation');self.thread.start()
    def _run(self):
        state=self.state
        class Listener(RawInputButtonListener):
            def _handle_hid_body(self,body,*,device_path=None):
                for report in parse_rawinput_hid_payload(body):
                    try:state.update(report)
                    except ValueError:state.release()
            def _handle_physical_keyboard_body(self,*args,**kwargs):
                pass # Never inspect other physical keyboards.
        listener=None;path=None;warned=False
        try:
            while not self.stop_event.wait(.5):
                paths=selected_paths()
                new=paths[0] if len(paths)==1 else None
                if new!=path:
                    if listener:listener.stop();listener=None
                    state.release();path=new
                    if new:
                        listener=Listener(lambda *_:None)
                        listener.set_device_removed_callback(lambda:state.release())
                        listener.start(new)
                        self.log('navigation_ready',{})
                if len(paths)>1 and not warned:
                    self.log('navigation_ambiguous',{'count':len(paths)});warned=True
        except Exception as exc:self.log('navigation_error',{'error_type':type(exc).__name__})
        finally:
            if listener:listener.stop()
            state.release()
    def stop(self):
        self.stop_event.set()
        if self.thread:self.thread.join(4)
        self.state.release()
