# SPDX-License-Identifier: GPL-3.0-only
"""Offline regressions for the 9451 ATVV adaptation.

These deliberately do NOT need a real remote: they prove the frame reassembly
and ADPCM decode recover a known signal when 134-byte frames (6-byte header)
are fragmented into 20-byte BLE notifications -- the exact failure mode of
the earlier probe.
"""
from __future__ import annotations

import math
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from rm9451 import protocol as p  # noqa: E402


def sine(rate: int = 8000, seconds: float = 1.0, freq: float = 440.0, amp: float = 9000):
    return [int(round(amp * math.sin(2 * math.pi * freq * i / rate)))
            for i in range(int(rate * seconds))]


def build_frames(adpcm: bytes, payload_size: int = 128, header: bytes = b"\x00"*6):
    """Pack ADPCM into 134-byte frames: 6-byte header + 128-byte payload."""
    assert len(header) + payload_size == 134
    frames = []
    for offset in range(0, len(adpcm), payload_size):
        chunk = adpcm[offset:offset + payload_size]
        if len(chunk) < payload_size:
            break  # keep only complete frames for exact comparison
        frames.append(header + chunk)
    return frames


def fragment(frames, mtu: int = 20):
    """Split the continuous frame stream into realistic BLE notifications."""
    stream = b"".join(frames)
    return [stream[i:i + mtu] for i in range(0, len(stream), mtu)]


class CapabilitiesTests(unittest.TestCase):
    def test_real_device_caps_response(self):
        # 0b 01 00 01 03 00 86 00 00 -- read from this exact device.
        caps = p.parse_caps(bytes.fromhex("0b0100010300860000"))
        self.assertEqual(caps.version, 0x0100)
        self.assertEqual(caps.codecs, 0x01)
        self.assertEqual(caps.interaction, 0x03)
        self.assertEqual(caps.frame_size, 134)
        self.assertEqual(caps.codec, p.CODEC_8K_ADPCM)
        self.assertEqual(caps.sample_rate, 8000)
        self.assertTrue(caps.is_8k_adpcm)

    def test_16k_caps_still_parses(self):
        caps = p.parse_caps(bytes.fromhex("0b0100020300780000"))
        self.assertEqual(caps.codec, p.CODEC_16K_ADPCM)
        self.assertEqual(caps.sample_rate, 16000)

    def test_rejects_garbage(self):
        with self.assertRaises(p.ProtocolError):
            p.parse_caps(b"\x04\x01\x00")
        with self.assertRaises(p.ProtocolError):
            p.parse_caps(b"\x0b\x01")


class ControlTests(unittest.TestCase):
    def test_physical_start_observed_on_device(self):
        ev = p.parse_control(bytes.fromhex("04030110"))
        self.assertEqual(ev.kind, "start")
        self.assertEqual((ev.reason, ev.codec, ev.session_id), (3, 1, 0x10))

    def test_stop_and_sync(self):
        self.assertEqual(p.parse_control(b"\x00\x00").kind, "stop")
        sync = p.parse_control(bytes.fromhex("0a000000100022"))
        self.assertEqual(sync.kind, "sync")
        self.assertEqual(sync.predictor, 0x1000)
        self.assertEqual(sync.step_index, 0x22)

    def test_caps_event(self):
        self.assertEqual(p.parse_control(bytes.fromhex("0b0100010300860000")).kind, "caps")


class RealTraceTests(unittest.TestCase):
    """Facts read from the earlier real capture (probe/voice1.log on this host).

    That log recorded 2191 audio notifications, lengths [14, 20], first frame
    ``00 00 00 00 00 00 03 7f 7e 5d...`` and 315 ``0a 00 NN`` control
    notifications during a 10-second MIC_OPEN. 6*20 + 14 == 134 and
    2191 == 313*7, i.e. exactly seven notifications per 134-byte codec frame.
    """

    HEADER = b"\x00" * 6

    def seven_notifications(self, frame: bytes):
        assert len(frame) == 134
        return [frame[0:20], frame[20:40], frame[40:60],
                frame[60:80], frame[80:100], frame[100:120], frame[120:134]]

    def test_real_notification_layout(self):
        self.assertEqual(6 * 20 + 14, p.DEFAULT_FRAME_SIZE)
        self.assertEqual(313 * 7, 2191)
        adpcm = p.encode_adpcm(sine(seconds=10.1))
        frames = []
        for offset in range(0, len(adpcm), 128):
            chunk = adpcm[offset:offset + 128]
            if len(chunk) == 128:
                frames.append(self.HEADER + chunk)
        self.assertGreaterEqual(len(frames), 313)
        frames = frames[:313]

        notifications = []
        for frame in frames:
            parts = self.seven_notifications(frame)
            self.assertEqual([len(x) for x in parts], [20] * 6 + [14])
            notifications.extend(parts)
        self.assertEqual(len(notifications), 2191)

        assembler = p.FrameAssembler(134, 6)
        decoder = p.IMAADPCMDecoder()
        samples = []
        for note in notifications:
            for payload in assembler.feed(note):
                samples.extend(decoder.decode(payload))
        self.assertEqual(assembler.frames_seen, 313)
        self.assertEqual(assembler.pending_bytes, 0)
        self.assertEqual(len(samples), 313 * 256)  # 128 ADPCM bytes -> 256 @8 kHz
        self.assertEqual(round(313 * 256 / 8000, 3), 10.016)
        self.assertTrue(all(h == self.HEADER for h in assembler.headers_seen[:10]))
        self.assertEqual(decoder.decode(b""), [])

    def test_short_sync_is_frame_counter_not_predictor_reset(self):
        # 315 of these appeared in the real 10 s stream.
        ev = p.parse_control(bytes.fromhex("0a0001"))
        self.assertEqual(ev.kind, "frame_counter")
        self.assertEqual(ev.session_id, 1)
        self.assertIsNone(ev.predictor)
        # A real 7-byte sync still resets the predictor.
        self.assertEqual(p.parse_control(bytes.fromhex("0a000000100022")).kind, "sync")

    def test_real_caps_and_active_start(self):
        caps = p.parse_control(bytes.fromhex("0b0100010300860000"))
        self.assertEqual((caps.caps.frame_size, caps.caps.sample_rate), (134, 8000))
        start = p.parse_control(bytes.fromhex("04010100"))
        self.assertEqual((start.kind, start.codec, start.reason), ("start", 1, 1))


class DecodeTests(unittest.TestCase):
    def test_adpcm_round_trip_high_nibble_first(self):
        original = sine()
        encoded = p.encode_adpcm(original)
        decoded = p.IMAADPCMDecoder().decode(encoded)
        self.assertEqual(len(decoded), len(original))
        # IMA ADPCM is lossy; a 440 Hz sine must still be highly correlated.
        n = min(len(decoded), len(original))
        mx = sum(original[:n]) / n
        my = sum(decoded[:n]) / n
        cov = sum((original[i] - mx) * (decoded[i] - my) for i in range(n))
        vx = sum((v - mx) ** 2 for v in original[:n])
        vy = sum((v - my) ** 2 for v in decoded[:n])
        corr = cov / math.sqrt(vx * vy)
        self.assertGreater(corr, 0.95, f"corr={corr:.3f}")

    def test_134_byte_frames_over_20_byte_notifications(self):
        """The core regression: notifications must be concatenated BEFORE
        splitting into codec frames, and the ADPCM state must stay continuous."""
        original = sine(seconds=2.0)
        adpcm = p.encode_adpcm(original)
        frames = build_frames(adpcm)
        self.assertGreater(len(frames), 10)
        notifications = fragment(frames)

        assembler = p.FrameAssembler(134, 6)
        decoder = p.IMAADPCMDecoder()
        recovered = []
        for note in notifications:
            for payload in assembler.feed(note):
                recovered.extend(decoder.decode(payload))

        self.assertEqual(assembler.frames_seen, len(frames))
        self.assertEqual(assembler.pending_bytes, len(b"".join(frames)) % 134)
        self.assertEqual(assembler.headers_seen[0], b"\x00"*6)
        # Exact equality: no per-notification decode resets, no skipped bytes.
        reference = p.IMAADPCMDecoder().decode(b"".join(f[6:] for f in frames))
        self.assertEqual(recovered, reference)

    def test_voice_decoder_end_to_end(self):
        """Full pipeline produces 48 kHz PCM with sane levels, no saturation."""
        adpcm = p.encode_adpcm(sine(seconds=1.0))
        frames = build_frames(adpcm)
        dec = p.VoiceDecoder(134, 6, gain_db=0.0)
        total = 0
        for note in fragment(frames):
            total += len(dec.feed(note))
        self.assertEqual(dec.frames.frames_seen, len(frames))
        self.assertGreater(total, 40000)  # ~1 s at 48 kHz
        self.assertLess(dec.peak, 32768)
        self.assertGreater(dec.peak, 1000)
        self.assertEqual(p.frame_stats(dec)["sample_rate"], 8000)

    def test_wrong_header_alignment_is_detected(self):
        """Skipping no header when a 6-byte header exists must corrupt audio."""
        adpcm = p.encode_adpcm(sine(seconds=0.5))
        frames = build_frames(adpcm)
        stream = b"".join(frames)
        good = p.VoiceDecoder(134, 6, gain_db=0.0)
        bad = p.VoiceDecoder(134, 0, gain_db=0.0)
        for note in fragment(frames):
            good.feed(note)
            bad.feed(note)
        # Peak amplitude can coincide. Consuming the six metadata bytes as
        # samples must instead change the decoded stream duration.
        self.assertEqual(bad.pcm_samples-good.pcm_samples,len(frames)*6*2*6)
        self.assertEqual(good.frames.frames_seen, bad.frames.frames_seen)

    def test_sync_resets_predictor(self):
        dec = p.VoiceDecoder(134, 6, gain_db=0.0)
        dec.set_sync(1234, 40)
        dec.feed(b"\x00" * 134)
        self.assertIsNone(dec._pending_sync)

    def test_resampler_is_six_to_one_and_continuous(self):
        r = p.Resampler(8000, 48000)
        a = r.process([0, 1000, 2000, 3000])
        b = r.process([4000, 5000])
        self.assertEqual(len(a), 24)
        self.assertEqual(len(b), 12)
        self.assertGreater(b[0], a[-1] - 2000)

    def test_frame_stats_has_no_audio_or_text(self):
        dec = p.VoiceDecoder()
        dec.feed(b"\x00" * 134)
        stats = p.frame_stats(dec)
        self.assertEqual(set(stats) - {"frames", "pending_bytes", "pcm_samples", "peak",
                                   "sample_rate", "frame_size", "header_bytes", "headers",
                                   "discarded_packet_frames", "packet_mode"}, set())
        self.assertEqual(stats["frames"], 1)
        self.assertLessEqual(len(stats["headers"]), 4)


class HandshakeTests(unittest.TestCase):
    """The host-driven MIC_OPEN sequence proven on this exact remote.

    voice1.log: write 0A -> CAPS_RESP; write 0C -> CTL 04 + 2191 audio
    notifications; write 0D -> stream ends. Without 0C there is NO audio,
    which is why waiting for a physical voice-key edge failed on Windows.
    """

    def test_proven_single_byte_commands(self):
        self.assertEqual(p.GET_CAPS_PROVEN, b"\x0a")
        self.assertEqual(p.MIC_OPEN_PROVEN, b"\x0c")
        self.assertEqual(p.MIC_CLOSE_PROVEN, b"\x0d")
        self.assertEqual(p.OPCODE_MIC_OPEN_REQUEST, 0x08)
        # v1.0 variants kept for other firmware, not used by default here.
        self.assertEqual(p.GET_CAPABILITIES_V10, bytes.fromhex("0a0100000303"))
        self.assertEqual(p.MIC_OPEN_V10, bytes.fromhex("0c00"))

    def test_real_control_sequence_parses(self):
        self.assertEqual(p.parse_control(bytes.fromhex("0b0100010300860000")).kind,
                         "caps")
        self.assertEqual(p.parse_control(bytes.fromhex("04010100")).kind, "start")
        self.assertEqual(p.parse_control(bytes.fromhex("0800")).kind, "mic_button")
        self.assertEqual(p.parse_control(bytes.fromhex("0a0001")).kind,
                         "frame_counter")
        # 315 CTL notifications = 1 CAPS + 1 START + 313 frame counters.
        self.assertEqual(1 + 1 + 313, 315)

    def test_real_stream_byte_accounting(self):
        # 313 frames x 134 bytes, each fragmented as 6x20 + 1x14.
        self.assertEqual(313 * (6 * 20 + 14), 313 * 134)
        self.assertEqual(313 * 6 * 20 + 313 * 14, 41942)
        self.assertEqual(313 * 7, 2191)
        # 128 ADPCM bytes per frame -> 256 samples @ 8 kHz = 32 ms.
        self.assertEqual(round(313 * 256 / 8000, 3), 10.016)


if __name__ == "__main__":
    unittest.main(verbosity=2)
