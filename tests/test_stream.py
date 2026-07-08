"""Task 8: StreamHub fan-out + raw HTTP/1.0 icecast-style stream server.

Per physics-spike addendum: raw asyncio server (not aiohttp), HTTP/1.0 framing,
frame-aligned preroll, per-listener drop-oldest with re-alignment after drops.
"""

import asyncio

from needledrop.stream import RawStreamServer, StreamHub

# A minimal valid MP3 sync prefix: 0xFF + top-3-bits-set second byte.
FRAME = b"\xff\xfb\x90\x00" + b"A" * 28
JUNK = b"\x01\x02\x03\x04\x05"


async def start_server(hub):
    srv = RawStreamServer(hub, host="127.0.0.1", port=0, icy_name="needledrop-test")
    await srv.start()
    return srv


async def get_stream(port, headers=b""):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(b"GET /stream.mp3 HTTP/1.1\r\nHost: x\r\n" + headers + b"\r\n")
    await writer.drain()
    head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 2)
    return reader, writer, head


async def test_listener_receives_published_bytes():
    hub = StreamHub()
    srv = await start_server(hub)
    try:
        reader, writer, head = await get_stream(srv.port)
        assert head.startswith(b"HTTP/1.0 200 OK")
        assert b"audio/mpeg" in head
        assert b"icy-name" not in head.lower()  # no Icy-MetaData header sent
        hub.publish(b"MP3DATA1")
        chunk = await asyncio.wait_for(reader.readexactly(8), 2)
        assert chunk == b"MP3DATA1"
        writer.close()
    finally:
        await srv.close()


async def test_icy_name_only_when_requested():
    hub = StreamHub()
    srv = await start_server(hub)
    try:
        reader, writer, head = await get_stream(srv.port, b"Icy-MetaData: 1\r\n")
        assert b"icy-name: needledrop-test" in head
        writer.close()
    finally:
        await srv.close()


async def test_preroll_sent_frame_aligned():
    hub = StreamHub()
    hub.publish(JUNK)  # mid-frame junk lands in preroll
    hub.publish(FRAME)
    srv = await start_server(hub)
    try:
        reader, writer, head = await get_stream(srv.port)
        first = await asyncio.wait_for(reader.readexactly(2), 2)
        assert first[0] == 0xFF and (first[1] & 0xE0) == 0xE0
        writer.close()
    finally:
        await srv.close()


async def test_slow_listener_drops_oldest_not_others():
    hub = StreamHub(max_buffer_bytes=16)
    q_slow = hub.attach()
    q_fast = hub.attach()
    for i in range(10):
        hub.publish(bytes([i]) * 8)  # 80 bytes >> 16
    assert hub.dropped_chunks > 0
    assert q_fast.qsize() == q_slow.qsize() <= 2  # both bounded
    hub.detach(q_slow)
    hub.detach(q_fast)
    assert hub.listener_count == 0


async def test_realign_after_drop():
    hub = StreamHub(max_buffer_bytes=len(FRAME) * 2)
    q = hub.attach()
    for _ in range(5):
        hub.publish(JUNK + FRAME)  # forces drops on q
    assert hub.dropped_chunks > 0
    chunk = hub.consume(q, q.get_nowait())
    assert chunk[:1] == b"\xff" and (chunk[1] & 0xE0) == 0xE0
    hub.detach(q)


async def test_probe_get_tolerated_and_detached():
    hub = StreamHub()
    srv = await start_server(hub)
    try:
        reader, writer, head = await get_stream(srv.port, b"Range: bytes=0-\r\n")
        assert head.startswith(b"HTTP/1.0 200 OK")  # Range ignored, still streams
        assert hub.listener_count == 1
        writer.close()
        for _ in range(50):
            await asyncio.sleep(0.01)
            if hub.listener_count == 0:
                break
        assert hub.listener_count == 0  # detached cleanly
    finally:
        await srv.close()


async def test_garbage_connection_tolerated():
    hub = StreamHub()
    srv = await start_server(hub)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", srv.port)
        writer.write(b"not http at all")
        writer.close()
        await asyncio.sleep(0.05)
        assert hub.listener_count == 0
    finally:
        await srv.close()
