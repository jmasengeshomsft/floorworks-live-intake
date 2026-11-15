"""PCM16 helpers. ACS streams 24 kHz mono PCM16 little-endian."""
from __future__ import annotations

import base64
import math
import struct

SAMPLE_RATE = 24000
FRAME_MS = 200
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000


def b64d(s: str) -> bytes:
    return base64.b64decode(s)


def b64e(b: bytes) -> str:
    return base64.b64encode(b).decode()


def tone_frame(amplitude: int = 5500) -> bytes:
    """One 200 ms frame of a 440+480 Hz ringback tone."""
    out = bytearray()
    for n in range(FRAME_SAMPLES):
        t = n / SAMPLE_RATE
        v = (math.sin(2 * math.pi * 440 * t) + math.sin(2 * math.pi * 480 * t)) / 2 * amplitude
        out += struct.pack("<h", int(v))
    return bytes(out)


def silence_frame() -> bytes:
    return bytes(FRAME_SAMPLES * 2)


def ring_cycle() -> list[bytes]:
    """2 s of tone, 4 s of silence, in 200 ms frames."""
    return [tone_frame()] * 10 + [silence_frame()] * 20


def interleave(caller: bytes, echo_ref: bytes) -> bytes:
    """Stereo interleave: left = caller mic, right = AI echo reference (padded with silence)."""
    n = len(caller) // 2
    ref = echo_ref[: n * 2].ljust(n * 2, b"\x00")
    out = bytearray(n * 4)
    out[0::4] = caller[0 : n * 2 : 2]
    out[1::4] = caller[1 : n * 2 : 2]
    out[2::4] = ref[0::2]
    out[3::4] = ref[1::2]
    return bytes(out)


def resample_24_to_16(pcm: bytes) -> bytes:
    """Linear 24k -> 16k (ratio 1.5)."""
    n = len(pcm) // 2
    if n == 0:
        return b""
    s = struct.unpack(f"<{n}h", pcm[: n * 2])
    m = int(n / 1.5)
    out = []
    for i in range(m):
        pos = i * 1.5
        j = int(pos)
        frac = pos - j
        nxt = s[j + 1] if j + 1 < n else s[j]
        out.append(int(s[j] + (nxt - s[j]) * frac))
    return struct.pack(f"<{m}h", *out)
