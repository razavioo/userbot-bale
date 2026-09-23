from __future__ import annotations

import struct

import pytest

from userbot_bale.bale.grpc_web import (
    GrpcWebClient,
    _pack_frame,
    _unpack_frames,
    extract_access_token,
    extract_any_jwt_cookie,
)


def test_pack_frame_single():
    assert _pack_frame(b"abc") == b"\x00\x00\x00\x00\x03abc"


def test_pack_frame_trailer():
    assert _pack_frame(b"grpc-status: 0", trailer=True)[0] == 0x80


def test_unpack_frames_data_plus_trailer():
    buf = (
        b"\x00\x00\x00\x00\x05hello"
        + b"\x80\x00\x00\x00\x10grpc-status: 0\r\n"
    )
    frames = list(_unpack_frames(buf))
    assert len(frames) == 2
    assert frames[0] == (0x00, b"hello")
    assert frames[1][0] == 0x80
    assert b"grpc-status" in frames[1][1]


def test_extract_access_token_finds_jwt():
    cookies = [
        "_ga=GA1.1.foo; Path=/",
        "access_token=eyJHEADER.eyJPAYLOAD.sig; Path=/; Domain=bale.ai; HttpOnly",
    ]
    assert extract_access_token(cookies) == "eyJHEADER.eyJPAYLOAD.sig"


def test_extract_access_token_handles_logout():
    cookies = [
        "access_token=; Path=/; Max-Age=0",
    ]
    assert extract_access_token(cookies) is None


def test_extract_access_token_missing():
    assert extract_access_token([]) is None


def test_extract_any_jwt_cookie_finds_non_access_token_name():
    jwt = "eyJhbGciOiJIUzI1NiJ9." + "A" * 60 + ".sig"
    assert extract_any_jwt_cookie([f"session_key={jwt}; Path=/"]) == jwt


def test_extract_any_jwt_cookie_ignores_empty_and_short_values():
    assert extract_any_jwt_cookie(["access_token=; Max-Age=0"]) is None
    assert extract_any_jwt_cookie(["other=not-a-jwt"]) is None


def test_clear_empty_cookies_drops_blank_access_token():
    client = GrpcWebClient()
    try:
        client._http.cookies.set("access_token", "", domain="next-ws.bale.ai")
        client._http.cookies.set("keep_me", "value", domain="next-ws.bale.ai")
        client.clear_empty_cookies()
        names = {cookie.name for cookie in client._http.cookies.jar}
        assert "access_token" not in names
        assert "keep_me" in names
    finally:
        client.close()
