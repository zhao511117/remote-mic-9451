# SPDX-License-Identifier: GPL-3.0-only
"""Read-only readiness checks. Does not connect, record or attach Frida."""
from pathlib import Path
import hashlib,importlib.metadata,json,sys
root=Path(__file__).resolve().parent
sys.path[:0]=[str(root/'src'),str(root/'vendor')]
from rm9451.config import require_device,extra_keys_enabled

def main():
    checks={}
    try:require_device();checks['private_device_configuration']=True
    except (ValueError,OSError):checks['private_device_configuration']=False
    checks['windows_x64']=sys.platform=='win32' and __import__('struct').calcsize('P')==8
    checks['python_3_13']=sys.version_info[:2]==(3,13)
    for name,wanted in [('bleak','2.1.1'),('frida','17.15.3'),('pefile','2024.8.26')]:
        try:checks[name]=importlib.metadata.version(name)==wanted
        except importlib.metadata.PackageNotFoundError:checks[name]=False
    try:
        from rm9451.output import CableSink
        CableSink(lambda *_:None).resolve();checks['cable_output_endpoint']=True
    except Exception:checks['cable_output_endpoint']=False
    if extra_keys_enabled():
        from rm9451.key_mapping import DRIVER,DRIVER_SHA
        checks['experimental_hid_driver_build']=DRIVER.is_file() and hashlib.sha256(DRIVER.read_bytes()).hexdigest()==DRIVER_SHA
    print(json.dumps(checks,indent=2))
    return 0 if all(checks.values()) else 1
if __name__=='__main__':raise SystemExit(main())
