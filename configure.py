# SPDX-License-Identifier: GPL-3.0-only
"""Select an already-paired 9451 remote. Never pair/unpair or change defaults."""
from pathlib import Path
import argparse,json,sys
ROOT=Path(__file__).resolve().parent
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'vendor')]
from rm9451.config import CONFIG_PATH,settings

def discover():
    import winreg
    from ovb_rc003.remote_selection import container_key
    rows=set()
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,r'SYSTEM\CurrentControlSet\Enum\BTHLEDevice') as root:
        for i in range(winreg.QueryInfoKey(root)[0]):
            name=winreg.EnumKey(root,i);low=name.lower()
            if not low.startswith('{00001812-0000-1000-8000-00805f9b34fb}') or '18d1_pid&9451_' not in low:continue
            address=low.rsplit('_',1)[-1]
            if len(address)!=12 or any(c not in '0123456789abcdef' for c in address):continue
            with winreg.OpenKey(root,name) as service:
                for j in range(winreg.QueryInfoKey(service)[0]):
                    with winreg.OpenKey(service,winreg.EnumKey(service,j)) as instance:
                        try:container=winreg.QueryValueEx(instance,'ContainerID')[0]
                        except OSError:continue
                        rows.add((address,container_key(container)))
    return sorted(rows)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--device',type=int,help='1-based index when several remotes are paired')
    p.add_argument('--list',action='store_true',help='list candidates without saving configuration')
    p.add_argument('--enable-extra-keys',action='store_true',help='opt into the pinned-driver HID hook')
    args=p.parse_args()
    rows=discover()
    for i,(address,_) in enumerate(rows,1):print(f'{i}. 18D1:9451 remote (address ending {address[-4:].upper()})')
    if args.list:return 0
    if not rows:p.error('No paired 18D1:9451 HID service found. Pair in Windows Bluetooth settings first.')
    if args.device is None and len(rows)!=1:p.error('Several remotes found; select one with --device N')
    index=(args.device or 1)-1
    if not 0<=index<len(rows):p.error('Invalid --device index')
    address,container=rows[index];config=settings()
    config['device']={'bluetooth_address':':'.join(address[i:i+2].upper() for i in range(0,12,2)),
                      'container_key':container}
    config.setdefault('features',{})['extra_keys']=args.enable_extra_keys
    CONFIG_PATH.parent.mkdir(parents=True,exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('Saved private local configuration. Do not upload config.local.json or runtime logs.')
    print('Experimental extra keys: '+('enabled' if args.enable_extra_keys else 'disabled'))
    return 0

if __name__=='__main__':raise SystemExit(main())
