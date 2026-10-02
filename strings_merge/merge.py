"""Lossless streaming merge of translation updates into a .strings file."""

from .parser import _Scanner

_ESCAPES = {
    '"': '\\"',
    "\\": "\\\\",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}


def _escape(text):
    parts = []
    for ch in text:
        parts.append(_ESCAPES.get(ch, ch))
    return "".join(parts)


def _encode(text, encoding):
    return text.encode(encoding)


def _encode_literal(value, encoding):
    return _encode('"' + _escape(value) + '"', encoding)


def merge(reader, writer, updates):
    """Merge updates into the .strings document read from reader.

    reader/writer are binary streams (no seeking required).  updates is an
    ordered mapping of distinct keys to replacement values.  All original
    bytes are preserved except replaced value literals and content appended
    at the end for keys not present in the document.  The whole document is
    validated even when updates is empty.
    """
    scanner = _Scanner(reader)
    encoding = "utf-8"
    current_key = None
    seen = set()
    for event in scanner.events():
        kind = event[0]
        if kind == "start":
            encoding = event[1]
            writer.write(event[2])
        elif kind == "trivia" or kind == "punct":
            writer.write(event[1])
        elif kind == "key":
            current_key = event[1]
            writer.write(event[2])
        elif kind == "value":
            decoded, raw = event[1], event[2]
            if current_key in updates:
                seen.add(current_key)
                new_value = updates[current_key]
                if new_value != decoded:
                    writer.write(_encode_literal(new_value, encoding))
                else:
                    writer.write(raw)
            else:
                writer.write(raw)
        elif kind == "end":
            needs_newline = event[1]
            missing = [(k, v) for k, v in updates.items() if k not in seen]
            if missing:
                if needs_newline:
                    writer.write(_encode("\n", encoding))
                for key, value in missing:
                    line = '"%s" = "%s";\n' % (_escape(key), _escape(value))
                    writer.write(_encode(line, encoding))
