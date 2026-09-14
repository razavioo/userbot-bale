"""Conservative stdio MCP server for Bale messaging."""

from __future__ import annotations

from userbot_bale.control.auth import AuthStore
from userbot_bale.userbot.client import BaleUserClient
from userbot_bale.userbot.store import UserbotStore


class BaleMcpService:
    """Tool implementation kept independent of a particular MCP SDK version."""

    def __init__(self, client: BaleUserClient, store: UserbotStore) -> None:
        self._client = client
        self._store = store

    def account_status(self) -> dict[str, object]:
        status = AuthStore().status()
        return {
            "state": status.get("state", "unknown"),
            "user_id": status.get("user_id", "unknown"),
            "expires_in": status.get("expires_in", "unknown"),
            "allowed_peers": self._store.allowed_peers(),
        }

    def list_messages(self, peer_id: int, limit: int = 20) -> list[dict[str, object]]:
        self._require_allowed_peer(peer_id)
        self._ensure_started()
        return self._client.list_messages(peer_id, limit)

    def list_dialogs(self, limit: int = 20) -> list[dict[str, object]]:
        self._ensure_started()
        allowed_peers = set(self._store.allowed_peers())
        # Request more from client to ensure we find allowlisted dialogs
        candidate_limit = max(limit * 5, 100)
        return [
            dialog for dialog in self._client.list_dialogs(candidate_limit)
            if dialog.get("peer_id") in allowed_peers
        ][:limit]

    def send_text(self, peer_id: int, text: str) -> dict[str, object]:
        self._require_allowed_peer(peer_id)
        self._ensure_started()
        self._client.send_text(peer_id, text)
        return {"ok": True, "peer_id": peer_id}

    def _ensure_started(self) -> None:
        self._client.start()

    def _require_allowed_peer(self, peer_id: int) -> None:
        if not self._store.is_peer_allowed(peer_id):
            self._store.audit("access_rejected", peer_id=peer_id, detail="peer_not_allowlisted")
            raise PermissionError(f"peer {peer_id} is not in the MCP allowlist")


def create_service() -> BaleMcpService:
    record = AuthStore().load()
    if record is None:
        raise RuntimeError("no valid Bale session; run 'userbot-bale auth bale-login --save' first")
    store = UserbotStore()
    return BaleMcpService(BaleUserClient(jwt=record.jwt, store=store), store)


def create_server(service: BaleMcpService | None = None):
    """Create an MCP stdio server without exposing raw Bale RPC access."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise ImportError("MCP support requires 'pip install -e .[mcp]'") from exc

    service = service or create_service()
    server = FastMCP("userbot-bale")

    @server.tool()
    def account_status() -> dict[str, object]:
        """Get local Bale account status and the outbound peer allowlist."""
        return service.account_status()

    @server.tool()
    def list_messages(peer_id: int, limit: int = 20) -> list[dict[str, object]]:
        """Read recent remote history or local persisted messages for one approved peer."""
        return service.list_messages(peer_id, limit)

    @server.tool()
    def list_dialogs(limit: int = 20) -> list[dict[str, object]]:
        """List recent Bale dialogs using the authenticated account."""
        return service.list_dialogs(limit)

    @server.tool()
    def send_text(peer_id: int, text: str) -> dict[str, object]:
        """Send text only to an explicitly allowlisted Bale peer."""
        return service.send_text(peer_id, text)

    return server


def serve() -> None:
    create_server().run(transport="stdio")
