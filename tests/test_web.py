"""Task 11: REST/websocket/healthz layer, driven with a FakeCore."""

import asyncio

from aiohttp.test_utils import TestClient, TestServer

from needledrop.web import WS_INTERVAL, build_app


class FakeCore:
    stream_port = 8342

    def __init__(self):
        self.calls = []
        self.status = {"phase": "IDLE", "device_present": True, "listeners": 0,
                       "xruns": 0, "dropped_chunks": 0, "zones": [], "rms_db": -80.0,
                       "music_on_db": -45.0, "music_off_db": -55.0, "grace_remaining_s": None}

    def snapshot(self):
        return dict(self.status)

    async def manual_start(self):
        self.calls.append("start")

    async def manual_stop(self):
        self.calls.append("stop")

    async def keep_playing(self):
        self.calls.append("keep")

    async def calibrate(self, phase):
        self.calls.append(("calibrate", phase))
        return -68.5

    async def set_volume(self, zone, volume):
        self.calls.append(("volume", zone, volume))

    async def update_config(self, cfg):
        self.calls.append(("config", cfg))

    hub = type("H", (), {"attach": lambda s: None, "listener_count": 0})()


VALID_CFG = {"audio": {"device": {"usb_id": "08bb:2902"}},
             "sonos": {"vinyl_zones": ["Living Room"]}}


async def client(core):
    c = TestClient(TestServer(build_app(core)))
    await c.start_server()
    return c


async def test_status_and_controls():
    core = FakeCore()
    c = await client(core)
    try:
        s = await (await c.get("/api/status")).json()
        assert s["phase"] == "IDLE"
        assert (await c.post("/api/start")).status == 200
        assert core.calls == ["start"]
        assert (await c.post("/api/stop")).status == 200
        assert (await c.post("/api/keep-playing")).status == 200
        assert core.calls == ["start", "stop", "keep"]
    finally:
        await c.close()


async def test_healthz_shape_includes_stream_port():
    c = await client(FakeCore())
    try:
        h = await (await c.get("/healthz")).json()
        assert {"phase", "device_present", "listeners"} <= set(h)
        assert h["stream_port"] == 8342
    finally:
        await c.close()


async def test_put_config_validates():
    core = FakeCore()
    c = await client(core)
    try:
        bad = dict(VALID_CFG, detect={"music_on_db": -60, "music_off_db": -50})
        r = await c.put("/api/config", json=bad)
        assert r.status == 400
        assert "music_on_db" in (await r.json())["error"]
        assert core.calls == []
        r = await c.put("/api/config", json=VALID_CFG)
        assert r.status == 200
        assert core.calls and core.calls[0][0] == "config"
    finally:
        await c.close()


async def test_calibrate_endpoint():
    core = FakeCore()
    c = await client(core)
    try:
        r = await c.post("/api/calibrate", json={"phase": "floor"})
        assert (await r.json())["measured_db"] == -68.5
        assert ("calibrate", "floor") in core.calls
    finally:
        await c.close()


async def test_ws_pushes_vu_and_state():
    core = FakeCore()
    app = build_app(core)
    app[WS_INTERVAL] = 0.01
    c = TestClient(TestServer(app))
    await c.start_server()
    try:
        ws = await c.ws_connect("/ws")
        msgs = [await asyncio.wait_for(ws.receive_json(), 2) for _ in range(2)]
        types = {m["type"] for m in msgs}
        assert "vu" in types
        vu = next(m for m in msgs if m["type"] == "vu")
        assert vu["db"] == -80.0
        await ws.close()
    finally:
        await c.close()


async def test_index_served():
    c = await client(FakeCore())
    try:
        r = await c.get("/")
        assert r.status == 200
        assert "text/html" in r.headers["Content-Type"]
    finally:
        await c.close()


async def test_volume_route():
    core = FakeCore()
    c = await client(core)
    try:
        assert (await c.post("/api/volume", json={"zone": "Office", "volume": 30})).status == 200
        assert ("volume", "Office", 30) in core.calls
        assert (await c.post("/api/volume", json={"zone": "Office", "volume": 150})).status == 400
        assert (await c.post("/api/volume", json={"volume": 10})).status == 400
    finally:
        await c.close()
