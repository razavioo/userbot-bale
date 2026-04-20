from __future__ import annotations

import base64
import json
import time

import pytest

from baleobala.vpn.jwt_util import inspect, warn_if_near_expiry
from baleobala.vpn.router import FailoverRouter, RouterChoice


def _mkjwt(**claims) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"{header}.{body}."


def test_jwt_inspect_reads_exp():
    jwt = _mkjwt(exp=int(time.time()) + 3600)
    info = inspect(jwt)
    assert info.exp is not None
    assert 0 < (info.seconds_until_expiry or 0) <= 3600


def test_jwt_inspect_handles_garbage():
    info = inspect("not-a-jwt")
    assert info.exp is None
    assert info.seconds_until_expiry is None


def test_warn_near_expiry_doesnt_raise(caplog):
    jwt = _mkjwt(exp=int(time.time()) + 100)  # expires soon
    warn_if_near_expiry(jwt, days=1.0)
    assert any("JWT expires" in r.message for r in caplog.records)


def test_router_picks_first_working():
    calls = []

    def bad():
        calls.append("bad")
        raise RuntimeError("nope")

    class Good:
        mtu = 1
        def __init__(self) -> None:
            calls.append("good")

    r = FailoverRouter([
        RouterChoice("a", bad),
        RouterChoice("b", Good),
    ])
    name, t = r.build()
    assert name == "b"
    assert isinstance(t, Good)
    assert calls == ["bad", "good"]


def test_router_raises_when_all_fail():
    r = FailoverRouter([
        RouterChoice("a", lambda: (_ for _ in ()).throw(RuntimeError("x"))),
        RouterChoice("b", lambda: (_ for _ in ()).throw(RuntimeError("y"))),
    ])
    with pytest.raises(RuntimeError):
        r.build()
