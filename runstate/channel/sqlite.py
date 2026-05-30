"""SqliteChannel: the v0.2 substrate — a per-run append-only topic log.

One SQLite file per run; one ``log`` table whose autoincrement ``seq`` is the
total order. Reads are non-destructive (caller-owned cursors); the ``body`` is
stored as opaque JSON and never interpreted by the substrate.
"""

from __future__ import annotations

import json
import sqlite3
import time

from . import Envelope

_SCHEMA = """
CREATE TABLE IF NOT EXISTS log (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    topic      TEXT NOT NULL,
    name       TEXT,
    request_id TEXT,
    body       TEXT NOT NULL,
    created_at REAL NOT NULL
);
"""


class SqliteChannel:
    """A per-run topic log backed by one SQLite file."""

    def __init__(self, path):
        self._conn = sqlite3.connect(
            str(path), isolation_level=None, check_same_thread=False
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)

    def send(self, body: dict, *, topic: str, name=None, request_id=None) -> int:
        cur = self._conn.execute(
            "INSERT INTO log (topic, name, request_id, body, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (topic, name, request_id, json.dumps(body, separators=(",", ":")), time.time()),
        )
        return cur.lastrowid

    def read(
        self,
        after: int = 0,
        *,
        topics=None,
        name=None,
        request_ids=None,
        limit=None,
    ) -> list[Envelope]:
        where = ["seq > ?"]
        params: list = [after]
        if topics is not None:
            ors = []
            for t in topics:
                if t.endswith(".>"):
                    ors.append("topic GLOB ?")
                    params.append(t[:-1] + "*")  # "control.>" -> "control.*"
                else:
                    ors.append("topic = ?")
                    params.append(t)
            where.append("(" + " OR ".join(ors) + ")")
        if name is not None:
            where.append("name = ?")
            params.append(name)
        if request_ids is not None:
            # visibility: the caller's own request_ids PLUS unaddressed broadcasts
            placeholders = ", ".join("?" * len(request_ids))
            where.append(f"(request_id IS NULL OR request_id IN ({placeholders}))")
            params.extend(request_ids)
        sql = (
            "SELECT seq, topic, name, request_id, body FROM log WHERE "
            + " AND ".join(where)
            + " ORDER BY seq"
        )
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        rows = self._conn.execute(sql, params).fetchall()
        return [
            Envelope(seq, topic, name, request_id, json.loads(body))
            for (seq, topic, name, request_id, body) in rows
        ]

    def latest(self, topic: str, name=None) -> Envelope | None:
        if name is None:
            row = self._conn.execute(
                "SELECT seq, topic, name, request_id, body FROM log"
                " WHERE topic = ? ORDER BY seq DESC LIMIT 1",
                (topic,),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT seq, topic, name, request_id, body FROM log"
                " WHERE topic = ? AND name = ? ORDER BY seq DESC LIMIT 1",
                (topic, name),
            ).fetchone()
        if row is None:
            return None
        seq, topic, name, request_id, body = row
        return Envelope(seq, topic, name, request_id, json.loads(body))

    def close(self) -> None:
        self._conn.close()
