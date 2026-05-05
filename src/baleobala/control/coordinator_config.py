"""Load coordinator peer_id from config file or environment."""

from __future__ import annotations

import json
import os
from pathlib import Path

from baleobala.control.paths import config_dir

_ENV_VAR = "BALEOBALA_COORDINATOR_PEER_ID"
_CONFIG_FILE = "coordinator.json"


def load_coordinator_peer_id() -> int | None:
    """Return the coordinator peer_id from env var or config file, or None."""
    env = os.environ.get(_ENV_VAR, "").strip()
    if env:
        try:
            return int(env)
        except ValueError:
            pass

    cfg_path = config_dir() / _CONFIG_FILE
    if not cfg_path.exists():
        return None
    try:
        data = json.loads(cfg_path.read_text("utf-8"))
        value = data.get("coordinator_peer_id")
        if value is not None:
            return int(value)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        pass
    return None


def save_coordinator_peer_id(peer_id: int) -> None:
    path = config_dir() / _CONFIG_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    existing["coordinator_peer_id"] = int(peer_id)
    path.write_text(json.dumps(existing, indent=2), "utf-8")
