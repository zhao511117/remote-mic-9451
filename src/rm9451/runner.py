# SPDX-License-Identifier: GPL-3.0-only
"""Session state machine: BLE notifications -> PCM -> CABLE Input, with
hold-to-talk shortcut edges and hard cleanup guarantees.

Device-scoped BLE/audio session owner. The output adapter handles the verified
Doubao shortcut. No system-default, pairing, proxy, or AV settings are changed.
"""
from __future__ import annotations

import asyncio
from collections import deque
import json
import queue
import threading
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from . import ble, cached_link as bleak_link, output, protocol as proto


class JsonlLogger:
    """Structured, privacy-safe evidence. No audio, text, address or credentials."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        self._stream = path.open("a", encoding="utf-8", newline="\n")

    def __call__(self, event: str, data: Optional[dict] = None) -> None:
        row = {"ts": datetime.now().isoformat(timespec="milliseconds"), "event": event}
        if data:
            row.update(data)
        line = json.dumps(row, ensure_ascii=False, sort_keys=True)
        with self._lock:
            self._stream.write(line + "\n")
            self._stream.flush()
        if sys.stdout is not None:
            print(f"[{row['ts'][11:]}] {event} {json.dumps(data or {}, ensure_ascii=False, sort_keys=True)}",flush=True)

    def close(self) -> None:
        with self._lock:
            try:
                self._stream.close()
            except Exception:
                pass


class Bridge:
    FLUSH_SECONDS = 0.15
    NO_AUDIO_WARN_SECONDS = 3.0
    MAX_SESSION_SECONDS = 120.0

    def __init__(self, log: JsonlLogger, *, frame_size: int = proto.DEFAULT_FRAME_SIZE,
                 header_bytes: int = proto.DEFAULT_FRAME_HEADER, gain_db: float = 3.0,
                 hotkey_enabled: bool = True, audio_enabled: bool = True,
                 send_get_caps: bool = True, max_seconds: float = 0.0,
                 auto_mic: bool = False, mic_seconds: float = 6.0,
                 mic_gap: float = 2.0, caps_v10: bool = True,
                 focus_text: bool = False) -> None:
        self.log = log
        self.decoder = proto.VoiceDecoder(frame_size, header_bytes, gain_db=gain_db,packet_aligned=True)
        self.sink = output.CableSink(log, enabled=audio_enabled)
        self.hotkey = output.RightAltHold(log, enabled=hotkey_enabled)
        self.events: "queue.Queue[tuple]" = queue.Queue(maxsize=4096)
        self.stop_event = threading.Event()
        self.disconnected = threading.Event()
        self.send_get_caps = send_get_caps
        self.max_seconds = max_seconds
        self.hotkey_enabled = hotkey_enabled
        self.audio_enabled = audio_enabled
        # This remote only streams after the HOST writes MIC_OPEN, so we drive
        # bounded mic windows instead of waiting for a physical key edge.
        self.auto_mic = auto_mic
        self.mic_seconds = max(1.0, mic_seconds)
        self.mic_gap = max(0.5, mic_gap)
        self.caps_v10 = caps_v10
        self.focus_text = focus_text
        self.MAX_SESSION_SECONDS = max(120.0, self.mic_seconds + 4.0)

        self._recording = False
        self._started_at = 0.0
        self._last_audio_at = 0.0
        self._stop_at: Optional[float] = None
        self._warned_no_audio = False
        self._audio_notifications = 0
        self._dropped_notifications = 0
        self._sessions = 0
        self._caps_seen = False
        self._frame_counters = 0
        self._connections = 0
        self.navigation = None
        self._prestart_audio = deque(maxlen=14)

    # ---- BLE callbacks (run on WinRT callback threads) ----
    def _on_audio(self, data: bytes) -> None:
        self._push(("audio", data))

    def _on_control(self, data: bytes) -> None:
        self._push(("control", data))

    def _push(self, item: tuple) -> None:
        try:
            self.events.put_nowait(item)
        except queue.Full:
            self._dropped_notifications += 1

    def _on_disconnected(self) -> None:
        self.disconnected.set()

    # ---- consumer thread: owns decoder / sink / hotkey ----
    def _consume(self) -> None:
        while not self.stop_event.is_set():
            now = time.monotonic()
            if self.disconnected.is_set() and self._recording:
                self._finish("ble_disconnected")
            if self._stop_at is not None and now - self._stop_at >= self.FLUSH_SECONDS:
                self._finish("device_stop")
            if self._recording:
                if not self._warned_no_audio and now - self._last_audio_at > self.NO_AUDIO_WARN_SECONDS:
                    self._warned_no_audio = True
                    self.log("no_audio_yet", {"seconds": round(now - self._started_at, 2)})
                if now - self._started_at > self.MAX_SESSION_SECONDS:
                    self._finish("watchdog_timeout")
            try:
                kind, payload = self.events.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                if kind == "control":
                    self._handle_control(payload)
                elif kind == "audio":
                    self._handle_audio(payload)
                elif kind == "session_start":
                    self._begin_session("auto_mic")
                elif kind == "session_stop":
                    self._finish("mic_window_end")
            except Exception as exc:
                self.log("consumer_error", {"error_type": type(exc).__name__})

    def _handle_control(self, data: bytes) -> None:
        try:
            event = proto.parse_control(data)
        except proto.ProtocolError as exc:
            self.log("control_rejected", {"error_type": type(exc).__name__, "length": len(data)})
            return
        if event.kind == "caps":
            self._caps_seen = True
            self.decoder.configure(event.caps, header_bytes=self.decoder.frames.header_bytes)
            self.log("caps", {"version": event.caps.version, "codec": event.caps.codec,
                              "sample_rate": event.caps.sample_rate,
                              "frame_size": event.caps.frame_size,
                              "header_bytes": self.decoder.frames.header_bytes,
                              "raw_hex": event.caps.raw.hex()})
        elif event.kind == "start":
            if self._stop_at is not None:
                self._finish('next_session')
            self.log("control_start", {"reason": event.reason, "codec": event.codec,
                                       "session_id": event.session_id,
                                       "control_hex": data.hex()})
            self._begin_session("control_start")
        elif event.kind == "stop":
            self.log("audio_stop", {"control_hex": data.hex(),
                                     **proto.frame_stats(self.decoder)})
            self._stop_at=time.monotonic()
        elif event.kind == "sync":
            self.decoder.set_sync(event.predictor or 0, event.step_index or 0)
            self.log("audio_sync", {"predictor": event.predictor, "step_index": event.step_index})
        elif event.kind == "mic_button":
            self.log("mic_button", {})
        elif event.kind == "frame_counter":
            # One per 134-byte frame on this model (~315 in a 10 s stream).
            # Count only; these are NOT ADPCM predictor resets.
            self._frame_counters += 1
        else:
            self.log("unknown_control", {"opcode": event.opcode, "length": len(data)})

    def _begin_session(self, reason: str) -> None:
        """Idempotent: CTL 04 and the host-driven mic window can both fire."""
        if self._recording:
            return
        if self.disconnected.is_set():
            self.log("start_ignored_disconnected", {})
            return
        self._sessions += 1
        self._recording = True
        self._started_at = self._last_audio_at = time.monotonic()
        self._stop_at = None
        self._warned_no_audio = False
        self.decoder.on_start()
        self.log("audio_start", {"session": self._sessions, "reason": reason,
                                 **proto.frame_stats(self.decoder)})
        self.hotkey.press()
        # GATT characteristics have separate callback queues. An audio
        # notification can reach Python just before its control START.
        pending=list(self._prestart_audio)
        self._prestart_audio.clear()
        for when,data in pending:
            if when>=self._started_at-.15:
                self._feed_audio(data)

    def _handle_audio(self, data: bytes) -> None:
        self._audio_notifications += 1
        if not self._recording:
            if self._caps_seen and not self.disconnected.is_set():
                self._prestart_audio.append((time.monotonic(),data))
            return  # never play unsolicited audio into the virtual microphone
        self._feed_audio(data)

    def _feed_audio(self,data):
        self._last_audio_at = time.monotonic()
        pcm48 = self.decoder.feed(data)
        if pcm48:
            self.sink.write(pcm48)
            if self.decoder.frames.frames_seen and self.decoder.frames.frames_seen % 25 == 0:
                self.log("audio_progress", proto.frame_stats(self.decoder))

    def _finish(self, reason: str) -> None:
        if not self._recording and self._stop_at is None:
            return
        self._recording = False
        self._prestart_audio.clear()
        self._stop_at = None
        self.hotkey.release()
        self.log("session_end", {"reason": reason, "sessions": self._sessions,
                                 "audio_notifications": self._audio_notifications,
                                 "frame_counters": self._frame_counters,
                                 "dropped_notifications": self._dropped_notifications,
                                 **proto.frame_stats(self.decoder)})

    # ---- asyncio BLE owner ----
    async def _ble_loop(self) -> None:
        attempts = 0
        while not self.stop_event.is_set():
            link = bleak_link.BleakAtvvLink(
                self._on_audio, self._on_control, self._on_disconnected, self.log,
                send_get_caps=self.send_get_caps, caps_v10=self.caps_v10,
                clone_hold_cycle=not self.auto_mic)
            mic_open = False
            next_mic_at = 0.0
            mic_deadline: Optional[float] = None
            try:
                info = await link.connect(wait_seconds=25.0, stop_event=self.stop_event)
                attempts = 0
                self.disconnected.clear()
                self._connections += 1
                self.log("connected", info)
                print("READY: hold the microphone button to speak; release to finish.",
                      flush=True)
                next_mic_at = time.monotonic() + 0.4
                while not self.stop_event.is_set() and not self.disconnected.is_set():
                    now = time.monotonic()
                    if self.auto_mic:
                        if not mic_open and now >= next_mic_at:
                            if await link.open_mic():
                                mic_open = True
                                mic_deadline = now + self.mic_seconds
                                self._push(("session_start", b""))
                            else:
                                next_mic_at = now + 1.0
                        elif mic_open and mic_deadline is not None and now >= mic_deadline:
                            self._push(("session_stop", b""))
                            # Let the consumer release right Alt before the
                            # stream stops, so trailing PCM is not discarded.
                            await asyncio.sleep(0.15)
                            await link.close_mic()
                            mic_open = False
                            mic_deadline = None
                            next_mic_at = time.monotonic() + self.mic_gap
                            self.log("mic_window_done", {"next_in_s": self.mic_gap})
                    await asyncio.sleep(0.1)
            except Exception as exc:
                attempts += 1
                if attempts <= 3 or attempts % 10 == 0:
                    self.log("connect_error", {"attempt": attempts,
                                               "error_type": type(exc).__name__,
                                               "detail": str(exc)[:160]})
                if attempts == 1:
                    print("Tip: press ANY key on the remote to wake it (BLE sleep).", flush=True)
            finally:
                self.disconnected.set()
                self._push(("session_stop", b""))
                try:
                    await link.close()
                except Exception as exc:
                    self.log("close_error", {"error_type": type(exc).__name__})
            if self.stop_event.is_set():
                break
            await asyncio.sleep(1.0)

    def run(self) -> None:
        self.hotkey.prepare()
        if self.hotkey_enabled:
            from .buttons import Navigation
            self.navigation=Navigation(self.log)
            self.navigation.start()
        chosen = self.sink.start()
        self.log("bridge_start", {"frame_size": self.decoder.frames.frame_size,
                                  "header_bytes": self.decoder.frames.header_bytes,
                                  "gain_db": self.decoder.gain,
                                  "hotkey": self.hotkey_enabled,
                                  "audio": self.audio_enabled,
                                  "endpoint": chosen.get("index", -1) if chosen else -1})
        consumer = threading.Thread(target=self._consume, name="bridge-consumer", daemon=True)
        consumer.start()
        deadline = time.monotonic() + self.max_seconds if self.max_seconds > 0 else None
        try:
            if deadline is None:
                asyncio.run(self._ble_loop())
            else:
                async def _bounded():
                    task = asyncio.create_task(self._ble_loop())
                    await asyncio.sleep(max(0.0, deadline - time.monotonic()))
                    self.stop_event.set()
                    self.disconnected.set()
                    try:
                        await asyncio.wait_for(task, timeout=12.0)
                    except (asyncio.TimeoutError, asyncio.CancelledError):
                        task.cancel()
                        try:
                            await task
                        except BaseException:
                            pass
                        self.log("bounded_cancel", {})
                asyncio.run(_bounded())
        except Exception as exc:
            self.log("bridge_error", {"error_type": type(exc).__name__})
        finally:
            self.stop_event.set()
            self.disconnected.set()
            self.hotkey.release()
            self.hotkey.close()
            if self.navigation:self.navigation.stop()
            self.sink.stop()
            consumer.join(timeout=2.0)
            self.log("bridge_stop", {"sessions": self._sessions,
                                     "audio_notifications": self._audio_notifications,
                                     "caps_seen": self._caps_seen,
                                     "written_bytes": self.sink.written_bytes,
                                     "dropped_blocks": self.sink.dropped_blocks})
            self.log.close()
        return self._connections > 0
