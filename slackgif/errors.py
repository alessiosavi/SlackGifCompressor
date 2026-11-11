from __future__ import annotations


class SlackGifError(Exception):
    """Base class for all expected SlackGif failures."""


class UsageError(SlackGifError):
    """Bad arguments or an unsafe/ambiguous request."""


class ProbeError(SlackGifError):
    """The input is missing, empty, or not a parseable GIF."""


class BackendError(SlackGifError):
    """An encoder backend failed."""


class ValidationError(SlackGifError):
    """An encoded candidate failed structural validation."""


class OutputError(SlackGifError):
    """The final output could not be written or atomically replaced."""
