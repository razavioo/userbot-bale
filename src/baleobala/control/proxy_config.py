"""GUI proxy connection config persisted to ~/.config/baleobala/proxy.json.

Holds the user-entered relay peer_id, optional shared proxy secret, and the
local SOCKS5 listen port. Replaces the old coordinator_config module which
covered both coordinator and proxy state — the coordinator is gone.
"""

from __future__ import annotations

import json
from pathlib import Path

from baleobala.control.paths import config_dir

_CONFIG_FILE = "proxy.json"


def load_proxy_config() -> dict:
    """Return proxy config dict with keys: proxy_peer_id, proxy_secret, listen_port."""
    cfg_path = config_dir() / _CONFIG_FILE
    defaults: dict = {"proxy_peer_id": None, "proxy_secret": None, "listen_port": 10800}
    if not cfg_path.exists():
        return defaults
    try:
        data = json.loads(cfg_path.read_text("utf-8"))
        return {
            "proxy_peer_id": data.get("proxy_peer_id"),
            "proxy_secret": data.get("proxy_secret"),
            "listen_port": int(data.get("listen_port", 10800)),
        }
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return defaults


def save_proxy_config(
    *,
    peer_id: int | None = None,
    proxy_secret: str | None = None,
    listen_port: int = 10800,
) -> None:
    path = config_dir() / _CONFIG_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    if peer_id is not None:
        existing["proxy_peer_id"] = int(peer_id)
    if proxy_secret is not None:
        existing["proxy_secret"] = proxy_secret
    existing["listen_port"] = int(listen_port)
    path.write_text(json.dumps(existing, indent=2), "utf-8")
