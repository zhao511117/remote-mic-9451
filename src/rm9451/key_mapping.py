# SPDX-License-Identifier: GPL-3.0-only
"""Device-scoped extra keys. Existing voice and direction processes stay live."""
from pathlib import Path
import ctypes, hashlib, json, os, queue, subprocess, sys, threading, time
from ctypes import wintypes as W
from .buttons import CONTAINER
from .process_control import ProcessControl, NAME
from .config import require_device,settings,evidence_dir,disabled_marker,extra_keys_enabled,program_path,pythonw

ROOT=Path(__file__).resolve().parents[2]
DRIVER=Path(os.environ.get('SystemRoot',r'C:\Windows'))/'System32/drivers/umdf/microsoft.bluetooth.profiles.hidovergatt.dll'
DRIVER_SHA='41d2998c7f38e104b0a469404cfb05ddbfe7c0a912b9020f446f005f3f4156d4'
CALLBACK_RVA=96428
ADDRESS_OFFSET=24
ACTIONS={6:'escape',7:'desktop',10:'workbuddy',11:'codex',12:'none',13:'task_view'}

def selected_host():
    import winreg
    from ovb_rc003.remote_selection import container_key
    found=set()
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,r'SYSTEM\CurrentControlSet\Enum\BTHLEDevice') as root:
        for i in range(winreg.QueryInfoKey(root)[0]):
            name=winreg.EnumKey(root,i)
            low=name.lower()
            if not low.startswith('{00001812-0000-1000-8000-00805f9b34fb}') or '18d1_pid&9451_' not in low:continue
            address=low.rsplit('_',1)[-1]
            if len(address)!=12 or any(c not in '0123456789abcdef' for c in address):continue
            with winreg.OpenKey(root,name) as service:
                for j in range(winreg.QueryInfoKey(service)[0]):
                    with winreg.OpenKey(service,winreg.EnumKey(service,j)) as instance:
                        try:
                            if container_key(winreg.QueryValueEx(instance,'ContainerID')[0])!=CONTAINER:continue
                            with winreg.OpenKey(instance,r'Device Parameters\WUDFDiagnosticInfo') as info:
                                pid=winreg.QueryValueEx(info,'HostPid')[0]
                            if type(pid) is int and pid>0:found.add((pid,address))
                        except OSError:continue
    if len(found)!=1:raise RuntimeError('Selected HID host unavailable or ambiguous')
    return found.pop()

def make_script(address):
    data=DRIVER.read_bytes()
    if hashlib.sha256(data).hexdigest()!=DRIVER_SHA:raise RuntimeError('Unsupported HID driver build')
    import pefile
    image=pefile.PE(data=data,fast_load=True)
    try:prefix=list(image.get_data(CALLBACK_RVA,32))
    finally:image.close()
    config=dict(path=str(DRIVER).lower().replace('\\','/'),rva=CALLBACK_RVA,
                address_offset=ADDRESS_OFFSET,address=list(bytes.fromhex(address)[::-1]),prefix=prefix)
    return Path(__file__).with_suffix('.js').read_text(encoding='utf-8').replace('__CONFIG__',json.dumps(config))

def chord(keys):
    from .output import _Input,_InputUnion,_KeyBdInput,INPUT_KEYBOARD,KEYEVENTF_KEYUP
    u=ctypes.WinDLL('user32',use_last_error=True)
    u.SendInput.argtypes=(W.UINT,ctypes.POINTER(_Input),ctypes.c_int);u.SendInput.restype=W.UINT
    held=[]
    def edge(vk,up):
        item=_Input(type=INPUT_KEYBOARD,u=_InputUnion(ki=_KeyBdInput(
            wVk=vk,wScan=0,dwFlags=KEYEVENTF_KEYUP if up else 0,time=0,dwExtraInfo=0x524d4b4559)))
        return u.SendInput(1,ctypes.byref(item),ctypes.sizeof(_Input))==1
    try:
        for key in keys:
            if not edge(key,False):return False
            held.append(key)
        return True
    finally:
        for key in reversed(held):edge(key,True)

def app_windows(kind):
    """Find visible app windows by executable identity; never read command lines."""
    u=ctypes.WinDLL('user32',use_last_error=True);k=ctypes.WinDLL('kernel32',use_last_error=True)
    u.IsWindowVisible.argtypes=(W.HWND,);u.IsWindowVisible.restype=W.BOOL
    u.GetWindowThreadProcessId.argtypes=(W.HWND,ctypes.POINTER(W.DWORD))
    k.OpenProcess.argtypes=(W.DWORD,W.BOOL,W.DWORD);k.OpenProcess.restype=W.HANDLE
    k.QueryFullProcessImageNameW.argtypes=(W.HANDLE,W.DWORD,W.LPWSTR,ctypes.POINTER(W.DWORD))
    k.CloseHandle.argtypes=(W.HANDLE,)
    result=[]
    @ctypes.WINFUNCTYPE(W.BOOL,W.HWND,W.LPARAM)
    def visit(hwnd,_):
        if not u.IsWindowVisible(hwnd):return True
        pid=W.DWORD();u.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
        handle=k.OpenProcess(0x1000,False,pid.value)
        if not handle:return True
        try:
            buf=ctypes.create_unicode_buffer(32768);size=W.DWORD(len(buf))
            if k.QueryFullProcessImageNameW(handle,0,buf,ctypes.byref(size)):
                path=Path(buf.value)
                matched=(path.name.lower()=='workbuddy.exe' if kind=='workbuddy' else
                         path.name.lower()=='chatgpt.exe' and any(part.startswith('OpenAI.Codex_') for part in path.parts))
                if matched:result.append((hwnd,path))
        finally:k.CloseHandle(handle)
        return True
    u.EnumWindows.argtypes=(ctypes.c_void_p,W.LPARAM);u.EnumWindows(visit,0)
    return result

def activate_app(kind):
    windows=app_windows(kind)
    if windows:
        from .foreground import toggle_window
        hwnd,_=windows[0]
        return toggle_window(hwnd)
    if kind=='workbuddy':path=program_path('workbuddy_executable',Path(os.environ.get('ProgramFiles',r'C:\Program Files'))/'WorkBuddy/WorkBuddy.exe')
    else:
        # Installed desktop package, never the unrelated codex.exe CLI workers.
        app_id=settings().get('applications',{}).get('codex_app_id') or 'OpenAI.Codex_2p2nqsd0c76g0!App'
        os.startfile('shell:AppsFolder\\'+app_id)
        return True
    if not path.is_file():return False
    subprocess.Popen([str(path)],cwd=str(path.parent),creationflags=subprocess.CREATE_NO_WINDOW)
    return True

def dispatch(selector,*,send_chord=chord,activate=activate_app):
    action=ACTIONS.get(selector)
    if action=='none':return True
    if action in ('codex','workbuddy'):return activate(action)
    keys={'escape':(0x1b,),'desktop':(0x5b,0x44),'task_view':(0x5b,0x09)}.get(action)
    return send_chord(keys) if keys else False

def spawn():
    if not extra_keys_enabled():return
    disabled_marker().unlink(missing_ok=True)
    subprocess.Popen([str(pythonw()),'-B',str(ROOT/'key_mapping.pyw')],
                     cwd=str(ROOT),creationflags=subprocess.CREATE_NO_WINDOW)

def main():
    if '--stop' in sys.argv[1:]:
        disabled_marker().parent.mkdir(parents=True,exist_ok=True)
        disabled_marker().touch()
        ProcessControl(name=NAME+'-keys').request_stop()
        return 0
    if not extra_keys_enabled():return 0
    require_device()
    disabled_marker().unlink(missing_ok=True)
    from .runner import JsonlLogger
    from .login_start import receiver_running
    log=JsonlLogger(evidence_dir()/'key-mapping.jsonl')
    control=ProcessControl(name=NAME+'-keys')
    if not control.start():log.close();return 0
    stop=threading.Event();control.watch(stop)
    session=script=None
    try:
        pid,address=selected_host();source=make_script(address)
        log('key_mapping_attach',{'host_pid':pid,'driver_sha256':DRIVER_SHA})
        import frida
        cancellation=frida.Cancellable();timer=threading.Timer(30,cancellation.cancel);timer.daemon=True;timer.start()
        try:
            with cancellation:session=frida.attach(pid)
        finally:timer.cancel()
        if selected_host()!=(pid,address):raise RuntimeError('HID host changed during attach')
        events=queue.Queue(maxsize=128);ready=threading.Event();fault=threading.Event()
        def message(msg,_):
            if msg.get('type')!='send':fault.set();return
            item=msg.get('payload',{});kind=item.get('kind')
            if kind=='ready':ready.set()
            elif kind=='fault':fault.set()
            elif kind=='key':
                try:events.put_nowait(item['selector'])
                except queue.Full:fault.set()
            elif kind=='report':log('key_report',{'selector':item.get('selector'),'suppressed':item.get('suppressed')})
        session.on('detached',lambda *_:fault.set())
        script=session.create_script(source);script.on('message',message);script.load()
        if not ready.wait(3):raise RuntimeError('HID hook not ready')
        log('key_mapping_ready',{'actions':ACTIONS})
        while not stop.is_set() and not fault.is_set() and receiver_running():
            script.exports_sync.renew()
            until=time.monotonic()+0.4
            while time.monotonic()<until and not stop.is_set() and not fault.is_set():
                try:selector=events.get(timeout=.05)
                except queue.Empty:continue
                success=dispatch(selector)
                log('key_action',{'selector':selector,'action':ACTIONS.get(selector),'success':success})
        if fault.is_set():log('key_mapping_fault',{})
        return 0
    except Exception as exc:
        log('key_mapping_error',{'error_type':type(exc).__name__,'detail':str(exc)[:120]})
        return 1
    finally:
        if script:
            try:script.exports_sync.disable()
            except Exception:pass
            try:script.unload()
            except Exception:pass
        if session:
            try:session.detach()
            except Exception:pass
        control.close();log('key_mapping_stopped',{});log.close()
