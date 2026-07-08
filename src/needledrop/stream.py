"""MP3 fan-out hub + raw HTTP/1.0 icecast-style stream server.

Physics-spike findings (spec addendum, binding):
- Old Sonos firmware (sw 86.7) rejects aiohttp's chunked transfer-encoding, so
  /stream.mp3 is served by a *raw* asyncio server with minimal HTTP/1.0 framing
  on `stream.port + 1` (web/API stays on `stream.port`).
- Frame-aligned start is mandatory: listeners must receive bytes starting at an
  MP3 sync word (0xFF, then top 3 bits set). We keep a ~1s preroll of recent
  MP3 chunks and align the burst; after any drop-oldest discard we re-align the
  next chunk for that listener.
- Sonos probes with several rapid `Connection: close` GETs; short-lived
  connections are expected and must detach cleanly.
"""

from __future__ import annotations

import asyncio
import collections
import logging

log = logging.getLogger(__name__)

CRLF = b"\r\n"
PREROLL_CHUNKS = 10  # ~1s of MP3 at the 4-frame chunk size used by capture


def find_sync(data: bytes, start: int = 0) -> int:
    """Return offset of the first MP3 sync word at/after `start`, or -1."""
    i = data.find(b"\xff", start)
    while i != -1 and i + 1 < len(data):
        if data[i + 1] & 0xE0 == 0xE0:
            return i
        i = data.find(b"\xff", i + 1)
    return -1


class StreamHub:
    """Fan-out of encoded MP3 chunks to per-listener bounded queues."""

    def __init__(self, max_buffer_bytes: int = 96_000):  # ~2s of 320kbps
        self._max = max_buffer_bytes
        self._buffered: dict[asyncio.Queue, int] = {}
        self._realign: set[asyncio.Queue] = set()
        self.preroll: collections.deque[bytes] = collections.deque(maxlen=PREROLL_CHUNKS)
        self.dropped_chunks = 0

    @property
    def listener_count(self) -> int:
        return len(self._buffered)

    def attach(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._buffered[q] = 0
        return q

    def detach(self, q: asyncio.Queue) -> None:
        self._buffered.pop(q, None)
        self._realign.discard(q)

    def publish(self, mp3: bytes) -> None:
        """Loop-thread only. Appends to preroll and every listener queue,
        dropping oldest chunks when a listener's buffer exceeds the bound."""
        if not mp3:
            return
        self.preroll.append(mp3)
        for q in list(self._buffered):
            q.put_nowait(mp3)
            self._buffered[q] += len(mp3)
            while self._buffered[q] > self._max and q.qsize() > 1:
                old = q.get_nowait()
                self._buffered[q] -= len(old)
                self.dropped_chunks += 1
                self._realign.add(q)

    def consume(self, q: asyncio.Queue, chunk: bytes) -> bytes:
        """Account for a chunk taken off `q`; re-align to an MP3 sync boundary
        if this listener suffered a drop since its last aligned write."""
        if q in self._buffered:
            self._buffered[q] -= len(chunk)
        if q in self._realign:
            sync = find_sync(chunk)
            if sync < 0:
                return b""  # still misaligned; keep scanning next chunks
            self._realign.discard(q)
            return chunk[sync:]
        return chunk

    def preroll_burst(self) -> bytes:
        """Recent MP3, aligned to the first sync word (may be empty)."""
        burst = b"".join(self.preroll)
        sync = find_sync(burst)
        return burst[sync:] if sync >= 0 else b""


class RawStreamServer:
    """Minimal HTTP/1.0 server for GET /stream.mp3 - icecast-style framing."""

    def __init__(self, hub: StreamHub, host: str = "0.0.0.0", port: int = 8342,
                 icy_name: str = "needledrop"):
        self._hub = hub
        self._host = host
        self._port = port
        self._icy_name = icy_name
        self._server: asyncio.AbstractServer | None = None

    @property
    def port(self) -> int:
        if self._server is not None:
            return self._server.sockets[0].getsockname()[1]
        return self._port

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._client, self._host, self._port)

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request = await asyncio.wait_for(reader.readuntil(CRLF + CRLF), 10)
        except Exception:
            writer.close()
            return
        head = [b"HTTP/1.0 200 OK",
                b"Content-Type: audio/mpeg",
                b"Cache-Control: no-cache",
                b"Connection: close"]
        if b"icy-metadata: 1" in request.lower():
            head.insert(2, b"icy-name: " + self._icy_name.encode())
        writer.write(CRLF.join(head) + CRLF + CRLF)
        q = self._hub.attach()
        try:
            burst = self._hub.preroll_burst()
            if burst:
                writer.write(burst)
                await writer.drain()
            pump = asyncio.ensure_future(self._pump(q, writer))
            eof = asyncio.ensure_future(reader.read(1024))  # b"" on client close
            done, pending = await asyncio.wait({pump, eof},
                                               return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
        except Exception:
            pass
        finally:
            self._hub.detach(q)
            try:
                writer.close()
            except Exception:
                pass

    async def _pump(self, q: asyncio.Queue, writer: asyncio.StreamWriter) -> None:
        while True:
            chunk = self._hub.consume(q, await q.get())
            if chunk:
                writer.write(chunk)
                await writer.drain()
