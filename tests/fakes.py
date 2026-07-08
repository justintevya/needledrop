"""Shared test fakes: FakeZone mimics the slice of SoCo that sonos.py uses."""

from __future__ import annotations


class FakeGroup:
    def __init__(self, coordinator):
        self.coordinator = coordinator


class FakeZone:
    def __init__(self, name: str, state: str = "STOPPED", uri: str = "", volume: int = 20):
        self.player_name = name
        self.state = state
        self.uri = uri
        self.volume = volume
        self.group = FakeGroup(self)
        self.played: list[str] = []
        self.joined = None
        self.stopped = False
        self.unjoined = False

    def get_current_transport_info(self):
        return {"current_transport_state": self.state}

    def get_current_track_info(self):
        return {"uri": self.uri}

    def play_uri(self, uri: str, title: str = ""):
        self.played.append(uri)
        self.uri = uri
        self.state = "PLAYING"
        self.group = FakeGroup(self)

    def join(self, other):
        self.joined = other
        self.group = other.group

    def unjoin(self):
        self.unjoined = True
        self.group = FakeGroup(self)

    def stop(self):
        self.stopped = True
        self.state = "STOPPED"


def fake_discover(zones):
    def discover(**kwargs):
        return set(zones)

    return discover
