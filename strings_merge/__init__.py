"""Streaming, lossless merge for iOS .strings resource files."""

from .api import iter_entries, merge
from .errors import StringsParseError

__all__ = ["iter_entries", "merge", "StringsParseError"]
