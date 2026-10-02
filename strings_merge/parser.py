"""Event scanner for .strings documents.

Produces a stream of events without ever buffering more than the current
entry plus a small trivia chunk:

    ("start", encoding, bom_bytes)
    ("trivia", raw_bytes)          # whitespace / comments, possibly chunked
    ("key", decoded_key, raw_bytes)
    ("value", decoded_value, raw_bytes)
    ("punct", raw_bytes)           # '=' or ';'
    ("end", needs_newline)
"""

from .charstream import CharStream
from .errors import StringsParseError

_TRIVIA_FLUSH = 65536
_HEX = frozenset("0123456789abcdefABCDEF")
_OCTAL = frozenset("01234567")


class _Scanner:
    def __init__(self, reader, chunk_size=65536):
        self._cs = CharStream(reader, chunk_size)
        self._cs.prepare()
        self._peeked = None
        self.last_char = None

    # -- char access ----------------------------------------------------

    def _peek(self):
        if self._peeked is None:
            self._peeked = self._cs.next()
        return self._peeked

    def _next(self):
        if self._peeked is not None:
            item = self._peeked
            self._peeked = None
        else:
            item = self._cs.next()
        if item is not None:
            self.last_char = item[0]
        return item

    def _error(self, message):
        raise StringsParseError("%s (at byte offset %d)" % (message, self._cs.pos))

    # -- trivia -----------------------------------------------------------

    def _scan_trivia(self):
        pending = bytearray()
        while True:
            item = self._peek()
            if item is None:
                break
            ch = item[0]
            if ch in " \t\r\n":
                self._next()
                pending += item[1]
            elif ch == "/":
                self._next()
                nxt = self._peek()
                if nxt is not None and nxt[0] == "/":
                    self._next()
                    pending += item[1] + nxt[1]
                    for chunk in self._scan_line_comment(pending):
                        yield chunk
                    pending = bytearray()
                elif nxt is not None and nxt[0] == "*":
                    self._next()
                    pending += item[1] + nxt[1]
                    for chunk in self._scan_block_comment(pending):
                        yield chunk
                    pending = bytearray()
                else:
                    self._error("unexpected '/' outside comment or string")
            else:
                break
            if len(pending) >= _TRIVIA_FLUSH:
                yield bytes(pending)
                pending = bytearray()
        if pending:
            yield bytes(pending)

    def _scan_line_comment(self, pending):
        while True:
            item = self._peek()
            if item is None or item[0] in "\r\n":
                break
            self._next()
            pending += item[1]
            if len(pending) >= _TRIVIA_FLUSH:
                yield bytes(pending)
                pending = bytearray()
        if pending:
            yield bytes(pending)

    def _scan_block_comment(self, pending):
        if pending:
            yield bytes(pending)
        for chunk in self._cs.scan_block_comment():
            yield chunk
        # a block comment always ends with '/', keep last_char in sync
        self.last_char = "/"

    # -- strings ----------------------------------------------------------

    def _scan_string(self):
        item = self._next()
        assert item is not None and item[0] == '"'
        raw = bytearray(item[1])
        decoded = []
        while True:
            item = self._next()
            if item is None:
                self._error("unterminated string literal")
            ch, raw_bytes = item
            raw += raw_bytes
            if ch == '"':
                return "".join(decoded), bytes(raw)
            if ch == "\r" or ch == "\n":
                self._error("unescaped line break in string literal")
            if ch == "\\":
                self._scan_escape(raw, decoded)
            else:
                decoded.append(ch)

    def _take(self, raw):
        item = self._next()
        if item is None:
            self._error("unterminated string literal")
        raw += item[1]
        return item[0]

    def _scan_escape(self, raw, decoded):
        ch = self._take(raw)
        if ch == "\\":
            decoded.append("\\")
        elif ch == '"':
            decoded.append('"')
        elif ch == "n":
            decoded.append("\n")
        elif ch == "r":
            decoded.append("\r")
        elif ch == "t":
            decoded.append("\t")
        elif ch == "\n":
            pass  # line continuation
        elif ch == "\r":
            item = self._peek()
            if item is not None and item[0] == "\n":
                self._next()
                raw += item[1]
        elif ch in _OCTAL:
            digits = [ch]
            for _ in range(2):
                item = self._peek()
                if item is None or item[0] not in _OCTAL:
                    break
                self._next()
                raw += item[1]
                digits.append(item[0])
            value = int("".join(digits), 8)
            if value > 255:
                self._error("octal escape \\%s exceeds 255" % "".join(digits))
            decoded.append(chr(value))
        elif ch == "U":
            unit = self._read_hex4(raw)
            if 0xD800 <= unit <= 0xDBFF:
                backslash = self._take(raw)
                if backslash != "\\":
                    self._error("unpaired high surrogate escape")
                marker = self._take(raw)
                if marker != "U":
                    self._error("unpaired high surrogate escape")
                low = self._read_hex4(raw)
                if not 0xDC00 <= low <= 0xDFFF:
                    self._error("unpaired high surrogate escape")
                cp = 0x10000 + ((unit - 0xD800) << 10) + (low - 0xDC00)
                decoded.append(chr(cp))
            elif 0xDC00 <= unit <= 0xDFFF:
                self._error("unpaired low surrogate escape")
            else:
                decoded.append(chr(unit))
        else:
            self._error("invalid escape \\%s" % ch)

    def _read_hex4(self, raw):
        digits = []
        for _ in range(4):
            ch = self._take(raw)
            if ch not in _HEX:
                self._error("invalid \\U escape: expected 4 hex digits")
            digits.append(ch)
        return int("".join(digits), 16)

    # -- top level --------------------------------------------------------

    def events(self):
        yield ("start", self._cs.encoding, self._cs.bom)
        while True:
            for chunk in self._scan_trivia():
                yield ("trivia", chunk)
            item = self._peek()
            if item is None:
                break
            if item[0] != '"':
                self._error("expected string literal, found %r" % item[0])
            key, raw = self._scan_string()
            yield ("key", key, raw)
            for chunk in self._scan_trivia():
                yield ("trivia", chunk)
            item = self._next()
            if item is None or item[0] != "=":
                self._error("expected '=' after key")
            yield ("punct", item[1])
            for chunk in self._scan_trivia():
                yield ("trivia", chunk)
            item = self._peek()
            if item is None or item[0] != '"':
                self._error("expected value string literal")
            value, raw = self._scan_string()
            yield ("value", value, raw)
            for chunk in self._scan_trivia():
                yield ("trivia", chunk)
            item = self._next()
            if item is None or item[0] != ";":
                self._error("expected ';' after value")
            yield ("punct", item[1])
        needs_newline = self.last_char is not None and self.last_char != "\n"
        yield ("end", needs_newline)


def iter_entries(reader):
    """Yield (key, value) pairs from a .strings binary stream, in order.

    Duplicate keys are preserved.  The whole document is validated while
    iterating; malformed input raises StringsParseError.
    """
    scanner = _Scanner(reader)
    key = None
    for event in scanner.events():
        kind = event[0]
        if kind == "key":
            key = event[1]
        elif kind == "value":
            yield key, event[1]
