"""SQLite persistence for local userbot state and audit records."""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

from userbot_bale.control.paths import data_dir


class UserbotStore:
    """Local state store. JWTs are deliberately not stored here."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (data_dir() / "userbot.sqlite3")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS allowed_peers (
                    peer_id INTEGER PRIMARY KEY,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    message_id TEXT PRIMARY KEY,
                    peer_id INTEGER NOT NULL,
                    sender_id INTEGER NOT NULL,
                    direction TEXT NOT NULL CHECK(direction IN ('inbound', 'outbound')),
                    text TEXT NOT NULL,
                    received_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS messages_peer_time
                    ON messages(peer_id, received_at DESC);
                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    peer_id INTEGER,
                    detail TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS resolved_phones (
                    phone TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    resolved_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS peer_types (
                    peer_id INTEGER PRIMARY KEY,
                    peer_type INTEGER NOT NULL
                );
                );
                """
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def allow_peer(self, peer_id: int) -> None:
        if peer_id <= 0:
            raise ValueError("peer_id must be positive")
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO allowed_peers(peer_id, created_at) VALUES (?, ?)",
                (peer_id, time.time()),
            )
            self._conn.commit()

    def disallow_peer(self, peer_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM allowed_peers WHERE peer_id = ?", (peer_id,))
            self._conn.commit()

    def is_peer_allowed(self, peer_id: int) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM allowed_peers WHERE peer_id = ?", (peer_id,)
            ).fetchone()
        return row is not None

    def allowed_peers(self) -> list[int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT peer_id FROM allowed_peers ORDER BY peer_id"
            ).fetchall()
        return [int(row[0]) for row in rows]

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
        """Persist a message and return whether it was newly inserted."""
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT OR IGNORE INTO messages(
                    message_id, peer_id, sender_id, direction, text, received_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (message_id, peer_id, sender_id, direction, text, received_at or time.time()),
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO peer_types(peer_id, peer_type) VALUES (?, ?)",
                (peer_id, peer_type),
            )
            self._conn.commit()
        return cursor.rowcount == 1

    def reserve_outbound(
        self,
        peer_id: int,
        *,
        maximum: int,
        window_seconds: float,
    ) -> bool:
        """Atomically reserve an outbound message slot for one peer.

        A failed network send consumes its reservation. This deliberately
        favors preventing repeated automated retries over throughput.
        """
        cutoff = time.time() - window_seconds
        with self._lock:
            row = self._conn.execute(
                """
                SELECT COUNT(*) FROM audit_events
                WHERE event_type = 'outbound_reserved' AND peer_id = ? AND created_at >= ?
                """,
                (peer_id, cutoff),
            ).fetchone()
            if row is not None and int(row[0]) >= maximum:
                return False
            self._conn.execute(
                "INSERT INTO audit_events(event_type, peer_id, detail, created_at) VALUES (?, ?, ?, ?)",
                ("outbound_reserved", peer_id, f"window={window_seconds}", time.time()),
            )
            self._conn.commit()
        return True

    def list_messages(self, peer_id: int, limit: int = 20) -> list[dict[str, object]]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT message_id, peer_id, sender_id, direction, text, received_at
                FROM messages WHERE peer_id = ?
                ORDER BY received_at DESC LIMIT ?
                """,
                (peer_id, limit),
            ).fetchall()
        return [
            {
                "message_id": row[0], "peer_id": row[1], "sender_id": row[2],
                "direction": row[3], "text": row[4], "received_at": row[5],
            }
            for row in rows
        ]

    def search_messages(
        self, query: str, *, peer_id: int | None = None, limit: int = 20,
    ) -> list[dict[str, object]]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        pattern = f"%{query}%"
        with self._lock:
            if peer_id is not None:
                rows = self._conn.execute(
                    """
                    SELECT message_id, peer_id, sender_id, direction, text, received_at
                    FROM messages
                    WHERE peer_id = ? AND text LIKE ?
                    ORDER BY received_at DESC LIMIT ?
                    """,
                    (peer_id, pattern, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """
                    SELECT message_id, peer_id, sender_id, direction, text, received_at
                    FROM messages
                    WHERE text LIKE ?
                    ORDER BY received_at DESC LIMIT ?
                    """,
                    (pattern, limit),
                ).fetchall()
        return [
            {
                "message_id": row[0], "peer_id": row[1], "sender_id": row[2],
                "direction": row[3], "text": row[4], "received_at": row[5],
            }
            for row in rows
        ]

    def list_dialogs(self, limit: int = 20) -> list[dict[str, object]]:
        """Return locally observed conversations ordered by recent activity.

        The server's dialog-index RPC is not available for every web JWT.
        Incoming GetDiff events and userbot sends are already durable local
        evidence of a conversation, so expose that bounded index as a
        reliable fallback without claiming it is the account's full remote
        dialog list.
        """
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT peer_id, MAX(received_at), COUNT(*)
                FROM messages
                GROUP BY peer_id
                ORDER BY MAX(received_at) DESC, peer_id ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            types = dict(self._conn.execute("SELECT peer_id, peer_type FROM peer_types").fetchall())
        return [
            {
                "peer_id": int(row[0]),
                "peer_type": types.get(int(row[0]), 1),
                "last_message_at": float(row[1]),
                "message_count": int(row[2]),
                "source": "local_observed",
            }
            for row in rows
        ]

    def audit(self, event_type: str, *, peer_id: int | None = None, detail: str = "") -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO audit_events(event_type, peer_id, detail, created_at) VALUES (?, ?, ?, ?)",
                (event_type, peer_id, detail, time.time()),
            )
            self._conn.commit()

    def get_resolved_phone(self, phone: str) -> int | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT user_id FROM resolved_phones WHERE phone = ?", (phone,)
            ).fetchone()
        return int(row[0]) if row is not None else None

    def put_resolved_phone(self, phone: str, user_id: int) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO resolved_phones(phone, user_id, resolved_at)
                VALUES (?, ?, ?)
                ON CONFLICT(phone) DO UPDATE SET
                    user_id=excluded.user_id,
                    resolved_at=excluded.resolved_at
                """,
                (phone, user_id, time.time()),
            )
            self._conn.commit()


class MemoryUserbotStore:
    """In-memory state store for tests, ephemeral userbots, and containerized deployments."""

    def __init__(self) -> None:
        self._allowed_peers: set[int] = set()
        self._messages: list[dict[str, object]] = []
        self._message_ids: set[str] = set()
        self._audit_events: list[dict[str, object]] = []
        self._reservations: dict[int, list[float]] = {}
        self._resolved_phones: dict[str, int] = {}
        self._lock = threading.Lock()

    def close(self) -> None:
        pass

    def allow_peer(self, peer_id: int) -> None:
        if peer_id <= 0:
            raise ValueError("peer_id must be positive")
        with self._lock:
            self._allowed_peers.add(peer_id)

    def disallow_peer(self, peer_id: int) -> None:
        with self._lock:
            self._allowed_peers.discard(peer_id)

    def is_peer_allowed(self, peer_id: int) -> bool:
        with self._lock:
            return peer_id in self._allowed_peers

    def allowed_peers(self) -> list[int]:
        with self._lock:
            return sorted(self._allowed_peers)

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
        with self._lock:
            if message_id in self._message_ids:
                return False
            self._message_ids.add(message_id)
            self._messages.append({
                "message_id": message_id,
                "peer_id": peer_id,
                "sender_id": sender_id,
                "direction": direction,
                "text": text,
                "received_at": received_at or time.time(),
                "peer_type": peer_type,
            })
            return True

    def reserve_outbound(
        self,
        peer_id: int,
        *,
        maximum: int,
        window_seconds: float,
    ) -> bool:
        now = time.time()
        cutoff = now - window_seconds
        with self._lock:
            times = [t for t in self._reservations.get(peer_id, []) if t >= cutoff]
            if len(times) >= maximum:
                return False
            times.append(now)
            self._reservations[peer_id] = times
            return True

    def list_messages(self, peer_id: int, limit: int = 20) -> list[dict[str, object]]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        with self._lock:
            matching = [m for m in self._messages if m["peer_id"] == peer_id]
            matching.sort(key=lambda x: float(x["received_at"]), reverse=True)
            return matching[:limit]

    def search_messages(
        self, query: str, *, peer_id: int | None = None, limit: int = 20,
    ) -> list[dict[str, object]]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        q = query.lower()
        with self._lock:
            matching = [
                m for m in self._messages
                if (peer_id is None or m["peer_id"] == peer_id) and q in str(m["text"]).lower()
            ]
            matching.sort(key=lambda x: float(x["received_at"]), reverse=True)
            return matching[:limit]

    def list_dialogs(self, limit: int = 20) -> list[dict[str, object]]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        with self._lock:
            by_peer: dict[int, list[dict[str, object]]] = {}
            for m in self._messages:
                by_peer.setdefault(int(m["peer_id"]), []).append(m)
            dialogs = []
            for pid, msgs in by_peer.items():
                last_time = max(float(m["received_at"]) for m in msgs)
                dialogs.append({
                    "peer_id": pid,
                    "last_message_at": last_time,
                    "message_count": len(msgs),
                    "source": "local_observed",
                })
            dialogs.sort(key=lambda x: float(x["last_message_at"]), reverse=True)
            return dialogs[:limit]

    def audit(self, event_type: str, *, peer_id: int | None = None, detail: str = "") -> None:
        with self._lock:
            self._audit_events.append({
                "event_type": event_type,
                "peer_id": peer_id,
                "detail": detail,
                "created_at": time.time(),
            })

    def get_resolved_phone(self, phone: str) -> int | None:
        with self._lock:
            return self._resolved_phones.get(phone)

    def put_resolved_phone(self, phone: str, user_id: int) -> None:
        with self._lock:
            self._resolved_phones[phone] = user_id
