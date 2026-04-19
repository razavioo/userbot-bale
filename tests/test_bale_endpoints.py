"""Bale endpoint bootstrap parsing."""

from __future__ import annotations

from baleobala.bale.endpoints import Endpoint, parse_endpoints


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
