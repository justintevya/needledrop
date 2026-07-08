"""Task 9: capture supervisor with unplug recovery; arecord default backend."""

import asyncio
import io
from contextlib import contextmanager

from needledrop.capture import ArecordBackend, CaptureSupervisor
from needledrop.config import AudioCfg, DeviceCfg


class FlakyBackend:
    def __init__(self):
        self.opens = 0

    @contextmanager
    def open(self, device, callback):
        self.opens += 1
        if self.opens <= 2:
            raise OSError("no device")
        callback(b"\x00\x00" * 200)
        yield


async def test_supervisor_retries_and_recovers():
    got = []
    be = FlakyBackend()
    sup = CaptureSupervisor(AudioCfg(device=DeviceCfg()), got.append,
                            backend=be, resolve=lambda: "hw:CARD=FAKE",
                            backoff_s=(0.01, 0.01, 0.01))
    task = asyncio.create_task(sup.run())
    await asyncio.sleep(0.2)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert be.opens >= 3 and got and sup.device_present


async def test_device_present_false_while_failing():
    sup = CaptureSupervisor(AudioCfg(device=DeviceCfg()), lambda b: None,
                            backend=FlakyBackend(), resolve=lambda: "hw:CARD=FAKE",
                            backoff_s=(60.0,))  # first failure parks us in backoff
    task = asyncio.create_task(sup.run())
    await asyncio.sleep(0.05)
    assert sup.device_present is False
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def test_default_backend_is_arecord():
    sup = CaptureSupervisor(AudioCfg(device=DeviceCfg()), lambda b: None)
    assert isinstance(sup.backend, ArecordBackend)


class FakeProc:
    def __init__(self, data: bytes):
        self.stdout = io.BytesIO(data)
        self.killed = False

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        return 0


def test_arecord_backend_command_and_pump():
    calls = {}

    def fake_popen(argv, stdout=None):
        calls["argv"] = argv
        return FakeProc(b"\x01\x02" * ArecordBackend.CHUNK_BYTES)

    got = []
    be = ArecordBackend(popen=fake_popen)
    with be.open("hw:CARD=CODEC,DEV=0", got.append) as session:
        session.pump(None)  # reads until EOF (process exit == device lost)
    assert calls["argv"][:2] == ["arecord", "-D"]
    assert calls["argv"][2] == "hw:CARD=CODEC,DEV=0"
    for flag in ("-f", "S16_LE", "-r", "44100", "-c", "2", "-t", "raw", "-q"):
        assert flag in calls["argv"]
    assert got and all(len(c) <= ArecordBackend.CHUNK_BYTES for c in got)
    assert b"".join(got) == b"\x01\x02" * ArecordBackend.CHUNK_BYTES
