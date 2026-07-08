"""Vinyl-aware level detection: biquad high-pass + windowed RMS + hysteresis.

Pure Python by design (no numpy/scipy). The high-pass rejects turntable
rumble; hysteresis between music_on_db/music_off_db avoids flicker in
quiet passages and track gaps.
"""

from __future__ import annotations

import math
from array import array

from needledrop.config import DetectCfg

DB_FLOOR = -100.0
WINDOW_S = 0.3          # RMS window length
EMA_ALPHA = 0.3         # per-window smoothing → ~1s time constant
HP_STAGES = 3           # cascaded biquads: 36 dB/oct rumble rejection


class Biquad:
    """RBJ cookbook biquad, Direct Form 1."""

    def __init__(self, b0: float, b1: float, b2: float, a1: float, a2: float):
        self.b0, self.b1, self.b2, self.a1, self.a2 = b0, b1, b2, a1, a2
        self._x1 = self._x2 = self._y1 = self._y2 = 0.0

    @classmethod
    def highpass(cls, fs: float, f0: float, q: float = 0.707) -> "Biquad":
        w0 = 2.0 * math.pi * f0 / fs
        cw, sw = math.cos(w0), math.sin(w0)
        alpha = sw / (2.0 * q)
        a0 = 1.0 + alpha
        b0 = (1.0 + cw) / 2.0 / a0
        b1 = -(1.0 + cw) / a0
        b2 = (1.0 + cw) / 2.0 / a0
        a1 = (-2.0 * cw) / a0
        a2 = (1.0 - alpha) / a0
        return cls(b0, b1, b2, a1, a2)

    def process(self, samples: list[float]) -> list[float]:
        b0, b1, b2, a1, a2 = self.b0, self.b1, self.b2, self.a1, self.a2
        x1, x2, y1, y2 = self._x1, self._x2, self._y1, self._y2
        out = []
        append = out.append
        for x in samples:
            y = b0 * x + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
            x2, x1 = x1, x
            y2, y1 = y1, y
            append(y)
        self._x1, self._x2, self._y1, self._y2 = x1, x2, y1, y2
        return out


class LevelDetector:
    def __init__(self, cfg: DetectCfg, sample_rate: int = 44100):
        self._cfg = cfg
        self._fs = sample_rate
        self._window_n = int(WINDOW_S * sample_rate)
        self._filters = [
            Biquad.highpass(sample_rate, cfg.highpass_hz) for _ in range(HP_STAGES)
        ]
        self._pending: list[float] = []
        self._ema: float | None = None
        self._music = False

    def feed(self, pcm_s16le_stereo: bytes) -> None:
        samples = array("h")
        samples.frombytes(pcm_s16le_stereo[: len(pcm_s16le_stereo) & ~3])
        # stereo S16 → mono floats in [-1, 1]
        mono = [
            (samples[i] + samples[i + 1]) / 65536.0 for i in range(0, len(samples), 2)
        ]
        for filt in self._filters:
            mono = filt.process(mono)
        self._pending.extend(mono)
        n = self._window_n
        while len(self._pending) >= n:
            window, self._pending = self._pending[:n], self._pending[n:]
            self._consume_window(window)

    def _consume_window(self, window: list[float]) -> None:
        rms = math.sqrt(math.fsum(x * x for x in window) / len(window))
        db = max(20.0 * math.log10(rms) if rms > 0.0 else DB_FLOOR, DB_FLOOR)
        if self._ema is None:
            self._ema = db
        else:
            self._ema += EMA_ALPHA * (db - self._ema)
        if self._ema >= self._cfg.music_on_db:
            self._music = True
        elif self._ema <= self._cfg.music_off_db:
            self._music = False

    @property
    def rms_db(self) -> float:
        return self._ema if self._ema is not None else DB_FLOOR

    @property
    def music(self) -> bool:
        return self._music
