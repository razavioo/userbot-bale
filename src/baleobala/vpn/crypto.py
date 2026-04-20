"""
Authenticated encryption for VPN frames.

Wraps any Transport with ChaCha20-Poly1305. A 32-byte pre-shared key is
derived from a user-supplied passphrase via HKDF-SHA256. Per-frame
structure:

    ┌──────────────┬────────────────────────────┐
    │  12-byte     │  AES-GCM-style ciphertext  │
    │  random      │  = encrypt(plaintext) +    │
    │  nonce       │    16-byte Poly1305 tag    │
    └──────────────┴────────────────────────────┘

Overhead: 28 bytes per frame (nonce + tag). The wrapped transport's MTU
is reduced by 28 and the inner tunnel splits IP packets accordingly.

Why PSK + AEAD (and not something fancier)?
    * Bale's SFU terminates WebRTC TLS; it sees plaintext DataChannel
      payloads and audio. If Bale (or anyone with SFU access) is
      untrusted, the VPN payload has to be unreadable there.
    * Both endpoints of this VPN are already peers of each other in
      Bale — they can exchange a PSK out-of-band. No PKI required.
    * Frame-level AEAD gives confidentiality + integrity per IP packet,
      including replay detection via the 96-bit random nonce.
    * ChaCha20-Poly1305 is software-fast on machines without AES-NI —
      relevant for cheap VPS exit nodes.

Nonce choice: random 12 bytes per frame. At 2^48 frames you get a
negligible (2^-32) chance of a collision; a VPN session that ran at
1 Mpps for a year wouldn't reach that. Deterministic nonces keyed by
(sess_id, seq) would save 12 bytes but require per-peer counters that
survive hot-swap, and would leak traffic analysis of retransmits. Not
worth it.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

log = logging.getLogger(__name__)

NONCE_LEN = 12
TAG_LEN = 16
OVERHEAD = NONCE_LEN + TAG_LEN  # 28


def derive_key(passphrase: str, *, salt: bytes = b"baleobala-vpn-v1") -> bytes:
    """HKDF-SHA256 the passphrase into a 32-byte ChaCha20 key. Using a
    fixed project-global salt is fine here: the passphrase itself is
    the secret, and HKDF domain-separates us from any future reuse of
    the same passphrase for a different purpose."""
    kdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        info=b"vpn-frame-aead",
    )
    return kdf.derive(passphrase.encode("utf-8"))


class EncryptedTransport:
    """Wrap a Transport with ChaCha20-Poly1305 AEAD.

    Preserves the Transport protocol (send_bytes / recv_bytes / close /
    mtu / rate_hint). MTU is the inner transport's MTU minus 28 bytes
    of overhead, clamped to 1 — callers should avoid wrapping an
    already-tiny transport (the audio path at 128 B becomes 100 B,
    which is still workable)."""

    def __init__(self, inner, key: bytes) -> None:  # type: ignore[no-untyped-def]
        if len(key) != 32:
            raise ValueError("ChaCha20-Poly1305 key must be 32 bytes")
        self._inner = inner
        self._aead = ChaCha20Poly1305(key)
        base_mtu = getattr(inner, "mtu", 1500)
        self.mtu = max(1, base_mtu - OVERHEAD)
        self.rate_hint = float(getattr(inner, "rate_hint", 0.0))

    def send_bytes(self, data: bytes) -> None:
        if len(data) > self.mtu:
            raise ValueError(f"frame {len(data)} > encrypted mtu {self.mtu}")
        nonce = os.urandom(NONCE_LEN)
        ct = self._aead.encrypt(nonce, data, None)
        self._inner.send_bytes(nonce + ct)

    def recv_bytes(self, timeout: Optional[float] = None) -> Optional[bytes]:
        buf = self._inner.recv_bytes(timeout=timeout)
        if buf is None:
            return None
        if len(buf) < OVERHEAD:
            log.debug("drop short (%d bytes) — not a valid AEAD frame", len(buf))
            return None
        nonce, ct = buf[:NONCE_LEN], buf[NONCE_LEN:]
        try:
            return self._aead.decrypt(nonce, ct, None)
        except Exception:  # noqa: BLE001
            # Either a corrupted/bitflipped frame or an attacker's
            # injected bytes — either way, drop silently and let the
            # sender retransmit.
            log.debug("AEAD decrypt failed — dropping %d-byte frame", len(buf))
            return None

    def close(self) -> None:
        self._inner.close()
