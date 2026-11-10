# SPDX-License-Identifier: GPL-3.0-only
"""ATVV protocol core for the 18D1:9451:0105 "Google TV Remote" (ZTKA-IR57).

Pure Python, no Windows APIs, so the whole chain can be regression-tested
offline with synthetic ADPCM before any real device is touched.

Interoperability facts used here (not creative expression):

* GATT UUIDs / control opcodes: Google "Voice over BLE" spec v1.0, and the
  upstream project's ``atvv_protocol.py`` (GPL-3.0-only, HEAD 07fae80).
* IMA/DVI ADPCM step + index tables: standard, also present in that module.
* 134-byte frames = 6-byte header + 128 bytes of ADPCM (8 kHz, 32 ms/frame),
  fragmented over the air into 20-byte notifications: Remotesolution RC89
  Bluetooth Remote Controller manual, "Audio frame" section. This matches the
  CAPS_RESP ``bytes per frame`` value 0x0086 observed on this exact device
  (0b 01 00 01 03 00 86 00 00) and the upstream TESTING.md note
  CHECK-GOOGLE-TV-9451-001 ("codec 1, 8 kHz, 134 byte frames").

The old probe decoded every 20-byte notification as a standalone frame and
skipped its first byte; that desynchronises the ADPCM predictor and produced
the clipped, unusable voice_capture.wav. Here notifications are concatenated
into one continuous byte stream and only then split into codec frames.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import List, Optional, Sequence, Tuple

VOICE_SERVICE_UUID = "ab5e0001-5a21-4f05-bc7d-af01f617b664"
VOICE_TX_UUID = "ab5e0002-5a21-4f05-bc7d-af01f617b664"
VOICE_AUDIO_UUID = "ab5e0003-5a21-4f05-bc7d-af01f617b664"
VOICE_CONTROL_UUID = "ab5e0004-5a21-4f05-bc7d-af01f617b664"

OPCODE_AUDIO_STOP = 0x00
OPCODE_AUDIO_START = 0x04
OPCODE_MIC_BUTTON = 0x08
OPCODE_MIC_OPEN_REQUEST = 0x08  # spec name for the same opcode
OPCODE_AUDIO_SYNC = 0x0A
OPCODE_CAPS = 0x0B

# v1.0 GET_CAPS. The 0x0003 compatibility field matters: MAINTENANCE.md
# records that writing 0x0001 here caused this model's link to fail.
GET_CAPABILITIES_V10 = bytes((0x0A, 0x01, 0x00, 0x00, 0x03, 0x03))
MIC_OPEN_V10 = bytes((0x0C, 0x00))
MIC_CLOSE_V10 = bytes((0x0D, 0x00))

# Commands PROVEN on this exact unit, from vibe/probe/voice_test.py + voice1.log
# (2026-11-08, 2191 real audio notifications). The remote answers a
# single-byte 0x0A with CAPS_RESP. 0x0C/0x0D support host-requested recording.
# A live 2026-11-08 capture also confirmed native physical hold-to-talk START
# (reason 3) and release STOP (reason 2); default operation uses those edges.
GET_CAPS_PROVEN = bytes((0x0A,))
MIC_OPEN_PROVEN = bytes((0x0C,))
MIC_CLOSE_PROVEN = bytes((0x0D,))

CODEC_8K_ADPCM = 0x01
CODEC_16K_ADPCM = 0x02
SAMPLE_RATE_8K = 8000
SAMPLE_RATE_16K = 16000
DEFAULT_FRAME_SIZE = 134
DEFAULT_FRAME_HEADER = 6
OUTPUT_SAMPLE_RATE = 48000

_STEP_TABLE = (
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31,
    34, 37, 41, 45, 50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130,
    143, 157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408, 449,
    494, 544, 598, 658, 724, 796, 876, 963, 1060, 1166, 1282, 1411,
    1552, 1707, 1878, 2066, 2272, 2499, 2749, 3024, 3327, 3660, 4026,
    4428, 4871, 5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442,
    11487, 12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623,
    27086, 29794, 32767,
)
_INDEX_TABLE = (-1, -1, -1, -1, 2, 4, 6, 8)


class ProtocolError(ValueError):
    """Malformed or unsupported ATVV control payload."""


@dataclass(frozen=True)
class Capabilities:
    version: int
    codecs: int
    interaction: int
    frame_size: int
    codec: int
    sample_rate: int
    raw: bytes

    @property
    def is_8k_adpcm(self) -> bool:
        return self.codec == CODEC_8K_ADPCM


def parse_caps(data: bytes) -> Capabilities:
    """Parse CAPS_RESP (0x0B ...).

    Real response from this device: 0b 01 00 01 03 00 86 00 00
      version=0x0100, codecs=0x01 (8 kHz ADPCM), interaction=0x03,
      bytes/frame=0x0086=134.
    """
    if not data or data[0] != OPCODE_CAPS or len(data) < 7:
        raise ProtocolError("caps: short or wrong opcode")
    version = (data[1] << 8) | data[2]
    if version >= 0x0100:
        codecs = data[3]
        interaction = data[4]
        # Some v1.0 remotes pack codecs into byte 3 of the legacy layout.
        if codecs == 0 and len(data) >= 9 and (data[4] & 0x03):
            codecs = data[4]
            interaction = 0x03
    else:
        if len(data) < 9:
            raise ProtocolError("caps: short legacy payload")
        codecs = data[4]
        interaction = 0
    frame_size = (data[5] << 8) | data[6]
    codec = CODEC_16K_ADPCM if (codecs & CODEC_16K_ADPCM) else CODEC_8K_ADPCM
    rate = SAMPLE_RATE_16K if codec == CODEC_16K_ADPCM else SAMPLE_RATE_8K
    return Capabilities(
        version=version,
        codecs=codecs,
        interaction=interaction,
        frame_size=frame_size or DEFAULT_FRAME_SIZE,
        codec=codec,
        sample_rate=rate,
        raw=bytes(data),
    )


@dataclass(frozen=True)
class ControlEvent:
    kind: str  # caps | start | stop | sync | mic_button | unknown
    caps: Optional[Capabilities] = None
    session_id: Optional[int] = None
    reason: Optional[int] = None
    codec: Optional[int] = None
    predictor: Optional[int] = None
    step_index: Optional[int] = None
    opcode: int = 0


def parse_control(data: bytes) -> ControlEvent:
    if not data:
        raise ProtocolError("control: empty payload")
    opcode = data[0]
    if opcode == OPCODE_CAPS:
        return ControlEvent("caps", caps=parse_caps(data), opcode=opcode)
    if opcode == OPCODE_AUDIO_START:
        # Observed on this model: 04 03 01 10 (reason, codec, session/flags).
        return ControlEvent(
            "start",
            reason=data[1] if len(data) >= 2 else None,
            codec=data[2] if len(data) >= 3 else None,
            session_id=data[3] if len(data) >= 4 else None,
            opcode=opcode,
        )
    if opcode == OPCODE_AUDIO_STOP:
        return ControlEvent("stop", reason=data[1] if len(data) >= 2 else None, opcode=opcode)
    if opcode == OPCODE_AUDIO_SYNC:
        if len(data) >= 7:
            return ControlEvent(
                "sync",
                predictor=int.from_bytes(data[4:6], "big", signed=True),
                step_index=data[6],
                opcode=opcode,
            )
        # This model emits 315 short "0a 00 NN" counter notifications during a
        # 10 s stream (one per 134-byte frame). They are frame counters, NOT
        # predictor resets: treating them as an ADPCM sync would destroy audio.
        if len(data) >= 3:
            return ControlEvent("frame_counter", session_id=data[2], opcode=opcode)
        return ControlEvent("unknown", opcode=opcode)
    if opcode == OPCODE_MIC_BUTTON:
        return ControlEvent("mic_button", opcode=opcode)
    return ControlEvent("unknown", opcode=opcode)


class IMAADPCMDecoder:
    """Standard IMA/DVI 4-bit decoder, 2 samples/byte, HIGH nibble first.

    State stays continuous within a payload; VoiceDecoder restores each
    complete DVI frame's predictor/index before passing its payload here.
    """

    def __init__(self) -> None:
        self.predictor = 0
        self.step_index = 0

    def reset(self, predictor: int = 0, step_index: int = 0) -> None:
        self.predictor = min(32767, max(-32768, predictor))
        self.step_index = min(88, max(0, step_index))

    def decode(self, data: bytes) -> List[int]:
        out: List[int] = []
        append = out.append
        for byte in data:
            append(self._nibble(byte >> 4))
            append(self._nibble(byte & 0x0F))
        return out

    def _nibble(self, nibble: int) -> int:
        step = _STEP_TABLE[self.step_index]
        difference = step >> 3
        if nibble & 1:
            difference += step >> 2
        if nibble & 2:
            difference += step >> 1
        if nibble & 4:
            difference += step
        self.predictor = (self.predictor - difference) if (nibble & 8) else (self.predictor + difference)
        self.predictor = min(32767, max(-32768, self.predictor))
        self.step_index += _INDEX_TABLE[nibble & 7]
        self.step_index = min(88, max(0, self.step_index))
        return self.predictor


def encode_adpcm(samples: Sequence[int]) -> bytes:
    """Reference encoder, used by offline round-trip tests (high nibble first)."""
    step_index = 0
    predictor = 0
    out = bytearray()
    pending = 0
    for index, target in enumerate(samples):
        step = _STEP_TABLE[step_index]
        diff = int(target) - predictor
        nibble = 0 if diff >= 0 else 8
        magnitude = abs(diff)
        if magnitude >= step:
            nibble |= 4
            magnitude -= step
        if magnitude >= step >> 1:
            nibble |= 2
            magnitude -= step >> 1
        if magnitude >= step >> 2:
            nibble |= 1
        difference = step >> 3
        if nibble & 1:
            difference += step >> 2
        if nibble & 2:
            difference += step >> 1
        if nibble & 4:
            difference += step
        predictor = (predictor - difference) if (nibble & 8) else (predictor + difference)
        predictor = min(32767, max(-32768, predictor))
        step_index = min(88, max(0, step_index + _INDEX_TABLE[nibble & 7]))
        if index % 2 == 0:
            pending = nibble << 4
        else:
            out.append(pending | nibble)
            pending = 0
    if len(samples) % 2:
        out.append(pending)
    return bytes(out)


class FrameAssembler:
    """Reassemble 20-byte BLE notifications into complete codec frames.

    ``frame_size`` is the value advertised in CAPS_RESP (134 on this model).
    ``header_bytes`` strips the 6-byte per-frame header from the RC89 layout;
    set 0 for remotes whose frame_size already means pure ADPCM payload.
    """

    def __init__(self, frame_size: int = DEFAULT_FRAME_SIZE,
                 header_bytes: int = DEFAULT_FRAME_HEADER) -> None:
        if frame_size < 8:
            raise ValueError("frame_size too small")
        if header_bytes < 0 or header_bytes >= frame_size:
            raise ValueError("header_bytes out of range")
        self.frame_size = frame_size
        self.header_bytes = header_bytes
        self._pending = bytearray()
        self.frames_seen = 0
        self.headers_seen: List[bytes] = []

    def reset(self) -> None:
        self._pending.clear()

    def feed_records(self, data: bytes):
        self._pending.extend(data)
        frames = []
        while len(self._pending) >= self.frame_size:
            frame = bytes(self._pending[:self.frame_size])
            del self._pending[:self.frame_size]
            if self.header_bytes and len(self.headers_seen) < 4:
                self.headers_seen.append(frame[:self.header_bytes])
            frames.append((frame[:self.header_bytes],frame[self.header_bytes:]))
            self.frames_seen += 1
        return frames

    def feed(self, data: bytes) -> List[bytes]:
        return [payload for _,payload in self.feed_records(data)]

    @property
    def pending_bytes(self) -> int:
        return len(self._pending)


class NotificationFrameAssembler:
    """134-byte clone frames: six 20-byte packets and one 14-byte terminator.

    A host restart can abandon an unfinished frame. The next 14-byte packet
    gives an unambiguous frame end independent of control callback ordering.
    Discard an interrupted assembly rather than poisoning every later frame.
    """
    def __init__(self):
        self._pending=bytearray()
        self._discarding=False
        self.discarded_frames=0

    def reset(self):
        self._pending.clear();self._discarding=False

    @property
    def pending_bytes(self):return len(self._pending)

    def feed(self,data):
        if not data:return []
        if len(data)==134:
            if self._pending or self._discarding:self.discarded_frames+=1
            self.reset()
            return [bytes(data)]
        if len(data)==20:
            if not self._discarding:
                if len(self._pending)+20<=120:self._pending.extend(data)
                else:
                    self._pending.clear();self._discarding=True
            return []
        if len(data)==14:
            frame=(bytes(self._pending)+bytes(data)
                   if not self._discarding and len(self._pending)==120 else None)
            if frame is None:self.discarded_frames+=1
            self.reset()
            return [frame] if frame is not None else []
        self._pending.clear();self._discarding=True
        return []


class DCHighPass:
    """Remove slow ADPCM predictor bias without touching speech frequencies."""

    def __init__(self, sample_rate: int = SAMPLE_RATE_8K, cutoff_hz: float = 20.0) -> None:
        self._alpha = math.exp(-2.0 * math.pi * cutoff_hz / sample_rate)
        self.reset()

    def reset(self) -> None:
        self._previous_input = 0.0
        self._previous_output = 0.0
        self._initialized = False

    def process(self, samples: Sequence[int]) -> List[int]:
        if not samples:
            return []
        if not self._initialized:
            self._previous_input = float(samples[0])
            self._initialized = True
        out: List[int] = []
        for sample in samples:
            current = float(sample)
            value = current - self._previous_input + self._alpha * self._previous_output
            self._previous_input = current
            self._previous_output = value
            out.append(min(32767, max(-32768, int(round(value)))))
        return out


class Resampler:
    """Continuous linear resampler (8 kHz -> 48 kHz) with no per-frame seams."""

    def __init__(self, source_rate: int, target_rate: int = OUTPUT_SAMPLE_RATE) -> None:
        if source_rate <= 0 or target_rate <= 0:
            raise ValueError("invalid sample rate")
        self.source_rate = source_rate
        self.target_rate = target_rate
        # Integer-multiple rates (8k/16k -> 48k) use an exact path; floating
        # point accumulation of 1/6 otherwise drifts and yields an extra
        # sample every few frames, which would slowly shift the audio clock.
        self._steps = (target_rate // source_rate
                       if target_rate >= source_rate and target_rate % source_rate == 0 else 0)
        self._previous = 0
        self._have_previous = False
        self._position = 0.0

    def reset(self) -> None:
        self._previous = 0
        self._have_previous = False
        self._position = 0.0

    def process(self, samples: Sequence[int]) -> List[int]:
        if not samples:
            return []
        out: List[int] = []
        previous = self._previous if self._have_previous else samples[0]
        if self._steps:
            steps = self._steps
            for current in samples:
                delta = current - previous
                for index in range(steps):
                    value = previous + delta * index / steps
                    out.append(min(32767, max(-32768, int(round(value)))))
                previous = current
        else:
            ratio = self.target_rate / self.source_rate
            for current in samples:
                delta = current - previous
                while self._position < 1.0:
                    value = previous + delta * self._position
                    out.append(min(32767, max(-32768, int(round(value)))))
                    self._position += 1.0 / ratio
                self._position -= 1.0
                previous = current
        self._previous = previous
        self._have_previous = True
        return out


class VoiceDecoder:
    """CAPS-aware ATVV audio pipeline: notifications -> int16 PCM @48 kHz."""

    def __init__(self, frame_size: int = DEFAULT_FRAME_SIZE,
                 header_bytes: int = DEFAULT_FRAME_HEADER,
                 sample_rate: int = SAMPLE_RATE_8K,
                 gain_db: float = 3.0, *, packet_aligned: bool = False) -> None:
        self.frames = FrameAssembler(frame_size, header_bytes)
        self._packet_aligned=packet_aligned
        self.packets=(NotificationFrameAssembler()
                      if packet_aligned and frame_size==134 and header_bytes==6 else None)
        self.decoder = IMAADPCMDecoder()
        self.highpass = DCHighPass(sample_rate)
        self.resampler = Resampler(sample_rate)
        self.gain = 10.0 ** (min(24.0, max(-24.0, gain_db)) / 20.0)
        self.sample_rate = sample_rate
        self.pcm_samples = 0
        self.peak = 0
        self._pending_sync: Optional[Tuple[int, int]] = None

    def configure(self, caps: Capabilities, header_bytes: Optional[int] = None) -> None:
        header = self.frames.header_bytes if header_bytes is None else header_bytes
        self.frames = FrameAssembler(caps.frame_size, header)
        self.packets=(NotificationFrameAssembler()
                      if self._packet_aligned and caps.frame_size==134 and header==6 else None)
        self.highpass = DCHighPass(caps.sample_rate)
        self.resampler = Resampler(caps.sample_rate)
        self.sample_rate = caps.sample_rate
        self.reset_stream()

    def reset_stream(self) -> None:
        self.frames.reset()
        if self.packets:self.packets.reset()
        self.decoder.reset()
        self.highpass.reset()
        self.resampler.reset()
        self._pending_sync = None

    def on_start(self) -> None:
        self.reset_stream()

    def on_sync(self, predictor: int, step_index: int) -> None:
        self.decoder.reset(predictor, step_index)
        self.highpass.reset()

    def feed(self, notification: bytes) -> List[int]:
        pcm48: List[int] = []
        blocks=self.packets.feed(notification) if self.packets else [notification]
        records=[record for block in blocks for record in self.frames.feed_records(block)]
        for header,payload in records:
            if len(header)==6:
                if header[2]!=0 or header[5]>88:
                    raise ProtocolError('invalid DVI frame header')
                # Each 134-byte frame carries independent codec state:
                # seqBE(2), reserved(1), predictorBE(2), index(1).
                self.decoder.reset(int.from_bytes(header[3:5],'big',signed=True),header[5])
                self._pending_sync=None
            elif self._pending_sync is not None:
                self.on_sync(*self._pending_sync)
                self._pending_sync = None
            decoded = self.decoder.decode(payload)
            centered = self.highpass.process(decoded)
            amplified = [min(32767, max(-32768, int(round(v * self.gain)))) for v in centered]
            pcm48.extend(self.resampler.process(amplified))
        self.pcm_samples += len(pcm48)
        if pcm48:
            self.peak = max(self.peak, max(abs(v) for v in pcm48))
        return pcm48

    def set_sync(self, predictor: int, step_index: int) -> None:
        self._pending_sync = (predictor, step_index)


def frame_stats(decoder: VoiceDecoder) -> dict:
    """Privacy-safe evidence: counts and levels only, never audio or text."""
    return {
        "frames": decoder.frames.frames_seen,
        "pending_bytes": decoder.frames.pending_bytes+(decoder.packets.pending_bytes if decoder.packets else 0),
        "discarded_packet_frames": decoder.packets.discarded_frames if decoder.packets else 0,
        "packet_mode": decoder.packets is not None,
        "pcm_samples": decoder.pcm_samples,
        "peak": decoder.peak,
        "sample_rate": decoder.sample_rate,
        "frame_size": decoder.frames.frame_size,
        "header_bytes": decoder.frames.header_bytes,
        "headers": [h.hex() for h in decoder.frames.headers_seen[:4]],
    }
