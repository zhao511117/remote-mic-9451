# SPDX-License-Identifier: GPL-3.0-only
"""CLI entry point for the 9451 adaptation bridge.

    python -B -m rm9451 --self-test
    python -B -m rm9451 --max-seconds 60 --no-hotkey --no-audio   # link probe
    python -B -m rm9451 --max-seconds 90 --mic-seconds 6          # full bridge
"""
from __future__ import annotations

import argparse
import asyncio
import ctypes
import json
import sys
from datetime import datetime
from pathlib import Path

from . import protocol as proto
from .config import evidence_dir,require_device,extra_keys_enabled

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = evidence_dir()


def initialize_mta():
    """Initialize before PortAudio can select MAINSTA on this thread.

    Without a Windows message pump, WinRT async completion can hang in STA.
    Keeping the CLI thread MTA also matches Bleak's documented requirement.
    """
    import atexit
    ole=ctypes.OleDLL('ole32')
    ole.CoInitializeEx.argtypes=(ctypes.c_void_p,ctypes.c_uint)
    ole.CoInitializeEx.restype=ctypes.c_long
    result=ole.CoInitializeEx(None,0)
    if result not in (0,1):
        raise RuntimeError(f'Bluetooth requires an MTA thread: HRESULT {result & 0xffffffff:08x}')
    atexit.register(ole.CoUninitialize)


def self_test() -> int:
    from . import ble, output

    report: dict = {"timestamp": datetime.now().isoformat(timespec="seconds"),
                    "python": sys.version.split()[0], "checks": {}}
    try:
        report["checks"]["ble_cached_gatt"] = asyncio.run(ble.read_only_probe())
    except Exception as exc:
        report["checks"]["ble_cached_gatt"] = {"error_type": type(exc).__name__,
                                               "detail": str(exc)[:200]}
    try:
        report["checks"]["cable_endpoint"] = output.CableSink(lambda *_: None).resolve()
    except Exception as exc:
        report["checks"]["cable_endpoint"] = {"error_type": type(exc).__name__,
                                              "detail": str(exc)[:200]}
    try:
        user32 = ctypes.windll.user32
        user32.MapVirtualKeyW.argtypes = (ctypes.c_uint, ctypes.c_uint)
        user32.MapVirtualKeyW.restype = ctypes.c_uint
        report["checks"]["ralt"] = {
            "input_struct_bytes": ctypes.sizeof(output._Input),
            "map_vk_to_vsc_ex": hex(int(user32.MapVirtualKeyW(output.VK_RMENU, 4))),
            "expected_scan": "0xe038",
        }
    except Exception as exc:
        report["checks"]["ralt"] = {"error_type": type(exc).__name__}
    fixture = proto.parse_caps(bytes.fromhex("0b0100010300860000"))
    report["checks"]["protocol"] = {
        "default_frame_size": proto.DEFAULT_FRAME_SIZE,
        "default_header_bytes": proto.DEFAULT_FRAME_HEADER,
        "get_caps_v10": proto.GET_CAPABILITIES_V10.hex(),
        "get_caps_proven": proto.GET_CAPS_PROVEN.hex(),
        "mic_open_proven": proto.MIC_OPEN_PROVEN.hex(),
        "mic_close_proven": proto.MIC_CLOSE_PROVEN.hex(),
        "caps_fixture": {"frame_size": fixture.frame_size,
                         "codec": fixture.codec, "sample_rate": fixture.sample_rate},
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    ok = (isinstance(report["checks"]["ble_cached_gatt"], dict)
          and "error_type" not in report["checks"]["ble_cached_gatt"]
          and "error_type" not in report["checks"]["cable_endpoint"])
    return 0 if ok else 1


def main(argv=None) -> int:
    initialize_mta()
    parser = argparse.ArgumentParser(description="Google TV Remote 9451 ATVV bridge")
    parser.add_argument("--self-test", action="store_true", help="read-only environment check")
    parser.add_argument("--stop",action="store_true",help="stop the background instance")
    parser.add_argument("--frame-size", type=int, default=proto.DEFAULT_FRAME_SIZE)
    parser.add_argument("--frame-header", type=int, default=proto.DEFAULT_FRAME_HEADER,
                        choices=(0, 6), help="bytes of per-frame header to strip")
    parser.add_argument("--gain-db", type=float, default=3.0)
    parser.add_argument("--no-hotkey", action="store_true", help="do not inject right Alt")
    parser.add_argument("--no-audio", action="store_true", help="do not open CABLE Input")
    parser.add_argument("--no-get-caps", action="store_true", help="skip GET_CAPS write")
    parser.add_argument("--caps-v10", action="store_true", default=True,
                        help="use the 6-byte v1.0 GET_CAPS instead of the proven 1-byte one")
    parser.add_argument("--legacy-caps",action="store_false",dest="caps_v10",
                        help="diagnostic only: use the old one-byte capability query")
    parser.add_argument("--no-auto-mic", action="store_true",
                        help="do not write MIC_OPEN; wait for a physical voice-key edge")
    parser.add_argument("--diagnostic-auto-mic", action="store_true",
                        help="explicit diagnostic only: repeatedly open microphone")
    parser.add_argument("--mic-seconds", type=float, default=6.0,
                        help="length of each host-driven mic window (default 6 s)")
    parser.add_argument("--mic-gap", type=float, default=2.0,
                        help="pause between mic windows, lets the IME commit text")
    parser.add_argument("--no-focus", action="store_true",
                        help="compatibility flag; the receiver never selects a text window")
    parser.add_argument("--max-seconds", type=float, default=0.0,
                        help="auto-stop after N seconds (0 = run until interrupted)")
    parser.add_argument("--evidence-dir", default=str(EVIDENCE))
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    from .process_control import ProcessControl
    control=ProcessControl()
    if args.stop:
        control.request_stop()
        ProcessControl(name=control.name+'-login').request_stop()
        ProcessControl(name=control.name+'-keys').request_stop()
        return 0
    require_device()
    if not control.start():
        return 0

    from .runner import Bridge, JsonlLogger

    evidence_dir = Path(args.evidence_dir)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log = JsonlLogger(evidence_dir / f"run-{stamp}.jsonl")
    print(f"Evidence: {log.path}", flush=True)
    bridge = Bridge(
        log,
        frame_size=args.frame_size,
        header_bytes=args.frame_header,
        gain_db=args.gain_db,
        hotkey_enabled=not args.no_hotkey,
        audio_enabled=not args.no_audio,
        send_get_caps=not args.no_get_caps,
        max_seconds=args.max_seconds,
        auto_mic=args.diagnostic_auto_mic and not args.no_auto_mic,
        mic_seconds=args.mic_seconds,
        mic_gap=args.mic_gap,
        caps_v10=args.caps_v10,
        focus_text=False,
    )
    try:
        control.watch(bridge.stop_event)
        if not args.no_hotkey:
            from .login_start import ensure_supervisor
            ensure_supervisor()
            from .key_mapping import spawn
            if extra_keys_enabled():spawn()
        connected = bridge.run()
    except KeyboardInterrupt:
        print("\nInterrupted; releasing shortcut and closing streams...", flush=True)
        bridge.stop_event.set()
        bridge.hotkey.release()
        bridge.sink.stop()
        return 130
    finally:
        ProcessControl(name=control.name+'-keys').request_stop()
        control.close()
    return 0 if connected else 2


if __name__ == "__main__":
    raise SystemExit(main())
