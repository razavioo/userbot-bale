"""Filesystem locations for userbot-bale user state."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "userbot-bale"
APP_GROUP_IDENTIFIER = os.environ.get("USERBOT_BALE_APP_GROUP_IDENTIFIER", "group.com.userbot_bale.vpn")


def is_android_runtime() -> bool:
    override = os.environ.get("USERBOT_BALE_FORCE_ANDROID", "").strip().lower()
    if override:
        return override in {"1", "true", "yes", "on"}
    if sys.platform != "linux":
        return False
    return any(
        os.environ.get(name)
        for name in (
            "ANDROID_ROOT",
            "ANDROID_DATA",
            "ANDROID_ARGUMENT",
        )
    )


def app_dir() -> Path:
    override = os.environ.get("USERBOT_BALE_HOME")
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if sys.platform.startswith("linux"):
        base = os.environ.get("XDG_CONFIG_HOME")
        if base:
            return Path(base).expanduser() / APP_NAME
        return Path.home() / ".config" / APP_NAME
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base).expanduser() / APP_NAME
    return Path.home() / f".{APP_NAME}"


def config_dir() -> Path:
    return app_dir()


def data_dir() -> Path:
    return app_dir() / "state"


def shared_container_dir() -> Path:
    override = (
        os.environ.get("USERBOT_BALE_SHARED_CONTAINER")
        or os.environ.get("USERBOT_BALE_APP_GROUP_DIR")
        or os.environ.get("USERBOT_BALE_HOME")
    )
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Group Containers" / APP_GROUP_IDENTIFIER
    return app_dir()
