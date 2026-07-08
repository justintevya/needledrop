"""aiohttp layer: dashboard, REST API, websocket, /healthz.

Runs on `stream.port`. The MP3 stream itself is served by the raw HTTP/1.0
server on `stream.port + 1` (see stream.py + spec addendum); `/stream.mp3`
here just redirects there for humans poking around with a browser.

Handlers are thin - all state lives in the Core passed to build_app().
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from dataclasses import asdict
from pathlib import Path

from aiohttp import web

from needledrop.config import ConfigError, config_from_dict

log = logging.getLogger(__name__)

CORE = web.AppKey("core", object)
WS_INTERVAL = web.AppKey("ws_interval", float)
STATIC_DIR = Path(__file__).parent / "static"
PLACEHOLDER = "<!doctype html><title>needledrop</title><h1>needledrop</h1><p>dashboard pending</p>"


def build_app(core) -> web.Application:
    app = web.Application()
    app[CORE] = core
    app.router.add_get("/", index)
    app.router.add_get("/stream.mp3", stream_redirect)
    app.router.add_get("/healthz", healthz)
    app.router.add_get("/api/status", api_status)
    app.router.add_post("/api/start", api_start)
    app.router.add_post("/api/stop", api_stop)
    app.router.add_post("/api/keep-playing", api_keep_playing)
    app.router.add_post("/api/volume", api_volume)
    app.router.add_get("/api/config", api_get_config)
    app.router.add_put("/api/config", api_put_config)
    app.router.add_post("/api/calibrate", api_calibrate)
    app.router.add_get("/ws", ws_handler)
    return app


async def index(request: web.Request) -> web.Response:
    page = STATIC_DIR / "index.html"
    if page.exists():
        return web.FileResponse(page)
    return web.Response(text=PLACEHOLDER, content_type="text/html")


async def stream_redirect(request: web.Request) -> web.Response:
    host = request.host.split(":")[0]
    port = request.app[CORE].stream_port
    raise web.HTTPFound(f"http://{host}:{port}/stream.mp3")


async def healthz(request: web.Request) -> web.Response:
    core = request.app[CORE]
    snap = core.snapshot()
    keys = ("phase", "device_present", "listeners", "xruns", "dropped_chunks", "zones")
    body = {k: snap.get(k) for k in keys}
    body["stream_port"] = core.stream_port
    return web.json_response(body)


async def api_status(request: web.Request) -> web.Response:
    return web.json_response(request.app[CORE].snapshot())


async def api_start(request: web.Request) -> web.Response:
    await request.app[CORE].manual_start()
    return web.json_response({"ok": True})


async def api_stop(request: web.Request) -> web.Response:
    await request.app[CORE].manual_stop()
    return web.json_response({"ok": True})


async def api_keep_playing(request: web.Request) -> web.Response:
    await request.app[CORE].keep_playing()
    return web.json_response({"ok": True})


async def api_volume(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        zone = str(data["zone"])
        volume = int(data["volume"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return web.json_response({"error": "expected {zone: str, volume: int}"}, status=400)
    if not 0 <= volume <= 100:
        return web.json_response({"error": "volume must be 0-100"}, status=400)
    await request.app[CORE].set_volume(zone, volume)
    return web.json_response({"ok": True})


async def api_get_config(request: web.Request) -> web.Response:
    return web.json_response(asdict(request.app[CORE].cfg))


async def api_put_config(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "invalid JSON body"}, status=400)
    try:
        cfg = config_from_dict(data)
    except ConfigError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    await request.app[CORE].update_config(cfg)
    return web.json_response({"ok": True, "restarting": True})


async def api_calibrate(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        phase = data["phase"]
    except (json.JSONDecodeError, KeyError):
        return web.json_response({"error": "body must be {\"phase\": ...}"}, status=400)
    if phase not in ("floor", "groove"):
        return web.json_response({"error": "phase must be 'floor' or 'groove'"}, status=400)
    measured = await request.app[CORE].calibrate(phase)
    return web.json_response({"measured_db": measured})


async def ws_handler(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    core = request.app[CORE]
    interval = request.app.get(WS_INTERVAL, 0.3)

    async def sender() -> None:
        last_phase = None
        while not ws.closed:
            snap = core.snapshot()
            await ws.send_json({"type": "vu", "db": snap.get("rms_db")})
            if snap.get("phase") != last_phase:
                last_phase = snap.get("phase")
                await ws.send_json({"type": "state", **snap})
            await asyncio.sleep(interval)

    push = asyncio.ensure_future(sender())
    try:
        async for _ in ws:  # drain incoming frames so CLOSE is processed
            pass
    except (ConnectionResetError, RuntimeError):
        pass
    finally:
        push.cancel()
        with contextlib.suppress(asyncio.CancelledError, ConnectionResetError, RuntimeError):
            await push
    return ws
