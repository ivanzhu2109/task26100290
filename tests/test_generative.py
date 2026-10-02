"""Generative large-input test: 32+ MiB block comment, 100k entries.

The input is produced lazily by a generator (never materialized), the
output is hashed incrementally, and the expected hash comes from an
independently generated expected byte stream.  Peak memory of the merge
is asserted to stay under 8 MiB.
"""

import hashlib
import io
import os
import sys
import tracemalloc
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from strings_merge import merge

COMMENT_SIZE = 32 * 1024 * 1024  # 32 MiB of comment body
ENTRY_COUNT = 100_000
UPDATED_KEY = "key050000"
UPDATED_VALUE = "updated-\u00e9\U0001F600-value"
MEMORY_LIMIT = 8 * 1024 * 1024


def entry_line(index, value=None):
    if value is None:
        value = "value%06d" % index
    return '"key%06d" = "%s";\n' % (index, value)


def input_chunks():
    """Yield the input document as small chunks, never all at once."""
    yield b"/*"
    body = b"x" * 65536
    remaining = COMMENT_SIZE
    while remaining > 0:
        piece = body[:remaining]
        remaining -= len(piece)
        yield piece
    yield b"*/\n"
    for i in range(ENTRY_COUNT):
        yield entry_line(i).encode("utf-8")


def expected_chunks():
    """Independently generated expected output (one value replaced)."""
    yield b"/*"
    body = b"x" * 65536
    remaining = COMMENT_SIZE
    while remaining > 0:
        piece = body[:remaining]
        remaining -= len(piece)
        yield piece
    yield b"*/\n"
    for i in range(ENTRY_COUNT):
        key = "key%06d" % i
        value = UPDATED_VALUE if key == UPDATED_KEY else "value%06d" % i
        yield entry_line(i, value).encode("utf-8")


class GenerativeReader:
    def __init__(self, chunks):
        self._it = iter(chunks)
        self._buf = b""

    def read(self, n=-1):
        while not self._buf:
            try:
                self._buf = next(self._it)
            except StopIteration:
                return b""
        if n < 0 or n >= len(self._buf):
            chunk, self._buf = self._buf, b""
            return chunk
        chunk, self._buf = self._buf[:n], self._buf[n:]
        return chunk


class HashWriter:
    def __init__(self):
        self.hasher = hashlib.sha256()
        self.size = 0

    def write(self, data):
        self.hasher.update(data)
        self.size += len(data)


def hash_of(chunks):
    hasher = hashlib.sha256()
    size = 0
    for chunk in chunks:
        hasher.update(chunk)
        size += len(chunk)
    return hasher.hexdigest(), size


class GenerativeMergeTest(unittest.TestCase):
    def test_large_streaming_merge_content_and_memory(self):
        expected_hash, expected_size = hash_of(expected_chunks())
        self.assertGreaterEqual(expected_size, COMMENT_SIZE)

        writer = HashWriter()
        tracemalloc.start()
        try:
            tracemalloc.reset_peak()
            merge(
                GenerativeReader(input_chunks()),
                writer,
                {UPDATED_KEY: UPDATED_VALUE},
            )
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

        self.assertEqual(writer.size, expected_size)
        self.assertEqual(writer.hasher.hexdigest(), expected_hash)
        self.assertLessEqual(
            peak, MEMORY_LIMIT,
            "peak extra memory %d bytes exceeds 8 MiB" % peak,
        )


if __name__ == "__main__":
    unittest.main()
