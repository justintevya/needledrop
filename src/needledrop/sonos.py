"""Sonos orchestration via SoCo: group vinyl zones, play the stream on the
coordinator, watchdog, busy-skip, best-effort restore on stop.

Spike findings (binding): play_uri on a grouped non-coordinator raises - the
target must unjoin() first, then settle ~5s before Play succeeds (UPnP 701 is
transient after a regroup), so play is retried up to 3 times. Never key any
state off HTTP listener connect/disconnect counts (Sonos probes with short
GETs from arbitrary fleet IPs).

soco is imported lazily so unit tests never touch network-capable code.
All per-zone SoCo calls are wrapped: failures are logged and the zone marked
unreachable - they never raise out of the controller.
"""

from __future__ import annotations

import logging
import time
from typing import Callable, TypedDict

from needledrop.config import SonosCfg

log = logging.getLogger(__name__)

BUSY_STATES = {"PLAYING", "TRANSITIONING"}
PLAY_RETRIES = 3
UNJOIN_SETTLE_S = 5.0
STREAM_TITLE = "Vinyl - needledrop"


class ZoneInfo(TypedDict):
    name: str
    grouped: bool
    skipped_busy: bool
    volume: int
    reachable: bool


def _default_discover(**kwargs):
    import soco  # lazy: never imported during unit tests

    return soco.discover(**kwargs)


class SonosController:
    def __init__(self, cfg: SonosCfg, stream_url: str,
                 discover: Callable = _default_discover,
                 sleep: Callable[[float], None] = time.sleep):
        self._cfg = cfg
        self._discover = discover
        self._sleep = sleep
        self._uri = "x-rincon-mp3radio://" + stream_url.split("://", 1)[-1]
        self._coordinator = None
        self._members: list = []
        self._snapshots: dict[str, int] = {}
        self._infos: list[ZoneInfo] = []

    # -- helpers ---------------------------------------------------------

    def _find_zones(self) -> dict[str, object]:
        try:
            found = self._discover(timeout=10) or []
        except Exception as exc:
            log.warning("sonos discovery failed: %s", exc)
            found = []
        by_name = {z.player_name: z for z in found}
        return {name: by_name.get(name) for name in self._cfg.vinyl_zones}

    def _is_busy(self, zone) -> bool:
        if not self._cfg.dont_interrupt_busy:
            return False
        state = zone.get_current_transport_info()["current_transport_state"]
        uri = zone.get_current_track_info().get("uri", "")
        return state in BUSY_STATES and uri != self._uri

    def _play_with_retry(self, zone) -> bool:
        """Unjoin a grouped non-coordinator first (settle 5s), retry Play 3x."""
        try:
            if zone.group is not None and zone.group.coordinator is not zone:
                zone.unjoin()
                self._sleep(UNJOIN_SETTLE_S)
        except Exception as exc:
            log.warning("%s: unjoin failed: %s", zone.player_name, exc)
        for attempt in range(1, PLAY_RETRIES + 1):
            try:
                zone.play_uri(self._uri, title=STREAM_TITLE)
                return True
            except Exception as exc:  # SoCoUPnPException 701 is transient
                log.warning("%s: play attempt %d failed: %s", zone.player_name, attempt, exc)
                if attempt < PLAY_RETRIES:
                    self._sleep(UNJOIN_SETTLE_S)
        return False

    # -- public API ------------------------------------------------------

    def start(self) -> list[ZoneInfo]:
        zones = self._find_zones()
        infos: list[ZoneInfo] = []
        candidates: dict[str, object] = {}
        for name, zone in zones.items():
            info = ZoneInfo(name=name, grouped=False, skipped_busy=False,
                            volume=0, reachable=zone is not None)
            if zone is not None:
                try:
                    info["volume"] = zone.volume
                    info["skipped_busy"] = self._is_busy(zone)
                except Exception as exc:
                    log.warning("%s: unreachable: %s", name, exc)
                    info["reachable"] = False
                if info["reachable"] and not info["skipped_busy"]:
                    candidates[name] = zone
            infos.append(info)
        self._infos = infos
        if not candidates:
            log.warning("no playable vinyl zones found")
            return infos
        coord_name = (self._cfg.preferred_coordinator
                      if self._cfg.preferred_coordinator in candidates
                      else next(iter(candidates)))
        coordinator = candidates[coord_name]
        self._snapshots = {}
        for name, zone in candidates.items():
            try:
                self._snapshots[name] = zone.volume
                if self._cfg.master_volume is not None:
                    zone.volume = self._cfg.master_volume
            except Exception as exc:
                log.warning("%s: volume snapshot failed: %s", name, exc)
        if not self._play_with_retry(coordinator):
            self._mark(coord_name, reachable=False)
            return infos
        self._coordinator = coordinator
        self._members = []
        self._mark(coord_name, grouped=True)
        for name, zone in candidates.items():
            if zone is coordinator:
                continue
            try:
                zone.join(coordinator)
                self._members.append(zone)
                self._mark(name, grouped=True)
            except Exception as exc:
                log.warning("%s: join failed: %s", name, exc)
                self._mark(name, reachable=False)
        return infos

    def _mark(self, name: str, **fields) -> None:
        for info in self._infos:
            if info["name"] == name:
                info.update(fields)  # type: ignore[typeddict-item]

    def stop(self) -> None:
        """Stop, ungroup, restore snapshots - best effort, never raises."""
        if self._coordinator is not None:
            try:
                self._coordinator.stop()
            except Exception as exc:
                log.warning("coordinator stop failed: %s", exc)
        for zone in self._members:
            try:
                zone.unjoin()
            except Exception as exc:
                log.warning("%s: unjoin failed: %s", zone.player_name, exc)
        for zone in [self._coordinator, *self._members]:
            if zone is None:
                continue
            snap = self._snapshots.get(zone.player_name)
            if snap is not None:
                try:
                    zone.volume = snap
                except Exception as exc:
                    log.warning("%s: volume restore failed: %s", zone.player_name, exc)
        self._coordinator = None
        self._members = []

    def watchdog_tick(self, music: bool) -> None:
        """Every ~10s in PLAYING: re-issue play if the coordinator stopped."""
        if not music or self._coordinator is None:
            return
        try:
            state = self._coordinator.get_current_transport_info()["current_transport_state"]
        except Exception as exc:
            log.warning("watchdog: coordinator unreachable: %s", exc)
            return
        if state not in BUSY_STATES:
            log.info("watchdog: coordinator %s, re-issuing play", state)
            self._play_with_retry(self._coordinator)

    def set_volume(self, zone_name: str, vol: int) -> None:
        for zone in [self._coordinator, *self._members]:
            if zone is not None and zone.player_name == zone_name:
                try:
                    zone.volume = vol
                except Exception as exc:
                    log.warning("%s: set volume failed: %s", zone_name, exc)
                return

    def zones(self) -> list[ZoneInfo]:
        return list(self._infos)
