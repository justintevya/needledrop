import math
import struct

from needledrop.config import DetectCfg
from needledrop.detect import Biquad, LevelDetector
from needledrop.ringbuf import RingBuffer


def pcm(freq_hz: float, seconds: float, amp: float, fs=44100) -> bytes:
    n = int(seconds * fs)
    out = bytearray()
    for i in range(n):
        v = int(amp * 32767 * math.sin(2 * math.pi * freq_hz * i / fs))
        out += struct.pack("<hh", v, v)
    return bytes(out)


def silence(seconds: float) -> bytes:
    return pcm(0, seconds, 0)


def test_silence_is_quiet_and_music_detected():
    d = LevelDetector(DetectCfg())
    d.feed(silence(2))
    assert d.rms_db < -70 and d.music is False
    d.feed(pcm(1000, 3, 0.3))            # ~ -10 dBFS tone
    assert d.rms_db > -20 and d.music is True


def test_hysteresis_holds_between_thresholds():
    d = LevelDetector(DetectCfg())
    d.feed(pcm(1000, 2, 0.3))
    assert d.music
    d.feed(pcm(1000, 3, 0.003))          # ~ -50 dBFS: between -45 and -55 → hold True
    assert d.music is True
    d.feed(silence(4))
    assert d.music is False


def test_rumble_rejected_by_highpass():
    d = LevelDetector(DetectCfg())
    d.feed(pcm(20, 4, 0.2))              # 20 Hz rumble, would be ~-14 dBFS raw
    assert d.rms_db < -40                # HP at 40 Hz crushes it
    assert d.music is False


def test_biquad_passes_1k():
    b = Biquad.highpass(44100, 40)
    sine = [math.sin(2 * math.pi * 1000 * i / 44100) for i in range(4410)]
    out = b.process(sine)
    peak = max(abs(x) for x in out[2000:])
    assert 0.9 < peak < 1.1


def test_ringbuffer_drops_oldest_and_drains():
    rb = RingBuffer(capacity_bytes=8)
    rb.write(b"12345678")
    rb.write(b"AB")                      # over capacity → oldest 2 bytes dropped
    assert rb.read_all() == b"345678AB"
    assert rb.read_all() == b""
