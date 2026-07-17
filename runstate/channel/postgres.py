"""PostgresChannel: the cross-host substrate backend.

Design: docs/specs/channel-postgres.md. One shared ``log`` table for
all runs, keyed ``(run_id, seq)``; ``PRIMARY KEY (run_id, seq)`` is the CAS
arbiter (contiguous per-run seq, cross-host-reliable). ``body`` stays opaque
``text`` (``jsonb`` would mutate the immutable snapshot). The substrate never
parses ``body``.

The one design principle: **claim = the uniform CAS; liveness = a poset the
Watcher combines.** A Postgres-specific liveness *signal* (a session advisory
lock) is added in a later cycle as a Watcher-consumed capability -- never a claim
arbiter.
"""

from __future__ import annotations

import json
import random
import threading
import time
from collections.abc import Callable
from typing import Any

import psycopg

from .base import Channel
from .envelope import Body, Envelope

# A fixed key for the schema-provisioning advisory lock: the 8 ASCII bytes of
# b"runstate" as one int8 (top bit clear, so it's a valid positive bigint). All
# cold-starters agree on this one key, so concurrent first-connectors serialize
# the DDL on it instead of racing the pg_type/pg_class catalog.
_SCHEMA_LOCK_KEY = 0x72756E7374617465  # b"runstate"

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS log (
    run_id     text   NOT NULL,
    seq        bigint NOT NULL,
    topic      text   NOT NULL,
    name       text,
    request_id text,
    body       text   NOT NULL,
    created_at double precision NOT NULL,
    PRIMARY KEY (run_id, seq)
)
"""

_CREATE_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_log_run_topic_seq ON log (run_id, topic, seq)"
)


def ensure_schema(dsn: str) -> None:
    """Provision the shared ``log`` table, once, cross-host-safely.

    ``CREATE TABLE IF NOT EXISTS`` is **not** concurrency-safe in Postgres
    (concurrent first-connectors race the ``pg_type`` / ``pg_class`` catalog -- the
    analogue of SQLite's WAL-birth race), so the DDL runs under a server-side
    ``pg_advisory_xact_lock``: two orchestrators cold-starting against a fresh DB
    serialize on the one key instead of racing. This is why DDL is here, not in
    ``__init__`` -- the channel constructor only probes for the table. Idempotent;
    the orchestration helpers (sweep, the launchers) call it at startup so
    cold-start-many-workers is self-sufficient."""
    with psycopg.connect(dsn) as conn, conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (_SCHEMA_LOCK_KEY,))
        conn.execute(_CREATE_TABLE)
        conn.execute(_CREATE_INDEX)


# The CAS: insert at seq=expected+1 iff the run's current max is still expected.
# The PRIMARY KEY (run_id, seq) is the arbiter -- a rival that committed expected+1
# first turns this into a UniqueViolation (a provable loss), not a corruption.
_CAS = """
INSERT INTO log (run_id, seq, topic, name, request_id, body, created_at)
SELECT %(run)s, %(expected)s + 1, %(topic)s, %(name)s, %(rid)s, %(body)s,
       extract(epoch FROM clock_timestamp())
WHERE (SELECT COALESCE(MAX(seq), 0) FROM log WHERE run_id = %(run)s) = %(expected)s
RETURNING seq
"""

# Unconditional append: max+1, optimistic; the PK is the sole arbiter, so a lost
# race is a UniqueViolation we retry (a fresh max each round -> termination).
_UNCONDITIONAL = """
INSERT INTO log (run_id, seq, topic, name, request_id, body, created_at)
SELECT %(run)s, COALESCE(MAX(seq), 0) + 1, %(topic)s, %(name)s, %(rid)s, %(body)s,
       extract(epoch FROM clock_timestamp())
FROM log WHERE run_id = %(run)s
RETURNING seq
"""

# Per-run write contention is low by construction (the worker is the sole value/
# lifecycle writer; a cross-host dashboard/BO sending control.* is the rare second
# writer), so this bound is never approached -- but a substrate primitive states
# its bound, and exhaustion raises a fault rather than returning a false loss.
_SEND_RETRY_BOUND = 256


def _escape_like(s: str) -> str:
    """Escape a literal string for use as a LIKE prefix (default backslash escape).
    User topics are arbitrary, so ``%`` / ``_`` in the prefix must not act as
    wildcards (passed as a bound param, so string-literal escaping doesn't apply)."""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _episode_key_str(run_id: str, started_seq: int) -> str:
    """The episode advisory-lock key MATERIAL: a length-prefixed encoding of the full
    ``(run_id, started_seq)`` pair, so distinct pairs never alias (a ``:`` inside
    run_id can't shift the boundary). ``hashtextextended`` turns this into the int8
    lock key *server-side*, so the holder and an independent observer derive a
    bit-identical key cross-process -- NOT a two-``int4`` key (which collapses to a
    32-bit collision domain since first episodes share ``started_seq``) and NOT
    Python's salted ``hash()`` (unstable across processes)."""
    return f"{len(run_id)}:{run_id}:{started_seq}"


# Whether the episode's session advisory lock is granted (to anyone). The bigint key
# is stored split across pg_locks as (classid=high32, objid=low32, objsubid=1); we
# reconstruct the halves by shift-then-mask -- mask-after-shift is independent of
# >>'s sign-extension, and sidesteps the bigint overflow a (classid<<32 | objid)
# reconstruction hits for high-bit-set (negative) keys.
_EPISODE_ALIVE = """
WITH k(v) AS (SELECT hashtextextended(%(s)s, 0))
SELECT EXISTS (
    SELECT 1 FROM pg_locks, k
    WHERE locktype = 'advisory' AND granted AND objsubid = 1
      AND classid = ((k.v >> 32) & 4294967295)::oid
      AND objid   = (k.v & 4294967295)::oid
)
"""

# The same probe scoped to THIS backend -- the pooler self-check: right after acquiring
# we must see the lock held by our own pid, else a transaction-mode pooler reassigned
# the backend and the session lock is worthless.
_EPISODE_HELD_BY_ME = """
WITH k(v) AS (SELECT hashtextextended(%(s)s, 0))
SELECT EXISTS (
    SELECT 1 FROM pg_locks, k
    WHERE locktype = 'advisory' AND granted AND objsubid = 1
      AND pid = pg_backend_pid()
      AND classid = ((k.v >> 32) & 4294967295)::oid
      AND objid   = (k.v & 4294967295)::oid
)
"""


class PostgresChannel(Channel):
    """A handle on one run's slice of the shared ``log`` table (see ``base.Channel``).

    All ops scope to ``WHERE run_id = %(run)s``; ``PRIMARY KEY (run_id, seq)`` is the
    CAS arbiter. One dedicated persistent connection in ``autocommit=True``, serialized
    by an internal ``threading.Lock`` (psycopg connections aren't safe for concurrent
    statement use, and the ThreadLauncher hands one handle to both the worker and the
    Watcher). ``__init__`` only probes for the table -- ``ensure_schema(dsn)`` must have
    run first (the DDL is concurrency-unsafe; it lives out of the hot path)."""

    def __init__(
        self,
        dsn: str,
        run_id: str,
        *,
        json_default: Callable[[object], object] | None = None,
    ) -> None:
        self._run_id = run_id
        self._json_default = json_default
        self._lock = threading.Lock()
        self._conn = psycopg.connect(dsn, autocommit=True)
        # lock_timeout is the busy_timeout analogue: a wedged conflicting writer
        # makes the CAS raise (indeterminate) rather than hang forever.
        self._conn.execute("SET lock_timeout = '5000ms'")
        registered = self._conn.execute("SELECT to_regclass('log')").fetchone()
        if registered is None or registered[0] is None:
            self._conn.close()
            raise RuntimeError(
                "the postgres 'log' table is absent; call "
                "runstate.channel.postgres.ensure_schema(dsn) first"
            )

    def send(
        self,
        body: Body,
        *,
        topic: str,
        name: str | None = None,
        request_id: str | None = None,
        expected_seq: int | None = None,
    ) -> int | None:
        # json_default (sender-side) coerces exotic value payloads on the way out;
        # the stored text is always standard JSON, so any reader uses plain loads.
        body_json = json.dumps(body, default=self._json_default, separators=(",", ":"))
        params = {
            "run": self._run_id,
            "topic": topic,
            "name": name,
            "rid": request_id,
            "body": body_json,
            "expected": expected_seq,
        }
        with self._lock:
            if expected_seq is not None:
                # Compare-and-append. A UniqueViolation means a rival committed
                # (run, expected+1) first -> the claim is PROVABLY lost (None), the
                # same answer as the gate failing. The catch is UniqueViolation-
                # specific: a connection drop or lock_timeout exhaustion (a wedged
                # rival holding an uncommitted (run, expected+1)) is INDETERMINATE
                # and must propagate as a raise, never be synthesized into a loss.
                try:
                    row = self._conn.execute(_CAS, params).fetchone()
                except psycopg.errors.UniqueViolation:
                    return None
                # row is None when the gate (max == expected) was false: log moved.
                return int(row[0]) if row else None
            # Unconditional append: optimistic max+1, retry the PK race. Each round
            # recomputes max (strictly advancing), so it terminates; bound exhaustion
            # is a fault (raise), not a false loss.
            for _ in range(_SEND_RETRY_BOUND):
                try:
                    row = self._conn.execute(_UNCONDITIONAL, params).fetchone()
                except psycopg.errors.UniqueViolation:
                    time.sleep(random.uniform(0.0, 0.002))  # jitter to break herds
                    continue
                assert row is not None  # the aggregate SELECT always yields one row
                return int(row[0])
            raise RuntimeError(
                f"postgres unconditional send for run {self._run_id!r} exhausted "
                f"{_SEND_RETRY_BOUND} retries under contention"
            )

    def read(
        self,
        after: int = 0,
        *,
        topics: list[str] | None = None,
        name: str | None = None,
        request_ids: list[str] | None = None,
        limit: int | None = None,
    ) -> list[Envelope]:
        if topics is not None and not topics:
            return (
                []
            )  # "among these zero topics": vacuously none (no empty OR-clause SQL)
        where = ["run_id = %s", "seq > %s"]
        params: list[Any] = [self._run_id, after]
        if topics is not None:
            ors = []
            for t in topics:
                if t.endswith(".>"):
                    ors.append("topic LIKE %s")
                    params.append(
                        _escape_like(t[:-1]) + "%"
                    )  # "control.>" -> "control.%"
                else:
                    ors.append("topic = %s")
                    params.append(t)
            where.append("(" + " OR ".join(ors) + ")")
        if name is not None:
            where.append("name = %s")
            params.append(name)
        if request_ids is not None:
            # visibility: the caller's own request_ids PLUS unaddressed broadcasts
            where.append("(request_id IS NULL OR request_id = ANY(%s))")
            params.append(list(request_ids))
        sql = (
            "SELECT seq, topic, name, request_id, body FROM log WHERE "
            + " AND ".join(where)
            + " ORDER BY seq"
        )
        if limit is not None:
            sql += " LIMIT %s"
            params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [
            Envelope(seq, topic, name, request_id, json.loads(body))
            for (seq, topic, name, request_id, body) in rows
        ]

    def latest(self, topic: str, name: str | None = None) -> Envelope | None:
        sql = (
            "SELECT seq, topic, name, request_id, body FROM log"
            " WHERE run_id = %s AND topic = %s"
        )
        params: list[Any] = [self._run_id, topic]
        if name is not None:
            sql += " AND name = %s"
            params.append(name)
        sql += " ORDER BY seq DESC LIMIT 1"
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
        if row is None:
            return None
        seq, topic, name, request_id, body = row
        return Envelope(seq, topic, name, request_id, json.loads(body))

    def last_seq(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM log WHERE run_id = %s",
                [self._run_id],
            ).fetchone()
        assert row is not None  # aggregate always yields one row
        return int(row[0])

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --- liveness capability (EpisodeHolder / EpisodeProbe) ---
    # claim = the uniform CAS; the lock is a Watcher-consumed liveness SIGNAL only.

    def hold_episode(self, started_seq: int) -> None:
        """Pin this episode's liveness on THIS channel's connection (worker side, after
        the claim CAS). Session-scoped, so it auto-releases when the connection dies --
        the whole point. Taken *after* the claim: a signal, not a gate."""
        key_str = _episode_key_str(self._run_id, started_seq)
        with self._lock:
            self._conn.execute(
                "SELECT pg_advisory_lock(hashtextextended(%s, 0))", (key_str,)
            )
            held = self._conn.execute(_EPISODE_HELD_BY_ME, {"s": key_str}).fetchone()
        if not (held and held[0]):
            raise RuntimeError(
                "episode lock not visible for this backend right after acquiring it; a "
                "transaction-mode pooler (e.g. pgbouncer) is in the path -- use a direct "
                "or session-pooled endpoint"
            )

    def episode_alive(self, started_seq: int) -> bool:
        """Whether this episode's lock is still held by *someone* (observer side). True
        is a definitive cross-host liveness signal -- where a foreign-host handle probe
        abstains; False past a birth grace is a definitive death (the Watcher applies
        the grace + the staleness floor)."""
        key_str = _episode_key_str(self._run_id, started_seq)
        with self._lock:
            row = self._conn.execute(_EPISODE_ALIVE, {"s": key_str}).fetchone()
        return bool(row and row[0])
