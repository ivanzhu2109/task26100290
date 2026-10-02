"""Tests for strings_merge.  Run with: python3 -m unittest discover -s tests -v
or directly: python3 tests/test_strings_merge.py
"""

import io
import os
import sys
import tracemalloc
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from strings_merge import iter_entries, merge, StringsParseError

CHUNK_PATTERNS = ([1], [2, 3, 5, 7, 11, 13], [64, 1, 3, 128, 2, 5], [65536])
ENCODINGS = ("utf-8", "utf-8-bom", "utf-16-le", "utf-16-be")


def encode_doc(text, encoding):
    if encoding == "utf-8":
        return text.encode("utf-8")
    if encoding == "utf-8-bom":
        return b"\xef\xbb\xbf" + text.encode("utf-8")
    if encoding == "utf-16-le":
        return b"\xff\xfe" + text.encode("utf-16-le")
    if encoding == "utf-16-be":
        return b"\xfe\xff" + text.encode("utf-16-be")
    raise ValueError(encoding)


class ChunkedReader:
    """Binary reader returning irregular chunk sizes; never seeks."""

    def __init__(self, data, sizes):
        self._data = data
        self._sizes = sizes
        self._i = 0
        self._pos = 0

    def read(self, n=-1):
        if self._pos >= len(self._data):
            return b""
        size = self._sizes[self._i % len(self._sizes)]
        self._i += 1
        if n is not None and n >= 0:
            size = min(size, n)
        out = self._data[self._pos : self._pos + size]
        self._pos += len(out)
        return out


def run_merge(data, updates, sizes=(65536,)):
    out = io.BytesIO()
    merge(ChunkedReader(data, sizes), out, updates)
    return out.getvalue()


DOC = (
    "// header comment\n"
    '/* block "fake" = "decl"; still comment */\n'
    '"greeting" = "Hello\\nWorld";\n'
    '"\\U0061lert" = "a\\tb";\n'
    '"emoji" = "\\Ud83d\\Ude00";\n'
    '"emoji" = "\U0001F600";\n'
    '"oct" = "\\101\\060\\07";\n'
    '"uni" = "caf\u00e9";\n'
    "// trailing comment without newline"
)

DOC_ENTRIES = [
    ("greeting", "Hello\nWorld"),
    ("alert", "a\tb"),
    ("emoji", "\U0001F600"),
    ("emoji", "\U0001F600"),
    ("oct", "A0\x07"),
    ("uni", "caf\u00e9"),
]


class TestIterEntries(unittest.TestCase):
    def test_entries_all_encodings_and_chunkings(self):
        for enc in ENCODINGS:
            data = encode_doc(DOC, enc)
            for sizes in CHUNK_PATTERNS:
                with self.subTest(enc=enc, sizes=sizes):
                    got = list(iter_entries(ChunkedReader(data, sizes)))
                    self.assertEqual(got, DOC_ENTRIES)

    def test_no_bom_is_utf8(self):
        data = '"k" = "\u00e9";\n'.encode("utf-8")
        self.assertEqual(list(iter_entries(ChunkedReader(data, [1]))), [("k", "\u00e9")])

    def test_empty_input(self):
        self.assertEqual(list(iter_entries(ChunkedReader(b"", [1]))), [])

    def test_line_continuation(self):
        doc = b'"a" = "x\\\r\ny\\\nz\\\rw";'
        self.assertEqual(list(iter_entries(ChunkedReader(doc, [1]))), [("a", "xyzw")])

    def test_surrogate_pair_escape(self):
        doc = b'"a" = "\\Ud83d\\Ude00";'
        self.assertEqual(list(iter_entries(ChunkedReader(doc, [1]))), [("a", "\U0001F600")])


class TestMergeEncodings(unittest.TestCase):
    UPDATES = {"uni": "caf\u00e9s", "emoji": "\U0001F600", "new": "\u03bd\u03ad\u03bf"}

    EXPECTED = (
        "// header comment\n"
        '/* block "fake" = "decl"; still comment */\n'
        '"greeting" = "Hello\\nWorld";\n'
        '"\\U0061lert" = "a\\tb";\n'
        '"emoji" = "\\Ud83d\\Ude00";\n'          # semantically equal: untouched
        '"emoji" = "\U0001F600";\n'              # semantically equal: untouched
        '"oct" = "\\101\\060\\07";\n'
        '"uni" = "caf\u00e9s";\n'                # replaced literal
        "// trailing comment without newline"
        "\n"                                       # guard newline before appends
        '"new" = "\u03bd\u03ad\u03bf";\n'
    )

    def test_merge_all_encodings_and_chunkings(self):
        for enc in ENCODINGS:
            data = encode_doc(DOC, enc)
            want = encode_doc(self.EXPECTED, enc)
            for sizes in CHUNK_PATTERNS:
                with self.subTest(enc=enc, sizes=sizes):
                    self.assertEqual(run_merge(data, self.UPDATES, sizes), want)

    def test_identity_when_nothing_changes(self):
        for enc in ENCODINGS:
            data = encode_doc(DOC, enc)
            for updates in ({}, {"emoji": "\U0001F600", "uni": "caf\u00e9",
                                "greeting": "Hello\nWorld", "alert": "a\tb",
                                "oct": "A0\x07"}):
                for sizes in CHUNK_PATTERNS:
                    with self.subTest(enc=enc, sizes=sizes, updates=updates):
                        self.assertEqual(run_merge(data, updates, sizes), data)


class TestMergeSemantics(unittest.TestCase):
    def test_all_duplicate_keys_updated(self):
        doc = b'"k" = "1";\n"\\U006b" = "2";\n"\\153" = "3";\n'
        want = b'"k" = "z";\n"\\U006b" = "z";\n"\\153" = "z";\n'
        self.assertEqual(run_merge(doc, {"k": "z"}, [1]), want)

    def test_key_bytes_never_touched(self):
        doc = b'"\\U006b" = "1";'
        self.assertEqual(run_merge(doc, {"k": "2"}), b'"\\U006b" = "2";')

    def test_value_escaping_on_output(self):
        doc = b'"k" = "old";'
        want = b'"k" = "a\\nb\\t\\"q\\"\\\\";'
        self.assertEqual(run_merge(doc, {"k": 'a\nb\t"q"\\'}), want)

    def test_append_order_and_multiple_missing(self):
        doc = b'"a" = "1";'
        want = b'"a" = "1";\n"b" = "2";\n"c" = "3";\n'
        self.assertEqual(run_merge(doc, {"b": "2", "c": "3"}), want)

    def test_append_into_empty_file(self):
        self.assertEqual(run_merge(b"", {"k": "v"}), b'"k" = "v";\n')

    def test_comment_with_fake_declaration_untouched(self):
        doc = b'/* "k" = "fake"; */ "k" = "real";\n// "k" = "alsoFake";\n'
        want = b'/* "k" = "fake"; */ "k" = "new";\n// "k" = "alsoFake";\n'
        self.assertEqual(run_merge(doc, {"k": "new"}, [1]), want)

    def test_trailing_line_comment_not_swallowing_appends(self):
        doc = b'"a" = "1"; // tail comment'
        want = b'"a" = "1"; // tail comment\n"b" = "2";\n'
        self.assertEqual(run_merge(doc, {"b": "2"}, [1]), want)

    def test_trailing_line_comment_identity_without_appends(self):
        doc = b'"a" = "1"; // tail comment'
        self.assertEqual(run_merge(doc, {"a": "1"}), doc)
        self.assertEqual(run_merge(doc, {}), doc)

    def test_empty_updates_still_rejects_malformed_tail(self):
        for bad in (b'"a" = "1"; "b"', b'"a" = "1"; /* open',
                    b'"a" = "1"; "b" = ', b'"a" = "1"; "b" = "x"',
                    b'"a" = "1"; "b" = "\\q";', b'"a" = "1"; "b" = "x'):
            with self.subTest(bad=bad):
                with self.assertRaises(StringsParseError):
                    run_merge(bad, {})

    def test_invalid_input_rejected(self):
        bad_docs = [
            b'\xff',                              # invalid utf-8
            b'\xed\xa0\x80',                      # CESU-8 surrogate
            b'\xff\xfe\x00\xd8',                  # utf-16 lone high surrogate
            b'\xff\xfe\x00\xdc',                  # utf-16 lone low surrogate
            b'\xff\xfe\x41',                      # truncated utf-16 unit
            b'"a" = "x',                          # unterminated string
            b'"a" = "x"; /* comment',             # unterminated comment
            b'"a" = "x"; /',                      # dangling slash
            b'"a" = "x"; = "y";',                 # stray equals
            b'"a" = "x" "y";',                    # missing semicolon
            b'"a" = "x";;',                       # stray semicolon
            b'"a" = "\\Ud83d\\U0041";',           # high surrogate + non-low
            b'"a" = "\\Udc00";',                  # lone low surrogate escape
            b'"a" = "\\U123";',                   # short \U escape
            b'"a" = "x\ny";',                     # raw newline in string
        ]
        for bad in bad_docs:
            with self.subTest(bad=bad):
                with self.assertRaises(StringsParseError):
                    run_merge(bad, {}, [1])

    def test_valid_edge_escapes(self):
        doc = b'"a" = "\\0\\07\\377\\U0041\\"q\\"\\\\";'
        self.assertEqual(
            list(iter_entries(ChunkedReader(doc, [1]))),
            [("a", '\x00\x07\xffA"q"\\')],
        )


def big_input_pieces(comment_len, count, replaced=None):
    """Generates the big test document without materializing it."""
    yield b"/*"
    chunk = b"x" * 65536
    remaining = comment_len
    while remaining > 0:
        piece = chunk if remaining >= len(chunk) else chunk[:remaining]
        yield piece
        remaining -= len(piece)
    yield b"*/\n"
    for i in range(count):
        key = "key%06d" % i
        value = "value%06d" % i
        if replaced and key in replaced:
            value = replaced[key]
        yield ('"%s" = "%s";\n' % (key, value)).encode("utf-8")


class GenReader:
    """Reader serving generated pieces; bounded internal buffer."""

    def __init__(self, pieces):
        self._it = iter(pieces)
        self._buf = bytearray()
        self._eof = False

    def read(self, n=-1):
        if n is None or n < 0:
            n = 65536
        while len(self._buf) < n and not self._eof:
            piece = next(self._it, None)
            if piece is None:
                self._eof = True
            else:
                self._buf += piece
        out = bytes(self._buf[:n])
        del self._buf[:n]
        return out


class ExpectWriter:
    """Writer verifying output against a generated expected stream."""

    def __init__(self, pieces):
        self._it = iter(pieces)
        self._buf = bytearray()
        self._eof = False
        self.total = 0

    def _fill(self, n):
        while len(self._buf) < n and not self._eof:
            piece = next(self._it, None)
            if piece is None:
                self._eof = True
            else:
                self._buf += piece

    def write(self, data):
        self._fill(len(data))
        expect = bytes(self._buf[: len(data)])
        del self._buf[: len(data)]
        if expect != data:
            raise AssertionError(
                "output mismatch at byte %d: got %r, want %r"
                % (self.total, data[:32], expect[:32])
            )
        self.total += len(data)
        return len(data)

    def check_complete(self):
        self._fill(1)
        if self._buf or not self._eof:
            raise AssertionError("output shorter than expected")


class TestBigStream(unittest.TestCase):
    COMMENT_LEN = 32 * 1024 * 1024
    ENTRY_COUNT = 100_000
    MEMORY_LIMIT = 8 * 1024 * 1024

    def test_big_merge_memory_and_content(self):
        updates = {"key050000": "REPLACED"}
        reader = GenReader(big_input_pieces(self.COMMENT_LEN, self.ENTRY_COUNT))
        writer = ExpectWriter(
            big_input_pieces(self.COMMENT_LEN, self.ENTRY_COUNT, replaced=updates)
        )
        tracemalloc.start()
        try:
            merge(reader, writer, updates)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        writer.check_complete()
        # 5 bytes for /* */ \n; each entry line is 29 bytes, the replaced one 26
        expected_total = self.COMMENT_LEN + 5 + (self.ENTRY_COUNT - 1) * 29 + 26
        self.assertEqual(writer.total, expected_total)
        self.assertLess(
            peak,
            self.MEMORY_LIMIT,
            "peak extra memory %d bytes exceeds 8 MiB" % peak,
        )


if __name__ == "__main__":
    unittest.main()
