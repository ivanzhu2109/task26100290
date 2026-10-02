"""Merge semantics: duplicates, escape equivalence, comments, malformed tails."""

import io
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from strings_merge import iter_entries, merge, StringsParseError


def run_merge(doc, updates):
    out = io.BytesIO()
    merge(io.BytesIO(doc), out, updates)
    return out.getvalue()


class DuplicateKeyTest(unittest.TestCase):
    def test_all_occurrences_updated(self):
        # Three occurrences of "k", written with mixed but equivalent escapes.
        doc = (
            b'"k" = "old";\n'
            b'"k" = "\\U006F\\154\\144";\n'  # "old" via mixed escapes
            b'"\\U006B" = "old";\n'          # key written as escape
        )
        got = run_merge(doc, {"k": "new"})
        assert got == (
            b'"k" = "new";\n'
            b'"k" = "new";\n'
            b'"\\U006B" = "new";\n'
        ), got

    def test_semantically_equal_value_keeps_original_bytes(self):
        doc = b'"k" = "\\U0041\\101\\n";\n'
        got = run_merge(doc, {"k": "AA\n"})
        assert got == doc, got  # byte-identical: no real change

    def test_no_normalization(self):
        # "e + combining accent" (decoded key) vs precomposed "é" update key:
        # distinct keys, so the precomposed update must be appended.
        doc = '"é" = "1";\n'.encode("utf-8")
        got = run_merge(doc, {"é": "2"})
        expected = doc + '"é" = "2";\n'.encode("utf-8")
        assert got == expected, got


class CommentTest(unittest.TestCase):
    def test_pseudo_declarations_in_comments_ignored(self):
        doc = (
            b'/* "k" = "fake"; */\n'
            b'// "k" = "also fake";\n'
            b'"k" = "real";\n'
        )
        got = run_merge(doc, {"k": "updated"})
        assert got == (
            b'/* "k" = "fake"; */\n'
            b'// "k" = "also fake";\n'
            b'"k" = "updated";\n'
        ), got

    def test_trailing_line_comment_does_not_swallow_appends(self):
        doc = b'"a" = "1";\n// trailing comment without newline'
        got = run_merge(doc, {"b": "2"})
        assert got == doc + b'\n"b" = "2";\n', got
        # and the appended entry must be visible to a parser
        entries = list(iter_entries(io.BytesIO(got)))
        assert entries == [("a", "1"), ("b", "2")], entries

    def test_append_after_newline_needs_no_extra_blank(self):
        doc = b'"a" = "1";\n'
        got = run_merge(doc, {"b": "2"})
        assert got == b'"a" = "1";\n"b" = "2";\n', got

    def test_append_to_empty_file(self):
        got = run_merge(b"", {"a": "1", "b": "2"})
        assert got == b'"a" = "1";\n"b" = "2";\n', got

    def test_append_order_follows_updates(self):
        got = run_merge(b"", {"z": "1", "a": "2", "m": "3"})
        assert got == b'"z" = "1";\n"a" = "2";\n"m" = "3";\n', got

    def test_untouched_regions_byte_preserved(self):
        doc = (
            b'  /* lead */  "a"\t=\r\n"1" ; // note\n'
            b'"b"="\\t\\U00E9\\377";\n'
        )
        got = run_merge(doc, {"b": "changed"})
        assert got == (
            b'  /* lead */  "a"\t=\r\n"1" ; // note\n'
            b'"b"="changed";\n'
        ), got


class MalformedInputTest(unittest.TestCase):
    def test_empty_updates_still_reject_malformed_tail(self):
        bad_docs = [
            b'"a" = "1";\n"b" = "2"',          # missing semicolon
            b'"a" = "1";\n"b"',                # incomplete declaration
            b'"a" = "1";\n/* unterminated',    # unterminated block comment
            b'"a" = "1";\n"unterminated',      # unterminated string
            b'"a" = "1";\n stray',             # stray token
            b'"a" = "1";\n"b" = "\\q";',       # invalid escape
            b'"a" = "1";\n"b" = "raw\rbreak";',  # unescaped CR
            b"/* bad \xff byte */",            # invalid UTF-8 in comment
            b"// bad \xc3\x28 line",            # invalid UTF-8 in line comment
        ]
        for doc in bad_docs:
            with self.subTest(doc=doc):
                out = io.BytesIO()
                with self.assertRaises(StringsParseError):
                    merge(io.BytesIO(doc), out, {})

    def test_invalid_encoding_rejected(self):
        for doc in [b"\xff", b'"a" = "\xc3";', b"\xff\xfe" + b"a"]:
            with self.subTest(doc=doc):
                with self.assertRaises(StringsParseError):
                    list(iter_entries(io.BytesIO(doc)))

    def test_surrogate_errors(self):
        for doc in [
            b'"a" = "\\UD800";',          # lone high surrogate
            b'"a" = "\\UD800x";',         # high surrogate then plain char
            b'"a" = "\\UD800\\";',        # high surrogate then other escape
            b'"a" = "\\UDC00";',          # lone low surrogate
            b'"a" = "\\UD800\\UD800\\UDC00";',  # high+high
        ]:
            with self.subTest(doc=doc):
                with self.assertRaises(StringsParseError):
                    list(iter_entries(io.BytesIO(doc)))


if __name__ == "__main__":
    unittest.main()
