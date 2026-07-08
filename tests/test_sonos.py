"""Task 10: Sonos orchestration - busy-skip, watchdog, restore, unjoin+retry."""

from needledrop.config import SonosCfg
from needledrop.sonos import SonosController

from tests.fakes import FakeGroup, FakeZone, fake_discover


def make(zones, sleeps=None, **cfg):
    c = SonosCfg(vinyl_zones=cfg.pop("vinyl_zones", ["Living Room", "Office"]),
                 preferred_coordinator="Living Room", **cfg)
    sleep = sleeps.append if sleeps is not None else (lambda s: None)
    return SonosController(c, "http://10.0.0.5:8342/stream.mp3",
                           discover=fake_discover(zones), sleep=sleep)


def test_groups_and_plays_on_coordinator():
    lr, of = FakeZone("Living Room"), FakeZone("Office")
    ctl = make([lr, of])
    infos = ctl.start()
    assert lr.played and lr.played[0].startswith("x-rincon-mp3radio://10.0.0.5:8342")
    assert of.joined is lr
    assert all(i["grouped"] for i in infos)


def test_busy_zone_skipped():
    lr = FakeZone("Living Room")
    of = FakeZone("Office", state="PLAYING", uri="spotify:whatever")
    infos = make([lr, of]).start()
    office = next(i for i in infos if i["name"] == "Office")
    assert office["skipped_busy"] and not of.joined


def test_watchdog_reissues_play():
    lr = FakeZone("Living Room")
    ctl = make([lr])
    ctl.start()
    lr.state = "STOPPED"
    ctl.watchdog_tick(music=True)
    assert len(lr.played) == 2


def test_stop_restores():
    lr, of = FakeZone("Living Room"), FakeZone("Office")
    lr.volume, of.volume = 30, 40
    ctl = make([lr, of], master_volume=15)
    ctl.start()
    assert lr.volume == 15 and of.volume == 15
    ctl.stop()
    assert lr.stopped and of.unjoined
    assert lr.volume == 30 and of.volume == 40  # snapshots restored


def test_grouped_noncoordinator_unjoins_and_retries():
    other = FakeZone("Kitchen")

    class GroupedFlakyZone(FakeZone):
        def __init__(self):
            super().__init__("Living Room")
            self.group = FakeGroup(other)  # grouped, not the coordinator
            self.failures = 2

        def play_uri(self, uri, title=""):
            if self.failures:
                self.failures -= 1
                raise RuntimeError("UPnP 701 transient")
            super().play_uri(uri, title)

    lr = GroupedFlakyZone()
    sleeps = []
    ctl = make([lr, other], sleeps=sleeps, vinyl_zones=["Living Room"])
    ctl.start()
    assert lr.unjoined
    assert 5 in sleeps  # settle wait after unjoin (injectable sleep)
    assert lr.played  # third attempt succeeded


def test_unreachable_zone_never_raises():
    class DeadZone(FakeZone):
        def get_current_transport_info(self):
            raise OSError("unreachable")

        def play_uri(self, uri, title=""):
            raise OSError("unreachable")

    lr, dead = FakeZone("Living Room"), DeadZone("Office")
    infos = make([lr, dead]).start()
    office = next(i for i in infos if i["name"] == "Office")
    assert office["reachable"] is False
    assert lr.played  # rest of the fleet still starts


def test_missing_zone_marked_unreachable():
    lr = FakeZone("Living Room")
    infos = make([lr]).start()
    office = next(i for i in infos if i["name"] == "Office")
    assert office["reachable"] is False


def test_set_volume():
    lr = FakeZone("Living Room")
    ctl = make([lr])
    ctl.start()
    ctl.set_volume("Living Room", 7)
    assert lr.volume == 7
