# SPDX-License-Identifier: GPL-3.0-only
"""Windows output side: VB-CABLE playback + physical right-Alt hold.

Only the already-configured CABLE Input endpoint is opened; the system default
recording device is never touched. The right-Alt edge is injected as an E0
extended scan-code event (VK_RMENU), matching the upstream client's shortcut
delivery so DoubaoIME recognises it as *right* Alt.
"""
from __future__ import annotations

import atexit
import ctypes
from ctypes import wintypes
import queue
import threading
from typing import List, Optional

DEFAULT_CABLE_NAME = "CABLE Input (VB-Audio Virtual Cable)"
DEFAULT_HOST_API = "Windows WASAPI"
from .config import settings
DEFAULT_CABLE_NAME=settings().get('audio',{}).get('output_name') or DEFAULT_CABLE_NAME
DEFAULT_HOST_API=settings().get('audio',{}).get('host_api') or DEFAULT_HOST_API
OUTPUT_RATE = 48000

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
KEYEVENTF_EXTENDEDKEY = 0x0001
VK_RMENU = 0xA5
RALT_SCAN = 0x38  # E0 38 on PS/2 Set 1 == right Alt


class _KeyBdInput(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _HardwareInput(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _InputUnion(ctypes.Union):
    _fields_ = [("ki", _KeyBdInput), ("mi", _MouseInput), ("hi", _HardwareInput)]


class _Input(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _InputUnion)]


def _send_edge(key_up: bool) -> bool:
    """Send one physical right-Alt edge. Returns True if Windows accepted it."""
    user32 = ctypes.windll.user32
    flags = KEYEVENTF_SCANCODE | KEYEVENTF_EXTENDEDKEY
    if key_up:
        flags |= KEYEVENTF_KEYUP
    event = _Input(
        type=INPUT_KEYBOARD,
        u=_InputUnion(ki=_KeyBdInput(
            wVk=0, wScan=RALT_SCAN, dwFlags=flags, time=0, dwExtraInfo=0x524D494300000001,
        )),
    )
    user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(_Input), ctypes.c_int)
    user32.SendInput.restype = wintypes.UINT
    sent = int(user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(_Input)))
    return sent == 1


class RightAltHold:
    """Idempotent hold with a hard release guarantee (no stuck Alt on exit)."""

    def __init__(self, log, *, enabled: bool = True) -> None:
        self._log = log
        self._enabled = enabled
        self._down = False
        self._lock = threading.Lock()
        self._physicalizer = None
        atexit.register(self.release)

    def prepare(self):
        if self._enabled and self._physicalizer is None:
            from .doubao import create_physicalizer
            self._physicalizer=create_physicalizer(self._log)

    def close(self):
        self.release()
        if self._physicalizer:
            self._physicalizer.stop()
            self._physicalizer=None

    @property
    def down(self) -> bool:
        with self._lock:
            return self._down

    def press(self) -> None:
        if not self._enabled:
            return
        with self._lock:
            if self._down:
                return
            from .doubao import activate_current_target
            activate_current_target(self._log)
            ok = _send_edge(False)
            self._down = bool(ok)
            if not ok:
                raise RuntimeError('Right Alt key-down was rejected')
        self._log("ralt_down", {"accepted": bool(ok)})

    def release(self) -> None:
        if not self._enabled:
            return
        with self._lock:
            if not self._down:
                return
            ok = _send_edge(True)
            self._down = False
        self._log("ralt_up", {"accepted": bool(ok)})


class CableSink:
    """Writes decoded 48 kHz mono PCM to CABLE Input as stereo int16."""

    def __init__(self, log, *, name: str = DEFAULT_CABLE_NAME,
                 host_api: str = DEFAULT_HOST_API, enabled: bool = True) -> None:
        self._log = log
        self._name = name
        self._host_api = host_api
        self._enabled = enabled
        self._queue: "queue.Queue[bytes]" = queue.Queue(maxsize=512)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._stream = None
        self.written_bytes = 0
        self.dropped_blocks = 0

    def resolve(self) -> dict:
        import sounddevice as sd
        host_apis = sd.query_hostapis()
        matches = []
        for index, device in enumerate(sd.query_devices()):
            if int(device.get("max_output_channels") or 0) < 2:
                continue
            if str(device.get("name", "")) != self._name:
                continue
            api = str(host_apis[device["hostapi"]]["name"]) if host_apis else ""
            if self._host_api and self._host_api not in api:
                continue
            matches.append({"index": index, "host_api": api,
                            "rate": int(device.get("default_samplerate") or 0),
                            "channels": int(device["max_output_channels"])})
        if not matches:
            raise RuntimeError(f"CABLE Input endpoint not found: {self._name!r} / {self._host_api!r}")
        # Prefer the WASAPI entry with 2 channels (VB-CABLE exposes a 16ch variant).
        matches.sort(key=lambda row: (row["channels"] != 2, row["index"]))
        return matches[0]

    def start(self) -> dict:
        if not self._enabled:
            return {"enabled": False}
        import sounddevice as sd
        chosen = self.resolve()
        self._stream = sd.RawOutputStream(
            samplerate=OUTPUT_RATE,
            blocksize=480,
            device=chosen["index"],
            channels=2,
            dtype="int16",
        )
        self._stream.start()
        self._stop.clear()
        self._thread = threading.Thread(target=self._writer, name="cable-writer", daemon=True)
        self._thread.start()
        self._log("cable_open", chosen)
        return chosen

    def _writer(self) -> None:
        while not self._stop.is_set():
            try:
                chunk = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            data = bytearray(chunk)
            while len(data) < 16384:
                try:
                    data.extend(self._queue.get_nowait())
                except queue.Empty:
                    break
            try:
                self._stream.write(bytes(data))
                self.written_bytes += len(data)
            except Exception as exc:
                self._log("cable_write_error", {"error_type": type(exc).__name__})

    def write(self, samples: List[int]) -> None:
        if not self._enabled or not samples or self._stream is None:
            return
        import numpy as np
        mono = np.asarray(samples, dtype="<i2")
        stereo = np.column_stack((mono, mono)).tobytes()
        try:
            self._queue.put_nowait(stereo)
        except queue.Full:
            self.dropped_blocks += 1

    def stop(self) -> None:
        if self._stream is None:
            return
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        try:
            self._stream.stop()
            self._stream.close()
        except Exception as exc:
            self._log("cable_close_error", {"error_type": type(exc).__name__})
        self._stream = None
        self._log("cable_closed", {"written_bytes": self.written_bytes,
                                   "dropped_blocks": self.dropped_blocks})
