# SPDX-License-Identifier: GPL-3.0-only
"""Reuse the upstream's exact-build-verified, owned-marker IME adapter."""
from pathlib import Path
import sys
import threading

def create_physicalizer(log):
    vendor=Path(__file__).resolve().parents[2]/'vendor'
    if str(vendor) not in sys.path:sys.path.insert(0,str(vendor))
    from ovb_rc003.doubao_rpc import DoubaoPhysicalizer
    class Observed(DoubaoPhysicalizer):
        def _on_marker_processed(self,payload):
            log('ime_marker',{'key_up':bool(payload.get('key_up')),
                              'vk':int(payload.get('vk',0))})
            super()._on_marker_processed(payload)
    physicalizer=Observed()
    import frida
    cancellation=frida.Cancellable()
    # A real Windows elevation prompt needs time for the user's response.
    # No automatic reattach/retry loop is used on timeout.
    timeout=threading.Timer(30.0,cancellation.cancel)
    timeout.daemon=True
    log('ime_adapter',{'status':'starting','timeout_seconds':30})
    timeout.start()
    try:
        with cancellation:
            started=physicalizer.start((0xa5,))
    except Exception:
        physicalizer.stop()
        raise
    finally:
        timeout.cancel()
    if not started:
        error=physicalizer.error
        physicalizer.stop()
        raise RuntimeError('Doubao shortcut adapter unavailable: '+str(error))
    log('ime_adapter',{'status':physicalizer.status})
    return physicalizer


def activate_current_target(log):
    from ovb_rc003 import wetype_control_windows as profiles
    switched=profiles._run_on_sta_thread(profiles._activate_doubao_input_profile)
    log('ime_target',{'confirmed':True,'switched':bool(switched)})
