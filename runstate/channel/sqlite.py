"""SqliteChannel: stdlib sqlite3 Channel backend.

One DB per run at <root>/<run_id>/channel.db. WAL mode for concurrent
read with one writer per direction. Unlike FileChannel, SqliteChannel
retains consumed messages (rows are marked consumed_at instead of
deleted), so history is preserved for users who want to inspect or
replay.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Literal, Optional


_POLL_INTERVAL_S = 0.050


_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    direction TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at REAL NOT NULL,
    consumed_at REAL
);
CREATE INDEX IF NOT EXISTS idx_messages_direction_consumed
    ON messages (direction, consumed_at);
"""


class SqliteChannel:
    """SQLite-based Channel: one row per message, durable transactional writes."""

    def __init__(
        self,
        *,
        run_id: str,
        role: Literal["worker", "orchestrator"],
        root: str,
    ):
        self.run_id = run_id
        self.role = role
        self._root = Path(root)
        self._run_dir = self._root / run_id
        self._run_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = self._run_dir / "channel.db"

        if role == "worker":
            self._send_dir = "to_orchestrator"
            self._recv_dir = "to_worker"
        elif role == "orchestrator":
            self._send_dir = "to_worker"
            self._recv_dir = "to_orchestrator"
        else:
            raise ValueError(f"role must be 'worker' or 'orchestrator', got {role!r}")

        # Open the connection. isolation_level=None gives us autocommit
        # for our explicit BEGIN/COMMIT use; we use WAL for concurrent
        # readers + one writer.
        self._conn: Optional[sqlite3.Connection] = sqlite3.connect(
            str(self._db_path),
            isolation_level=None,
            timeout=30.0,
            check_same_thread=False,
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        # Apply schema. CREATE IF NOT EXISTS makes this idempotent across
        # multiple processes opening the same db.
        self._conn.executescript(_SCHEMA)

    def send(self, message: dict) -> None:
        if self._conn is None:
            raise RuntimeError("Channel is closed")
        payload = json.dumps(message, sort_keys=True, separators=(",", ":"))
        self._conn.execute(
            "INSERT INTO messages (direction, payload, created_at) VALUES (?, ?, ?)",
            (self._send_dir, payload, time.time()),
        )

    def recv(self, timeout: Optional[float] = None) -> Optional[dict]:
        if self._conn is None:
            raise RuntimeError("Channel is closed")
        deadline = None if timeout is None else time.monotonic() + timeout

        while True:
            msg = self._try_recv_one()
            if msg is not None:
                return msg
            if timeout == 0:
                return None
            if deadline is not None and time.monotonic() >= deadline:
                return None
            time.sleep(_POLL_INTERVAL_S)

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ----- internals -----

    def _try_recv_one(self) -> Optional[dict]:
        """Attempt one non-blocking receive. Returns the message or None.

        Uses an explicit BEGIN IMMEDIATE to serialize concurrent receivers
        on the same direction (acquires a RESERVED lock that blocks other
        writers). Within the transaction, finds the smallest-id unread
        message, marks it consumed, and returns its payload.
        """
        if self._conn is None:
            return None
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            row = self._conn.execute(
                "SELECT id, payload FROM messages "
                "WHERE direction = ? AND consumed_at IS NULL "
                "ORDER BY id LIMIT 1",
                (self._recv_dir,),
            ).fetchone()
            if row is None:
                self._conn.execute("COMMIT")
                return None
            msg_id, payload = row
            self._conn.execute(
                "UPDATE messages SET consumed_at = ? WHERE id = ?",
                (time.time(), msg_id),
            )
            self._conn.execute("COMMIT")
            return json.loads(payload)
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    def iter_history(self, direction: Literal["to_worker", "to_orchestrator"]):
        """Yield all messages ever sent in the given direction, oldest first.

        Available only on SqliteChannel (FileChannel deletes consumed
        messages and cannot replay). Useful for post-hoc analysis and
        the `result.replay_messages()` story in higher-level helpers.
        """
        if self._conn is None:
            raise RuntimeError("Channel is closed")
        for (payload,) in self._conn.execute(
            "SELECT payload FROM messages WHERE direction = ? ORDER BY id",
            (direction,),
        ):
            yield json.loads(payload)
