"""Needle state machine: IDLE / SENSING / PLAYING / GRACE.

Pure logic, no asyncio. Injectable clock for testability. Consumers act on
returned StateEvents (Sonos start/stop, UI push) - events fire exactly on
transitions.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Callable

from needledrop.config import DetectCfg


class Phase(StrEnum):
    IDLE = "IDLE"
    SENSING = "SENSING"
    PLAYING = "PLAYING"
    GRACE = "GRACE"


@dataclass
class StateEvent:
    phase: Phase
    reason: str


class StateMachine:
    def __init__(self, cfg: DetectCfg, now: Callable[[], float] = time.monotonic):
        self._cfg = cfg
        self._now = now
        self._phase = Phase.IDLE
        self._sensing_since = 0.0
        self._grace_since = 0.0

    @property
    def phase(self) -> Phase:
        return self._phase

    @property
    def grace_remaining_s(self) -> float | None:
        if self._phase is not Phase.GRACE:
            return None
        return max(0.0, self._cfg.end_of_side_s - (self._now() - self._grace_since))

    def update(self, music: bool) -> StateEvent | None:
        """Call once per detector window; returns an event exactly on transitions."""
        t = self._now()
        if self._phase is Phase.IDLE:
            if music:
                self._phase = Phase.SENSING
                self._sensing_since = t
                return StateEvent(Phase.SENSING, "music detected")
        elif self._phase is Phase.SENSING:
            if not music:
                self._phase = Phase.IDLE
                return StateEvent(Phase.IDLE, "music flickered off during debounce")
            if t - self._sensing_since >= self._cfg.start_debounce_s:
                self._phase = Phase.PLAYING
                return StateEvent(Phase.PLAYING, "music sustained through debounce")
        elif self._phase is Phase.PLAYING:
            if not music:
                self._phase = Phase.GRACE
                self._grace_since = t
                return StateEvent(Phase.GRACE, "music stopped")
        elif self._phase is Phase.GRACE:
            if music:
                self._phase = Phase.PLAYING
                return StateEvent(Phase.PLAYING, "music returned")
            if t - self._grace_since >= self._cfg.end_of_side_s:
                self._phase = Phase.IDLE
                return StateEvent(Phase.IDLE, "end of side")
        return None

    def manual_start(self) -> StateEvent:
        self._phase = Phase.PLAYING
        return StateEvent(Phase.PLAYING, "manual start")

    def manual_stop(self) -> StateEvent:
        self._phase = Phase.IDLE
        return StateEvent(Phase.IDLE, "manual stop")

    def keep_playing(self) -> None:
        """In GRACE: restart the end-of-side countdown."""
        if self._phase is Phase.GRACE:
            self._grace_since = self._now()
