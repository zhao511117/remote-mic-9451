# SPDX-License-Identifier: GPL-3.0-only
import unittest,time
from rm9451.runner import Bridge

class SessionOrderTests(unittest.TestCase):
    def test_audio_callback_before_control_start_does_not_lose_frame_header(self):
        b=Bridge(lambda *_:None,hotkey_enabled=False,audio_enabled=False)
        b._caps_seen=True
        frame=b'\x00'*134
        b._handle_audio(frame[:20])
        b._handle_control(bytes.fromhex('04030101'))
        for off in range(20,134,20):b._handle_audio(frame[off:off+20])
        self.assertEqual(b.decoder.frames.frames_seen,1)
        self.assertEqual(b.decoder.frames.pending_bytes,0)
        self.assertEqual(b._audio_notifications,7)

    def test_audio_arriving_after_stop_is_flushed_before_key_release(self):
        b=Bridge(lambda *_:None,hotkey_enabled=False,audio_enabled=False)
        b._handle_control(bytes.fromhex('04030101'))
        for _ in range(6):b._handle_audio(b'\x00'*20)
        b._handle_control(bytes.fromhex('0002'))
        self.assertTrue(b._recording)
        b._handle_audio(b'\x00'*14)
        self.assertEqual(b.decoder.frames.frames_seen,1)
        b._finish('device_stop')
        self.assertFalse(b._recording)

    def test_next_utterance_can_start_during_previous_tail_flush(self):
        b=Bridge(lambda *_:None,hotkey_enabled=False,audio_enabled=False)
        b._handle_control(bytes.fromhex('04030101'))
        b._handle_control(bytes.fromhex('0002'))
        b._handle_control(bytes.fromhex('04030102'))
        self.assertEqual(b._sessions,2)
        self.assertTrue(b._recording)
