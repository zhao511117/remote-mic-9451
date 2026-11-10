# SPDX-License-Identifier: GPL-3.0-only
import asyncio
import unittest
import subprocess
import sys
from unittest.mock import AsyncMock
from unittest.mock import patch
from rm9451.cached_link import BleakAtvvLink, paired_backend
from rm9451.ble import DEVICE_ADDRESS
from rm9451.runner import Bridge


class TransportTests(unittest.IsolatedAsyncioTestCase):
    def active_link(self):
        link=BleakAtvvLink(lambda _:None,lambda _:None,lambda:None,lambda *args:None)
        link._loop=asyncio.get_running_loop()
        link._caps_seen=asyncio.Event()
        link._command_lock=asyncio.Lock()
        link.write_tx=AsyncMock(return_value=True)
        link._handle_control(None,bytes.fromhex('0b0100010300860000'))
        return link

    async def test_extend_uses_current_native_stream_and_is_periodic(self):
        link=self.active_link()
        with patch('rm9451.cached_link.time.monotonic',return_value=100):
            link._handle_control(None,bytes.fromhex('04030110'))
        await link._keepalive_once(104.9)
        link.write_tx.assert_not_awaited()
        await link._keepalive_once(105)
        link.write_tx.assert_awaited_once_with(bytes.fromhex('0e10'))
        await link._keepalive_once(105.1)
        self.assertEqual(link.write_tx.await_count,1)
        await link._keepalive_once(110)
        self.assertEqual(link.write_tx.await_count,2)

    async def test_no_extend_idle_or_after_stop_or_disconnect(self):
        for ending in ('idle','stop','disconnect'):
            link=self.active_link()
            if ending!='idle':
                link._handle_control(None,bytes.fromhex('04030110'))
                if ending=='stop':link._handle_control(None,bytes.fromhex('0002'))
                else:
                    link._armed=True
                    link._on_ble_disconnect()
            await link._keepalive_once(100000000000)
            link.write_tx.assert_not_awaited()

    async def test_stream_change_while_waiting_for_lock_cannot_extend_old_stream(self):
        link=self.active_link()
        with patch('rm9451.cached_link.time.monotonic',return_value=100):
            link._handle_control(None,bytes.fromhex('04030110'))
        await link._command_lock.acquire()
        task=asyncio.create_task(link._keepalive_once(105))
        await asyncio.sleep(0)
        with patch('rm9451.cached_link.time.monotonic',return_value=105):
            link._handle_control(None,bytes.fromhex('0002'))
            link._handle_control(None,bytes.fromhex('04030111'))
        link._command_lock.release()
        await task
        link.write_tx.assert_not_awaited()
        await link._keepalive_once(110)
        link.write_tx.assert_awaited_once_with(bytes.fromhex('0e11'))

    async def test_extend_failure_does_not_spin_or_reopen_mic(self):
        link=self.active_link()
        link.open_mic=AsyncMock()
        link.write_tx.return_value=False
        with patch('rm9451.cached_link.time.monotonic',return_value=100):
            link._handle_control(None,bytes.fromhex('04030110'))
        await link._keepalive_once(105)
        await link._keepalive_once(105.1)
        self.assertEqual(link.write_tx.await_count,1)
        link.open_mic.assert_not_awaited()

    async def test_session_limit_closes_matching_stream_and_stops_extending(self):
        link=self.active_link()
        with patch('rm9451.cached_link.time.monotonic',return_value=100):
            link._handle_control(None,bytes.fromhex('04030110'))
        await link._keepalive_once(220)
        link.write_tx.assert_awaited_once_with(bytes.fromhex('0d10'))
        await link._keepalive_once(225)
        self.assertEqual(link.write_tx.await_count,1)

    async def test_short_legacy_start_is_not_extended_with_guessed_stream(self):
        link=self.active_link()
        link._handle_control(None,bytes.fromhex('04'))
        await link._keepalive_once(100000000000)
        link.write_tx.assert_not_awaited()

    async def test_no_microphone_write_without_physical_request(self):
        link=BleakAtvvLink(lambda _:None,lambda _:None,lambda:None,lambda *args:None)
        link._loop=asyncio.get_running_loop()
        link._caps_seen=asyncio.Event()
        link.open_mic=AsyncMock(return_value=True)
        link._handle_control(None,b'\x0b\x01\x00\x01\x03\x00\x86\x00\x00')
        await asyncio.sleep(0)
        link.open_mic.assert_not_awaited()
        link._handle_control(None,b'\x08\x00')
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        link.open_mic.assert_awaited_once()

    async def test_disconnect_notifies_owner_and_resets_microphone(self):
        events=[]
        link=BleakAtvvLink(None,None,lambda:events.append('lost'),lambda *args:None)
        link._armed=True
        link._mic_open=True
        link._on_ble_disconnect()
        link._on_ble_disconnect()
        self.assertEqual(events,['lost'])
        self.assertFalse(link._mic_open)

    async def test_failed_mic_close_remains_pending_for_cleanup(self):
        link=BleakAtvvLink(None,None,None,lambda *args:None)
        link._command_lock=asyncio.Lock()
        link._mic_open=True
        link.write_tx=AsyncMock(return_value=False)
        self.assertFalse(await link.close_mic())
        self.assertTrue(link._mic_open)

class DefaultTests(unittest.TestCase):
    def test_audio_initialization_keeps_ble_thread_mta(self):
        code=('import asyncio; from rm9451.__main__ import initialize_mta; '
              'initialize_mta(); import sounddevice; '
              'from bleak.backends.winrt.util import assert_mta; '
              'asyncio.run(assert_mta()); print("MTA_OK")')
        result=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('MTA_OK',result.stdout)
    def test_hold_to_talk_is_default(self):
        bridge=Bridge(lambda *args:None,hotkey_enabled=False,audio_enabled=False)
        self.assertFalse(bridge.auto_mic)
        self.assertTrue(bridge.caps_v10)
        self.assertFalse(bridge.focus_text)

    def test_cache_backend_bypasses_implicit_scan_for_exact_device(self):
        backend=paired_backend()('12:34:56:78:9A:BC',winrt={},timeout=2)
        self.assertEqual(backend._device_info,DEVICE_ADDRESS)
