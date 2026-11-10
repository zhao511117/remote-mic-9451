# SPDX-License-Identifier: GPL-3.0-only
"""Private per-installation settings; no device identity is shipped in source."""
import json,os,re
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
STATE=Path(os.environ.get('LOCALAPPDATA',str(ROOT)))/'RemoteMic9451'
CONFIG_PATH=Path(os.environ.get('RM9451_CONFIG',str(ROOT/'config.local.json')))

def settings():
    if not CONFIG_PATH.is_file():return {}
    value=json.loads(CONFIG_PATH.read_text(encoding='utf-8-sig'))
    if not isinstance(value,dict):raise ValueError('Configuration must be a JSON object')
    return value

def device_identity(required=False):
    device=settings().get('device',{})
    address=device.get('bluetooth_address') or ''
    container=device.get('container_key') or ''
    if not address and not container and not required:return 0,''
    compact=str(address).replace(':','').replace('-','')
    if not re.fullmatch(r'[0-9a-fA-F]{12}',compact) or not re.fullmatch(r'[0-9a-f]{64}',str(container)):
        raise ValueError('Run .venv/Scripts/python.exe configure.py after pairing the 18D1:9451 remote')
    return int(compact,16),container

def require_device():return device_identity(required=True)
def extra_keys_enabled():return settings().get('features',{}).get('extra_keys',False) is True
def evidence_dir():return STATE/'evidence'
def disabled_marker():return STATE/'key-mapping.disabled'
def pythonw():
    import sys
    return Path(sys.executable).with_name('pythonw.exe')

def program_path(key,default):
    return Path(os.path.expandvars(settings().get('applications',{}).get(key) or str(default)))
