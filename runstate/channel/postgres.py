"""PostgresChannel: the cross-host substrate backend.

Design: docs/specs/channel-postgres.md. One shared ``log`` table for
all runs, keyed ``(run_id, seq)``; ``PRIMARY KEY (run_id, seq)`` is the CAS
arbiter (contiguous per-run seq, cross-host-reliable). ``body`` stays opaque
``text`` (``jsonb`` would mutate the immutable snapshot). The substrate never
parses ``body``.

The one design principle: **claim = the uniform CAS; liveness = evidence.** Each
connection holds an advisory lock under a fresh key from before its first write,
and every row is stamped with its writer's key and server, so the session probe
(``SessionProbe``) can say whether the session that wrote a claim has ended:
evidence for a death report, never a claim arbiter.
"""

from __future__ import annotations

import json
import random
import secrets
import threading
import time
from collections.abc import Callable
from typing import Any

import psycopg
from psycopg import sql

from .. import formats
from ..formats import FORMATS, LOG_FORMAT, LogFormatMismatch, LogFormatMissing
from .base import Channel, RunNotFound, Session
from .envelope import Body, Envelope

# A fixed key for the schema-provisioning advisory lock: the 8 ASCII bytes of
# b"runstate" as one int8 (top bit clear, so it's a valid positive bigint). All
# cold-starters agree on this one key, so concurrent first-connectors serialize
# the DDL on it instead of racing the pg_type/pg_class catalog.
_SCHEMA_LOCK_KEY = 0x72756E7374617465  # b"runstate"


def ensure_schema(dsn: str) -> None:
    """Provision the shared ``log`` table, once, cross-host-safely.

    ``CREATE TABLE IF NOT EXISTS`` is **not** concurrency-safe in Postgres
    (concurrent first-connectors race the ``pg_type`` / ``pg_class`` catalog -- the
    analogue of SQLite's WAL-birth race), so the DDL runs under a server-side
    ``pg_advisory_xact_lock``: two orchestrators cold-starting against a fresh DB
    serialize on the one key instead of racing. This is why DDL is here, not in
    ``__init__`` -- the channel constructor only probes for the table. Idempotent;
    the orchestration helpers (the launchers) call it at startup so
    cold-start-many-workers is self-sufficient."""
    schema = FORMATS[LOG_FORMAT].pg_schema()
    with psycopg.connect(dsn) as conn, conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (_SCHEMA_LOCK_KEY,))
        conn.execute(
            sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema))
        )
        conn.execute(
            sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(schema))
        )
        for ddl in FORMATS[LOG_FORMAT].pg_ddl:  # the tables of the format it writes
            conn.execute(ddl)


def _has_rows(conn: psycopg.Connection[Any], schema: str, run_id: str) -> bool:
    table = sql.SQL("{}.log").format(sql.Identifier(schema)).as_string(conn)
    reg = conn.execute("SELECT to_regclass(%s)", [table]).fetchone()
    if reg is None or reg[0] is None:
        return False
    hit = conn.execute(
        sql.SQL("SELECT 1 FROM {}.log WHERE run_id = %s LIMIT 1").format(
            sql.Identifier(schema)
        ),
        [run_id],
    ).fetchone()
    return hit is not None


# The legacy table: the `log` that an UNQUALIFIED name resolves to under the
# connection's own search path, as every statement of a runstate from before
# versioned addresses was unqualified (a DSN may carry `options=-csearch_path=`).
# It is runstate's only if it has a `run_id` column (another application's `log`
# is not), and legacy only outside the versioned `runstate_v…` schemas.
_LEGACY_LOG = """
SELECT n.nspname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.oid = to_regclass('log') AND NOT starts_with(n.nspname, 'runstate_v')
  AND EXISTS (SELECT 1 FROM pg_attribute a
              WHERE a.attrelid = c.oid AND a.attname = 'run_id' AND NOT a.attisdropped)
"""


def check_format(conn: psycopg.Connection[Any], run_id: str) -> bool:
    """log-formats.md §4 on postgres. Raises for a newer format in the database,
    or for the run held by an older format or the legacy table. Returns whether
    the run has rows at the current address. Call it before setting the
    connection's search path: the legacy table is found under its own."""
    names = [r[0] for r in conn.execute("SELECT nspname FROM pg_namespace").fetchall()]
    newer = formats.newer_in(names, prefix="runstate_v", sep="_", than=LOG_FORMAT)
    if newer:
        raise LogFormatMismatch(
            found=newer[-1],
            expected=LOG_FORMAT,
            where="this database",
            command=formats.POSTGRES_MIGRATE_COMMAND,
        )
    if _has_rows(conn, FORMATS[LOG_FORMAT].pg_schema(), run_id):
        return True
    for version, layout in formats.older_than(LOG_FORMAT):
        if _has_rows(conn, layout.pg_schema(), run_id):
            raise LogFormatMismatch(
                found=version,
                expected=LOG_FORMAT,
                where=f"schema {layout.pg_schema()}",
                command=formats.POSTGRES_MIGRATE_COMMAND,
            )
    legacy = conn.execute(_LEGACY_LOG).fetchone()
    if legacy is not None and _has_rows(conn, legacy[0], run_id):
        raise LogFormatMissing(
            where=f"{legacy[0]}.log",
            instructions=formats.postgres_onboarding(run_id, legacy[0]),
        )
    return False


# The server a stamp names (channel-postgres.md, "Liveness"): its system identifier,
# which a physical standby shares, and the timeline its WAL is being inserted on,
# which a promotion advances at once. Not pg_control_checkpoint()'s timeline: since
# PostgreSQL 13 a promotion writes only an end-of-recovery record and requests a
# spread checkpoint, so the checkpoint's timeline stayed the old one for more than
# 100 s after a promotion, and a probe there took the promoted server for the old
# primary. pg_split_walfile_name needs PostgreSQL 16, runstate's floor. Evaluated in
# the statement that writes each row, so a stamp is always the timeline the row was
# written on, even on a connection opened on a standby that was promoted since; an
# INSERT succeeds only out of recovery, where pg_current_wal_lsn() is defined.
_IDENTITY = (
    "(SELECT system_identifier FROM pg_control_system())::text || ':' ||"
    " (pg_split_walfile_name(pg_walfile_name(pg_current_wal_lsn()))).timeline_id::text"
)

# The oldest server the stamp can be written on, as ``server_version_num``:
# pg_split_walfile_name arrived in PostgreSQL 16. The open refuses an older server,
# so it fails before any write rather than at its first claim with UndefinedFunction.
_SERVER_VERSION_FLOOR = 160000

# The CAS: insert at seq=expected+1 iff the run's current max is still expected.
# The PRIMARY KEY (run_id, seq) is the arbiter -- a rival that committed expected+1
# first turns this into a UniqueViolation (a provable loss), not a corruption.
_CAS = f"""
INSERT INTO log (run_id, seq, topic, name, request_id, body, created_at,
                 writer_key, writer_server)
SELECT %(run)s, %(expected)s + 1, %(topic)s, %(name)s, %(rid)s, %(body)s,
       extract(epoch FROM clock_timestamp()), %(key)s, {_IDENTITY}
WHERE (SELECT COALESCE(MAX(seq), 0) FROM log WHERE run_id = %(run)s) = %(expected)s
RETURNING seq
"""

# Unconditional append: max+1, optimistic; the PK is the sole arbiter, so a lost
# race is a UniqueViolation we retry (a fresh max each round -> termination).
_UNCONDITIONAL = f"""
INSERT INTO log (run_id, seq, topic, name, request_id, body, created_at,
                 writer_key, writer_server)
SELECT %(run)s, COALESCE(MAX(seq), 0) + 1, %(topic)s, %(name)s, %(rid)s, %(body)s,
       extract(epoch FROM clock_timestamp()), %(key)s, {_IDENTITY}
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


# --- the session probe (commits-and-lineage.md §3; channel-postgres.md, "Liveness") ---

# The server this connection is on, as a stamp names it, or NULL in recovery. A probe
# reads pg_locks only on the server that wrote the stamp, never a standby (whose
# pg_locks know nothing of the primary's sessions), so NULL or a mismatch abstains.
# CASE short-circuits, so pg_current_wal_lsn() is never evaluated in recovery.
_SERVER = f"SELECT CASE WHEN pg_is_in_recovery() THEN NULL ELSE {_IDENTITY} END"

# Draws of a fresh key before the open gives up. A collision with a held key is a
# 2^-64 event per live connection, so a second draw is already a sign of something
# else (a key space someone fills on purpose); the bound makes that a loud failure.
_KEY_DRAWS = 16

# A granted lock on the bigint advisory key %(key)s, by any backend in any database;
# the two queries below narrow it. The key is stored split across pg_locks as
# (classid=high32, objid=low32, objsubid=1); shift-then-mask is independent of >>'s
# sign extension.
_KEY_GRANTED = """
    SELECT 1 FROM pg_locks
    WHERE locktype = 'advisory' AND granted AND objsubid = 1
      AND classid = ((%(key)s::bigint >> 32) & 4294967295)::oid
      AND objid   = (%(key)s::bigint & 4294967295)::oid
"""

# Whether the key is granted in THIS database (pg_locks is cluster-wide, and
# advisory keys are per database), to any backend.
_KEY_HELD = f"""
SELECT EXISTS ({_KEY_GRANTED}
      AND database = (SELECT oid FROM pg_database WHERE datname = current_database())
)
"""

# The pooler self-check: right after taking its key, the connection must see the
# lock held by its own backend, else a transaction-mode pooler reassigned the
# backend between statements and the session lock is worthless. Run at the open,
# before any write, so a pooler fails the open and can never strand a claim.
_KEY_HELD_BY_ME = f"""
SELECT EXISTS ({_KEY_GRANTED}
      AND pid = pg_backend_pid()
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
        create: bool = True,
        json_default: Callable[[object], object] | None = None,
    ) -> None:
        self._run_id = run_id
        self._json_default = json_default
        self._lock = threading.Lock()
        self._conn = psycopg.connect(dsn, autocommit=True)
        try:  # one guard: a failure anywhere in the open closes the connection
            self._key = self._open(run_id, create=create)
        except BaseException:
            self._conn.close()
            raise

    def _open(self, run_id: str, *, create: bool) -> int:
        """Open the connection on the run, and return its session key."""
        # lock_timeout is the busy_timeout analogue: a wedged conflicting writer
        # makes the CAS raise (indeterminate) rather than hang forever.
        self._conn.execute("SET lock_timeout = '5000ms'")
        key = self._become_writer()
        has_rows = check_format(self._conn, run_id)  # before the search path
        self._conn.execute(
            sql.SQL("SET search_path TO {}").format(
                sql.Identifier(FORMATS[LOG_FORMAT].pg_schema())
            )
        )
        registered = self._conn.execute("SELECT to_regclass('log')").fetchone()
        if registered is None or registered[0] is None:
            raise RuntimeError(
                "the postgres 'log' table is absent; call "
                "runstate.channel.postgres.ensure_schema(dsn) first"
            )
        if not create:
            # Attach (create=False): the constructor above is already non-creating
            # (it only probes for the shared *table*). Existence is per-run: no
            # rows WHERE run_id=X == the run doesn't exist. This is the semantic
            # sqlite/memory now conform to -- postgres embodies it natively.
            if not has_rows:
                raise RunNotFound(f"run {run_id!r} has no records (postgres backend)")
        return key

    def _become_writer(self) -> int:
        """Make this connection a durable writer with a session key of its own, before
        any write (channel-postgres.md, "Liveness"), in this order:

        - ``synchronous_commit = on``: else a commit is acknowledged before it is
          durable, and a crash loses rows readers saw and reuses their seqs. Set on
          this connection, whatever the database or role defaults to.
        - refuse a server older than PostgreSQL 16 (``_SERVER_VERSION_FLOOR``): every
          row's stamp calls ``pg_split_walfile_name``, which it lacks, so its first
          write would fail.
        - refuse ``fsync = off``: the same loss, server-wide, which a connection cannot
          fix (14 to 2,386 visible rows lost in 40 of 40 crash trials; review §12,
          item 15).
        - a fresh random key, taken by try-lock: never derived from the run or the
          claim, which every racing claimant shares and which would make the lock a
          claim gate. Drawn again while another connection holds it.
        - the pooler self-check.

        Returns the key, which is stamped on every row this connection writes. The
        server's identity is not read here: each row's statement stamps the timeline it
        is written on."""
        self._conn.execute("SET synchronous_commit = on")
        version = self._conn.execute("SHOW server_version_num").fetchone()
        assert version is not None  # SHOW always yields one row
        if int(version[0]) < _SERVER_VERSION_FLOOR:
            raise RuntimeError(
                f"the postgres server is version {version[0]}, below runstate's floor "
                f"of PostgreSQL {_SERVER_VERSION_FLOOR // 10000} "
                f"(server_version_num {_SERVER_VERSION_FLOOR}): every row's stamp "
                "calls pg_split_walfile_name, which PostgreSQL 16 introduced"
            )
        fsync = self._conn.execute("SHOW fsync").fetchone()
        assert fsync is not None  # SHOW always yields one row
        if fsync[0] == "off":
            raise RuntimeError(
                "the postgres server runs with fsync = off, so a crash loses rows "
                "readers already saw and reuses their seqs; runstate refuses it"
            )
        for _ in range(_KEY_DRAWS):
            key = int.from_bytes(secrets.token_bytes(8), "big", signed=True)
            taken = self._conn.execute(
                "SELECT pg_try_advisory_lock(%s::bigint)", (key,)
            ).fetchone()
            assert taken is not None  # a scalar SELECT always yields one row
            if taken[0]:
                break
        else:
            raise RuntimeError(
                f"no free advisory-lock key in {_KEY_DRAWS} random draws; another "
                "party holds keys runstate draws from"
            )
        held = self._conn.execute(_KEY_HELD_BY_ME, {"key": key}).fetchone()
        assert held is not None  # a scalar SELECT always yields one row
        if not held[0]:
            raise RuntimeError(
                "session lock not visible for this backend right after taking it; a "
                "transaction-mode pooler (e.g. pgbouncer) is in the path -- use a direct "
                "or session-pooled endpoint"
            )
        return key

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
            "key": self._key,
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
            if request_ids:
                where.append("(request_id IS NULL OR request_id = ANY(%s))")
                params.append(list(request_ids))
            else:
                # zero ids of one's own -> only the broadcasts. Explicit branch:
                # an empty array parameter's adaptation is driver-dependent, and
                # every backend must answer this edge identically.
                where.append("request_id IS NULL")
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

    # --- the session probe (SessionProbe) ---
    # Evidence for a death report, which report_if_dead writes; never a claim arbiter.

    def writer_session(self, seq: int) -> Session | None:
        """The stamp on the record at ``seq``, or None when it has none: a row the
        migration store copied, which no live session wrote."""
        with self._lock:
            row = self._conn.execute(
                "SELECT writer_server, writer_key FROM log"
                " WHERE run_id = %s AND seq = %s",
                [self._run_id, seq],
            ).fetchone()
        if row is None:
            raise LookupError(f"run {self._run_id!r} has no record at seq {seq}")
        server, key = row
        if server is None or key is None:
            return None
        return Session(server=server, key=key)

    def session_ended(self, session: Session) -> bool | None:
        """Whether no connection holds ``session.key``, read on the server that wrote
        the stamp. None when this connection cannot tell: it is on another server (a
        different system or timeline), or on a standby, whose pg_locks know nothing of
        the primary's sessions. The identity is read at each probe, since a server
        this connection reached as a standby may since have been promoted."""
        with self._lock:
            identity = self._conn.execute(_SERVER).fetchone()
            assert identity is not None  # a scalar SELECT always yields one row
            if identity[0] != session.server:  # NULL in recovery: never a match
                return None
            held = self._conn.execute(_KEY_HELD, {"key": session.key}).fetchone()
        assert held is not None  # a scalar SELECT always yields one row
        return not held[0]
