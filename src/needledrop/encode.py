"""Thin lameenc wrapper: PCM S16LE → MP3 CBR frames, plus silence generation.

lameenc is imported lazily so pure-logic modules/tests never need the wheel
(it may be unavailable on dev boxes; CI on Linux covers it).
"""

from __future__ import annotations


class Mp3Encoder:
    def __init__(self, bitrate: int = 320, sample_rate: int = 44100, channels: int = 2):
        import lameenc

        enc = lameenc.Encoder()
        enc.set_bit_rate(bitrate)
        enc.set_in_sample_rate(sample_rate)
        enc.set_channels(channels)
        enc.set_quality(2)
        self._enc = enc

    def encode(self, pcm_s16le: bytes) -> bytes:
        """Encode interleaved S16LE PCM; may return b"" while lame buffers."""
        return bytes(self._enc.encode(pcm_s16le))

    def flush(self) -> bytes:
        return bytes(self._enc.flush())


def make_silence(ms: int, sample_rate: int = 44100) -> bytes:
    """PCM zeros (stereo S16LE) - GRACE keepalive so listener sockets never stall."""
    frames = sample_rate * ms // 1000
    return b"\x00" * (frames * 4)
