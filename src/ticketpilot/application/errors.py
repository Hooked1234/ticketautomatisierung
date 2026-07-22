"""Safe application errors and redaction helpers."""

from __future__ import annotations

import re


class TicketPilotError(Exception):
    """Base for errors safe to classify at the application boundary."""


class ConfirmationRequired(TicketPilotError):
    pass


class ConcurrencyConflict(TicketPilotError):
    pass


class DefiniteWriteError(TicketPilotError):
    """The adapter knows the write did not take effect."""


class UncertainWriteError(TicketPilotError):
    """A write may have taken effect and must not be automatically repeated."""


_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)(authorization\s*[:=]\s*)([^\s,;]+(?:\s+[^\s,;]+)?)"), r"\1<redacted>"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+\-/=]+"), "Bearer <redacted>"),
    (
        re.compile(r"(?i)(\b(?:token|pat|api[_-]?key|password|secret)\b\s*[:=]\s*)([^\s,;}&]+)"),
        r"\1<redacted>",
    ),
)


def safe_error_message(error: BaseException | str, *, limit: int = 300) -> str:
    """Return a compact message with common credential forms removed."""

    if isinstance(error, TimeoutError):
        return "Operation timed out."
    text = str(error).replace("\r", " ").replace("\n", " ").strip()
    if not text:
        text = error.__class__.__name__ if isinstance(error, BaseException) else "Operation failed."
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text
