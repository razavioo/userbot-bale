"""Filesystem locations for baleobala user state."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "baleobala"
APP_GROUP_IDENTIFIER = "group.com.baleobala.vpn"


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


def shared_container_dir() -> Path:
    override = os.environ.get("BALEOBALA_SHARED_CONTAINER") or os.environ.get("BALEOBALA_APP_GROUP_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Group Containers" / APP_GROUP_IDENTIFIER
    return app_dir()
