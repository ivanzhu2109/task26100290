"""Streaming parser for iOS .strings documents (operates on decoded text).

The parser is a state machine fed with ChunkViews.  It never buffers more
than the current string literal; comments and whitespace are scanned with
str.find/regex jumps so arbitrarily long comments cost no extra memory.
"""

from __future__ import annotations

import re

from .decoder import ChunkView
from .errors import StringsParseError

_STRING_STOP_RE = re.compile(r'["\\\r\n]')
_LINE_END_RE = re.compile(r"[\r\n]")
_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")
_WHITESPACE = frozenset(" \t\r\n")


class EntryHandler:
    """Callbacks invoked by the parser while scanning a document."""

    def on_string_start(self, content_offset: int) -> None: ...
    def on_key(self, start: int, end: int, decoded: str) -> None: ...
    def on_value(self, start: int, end: int, decoded: str) -> None: ...
    def on_entry(self, key: str, value: str) -> None: ...


class Parser:
    """Stateful, chunk-driven parser. Raises StringsParseError on bad input."""

    WS, STRING, LINE_COMMENT, BLOCK_COMMENT = range(4)
    EXPECT_KEY, EXPECT_EQ, EXPECT_VALUE, EXPECT_SEMI = range(4)

    def __init__(self, handler: EntryHandler) -> None:
        self.handler = handler
        self.mode = self.WS
        self.decl = self.EXPECT_KEY
        self.esc: str | None = None
        self._slash = False
        self._block_star = False
        self._parts: list[str] = []
        self._lit_start = 0
        self._key: str | None = None
        self._value: str | None = None
        self._oct_value = 0
        self._oct_count = 0
        self._hex_value = 0
        self._hex_count = 0
        self._hi = 0

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _error(message: str, view: ChunkView, index: int) -> StringsParseError:
        return StringsParseError(message, view.offset(index))

    def _finish_octal(self) -> None:
        if self._oct_value > 255:
            raise StringsParseError("octal escape exceeds 255")
        self._parts.append(chr(self._oct_value))
        self.esc = None

    def _finish_hex(self, view: ChunkView, index: int) -> None:
        value = self._hex_value
        if 0xD800 <= value <= 0xDBFF:
            self._hi = value
            self.esc = "uhex_hi"
        elif 0xDC00 <= value <= 0xDFFF:
            raise self._error("lone low surrogate escape", view, index)
        else:
            self._parts.append(chr(value))
            self.esc = None

    def _feed_escape(self, view: ChunkView, i: int) -> int:
        """Process text[i] as part of an escape; returns the next index."""
        c = view.text[i]
        esc = self.esc
        if esc == "esc":
            if c == "\\":
                self._parts.append("\\")
                self.esc = None
            elif c == '"':
                self._parts.append('"')
                self.esc = None
            elif c == "n":
                self._parts.append("\n")
                self.esc = None
            elif c == "r":
                self._parts.append("\r")
                self.esc = None
            elif c == "t":
                self._parts.append("\t")
                self.esc = None
            elif "0" <= c <= "7":
                self._oct_value = int(c)
                self._oct_count = 1
                self.esc = "octal"
            elif c == "U":
                self._hex_value = 0
                self._hex_count = 0
                self.esc = "uhex"
            elif c == "\n":
                self.esc = None  # line continuation
            elif c == "\r":
                self.esc = "esc_cr"  # line continuation, maybe CRLF
            else:
                raise self._error("invalid escape sequence", view, i)
            return i + 1
        if esc == "esc_cr":
            self.esc = None
            if c == "\n":
                return i + 1
            return i  # reprocess in string mode
        if esc == "octal":
            if "0" <= c <= "7":
                self._oct_value = self._oct_value * 8 + int(c)
                self._oct_count += 1
                if self._oct_count == 3:
                    self._finish_octal()
                return i + 1
            self._finish_octal()
            return i  # reprocess in string mode
        if esc == "uhex":
            if c in _HEX_DIGITS:
                self._hex_value = self._hex_value * 16 + int(c, 16)
                self._hex_count += 1
                if self._hex_count == 4:
                    self._finish_hex(view, i)
                return i + 1
            raise self._error("\\U escape needs exactly 4 hex digits", view, i)
        if esc == "uhex_hi":
            if c == "\\":
                self.esc = "uhex_hi_u"
                return i + 1
            raise self._error("lone high surrogate escape", view, i)
        if esc == "uhex_hi_u":
            if c == "U":
                self._hex_value = 0
                self._hex_count = 0
                self.esc = "uhex_lo"
                return i + 1
            raise self._error("lone high surrogate escape", view, i)
        # esc == "uhex_lo"
        if c in _HEX_DIGITS:
            self._hex_value = self._hex_value * 16 + int(c, 16)
            self._hex_count += 1
            if self._hex_count == 4:
                lo = self._hex_value
                if not 0xDC00 <= lo <= 0xDFFF:
                    raise self._error("lone high surrogate escape", view, i)
                combined = 0x10000 + ((self._hi - 0xD800) << 10) + (lo - 0xDC00)
                self._parts.append(chr(combined))
                self.esc = None
            return i + 1
        raise self._error("\\U escape needs exactly 4 hex digits", view, i)

    def _close_string(self, view: ChunkView, i: int) -> None:
        decoded = "".join(self._parts)
        del self._parts[:]
        end = view.offset(i)
        if self.decl == self.EXPECT_KEY:
            self._key = decoded
            self.decl = self.EXPECT_EQ
            self.handler.on_key(self._lit_start, end, decoded)
        else:
            self._value = decoded
            self.decl = self.EXPECT_SEMI
            self.handler.on_value(self._lit_start, end, decoded)
        self.mode = self.WS

    # -- main entry ----------------------------------------------------------

    def feed(self, view: ChunkView) -> None:
        text = view.text
        n = len(text)
        i = 0
        while i < n:
            if self.esc is not None:
                i = self._feed_escape(view, i)
                continue
            mode = self.mode
            if mode == self.STRING:
                match = _STRING_STOP_RE.search(text, i)
                if match is None:
                    self._parts.append(text[i:])
                    break
                j = match.start()
                if j > i:
                    self._parts.append(text[i:j])
                c = text[j]
                if c == '"':
                    self._close_string(view, j)
                elif c == "\\":
                    self.esc = "esc"
                else:
                    raise self._error("unescaped newline in string literal", view, j)
                i = j + 1
            elif mode == self.LINE_COMMENT:
                match = _LINE_END_RE.search(text, i)
                if match is None:
                    break
                self.mode = self.WS
                i = match.start()  # reprocess the newline as whitespace
            elif mode == self.BLOCK_COMMENT:
                if self._block_star:
                    self._block_star = False
                    if text[i] == "/":
                        self.mode = self.WS
                        i += 1
                        continue
                j = text.find("*/", i)
                if j < 0:
                    self._block_star = text.endswith("*")
                    break
                self.mode = self.WS
                i = j + 2
            else:  # WS
                if self._slash:
                    self._slash = False
                    if text[i] == "/":
                        self.mode = self.LINE_COMMENT
                        i += 1
                        continue
                    if text[i] == "*":
                        self.mode = self.BLOCK_COMMENT
                        i += 1
                        continue
                    raise self._error("unexpected '/'", view, i)
                c = text[i]
                if c in _WHITESPACE:
                    i += 1
                elif c == "/":
                    self._slash = True
                    i += 1
                elif c == '"':
                    if self.decl == self.EXPECT_KEY or self.decl == self.EXPECT_VALUE:
                        self._lit_start = view.offset(i + 1)
                        self.handler.on_string_start(self._lit_start)
                        self.mode = self.STRING
                        i += 1
                    else:
                        raise self._error("unexpected string literal", view, i)
                elif c == "=":
                    if self.decl != self.EXPECT_EQ:
                        raise self._error("unexpected '='", view, i)
                    self.decl = self.EXPECT_VALUE
                    i += 1
                elif c == ";":
                    if self.decl != self.EXPECT_SEMI:
                        raise self._error("unexpected ';'", view, i)
                    self.decl = self.EXPECT_KEY
                    self.handler.on_entry(self._key, self._value)
                    i += 1
                else:
                    raise self._error("unexpected character %r" % c, view, i)

    def finish(self) -> None:
        if self.mode == self.BLOCK_COMMENT:
            raise StringsParseError("unterminated block comment")
        if self.mode == self.STRING or self.esc is not None:
            raise StringsParseError("unterminated string literal")
        if self._slash:
            raise StringsParseError("dangling '/' at end of input")
        if self.decl != self.EXPECT_KEY:
            raise StringsParseError("incomplete declaration")
