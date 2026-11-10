# SPDX-License-Identifier: GPL-3.0-only
"""Foreground activation only in response to the remote's application keys."""
import ctypes,time
from ctypes import wintypes as W

class WindowsForeground:
    def __init__(self):
        u=self.u=ctypes.WinDLL('user32',use_last_error=True)
        u.GetForegroundWindow.argtypes=();u.GetForegroundWindow.restype=W.HWND
        u.IsIconic.argtypes=(W.HWND,);u.IsIconic.restype=W.BOOL
        u.ShowWindowAsync.argtypes=(W.HWND,ctypes.c_int);u.ShowWindowAsync.restype=W.BOOL
        u.SetForegroundWindow.argtypes=(W.HWND,);u.SetForegroundWindow.restype=W.BOOL
        u.GetAsyncKeyState.argtypes=(ctypes.c_int,);u.GetAsyncKeyState.restype=ctypes.c_short
    def foreground(self):return self.u.GetForegroundWindow()
    def is_minimized(self,hwnd):return bool(self.u.IsIconic(hwnd))
    def restore(self,hwnd):return bool(self.u.ShowWindowAsync(hwnd,9))
    def minimize(self,hwnd):return bool(self.u.ShowWindowAsync(hwnd,6))
    def set_foreground(self,hwnd):return bool(self.u.SetForegroundWindow(hwnd))
    def unlock(self):
        # ALT enables foreground changes (LockSetForegroundWindow remarks).
        # Only use left ALT; never touch the voice shortcut's right ALT.
        if self.u.GetAsyncKeyState(0xa4)&0x8000:return False
        from .output import _Input,_InputUnion,_KeyBdInput,INPUT_KEYBOARD,KEYEVENTF_KEYUP
        def event(up):
            return _Input(type=INPUT_KEYBOARD,u=_InputUnion(ki=_KeyBdInput(
                wVk=0xa4,wScan=0x38,dwFlags=KEYEVENTF_KEYUP if up else 0,
                time=0,dwExtraInfo=0x524d464f43)))
        self.u.SendInput.argtypes=(W.UINT,ctypes.POINTER(_Input),ctypes.c_int)
        self.u.SendInput.restype=W.UINT
        pair=(_Input*2)(event(False),event(True))
        inserted=self.u.SendInput(2,pair,ctypes.sizeof(_Input))
        if inserted==1:
            release=event(True)
            self.u.SendInput(1,ctypes.byref(release),ctypes.sizeof(_Input))
        return inserted==2

def activate_window(hwnd,*,api=None,wait=time.sleep):
    api=api or WindowsForeground()
    # Restore only minimized windows, preserving maximized/normal geometry.
    if api.is_minimized(hwnd):
        api.restore(hwnd)
        for _ in range(6):
            if not api.is_minimized(hwnd):break
            wait(.025)
    def attempt():
        api.set_foreground(hwnd)
        for _ in range(4):
            if api.foreground()==hwnd:return True
            wait(.025)
        return False
    if api.foreground()==hwnd or attempt():return True
    if not api.unlock():return False
    return attempt()

def toggle_window(hwnd,*,api=None,wait=time.sleep):
    api=api or WindowsForeground()
    if api.foreground()==hwnd and not api.is_minimized(hwnd):
        api.minimize(hwnd)
        for _ in range(6):
            if api.is_minimized(hwnd):return True
            wait(.025)
        return False
    return activate_window(hwnd,api=api,wait=wait)
