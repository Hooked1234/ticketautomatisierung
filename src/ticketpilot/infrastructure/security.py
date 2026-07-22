"""Security helpers for data crossing infrastructure boundaries.

The module deliberately has no third-party dependencies.  It is shared by the
SQLite and logging adapters so redaction rules cannot silently drift apart.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from collections.abc import Mapping, Sequence
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

REDACTED = "<REDACTED>"
TRUNCATED = "<TRUNCATED>"

_SENSITIVE_KEY_PARTS = frozenset(
    {
        "authorization",
        "auth",
        "token",
        "password",
        "passwd",
        "secret",
        "cookie",
        "credential",
        "api_key",
        "apikey",
        "access_key",
        "private_key",
        "client_secret",
        "pat",
    }
)
_AUTH_RE = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]+")
_HEADER_RE = re.compile(
    r"(?i)\b(authorization|x-api-key|api[-_ ]?key|token|password|secret)\b"
    r"(\s*[:=]\s*)([^\s,;]+)"
)
_URL_CREDENTIAL_RE = re.compile(r"(?i)(https?://)([^/@\s:]+):([^/@\s]+)@")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_KNOWN_SECRETS: set[str] = set()
_KNOWN_SECRETS_LOCK = threading.RLock()


class SensitiveDataError(ValueError):
    """Raised when a caller attempts to persist a credential-like field."""


def is_sensitive_key(key: object) -> bool:
    """Return whether *key* names credential material.

    Matching is intentionally based on normalized key segments.  A field such
    as ``token_count`` is considered sensitive too: refusing it is preferable
    to accidentally persisting an actual token.
    """

    normalized = re.sub(r"[^a-z0-9]+", "_", str(key).strip().lower()).strip("_")
    if normalized in _SENSITIVE_KEY_PARTS:
        return True
    parts = frozenset(normalized.split("_"))
    return bool(parts & _SENSITIVE_KEY_PARTS)


def assert_no_secret_fields(value: Any, *, path: str = "$") -> None:
    """Reject mappings containing credential-like keys at any nesting level."""

    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if is_sensitive_key(key):
                raise SensitiveDataError(
                    f"Credential-like field is not allowed in local persistence: {child_path}"
                )
            assert_no_secret_fields(child, path=child_path)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            assert_no_secret_fields(child, path=f"{path}[{index}]")


def redact_text(text: object, *, max_length: int | None = None) -> str:
    """Redact common secret representations from an arbitrary value."""

    result = str(text)
    with _KNOWN_SECRETS_LOCK:
        known_secrets = tuple(_KNOWN_SECRETS)
    for secret in known_secrets:
        result = result.replace(secret, REDACTED)
    result = _AUTH_RE.sub(lambda match: f"{match.group(1)} {REDACTED}", result)
    result = _HEADER_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}{REDACTED}", result)
    result = _URL_CREDENTIAL_RE.sub(lambda match: f"{match.group(1)}{REDACTED}@", result)
    result = _JWT_RE.sub(REDACTED, result)
    result = _redact_url_query(result)
    if max_length is not None and len(result) > max_length:
        keep = max(0, max_length - len(TRUNCATED) - 1)
        return f"{result[:keep]}…{TRUNCATED}"
    return result


def register_secret(secret: str) -> None:
    """Remember a process-local secret so even unlabelled occurrences are redacted."""

    value = str(secret)
    if len(value) >= 4:
        with _KNOWN_SECRETS_LOCK:
            _KNOWN_SECRETS.add(value)


def _redact_url_query(text: str) -> str:
    """Redact sensitive query values when *text* itself is one HTTP URL."""

    if not text.lower().startswith(("http://", "https://")):
        return text
    try:
        split = urlsplit(text)
        if not split.query:
            return text
        query = [
            (key, REDACTED if is_sensitive_key(key) else value)
            for key, value in parse_qsl(split.query, keep_blank_values=True)
        ]
        return urlunsplit(
            (split.scheme, split.netloc, split.path, urlencode(query), split.fragment)
        )
    except ValueError:
        return text


def sanitize(
    value: Any,
    *,
    max_depth: int = 8,
    max_items: int = 100,
    max_string: int | None = 2_000,
    _depth: int = 0,
) -> Any:
    """Return a JSON-compatible, bounded and redacted copy of *value*.

    Unknown objects become their redacted string form.  This prevents logging
    formatters from invoking arbitrary encoders on external response objects.
    """

    if _depth >= max_depth:
        return TRUNCATED
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return redact_text(value, max_length=max_string)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"<BINARY:{len(value)} bytes>"
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for index, (raw_key, child) in enumerate(value.items()):
            if index >= max_items:
                result[TRUNCATED] = f"{len(value) - max_items} more items"
                break
            key = redact_text(raw_key, max_length=200)
            result[key] = (
                REDACTED
                if is_sensitive_key(raw_key)
                else sanitize(
                    child,
                    max_depth=max_depth,
                    max_items=max_items,
                    max_string=max_string,
                    _depth=_depth + 1,
                )
            )
        return result
    if isinstance(value, Sequence):
        result_list = [
            sanitize(
                child,
                max_depth=max_depth,
                max_items=max_items,
                max_string=max_string,
                _depth=_depth + 1,
            )
            for child in value[:max_items]
        ]
        if len(value) > max_items:
            result_list.append(TRUNCATED)
        return result_list
    return redact_text(value, max_length=max_string)


def safe_json_dumps(value: Any, *, compact: bool = True) -> str:
    """Serialize a sanitized value deterministically."""

    kwargs: dict[str, Any] = {
        "ensure_ascii": False,
        "sort_keys": True,
        "allow_nan": False,
    }
    if compact:
        kwargs["separators"] = (",", ":")
    return json.dumps(sanitize(value), **kwargs)


def safe_error_message(error: BaseException, *, max_length: int = 500) -> str:
    """Return a bounded exception description without traceback or response body."""

    message = redact_text(error, max_length=max_length)
    return f"{type(error).__name__}: {message}"


class RedactingFilter(logging.Filter):
    """Redact a log record before any handler writes it."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            rendered = record.getMessage()
        except Exception:  # pragma: no cover - defensive against broken __str__ implementations
            rendered = "Log message could not be rendered safely"
        record.msg = redact_text(rendered, max_length=4_000)
        record.args = ()
        if record.exc_info:
            error = record.exc_info[1]
            record.exc_text = safe_error_message(error) if error else "Exception"
            record.exc_info = None
        for key, value in tuple(record.__dict__.items()):
            if key.startswith("_") or key in _LOG_RECORD_STANDARD_FIELDS:
                continue
            record.__dict__[key] = REDACTED if is_sensitive_key(key) else sanitize(value)
        return True


_LOG_RECORD_STANDARD_FIELDS = frozenset(logging.makeLogRecord({}).__dict__)


class SafeJsonFormatter(logging.Formatter):
    """Minimal JSON-lines formatter containing only safe, useful fields."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_text(record.getMessage(), max_length=4_000),
        }
        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _LOG_RECORD_STANDARD_FIELDS and not key.startswith("_")
        }
        if extras:
            payload["context"] = sanitize(extras)
        if record.exc_text:
            payload["error"] = redact_text(record.exc_text, max_length=500)
        return safe_json_dumps(payload)


def configure_secure_file_logging(
    path: str | Path,
    *,
    logger_name: str = "ticketpilot",
    max_bytes: int = 1_000_000,
    backup_count: int = 3,
    level: int = logging.INFO,
) -> logging.Logger:
    """Create a size-bounded local JSON-lines logger with mandatory redaction."""

    log_path = Path(path).expanduser()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
        delay=False,
    )
    try:
        os.chmod(log_path, 0o600)
    except OSError:
        pass
    handler.addFilter(RedactingFilter())
    handler.setFormatter(SafeJsonFormatter())

    logger = logging.getLogger(logger_name)
    logger.setLevel(level)
    logger.propagate = False
    for existing in tuple(logger.handlers):
        if getattr(existing, "_ticketpilot_secure_handler", False):
            logger.removeHandler(existing)
            existing.close()
    handler.__dict__["_ticketpilot_secure_handler"] = True
    logger.addHandler(handler)
    return logger


__all__ = [
    "REDACTED",
    "TRUNCATED",
    "RedactingFilter",
    "SafeJsonFormatter",
    "SensitiveDataError",
    "assert_no_secret_fields",
    "configure_secure_file_logging",
    "is_sensitive_key",
    "redact_text",
    "register_secret",
    "safe_error_message",
    "safe_json_dumps",
    "sanitize",
]
