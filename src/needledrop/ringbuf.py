"""Thread-safe bytes-oriented PCM ring buffer (overwrites oldest on overflow)."""

from __future__ import annotations

import threading


class RingBuffer:
    def __init__(self, capacity_bytes: int):
        if capacity_bytes <= 0:
            raise ValueError("capacity_bytes must be positive")
        self._capacity = capacity_bytes
        self._buf = bytearray()
        self._lock = threading.Lock()

    def write(self, data: bytes) -> None:
        """Append data, discarding the oldest bytes if capacity is exceeded."""
        with self._lock:
            self._buf += data
            overflow = len(self._buf) - self._capacity
            if overflow > 0:
                del self._buf[:overflow]

    def read_all(self) -> bytes:
        """Drain and return everything currently buffered."""
        with self._lock:
            data = bytes(self._buf)
            self._buf.clear()
            return data

    def __len__(self) -> int:
        with self._lock:
            return len(self._buf)
