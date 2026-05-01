from __future__ import annotations

import ssl
from types import SimpleNamespace

import pytest

from baleobala.bale import ws_client


def test_build_ssl_context_defaults_to_none(monkeypatch) -> None:
    monkeypatch.delenv("BALE_SSL_CA_FILE", raising=False)
    monkeypatch.delenv("BALE_SSL_CA_PATH", raising=False)
    monkeypatch.delenv("BALE_WS_SSL_NO_VERIFY", raising=False)
    monkeypatch.delenv("BALE_SSL_NO_VERIFY", raising=False)
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)

    config = ws_client.WsTlsConfig.from_sources()

    assert ws_client._build_ssl_context(config) is None


def test_build_ssl_context_uses_insecure_mode(monkeypatch) -> None:
    monkeypatch.setenv("BALE_WS_SSL_NO_VERIFY", "1")
    monkeypatch.delenv("BALE_SSL_CA_FILE", raising=False)
    monkeypatch.delenv("BALE_SSL_CA_PATH", raising=False)
    monkeypatch.delenv("BALE_SSL_NO_VERIFY", raising=False)
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)

    config = ws_client.WsTlsConfig.from_sources(allow_insecure_debug=True)
    ctx = ws_client._build_ssl_context(config)
    assert ctx is not None
    assert ctx.verify_mode == ssl.CERT_NONE
    assert ctx.check_hostname is False


def test_tls_config_rejects_insecure_without_debug_permission(monkeypatch) -> None:
    monkeypatch.setenv("BALE_WS_SSL_NO_VERIFY", "1")

    with pytest.raises(ws_client.WsTlsPolicyError) as excinfo:
        ws_client.WsTlsConfig.from_sources()

    assert excinfo.value.failure_class == "tls_insecure_rejected"


def test_build_ssl_context_loads_custom_ca(monkeypatch) -> None:
    monkeypatch.delenv("BALE_SSL_CA_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    config = ws_client.WsTlsConfig(ca_file="/tmp/custom-ca.pem")
    calls: list[tuple[str | None, str | None]] = []

    real_create_default_context = ssl.create_default_context

    def fake_create_default_context():
        ctx = real_create_default_context()
        real_load = ctx.load_verify_locations

        def fake_load_verify_locations(*, cafile=None, capath=None, cadata=None):
            calls.append((cafile, capath))
            return real_load(cadata="-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n")

        ctx.load_verify_locations = fake_load_verify_locations  # type: ignore[method-assign]
        return ctx

    monkeypatch.setattr(ws_client.ssl, "create_default_context", fake_create_default_context)

    with pytest.raises(ws_client.WsTlsPolicyError):
        ws_client._build_ssl_context(config)

    assert calls == [("/tmp/custom-ca.pem", None)]


def test_build_ssl_context_reports_ca_load_failure() -> None:
    config = ws_client.WsTlsConfig(ca_file="/definitely/missing-ca.pem")

    with pytest.raises(ws_client.WsTlsPolicyError) as excinfo:
        ws_client._build_ssl_context(config)

    assert excinfo.value.failure_class == "tls_ca_load_failed"


def test_ws_client_logs_tls_verify_failures(monkeypatch, caplog) -> None:
    caplog.set_level("WARNING")

    class DummyConnect:
        async def __aenter__(self):
            raise ssl.SSLCertVerificationError("verify failed")

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(
        ws_client,
        "websockets",
        SimpleNamespace(connect=lambda *args, **kwargs: DummyConnect()),
    )

    client = ws_client.WsClient(jwt="secret-jwt")
    with pytest.raises(RuntimeError) as excinfo:
        client.start(timeout=0.1)
    client.stop()

    assert "WS failed to connect" in str(excinfo.value)
    assert "tls_verify_failed" in caplog.text
    assert "secret-jwt" not in caplog.text


def test_ws_client_disables_auto_proxy(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class DummyConnect:
        async def __aenter__(self):
            raise OSError("stop after connect kwargs captured")

        async def __aexit__(self, exc_type, exc, tb):
            return False

    def fake_connect(uri, *, proxy=True, **kwargs):  # noqa: ANN001
        captured["proxy"] = proxy
        return DummyConnect()

    monkeypatch.setattr(
        ws_client,
        "websockets",
        SimpleNamespace(connect=fake_connect),
    )

    client = ws_client.WsClient(jwt="secret-jwt")
    with pytest.raises(RuntimeError):
        client.start(timeout=0.1)
    client.stop()

    assert captured["proxy"] is None
