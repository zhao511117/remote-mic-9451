# SPDX-License-Identifier: GPL-3.0-only
"""WinRT BLE link, scoped to the ONE paired device on this machine.

Connection uses the Windows pairing cache (BluetoothLEDevice.from_bluetooth_
address_async), not BleakScanner: this remote stops advertising when asleep.
Verified on this host before writing this module: 10 cached services, HID
0x1812 and ATVV ab5e0001 with TX/AUDIO/CONTROL characteristics present.
"""
from __future__ import annotations

import asyncio
from typing import Callable, Optional

from . import protocol as proto

from .config import device_identity,require_device
DEVICE_ADDRESS = device_identity()[0]

AudioCallback = Callable[[bytes], None]
ControlCallback = Callable[[bytes], None]


class BleUnavailableError(RuntimeError):
    pass


class LinkNotConnectedError(RuntimeError):
    pass


def _winrt_modules():
    try:
        from winrt.windows.devices.bluetooth import (
            BluetoothCacheMode,
            BluetoothConnectionStatus,
            BluetoothLEDevice,
        )
        from winrt.windows.devices.bluetooth.genericattributeprofile import (
            GattClientCharacteristicConfigurationDescriptorValue,
            GattCommunicationStatus,
            GattSession,
        )
        from winrt.windows.storage.streams import DataWriter
        import winrt.windows.foundation  # noqa: F401 - projection must load up front
        import winrt.windows.foundation.collections  # noqa: F401
    except ImportError as exc:
        raise BleUnavailableError(f"winrt Bluetooth projection missing: {exc}") from exc
    return {
        "le": BluetoothLEDevice,
        "cache_mode": BluetoothCacheMode,
        "connection_status": BluetoothConnectionStatus,
        "cccd": GattClientCharacteristicConfigurationDescriptorValue,
        "gatt_status": GattCommunicationStatus,
        "session": GattSession,
        "writer": DataWriter,
    }


class AtvvLink:
    """One GATT session. Not reusable after close(); create a new one."""

    def __init__(self, on_audio: AudioCallback, on_control: ControlCallback,
                 on_disconnected: Callable[[], None], log: Callable[[str, dict], None],
                 *, send_get_caps: bool = True) -> None:
        self._on_audio = on_audio
        self._on_control = on_control
        self._on_disconnected = on_disconnected
        self._log = log
        self._send_get_caps = send_get_caps
        self._winrt = None
        self._device = None
        self._service = None
        self._tx = None
        self._audio = None
        self._control = None
        self._audio_token = None
        self._control_token = None
        self._connection_token = None
        self._gatt_session = None
        self._closed = False

    @staticmethod
    def _uuid(obj) -> str:
        return str(obj).casefold()

    async def connect(self, *, wait_seconds: float = 25.0, stop_event=None) -> dict:
        self._winrt = _winrt_modules()
        winrt = self._winrt
        dev = await winrt["le"].from_bluetooth_address_async(DEVICE_ADDRESS)
        if dev is None:
            raise LinkNotConnectedError("from_bluetooth_address_async returned null")
        self._device = dev
        disconnected = int(winrt["connection_status"].DISCONNECTED)

        # Microsoft GATT Client guidance: creating a BluetoothLEDevice alone
        # does not necessarily connect. MaintainConnection = True makes the OS
        # wait indefinitely and connect as soon as the peripheral advertises
        # again -- exactly what is needed for a remote that only wakes on a key
        # press. Best-effort: fall back to polling if the projection lacks it.
        try:
            # winrt-python requires the BluetoothDeviceId OBJECT here. Passing
            # the string BluetoothLEDevice.device_id fails with
            # "TypeError: convert_to returned null" on this projection.
            bluetooth_id = getattr(dev, "bluetooth_device_id", None)
            if bluetooth_id is None:
                bluetooth_id = getattr(dev, "device_id", None)
            if bluetooth_id is not None:
                self._gatt_session = await winrt["session"].from_device_id_async(bluetooth_id)
                self._gatt_session.maintain_connection = True
                self._log("gatt_session_maintain", {"ok": bool(self._gatt_session.maintain_connection)})
        except Exception as exc:
            self._log("gatt_session_unavailable", {"error_type": type(exc).__name__,
                                                   "detail": str(exc)[:120]})

        name = str(getattr(dev, "name", "") or "")
        status = int(getattr(dev, "connection_status", 0))
        # Hold this connection attempt open while the OS waits for the remote
        # to advertise. One key press anywhere in this window is enough.
        waited = 0.0
        while status == disconnected and waited < wait_seconds:
            if stop_event is not None and stop_event.is_set():
                raise LinkNotConnectedError("stopped")
            await asyncio.sleep(0.5)
            waited += 0.5
            status = int(getattr(dev, "connection_status", 0))
        self._log("device_open", {"name": name, "connection_status": status,
                                  "waited_seconds": round(waited, 1)})
        if status == disconnected:
            raise LinkNotConnectedError("remote_asleep")

        # Passing a cache mode to get_gatt_services_async() raises "Invalid
        # parameter count" on this projection, but the dedicated
        # *_with_cache_mode_async overloads exist. Use CACHED explicitly: the
        # no-arg overload returns the pairing cache instantly while
        # disconnected, yet hangs right after a fresh connection because
        # Windows starts an uncached GATT refresh (observed 2026-11-08: both
        # real runs reached device_open/status=1 and never reached CCCD).
        await asyncio.sleep(0.8)  # let the freshly established link settle
        self._log("gatt_step", {"step": "services_begin", "mode": "cached"})
        try:
            services_result = await asyncio.wait_for(
                dev.get_gatt_services_with_cache_mode_async(winrt["cache_mode"].CACHED),
                timeout=8.0,
            )
        except Exception as exc:
            self._log("gatt_step", {"step": "services_failed", "error_type": type(exc).__name__})
            raise LinkNotConnectedError(f"service discovery: {type(exc).__name__}") from exc
        services = list(services_result.services)
        self._log("gatt_step", {"step": "services_done", "count": len(services)})
        service = next(
            (s for s in services if self._uuid(s.uuid) == proto.VOICE_SERVICE_UUID),
            None,
        )
        if service is None:
            raise LinkNotConnectedError("ATVV service absent from GATT table")
        self._service = service

        self._log("gatt_step", {"step": "chars_begin", "mode": "cached"})
        try:
            chars_result = await asyncio.wait_for(
                service.get_characteristics_with_cache_mode_async(winrt["cache_mode"].CACHED),
                timeout=8.0,
            )
        except Exception as exc:
            self._log("gatt_step", {"step": "chars_failed", "error_type": type(exc).__name__})
            raise LinkNotConnectedError(f"characteristic discovery: {type(exc).__name__}") from exc
        chars = list(chars_result.characteristics)
        self._log("gatt_step", {"step": "chars_done", "count": len(chars)})
        wanted = {
            proto.VOICE_TX_UUID: "tx",
            proto.VOICE_AUDIO_UUID: "audio",
            proto.VOICE_CONTROL_UUID: "control",
        }
        for characteristic in chars:
            role = wanted.get(self._uuid(characteristic.uuid))
            if role == "tx":
                self._tx = characteristic
            elif role == "audio":
                self._audio = characteristic
            elif role == "control":
                self._control = characteristic
        if self._tx is None or self._audio is None or self._control is None:
            raise LinkNotConnectedError("ATVV characteristic set incomplete")

        self._audio_token = self._audio.add_value_changed(self._handle_audio)
        self._control_token = self._control.add_value_changed(self._handle_control)
        self._connection_token = dev.add_connection_status_changed(
            self._handle_connection_status
        )

        self._log("gatt_step", {"step": "handlers_registered"})
        for role, characteristic in (("audio", self._audio), ("control", self._control)):
            self._log("gatt_step", {"step": "cccd_begin", "role": role})
            try:
                subscribe = await asyncio.wait_for(
                    characteristic.write_client_characteristic_configuration_descriptor_async(
                        winrt["cccd"].NOTIFY
                    ),
                    timeout=8.0,
                )
                ok = subscribe == winrt["gatt_status"].SUCCESS
            except Exception as exc:  # unreachable while the remote sleeps
                self._log("cccd_error", {"role": role, "error_type": type(exc).__name__})
                raise LinkNotConnectedError(f"cccd {role}: {type(exc).__name__}") from exc
            self._log("cccd", {"role": role, "ok": bool(ok)})
            if not ok:
                raise LinkNotConnectedError(f"cccd {role} status={subscribe}")

        if self._send_get_caps:
            # Optional negotiation. Hold-to-talk streaming does not depend on
            # it, so a failed write must not tear down the link.
            try:
                await asyncio.wait_for(self._write(proto.GET_CAPABILITIES_V10), timeout=5.0)
                self._log("get_caps_sent", {"bytes": len(proto.GET_CAPABILITIES_V10)})
            except Exception as exc:
                self._log("get_caps_failed", {"error_type": type(exc).__name__})

        return {"name": name, "connection_status": status, "services": len(services),
                "characteristics": len(chars)}

    def _handle_audio(self, _sender, args) -> None:
        try:
            self._on_audio(bytes(args.characteristic_value))
        except Exception:
            pass  # WinRT callback thread must never leak an exception

    def _handle_control(self, _sender, args) -> None:
        try:
            self._on_control(bytes(args.characteristic_value))
        except Exception:
            pass

    def _handle_connection_status(self, sender, _args) -> None:
        try:
            disconnected = sender.connection_status == self._winrt["connection_status"].DISCONNECTED
        except Exception:
            disconnected = True
        if disconnected and not self._closed:
            self._log("ble_disconnected", {})
            self._on_disconnected()

    async def _write(self, data: bytes) -> None:
        writer = self._winrt["writer"]()
        try:
            writer.write_bytes(bytes(data))
            result = await self._tx.write_value_with_result_async(writer.detach_buffer())
        finally:
            writer.close()
        if result.status != self._winrt["gatt_status"].SUCCESS:
            raise LinkNotConnectedError(f"tx write status={result.status}")

    async def close(self) -> None:
        self._closed = True
        for characteristic, role in ((self._audio, "audio"), (self._control, "control")):
            if characteristic is None:
                continue
            try:
                await asyncio.wait_for(
                    characteristic.write_client_characteristic_configuration_descriptor_async(
                        self._winrt["cccd"].NONE
                    ),
                    timeout=2.0,
                )
            except Exception as exc:
                self._log("unsubscribe_error", {"role": role, "error_type": type(exc).__name__})
        for holder, token, method in (
            (self._audio, self._audio_token, "remove_value_changed"),
            (self._control, self._control_token, "remove_value_changed"),
            (self._device, self._connection_token, "remove_connection_status_changed"),
        ):
            if holder is not None and token is not None:
                try:
                    getattr(holder, method)(token)
                except Exception as exc:
                    self._log("token_error", {"error_type": type(exc).__name__})
        self._audio_token = self._control_token = self._connection_token = None
        if self._gatt_session is not None:
            try:
                self._gatt_session.maintain_connection = False
                self._gatt_session.close()
            except Exception as exc:
                self._log("gatt_session_close_error", {"error_type": type(exc).__name__})
            self._gatt_session = None
        if self._device is not None:
            try:
                self._device.close()
            except Exception:
                pass
        self._device = self._service = self._tx = self._audio = self._control = None
        self._log("link_closed", {})


async def read_only_probe() -> dict:
    """Self-test: enumerate cached GATT without subscribing or writing."""
    require_device()
    winrt = _winrt_modules()
    dev = await winrt["le"].from_bluetooth_address_async(DEVICE_ADDRESS)
    result = await dev.get_gatt_services_async()
    names = [str(s.uuid).casefold() for s in result.services]
    atvv = next((s for s in result.services
                 if str(s.uuid).casefold() == proto.VOICE_SERVICE_UUID), None)
    chars = []
    if atvv is not None:
        chars_result = await atvv.get_characteristics_async()
        chars = [str(c.uuid).casefold() for c in chars_result.characteristics]
    info = {
        "name": str(getattr(dev, "name", "") or ""),
        "connection_status": int(getattr(dev, "connection_status", 0)),
        "service_count": len(names),
        "has_hid": any(u.startswith("00001812") for u in names),
        "has_atvv": atvv is not None,
        "atvv_characteristics": chars,
    }
    dev.close()
    return info
