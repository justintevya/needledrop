"""Task 11: composition root - Core wiring + detect_lan_ip helper."""

from needledrop.app import Core, detect_lan_ip
from needledrop.capture import CaptureSupervisor
from needledrop.config import Config, SonosCfg
from needledrop.detect import LevelDetector
from needledrop.state import StateMachine
from needledrop.stream import StreamHub


class FakeSonos:
    def __init__(self):
        self.started = 0
        self.stopped = 0
        self.ticks = []

    def start(self):
        self.started += 1
        return []

    def stop(self):
        self.stopped += 1

    def watchdog_tick(self, music):
        self.ticks.append(music)

    def zones(self):
        return []


def make_core():
    cfg = Config(sonos=SonosCfg(vinyl_zones=["Living Room"]))
    sonos = FakeSonos()
    sup = CaptureSupervisor(cfg.audio, lambda b: None,
                            backend=None, resolve=lambda: "hw:FAKE")
    core = Core(cfg, hub=StreamHub(), detector=LevelDetector(cfg.detect),
                sm=StateMachine(cfg.detect), sonos=sonos, supervisor=sup,
                encoder=None)
    return core, sonos


async def test_manual_start_stop_drive_sonos():
    core, sonos = make_core()
    await core.manual_start()
    assert core.snapshot()["phase"] == "PLAYING"
    assert sonos.started == 1
    await core.manual_stop()
    assert core.snapshot()["phase"] == "IDLE"
    assert sonos.stopped == 1


async def test_snapshot_shape():
    core, _ = make_core()
    snap = core.snapshot()
    assert {"phase", "device_present", "listeners", "xruns", "dropped_chunks",
            "zones", "rms_db", "music_on_db", "music_off_db",
            "grace_remaining_s"} <= set(snap)
    assert core.stream_port == 8342  # stream.port + 1


def test_detect_lan_ip_udp_trick():
    class FakeSock:
        def connect(self, addr):
            pass

        def getsockname(self):
            return ("192.0.2.50", 54321)

        def close(self):
            pass

    class FakeMod:
        AF_INET = 2
        SOCK_DGRAM = 1

        @staticmethod
        def socket(*a):
            return FakeSock()

    assert detect_lan_ip(sock_mod=FakeMod()) == "192.0.2.50"


def test_detect_lan_ip_fallback_to_hostname():
    class FakeMod:
        AF_INET = 2
        SOCK_DGRAM = 1

        @staticmethod
        def socket(*a):
            raise OSError("no route")

        @staticmethod
        def gethostname():
            return "kvnyl"

        @staticmethod
        def gethostbyname(name):
            assert name == "kvnyl"
            return "10.9.8.7"

    assert detect_lan_ip(sock_mod=FakeMod()) == "10.9.8.7"
