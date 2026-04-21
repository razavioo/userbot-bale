"""Bale-specific carrier controller.

This layer knows how to ask Bale for LiveKit credentials, but it does not
know anything about the tunnel protocol itself.
"""

from __future__ import annotations

from dataclasses import dataclass

from baleobala.bale.api import BaleApiClient


@dataclass(frozen=True)
class CarrierCredentials:
    url: str
    token: str
    room: str
    identity: str


class BaleCarrierController:
    """Dial Bale, get credentials, and open a LiveKit carrier session."""

    def __init__(self, client: BaleApiClient | None = None) -> None:
        self._client = client or BaleApiClient()

    def dial(
        self,
        *,
        peer_id: int,
        peer_type: int = 1,
        video: bool = False,
        invite_enable: bool = True,
        creds_timeout: float = 120.0,
    ) -> CarrierCredentials:
        self._client.start()
        try:
            creds = self._client.fetch_livekit_credentials(
                peer_id,
                peer_type=peer_type,
                video=video,
                invite_enable=invite_enable,
                creds_timeout=creds_timeout,
            )
            return CarrierCredentials(
                url=creds.url,
                token=creds.token,
                room=creds.room,
                identity=creds.identity,
            )
        finally:
            self._client.stop()

    def search_contacts(self, query: str):
        self._client.start()
        try:
            return self._client.search_contacts(query)
        finally:
            self._client.stop()

    def resolve_peer(self, phone: str | int) -> int:
        self._client.start()
        try:
            return self._client.resolve_peer(phone)
        finally:
            self._client.stop()

    def answer(
        self,
        *,
        timeout: float = 120.0,
    ) -> CarrierCredentials:
        self._client.start()
        try:
            import threading

            got = threading.Event()
            holder: list[CarrierCredentials] = []

            def on_creds(creds) -> None:  # noqa: ANN001
                if not holder:
                    holder.append(
                        CarrierCredentials(
                            url=creds.url,
                            token=creds.token,
                            room=creds.room,
                            identity="baleobala",
                        )
                    )
                    got.set()

            self._client.listen_incoming_calls(on_creds)
            if not got.wait(timeout=timeout):
                raise TimeoutError(f"no incoming call within {timeout}s")
            return holder[0]
        finally:
            self._client.stop()

    def open_session(self, creds: CarrierCredentials) -> LiveKitCarrierSession:
        from baleobala.carrier.livekit import LiveKitCarrierSession

        return LiveKitCarrierSession(
            url=creds.url,
            token=creds.token,
            identity=creds.identity,
        )
