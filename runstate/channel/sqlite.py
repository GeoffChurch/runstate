"""SqliteChannel: the v0.2 substrate — a per-run append-only topic log.

One SQLite file per run; one ``log`` table whose autoincrement ``seq`` is the
total order. Reads are non-destructive (caller-owned cursors); the ``body`` is
stored as opaque JSON and never interpreted by the substrate.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time

from .envelope import Envelope

_SCHEMA = """
CREATE TABLE IF NOT EXISTS log (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    topic      TEXT NOT NULL,
    name       TEXT,
    request_id TEXT,
    body       TEXT NOT NULL,
    created_at REAL NOT NULL
);
-- latest(topic) is WHERE topic=? ORDER BY seq DESC LIMIT 1, called on every
-- Watcher poll (twice, for stopped/terminated, which usually don't exist yet).
-- Without this it's a full table scan per poll; the index makes it a seek.
CREATE INDEX IF NOT EXISTS idx_log_topic_seq ON log (topic, seq);
"""


class SqliteChannel:
    """A per-run topic log backed by one SQLite file."""

    def __init__(self, path, *, json_default=None):
        self._json_default = json_default
        self._conn = sqlite3.connect(
            str(path), isolation_level=None, check_same_thread=False
        )
        # ONE handle may be shared across threads (ThreadLauncher hands the same
        # instance to the worker thread and the orchestrator's Watcher), and the
        # sqlite3 module mis-handles concurrent statement use on one connection
        # even when compiled threadsafe — so every connection touch is serialized.
        self._lock = threading.Lock()
        # WAL conversion races at db birth: it takes a SHARED->EXCLUSIVE lock
        # escalation that sqlite exempts from the busy handler (deadlock-prone
        # path), so when several fresh connections collide -- the multi-claimant
        # ensure-create topology -- losers raise SQLITE_BUSY no matter what
        # busy_timeout says. The mode is persistent in the file (one opener
        # converts; for everyone else the pragma is a no-op read), so a brief
        # bounded retry absorbs birth contention entirely.
        for retries_left in reversed(range(20)):
            try:
                self._conn.execute("PRAGMA journal_mode=WAL")
                break
            except sqlite3.OperationalError as exc:
                busy = getattr(exc, "sqlite_errorcode", None) == sqlite3.SQLITE_BUSY
                if not busy or not retries_left:
                    raise
                time.sleep(0.01)
        # Cross-connection CAS contention (send(expected_seq=)): a nonzero
        # busy_timeout makes the losing claimant WAIT on the winner's write
        # transaction and then cleanly observe the moved seq (returning None)
        # instead of raising "database is locked". Single-writer-per-run is the
        # norm, so outside a genuine multi-claimant race this never engages.
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(_SCHEMA)

    def send(self, body: dict, *, topic: str, name=None, request_id=None,
             expected_seq=None) -> int | None:
        # json_default (sender-side) coerces exotic value payloads on the way out;
        # the stored text is always standard JSON, so any reader uses plain loads.
        body_json = json.dumps(body, default=self._json_default, separators=(",", ":"))
        params = (topic, name, request_id, body_json, time.time())
        with self._lock:
            if expected_seq is None:
                # Unconditional append: a single autocommitted INSERT (the
                # connection runs in autocommit mode, isolation_level=None).
                return self._conn.execute(
                    "INSERT INTO log (topic, name, request_id, body, created_at)"
                    " VALUES (?, ?, ?, ?, ?)",
                    params,
                ).lastrowid
            # Compare-and-append (the run-episodes self-claim / §12.1 single-spawn
            # guard). The check and the INSERT must be one critical section across
            # connections AND processes; a guarded INSERT is ONE statement, hence
            # one implicit write transaction, which sqlite serializes against
            # every other writer. Atomicity by construction also means no
            # multi-statement BEGIN..COMMIT window on this connection for another
            # thread's send to fall into (and be erased by a rollback), and no
            # uncommitted state for same-connection reads to phantom-see.
            try:
                cur = self._conn.execute(
                    "INSERT INTO log (topic, name, request_id, body, created_at)"
                    " SELECT ?, ?, ?, ?, ?"
                    " WHERE (SELECT COALESCE(MAX(seq), 0) FROM log) = ?",
                    (*params, expected_seq),
                )
            except sqlite3.OperationalError as exc:
                if getattr(exc, "sqlite_errorcode", None) != sqlite3.SQLITE_BUSY:
                    raise
                # busy_timeout exhausted: a competing writer held the file's write
                # lock for the whole wait, so the guard never ran. Disambiguate
                # (WAL keeps the log readable while that writer holds the lock):
                # if the log moved past expected_seq the claim is PROVABLY lost
                # -> None, the same answer as losing the guard. If it hasn't
                # moved, the holder is wedged mid-transaction and the outcome is
                # indeterminate -> surface the fault. Synthesizing a loss here
                # would be a silent liveness hole: a holder that then rolls back
                # would leave the run claimed by nobody.
                last = self._conn.execute(
                    "SELECT COALESCE(MAX(seq), 0) FROM log"
                ).fetchone()[0]
                if last > expected_seq:
                    return None
                raise
            # Gate on rowcount: when the guard fails nothing was inserted, and
            # lastrowid is a stale leftover from a prior INSERT, not a seq.
            return cur.lastrowid if cur.rowcount == 1 else None

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
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [
            Envelope(seq, topic, name, request_id, json.loads(body))
            for (seq, topic, name, request_id, body) in rows
        ]

    def latest(self, topic: str, name=None) -> Envelope | None:
        with self._lock:
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
        with self._lock:
            self._conn.close()
