"""Capture supervisor: ALSA device → raw S16LE stereo PCM callback, with
unplug recovery (exponential backoff + device re-resolution).

Default backend is an `arecord` subprocess pipe - the physics spike showed
PortAudio/sounddevice overflowing every period on a PipeWire host while
arecord was flawless. `SounddeviceBackend` remains as an optional alternative
(lazy import, never required).

Callbacks fire on the supervisor's reader thread; app.py bridges them onto
the event loop via `loop.call_soon_threadsafe`.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import threading
from contextlib import contextmanager
from typing import Callable, ContextManager, Protocol

from needledrop.config import AudioCfg

log = logging.getLogger(__name__)

PcmCallback = Callable[[bytes], None]


class CaptureBackend(Protocol):
    def open(self, device: str, callback: PcmCallback) -> ContextManager:
        """Open a capture session. The yielded object may expose
        `pump(stop_event)` - a blocking read loop returning on stream end.
        Backends without `pump` (callback-driven) yield None."""
        ...


class _ArecordSession:
    def __init__(self, proc, callback: PcmCallback, chunk_bytes: int):
        self._proc = proc
        self._callback = callback
        self._chunk = chunk_bytes

    def pump(self, stop: threading.Event | None) -> None:
        """Blocking read loop; returns on process exit (device lost) or stop."""
        while stop is None or not stop.is_set():
            data = self._proc.stdout.read(self._chunk)
            if not data:
                break
            self._callback(data)


class ArecordBackend:
    """arecord -D <dev> -f S16_LE -r 44100 -c 2 -t raw -q | fixed-size chunks."""

    CHUNK_BYTES = 4608 * 4  # 4608 stereo S16 frames ≙ 4 MP3 frames at 44.1k

    def __init__(self, popen=subprocess.Popen):
        self._popen = popen

    @contextmanager
    def open(self, device: str, callback: PcmCallback):
        proc = self._popen(
            ["arecord", "-D", device, "-f", "S16_LE", "-r", "44100",
             "-c", "2", "-t", "raw", "-q"],
            stdout=subprocess.PIPE,
        )
        try:
            yield _ArecordSession(proc, callback, self.CHUNK_BYTES)
        finally:
            try:
                proc.kill()
                proc.wait(timeout=5)
            except Exception:
                pass


class SounddeviceBackend:
    """Optional PortAudio backend (callback-driven). Not the default."""

    def __init__(self, sample_rate: int = 44100, blocksize: int = 4608):
        self._sample_rate = sample_rate
        self._blocksize = blocksize
        self.xruns = 0

    @contextmanager
    def open(self, device: str, callback: PcmCallback):
        import sounddevice as sd  # lazy: wheel may be absent on dev boxes

        def cb(indata, frames, t, status):
            if status:
                self.xruns += 1
            callback(bytes(indata))

        with sd.RawInputStream(device=device, samplerate=self._sample_rate,
                               channels=2, dtype="int16",
                               blocksize=self._blocksize, callback=cb):
            yield None


class CaptureSupervisor:
    """Runs capture forever: resolve device → open backend in a worker thread →
    on failure/stream-end mark device lost, back off, re-resolve, retry."""

    def __init__(self, cfg: AudioCfg, on_pcm: PcmCallback,
                 backend: CaptureBackend | None = None,
                 resolve: Callable[[], str] | None = None,
                 backoff_s: tuple[float, ...] = (1, 2, 5, 10, 30)):
        self._cfg = cfg
        self._on_pcm = on_pcm
        self.backend = backend if backend is not None else ArecordBackend()
        self._resolve = resolve if resolve is not None else self._default_resolve
        self._backoff = backoff_s
        self._stop = threading.Event()
        self.device_present = False
        self.xruns = 0

    def _default_resolve(self) -> str:
        from needledrop import devices

        cards = devices.list_cards()
        return devices.alsa_device_string(devices.resolve_card(self._cfg.device, cards))

    def _session(self, device: str) -> None:
        """Blocking; runs in an executor thread for the life of one capture."""
        with self.backend.open(device, self._on_pcm) as session:
            self.device_present = True
            if session is not None and hasattr(session, "pump"):
                session.pump(self._stop)
            else:
                self._stop.wait()

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        attempt = 0
        try:
            while not self._stop.is_set():
                try:
                    device = self._resolve()
                    await loop.run_in_executor(None, self._session, device)
                    attempt = 0  # session ran; restart backoff ladder fresh
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log.warning("capture failed: %s", exc)
                if self._stop.is_set():
                    return
                self.device_present = False
                self.xruns += 1  # any session end while running counts as a dropout
                delay = self._backoff[min(attempt, len(self._backoff) - 1)]
                attempt += 1
                await asyncio.sleep(delay)
        finally:
            self._stop.set()  # release any executor thread parked in _session
