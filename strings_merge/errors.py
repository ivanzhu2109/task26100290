"""Exception types for strings_merge."""


class StringsParseError(Exception):
    """Raised when the input .strings document is malformed."""

    def __init__(self, message: str, offset: int | None = None) -> None:
        self.offset = offset
        if offset is not None:
            message = "%s (at byte offset %d)" % (message, offset)
        super().__init__(message)
