"""Conservative stdio MCP server for Bale messaging."""

from __future__ import annotations

from contextlib import asynccontextmanager
import secrets
import threading
import time

from userbot_bale.control.auth import AuthStore
from userbot_bale.mcp.rpc_paths import list_service_paths
from userbot_bale.userbot.client import BaleUserClient
from userbot_bale.userbot.store import UserbotStore

_CONFIRM_TTL_SECONDS = 300.0

class BaleMcpService:
    """Tool implementation kept independent of a particular MCP SDK version."""

    def __init__(self, client: BaleUserClient, store: UserbotStore) -> None:
        self._client = client
        self._store = store
        self._start_lock = threading.Lock()
        self._pending_sends: dict[str, tuple[int, str, float]] = {}

    def account_status(self) -> dict[str, object]:
        status = AuthStore().status()
        return {
            "state": status.get("state", "unknown"),
            "user_id": status.get("user_id", "unknown"),
            "expires_in": status.get("expires_in", "unknown"),
            "allowed_peers": self._store.allowed_peers(),
        }

    def list_messages(self, peer_id: int, limit: int = 20) -> dict[str, object]:
        self._require_allowed_peer(peer_id)
        self._validate_limit(limit)
        self._ensure_started()
        messages = self._client.list_messages(peer_id, limit)
        return {"messages": messages, "count": len(messages)}

    def search_messages(
        self, query: str, peer_id: int | None = None, limit: int = 20,
    ) -> dict[str, object]:
        if peer_id is not None:
            self._require_allowed_peer(peer_id)
        self._validate_limit(limit)
        self._ensure_started()
        results = self._client.search_messages(query, peer_id=peer_id, limit=limit)
        allowed_peers = set(self._store.allowed_peers())
        filtered = [
            msg for msg in results
            if msg.get("peer_id") in allowed_peers
        ]
        return {"messages": filtered, "count": len(filtered)}

    def search_contacts(self, query: str) -> dict[str, object]:
        self._ensure_started()
        contacts = self._client.search_contacts(query)
        return {"contacts": contacts, "count": len(contacts)}

    def resolve_phone(self, phone: str) -> dict[str, object]:
        self._ensure_started()
        user_id = self._client.resolve_phone(phone)
        return {"phone": phone, "user_id": user_id, "is_allowed": self._store.is_peer_allowed(user_id)}

    def list_dialogs(self, limit: int = 20) -> dict[str, object]:
        self._validate_limit(limit)
        self._ensure_started()
        allowed_peers = set(self._store.allowed_peers())
        # Request more from client to ensure we find allowlisted dialogs
        candidate_limit = max(limit * 5, 100)
        dialogs = [
            dialog for dialog in self._client.list_dialogs(candidate_limit)
            if dialog.get("peer_id") in allowed_peers
        ][:limit]
        return {"dialogs": dialogs, "count": len(dialogs)}

    def send_text(
        self, peer_id: int, text: str, *, confirm_token: str | None = None,
    ) -> dict[str, object]:
        self._require_allowed_peer(peer_id)
        self._ensure_started()
        now = time.time()
        self._prune_pending(now)
        if confirm_token is None:
            token = secrets.token_urlsafe(18)
            self._pending_sends[token] = (peer_id, text, now + _CONFIRM_TTL_SECONDS)
            return {
                "ok": False,
                "needs_confirm": True,
                "confirm_token": token,
                "peer_id": peer_id,
                "preview": {"peer_id": peer_id, "text": text},
                "expires_in": int(_CONFIRM_TTL_SECONDS),
            }
        pending = self._pending_sends.pop(confirm_token, None)
        if pending is None:
            raise PermissionError(
                "invalid or expired confirm_token; call send_text again without "
                "confirm_token to obtain a fresh one"
            )
        pending_peer, pending_text, expires_at = pending
        if now > expires_at:
            raise PermissionError("confirm_token expired; request a new one")
        if pending_peer != peer_id or pending_text != text:
            raise PermissionError("confirm_token does not match peer_id/text")
        self._client.send_text(peer_id, text)
        return {"ok": True, "peer_id": peer_id, "confirmed": True}

    def mark_read(self, peer_id: int, date: int) -> dict[str, object]:
        self._require_allowed_peer(peer_id)
        self._ensure_started()
        self._client.mark_read(peer_id, date)
        return {"ok": True, "peer_id": peer_id, "date": date}

    @staticmethod
    def list_rpc_paths(
        service: str | None = None,
        query: str | None = None,
        limit: int = 100,
    ) -> dict[str, object]:
        """Read-only discovery of Bale /bale.*/* paths from the APK inventory."""
        return list_service_paths(service=service, query=query, limit=limit)

    def _prune_pending(self, now: float) -> None:
        expired = [token for token, (_, _, expires) in self._pending_sends.items() if now > expires]
        for token in expired:
            del self._pending_sends[token]

    def _ensure_started(self) -> None:
        with self._start_lock:
            self._client.start()

    def close(self) -> None:
        with self._start_lock:
            self._client.stop()

    def _require_allowed_peer(self, peer_id: int) -> None:
        if not self._store.is_peer_allowed(peer_id):
            self._store.audit("access_rejected", peer_id=peer_id, detail="peer_not_allowlisted")
            raise PermissionError(f"peer {peer_id} is not in the MCP allowlist")

    @staticmethod
    def _validate_limit(limit: int) -> None:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")

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
        from mcp.types import ToolAnnotations
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise ImportError("MCP support requires 'pip install -e .[mcp]'") from exc

    service = service or create_service()
    @asynccontextmanager
    async def lifespan(_server):  # noqa: ANN001
        try:
            yield {}
        finally:
            service.close()

    server = FastMCP("userbot-bale", lifespan=lifespan)
    read_only = ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    )

    @server.tool(annotations=read_only)
    def account_status() -> dict[str, object]:
        """Get local Bale account status and the outbound peer allowlist."""
        return service.account_status()

    @server.tool(annotations=read_only)
    def list_messages(peer_id: int, limit: int = 20) -> dict[str, object]:
        """Read recent remote history or local persisted messages for one approved peer."""
        return service.list_messages(peer_id, limit)

    @server.tool(annotations=read_only)
    def search_messages(query: str, peer_id: int | None = None, limit: int = 20) -> dict[str, object]:
        """Search local message history for text across one or all approved peers."""
        return service.search_messages(query, peer_id=peer_id, limit=limit)

    @server.tool(annotations=read_only)
    def search_contacts(query: str) -> dict[str, object]:
        """Search Bale contacts and directory by name or phone query."""
        return service.search_contacts(query)

    @server.tool(annotations=read_only)
    def resolve_phone(phone: str) -> dict[str, object]:
        """Resolve a phone number to a numeric Bale user ID."""
        return service.resolve_phone(phone)

    @server.tool(annotations=read_only)
    def list_dialogs(limit: int = 20) -> dict[str, object]:
        """List recent Bale dialogs using the authenticated account."""
        return service.list_dialogs(limit)

    @server.tool(annotations=read_only)
    def list_rpc_paths(
        service: str | None = None,
        query: str | None = None,
        limit: int = 100,
    ) -> dict[str, object]:
        """List known Bale gRPC /bale.*/* service paths from the offline APK inventory (read-only)."""
        # Parameter `service` shadows the outer BaleMcpService instance;
        # list_rpc_paths is a staticmethod, so call it on the class.
        return BaleMcpService.list_rpc_paths(
            service=service, query=query, limit=limit,
        )

    @server.tool(annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ))
    def send_text(peer_id: int, text: str, confirm_token: str | None = None) -> dict[str, object]:
        """Send text to an allowlisted peer only after a two-phase confirm_token handshake."""
        return service.send_text(peer_id, text, confirm_token=confirm_token)

    @server.tool(annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ))
    def mark_read(peer_id: int, date: int) -> dict[str, object]:
        """Mark incoming messages as read up to a specific timestamp date for an approved peer."""
        return service.mark_read(peer_id, date)

    return server


def serve() -> None:
    create_server().run(transport="stdio")
