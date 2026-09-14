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
        return [
            {
                "peer_id": int(row[0]),
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
