"""Multi-tenant organization-scoped HTTP bridge and delivery store for Bale userbots."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import re
import secrets
import signal
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import certifi
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from userbot_bale.bale.auth import BaleAuth
from userbot_bale.bale.grpc_web import GrpcWebError
from userbot_bale.userbot.client import BaleUserClient
from userbot_bale.userbot.store import UserbotStore
from userbot_bale.vpn.jwt_util import user_id

log = logging.getLogger("userbot-bale.bridge")


class BridgeError(Exception):
    def __init__(self, code: str, status: int = 400) -> None:
        self.code, self.status = code, status


def positive(value: object) -> int:
    if type(value) is not int or not 0 < value <= 2**53 - 1:
        raise BridgeError("INVALID_NUMBER")
    return value


def text_field(body: dict, key: str, maximum: int = 4000) -> str:
    value = body.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise BridgeError("INVALID_INPUT")
    return value.strip()


class DeliveryStore(UserbotStore):
    """Extends UserbotStore with atomic inbound webhook queue and peer-type tracking."""

    def __init__(self, path: Path | None, organization: str, account: int) -> None:
        super().__init__(path)
        self.organization, self.account = organization, account
        self._conn.execute("CREATE TABLE IF NOT EXISTS peer_types (peer INTEGER PRIMARY KEY, type INTEGER NOT NULL)")
        self._conn.execute("CREATE TABLE IF NOT EXISTS pending (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
        self._conn.commit()

    def record_message(
        self,
        *,
        message_id: str,
        peer_id: int,
        sender_id: int,
        direction: str,
        text: str,
        received_at: float | None = None,
        peer_type: int = 1,
    ) -> bool:
        timestamp = received_at or time.time()
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "INSERT OR IGNORE INTO messages VALUES (?, ?, ?, ?, ?, ?)",
                (message_id, peer_id, sender_id, direction, text, timestamp),
            )
            self._conn.execute("INSERT OR REPLACE INTO peer_types VALUES (?, ?)", (peer_id, peer_type))
            if cursor.rowcount and direction == "inbound":
                self._conn.execute("INSERT OR IGNORE INTO allowed_peers (peer_id, created_at) VALUES (?, ?)", (peer_id, time.time()))
                payload = dict(
                    organizationId=self.organization,
                    accountId=str(self.account),
                    peerId=str(peer_id),
                    peerType=peer_type,
                    senderId=str(sender_id),
                    messageId=message_id,
                    text=text,
                    timestamp=timestamp,
                )
                self._conn.execute("INSERT INTO pending VALUES (?, ?)", (message_id, json.dumps(payload)))
            return cursor.rowcount == 1

    def list_dialogs(self, limit: int = 20) -> list[dict[str, object]]:
        dialogs = super().list_dialogs(limit)
        with self._lock:
            types = dict(self._conn.execute("SELECT peer, type FROM peer_types").fetchall())
        return [dict(dialog, peer_type=types.get(dialog["peer_id"], 1)) for dialog in dialogs]

    def pending(self) -> list[tuple[str, str]]:
        with self._lock:
            return self._conn.execute("SELECT id, payload FROM pending LIMIT 50").fetchall()

    def delivered(self, message_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM pending WHERE id = ?", (message_id,))


class MultiTenantBridge:
    """Manages active Bale userbot sessions isolated by tenant/organization."""

    def __init__(
        self,
        directory: str | Path,
        key: bytes,
        backend: str,
        webhook_secret: str,
        client_factory: type = BaleUserClient,
        auth_factory: type = BaleAuth,
    ) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.crypto = AESGCM(key)
        self.backend, self.webhook_secret = backend.rstrip("/"), webhook_secret
        self.client_factory, self.auth_factory = client_factory, auth_factory
        self.db = sqlite3.connect(self.directory / "sessions.sqlite3", check_same_thread=False)
        self.db.execute("PRAGMA secure_delete = ON")
        self.db.execute("CREATE TABLE IF NOT EXISTS sessions (org TEXT PRIMARY KEY, account INTEGER NOT NULL, token BLOB NOT NULL, enabled INTEGER NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS stores (org TEXT NOT NULL, account INTEGER NOT NULL, PRIMARY KEY (org, account))")
        self.db.commit()
        self.lock = threading.RLock()
        self.clients: dict[str, Any] = {}
        self.stores: dict[tuple[str, int], DeliveryStore] = {}
        self.auths: dict[str, tuple[Any, str, float]] = {}
        self.auth_times: dict[str, float] = {}
        self.stopping = threading.Event()
        for org, account in self.db.execute("SELECT org, account FROM stores").fetchall():
            self.store(org, account)

    def encrypt(self, org: str, jwt: str) -> bytes:
        nonce = secrets.token_bytes(12)
        return nonce + self.crypto.encrypt(nonce, jwt.encode(), org.encode())

    def decrypt(self, org: str, token: bytes) -> str:
        return self.crypto.decrypt(token[:12], token[12:], org.encode()).decode()

    def store(self, org: str, account: int) -> DeliveryStore:
        key = (org, account)
        if key not in self.stores:
            filename = hashlib.sha256(f"{org}:{account}".encode()).hexdigest() + ".sqlite3"
            self.stores[key] = DeliveryStore(self.directory / filename, org, account)
            with self.db:
                self.db.execute("INSERT OR IGNORE INTO stores VALUES (?, ?)", (org, account))
        return self.stores[key]

    def connect(self, org: str, jwt: str) -> dict[str, object]:
        account = user_id(jwt)
        if not account or not 0 < account <= 2**53 - 1:
            raise BridgeError("INVALID_SESSION")
        existing = self.db.execute("SELECT account FROM sessions WHERE org = ?", (org,)).fetchone()
        if existing and existing[0] != account:
            raise BridgeError("ACCOUNT_CHANGE_REQUIRES_DISCONNECT", 409)
        client = self.client_factory(jwt=jwt, store=self.store(org, account))
        old = self.clients.pop(org, None)
        if old:
            old.stop()
        try:
            client.start()
            with self.db:
                self.db.execute("INSERT OR REPLACE INTO sessions VALUES (?, ?, ?, 1)", (org, account, self.encrypt(org, jwt)))
            self.clients[org] = client
        except Exception:
            client.stop()
            raise
        return self.status(org)

    def status(self, org: str) -> dict[str, object]:
        row = self.db.execute("SELECT account, enabled FROM sessions WHERE org = ?", (org,)).fetchone()
        client = self.clients.get(org)
        connected = bool(client and getattr(client, "_api", None) and getattr(client._api, "_ws", None) and client._api._ws._connected.is_set())
        return dict(
            status="CONNECTED" if connected else ("DISCONNECTED" if row else "NOT_CONFIGURED"),
            accountId=str(row[0]) if row else None,
            allowedPeers=self.store(org, row[0]).allowed_peers() if row else [],
        )

    def execute(self, org: str, body: dict) -> dict[str, object]:
        if not isinstance(org, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", org):
            raise BridgeError("INVALID_ORGANIZATION")
        if not isinstance(body, dict):
            raise BridgeError("INVALID_INPUT")
        with self.lock:
            action = body.get("action")
            if action == "status":
                return self.status(org)
            if action == "request-code":
                phone = text_field(body, "phone", 16)
                if not re.fullmatch(r"\+?[1-9][0-9]{7,14}", phone):
                    raise BridgeError("INVALID_PHONE")
                now = time.monotonic()
                if now - self.auth_times.get(org, -1000) < 60:
                    raise BridgeError("RATE_LIMITED", 429)
                self.auth_times[org] = now
                self.close_auth(org)
                auth = self.auth_factory(device_title="userbot-bale", session_id=secrets.token_hex(16))
                try:
                    tx = auth.start_phone_auth(int(phone.lstrip("+")))
                    self.auths[org] = (auth, tx, now)
                except Exception:
                    auth._client.close()
                    raise
                return dict(status="CODE_SENT")
            if action == "login":
                pending = self.auths.get(org)
                if not pending or time.monotonic() - pending[2] > 300:
                    self.close_auth(org)
                    raise BridgeError("CODE_EXPIRED")
                code = text_field(body, "code", 10)
                if not code.isdigit():
                    raise BridgeError("INVALID_CODE")
                try:
                    session = pending[0].validate_code(pending[1], code)
                except GrpcWebError as err:
                    if "PHONE_CODE_INVALID" in (err.message or ""):
                        raise BridgeError("INVALID_CODE", 400)
                    if "PHONE_CODE_EXPIRED" in (err.message or ""):
                        self.close_auth(org)
                        raise BridgeError("CODE_EXPIRED", 400)
                    raise
                self.close_auth(org)
                return self.connect(org, session.jwt)
            if action == "import-session":
                return self.connect(org, text_field(body, "jwt", 16384))
            if action == "reconnect":
                row = self.db.execute("SELECT token FROM sessions WHERE org = ?", (org,)).fetchone()
                if not row:
                    raise BridgeError("NOT_CONFIGURED", 409)
                return self.connect(org, self.decrypt(org, row[0]))
            if action == "disconnect":
                client = self.clients.pop(org, None)
                if client:
                    client.stop()
                self.close_auth(org)
                with self.db:
                    self.db.execute("DELETE FROM sessions WHERE org = ?", (org,))
                return self.status(org)
            client = self.clients.get(org)
            if not client:
                raise BridgeError("NOT_CONNECTED", 409)
            if action in ("allow-peer", "disallow-peer"):
                peer = positive(body.get("peerId"))
                (client.store.allow_peer if action == "allow-peer" else client.store.disallow_peer)(peer)
                return self.status(org)
            limit = body.get("limit", 30)
            if type(limit) is not int or not 1 <= limit <= 100:
                raise BridgeError("INVALID_LIMIT")
            if action == "dialogs":
                return dict(dialogs=client.list_dialogs(limit))
            if action == "contacts":
                return dict(contacts=client.search_contacts(text_field(body, "query", 200)))
            if action == "resolve-phone":
                return dict(peerId=client.resolve_phone(text_field(body, "phone", 16)))
            if action == "search":
                peer = positive(body["peerId"]) if "peerId" in body else None
                return dict(messages=client.search_messages(text_field(body, "query", 200), peer_id=peer, limit=limit))
            peer = positive(body.get("peerId"))
            peer_type = body.get("peerType", 1)
            if type(peer_type) is not int or peer_type not in (1, 2, 3):
                raise BridgeError("INVALID_PEER_TYPE")
            if action == "messages":
                client._dialog_peer_types[peer] = peer_type
                return dict(messages=client.list_messages(peer, limit))
            if action == "mark-read":
                if not client.store.is_peer_allowed(peer):
                    raise BridgeError("PEER_NOT_ALLOWED", 403)
                date_val = body.get("date")
                date_ts = positive(date_val) if date_val is not None else int(time.time() * 1000)
                client._api.mark_read(peer, date_ts, peer_type=peer_type)
                return dict(success=True)
            if action == "send":
                if str(body.get("accountId")) != str(client.user_id):
                    raise BridgeError("ACCOUNT_MISMATCH", 409)
                if peer_type != 1:
                    client._remember_dialog_types(client._api.load_dialogs(limit=100))
                if body.get("autoAllow"):
                    client.store.allow_peer(peer)
                reply_to = positive(body["replyTo"]) if body.get("replyTo") is not None else None
                is_silent = bool(body.get("isSilent", False))
                rid = client.send_text(peer, text_field(body, "text"), peer_type=peer_type, reply_to=reply_to, is_silent=is_silent)
                return dict(success=True, messageId=str(rid), createdAt=time.time())
            raise BridgeError("INVALID_ACTION")

    def close_auth(self, org: str) -> None:
        pending = self.auths.pop(org, None)
        if pending:
            pending[0]._client.close()

    def restore(self) -> None:
        with self.lock:
            for org, token in self.db.execute("SELECT org, token FROM sessions WHERE enabled = 1").fetchall():
                try:
                    self.connect(org, self.decrypt(org, token))
                except Exception:
                    log.warning("Session restore failed for org %s; reconnect required", org)

    def deliver(self) -> None:
        with httpx.Client(timeout=15, follow_redirects=False, trust_env=False) as http:
            while not self.stopping.wait(2):
                with self.lock:
                    stores = list(self.stores.values())
                    for org, (_, _, created) in list(self.auths.items()):
                        if time.monotonic() - created > 300:
                            self.close_auth(org)
                for store in stores:
                    for message_id, raw in store.pending():
                        try:
                            response = http.post(
                                self.backend + "/api/webhooks/bale/userbot",
                                content=raw,
                                headers={
                                    "content-type": "application/json",
                                    "x-internal-webhook-secret": self.webhook_secret,
                                },
                            )
                            response.raise_for_status()
                            store.delivered(message_id)
                        except Exception:
                            log.warning("Inbound delivery pending retry")
                            break

    def close(self) -> None:
        self.stopping.set()
        with self.lock:
            for client in self.clients.values():
                client.stop()
            for org in list(self.auths):
                self.close_auth(org)


def handler_for(bridge: MultiTenantBridge, api_key: str) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_) -> None:
            pass

        def respond(self, status: int, body: dict) -> None:
            raw = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:
            self.respond(200 if self.path == "/health" else 404, {"ok": self.path == "/health"})

        def do_POST(self) -> None:
            raw_key = self.headers.get("x-bridge-api-key", "")
            if not hmac.compare_digest(raw_key.encode("latin1"), api_key.encode("utf-8")):
                return self.respond(401, {"error": "UNAUTHORIZED"})
            match = re.fullmatch(r"/sessions/([A-Za-z0-9_-]{1,128})", self.path)
            if not match:
                return self.respond(404, {"error": "NOT_FOUND"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 32768:
                    raise BridgeError("INVALID_BODY")
                self.connection.settimeout(10)
                body = json.loads(self.rfile.read(length))
                self.respond(200, bridge.execute(match[1], body))
            except BridgeError as error:
                self.respond(error.status, {"error": error.code})
            except PermissionError:
                self.respond(403, {"error": "PEER_NOT_ALLOWED"})
            except LookupError:
                self.respond(404, {"error": "USER_NOT_FOUND"})
            except (ValueError, TypeError):
                self.respond(400, {"error": "INVALID_INPUT"})
            except Exception:
                log.warning("Bale operation failed")
                self.respond(502, {"error": "PROVIDER_FAILED"})

    return Handler


def run_bridge_server() -> None:
    os.umask(0o077)
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("userbot_bale").setLevel(logging.CRITICAL)
    key = base64.b64decode(os.environ["KALAHAMOON_INTEGRATION_ENCRYPTION_KEY"], validate=True)
    if len(key) != 32:
        raise ValueError("Integration encryption key must contain 32 bytes")
    api_key = (os.environ.get("BALE_BRIDGE_API_KEY") or os.environ.get("BRIDGE_API_KEY") or "").strip()
    secret = os.environ["INTERNAL_WEBHOOK_SECRET"].strip()
    if not api_key or not secret:
        raise ValueError("Bridge and webhook keys are required")
    bridge = MultiTenantBridge(
        os.environ.get("BALE_BRIDGE_DATA_DIR", "./data"),
        key,
        os.environ.get("MAIN_BACKEND_URL", "http://localhost:3000"),
        secret,
    )
    server = ThreadingHTTPServer(
        (os.environ.get("HOST", "127.0.0.1"), int(os.environ.get("PORT", "3003"))),
        handler_for(bridge, api_key),
    )

    def stop(*_):
        bridge.close()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    threading.Thread(target=bridge.restore, daemon=True).start()
    worker = threading.Thread(target=bridge.deliver, daemon=True)
    worker.start()
    try:
        server.serve_forever()
    finally:
        bridge.close()
        worker.join(timeout=20)
        server.server_close()
