from __future__ import annotations

import ssl

from baleobala.bale import ws_client


def test_build_ssl_context_defaults_to_none(monkeypatch) -> None:
    monkeypatch.delenv("BALE_SSL_CA_FILE", raising=False)
    monkeypatch.delenv("BALE_SSL_CA_PATH", raising=False)
    monkeypatch.delenv("BALE_WS_SSL_NO_VERIFY", raising=False)
    monkeypatch.delenv("BALE_SSL_NO_VERIFY", raising=False)
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)

    assert ws_client._build_ssl_context() is None


def test_build_ssl_context_uses_insecure_mode(monkeypatch) -> None:
    monkeypatch.setenv("BALE_WS_SSL_NO_VERIFY", "1")
    monkeypatch.delenv("BALE_SSL_CA_FILE", raising=False)
    monkeypatch.delenv("BALE_SSL_CA_PATH", raising=False)
    monkeypatch.delenv("BALE_SSL_NO_VERIFY", raising=False)
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)

    ctx = ws_client._build_ssl_context()
    assert ctx is not None
    assert ctx.verify_mode == ssl.CERT_NONE
    assert ctx.check_hostname is False
