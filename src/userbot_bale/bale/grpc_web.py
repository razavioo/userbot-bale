"""
Minimal gRPC-Web-over-HTTP/2 client for Bale's auth RPCs.

Unlike the app-tier RPCs (carried over a JWT-authenticated WebSocket
on /ws/), Bale's auth endpoints live on the same host but speak
gRPC-Web-over-HTTP2 — and they must accept unauthenticated calls so
new sessions can bootstrap. Live-verified 2026-04-20: StartPhoneAuth,
ValidateCode, and the JWT lands as a `Set-Cookie: access_token=...`
on the ValidateCode success response.

Wire format (per gRPC-Web spec):
    request  : 0x00 || u32-be-len || protobuf-body
    response : [0x00||u32-len||data-frame] [0x80||u32-len||trailer]
        trailer contains `grpc-status: 0\r\ngrpc-message: ...`

Bale also requires a set of custom headers on every request that the
web client sends — app_version, browser_type, session_id, mt_* duplicates,
and `x-grpc-web: 1`. We reproduce them exactly.
"""

from __future__ import annotations

import logging
import struct
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

log = logging.getLogger(__name__)

DEFAULT_HOST = "https://next-ws.bale.ai"
DEFAULT_SESSION_ID = str(int(time.time() * 1000))

# Client-info headers observed in every web.bale.ai gRPC-Web request.
# Bale's gateway validates at least `app_version` and `x-grpc-web`.
DEFAULT_HEADERS: Dict[str, str] = {
    "content-type": "application/grpc-web+proto",
    "x-grpc-web": "1",
    "app_version": "151668",
    "browser_type": "1",
    "browser_version": "147.0.0.0",
    "os_type": "4",
    "mt_app_version": "151668",
    "mt_browser_type": "1",
    "mt_browser_version": "147.0.0.0",
    "mt_os_type": "4",
    "origin": "https://web.bale.ai",
    "referer": "https://web.bale.ai/",
    "accept": "*/*",
    "accept-language": "en-US,en;q=0.9,fa;q=0.8",
    "accept-encoding": "gzip, deflate, br",
    "sec-ch-ua": '"Chromium";v="147", "Not/A)Brand";v="24", "Google Chrome";v="147"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Linux"',
    "sec-fetch-site": "same-site",
    "sec-fetch-mode": "cors",
    "sec-fetch-dest": "empty",
    "user-agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
    ),
}


class GrpcWebError(RuntimeError):
    def __init__(self, status: int, message: str, http_status: int = 0) -> None:
        super().__init__(f"grpc-status={status} grpc-message={message!r}")
        self.status = status
        self.message = message
        self.http_status = http_status


@dataclass
class GrpcWebResponse:
    body: bytes              # protobuf payload (without the 5-byte envelope)
    set_cookies: list[str] = field(default_factory=list)
    http_status: int = 200


def _pack_frame(body: bytes, *, trailer: bool = False) -> bytes:
    flags = 0x80 if trailer else 0x00
    return bytes([flags]) + struct.pack(">I", len(body)) + body


def _unpack_frames(buf: bytes):
    """Iterate (flags, body) pairs across a gRPC-Web response."""
    off = 0
    while off + 5 <= len(buf):
        flags = buf[off]
        length = struct.unpack(">I", buf[off + 1 : off + 5])[0]
        yield flags, buf[off + 5 : off + 5 + length]
        off += 5 + length


class GrpcWebClient:
    """Unary gRPC-Web caller. Keeps a single persistent httpx HTTP/2
    client so that cookies set by one call (e.g. ValidateCode) are
    automatically carried into the next (e.g. GetJWTToken)."""

    def __init__(
        self,
        *,
        host: str = DEFAULT_HOST,
        session_id: str | None = None,
        user_id: int | None = None,
        extra_headers: Dict[str, str] | None = None,
        timeout: float = 30.0,
    ) -> None:
        try:
            import httpx
        except ImportError as e:
            raise ImportError(
                "grpc-web auth requires httpx. Install: pip install 'httpx[http2]'"
            ) from e
        self._host = host
        self._session_id = session_id or DEFAULT_SESSION_ID
        self._user_id = user_id
        self._extra = dict(extra_headers or {})
        self._timeout = timeout
        # Use HTTP/1.1 — HTTP/2 fingerprinting (SETTINGS frame) differs
        # from Chrome's and causes Bale's WAF to block authenticated calls.
        self._http = httpx.Client(http2=False, timeout=timeout)

    def set_user_id(self, user_id: int) -> None:
        self._user_id = user_id

    def clear_empty_cookies(self) -> None:
        """Drop blank cookies (e.g. ``access_token=`` from StartPhoneAuth).

        ValidateCode must echo the blank cookie for flow continuity, but
        sending ``access_token=`` on GetJWTToken makes the gateway treat the
        call as authenticated-with-invalid-JWT and answer HTTP 401."""
        for cookie in list(self._http.cookies.jar):
            if not cookie.value:
                self._http.cookies.delete(
                    cookie.name, domain=cookie.domain, path=cookie.path
                )

    def cookie_summaries(self) -> list[str]:
        """Safe cookie diagnostics: name, domain, path, value length only."""
        out: list[str] = []
        for cookie in self._http.cookies.jar:
            out.append(
                f"{cookie.name}@{cookie.domain}{cookie.path}"
                f":len={len(cookie.value or '')}"
            )
        return out

    def clear_user_id(self) -> None:
        self._user_id = None

    def close(self) -> None:
        self._http.close()

    def unary(
        self,
        service: str,
        method: str,
        payload: bytes,
        *,
        jwt: Optional[str] = None,
    ) -> GrpcWebResponse:
        url = f"{self._host}/{service}/{method}"
        headers = dict(DEFAULT_HEADERS)
        headers["session_id"] = self._session_id
        headers["mt_session_id"] = self._session_id
        if self._user_id is not None:
            headers["user_id"] = str(self._user_id)
        headers.update(self._extra)

        if jwt:
            self._http.cookies.set("access_token", jwt)

        log.debug("gRPC-Web POST %s/%s (%d bytes)", service, method, len(payload))
        r = self._http.post(
            url,
            content=_pack_frame(payload),
            headers=headers,
        )

        grpc_status = r.headers.get("grpc-status")
        grpc_message = r.headers.get("grpc-message", "")
        set_cookies = list(r.headers.get_list("set-cookie"))

        # Force-store ALL Set-Cookie values from the response, including
        # expired (Max-Age=0) ones that httpx discards. The Bale server
        # sends `access_token=; Max-Age=0` on StartPhoneAuth to reset any
        # prior session; a browser stores this empty cookie and echoes it
        # back in ValidateCode, signalling flow continuity. Without it the
        # server won't set the real JWT cookie in the ValidateCode response.
        # Honor Domain= from the header (Bale often uses Domain=bale.ai) so
        # cross-subdomain cookies still attach to next-ws.bale.ai requests.
        host = self._host.split("://", 1)[-1].split("/", 1)[0]
        for sc in set_cookies:
            head = sc.split(";", 1)[0].strip()
            if "=" not in head:
                continue
            name, value = head.split("=", 1)
            name = name.strip()
            value = value.strip()
            domain = host
            for part in sc.split(";")[1:]:
                part = part.strip()
                if part.lower().startswith("domain="):
                    raw = part.split("=", 1)[1].strip().lstrip(".")
                    if raw:
                        domain = raw
            # Also pin the request host so the cookie matches either form.
            self._http.cookies.set(name, value, domain=domain)
            if domain != host:
                self._http.cookies.set(name, value, domain=host)

        # Synthesize Set-Cookie list from jar for extract_access_token().
        if not set_cookies and r.cookies:
            for name, value in r.cookies.items():
                set_cookies.append(f"{name}={value}")
        log.debug(
            "gRPC-Web %s/%s: http=%d cookies=%s",
            service, method, r.status_code, self.cookie_summaries(),
        )
        body = b""
        trailer_text = ""
        for flags, frame_body in _unpack_frames(r.content):
            if flags & 0x80:
                trailer_text = frame_body.decode("latin-1", errors="replace")
                # Trailer typically: "grpc-status: N\r\ngrpc-message: MSG\r\n"
                for line in trailer_text.split("\r\n"):
                    if line.lower().startswith("grpc-status:"):
                        grpc_status = line.split(":", 1)[1].strip()
                    elif line.lower().startswith("grpc-message:"):
                        grpc_message = line.split(":", 1)[1].strip()
            else:
                body = frame_body

        status_int = int(grpc_status) if grpc_status and grpc_status.isdigit() else None
        if r.status_code >= 400 or (status_int is not None and status_int != 0):
            raise GrpcWebError(
                status=status_int if status_int is not None else -1,
                message=grpc_message or f"http {r.status_code}",
                http_status=r.status_code,
            )
        return GrpcWebResponse(
            body=body, set_cookies=set_cookies, http_status=r.status_code
        )


def extract_access_token(set_cookies: list[str]) -> Optional[str]:
    """Pull the JWT out of `Set-Cookie: access_token=<jwt>; Path=...`.
    Returns None on cookie-delete (empty value) or absence."""
    for sc in set_cookies:
        head = sc.split(";", 1)[0].strip()
        if head.startswith("access_token="):
            value = head.split("=", 1)[1]
            if value:
                return value
    return None


def extract_any_jwt_cookie(set_cookies: list[str]) -> Optional[str]:
    """Find a JWT-shaped value in any Set-Cookie (not just access_token)."""
    for sc in set_cookies:
        head = sc.split(";", 1)[0].strip()
        if "=" not in head:
            continue
        value = head.split("=", 1)[1].strip()
        if value.startswith("eyJ") and len(value) >= 50:
            return value
    return None
