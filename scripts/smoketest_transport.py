"""
Live smoketest: connect to Bale's production TLS endpoint and probe
which SHA-256 variant the pin represents.

Run as a standalone script (not via pytest) because it hits the live
network and the outcome is informational, not pass/fail for CI.

    python scripts/smoketest_transport.py
"""

from __future__ import annotations

import hashlib
import socket
import ssl
import sys
from pathlib import Path

# Allow running without install
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from userbot_bale.bale.endpoints import fetch_endpoints  # noqa: E402


def sha256_hex(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def try_import_cryptography():
    try:
        from cryptography import x509  # type: ignore
        from cryptography.hazmat.primitives import serialization  # type: ignore
        return x509, serialization
    except ImportError:
        return None, None


def main() -> int:
    endpoints = fetch_endpoints()
    print(f"Fetched {len(endpoints)} endpoint(s) from ep.bale.ai")
    for ep in endpoints:
        print(f"  {ep.scheme}://{ep.host}@{ep.ip}:{ep.port} pin={ep.pin[:16]}...")

    tls_ep = next((e for e in endpoints if e.scheme == "tls"), None)
    if tls_ep is None:
        print("no TLS endpoint available", file=sys.stderr)
        return 1

    print(f"\nConnecting to {tls_ep.host}:{tls_ep.port} (IP {tls_ep.ip})...")
    try:
        ip = socket.gethostbyname(tls_ep.host)
    except OSError:
        ip = tls_ep.ip
    raw = socket.create_connection((ip, tls_ep.port), timeout=10)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    sslsock = ctx.wrap_socket(raw, server_hostname=tls_ep.host)
    cert_der = sslsock.getpeercert(binary_form=True)
    sslsock.close()
    assert cert_der is not None

    print(f"\nReceived cert: {len(cert_der)} bytes")
    expected = tls_ep.pin.lower()
    print(f"Expected pin: {expected}")

    # Variant 1: SHA-256 of full cert DER
    v1 = sha256_hex(cert_der)
    match_cert = "MATCH" if v1 == expected else "no"
    print(f"\nSHA-256(cert-DER)        = {v1}   [{match_cert}]")

    # Variants 2/3/4 need the x509 library to extract SPKI / pubkey DER
    x509, serialization = try_import_cryptography()
    if x509 is None:
        print("\n(install `cryptography` to probe SPKI + pubkey variants)")
        return 0 if v1 == expected else 2

    cert = x509.load_der_x509_certificate(cert_der)
    spki_der = cert.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    v2 = sha256_hex(spki_der)
    match_spki = "MATCH" if v2 == expected else "no"
    print(f"SHA-256(SPKI-DER)        = {v2}   [{match_spki}]")

    # Raw pubkey bytes (RSA modulus or EC point)
    pk = cert.public_key()
    try:
        from cryptography.hazmat.primitives.asymmetric import rsa, ec
        if isinstance(pk, rsa.RSAPublicKey):
            numbers = pk.public_numbers()
            n_bytes = numbers.n.to_bytes(
                (numbers.n.bit_length() + 7) // 8, "big"
            )
            v3 = sha256_hex(n_bytes)
            match_raw = "MATCH" if v3 == expected else "no"
            print(f"SHA-256(RSA modulus)     = {v3}   [{match_raw}]")
        elif isinstance(pk, ec.EllipticCurvePublicKey):
            raw_bytes = pk.public_bytes(
                encoding=serialization.Encoding.X962,
                format=serialization.PublicFormat.UncompressedPoint,
            )
            v3 = sha256_hex(raw_bytes)
            match_raw = "MATCH" if v3 == expected else "no"
            print(f"SHA-256(EC point)        = {v3}   [{match_raw}]")
    except Exception as e:  # noqa: BLE001
        print(f"(pubkey raw probe failed: {e})")

    # Subject + issuer fingerprint (occasionally used)
    v4 = sha256_hex(cert.subject.public_bytes())
    print(f"SHA-256(subject)         = {v4}")
    v5 = sha256_hex(cert.fingerprint(__import__('cryptography.hazmat.primitives.hashes', fromlist=['SHA256']).SHA256()))
    print(f"SHA-256(cert-SHA256)     = {v5}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
