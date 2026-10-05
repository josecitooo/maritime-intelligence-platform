"""Structured logging with secret redaction.

Two formats:

* `pretty` — human-readable, used in development.
* `json`   — one object per line, used in production and by log shippers.

Secrets must never reach a log record. As a second line of defence, any
extra field whose *name* looks sensitive is redacted before formatting.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any

_SENSITIVE_TOKENS = frozenset(
    {"key", "keys", "apikey", "password", "passwd", "secret", "token", "credential", "credentials"}
)
_SENSITIVE_SUBSTRINGS = ("database_url", "connection_string", "api_key")
_REDACTED = "***"


def _is_sensitive(name: str) -> bool:
    """True when a field *name* looks like it carries a credential.

    Matching is deliberately name-based: values cannot be inspected
    reliably, and the governing rule remains "never log a secret".
    """
    lowered = name.lower()
    if any(fragment in lowered for fragment in _SENSITIVE_SUBSTRINGS):
        return True
    return any(part in _SENSITIVE_TOKENS for part in re.split(r"[^a-z0-9]+", lowered))

# Attributes present on every LogRecord — anything else is caller-supplied "extra".
_BASE_RECORD = logging.LogRecord(name="", level=0, pathname="", lineno=0, msg="", args=(), exc_info=None)
_STANDARD_ATTRS = frozenset(_BASE_RECORD.__dict__) | {"message", "asctime", "taskName"}


def _extra_fields(record: logging.LogRecord) -> dict[str, Any]:
    return {
        key: value
        for key, value in record.__dict__.items()
        if key not in _STANDARD_ATTRS and not key.startswith("_")
    }


def redact(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a copy with sensitive-looking keys masked, recursively."""
    cleaned: dict[str, Any] = {}
    for key, value in payload.items():
        if _is_sensitive(str(key)):
            cleaned[key] = _REDACTED
        elif isinstance(value, dict):
            cleaned[key] = redact(value)
        else:
            cleaned[key] = value
    return cleaned


def _utc(record: logging.LogRecord) -> datetime:
    return datetime.fromtimestamp(record.created, tz=UTC)


class JsonFormatter(logging.Formatter):
    """One JSON object per line, timestamps in UTC."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": _utc(record).isoformat(timespec="seconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        payload.update(redact(_extra_fields(record)))
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


class PrettyFormatter(logging.Formatter):
    """Compact line format: `[HH:MM:SS] LEVEL  logger: message key=value`."""

    def format(self, record: logging.LogRecord) -> str:
        stamp = _utc(record).strftime("%H:%M:%S")
        line = f"[{stamp}] {record.levelname:<7} {record.name}: {record.getMessage()}"

        extra = redact(_extra_fields(record))
        if extra:
            line += "  " + "  ".join(f"{key}={_render(value)}" for key, value in extra.items())
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def _render(value: Any) -> str:
    if isinstance(value, str) and (" " in value or not value):
        return f'"{value}"'
    return str(value)


def setup_logging(level: str = "INFO", fmt: str = "pretty") -> None:
    """Install a single stdout handler on the root logger (idempotent)."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if fmt == "json" else PrettyFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())


def get_logger(name: str) -> logging.Logger:
    """Module-level logger, e.g. `get_logger(__name__)`."""
    return logging.getLogger(name)
