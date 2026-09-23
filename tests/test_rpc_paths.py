from __future__ import annotations

import pytest

from userbot_bale.mcp.rpc_paths import SERVICE_PATHS, list_service_paths


def test_inventory_shape() -> None:
    assert len(SERVICE_PATHS) == 46
    assert sum(len(v) for v in SERVICE_PATHS.values()) == 308
    assert all(isinstance(v, tuple) for v in SERVICE_PATHS.values())
    assert all(
        path.startswith("/bale.")
        for methods in SERVICE_PATHS.values()
        for path in methods
    )


def test_list_service_paths_filters() -> None:
    auth = list_service_paths(service="auth.v1", limit=50)
    assert auth["total"] == 16
    assert auth["count"] == 16
    assert all(p.startswith("/bale.auth.v1.Auth/") for p in auth["paths"])

    history = list_service_paths(query="LoadHistory", limit=10)
    assert history["total"] >= 1
    assert any("LoadHistory" in p for p in history["paths"])

    limited = list_service_paths(limit=5)
    assert limited["count"] == 5
    assert limited["total"] == 308


def test_list_service_paths_rejects_bad_limit() -> None:
    with pytest.raises(ValueError):
        list_service_paths(limit=0)
    with pytest.raises(ValueError):
        list_service_paths(limit=201)
