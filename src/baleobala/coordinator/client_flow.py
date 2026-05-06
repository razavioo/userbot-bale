"""Client-side coordinator flow.

Placed in the coordinator package so LiveKitSession is a module-level
import that tests can patch cleanly.
"""

from __future__ import annotations

import platform
import sys

from baleobala.bale.api import BaleApiClient
from baleobala.bale.livekit_backend import LiveKitSession
from baleobala.carrier.bale import BaleCarrierController
from baleobala.coordinator.protocol import (
    CONTROL_TOPIC,
    DenyReason,
    Kind,
    decode as ctrl_decode,
    encode as ctrl_encode,
    make_hello,
)


def resolve_via_coordinator(
    *,
    coordinator_peer_id: int,
    jwt: str,
    ws_tls_config,
    identity: str,
    answer_timeout: float,
    client_peer_id: int | None = None,
    client_factory=None,
    controller_factory=None,
):
    """Coordinator-mode credential resolution (client side).

    1. Dial coordinator, send HELLO, receive ASSIGN (or raise on DENY).
    2. Hang up coordinator session.
    3. Enter answer mode; relay calls us back.
    4. Return the inbound LiveKit credentials for the real VPN session.

    `client_factory` and `controller_factory` are injectable for tests.
    """
    _BaleApiClient = client_factory or BaleApiClient
    _BaleCarrierController = controller_factory or BaleCarrierController

    print(
        f"[bale-call] coordinator mode — calling coordinator peer={coordinator_peer_id}",
        file=sys.stderr,
    )

    coord_client = _BaleApiClient(jwt=jwt, ws_tls_config=ws_tls_config)
    coord_client.start()
    try:
        coord_creds = coord_client.fetch_livekit_credentials(
            coordinator_peer_id, creds_timeout=30.0,
        )
    except Exception as exc:
        coord_client.stop()
        raise SystemExit(f"failed to reach coordinator: {exc}") from exc

    coord_session = LiveKitSession(
        url=coord_creds.url, token=coord_creds.token,
        identity=f"{identity}-coord",
    )
    assign_msg = None
    try:
        coord_session.start()
        coord_session.wait_for_remote_participant(timeout=15.0)
        ch = coord_session.data_channel(topic=CONTROL_TOPIC, reliable=True)
        ch.send_bytes(ctrl_encode(make_hello(
            client_id=identity,
            app_version=platform.node(),
            client_peer_id=client_peer_id,
        )))
        payload = ch.recv_bytes(timeout=10.0)
        if payload is None:
            raise SystemExit("coordinator did not respond to HELLO")
        try:
            assign_msg = ctrl_decode(payload)
        except Exception as exc:
            raise SystemExit(f"coordinator sent invalid response: {exc}") from exc

        if assign_msg.kind == Kind.DENY:
            reason = assign_msg.get("reason", DenyReason.NO_CAPACITY)
            raise SystemExit(f"coordinator denied connection: {reason}")

        if assign_msg.kind != Kind.ASSIGN:
            raise SystemExit(f"coordinator sent unexpected kind={assign_msg.kind}")

    finally:
        try:
            coord_session.stop()
        except Exception:
            pass
        coord_client.stop()

    relay_peer_id = int(assign_msg.get("relay_peer_id", 0))
    if relay_peer_id <= 0:
        raise SystemExit("coordinator returned invalid relay_peer_id")
    print(
        f"[bale-call] assigned relay={relay_peer_id} — calling relay directly",
        file=sys.stderr,
    )

    # Call the relay directly; it will accept because the coordinator
    # pre-registered our peer_id in its expected-clients set.
    relay_client = _BaleApiClient(jwt=jwt, ws_tls_config=ws_tls_config)
    controller = _BaleCarrierController(client=relay_client)
    try:
        creds = controller.dial(peer_id=relay_peer_id, creds_timeout=30.0)
    finally:
        relay_client.stop()

    print(f"[bale-call] relay connected — room={creds.url}", file=sys.stderr)
    return creds
