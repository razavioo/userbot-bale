"""Bale endpoint bootstrap parsing."""

from __future__ import annotations

import pytest

from userbot_bale.bale.endpoints import (
    ENDPOINTS_HTTPS_URL,
    ENDPOINTS_URL,
    Endpoint,
    fetch_endpoints,
    parse_endpoints,
)


SAMPLE = """r
tls@6d9ba5c5c665b0a7066682a05329f2a9c4c11aa08c9c548ebd33b9d06bf6e444://rpc-ssl-c002.bale.ai@2.189.68.117:443#1013
tcp@6d9ba5c5c665b0a7066682a05329f2a9c4c11aa08c9c548ebd33b9d06bf6e444://rpc-c002.bale.ai@2.189.68.106:443#1014
"""


def test_parse_live_response() -> None:
    endpoints = parse_endpoints(SAMPLE)
    assert len(endpoints) == 2
    tls, tcp = endpoints
    assert tls.scheme == "tls"
    assert tls.host == "rpc-ssl-c002.bale.ai"
    assert tls.ip == "2.189.68.117"
    assert tls.port == 443
    assert tls.id == 1013
    assert len(tls.pin) == 64
    assert tcp.scheme == "tcp"
    assert tcp.host == "rpc-c002.bale.ai"
    assert tcp.id == 1014


def test_parse_skips_leading_r_and_blanks() -> None:
    endpoints = parse_endpoints("r\n\n\n")
    assert endpoints == []


def test_parse_skips_malformed_lines() -> None:
    body = "garbage://nope\n" + SAMPLE
    endpoints = parse_endpoints(body)
    assert len(endpoints) == 2


def test_parse_without_trailing_id() -> None:
    line = (
        "tls@" + "a" * 64 + "://host.example@1.2.3.4:443"
    )
    endpoints = parse_endpoints(line)
    assert len(endpoints) == 1
    assert endpoints[0].id == 0


class _FakeResponse:
    def __init__(self, body: str) -> None:
        self._body = body.encode("utf-8")

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def read(self) -> bytes:
        return self._body


def test_fetch_endpoints_uses_primary_bootstrap_response(monkeypatch) -> None:
    calls: list[tuple[str, float, object]] = []
    sentinel_context = object()

    def fake_context() -> object:
        return sentinel_context

    def fake_urlopen(req, timeout: float, context) -> _FakeResponse:
        calls.append((req.full_url, timeout, context))
        return _FakeResponse(SAMPLE)

    monkeypatch.setattr("userbot_bale.bale.endpoints.ssl._create_unverified_context", fake_context)
    monkeypatch.setattr("userbot_bale.bale.endpoints.urlopen", fake_urlopen)

    endpoints = fetch_endpoints(timeout=1.25)

    assert endpoints == parse_endpoints(SAMPLE)
    assert calls == [(ENDPOINTS_URL, 1.25, sentinel_context)]


def test_fetch_endpoints_falls_back_after_bootstrap_timeouts(monkeypatch) -> None:
    attempts: list[str] = []

    def fake_urlopen(req, timeout: float, context) -> _FakeResponse:
        del timeout, context
        attempts.append(req.full_url)
        raise TimeoutError("read timed out")

    monkeypatch.setattr("userbot_bale.bale.endpoints.urlopen", fake_urlopen)

    endpoints = fetch_endpoints(timeout=0.5)

    assert attempts == [ENDPOINTS_URL, ENDPOINTS_HTTPS_URL]
    assert len(endpoints) >= 1
    assert all(item.host.endswith(".bale.ai") for item in endpoints)


def test_fetch_endpoints_raises_last_error_when_fallback_disabled(monkeypatch) -> None:
    def fake_urlopen(req, timeout: float, context) -> _FakeResponse:
        del req, timeout, context
        raise TimeoutError("read timed out")

    monkeypatch.setattr("userbot_bale.bale.endpoints.urlopen", fake_urlopen)

    with pytest.raises(TimeoutError, match="read timed out"):
        fetch_endpoints(timeout=0.5, allow_fallback=False)


def test_fetch_endpoints_falls_back_when_bootstrap_body_is_empty(monkeypatch) -> None:
    attempts: list[str] = []

    def fake_urlopen(req, timeout: float, context) -> _FakeResponse:
        del timeout, context
        attempts.append(req.full_url)
        return _FakeResponse("r\n\n")

    monkeypatch.setattr("userbot_bale.bale.endpoints.urlopen", fake_urlopen)

    endpoints = fetch_endpoints()

    assert attempts == [ENDPOINTS_URL, ENDPOINTS_HTTPS_URL]
    assert len(endpoints) >= 1
    assert endpoints[0].scheme == "tls"
