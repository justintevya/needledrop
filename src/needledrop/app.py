"""Composition root: wires capture → detector/state → encoder → hub → sonos,
and owns both server lifecycles (aiohttp on stream.port, raw MP3 stream on
stream.port + 1).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket as _socket
import time
from pathlib import Path

from aiohttp import web

from needledrop.capture import CaptureSupervisor
from needledrop.config import Config, save_config
from needledrop.detect import LevelDetector
from needledrop.encode import make_silence
from needledrop.state import Phase, StateMachine
from needledrop.stream import RawStreamServer, StreamHub

log = logging.getLogger(__name__)

TICK_S = 0.3
WATCHDOG_EVERY_S = 10.0
CALIBRATE_S = 10.0


def detect_lan_ip(sock_mod=_socket) -> str:
    """LAN IP via the UDP-connect trick (no packets sent); hostname fallback."""
    try:
        s = sock_mod.socket(sock_mod.AF_INET, sock_mod.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return sock_mod.gethostbyname(sock_mod.gethostname())


class Core:
    """Owns every runtime component; web.py handlers call into it."""

    def __init__(self, cfg: Config, *, hub: StreamHub, detector: LevelDetector,
                 sm: StateMachine, sonos, supervisor: CaptureSupervisor,
                 encoder=None, config_path: Path | None = None,
                 now=time.monotonic):
        self.cfg = cfg
        self.hub = hub
        self.detector = detector
        self.sm = sm
        self.sonos = sonos
        self.supervisor = supervisor
        self.encoder = encoder
        self._config_path = config_path
        self._now = now
        self._loop: asyncio.AbstractEventLoop | None = None
        self._pcm_q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=100)
        self._pcm_seen = False
        self._sonos_active = False
        self._last_watchdog = 0.0
        self.shutdown = asyncio.Event()

    @property
    def stream_port(self) -> int:
        return self.cfg.stream.port + 1

    @classmethod
    def from_config(cls, cfg: Config, config_path: Path | None = None) -> "Core":
        from needledrop.sonos import SonosController

        hub = StreamHub()
        core = cls(
            cfg,
            hub=hub,
            detector=LevelDetector(cfg.detect, cfg.audio.sample_rate),
            sm=StateMachine(cfg.detect),
            sonos=SonosController(
                cfg.sonos,
                f"http://{detect_lan_ip()}:{cfg.stream.port + 1}/stream.mp3",
            ),
            supervisor=None,  # placeholder; needs core._on_pcm bound first
            encoder=cls._make_encoder(cfg.stream.bitrate),
            config_path=config_path,
        )
        core.supervisor = CaptureSupervisor(cfg.audio, core._on_pcm)
        return core

    @staticmethod
    def _make_encoder(bitrate: int):
        try:
            from needledrop.encode import Mp3Encoder

            return Mp3Encoder(bitrate)
        except Exception as exc:  # lameenc wheel absent on some dev boxes
            log.error("MP3 encoder unavailable (%s) - stream will be silent", exc)
            return None

    # -- capture thread → loop bridge -------------------------------------

    def _on_pcm(self, pcm: bytes) -> None:
        """Called on the capture thread."""
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._ingest, pcm)

    def _ingest(self, pcm: bytes) -> None:
        self.detector.feed(pcm)
        self._pcm_seen = True
        with contextlib.suppress(asyncio.QueueFull):
            self._pcm_q.put_nowait(pcm)

    # -- control -----------------------------------------------------------

    async def manual_start(self) -> None:
        await self._apply(self.sm.manual_start())

    async def manual_stop(self) -> None:
        await self._apply(self.sm.manual_stop())

    async def keep_playing(self) -> None:
        self.sm.keep_playing()

    async def set_volume(self, zone: str, volume: int) -> None:
        await asyncio.get_running_loop().run_in_executor(
            None, self.sonos.set_volume, zone, volume
        )

    async def update_config(self, cfg: Config) -> None:
        """Persist a validated config, then exit for a clean systemd restart."""
        if self._config_path is not None:
            save_config(cfg, self._config_path)
        self.shutdown.set()

    async def calibrate(self, phase: str, duration_s: float = CALIBRATE_S) -> float:
        """Sample the smoothed post-filter level for `duration_s`; return dBFS."""
        end = self._now() + duration_s
        peak = -100.0
        while self._now() < end:
            peak = max(peak, self.detector.rms_db)
            await asyncio.sleep(TICK_S)
        return round(max(peak, self.detector.rms_db), 1)

    async def _apply(self, event) -> None:
        if event is None:
            return
        loop = asyncio.get_running_loop()
        if event.phase is Phase.PLAYING and not self._sonos_active:
            self._sonos_active = True
            await loop.run_in_executor(None, self.sonos.start)
        elif event.phase is Phase.IDLE and self._sonos_active:
            self._sonos_active = False
            await loop.run_in_executor(None, self.sonos.stop)

    def snapshot(self) -> dict:
        return {
            "phase": str(self.sm.phase),
            "device_present": self.supervisor.device_present if self.supervisor else False,
            "listeners": self.hub.listener_count,
            "xruns": self.supervisor.xruns if self.supervisor else 0,
            "dropped_chunks": self.hub.dropped_chunks,
            "zones": self.sonos.zones(),
            "rms_db": round(self.detector.rms_db, 1),
            "music_on_db": self.cfg.detect.music_on_db,
            "music_off_db": self.cfg.detect.music_off_db,
            "grace_remaining_s": self.sm.grace_remaining_s,
        }

    # -- runtime loops -------------------------------------------------------

    async def _encode_loop(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            pcm = await self._pcm_q.get()
            if self.encoder is None:
                continue
            mp3 = await loop.run_in_executor(None, self.encoder.encode, pcm)
            if mp3:
                self.hub.publish(mp3)

    async def _tick_loop(self) -> None:
        while True:
            await asyncio.sleep(TICK_S)
            await self._apply(self.sm.update(self.detector.music))
            phase = self.sm.phase
            # Never stall listener sockets: encode silence while the stream
            # must stay alive but no PCM is flowing (device lost mid-side).
            if phase in (Phase.PLAYING, Phase.GRACE) and not self._pcm_seen:
                self._ingest(make_silence(int(TICK_S * 1000), self.cfg.audio.sample_rate))
            self._pcm_seen = False
            if phase is Phase.PLAYING and self._now() - self._last_watchdog >= WATCHDOG_EVERY_S:
                self._last_watchdog = self._now()
                # Fire-and-forget: watchdog_tick's unjoin/settle/retry can block
                # 10-15s and must never delay state updates or silence keepalive.
                task = asyncio.get_running_loop().run_in_executor(
                    None, self.sonos.watchdog_tick, self.detector.music)
                self._watchdog_task = task  # kept for tests/inspection; not awaited

    async def run(self) -> None:
        from needledrop.web import build_app

        self._loop = asyncio.get_running_loop()
        raw = RawStreamServer(self.hub, port=self.stream_port)
        await raw.start()
        runner = web.AppRunner(build_app(self))
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", self.cfg.stream.port)
        await site.start()
        log.info("web/API on :%d, mp3 stream on :%d", self.cfg.stream.port, self.stream_port)
        tasks = [asyncio.create_task(t) for t in
                 (self.supervisor.run(), self._encode_loop(), self._tick_loop())]
        try:
            await self.shutdown.wait()
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await raw.close()
            await runner.cleanup()


async def main_async(config_path: Path) -> None:
    from needledrop.config import load_config

    cfg = load_config(config_path)
    core = Core.from_config(cfg, config_path=config_path)
    await core.run()
