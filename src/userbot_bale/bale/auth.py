"""
Bale phone/SMS auth flow — live over gRPC-Web HTTP/2 (Phase 12).

Flow:
    1. POST StartPhoneAuth(phone, app_id, api_key, device_hash, title)
       → ResponseStartPhoneAuth { transaction_hash, is_registered, ... }
       Server sends SMS to the phone.
    2. User hands over the 5-digit code.
    3. POST ValidateCode(transaction_hash, code, is_jwt=1)
       → ResponseAuth { user, config, jwt? }
       JWT may be in Set-Cookie OR in the body field 4.
       For accounts where neither is present:
    4. POST GetJWTToken (empty body, same session_id header)
       → ResponseGetJWTToken { jwt: StringValue }

Live-verified 2026-04-20 with a real account. See bale/grpc_web.py for
the transport wire format.

Usage:
    auth = BaleAuth()
    tx = auth.start_phone_auth(989...)
    # user gets SMS; prompt for code
    session = auth.validate_code(tx, "12345")
    jwt = session.jwt          # use with BaleApiClient(jwt=...)
"""

from __future__ import annotations

import logging
import secrets
import uuid
from dataclasses import dataclass

from userbot_bale.bale.grpc_web import (
    GrpcWebClient,
    GrpcWebError,
    extract_access_token,
    extract_any_jwt_cookie,
)
from userbot_bale.bale.protos import (
    AUTH_SERVICE,
    RequestGetJWTToken,
    RequestSignUp,
    RequestStartPhoneAuth,
    RequestValidateCode,
    WEB_API_KEY,
    WEB_APP_ID,
    parse_get_jwt_token_response,
    parse_response_auth,
    parse_transaction_hash,
    parse_user_id_from_auth_response,
)


class NeedsSignUpException(Exception):
    """Raised by validate_code when Bale accepted the SMS code but the
    phone has no profile yet — caller must follow up with sign_up(name).
    Carries the same transaction_hash so the SignUp call reuses it."""

    def __init__(self, transaction_hash: str, detail: str = "") -> None:
        super().__init__(
            f"Bale account needs sign-up (provide a name). {detail}".rstrip()
        )
        self.transaction_hash = transaction_hash
        self.detail = detail

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuthSession:
    jwt: str
    # Opaque payload bytes so the caller can parse if they care about
    # the user profile that came back alongside the JWT.
    response_body: bytes


class BaleAuth:
    """Wrapper that walks the two-step phone auth flow.

    One instance should cover one full flow — it keeps the session_id
    consistent across the StartPhoneAuth → ValidateCode pair, which
    Bale's gateway uses to tie the two calls together."""

    def __init__(
        self,
        *,
        app_id: int = WEB_APP_ID,
        api_key: str = WEB_API_KEY,
        device_title: str = "userbot-bale",
        device_hash: bytes | None = None,
        session_id: str | None = None,
    ) -> None:
        self._app_id = app_id
        self._api_key = api_key
        self._device_title = device_title
        # Web client uses a random UUID as the device_hash payload;
        # the proto declares this as `bytes` but on the wire it's ASCII.
        self._device_hash = device_hash or str(uuid.uuid4()).encode("ascii")
        self._client = GrpcWebClient(session_id=session_id)
        self._last_tx: str | None = None

    def start_phone_auth(self, phone_number: int) -> str:
        """Trigger SMS to `phone_number` (int, no '+', digits only).
        Returns the `transaction_hash` to hand to validate_code().
        Raises GrpcWebError on rejection (rate-limit, invalid phone)."""
        req = RequestStartPhoneAuth(
            phone_number=int(phone_number),
            app_id=self._app_id,
            api_key=self._api_key,
            device_hash=self._device_hash,
            device_title=self._device_title,
        )
        resp = self._client.unary(AUTH_SERVICE, "StartPhoneAuth", req.encode())
        tx = parse_transaction_hash(resp.body)
        if not tx:
            raise RuntimeError(
                f"StartPhoneAuth returned no transaction_hash; body={resp.body[:80]!r}"
            )
        self._last_tx = tx
        log.info("StartPhoneAuth OK; transaction_hash=%s", tx)
        return tx

    def validate_code(self, code: str, *, transaction_hash: str | None = None) -> AuthSession:
        """Submit the SMS code. Returns AuthSession with the JWT on
        success. Raises GrpcWebError with grpc-status=3 and message
        `PHONE_CODE_INVALID` or `PHONE_CODE_EXPIRED` on user-side
        failures the caller should retry by re-running start_phone_auth."""
        tx = transaction_hash or self._last_tx
        if not tx:
            raise RuntimeError("no transaction_hash — call start_phone_auth() first")
        req = RequestValidateCode(transaction_hash=tx, code=code, is_jwt=True)
        try:
            resp = self._client.unary(AUTH_SERVICE, "ValidateCode", req.encode())
        except GrpcWebError as e:
            # PHONE_NUMBER_UNOCCUPIED means the SMS code was accepted but no
            # profile exists for this phone. Same tx must be reused for SignUp.
            if "PHONE_NUMBER_UNOCCUPIED" in (e.message or ""):
                raise NeedsSignUpException(
                    transaction_hash=tx,
                    detail="PHONE_NUMBER_UNOCCUPIED",
                ) from e
            raise
        jwt = extract_access_token(resp.set_cookies)
        if not jwt:
            jwt = extract_any_jwt_cookie(resp.set_cookies)
        if not jwt:
            # Fallback: Bale sometimes ships the JWT inside the
            # ResponseAuth protobuf (field 4) instead of a Set-Cookie —
            # observed on accounts whose gateway does not set the
            # access_token cookie on the HTTP response.
            parsed = parse_response_auth(resp.body)
            if parsed is not None:
                jwt = parsed.jwt
        if not jwt:
            # Most accounts: Bale requires a follow-up GetJWTToken call.
            # Try several gateway states seen live (2026-09-23): blank
            # access_token + user_id → HTTP 401 even after clearing the
            # blank cookie; session_id-only also 401 with user_id set.
            user_id = parse_user_id_from_auth_response(resp.body)
            log.warning(
                "No JWT in ValidateCode response; calling GetJWTToken "
                "(user_id=%s, cookies=%s)",
                user_id,
                self._client.cookie_summaries(),
            )
            jwt = self._get_jwt_token(user_id)
        if not jwt:
            raise RuntimeError(
                "Could not obtain JWT after ValidateCode + GetJWTToken. "
                "Check logs for details."
            )
        log.info("Auth OK; JWT length=%d", len(jwt))
        return AuthSession(jwt=jwt, response_body=resp.body)

    def _get_jwt_token(self, user_id: int | None) -> str | None:
        """Try GetJWTToken under several cookie/user_id combinations.

        Live-observed: HTTP 401 when blank access_token is present; also
        401 after clear_empty_cookies() with user_id set. Strategy order
        is cheapest-first so a working path short-circuits."""
        from userbot_bale.bale.grpc_web import GrpcWebError

        strategies: list[tuple[str, int | None, bool]] = [
            # (label, user_id, clear_blank_cookies)
            ("clear_blank+user_id", user_id, True),
            ("keep_blank+user_id", user_id, False),
            ("clear_blank+no_user", None, True),
            ("keep_blank+no_user", None, False),
        ]
        last_err: Exception | None = None
        for label, uid, clear in strategies:
            if clear:
                self._client.clear_empty_cookies()
            if uid is not None:
                self._client.set_user_id(uid)
            else:
                self._client.clear_user_id()
            try:
                log.warning("GetJWTToken attempt: %s", label)
                jwt_resp = self._client.unary(
                    AUTH_SERVICE, "GetJWTToken", RequestGetJWTToken().encode()
                )
            except GrpcWebError as e:
                log.warning("GetJWTToken attempt %s failed: %s", label, e)
                last_err = e
                continue
            except Exception as e:
                log.warning("GetJWTToken attempt %s error: %s", label, e)
                last_err = e
                continue
            jwt = parse_get_jwt_token_response(jwt_resp.body)
            if jwt:
                log.warning(
                    "GetJWTToken OK via %s; JWT length=%d", label, len(jwt)
                )
                return jwt
            log.warning(
                "GetJWTToken attempt %s: HTTP %d but no JWT in body",
                label, jwt_resp.http_status,
            )
        if last_err is not None:
            log.warning("GetJWTToken all attempts failed; last=%s", last_err)
        return None

    def sign_up(self, name: str, *, transaction_hash: str | None = None) -> AuthSession:
        """Complete sign-up for a fresh phone after validate_code raised
        NeedsSignUpException. Reuses the same transaction_hash and the
        same gRPC-Web session_id, then resolves a JWT via the same
        cookie / body / GetJWTToken cascade as validate_code."""
        tx = transaction_hash or self._last_tx
        if not tx:
            raise RuntimeError("no transaction_hash — call start_phone_auth() first")
        req = RequestSignUp(transaction_hash=tx, name=name)
        resp = self._client.unary(AUTH_SERVICE, "SignUp", req.encode())
        jwt = extract_access_token(resp.set_cookies)
        if not jwt:
            jwt = extract_any_jwt_cookie(resp.set_cookies)
        if not jwt:
            parsed = parse_response_auth(resp.body)
            if parsed is not None:
                jwt = parsed.jwt
        if not jwt:
            user_id = parse_user_id_from_auth_response(resp.body)
            log.warning(
                "No JWT in SignUp response; calling GetJWTToken "
                "(user_id=%s, cookies=%s)",
                user_id,
                self._client.cookie_summaries(),
            )
            jwt = self._get_jwt_token(user_id)
        if not jwt:
            raise RuntimeError(
                "SignUp succeeded but no JWT could be obtained "
                "(neither cookie, body, nor GetJWTToken returned one)."
            )
        log.info("SignUp OK; JWT length=%d", len(jwt))
        return AuthSession(jwt=jwt, response_body=resp.body)
