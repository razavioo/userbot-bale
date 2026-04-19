"""
Bale auth flow (phone + SMS + JWT).

Status: scaffolded. See baleobala.bale.api for why the transport layer
is not wired yet.

The flow, mapped to the decompiled protos:

    1. POST RequestStartPhoneAuth(phone, api_key, api_hash, device_info)
       → ResponseStartPhoneAuth { transaction_hash, is_registered }
    2. User receives SMS.
    3. POST RequestValidateCode(transaction_hash, code)
       → ResponseAuth { user, config, jwt_token }
       OR (if unregistered) the caller must run RequestSignUp first.
    4. On every subsequent session, RequestGetJWTToken refreshes the
       bearer used for REST-style endpoints (some Bale services accept
       raw MTProto, some a JWT-over-HTTPS gateway).

The auth secrets (api_key, api_hash) are baked into the APK. They are
visible to any jadx user and are not meant to be cryptographically
protected — they identify the *client*, not the *user*. We'll extract
them from the decompiled ir.nasim.features.onboarding or the
BuildConfig equivalent when the transport layer is ready to use them.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AuthSession:
    user_id: int
    jwt_token: str
    auth_key: bytes  # MTProto auth-key negotiated during the handshake


class BaleAuth:
    def start_phone_auth(self, phone: str) -> str:
        """Return transaction_hash from ResponseStartPhoneAuth."""
        raise NotImplementedError(
            "Nasim-MTProto transport is not yet implemented. See "
            "docs/BALE_RE_NOTES.md for the completion plan."
        )

    def validate_code(self, transaction_hash: str, code: str) -> AuthSession:
        raise NotImplementedError(
            "Nasim-MTProto transport is not yet implemented. See "
            "docs/BALE_RE_NOTES.md for the completion plan."
        )
