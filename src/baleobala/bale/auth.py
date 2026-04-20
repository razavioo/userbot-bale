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

from baleobala.bale.grpc_web import (
    GrpcWebClient,
    GrpcWebError,
    extract_access_token,
)
from baleobala.bale.protos import (
    AUTH_SERVICE,
    RequestGetJWTToken,
    RequestStartPhoneAuth,
    RequestValidateCode,
    WEB_API_KEY,
    WEB_APP_ID,
    parse_get_jwt_token_response,
    parse_response_auth,
    parse_transaction_hash,
    parse_user_id_from_auth_response,
)

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
        device_title: str = "baleobala",
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
        resp = self._client.unary(AUTH_SERVICE, "ValidateCode", req.encode())
        jwt = extract_access_token(resp.set_cookies)
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
            # Set the user_id on the same persistent client so the server
            # can tie this request to the ValidateCode session.
            user_id = parse_user_id_from_auth_response(resp.body)
            log.warning(
                "No JWT in ValidateCode response; calling GetJWTToken (user_id=%s)",
                user_id,
            )
            try:
                if user_id is not None:
                    self._client.set_user_id(user_id)
                jwt_resp = self._client.unary(
                    AUTH_SERVICE, "GetJWTToken", RequestGetJWTToken().encode()
                )
                jwt = parse_get_jwt_token_response(jwt_resp.body)
                if jwt:
                    log.warning("GetJWTToken OK; JWT length=%d", len(jwt))
            except Exception as e:
                log.warning("GetJWTToken failed: %s", e)
        if not jwt:
            raise RuntimeError(
                "Could not obtain JWT after ValidateCode + GetJWTToken. "
                "Check logs for details."
            )
        log.info("Auth OK; JWT length=%d", len(jwt))
        return AuthSession(jwt=jwt, response_body=resp.body)
