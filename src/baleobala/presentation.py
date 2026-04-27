"""Shared user-facing copy and state mapping for CLI and GUI."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass

from baleobala.control.service import ControlSnapshot


def product_path() -> str:
    return "doctor -> auth -> pair -> connect"


def validate_phone_input(value: str) -> tuple[bool, str]:
    text = value.strip()
    if not text:
        return False, "Enter your Bale phone number to continue."
    normalized = text.lstrip("+").replace(" ", "")
    if not normalized.isdigit():
        return False, "Phone numbers must contain digits, with an optional leading +."
    if len(normalized) < 10:
        return False, "Phone number looks too short. Use your full mobile number."
    return True, ""


def validate_sms_code(value: str) -> tuple[bool, str]:
    code = value.strip()
    if not code:
        return False, "Enter the SMS code we sent to your phone."
    if not re.fullmatch(r"\d{4,8}", code):
        return False, "SMS codes should be 4 to 8 digits."
    return True, ""


def friendly_error_message(raw: str, *, context: str) -> str:
    text = (raw or "").strip() or "Unknown error."
    lowered = text.lower()
    if "phone must be digits" in lowered or "invalid phone" in lowered:
        return (
            "We could not use that phone number.\n"
            "Why this usually happens: the number format is incomplete or contains extra characters.\n"
            "Next step: enter the full mobile number with digits only, optionally starting with +."
        )
    if "no paired relay" in lowered or "need a pairing profile" in lowered or "no pairing" in lowered:
        return (
            "No paired relay is ready yet.\n"
            "Why this usually happens: this device has not created or accepted a pairing.\n"
            "Next step: create a pairing in the app or run `baleobala pair enroll --name home-relay`."
        )
    if "session expired" in lowered or "auth expired" in lowered or "jwt_expired" in lowered or "saved bale session expired" in lowered:
        return (
            "Your saved Bale session expired.\n"
            "Why this usually happens: the local JWT is expired or missing from the secret store.\n"
            "Next step: sign in again with `baleobala auth bale-login --phone +98912xxxxxxx --save`."
        )
    if "relay access was revoked" in lowered or "device revoked" in lowered or "access_revoked" in lowered:
        return (
            "Relay access was revoked for this device.\n"
            "Why this usually happens: the relay owner removed this device from the provisioning list.\n"
            "Next step: ask the relay owner to approve the device again, then sync the pairing."
        )
    if "provisioning" in lowered and ("pending" in lowered or "missing" in lowered or "incomplete" in lowered or "approval" in lowered):
        return (
            "Relay provisioning is not complete yet.\n"
            "Why this usually happens: the device has not been approved or the local pairing cache is stale.\n"
            "Next step: request access, approve it, then run `baleobala pair sync --profile-id <id>`."
        )
    if "credentials" in lowered and ("missing" in lowered or "expired" in lowered or "refresh" in lowered):
        return (
            "Relay credentials need a refresh.\n"
            "Why this usually happens: the local secret is missing or the credential epoch expired.\n"
            "Next step: run `baleobala pair sync --profile-id <id>` to refresh the saved secret."
        )
    if "network" in lowered or "timed out" in lowered or "timeout" in lowered:
        return (
            "The network request did not complete.\n"
            "Why this usually happens: the internet connection is offline or the Bale service did not respond in time.\n"
            "Next step: check connectivity, then try again."
        )
    if "pyside6" in lowered:
        return (
            "The desktop app could not start.\n"
            "Why this usually happens: the GUI dependency is not installed.\n"
            "Next step: install with `pip install -e \".[desktop]\"` and launch again."
        )
    if context == "login":
        return (
            "We could not complete sign-in.\n"
            f"Why this likely happened: {text}\n"
            "Next step: confirm the phone/code and try again."
        )
    return (
        "The action did not complete.\n"
        f"Why this likely happened: {text}\n"
        "Next step: review the guidance above and try again."
    )


@dataclass(frozen=True)
class Banner:
    title: str
    body: str
    tone: str = "info"


@dataclass(frozen=True)
class TimelineStep:
    key: str
    label: str
    done: bool
    current: bool


@dataclass(frozen=True)
class ReadinessItem:
    key: str
    label: str
    ready: bool
    detail: str


def connect_timeline(*, signed_in: bool, paired: bool, phase: str) -> list[TimelineStep]:
    order = ["signed_in", "paired", "connecting", "connected"]
    reached = {
        "signed_in": signed_in,
        "paired": paired,
        "connecting": phase in {"connecting", "connected", "stopping"},
        "connected": phase == "connected",
    }
    current_key = {
        "idle": "signed_in" if not signed_in else "paired",
        "authenticated": "paired",
        "paired": "paired",
        "connecting": "connecting",
        "connected": "connected",
        "stopping": "connecting",
        "error": "paired" if paired else "signed_in",
    }.get(phase, "signed_in")
    labels = {
        "signed_in": "Signed In",
        "paired": "Pair Relay",
        "connecting": "Connecting",
        "connected": "Connected",
    }
    return [
        TimelineStep(
            key=key,
            label=labels[key],
            done=reached[key],
            current=current_key == key and not reached.get("connected", False) if key != "connected" else current_key == key,
        )
        for key in order
    ]


def connect_banner(
    *,
    signed_in: bool,
    paired: bool,
    phase: str,
    detail: str = "",
    auth_state: str = "",
    connection: dict[str, str] | None = None,
) -> Banner:
    connection = connection or {}
    if connection.get("state") == "blocked":
        title = connection.get("title") or "Connection needs attention"
        body = connection.get("message") or detail or "Review the message below, fix the issue, and try again."
        return Banner(title, body, "err")
    if connection.get("state") == "ready":
        body = detail
        if not body:
            if connection.get("backend") == "direct":
                body = "Your direct proxy backend is ready. Start the secure connection when you are ready."
            else:
                body = "Your account is signed in and a relay pairing is available. Start the secure connection when you are ready."
        return Banner("Ready to connect", body, "ok")
    if not signed_in:
        if auth_state == "expired":
            return Banner(
                "Session expired",
                "Your Bale session expired. Sign in again to refresh relay provisioning.",
                "err",
            )
        return Banner(
            "Step 1: Sign in",
            "Use your Bale phone number to sign in first. After that, we can create or reuse a relay pairing.",
            "info",
        )
    if phase == "connected":
        body = detail or "Your secure connection is active. You can leave this window open while traffic is running."
        return Banner("Secure connection active", body, "ok")
    if phase == "connecting":
        return Banner(
            "Starting secure connection",
            detail or "We are checking your saved profile, relay pairing, and runtime before traffic starts.",
            "info",
        )
    if phase == "stopping":
        return Banner("Stopping connection", "We are closing the active session and restoring the idle state.", "info")
    if phase == "error":
        return Banner("Connection needs attention", detail or "Review the message below, fix the issue, and try again.", "err")
    if paired:
        return Banner(
            "Ready to connect",
            "Your account is signed in and a relay pairing is available. Start the secure connection when you are ready.",
            "ok",
        )
    return Banner(
        "Step 2: Pair a relay",
        "Create a new relay pairing or enter an existing pairing code. Advanced settings stay hidden unless you need them.",
        "info",
    )


def connect_readiness(
    *,
    signed_in: bool,
    paired: bool,
    saved_profile: bool,
    phase: str,
    auth_state: str = "",
    pairing_state: str = "",
    connection: dict[str, str] | None = None,
) -> list[ReadinessItem]:
    connection = connection or {}
    connecting = phase in {"connecting", "connected", "stopping"}
    pairing_detail = "Ready" if paired else "Create a new relay pairing or enter an existing pairing code."
    if pairing_state in {"revoked"}:
        pairing_detail = "Relay access was revoked for this device. Ask the relay owner to approve it again."
    elif pairing_state in {"rejected"}:
        pairing_detail = "Relay access was rejected. Request approval again from the relay owner."
    elif pairing_state in {"pending", "enrolled"}:
        pairing_detail = "Relay access is waiting for approval."
    elif connection.get("state") == "blocked" and connection.get("message"):
        pairing_detail = str(connection["message"])
    auth_detail = "Ready" if signed_in else "Sign in with your Bale phone number first."
    if auth_state == "expired":
        auth_detail = "Your saved Bale session expired. Sign in again to refresh relay provisioning."
    return [
        ReadinessItem(
            key="auth",
            label="Bale sign-in",
            ready=signed_in,
            detail=auth_detail,
        ),
        ReadinessItem(
            key="pairing",
            label="Relay pairing",
            ready=paired,
            detail=pairing_detail,
        ),
        ReadinessItem(
            key="profile",
            label="Saved profile",
            ready=saved_profile,
            detail="Ready" if saved_profile else "The app will create a default profile on first connect.",
        ),
        ReadinessItem(
            key="runtime",
            label="Runtime state",
            ready=connecting,
            detail="Active or changing" if connecting else "Idle until you start the secure connection.",
        ),
    ]


def summarize_doctor(checks: list[tuple[str, bool, str]]) -> tuple[str, list[str], str]:
    missing = [name for name, ok, _detail in checks if not ok]
    if sys.platform == "darwin":
        critical_names = {"python"}
    elif sys.platform.startswith("linux"):
        critical_names = {"python", "pactl", "sounddevice"}
    elif sys.platform == "win32":
        critical_names = {"python", "sounddevice", "powershell"}
    else:
        critical_names = {"python", "sounddevice"}
    critical = [name for name in missing if name in critical_names]
    if critical:
        headline = "Setup needs attention before first run."
        next_step = "Install the missing critical dependencies, then run `baleobala doctor` again."
    elif sys.platform == "darwin" and "codesign" in missing:
        headline = "Core runtime is ready; macOS tunnel signing needs attention."
        next_step = "Configure an Apple code-signing identity before installing or starting the packet tunnel."
    elif missing:
        headline = "Core setup looks usable, with optional extras missing."
        next_step = "You can continue with sign-in now. Install optional extras later if you need their features."
    else:
        headline = "This machine is ready for the main product path."
        next_step = f"Next step: follow `{product_path()}`."
    return headline, missing, next_step


def summarize_snapshot(snapshot: ControlSnapshot) -> list[str]:
    lines: list[str] = []
    auth_state = snapshot.auth.get("state", "empty")
    pairing_state = snapshot.pairing.get("state", "empty")
    pairing_auth = snapshot.pairing.get("authorization_status", "")
    pairing_provisioning = snapshot.pairing.get("provisioning_status", "")
    pairing_revoked = snapshot.pairing.get("revoked_at", "") not in {"", "none"} or pairing_auth == "revoked" or pairing_provisioning == "revoked"
    pairing_rejected = pairing_auth == "rejected" or pairing_provisioning == "rejected"
    backend_state = snapshot.backend.get("state", "stopped")
    connection = getattr(snapshot, "connection", {}) or {}
    if auth_state == "configured":
        who = snapshot.auth.get("phone", "saved account")
        lines.append(f"Signed in: yes ({who})")
    elif auth_state == "expired":
        lines.append("Signed in: session expired")
    else:
        lines.append("Signed in: no")

    if pairing_revoked:
        lines.append(f"Relay pairing: revoked ({snapshot.pairing.get('name', 'saved relay')})")
    elif pairing_rejected:
        lines.append(f"Relay pairing: rejected ({snapshot.pairing.get('name', 'saved relay')})")
    elif pairing_state in {"paired", "accepted", "complete"} and pairing_provisioning in {"complete", "accepted"}:
        lines.append(f"Relay pairing: ready ({snapshot.pairing.get('name', 'saved relay')})")
    elif pairing_state == "pending" or pairing_provisioning in {"pending", "enrolled"} or pairing_auth == "pending":
        lines.append(f"Relay pairing: waiting ({snapshot.pairing.get('name', 'unnamed relay')})")
    else:
        lines.append("Relay pairing: none")

    if backend_state == "running":
        lines.append(f"Connection: active via {snapshot.backend.get('backend', 'backend')}")
    elif backend_state == "degraded":
        lines.append("Connection: needs attention")
    else:
        lines.append("Connection: idle")

    if connection.get("state") == "blocked":
        lines.append(f"Connection gate: {connection.get('message', 'blocked')}")
    if connection.get("next_step"):
        lines.append(f"Next step: {connection.get('next_step')}")
    elif auth_state != "configured":
        lines.append("Next step: `baleobala auth bale-login --phone +98912xxxxxxx --save`")
    elif pairing_state not in {"paired", "accepted", "complete"}:
        lines.append("Next step: `baleobala pair enroll --name home-relay`")
    elif backend_state != "running":
        lines.append("Next step: `baleobala vpn up` or open `baleobala gui`.")
    return lines


def capability_note() -> str:
    if sys.platform == "darwin":
        return "Platform: macOS uses the desktop app for sign-in/pairing and the native app for system tunnel control."
    if sys.platform.startswith("linux"):
        return "Platform: Linux uses the shared CLI/Qt flow and can run the native Linux TUN path directly."
    if sys.platform == "win32":
        return "Platform: Windows uses the shared CLI/Qt flow and the WinHTTP proxy-backed development path."
    return "Platform: unsupported systems can still inspect state, but the full secure-connection path may be limited."
