# SPDX-License-Identifier: GPL-3.0-only
"""Address-bound ATVV transport; no implicit BLE scan or pairing changes."""
from __future__ import annotations
import asyncio
import time
from . import protocol as proto
from .ble import DEVICE_ADDRESS, LinkNotConnectedError


def paired_backend():
    from bleak.backends.winrt.client import BleakClientWinRT
    from importlib.metadata import version
    if version('bleak') != '2.1.1':
        raise RuntimeError('This paired-cache backend requires Bleak 2.1.1')
    class PairedBackend(BleakClientWinRT):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            # Version-specific adapter: string addresses otherwise trigger a
            # scan even when Windows already paired this device.
            self._device_info = DEVICE_ADDRESS
    return PairedBackend


class BleakAtvvLink:
    EXTEND_INTERVAL = 5.0
    MAX_STREAM_SECONDS = 120.0
    def __init__(self, on_audio, on_control, on_disconnected, log,
                 *, send_get_caps=True, caps_v10=False, clone_hold_cycle=False):
        self._on_audio, self._on_control = on_audio, on_control
        self._on_disconnected, self._log = on_disconnected, log
        self._send_get_caps, self._caps_v10 = send_get_caps, caps_v10
        self._client = None
        self._caps_seen = None
        self._mic_open = False
        self._closed = False
        self._armed = False
        self._loop = None
        self._tasks = set()
        self._subscribed = []
        self._command_lock = None
        self._protocol_version = 0
        self._stream_id = None
        self._stream_serial = 0
        self._stream_started_at = 0.0
        self._next_extend_at = 0.0
        self._keepalive_task = None
        self._clone_hold_cycle=clone_hold_cycle
        self._hold_cycle=None

    async def connect(self, *, wait_seconds=25.0, stop_event=None):
        from .config import require_device
        require_device()
        from bleak import BleakClient
        self._loop = asyncio.get_running_loop()
        self._caps_seen = asyncio.Event()
        self._command_lock = asyncio.Lock()
        if self._clone_hold_cycle:
            from .hold_cycle import CloneHoldCycle
            def abort_hold(reason):
                self._clear_stream()
                self._on_control(b'\x00\x02')
            self._hold_cycle=CloneHoldCycle(self.write_tx,self._command_lock,self._log,abort_hold)
        address = ':'.join(f'{DEVICE_ADDRESS:012X}'[i:i+2] for i in range(0,12,2))
        self._client = BleakClient(
            address, backend=paired_backend(), timeout=wait_seconds,
            services=[proto.VOICE_SERVICE_UUID],
            winrt={'use_cached_services': True},
            disconnected_callback=self._on_ble_disconnect)
        self._log('connect_begin', {'source':'paired_cache'})
        await self._client.connect()
        if stop_event and stop_event.is_set():
            raise LinkNotConnectedError('stopped')
        if not self._client.is_connected:
            raise LinkNotConnectedError('link_closed_during_discovery')
        self._armed = True
        self._log('gatt_ready', {'service_count':len(list(self._client.services))})
        for role, uuid, handler in (
            ('control',proto.VOICE_CONTROL_UUID,self._handle_control),
            ('audio',proto.VOICE_AUDIO_UUID,self._handle_audio)):
            self._log('cccd_begin', {'role':role})
            await asyncio.wait_for(self._client.start_notify(uuid,handler),8)
            self._subscribed.append(uuid)
            self._log('cccd',{'role':role,'ok':True})
        if self._send_get_caps:
            command=proto.GET_CAPABILITIES_V10 if self._caps_v10 else proto.GET_CAPS_PROVEN
            if not await self.write_tx(command):
                raise LinkNotConnectedError('caps_write_failed')
            await asyncio.wait_for(self._caps_seen.wait(),4)
        self._log('connected',{'caps_confirmed':self._caps_seen.is_set()})
        self._keepalive_task = asyncio.create_task(self._keepalive_loop())
        return {'transport':'paired_winrt','is_connected':self._client.is_connected}

    async def write_tx(self,data):
        if not self._client or not self._client.is_connected or self._closed:
            return False
        try:
            await asyncio.wait_for(self._client.write_gatt_char(
                proto.VOICE_TX_UUID,data,response=False),4)
            return True
        except Exception as exc:
            self._log('tx_error',{'error_type':type(exc).__name__,'opcode':data[0]})
            return False

    async def open_mic(self):
        async with self._command_lock:
            if self._mic_open:
                return True
            if not await self.write_tx(proto.MIC_OPEN_PROVEN):
                return False
            self._mic_open=True
            self._log('mic_open_sent',{})
            return True

    async def close_mic(self):
        async with self._command_lock:
            if not self._mic_open:
                return True
            command=(bytes((0x0d,self._stream_id)) if self._stream_id is not None
                     else proto.MIC_CLOSE_PROVEN)
            serial=self._stream_serial
            ok=await self.write_tx(command)
            if ok:
                if serial==self._stream_serial:
                    self._clear_stream()
            return ok

    def _clear_stream(self):
        self._stream_serial += 1
        self._stream_id = None
        self._mic_open = False

    async def _keepalive_once(self, now):
        if self._hold_cycle:
            await self._hold_cycle.tick(now)
            return
        stream_id,serial=self._stream_id,self._stream_serial
        if self._closed or stream_id is None or now<self._next_extend_at:
            return
        async with self._command_lock:
            # STOP/new START can arrive while a preceding GATT write holds lock.
            if self._closed or serial!=self._stream_serial or self._stream_id!=stream_id:
                return
            self._next_extend_at=now+self.EXTEND_INTERVAL
            elapsed=now-self._stream_started_at
            if elapsed>=self.MAX_STREAM_SECONDS:
                ok=await self.write_tx(bytes((0x0d,stream_id)))
                if serial==self._stream_serial:self._clear_stream()
                self._log('mic_session_limit',{'seconds':round(elapsed,2),'sent':ok})
                return
            ok=await self.write_tx(bytes((0x0e,stream_id)))
            self._log('mic_extend',{'stream_id':stream_id,'seconds':round(elapsed,2),'sent':ok})

    async def _keepalive_loop(self):
        while not self._closed:
            await asyncio.sleep(.2)
            try:
                await self._keepalive_once(time.monotonic())
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._next_extend_at=time.monotonic()+self.EXTEND_INTERVAL
                self._log('mic_extend_error',{'error_type':type(exc).__name__})

    def _handle_audio(self,_,data):
        if not self._closed:
            self._on_audio(bytes(data))

    def _handle_control(self,_,data):
        data=bytes(data)
        if self._closed or not data:
            return
        if data[0]==proto.OPCODE_CAPS:
            try:self._protocol_version=proto.parse_caps(data).version
            except proto.ProtocolError:pass
            self._loop.call_soon_threadsafe(self._caps_seen.set)
        elif data[0]==proto.OPCODE_AUDIO_START:
            self._clear_stream()
            self._mic_open=True
            if self._protocol_version>=0x0100 and len(data)>=4:
                self._stream_id=data[3]
                self._stream_started_at=time.monotonic()
                self._next_extend_at=self._stream_started_at+self.EXTEND_INTERVAL
        elif data[0]==proto.OPCODE_MIC_OPEN_REQUEST:
            self._loop.call_soon_threadsafe(self._schedule_open)
        elif data[0]==proto.OPCODE_AUDIO_STOP:
            self._clear_stream()
        if self._hold_cycle is None or self._hold_cycle.control(data):
            self._on_control(data)

    def _schedule_open(self):
        if self._closed:
            return
        task=self._loop.create_task(self.open_mic())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _on_ble_disconnect(self,*_):
        if self._armed and not self._closed:
            self._armed=False
            self._clear_stream()
            if self._hold_cycle:self._hold_cycle.reset()
            self._log('ble_disconnected',{})
            self._on_disconnected()

    async def close(self):
        if self._closed:
            return
        if self._keepalive_task:
            self._keepalive_task.cancel()
            await asyncio.gather(self._keepalive_task,return_exceptions=True)
            self._keepalive_task=None
        if self._hold_cycle:await self._hold_cycle.shutdown()
        if self._client and self._client.is_connected and self._mic_open:
            await self.close_mic()
        self._closed=True
        self._clear_stream()
        self._armed=False
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks,return_exceptions=True)
        if self._client:
            for uuid in reversed(self._subscribed):
                if not self._client.is_connected:
                    break
                try:
                    await asyncio.wait_for(self._client.stop_notify(uuid),2)
                except Exception as exc:
                    self._log('unsubscribe_error',{'error_type':type(exc).__name__})
            try:
                await asyncio.wait_for(self._client.disconnect(),5)
            except Exception as exc:
                self._log('disconnect_error',{'error_type':type(exc).__name__})
        self._client=None
        self._mic_open=False
        self._log('link_closed',{})
