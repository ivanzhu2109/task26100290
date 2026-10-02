"""Deterministic chunked binary readers for testing chunk-independence."""


class ChunkReader:
    """Binary reader that serves data in the given chunk sizes (cycled).

    read(n) returns at most the next chunk's worth of bytes (never more
    than n), and b"" only at end of input.
    """

    def __init__(self, data, sizes):
        self._data = data
        self._sizes = list(sizes) or [1]
        self._pos = 0
        self._idx = 0

    def read(self, n=-1):
        if self._pos >= len(self._data):
            return b""
        size = self._sizes[self._idx % len(self._sizes)]
        self._idx += 1
        if n >= 0:
            size = min(size, n)
        end = min(self._pos + size, len(self._data))
        chunk = self._data[self._pos:end]
        self._pos = end
        return chunk


ONE_BYTE = [1]
SMALL_PRIMES = [2, 3, 5, 7, 11, 13]
STUTTER = [1, 4, 1, 9, 2, 6, 5, 3, 5]
BIG_SMALL = [65536, 1, 3, 4096, 2]

ALL_CHUNKINGS = [ONE_BYTE, SMALL_PRIMES, STUTTER, BIG_SMALL]
