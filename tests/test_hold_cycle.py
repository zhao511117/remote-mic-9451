# SPDX-License-Identifier: GPL-3.0-only
import asyncio,unittest
from unittest.mock import AsyncMock,patch
from rm9451.hold_cycle import CloneHoldCycle

class HoldCycleTests(unittest.IsolatedAsyncioTestCase):
    def make_cycle(self):
        self.commands=[];self.stops=[]
        self.write=AsyncMock(return_value=True)
        self.cycle=CloneHoldCycle(self.write,asyncio.Lock(),lambda *_:None,self.stops.append)
        return self.cycle

    async def asyncTearDown(self):
        if hasattr(self,'cycle'):await self.cycle.shutdown()

    async def test_one_hold_keeps_internal_stop_and_restart_in_same_logical_session(self):
        c=self.make_cycle();self.assertTrue(c.control(bytes.fromhex('04030123'),100))
        async def write(data):
            if data==b'\x0d':self.assertFalse(c.control(b'\x00\x00',110))
            if data==b'\x0c':self.assertTrue(c.control(bytes.fromhex('04010100'),110.05))
            return True
        self.write.side_effect=write
        await c.tick(110)
        self.assertEqual([a.args[0] for a in self.write.await_args_list],[b'\x0d',b'\x0c'])
        self.assertTrue(c.active)
        self.assertEqual(self.stops,[])
        await c.tick(110.1)
        self.assertEqual(self.write.await_count,2)

    async def test_no_commands_idle_or_after_physical_release(self):
        c=self.make_cycle();await c.tick(100)
        c.control(bytes.fromhex('04030123'),100)
        self.assertTrue(c.control(b'\x00\x02',103))
        await c.tick(120)
        self.write.assert_not_awaited()

    async def test_release_during_close_prevents_reopen(self):
        c=self.make_cycle();c.control(bytes.fromhex('04030123'),100)
        async def write(data):
            c.control(b'\x00\x02',110)
            return True
        self.write.side_effect=write
        await c.tick(110)
        await asyncio.sleep(0)
        self.assertNotIn(b'\x0c',[a.args[0] for a in self.write.await_args_list])
        self.assertFalse(c.active)

    async def test_late_start_after_release_is_not_forwarded_and_is_closed(self):
        c=self.make_cycle();c.control(bytes.fromhex('04030123'),100)
        await c.tick(110)
        c.control(b'\x00\x02',110.01)
        self.assertFalse(c.control(bytes.fromhex('04010100'),110.1))
        await asyncio.sleep(0);await asyncio.sleep(0)
        self.assertFalse(c.active)
        self.assertEqual(self.write.await_args_list[-1].args[0],b'\x0d')

    async def test_new_hold_while_waiting_for_lock_is_not_closed_by_old_tick(self):
        c=self.make_cycle();c.control(bytes.fromhex('04030123'),100)
        await c.lock.acquire()
        task=asyncio.create_task(c.tick(110));await asyncio.sleep(0)
        c.control(b'\x00\x02',110)
        c.control(bytes.fromhex('04030124'),110.01)
        c.lock.release();await task
        self.write.assert_not_awaited()

    async def test_120_second_bound_closes_without_reopen(self):
        c=self.make_cycle();c.control(bytes.fromhex('04030123'),100)
        await c.tick(220)
        self.write.assert_awaited_once_with(b'\x0d')
        self.assertFalse(c.active);self.assertEqual(self.stops,['session_limit'])

    async def test_failed_close_cannot_start_new_microphone(self):
        c=self.make_cycle();c.control(bytes.fromhex('04030123'),100)
        self.write.return_value=False
        await c.tick(110)
        self.assertNotIn(b'\x0c',[a.args[0] for a in self.write.await_args_list])
        self.assertFalse(c.active);self.assertEqual(self.stops,['cycle_close_failed'])

    async def test_missing_restart_acknowledgment_fails_closed(self):
        c=self.make_cycle();c.control(bytes.fromhex('04030123'),100)
        await c.tick(110)
        await c.tick(113)
        self.assertFalse(c.active)
        self.assertEqual(self.stops,['cycle_restart_timeout'])
        self.assertEqual(self.write.await_args_list[-1].args[0],b'\x0d')

    async def test_unexpected_stop_does_not_restart(self):
        c=self.make_cycle();c.control(bytes.fromhex('04030123'),100)
        c.control(b'\x00\x01',105)
        await c.tick(110)
        self.write.assert_not_awaited();self.assertFalse(c.active)

    async def test_shutdown_cancels_owned_pending_tasks(self):
        c=self.make_cycle()
        self.assertFalse(c.control(bytes.fromhex('04010100'),100))
        await c.shutdown()
        self.assertFalse(c.active)
        self.assertEqual(len(c.tasks),0)

    async def test_transport_segment_restart_keeps_one_alt_hold_and_valid_pcm_frames(self):
        from rm9451.cached_link import BleakAtvvLink
        from rm9451.runner import Bridge
        bridge=Bridge(lambda *_:None,hotkey_enabled=False,audio_enabled=False)
        edges=[]
        class Hotkey:
            def press(self):edges.append('down')
            def release(self):edges.append('up')
        bridge.hotkey=Hotkey()
        link=BleakAtvvLink(bridge._handle_audio,bridge._handle_control,lambda:None,lambda *_:None)
        link._loop=asyncio.get_running_loop();link._command_lock=asyncio.Lock()
        link._protocol_version=0x100
        self.cycle=CloneHoldCycle(None,link._command_lock,lambda *_:None,lambda _:None)
        link._hold_cycle=self.cycle
        async def write(data):
            if data==b'\x0d':link._handle_control(None,b'\x00\x01')
            if data==b'\x0c':
                link._handle_control(None,bytes.fromhex('04010100'))
                bridge._handle_audio(b'\x00'*134)
            return True
        self.cycle.write=write
        with patch('rm9451.hold_cycle.time.monotonic',return_value=100):
            link._handle_control(None,bytes.fromhex('04030123'))
        bridge._handle_audio(b'\x00'*134)
        with patch('rm9451.hold_cycle.time.monotonic',return_value=110):
            await link._keepalive_once(110)
        bridge._handle_audio(b'\x00'*134)
        link._handle_control(None,b'\x00\x02')
        bridge._finish('device_stop')
        self.assertEqual(edges,['down','up'])
        self.assertEqual(bridge._sessions,1)
        self.assertEqual(bridge.decoder.frames.frames_seen,3)
        self.assertEqual(bridge.decoder.frames.pending_bytes,0)
