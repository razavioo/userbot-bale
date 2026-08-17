from __future__ import annotations

import base64
import json
import sys
import types
from pathlib import Path

from baleobala.control.auth import AuthStore
from baleobala.control.keychain import KeyringSecretBackend


def test_keyring_backend_delegates_to_the_platform_credential_manager(monkeypatch) -> None:
    values: dict[tuple[str, str], str] = {}

    class PasswordDeleteError(Exception):
        pass

    fake = types.SimpleNamespace(
        get_password=lambda service, name: values.get((service, name)),
        set_password=lambda service, name, value: values.__setitem__((service, name), value),
        delete_password=lambda service, name: values.pop((service, name)),
        errors=types.SimpleNamespace(PasswordDeleteError=PasswordDeleteError),
    )
    monkeypatch.setitem(sys.modules, "keyring", fake)
    backend = KeyringSecretBackend(service="test.baleobala")

    backend.save("auth.jwt", "secret")
    assert backend.load("auth.jwt") == "secret"
    backend.delete("auth.jwt")
    assert backend.load("auth.jwt") is None


def test_auth_store_exports_a_raw_jwt_only_when_explicitly_enabled(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_SECRET_BACKEND", "file")
    store = AuthStore(tmp_path / "auth.json")
    exported: list[str] = []
    monkeypatch.setattr(store, "_export_to_accounts", exported.append)

    store.save_jwt("header.payload.signature")
    assert exported == []

    monkeypatch.setenv("BALEOBALA_EXPORT_ACCOUNT_JWT", "1")
    store.save_jwt("second.header.signature")
    assert exported == ["second.header.signature"]


def test_auth_record_recovers_user_id_from_browser_jwt() -> None:
    encoded = base64.urlsafe_b64encode(
        json.dumps({"payload": {"user_id": 91}}).encode("utf-8")
    ).decode("ascii").rstrip("=")
    store = AuthStore(Path("auth-test.json"))

    record = store._record_for_jwt(f"header.{encoded}.signature")

    assert record.user_id == 91
