"""Filesystem locations for baleobala user state."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "baleobala"


def app_dir() -> Path:
    override = os.environ.get("BALEOBALA_HOME")
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if sys.platform.startswith("linux"):
        base = os.environ.get("XDG_CONFIG_HOME")
        if base:
            return Path(base).expanduser() / APP_NAME
        return Path.home() / ".config" / APP_NAME
    return Path.home() / f".{APP_NAME}"


def config_dir() -> Path:
    return app_dir()


def data_dir() -> Path:
    return app_dir() / "state"
