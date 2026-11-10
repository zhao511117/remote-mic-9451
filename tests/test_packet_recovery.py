# SPDX-License-Identifier: GPL-3.0-only
import unittest
from rm9451 import protocol as p

def frame(sequence=0):
    return sequence.to_bytes(2,'big')+bytes.fromhex('00040020')+b'\x61'*128

def notifications(encoded):
    return [encoded[i:i+20] for i in range(0,len(encoded),20)]

class PacketRecoveryTests(unittest.TestCase):
    def decoder(self):return p.VoiceDecoder(gain_db=0,packet_aligned=True)
    def feed_frame(self,decoder,encoded):
        output=[]
        for packet in notifications(encoded):output.extend(decoder.feed(packet))
        return output

    def test_video_regression_old_80byte_tail_does_not_poison_new_segment(self):
        d=self.decoder()
        self.feed_frame(d,frame(10))
        for packet in notifications(frame(11))[:4]:d.feed(packet)
        self.feed_frame(d,frame(0))
        pcm=self.feed_frame(d,frame(1))
        self.assertEqual(d.frames.frames_seen,2)
        self.assertEqual(len(pcm),1536)
        self.assertEqual(d.packets.discarded_frames,1)
        self.assertEqual(p.frame_stats(d)['pending_bytes'],0)

    def test_every_partial_tail_recovers_at_next_packet_end(self):
        for packets_before_cut in range(1,7):
            with self.subTest(partial=packets_before_cut*20):
                d=self.decoder()
                for packet in notifications(frame(10))[:packets_before_cut]:d.feed(packet)
                self.feed_frame(d,frame(0))
                self.feed_frame(d,frame(1))
                self.assertEqual(d.frames.frames_seen,1)
                self.assertEqual(d.packets.discarded_frames,1)
                self.assertEqual(p.frame_stats(d)['pending_bytes'],0)

    def test_lost_middle_packet_drops_one_frame_and_recovers(self):
        d=self.decoder();packets=notifications(frame())
        for packet in packets[:2]+packets[3:]:d.feed(packet)
        self.feed_frame(d,frame(1))
        self.assertEqual(d.frames.frames_seen,1)
        self.assertEqual(d.packets.discarded_frames,1)

    def test_lost_final_packet_cannot_grow_buffer_or_poison_following_frames(self):
        d=self.decoder()
        for packet in notifications(frame())[:-1]:d.feed(packet)
        self.feed_frame(d,frame(1));self.feed_frame(d,frame(2))
        self.assertEqual(d.frames.frames_seen,1)
        self.assertLessEqual(d.packets.pending_bytes,120)

    def test_undamaged_packet_pcm_matches_continuous_reference_exactly(self):
        encoded=frame(0)+frame(1)+frame(2)
        reference=p.VoiceDecoder(gain_db=0).feed(encoded)
        d=self.decoder();actual=[]
        for i in range(3):actual.extend(self.feed_frame(d,frame(i)))
        self.assertEqual(actual,reference)
        self.assertEqual(d.packets.discarded_frames,0)

    def test_complete_frame_notification_and_reset(self):
        d=self.decoder();d.feed(b'\x01'*20);d.on_start()
        self.assertEqual(len(d.feed(frame())),1536)
        self.assertEqual(d.packets.discarded_frames,0)

    def test_invalid_notification_recovers_and_memory_is_bounded(self):
        d=self.decoder()
        for _ in range(10000):d.feed(b'\x77'*20)
        self.assertLessEqual(d.packets.pending_bytes,120)
        d.feed(b'\x00'*14)
        self.assertEqual(len(self.feed_frame(d,frame())),1536)

    def test_repeated_realistic_cuts_do_not_change_logical_session_edges(self):
        from rm9451.runner import Bridge
        b=Bridge(lambda *_:None,hotkey_enabled=False,audio_enabled=False)
        b._handle_control(bytes.fromhex('04030123'))
        for _ in range(4):
            self.feed_frame(b.decoder,frame(2))
            for packet in notifications(frame(3))[:4]:b._handle_audio(packet)
            b._handle_control(bytes.fromhex('04010100'))
            self.feed_frame(b.decoder,frame(0))
            self.feed_frame(b.decoder,frame(1))
        self.assertEqual(b._sessions,1)
        self.assertEqual(b.decoder.frames.frames_seen,8)
        self.assertEqual(b.decoder.packets.discarded_frames,4)
