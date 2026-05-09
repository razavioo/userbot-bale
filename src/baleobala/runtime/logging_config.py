"""Structured logging configuration for baleobala services.

Usage:
    from baleobala.runtime.logging_config import configure_logging
    configure_logging(level=logging.INFO)

Environment:
    BALEOBALA_LOG_FORMAT  "text" (default) or "json"
        text — human-readable, coloured by level prefix
        json — newline-delimited JSON for journald / log aggregators;
               each record includes: ts, level, logger, msg, and any
               extra fields passed via `extra={"key": val}`.

The correlation ID (`cid`) that relay_handler.py threads through every
log call is already embedded in the message string. When using json
format it additionally surfaces as a top-level field if the log record
carries `cid` in `extra`.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any


class _TextFormatter(logging.Formatter):
    """Concise human-readable formatter."""

    _LEVEL_ABBR = {
        "DEBUG": "DBG",
        "INFO": "INF",
        "WARNING": "WRN",
        "ERROR": "ERR",
        "CRITICAL": "CRT",
    }

    def format(self, record: logging.LogRecord) -> str:
        ts = self.formatTime(record, "%H:%M:%S")
        abbr = self._LEVEL_ABBR.get(record.levelname, record.levelname[:3])
        name = record.name.removeprefix("baleobala.")
        msg = record.getMessage()
        base = f"{ts} {abbr} {name}: {msg}"
        if record.exc_info:
            base = base + "\n" + self.formatException(record.exc_info)
        return base


class _JsonFormatter(logging.Formatter):
    """Newline-delimited JSON — one dict per record."""

    def format(self, record: logging.LogRecord) -> str:
        obj: dict[str, Any] = {
            "ts": time.strftime(
                "%Y-%m-%dT%H:%M:%SZ",
                time.gmtime(record.created),
            ),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # Surface any `extra` fields (cid, peer_id, session_id, relay_id …).
        for key, val in record.__dict__.items():
            if key.startswith("_") or key in _LOGRECORD_BUILTIN_KEYS:
                continue
            try:
                json.dumps(val)  # ensure serialisable
                obj[key] = val
            except (TypeError, ValueError):
                obj[key] = repr(val)
        if record.exc_info:
            obj["exc"] = self.formatException(record.exc_info)
        return json.dumps(obj, ensure_ascii=False)


_LOGRECORD_BUILTIN_KEYS = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()
    | {"message", "asctime"}
)


def configure_logging(
    level: int = logging.INFO,
    *,
    fmt: str | None = None,
) -> None:
    """Configure the root logger.

    Args:
        level: minimum log level (e.g. logging.DEBUG).
        fmt:   "text" or "json". Falls back to BALEOBALA_LOG_FORMAT env
               var, then "text".
    """
    fmt = fmt or os.environ.get("BALEOBALA_LOG_FORMAT", "text")
    formatter: logging.Formatter
    if fmt == "json":
        formatter = _JsonFormatter()
    else:
        formatter = _TextFormatter()

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(level)
    # Avoid duplicate handlers if configure_logging() is called more than once.
    root.handlers.clear()
    root.addHandler(handler)

    # Quieten noisy third-party loggers.
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
