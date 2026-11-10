# SPDX-License-Identifier: GPL-3.0-only
"""One normal-user login launch. No elevation, hook attach or retry loop here."""
from pathlib import Path
import ctypes,json,subprocess,sys,threading,time
from datetime import datetime
from .process_control import ProcessControl,NAME
from .config import evidence_dir,disabled_marker,extra_keys_enabled,program_path,pythonw as pythonw_path

ROOT=Path(__file__).resolve().parents[2]
WATCHDOG=program_path('doubao_watchdog',Path(__import__('os').environ.get('ProgramFiles',r'C:\Program Files'))/'DoubaoIME/bootstrap/ImeWatchdog.exe')

def object_running(name):
    k=ctypes.WinDLL('kernel32',use_last_error=True)
    k.OpenMutexW.argtypes=(ctypes.c_uint,ctypes.c_int,ctypes.c_wchar_p)
    k.OpenMutexW.restype=ctypes.c_void_p
    k.CloseHandle.argtypes=(ctypes.c_void_p,)
    handle=k.OpenMutexW(0x100000,False,name)
    if handle:k.CloseHandle(handle);return True
    # Access denied also proves a named object exists: do not spawn blindly.
    return ctypes.get_last_error()==5

def receiver_running():return object_running(NAME)

def ensure_supervisor():
    if not object_running(NAME+'-login'):
        subprocess.Popen([str(pythonw_path()),'-B',str(ROOT/'login_start.pyw')],
                         cwd=str(ROOT),creationflags=subprocess.CREATE_NO_WINDOW)

def ime_names():
    vendor=str(ROOT/'vendor')
    if vendor not in sys.path:sys.path.insert(0,vendor)
    from ovb_rc003.voice_program_manager import _iter_windows_processes
    k=ctypes.WinDLL('kernel32',use_last_error=True)
    k.ProcessIdToSessionId.argtypes=(ctypes.c_uint,ctypes.POINTER(ctypes.c_uint))
    current=ctypes.c_uint()
    if not k.ProcessIdToSessionId(__import__('os').getpid(),ctypes.byref(current)):
        raise OSError('Cannot identify the current Windows session')
    result=set()
    for item in _iter_windows_processes(names={'imeservice.exe','imewatchdog.exe'}):
        session=ctypes.c_uint()
        if k.ProcessIdToSessionId(item.pid,ctypes.byref(session)) and session.value==current.value:
            result.add(item.name.casefold())
    return result

def launch_when_ready(*,is_running,names,start_watchdog,start_receiver,wait,cancelled,log,
                      clock=time.monotonic,timeout=90.0,initial_delay=8.0):
    if is_running():log('already_running');return 'already_running'
    if wait(initial_delay):log('cancelled');return 'cancelled'
    deadline=clock()+timeout
    watchdog_requested=False;ready_since=None
    while clock()<deadline:
        if cancelled():log('cancelled');return 'cancelled'
        if is_running():log('already_running');return 'already_running'
        processes=names()
        if 'imeservice.exe' in processes:
            if ready_since is None:ready_since=clock()
            if clock()-ready_since>=2.0:
                # Check cancellation/racing manual launch once more before spawn.
                if cancelled():log('cancelled');return 'cancelled'
                if is_running():log('already_running');return 'already_running'
                start_receiver();log('receiver_started');return 'receiver_started'
        else:
            ready_since=None
            if 'imewatchdog.exe' not in processes and not watchdog_requested:
                start_watchdog();watchdog_requested=True;log('ime_watchdog_started')
        if wait(1.0):log('cancelled');return 'cancelled'
    log('ime_startup_timeout');return 'ime_startup_timeout'

def main():
    from .__main__ import initialize_mta
    initialize_mta()
    control=ProcessControl(name=NAME+'-login')
    if not control.start():return 0
    stop=threading.Event();control.watch(stop)
    log_path=evidence_dir()/'login-startup.jsonl'
    log_path.parent.mkdir(parents=True,exist_ok=True)
    def log(event):
        with log_path.open('a',encoding='utf-8') as stream:
            stream.write(json.dumps({'ts':datetime.now().astimezone().isoformat(),'event':event})+'\n')
    pythonw=pythonw_path()
    def start_watchdog():
        if not WATCHDOG.is_file():raise FileNotFoundError('Doubao watchdog missing')
        subprocess.Popen([str(WATCHDOG)],cwd=str(WATCHDOG.parent),creationflags=subprocess.CREATE_NO_WINDOW)
    def start_receiver():
        subprocess.Popen([str(pythonw),'-B',str(ROOT/'start.py')],cwd=str(ROOT),creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        log('login_launcher_started')
        result=launch_when_ready(is_running=receiver_running,names=ime_names,start_watchdog=start_watchdog,
                          start_receiver=start_receiver,wait=stop.wait,cancelled=stop.is_set,log=log)
        if result in ('already_running','receiver_started') and not stop.is_set():
            from .recovery import supervise
            from .key_mapping import spawn
            def recover_receiver():
                launch_when_ready(is_running=receiver_running,names=ime_names,start_watchdog=start_watchdog,
                    start_receiver=start_receiver,wait=stop.wait,cancelled=stop.is_set,log=log,initial_delay=0)
            supervise(receiver_running=receiver_running,mapper_running=lambda:object_running(NAME+'-keys'),
                      start_receiver=recover_receiver,start_mapper=spawn,
                      mapper_disabled=lambda:not extra_keys_enabled() or disabled_marker().exists(),
                      wait=stop.wait,cancelled=stop.is_set,log=log,clock=time.monotonic)
        return 0
    except Exception as exc:
        log('startup_error_'+type(exc).__name__)
        return 1
    finally:control.close()
