"""Public API: iter_entries and merge.

Both functions stream: input is consumed in bounded chunks and output bytes
are written as soon as they are known to be final.  Peak extra memory is
bounded by one read chunk plus the current entry's literal, regardless of
file size or comment length.
"""

from __future__ import annotations

from typing import BinaryIO, Iterator, Mapping, Tuple

from .decoder import StreamDecoder
from .parser import EntryHandler, Parser

_READ_SIZE = 65536

_ESCAPE_MAP = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"}


def _escape(text: str) -> str:
    """Escape a decoded string for inclusion in a .strings literal."""
    return "".join(_ESCAPE_MAP.get(c, c) for c in text)


def iter_entries(reader: BinaryIO) -> Iterator[Tuple[str, str]]:
    """Yield decoded (key, value) pairs in document order, keeping duplicates.

    Raises StringsParseError on malformed input (surfaced during iteration).
    """
    entries: list[Tuple[str, str]] = []

    class _Handler(EntryHandler):
        def on_entry(self, key: str, value: str) -> None:
            entries.append((key, value))

    decoder = StreamDecoder()
    parser = Parser(_Handler())
    while True:
        chunk = reader.read(_READ_SIZE)
        if not chunk:
            break
        view = decoder.feed(chunk)
        if view.text:
            parser.feed(view)
        while entries:
            yield entries.pop(0)
    view = decoder.finish()
    if view.text:
        parser.feed(view)
    parser.finish()
    while entries:
        yield entries.pop(0)


class _MergeHandler(EntryHandler):
    def __init__(self, merger: "_Merger") -> None:
        self.merger = merger

    def on_string_start(self, content_offset: int) -> None:
        self.merger.hold = content_offset

    def on_key(self, start: int, end: int, decoded: str) -> None:
        merger = self.merger
        merger.current_key = decoded
        merger.hold = None

    def on_value(self, start: int, end: int, decoded: str) -> None:
        merger = self.merger
        key = merger.current_key
        if key in merger.updates:
            new_value = merger.updates[key]
            if decoded != new_value:
                head = start - merger.emit_pos
                merger.writer.write(bytes(merger.pending[:head]))
                merger.writer.write(
                    _escape(new_value).encode(merger.decoder.encoding)
                )
                del merger.pending[: end - merger.emit_pos]
                merger.emit_pos = end
            merger.seen.add(key)
        merger.hold = None


class _Merger:
    def __init__(
        self, reader: BinaryIO, writer: BinaryIO, updates: Mapping[str, str]
    ) -> None:
        self.reader = reader
        self.writer = writer
        self.updates = updates
        self.decoder = StreamDecoder()
        self.parser = Parser(_MergeHandler(self))
        self.pending = bytearray()  # bytes read but not yet written out
        self.emit_pos = 0           # absolute offset of pending[0]
        self.decoded_upto = 0
        self.hold: int | None = None
        self.current_key: str | None = None
        self.seen: set[str] = set()
        self.last_char: str | None = None
        self.any_input = False

    def _emit(self) -> None:
        limit = self.hold if self.hold is not None else self.decoded_upto
        count = limit - self.emit_pos
        if count > 0:
            self.writer.write(bytes(self.pending[:count]))
            del self.pending[:count]
            self.emit_pos += count

    def _consume(self, chunk: bytes) -> None:
        self.pending += chunk
        view = self.decoder.feed(chunk)
        self.decoded_upto = self.decoder.consumed
        if view.text:
            self.parser.feed(view)
            self.last_char = view.text[-1]
        self._emit()

    def run(self) -> None:
        while True:
            chunk = self.reader.read(_READ_SIZE)
            if not chunk:
                break
            self.any_input = True
            self._consume(chunk)
        view = self.decoder.finish()
        self.decoded_upto = self.decoder.consumed
        if view.text:
            self.parser.feed(view)
            self.last_char = view.text[-1]
        self.parser.finish()
        self._emit()
        missing = [key for key in self.updates if key not in self.seen]
        if missing:
            encoding = self.decoder.encoding
            if self.any_input and self.last_char != "\n":
                self.writer.write("\n".encode(encoding))
            for key in missing:
                line = '"%s" = "%s";\n' % (_escape(key), _escape(self.updates[key]))
                self.writer.write(line.encode(encoding))


def merge(reader: BinaryIO, writer: BinaryIO, updates: Mapping[str, str]) -> None:
    """Merge ``updates`` into the .strings document read from ``reader``.

    All original bytes are preserved except replaced value literals and
    declarations appended at the end for keys not present in the input.
    Raises StringsParseError on malformed input, even if ``updates`` is empty.
    """
    _Merger(reader, writer, updates).run()
