"""Tests for baleobala.runtime.logging_config."""
from __future__ import annotations

import json
import logging

import pytest


def _reset_root_logger():
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.WARNING)


@pytest.fixture(autouse=True)
def clean_root_logger():
    _reset_root_logger()
    yield
    _reset_root_logger()


def test_text_format_produces_readable_lines(capfd):
    from baleobala.runtime.logging_config import configure_logging
    configure_logging(level=logging.INFO, fmt="text")
    logging.getLogger("baleobala.test_mod").info("hello world")
    captured = capfd.readouterr()
    out = captured.err
    assert "hello world" in out
    assert "INF" in out
    assert "test_mod" in out


def test_json_format_produces_valid_json(capfd):
    from baleobala.runtime.logging_config import configure_logging
    configure_logging(level=logging.INFO, fmt="json")
    logging.getLogger("baleobala.vpn.relay_handler").info("cid=aabbccdd started")
    captured = capfd.readouterr()
    line = captured.err.strip()
    obj = json.loads(line)
    assert obj["level"] == "INFO"
    assert "cid=aabbccdd started" in obj["msg"]
    assert "logger" in obj
    assert "ts" in obj


def test_json_extra_fields_surface_as_top_level(capfd):
    from baleobala.runtime.logging_config import configure_logging
    configure_logging(level=logging.INFO, fmt="json")
    logging.getLogger("baleobala.x").info("msg", extra={"cid": "deadbeef", "peer_id": 42})
    captured = capfd.readouterr()
    obj = json.loads(captured.err.strip())
    assert obj.get("cid") == "deadbeef"
    assert obj.get("peer_id") == 42


def test_configure_logging_respects_env_var(monkeypatch, capfd):
    monkeypatch.setenv("BALEOBALA_LOG_FORMAT", "json")
    from baleobala.runtime.logging_config import configure_logging
    configure_logging(level=logging.WARNING)
    logging.getLogger("baleobala.z").warning("test-warning")
    captured = capfd.readouterr()
    obj = json.loads(captured.err.strip())
    assert obj["level"] == "WARNING"


def test_configure_logging_idempotent_no_duplicate_handlers():
    from baleobala.runtime.logging_config import configure_logging
    configure_logging(level=logging.INFO, fmt="text")
    configure_logging(level=logging.INFO, fmt="text")
    assert len(logging.getLogger().handlers) == 1


def test_noisy_third_party_loggers_silenced():
    from baleobala.runtime.logging_config import configure_logging
    configure_logging(level=logging.DEBUG, fmt="text")
    assert logging.getLogger("urllib3").level == logging.WARNING
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("asyncio").level == logging.WARNING
