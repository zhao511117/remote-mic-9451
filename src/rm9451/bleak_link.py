# SPDX-License-Identifier: GPL-3.0-only
"""Bleak transport for the 18D1:9451:0105 "Google TV Remote".

Why this exists
---------------
WinRT's GATT service discovery hangs indefinitely immediately after a freshly
established connection (2026-11-08: connection_status=1 then no progress for
72 s; ``asyncio.wait_for`` cannot cancel it -- Microsoft documents that the
connection process is not cancellable). Bleak's Windows backend is the exact
path that already captured 2191 real audio frames from THIS device in
``vibe/probe/voice_test.py`` (voice1.log), so we reuse the proven route.

Handshake proven on this unit (voice1.log)
------------------------------------------
    host writes 0A            -> CTL 0b 01 00 01 03 00 86 00 00 (CAPS_RESP)
    host writes 0C  (MIC_OPEN)-> CTL 04 01 01 00 (AUDIO_START) + audio stream
    host writes 0D (MIC_CLOSE)-> stream ends
    per 134-byte frame        -> CTL 0a 00 NN (big-endian frame counter)

So the host MUST open the microphone itself. Pressing the physical voice key
alone never produced audio in any Windows capture on this machine.

Only the ATVV GATT path is used. No pairing changes, no system settings, no
default-device changes. BLE addresses stay in memory and are never logged.
"""
from __future__ import annotations

import asyncio
from typing import Callable, Optional

from . import protocol as proto
from .ble import DEVICE_ADDRESS, LinkNotConnectedError

TX_UUID = proto.VOICE_TX_UUID
AUDIO_UUID = proto.VOICE_AUDIO_UUID
CONTROL_UUID = proto.VOICE_CONTROL_UUID
_NAME_KEYS = ("google tv remote", "chromecast", "ztka", "google")


def _cached_address() -> str:
    """Colon-form BLE address of the single paired remote, from the cache."""
    raw = f"{DEVICE_ADDRESS:012X}"
    return ":".join(raw[i:i + 2] for i in range(0, 12, 2))


class BleakAtvvLink:
    """One BLE session. Create a fresh instance for every reconnect."""

    def __init__(self, on_audio: Callable[[bytes], None],
                 on_control: Callable[[bytes], None],
                 on_disconnected: Callable[[], None],
                 log: Callable[[str, dict], None],
                 *, send_get_caps: bool = True,
                 caps_v10: bool = False) -> None:
        self._on_audio = on_audio
        self._on_control = on_control
        self._on_disconnected = on_disconnected
        self._log = log
        self._send_get_caps = send_get_caps
        self._caps_v10 = caps_v10
        self._client = None
        self._scanner = None
        self._caps_seen = asyncio.Event()
        self._mic_open = False
        self._closed = False
        # A failed direct-connect attempt can emit a disconnect callback;
        # ignore it until the link is actually armed.
        self._armed = False

    # ---- TX -------------------------------------------------------------
    async def write_tx(self, data: bytes) -> bool:
        """Write an ATVV command. Logs opcode/length only, never the address."""
        client = self._client
        if client is None or not client.is_connected:
            return False
        try:
            await asyncio.wait_for(
                client.write_gatt_char(TX_UUID, data, response=False), timeout=5.0)
            return True
        except Exception as exc:
            self._log("tx_write_error", {"error_type": type(exc).__name__,
                                         "opcode": data[0] if data else None,
                                         "bytes": len(data)})
            return False

    async def open_mic(self) -> bool:
        if self._mic_open:
            return True
        ok = await self.write_tx(proto.MIC_OPEN_PROVEN)
        if ok:
            self._mic_open = True
            self._log("mic_open_sent", {"bytes": len(proto.MIC_OPEN_PROVEN)})
        return ok

    async def close_mic(self) -> bool:
        if not self._mic_open:
            return True
        ok = await self.write_tx(proto.MIC_CLOSE_PROVEN)
        self._mic_open = False
        self._log("mic_close_sent", {"ok": ok,
                                     "bytes": len(proto.MIC_CLOSE_PROVEN)})
        return ok

    # ---- connect --------------------------------------------------------
    async def connect(self, *, wait_seconds: float = 25.0, stop_event=None) -> dict:
        from bleak import BleakClient, BleakScanner

        # Phase 1: connect straight to the paired Windows cache address. A
        # paired remote that just woke up is already connected to Windows, so
        # no scannable advertisement is required. This is the same address the
        # working probe used, just obtained from the pairing cache.
        client = None
        source = None
        name = "Google TV Remote"
        self._log("direct_connect_attempt", {})
        candidate = BleakClient(_cached_address(), timeout=12.0,
                                disconnected_callback=self._on_ble_disconnect)
        try:
            await asyncio.wait_for(candidate.connect(), timeout=14.0)
            if candidate.is_connected:
                client, source = candidate, "paired_cache"
            else:
                try:
                    await asyncio.wait_for(candidate.disconnect(), timeout=3.0)
                except Exception:
                    pass
        except Exception as exc:
            self._log("direct_connect_failed",
                      {"error_type": type(exc).__name__})
            try:
                await asyncio.wait_for(candidate.disconnect(), timeout=3.0)
            except Exception:
                pass
        if client is not None:
            self._log("direct_connect_ok", {"source": source})

        # Phase 2: fall back to the proven scan-for-advertisement route.
        if client is None:
            found: dict = {}
            advertised = asyncio.Event()

            def on_detect(device, adv):
                if advertised.is_set():
                    return
                detected = str(getattr(adv, "local_name", None)
                               or getattr(device, "name", "") or "").lower()
                if any(key in detected for key in _NAME_KEYS):
                    found["address"] = str(device.address)
                    found["name"] = detected
                    advertised.set()

            scanner = BleakScanner(detection_callback=on_detect)
            self._scanner = scanner
            await scanner.start()
            waited = 0.0
            try:
                while not advertised.is_set() and waited < wait_seconds:
                    if stop_event is not None and stop_event.is_set():
                        raise LinkNotConnectedError("stopped")
                    await asyncio.sleep(0.2)
                    waited += 0.2
            finally:
                try:
                    await scanner.stop()
                except Exception as exc:
                    self._log("scanner_stop_error",
                              {"error_type": type(exc).__name__})
                self._scanner = None

            if not advertised.is_set():
                raise LinkNotConnectedError("no_advertisement")
            # Raw BLE address stays in memory; evidence logs carry the name only.
            name = found["name"]
            self._log("remote_advertised", {"name": name,
                                            "waited_seconds": round(waited, 1)})
            client = BleakClient(found["address"], timeout=25.0,
                                 disconnected_callback=self._on_ble_disconnect)
            source = "advertisement"
            try:
                await asyncio.wait_for(client.connect(), timeout=30.0)
            except Exception as exc:
                raise LinkNotConnectedError(
                    f"bleak connect: {type(exc).__name__}") from exc

        self._client = client
        self._armed = True
        self._log("bleak_connected", {"is_connected": bool(client.is_connected),
                                      "source": source})

        # Already paired in Windows. Best-effort, mirrors the proven probe and
        # must never hold up the link (bleak 2.x pair() can be a no-op).
        try:
            await asyncio.wait_for(client.pair(), timeout=4.0)
            self._log("bleak_pair", {"ok": True})
        except Exception as exc:
            self._log("bleak_pair", {"ok": False,
                                     "error_type": type(exc).__name__})

        # CTL before AUDIO: the exact order used in the working probe.
        for role, uuid, handler in (("control", CONTROL_UUID, self._handle_control),
                                    ("audio", AUDIO_UUID, self._handle_audio)):
            try:
                await asyncio.wait_for(client.start_notify(uuid, handler),
                                       timeout=12.0)
                self._log("cccd", {"role": role, "ok": True})
            except Exception as exc:
                self._log("cccd_error", {"role": role,
                                         "error_type": type(exc).__name__})
                raise LinkNotConnectedError(
                    f"cccd {role}: {type(exc).__name__}") from exc

        if self._send_get_caps:
            command = (proto.GET_CAPABILITIES_V10 if self._caps_v10
                       else proto.GET_CAPS_PROVEN)
            await self.write_tx(command)
            self._log("get_caps_sent", {"bytes": len(command),
                                        "variant": "v10" if self._caps_v10 else "proven"})
            # Wait for CAPS before any MIC_OPEN so the decoder is configured
            # from the device's real frame size before audio starts flowing.
            try:
                await asyncio.wait_for(self._caps_seen.wait(), timeout=2.5)
                self._log("caps_seen_by_link", {})
            except asyncio.TimeoutError:
                self._log("caps_not_seen_by_link", {})

        return {"name": name, "transport": "bleak", "source": source,
                "is_connected": bool(client.is_connected)}

    # ---- notification callbacks ----------------------------------------
    def _handle_audio(self, _sender, data) -> None:
        try:
            self._on_audio(bytes(data))
        except Exception:
            pass

    def _handle_control(self, _sender, data) -> None:
        payload = bytes(data)
        try:
            self._on_control(payload)
        except Exception:
            pass
        if not payload:
            return
        opcode = payload[0]
        if opcode == proto.OPCODE_CAPS:
            self._caps_seen.set()
        elif opcode == proto.OPCODE_MIC_OPEN_REQUEST:
            # Voice key pressed on the remote: spec says the host must answer
            # with MIC_OPEN. Idempotent with our own auto-mic cycle.
            self._log("mic_open_request", {"length": len(payload)})
            if not self._mic_open:
                try:
                    asyncio.get_running_loop().create_task(self.open_mic())
                except RuntimeError:
                    pass

    def _on_ble_disconnect(self, *args) -> None:
        if self._closed or not self._armed:
            return
        self._log("ble_disconnected", {})
        self._on_disconnected()

    # ---- teardown -------------------------------------------------------
    async def close(self) -> None:
        self._closed = True
        self._armed = False
        client = self._client
        if client is not None and client.is_connected:
            # Never leave the remote's microphone open on exit.
            try:
                await self.close_mic()
            except Exception as exc:
                self._log("mic_close_error", {"error_type": type(exc).__name__})
            for role, uuid in (("audio", AUDIO_UUID), ("control", CONTROL_UUID)):
                try:
                    await asyncio.wait_for(client.stop_notify(uuid), timeout=2.0)
                except Exception as exc:
                    self._log("unsubscribe_error", {"role": role,
                                                    "error_type": type(exc).__name__})
            try:
                await asyncio.wait_for(client.disconnect(), timeout=4.0)
            except Exception as exc:
                self._log("disconnect_error", {"error_type": type(exc).__name__})
        self._client = None
        self._mic_open = False
        self._log("link_closed", {})
