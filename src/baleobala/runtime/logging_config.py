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

    NO_COLOR              if set, suppresses ANSI colours in text mode
                          (https://no-color.org/). Colours are also auto-
                          suppressed when stderr is not a TTY.

The correlation ID (`cid`) that relay_handler.py threads through every
log call is already embedded in the message string. When using json
format it additionally surfaces as a top-level field if the log record
carries `cid` in `extra`.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from typing import Any


# ANSI escape sequences. Premium-minimal palette: muted, low-saturation.
class _Ansi:
    RESET = "\x1b[0m"
    DIM = "\x1b[2m"
    BOLD = "\x1b[1m"
    # Foregrounds
    GREY = "\x1b[38;5;245m"
    SOFT_GREY = "\x1b[38;5;240m"
    VIOLET = "\x1b[38;5;141m"
    CYAN = "\x1b[38;5;117m"
    AMBER = "\x1b[38;5;215m"
    ROSE = "\x1b[38;5;204m"
    MAGENTA = "\x1b[38;5;201m"
    GREEN = "\x1b[38;5;115m"


def _colour_enabled(stream) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("BALEOBALA_FORCE_COLOR"):
        return True
    return bool(getattr(stream, "isatty", lambda: False)())


class _TextFormatter(logging.Formatter):
    """Concise human-readable formatter, optionally coloured.

    Layout (no colour):
        14:31:42 │ INF │ vpn.relay_handler · message body

    Colour, when enabled, only paints the timestamp (dim), the level
    (per-severity), the logger name (grey), and the dividers (dim).
    The message body stays plain so structured payloads remain greppable.
    """

    _LEVEL_ABBR = {
        "DEBUG": "DBG",
        "INFO": "INF",
        "WARNING": "WRN",
        "ERROR": "ERR",
        "CRITICAL": "CRT",
    }

    _LEVEL_COLOUR = {
        "DEBUG": _Ansi.CYAN,
        "INFO": _Ansi.VIOLET,
        "WARNING": _Ansi.AMBER,
        "ERROR": _Ansi.ROSE,
        "CRITICAL": _Ansi.BOLD + _Ansi.MAGENTA,
    }

    def __init__(self, *, use_colour: bool) -> None:
        super().__init__()
        self._use_colour = use_colour

    def format(self, record: logging.LogRecord) -> str:
        ts = self.formatTime(record, "%H:%M:%S")
        abbr = self._LEVEL_ABBR.get(record.levelname, record.levelname[:3])
        name = record.name.removeprefix("baleobala.")
        msg = record.getMessage()
        if self._use_colour:
            colour = self._LEVEL_COLOUR.get(record.levelname, "")
            sep = f"{_Ansi.SOFT_GREY}│{_Ansi.RESET}"
            dot = f"{_Ansi.SOFT_GREY}·{_Ansi.RESET}"
            base = (
                f"{_Ansi.DIM}{ts}{_Ansi.RESET} {sep} "
                f"{colour}{abbr}{_Ansi.RESET} {sep} "
                f"{_Ansi.GREY}{name}{_Ansi.RESET} {dot} {msg}"
            )
        else:
            base = f"{ts} │ {abbr} │ {name} · {msg}"
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
    handler = logging.StreamHandler()
    formatter: logging.Formatter
    if fmt == "json":
        formatter = _JsonFormatter()
    else:
        formatter = _TextFormatter(use_colour=_colour_enabled(handler.stream))
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


def print_banner(subtitle: str | None = None, *, stream=None) -> None:
    """Print a small startup banner. Auto-disabled when stderr isn't a TTY
    (so journald/log files stay clean) or NO_COLOR is set.

    Usage: call once from a CLI entry point before doing real work.
    """
    out = stream if stream is not None else sys.stderr
    if not _colour_enabled(out):
        return
    line = f"{_Ansi.VIOLET}▸{_Ansi.RESET} {_Ansi.BOLD}baleobala{_Ansi.RESET}"
    if subtitle:
        line += f"  {_Ansi.GREY}{subtitle}{_Ansi.RESET}"
    rule = f"{_Ansi.SOFT_GREY}{'─' * 48}{_Ansi.RESET}"
    out.write(f"{line}\n{rule}\n")
    out.flush()
