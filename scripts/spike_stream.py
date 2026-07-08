#!/usr/bin/env python3
"""needledrop physics spike v4 - findings encoded as code comments.

LEARNED (2026-07-07, Play:1 sw 86.7 'ZPS12'):
1. play_uri on a grouped non-coordinator raises SoCoSlaveException -> unjoin first,
   and unjoin needs several seconds to settle before Play succeeds (701 otherwise).
2. PortAudio/sounddevice on a PipeWire desktop overflowed every period (30% throughput).
   arecord raw pipe is rock-solid. Production default backend: arecord subprocess.
3. Encoding inside the capture callback starves capture -> decouple via queue + thread.
4. Sonos probes with 2-3 rapid GETs (Connection: close) before/while playing. Expect and
   tolerate short-lived connections; give instant preroll data.
5. (Testing here) aiohttp chunked transfer-encoding suspected rejected by old firmware ->
   raw HTTP/1.0 icecast-style framing on :8343 vs aiohttp on :8342.
"""
import argparse, asyncio, collections, queue, subprocess, threading, time
import lameenc
from aiohttp import web
import soco

CHUNK = 4608
listeners = set()
preroll = collections.deque(maxlen=10)
loop = None
stats = {"xruns": 0, "chunks": 0, "connects": 0, "disconnects": 0, "enc_lag_max": 0}
pcm_q: "queue.Queue[bytes]" = queue.Queue(maxsize=100)
CRLF = b"\r\n"


def capture_thread(device):
    n = CHUNK * 4
    while True:
        p = subprocess.Popen(["arecord", "-D", device, "-f", "S16_LE", "-r", "44100",
                              "-c", "2", "-t", "raw", "-q"], stdout=subprocess.PIPE)
        try:
            while True:
                data = p.stdout.read(n)
                if not data:
                    break
                try:
                    pcm_q.put_nowait(data)
                except queue.Full:
                    stats["xruns"] += 1
        finally:
            p.kill()
            stats["xruns"] += 1
            time.sleep(2)


def encode_thread():
    enc = lameenc.Encoder()
    enc.set_bit_rate(320); enc.set_in_sample_rate(44100)
    enc.set_channels(2); enc.set_quality(2)
    while True:
        pcm = pcm_q.get()
        stats["enc_lag_max"] = max(stats["enc_lag_max"], pcm_q.qsize())
        mp3 = enc.encode(pcm)
        stats["chunks"] += 1
        if mp3 and loop:
            data = bytes(mp3)
            preroll.append(data)
            loop.call_soon_threadsafe(_fanout, data)


def _fanout(data):
    for q in list(listeners):
        if q.qsize() > 40:
            q.get_nowait()
        q.put_nowait(data)


async def aio_stream(request):
    stats["connects"] += 1
    print(f"[{time.strftime('%H:%M:%S')}] AIOHTTP GET {request.remote}", flush=True)
    resp = web.StreamResponse(headers={"Content-Type": "audio/mpeg", "Cache-Control": "no-cache"})
    await resp.prepare(request)
    q = asyncio.Queue()
    for c in list(preroll):
        q.put_nowait(c)
    listeners.add(q)
    try:
        while True:
            await resp.write(await q.get())
    except Exception:
        pass
    finally:
        listeners.discard(q)
        stats["disconnects"] += 1
    return resp


async def raw_client(reader, writer):
    peer = writer.get_extra_info("peername")
    try:
        req = await asyncio.wait_for(reader.readuntil(CRLF + CRLF), 10)
    except Exception:
        writer.close(); return
    stats["connects"] += 1
    print(f"[{time.strftime('%H:%M:%S')}] RAW {peer} {req.splitlines()[0]!r}", flush=True)
    writer.write(b"HTTP/1.0 200 OK" + CRLF +
                 b"Content-Type: audio/mpeg" + CRLF +
                 b"icy-name: needledrop-spike" + CRLF +
                 b"Cache-Control: no-cache" + CRLF +
                 b"Connection: close" + CRLF + CRLF)
    q = asyncio.Queue()
    burst = b"".join(preroll)
    sync = next((i for i in range(len(burst) - 1)
                 if burst[i] == 0xFF and (burst[i + 1] & 0xE0) == 0xE0), 0)
    if burst[sync:]:
        q.put_nowait(burst[sync:])          # frame-aligned start
    listeners.add(q)
    try:
        while True:
            writer.write(await q.get())
            await writer.drain()
    except Exception:
        pass
    finally:
        listeners.discard(q)
        stats["disconnects"] += 1
        print(f"[{time.strftime('%H:%M:%S')}] RAW gone {peer} stats={stats}", flush=True)
        try:
            writer.close()
        except Exception:
            pass


async def statsdump(zone):
    while True:
        await asyncio.sleep(60)
        tr = ""
        if zone is not None:
            try:
                tr = zone.get_current_transport_info()["current_transport_state"]
            except Exception as e:
                tr = f"err:{e}"
        print(f"[{time.strftime('%H:%M:%S')}] STATS {stats} listeners={len(listeners)} transport={tr}", flush=True)


async def main():
    global loop
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", default="hw:1,0")
    ap.add_argument("--zone")
    ap.add_argument("--host", default="192.168.1.100")
    ap.add_argument("--port", type=int, default=8342)
    a = ap.parse_args()
    loop = asyncio.get_running_loop()
    threading.Thread(target=capture_thread, args=(a.device,), daemon=True).start()
    threading.Thread(target=encode_thread, daemon=True).start()

    app = web.Application()
    app.router.add_get("/stream.mp3", aio_stream)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", a.port).start()
    await asyncio.start_server(raw_client, "0.0.0.0", a.port + 1)
    print(f"aiohttp :{a.port}  raw-http/1.0 :{a.port + 1}", flush=True)

    z = None
    if a.zone:
        await asyncio.sleep(2)
        zones = list(soco.discover(timeout=10) or [])
        z = next(zz for zz in zones if zz.player_name == a.zone)
        try:
            z.unjoin()
            print("unjoined", flush=True)
        except Exception as e:
            print("unjoin:", e, flush=True)
        time.sleep(5)
        z.volume = 12
        for attempt in range(4):
            try:
                z.play_uri(f"x-rincon-mp3radio://{a.host}:{a.port + 1}/stream.mp3",
                           title="needledrop spike")
                print(f"PLAYING on {z.player_name} via RAW :{a.port + 1}", flush=True)
                break
            except Exception as e:
                print(f"play attempt {attempt + 1}: {e}", flush=True)
                time.sleep(4)
    asyncio.ensure_future(statsdump(z))
    await asyncio.Event().wait()


asyncio.run(main())
