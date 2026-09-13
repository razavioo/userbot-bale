from __future__ import annotations

import base64
import json
import time

import pytest

from userbot_bale.vpn.jwt_util import (
    JwtExpiredError,
    claims,
    inspect,
    is_expired,
    require_unexpired,
    user_id,
    warn_if_near_expiry,
)
from userbot_bale.vpn.router import FailoverRouter, RouterChoice


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


def test_jwt_claim_helpers_read_bale_user_id_layouts():
    nested = _mkjwt(payload={"user_id": "77"})
    top_level = _mkjwt(user_id=88)

    assert claims(nested) == {"payload": {"user_id": "77"}}
    assert user_id(nested) == 77
    assert user_id(top_level) == 88
    assert user_id("not-a-jwt") is None


def test_is_expired_and_require_unexpired():
    fresh = _mkjwt(exp=int(time.time()) + 3600)
    stale = _mkjwt(exp=int(time.time()) - 10)
    no_exp = _mkjwt(sub="x")

    assert is_expired(stale) is True
    assert is_expired(fresh) is False
    # Token without exp: treated as non-expired (we can't decide).
    assert is_expired(no_exp) is False

    require_unexpired(fresh)  # no raise
    require_unexpired(no_exp)  # no raise
    with pytest.raises(JwtExpiredError) as ei:
        require_unexpired(stale)
    assert ei.value.exp is not None


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
