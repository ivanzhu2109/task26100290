"""Chunk-independence and encoding tests for all three supported encodings.

Expected values are written out explicitly (encoded with codecs directly),
never round-tripped through the library under test.
"""

import io
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from strings_merge import iter_entries, merge
from chunkutil import ChunkReader, ALL_CHUNKINGS

# Logical document shared by all encodings.  Exercises: comments (line and
# block, including comment-like text inside strings and declaration-like
# text inside comments), escapes of every supported kind, a line
# continuation, a non-BMP character and duplicate keys.
DOC_TEXT = (
    "// header comment\n"
    "/* block\n"
    '   "fake" = "declaration"; */\n'
    '"greeting" = "Hello \\"world\\"";\n'
    '"greeting" = "tab\\tnewline\\nreturn\\rbackslash\\\\";\n'
    '"octal" = "\\0\\12\\177";\n'
    '"uni" = "\\U0041\\U00E9\\UD83D\\UDE00";\n'
    '"cont" = "abc\\\ndef";\n'
    '"semi;eq=//not-comment" = "/* not comment */";\n'
    "// trailing comment without newline"
)

EXPECTED_ENTRIES = [
    ("greeting", 'Hello "world"'),
    ("greeting", "tab\tnewline\nreturn\rbackslash\\"),
    ("octal", "\x00\x0a\x7f"),
    ("uni", "A\u00e9\U0001F600"),
    ("cont", "abcdef"),
    ("semi;eq=//not-comment", "/* not comment */"),
]

ENCODINGS = {
    "utf-8": ("utf-8", b""),
    "utf-8-bom": ("utf-8", b"\xef\xbb\xbf"),
    "utf-16-le": ("utf-16-le", b"\xff\xfe"),
    "utf-16-be": ("utf-16-be", b"\xfe\xff"),
}


def make_doc(encoding, bom):
    return bom + DOC_TEXT.encode(encoding)


class IterEntriesChunkedTest(unittest.TestCase):
    def test_entries_same_for_all_chunkings_and_encodings(self):
        for name, (encoding, bom) in ENCODINGS.items():
            data = make_doc(encoding, bom)
            for sizes in ALL_CHUNKINGS:
                with self.subTest(name=name, sizes=sizes):
                    entries = list(iter_entries(ChunkReader(data, sizes)))
                    self.assertEqual(entries, EXPECTED_ENTRIES)

    def test_iter_entries_is_lazy(self):
        data = make_doc("utf-8", b"")
        gen = iter_entries(ChunkReader(data, [1]))
        self.assertFalse(isinstance(gen, list))
        self.assertEqual(next(gen), EXPECTED_ENTRIES[0])


class MergeChunkedTest(unittest.TestCase):
    def test_noop_merge_is_byte_identical(self):
        for name, (encoding, bom) in ENCODINGS.items():
            data = make_doc(encoding, bom)
            for sizes in ALL_CHUNKINGS:
                with self.subTest(name=name, sizes=sizes):
                    out = io.BytesIO()
                    merge(ChunkReader(data, sizes), out, {})
                    self.assertEqual(out.getvalue(), data)

    def test_merge_output_bytes(self):
        updates = {
            "greeting": "Hi \U0001F600",
            "octal": "\x00\x0a\x7f",  # semantically identical: keep raw
            "brand new \u00e9": "v\\al\"ue\n",
        }
        for name, (encoding, bom) in ENCODINGS.items():
            data = make_doc(encoding, bom)
            expected_text = (
                "// header comment\n"
                "/* block\n"
                '   "fake" = "declaration"; */\n'
                '"greeting" = "Hi \U0001F600";\n'
                '"greeting" = "Hi \U0001F600";\n'
                '"octal" = "\\0\\12\\177";\n'  # untouched: same semantics
                '"uni" = "\\U0041\\U00E9\\UD83D\\UDE00";\n'
                '"cont" = "abc\\\ndef";\n'
                '"semi;eq=//not-comment" = "/* not comment */";\n'
                "// trailing comment without newline\n"
                '"brand new \u00e9" = "v\\\\al\\"ue\\n";\n'
            )
            expected = bom + expected_text.encode(encoding)
            for sizes in ALL_CHUNKINGS:
                with self.subTest(name=name, sizes=sizes):
                    out = io.BytesIO()
                    merge(ChunkReader(data, sizes), out, updates)
                    self.assertEqual(out.getvalue(), expected)


if __name__ == "__main__":
    unittest.main()


class Utf16CommentBoundaryTest(unittest.TestCase):
    """Bytes of multi-byte chars must not be mistaken for the '*/' terminator."""

    CASES = {
        # LE: bytes of ⩁⼀　 contain "2A 00 2F 00" at an odd offset
        "utf-16-le": (b"\xff\xfe", "⩁⼀　"),
        # BE: bytes of 䄀⨀⽁ contain "00 2A 00 2F" at an odd offset
        "utf-16-be": (b"\xfe\xff", "䄀⨀⽁"),
    }

    def _doc(self, bom, encoding, body):
        text = "/*" + body + "x*/\n" + '"k" = "v";\n'
        return bom + text.encode(encoding)

    def test_terminator_not_matched_inside_char(self):
        for name, (bom, body) in self.CASES.items():
            encoding = name
            data = self._doc(bom, encoding, body)
            for sizes in ALL_CHUNKINGS:
                with self.subTest(name=name, sizes=sizes):
                    entries = list(iter_entries(ChunkReader(data, sizes)))
                    self.assertEqual(entries, [("k", "v")])
                    out = io.BytesIO()
                    merge(ChunkReader(data, sizes), out, {})
                    self.assertEqual(out.getvalue(), data)
