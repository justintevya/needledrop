import pytest

lameenc = pytest.importorskip("lameenc")

from needledrop.encode import Mp3Encoder, make_silence  # noqa: E402


def test_encodes_valid_mp3_frames():
    enc = Mp3Encoder()
    out = enc.encode(make_silence(500)) + enc.flush()
    assert len(out) > 1000
    sync = next(i for i in range(len(out) - 1) if out[i] == 0xFF and (out[i + 1] & 0xE0) == 0xE0)
    assert (out[sync + 2] >> 4) & 0xF == 0xE   # 320kbps index for MPEG1 L3
