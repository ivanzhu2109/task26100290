"""Streaming byte decoder: BOM sniffing + chunked UTF-8/UTF-16 decoding.

The decoder never holds more than one chunk plus a few leftover bytes, and
exposes absolute byte offsets so callers can splice the original byte stream.
"""

from __future__ import annotations

import re

from .errors import StringsParseError

_UTF8_BOM = b"\xef\xbb\xbf"
_UTF16LE_BOM = b"\xff\xfe"
_UTF16BE_BOM = b"\xfe\xff"

_SURROGATE_RE = re.compile("[\ud800-\udfff]")


class ChunkView:
    """Decoded text of one chunk plus lazy char-index -> byte-offset mapping.

    Offsets are absolute positions in the original byte stream (BOM bytes
    included).  ``offset(i)`` is the byte offset of ``text[i]``; offsets must
    be requested with monotonically non-decreasing ``index``.
    """

    __slots__ = ("text", "base", "encoding", "_resolved_index", "_resolved_offset")

    def __init__(self, text: str, base: int, encoding: str) -> None:
        self.text = text
        self.base = base
        self.encoding = encoding
        self._resolved_index = 0
        self._resolved_offset = base

    def offset(self, index: int) -> int:
        if index < self._resolved_index:
            raise AssertionError("offsets must be requested monotonically")
        if index > self._resolved_index:
            self._resolved_offset += len(
                self.text[self._resolved_index:index].encode(self.encoding)
            )
            self._resolved_index = index
        return self._resolved_offset


def _utf8_safe_length(data: bytes) -> int:
    """Length of the longest prefix of ``data`` ending on a char boundary."""
    n = len(data)
    i = n
    while i > 0 and n - i < 4:
        byte = data[i - 1]
        if byte & 0xC0 != 0x80:  # lead byte (or ASCII)
            if byte < 0x80:
                return n
            if 0xC2 <= byte < 0xE0:
                expected = 2
            elif 0xE0 <= byte < 0xF0:
                expected = 3
            elif 0xF0 <= byte < 0xF5:
                expected = 4
            else:
                return n  # invalid lead byte: let the decoder raise
            if n - (i - 1) < expected:
                return i - 1
            return n
        i -= 1
    return n


def _utf16_safe_length(data: bytes, little_endian: bool) -> int:
    n = len(data) & ~1
    if n >= 2:
        if little_endian:
            unit = data[n - 2] | (data[n - 1] << 8)
        else:
            unit = (data[n - 2] << 8) | data[n - 1]
        if 0xD800 <= unit <= 0xDBFF:
            n -= 2  # wait for the low surrogate
    return n


class StreamDecoder:
    """Incrementally decodes bytes into ChunkViews.

    Encoding is sniffed from the BOM (UTF-8 / UTF-16LE / UTF-16BE); without a
    BOM the stream is decoded as UTF-8.  No other encodings are guessed.
    """

    def __init__(self) -> None:
        self.encoding: str | None = None  # decided after BOM sniffing
        self.bom_length = 0
        self._pending = b""       # bytes held back (BOM decision or split char)
        self._pending_pos = 0     # absolute offset of _pending[0]

    # -- BOM sniffing ----------------------------------------------------

    def _try_decide(self, at_eof: bool) -> bool:
        buf = self._pending
        if len(buf) >= 3:
            self._decide()
            return True
        if len(buf) == 2:
            if buf in (_UTF16LE_BOM, _UTF16BE_BOM):
                self._decide()
                return True
            if buf[0] == 0xEF and not at_eof:
                return False  # could still become a UTF-8 BOM
            self._decide()
            return True
        if len(buf) == 1:
            if buf[0] in (0xEF, 0xFF, 0xFE) and not at_eof:
                return False
            self._decide()
            return True
        if at_eof:
            self._decide()
            return True
        return False

    def _decide(self) -> None:
        buf = self._pending
        if buf.startswith(_UTF8_BOM):
            self.encoding, self.bom_length = "utf-8", 3
        elif buf.startswith(_UTF16LE_BOM):
            self.encoding, self.bom_length = "utf-16-le", 2
        elif buf.startswith(_UTF16BE_BOM):
            self.encoding, self.bom_length = "utf-16-be", 2
        else:
            self.encoding, self.bom_length = "utf-8", 0
        self._pending = buf[self.bom_length:]
        self._pending_pos += self.bom_length

    # -- decoding ----------------------------------------------------------

    def _decode_pending(self, take_all: bool) -> ChunkView:
        assert self.encoding is not None
        buf = self._pending
        if take_all:
            safe = len(buf)
        elif self.encoding == "utf-8":
            safe = _utf8_safe_length(buf)
        else:
            safe = _utf16_safe_length(buf, self.encoding == "utf-16-le")
        if safe == 0:
            return ChunkView("", self._pending_pos, self.encoding)
        chunk = buf[:safe]
        try:
            text = chunk.decode(self.encoding, "strict")
        except UnicodeDecodeError as exc:
            raise StringsParseError(
                "invalid %s data: %s" % (self.encoding, exc.reason),
                self._pending_pos + exc.start,
            ) from exc
        if _SURROGATE_RE.search(text) is not None:
            raise StringsParseError(
                "lone surrogate in %s data" % self.encoding, self._pending_pos
            )
        base = self._pending_pos
        self._pending = buf[safe:]
        self._pending_pos += safe
        return ChunkView(text, base, self.encoding)

    @property
    def consumed(self) -> int:
        """Absolute offset up to which bytes have been decoded."""
        return self._pending_pos

    def feed(self, data: bytes) -> ChunkView:
        """Decode newly read bytes; returns a (possibly empty) ChunkView."""
        self._pending += data
        if self.encoding is None and not self._try_decide(at_eof=False):
            return ChunkView("", 0, "utf-8")
        return self._decode_pending(take_all=False)

    def finish(self) -> ChunkView:
        """Flush at EOF; raises if bytes end mid-character."""
        if self.encoding is None:
            self._try_decide(at_eof=True)
        return self._decode_pending(take_all=True)
