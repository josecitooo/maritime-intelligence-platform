"""Logging tests: structured output plus secret redaction."""

from __future__ import annotations

import json
import logging
from datetime import datetime

from app.logging import JsonFormatter, PrettyFormatter, redact


def record(
    msg: str = "ingestion.completed",
    *,
    level: int = logging.INFO,
    args: tuple = (),
    **extra,
) -> logging.LogRecord:
    entry = logging.LogRecord("app.ingestion", level, __file__, 1, msg, args, None)
    for key, value in extra.items():
        setattr(entry, key, value)
    return entry


def test_redaction_is_recursive_and_key_based():
    payload = {
        "api_key": "abc",
        "nested": {"password": "hunter2", "count": 2},
        "supabase_key": "xyz",
        "mmsi": 413_797_631,
    }
    cleaned = redact(payload)

    assert cleaned["api_key"] == "***"
    assert cleaned["supabase_key"] == "***"
    assert cleaned["nested"]["password"] == "***"
    assert cleaned["nested"]["count"] == 2
    assert cleaned["mmsi"] == 413_797_631


def test_json_formatter_emits_one_parseable_line():
    entry = record(records_received=2481, valid=2392, api_key="super-secret")
    line = JsonFormatter().format(entry)

    payload = json.loads(line)
    assert payload["msg"] == "ingestion.completed"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "app.ingestion"
    assert payload["records_received"] == 2481
    assert payload["valid"] == 2392
    assert payload["api_key"] == "***"
    assert "super-secret" not in line


def test_json_formatter_timestamps_are_utc():
    payload = json.loads(JsonFormatter().format(record()))
    stamp = datetime.fromisoformat(payload["ts"])
    assert stamp.utcoffset() is not None and stamp.utcoffset().total_seconds() == 0


def test_json_formatter_survives_unserialisable_values():
    line = JsonFormatter().format(record(bbox=object()))
    assert json.loads(line)["bbox"].startswith("<object")


def test_pretty_formatter_shows_message_and_extras():
    line = PrettyFormatter().format(record(valid=2392, rejected=89))
    assert "ingestion.completed" in line
    assert "valid=2392" in line
    assert "rejected=89" in line
    assert "app.ingestion" in line


def test_pretty_formatter_redacts_sensitive_extras():
    line = PrettyFormatter().format(record(password="hunter2"))
    assert "hunter2" not in line
    assert "password=***" in line


def test_exception_info_is_included():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        entry = record()
        entry.exc_info = sys.exc_info()
    payload = json.loads(JsonFormatter().format(entry))
    assert "ValueError: boom" in payload["exc"]
