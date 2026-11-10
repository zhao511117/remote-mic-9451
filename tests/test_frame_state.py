# SPDX-License-Identifier: GPL-3.0-only
import unittest
from rm9451 import protocol as p

class FrameStateTests(unittest.TestCase):
    def test_real_header_is_big_endian_signed_predictor_and_step_index(self):
        dec=p.VoiceDecoder(gain_db=0)
        frame=bytes.fromhex('000100e4fe1f')+b'\x00'*128
        dec.feed(frame)
        reference=p.IMAADPCMDecoder()
        reference.reset(-6914,31)
        reference.decode(b'\x00'*128)
        self.assertEqual(dec.decoder.predictor,reference.predictor)
        self.assertEqual(dec.decoder.step_index,reference.step_index)

    def test_each_frame_resets_codec_state_after_loss_or_previous_audio(self):
        frame=bytes.fromhex('00020024fb36')+b'\x71'*128
        a=p.VoiceDecoder(gain_db=0)
        a.feed(b'\x00'*6+b'\x77'*128)
        a.feed(frame)
        b=p.VoiceDecoder(gain_db=0);b.feed(frame)
        self.assertEqual((a.decoder.predictor,a.decoder.step_index),
                         (b.decoder.predictor,b.decoder.step_index))

    def test_invalid_step_index_is_rejected(self):
        with self.assertRaises(p.ProtocolError):
            p.VoiceDecoder().feed(b'\x00'*5+b'\x59'+b'\x00'*128)

    def test_header_observation_is_bounded(self):
        f=p.FrameAssembler()
        f.feed((b'\x00'*134)*100)
        self.assertEqual(len(f.headers_seen),4)
