"""Streaming character decoder for .strings inputs.

Decodes bytes from a binary reader into (char, raw_bytes) pairs without
ever holding more than one chunk of the input.  Chunking of the underlying
reader never affects the decoded result.
"""

from .errors import StringsParseError

_UTF8 = "utf-8"
_UTF16_LE = "utf-16-le"
_UTF16_BE = "utf-16-be"


class CharStream:
    """Incremental decoder yielding (char, raw_bytes) via :meth:`next`.

    Also exposes :meth:`scan_block_comment` for bulk-scanning of block
    comment bodies so that arbitrarily long comments stay within a small
    memory bound and are processed at C speed.
    """

    def __init__(self, reader, chunk_size=65536):
        self._reader = reader
        self._chunk_size = max(16, chunk_size)
        self._buf = bytearray()
        self._eof = False
        self._prepared = False
        self.encoding = None
        self.bom = b""
        self.pos = 0  # absolute stream offset of buf[0]

    # -- low level ------------------------------------------------------

    def _fill(self, need):
        while len(self._buf) < need and not self._eof:
            data = self._reader.read(self._chunk_size)
            if not data:
                self._eof = True
            else:
                self._buf += data

    def _error(self, message):
        raise StringsParseError("%s (at byte offset %d)" % (message, self.pos))

    def prepare(self):
        """Detect BOM and encoding; strip the BOM from the buffer."""
        if self._prepared:
            return
        self._fill(3)
        buf = self._buf
        if buf[:3] == b"\xef\xbb\xbf":
            self.encoding = _UTF8
            self.bom = bytes(buf[:3])
            del buf[:3]
            self.pos += 3
        elif buf[:2] == b"\xff\xfe":
            self.encoding = _UTF16_LE
            self.bom = bytes(buf[:2])
            del buf[:2]
            self.pos += 2
        elif buf[:2] == b"\xfe\xff":
            self.encoding = _UTF16_BE
            self.bom = bytes(buf[:2])
            del buf[:2]
            self.pos += 2
        else:
            self.encoding = _UTF8
            self.bom = b""
        self._prepared = True

    # -- decoding ---------------------------------------------------------

    def next(self):
        """Return (char, raw_bytes) or None at end of input."""
        if not self._prepared:
            self.prepare()
        self._fill(1)
        if not self._buf:
            return None
        if self.encoding == _UTF8:
            return self._next_utf8()
        return self._next_utf16()

    def _next_utf8(self):
        buf = self._buf
        b0 = buf[0]
        if b0 < 0x80:
            del buf[0]
            self.pos += 1
            return chr(b0), bytes((b0,))
        if 0xC2 <= b0 <= 0xDF:
            length = 2
        elif 0xE0 <= b0 <= 0xEF:
            length = 3
        elif 0xF0 <= b0 <= 0xF4:
            length = 4
        else:
            self._error("invalid UTF-8 lead byte 0x%02X" % b0)
        self._fill(length)
        if len(buf) < length:
            self._error("truncated UTF-8 sequence")
        raw = bytes(buf[:length])
        try:
            text = raw.decode("utf-8", "strict")
        except UnicodeDecodeError:
            self._error("invalid UTF-8 sequence")
        del buf[:length]
        self.pos += length
        return text, raw

    def _next_utf16(self):
        buf = self._buf
        byteorder = "little" if self.encoding == _UTF16_LE else "big"
        self._fill(2)
        if len(buf) < 2:
            self._error("truncated UTF-16 code unit")
        unit = int.from_bytes(buf[:2], byteorder)
        if 0xD800 <= unit <= 0xDBFF:
            self._fill(4)
            if len(buf) < 4:
                self._error("truncated UTF-16 surrogate pair")
            unit2 = int.from_bytes(buf[2:4], byteorder)
            if not 0xDC00 <= unit2 <= 0xDFFF:
                self._error("unpaired high surrogate in UTF-16 input")
            raw = bytes(buf[:4])
            del buf[:4]
            self.pos += 4
            cp = 0x10000 + ((unit - 0xD800) << 10) + (unit2 - 0xDC00)
            return chr(cp), raw
        if 0xDC00 <= unit <= 0xDFFF:
            self._error("unpaired low surrogate in UTF-16 input")
        raw = bytes(buf[:2])
        del buf[:2]
        self.pos += 2
        return chr(unit), raw

    # -- bulk comment scanning ---------------------------------------------

    def _terminator_pattern(self):
        if self.encoding == _UTF8:
            return b"*/"
        if self.encoding == _UTF16_LE:
            return b"\x2a\x00\x2f\x00"
        return b"\x00\x2a\x00\x2f"

    def _complete_prefix_len(self, buf):
        """Largest prefix length of buf that ends on a char boundary."""
        if self.encoding == _UTF8:
            n = len(buf)
            # back up over trailing continuation bytes of a partial sequence
            start = n
            while start > 0 and start > n - 4:
                byte = buf[start - 1]
                if byte < 0x80:
                    break
                if byte >= 0xC0:
                    # lead byte; sequence complete only if it fits
                    need = 2 if byte < 0xE0 else (3 if byte < 0xF0 else 4)
                    if n - (start - 1) >= need:
                        return n
                    return start - 1
                start -= 1
            return n if start == n else start
        n = len(buf) & ~1
        if n >= 2:
            byteorder = "little" if self.encoding == _UTF16_LE else "big"
            unit = int.from_bytes(buf[n - 2:n], byteorder)
            if 0xD800 <= unit <= 0xDBFF:
                n -= 2
        return n

    def _validate(self, raw):
        codec = "utf-8" if self.encoding == _UTF8 else (
            "utf-16-le" if self.encoding == _UTF16_LE else "utf-16-be")
        try:
            raw.decode(codec, "strict")
        except UnicodeDecodeError:
            self._error("invalid %s data in comment" % codec)

    def scan_block_comment(self):
        """Yield raw byte chunks of a block comment body incl. the '*/'.

        Assumes the opening '/*' was already consumed.  Raises
        StringsParseError on EOF before the terminator.
        """
        if not self._prepared:
            self.prepare()
        pattern = self._terminator_pattern()
        aligned = self.encoding != _UTF8
        plen = len(pattern)
        while True:
            self._fill(self._chunk_size)
            buf = self._buf
            if not buf:
                self._error("unterminated block comment")
            idx = self._find_aligned(buf, pattern, aligned)
            if idx >= 0:
                segment = bytes(buf[:idx])
                self._validate(segment)
                end = idx + plen
                terminator = bytes(buf[idx:end])
                del buf[:end]
                self.pos += end
                if segment:
                    yield segment
                yield terminator
                return
            keep = self._complete_prefix_len(buf)
            if keep:
                segment = bytes(buf[:keep])
                self._validate(segment)
                del buf[:keep]
                self.pos += keep
                yield segment
            elif self._eof and buf:
                self._error("invalid trailing bytes in comment")

    @staticmethod
    def _find_aligned(buf, pattern, aligned):
        idx = buf.find(pattern)
        if not aligned:
            return idx
        while idx >= 0 and idx % 2 != 0:
            idx = buf.find(pattern, idx + 1)
        return idx
