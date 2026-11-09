"""Where logs are: the places a run's channel is opened from
(docs/specs/channel-locators.md), and run copy, one run's log copied between two
of them at its format, and dropped (docs/specs/run-copy.md).

A place is one object, built where the program reads its configuration and passed
down: ``SqliteLogs(root)``, ``PostgresLogs(dsn)`` or ``MemoryLogs()``. Each opens a
run's channel (``create``, ``attach``: the ``Logs`` Protocol). The two another
process can open also spell themselves as environment variables (``child_env``:
``ProcessLogs``), which ``from_env`` reads back, and are the endpoints of a copy
(``CopyLogs``).

A copy keeps every record's ``seq`` and ``created_at``, which no append can: every
append assigns the next seq and stamps the clock. So the copy writes the format's
rows itself, here, rather than a consumer writing runstate's tables.

A run has exactly one writable log, and every copy of it is sealed
(log-formats.md §2, rule 6). A copy into SQLite is a snapshot, written sealed. A
copy into Postgres is the run's writable log, so its source is sealed first; a
Postgres log cannot be sealed while it is worked on (#70), so a Postgres log is
never the source of a copy into Postgres.

A copy's endpoints are ``SqliteLogs(root)`` and ``PostgresLogs(dsn)``. Their
private side is the backend's half of a copy, which ``copy_run`` and ``drop_run``
call without asking which backend they hold."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import struct
import uuid
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, NamedTuple, Protocol
from urllib.request import pathname2url

from .channel import (
    Channel,
    Logs,
    Reader,
    RunNotFound,
    SqliteChannel,
    SqliteSnapshot,
    sqlite_log_path,
)
from .channel.envelope import Envelope
from .channel.memory import MemoryChannel, MemoryLog
from .formats import FORMATS, LOG_FORMAT, Row
from .observables import live_episode

if TYPE_CHECKING:
    import psycopg

__all__ = [
    "CopyLogs",
    "CopyRefused",
    "Copied",
    "MemoryLogs",
    "PostgresLogs",
    "Row",
    "RunLive",
    "SqliteLogs",
    "copy_run",
    "drop_run",
    "log_digest",
]


class RunLive(Exception):
    """The run's latest claim has not ended (``live_episode``), so it is not copied
    or dropped. Routine, not an error: the run is being worked on, and its next end
    is the time to copy it."""

    def __init__(self, run_id: str, handle: str) -> None:
        self.run_id = run_id
        self.handle = handle  # the live claim's handle
        super().__init__(
            f"run {run_id!r} is live: its latest claim, by {handle}, has not ended"
        )


class CopyRefused(Exception):
    """The destination holds a log of the run that the copy may not replace: rows
    the source does not extend, a strict prefix of them in Postgres (the run's
    writable log, which a writer may be appending to), or a writable SQLite log. An
    append-only log never diverges from its own copy, so this is an error."""


class Copied(NamedTuple):
    digest: str  # log_digest of the rows copied
    last_seq: int  # the newest seq copied


# --- the digest (spec §4) -------------------------------------------------------


def _text(s: str) -> bytes:
    b = s.encode("utf-8")
    return struct.pack(">I", len(b)) + b


def _nullable(s: str | None) -> bytes:
    return b"\x00" if s is None else b"\x01" + _text(s)


def _encode(row: Row) -> bytes:
    """One row's bytes, its fields in ``Row``'s order: unambiguous and
    language-neutral (a length before each text, the float's own bytes)."""
    return b"".join(
        (
            struct.pack(">q", row.seq),
            _text(row.topic),
            _nullable(row.name),
            _nullable(row.request_id),
            _text(row.body),
            struct.pack(">d", row.created_at),
        )
    )


def log_digest(rows: Iterable[Row]) -> str:
    """``"sha256:<64 hex digits>"`` over ``rows``, which must come in seq order
    (``ValueError`` otherwise), each encoded as spec §4's table gives it. The run id
    is not in it: the digest describes a log's contents, wherever it lives."""
    h = hashlib.sha256()
    last: int | None = None
    for row in rows:
        if last is not None and row.seq <= last:
            raise ValueError(
                f"log_digest takes rows in seq order: seq {row.seq} follows {last}"
            )
        last = row.seq
        h.update(_encode(row))
    return "sha256:" + h.hexdigest()


# --- what every place shares ---------------------------------------------------


def _refuse_live(run_id: str, rows: list[Row]) -> None:
    """``RunLive`` unless the latest claim in ``rows`` has ended: ``live_episode``,
    the claim gate, folded over the rows as a memory log, so the copy and the gate
    cannot disagree."""
    log = MemoryLog(
        [
            Envelope(r.seq, r.topic, r.name, r.request_id, json.loads(r.body))
            for r in rows
        ]
    )
    handle = live_episode(MemoryChannel(log))
    if handle is not None:
        raise RunLive(run_id, handle)


def _extends(rows: list[Row], held: list[Row]) -> bool:
    """Whether ``held`` is a prefix of ``rows``, compared row by row as the digest
    encodes them, so a prefix here is a prefix of the digest's input."""
    return len(held) <= len(rows) and all(
        _encode(h) == _encode(r) for h, r in zip(held, rows)
    )


def _verify(run_id: str, written: list[Row], digest: str, where: str) -> None:
    found = log_digest(written)
    if found != digest:
        raise RuntimeError(
            f"the copy of run {run_id!r} at {where} reads back with digest {found}, "
            f"not the source's {digest}; nothing is published"
        )


class CopyLogs(Logs, Protocol):
    """A place a run's log can be copied from or to: one backend's logs at
    ``LOG_FORMAT``, ``SqliteLogs(root)`` or ``PostgresLogs(dsn)``. Every open here,
    a channel's or a copy's, takes the opening checks (log-formats.md §4), so a run
    held in another format raises ``LogFormatMismatch`` or ``LogFormatMissing``,
    which ``runstate migrate`` resolves.

    ``rows`` is public, so a caller can compute ``log_digest`` on either side
    without copying. The rest is the backend's half of ``copy_run`` and
    ``drop_run``."""

    def rows(self, run_id: str) -> list[Row]:
        """The run's rows in seq order, in one read; ``RunNotFound`` if it has
        none."""
        ...

    # Whether a log copied here is the run's writable log, so its source is sealed.
    _writable: ClassVar[bool]

    def _export(self, run_id: str, *, seal: bool) -> list[Row]:
        """The rows a copy takes from here: ``RunNotFound`` without any,
        ``RunLive`` while the run is live. With ``seal``, the log here is sealed
        first, and read after the seal."""
        ...

    def _import(self, run_id: str, rows: list[Row], *, digest: str) -> None:
        """Write ``rows`` here as the run's log, unless what is here already equals
        them, and verify the write against ``digest`` before it is visible."""
        ...

    def _drop(self, run_id: str) -> None:
        """Delete the run's log here; nothing if it has none; ``RunLive`` while it
        is live."""
        ...


def copy_run(run_id: str, *, source: CopyLogs, destination: CopyLogs) -> Copied:
    """Copy the run's log from ``source`` to ``destination``, every field exact.

    In order: read the source in one read (a source the copy must seal, since the
    destination is writable, is sealed first and read after the seal); refuse a
    live run (``RunLive``) or a run the source does not hold (``RunNotFound``);
    check the destination, which may hold nothing, the same rows (a no-op, so a
    retry succeeds) or, in SQLite only, a strict prefix of them (replaced), and
    refuse anything else (``CopyRefused``); write; re-read the destination and
    compare digests; return the digest and the last seq.

    Postgres to SQLite writes a sealed snapshot and leaves Postgres the writable
    log. SQLite to Postgres seals the SQLite log, which is then the run's first
    snapshot. SQLite to SQLite writes a sealed snapshot and leaves the source as it
    was. Postgres to Postgres is refused (``ValueError``): the run would have two
    writable logs."""
    rows = source._export(run_id, seal=destination._writable)
    digest = log_digest(rows)
    destination._import(run_id, rows, digest=digest)
    return Copied(digest, rows[-1].seq)


def drop_run(run_id: str, *, logs: CopyLogs) -> None:
    """Delete the run's log from ``logs``, as a consumer's GC does: ``RunLive``
    while the run is live, nothing when it has no log there."""
    logs._drop(run_id)


# --- SQLite ----------------------------------------------------------------------

# A log's sidecars: WAL's -wal and -shm, and the -journal a rollback-mode writer
# that died mid-transaction leaves hot.
_SIDECARS = ("-wal", "-shm", "-journal")

_SQLITE_SELECT = (
    "SELECT seq, topic, name, request_id, body, created_at FROM log ORDER BY seq"
)


def _read_sqlite(path: Path) -> list[Row]:
    """Every row, in seq order, in one statement. Opened read-write, never
    ``mode=ro``: only a read-write open rolls back a hot ``-journal``. On a sealed
    log, read-write opens read-only."""
    conn = sqlite3.connect(f"file:{pathname2url(str(path))}?mode=rw", uri=True)
    try:
        return [Row(*r) for r in conn.execute(_SQLITE_SELECT)]
    finally:
        conn.close()


def _sealed(path: Path) -> bool:
    """Whether a log file is sealed: every write bit cleared (``seal_sqlite``)."""
    return not os.stat(path).st_mode & 0o222


def _seal(path: Path) -> None:
    # Imported here: runstate.migrations loads every retained step, which
    # `import runstate` should not pay for.
    from .migrations.seal import seal_sqlite

    seal_sqlite(path)


# What the two cross-process places write into a child's environment, and what
# ``from_env`` reads back: the one place a backend is a string.
ROOT_ENV = "RUNSTATE_CHANNEL_ROOT"
BACKEND_ENV = "RUNSTATE_CHANNEL_BACKEND"

# ``json_default``, on every place: a sender-side ``json.dumps`` hook for coercing
# exotic value payloads (numpy scalars -> float), given to each channel the place
# opens. An exempt default: it encodes, and changes no record a plain value would
# write; readers are unaffected.
JsonDefault = Callable[[object], object]


class MemoryLogs:
    """Logs held in this object, for tests and in-process runs: each instance is a
    place of its own, and two handles share a run's log by opening it from the
    same instance. It cannot cross a process boundary, so it is no
    ``ProcessLogs`` and no copy endpoint."""

    def __init__(self, *, json_default: JsonDefault | None = None) -> None:
        self._logs: dict[str, MemoryLog] = {}
        self._json_default = json_default

    def __repr__(self) -> str:
        return f"MemoryLogs(<{len(self._logs)} runs>)"

    def create(self, run_id: str) -> Channel:
        # Build a log only for an absent run; setdefault, not an assignment, so
        # two racing creates of one run still share the log that won.
        existing = self._logs.get(run_id)
        log = (
            existing
            if existing is not None
            else self._logs.setdefault(run_id, MemoryLog([]))
        )
        return MemoryChannel(log, json_default=self._json_default)

    def attach(self, run_id: str) -> Channel:
        # No setdefault (no phantom): an absent run or an empty log is "no
        # records", so the run does not exist.
        found = self._logs.get(run_id)
        if found is None or found.last_seq() == 0:
            raise RunNotFound(f"run {run_id!r} has no records in {self!r}")
        return MemoryChannel(found, json_default=self._json_default)

    def reader(self, run_id: str) -> Reader:
        return self.attach(run_id)


class SqliteLogs:
    """The logs under a SQLite root directory, which must exist: a run's log is at
    ``<root>/v<LOG_FORMAT>/<rid>.db``. A copy written here is a sealed
    snapshot."""

    _writable: ClassVar[bool] = False

    def __init__(
        self, root: str | os.PathLike[str], *, json_default: JsonDefault | None = None
    ) -> None:
        self._root = Path(root)
        self._json_default = json_default

    def __repr__(self) -> str:
        return f"SqliteLogs({str(self._root)!r})"

    def create(self, run_id: str) -> Channel:
        path = sqlite_log_path(self._root, run_id)
        if not path.exists():
            path.parent.mkdir(exist_ok=True)  # the root itself must already exist
        return SqliteChannel(path, create=True, json_default=self._json_default)

    def attach(self, run_id: str) -> Channel:
        path = sqlite_log_path(self._root, run_id)
        if not path.exists():
            raise RunNotFound(f"run has no records at {path}")
        return SqliteChannel(path, create=False, json_default=self._json_default)

    def reader(self, run_id: str) -> Reader:
        """A sealed snapshot with no sidecar beside it is opened immutable, with no
        locks (``SqliteSnapshot``); any other log as ``attach`` opens it, since
        only a read-write open rolls back a hot journal a dead writer left."""
        path = sqlite_log_path(self._root, run_id)
        if not path.exists():
            raise RunNotFound(f"run has no records at {path}")
        sidecars = (path.with_name(path.name + s) for s in ("-wal", "-journal"))
        if _sealed(path) and not any(p.exists() for p in sidecars):
            return SqliteSnapshot(path)
        return SqliteChannel(path, create=False, json_default=self._json_default)

    def child_env(self) -> Mapping[str, str]:
        return {BACKEND_ENV: "sqlite", ROOT_ENV: str(self._root)}

    def rows(self, run_id: str) -> list[Row]:
        path = sqlite_log_path(self._root, run_id)
        rows = _read_sqlite(path) if path.exists() else []
        if not rows:
            raise RunNotFound(f"run has no records at {path}")
        return rows

    def _export(self, run_id: str, *, seal: bool) -> list[Row]:
        """With ``seal``: read once, refuse a live run, seal, read again and refuse
        again, as ``runstate migrate`` seals. A claim that lands between the first
        read and the seal leaves the run sealed and live, and its worker's next
        append fails loudly; the copy raises ``RunLive``."""
        rows = self.rows(run_id)
        _refuse_live(run_id, rows)
        path = sqlite_log_path(self._root, run_id)
        if not seal or _sealed(path):
            return rows
        _seal(path)
        rows = self.rows(run_id)
        _refuse_live(run_id, rows)
        return rows

    def _import(self, run_id: str, rows: list[Row], *, digest: str) -> None:
        """Write a sealed snapshot: under a hidden temporary name beside the
        address, verified, sealed, then renamed into place, so a reader never sees
        a partial or writable one. The temporary file is removed on any failure."""
        path = sqlite_log_path(self._root, run_id)
        if path.exists():
            if not _sealed(path):
                raise CopyRefused(
                    f"{path} holds a writable log of run {run_id!r}: a copy never "
                    "replaces a run's writable log"
                )
            held = _read_sqlite(path)
            if not _extends(rows, held):
                raise CopyRefused(
                    f"{path} holds rows of run {run_id!r} that the source does not "
                    "extend"
                )
            if len(held) == len(rows):
                return  # already copied
        path.parent.mkdir(exist_ok=True)  # the root itself must already exist
        # This call's own temporary file: hidden, and not *.db, so no run listing
        # sees it, and two copies of one run never publish each other's.
        tmp = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
        try:
            conn = sqlite3.connect(tmp, isolation_level=None)
            try:
                conn.executescript(FORMATS[LOG_FORMAT].sqlite_ddl)
                conn.execute("BEGIN")
                conn.executemany(
                    "INSERT INTO log (seq, topic, name, request_id, body, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    rows,
                )
                conn.execute("COMMIT")
            finally:
                conn.close()
            _verify(run_id, _read_sqlite(tmp), digest, str(tmp))
            _seal(tmp)
            os.replace(tmp, path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    def _drop(self, run_id: str) -> None:
        """Delete the log, then its sidecars, which go even when the log is already
        gone, so a drop interrupted between the two completes on a retry."""
        path = sqlite_log_path(self._root, run_id)
        if path.exists():
            rows = _read_sqlite(path)
            if rows:
                _refuse_live(run_id, rows)
            path.unlink()
        for suffix in _SIDECARS:
            path.with_name(path.name + suffix).unlink(missing_ok=True)


# --- Postgres --------------------------------------------------------------------

_PG_SCHEMA = FORMATS[LOG_FORMAT].pg_schema()


def _pg_rows(conn: "psycopg.Connection[Any]", run_id: str) -> list[Row]:
    """The run's rows, in seq order, in one statement: one snapshot."""
    from psycopg import sql

    query = sql.SQL(
        "SELECT seq, topic, name, request_id, body, created_at"
        " FROM {}.log WHERE run_id = %s ORDER BY seq"
    ).format(sql.Identifier(_PG_SCHEMA))
    return [Row(*r) for r in conn.execute(query, [run_id])]


class PostgresLogs:
    """The logs in a Postgres database, named by its DSN: the rows of format
    ``LOG_FORMAT``'s schema, which ``channel.postgres.ensure_schema(dsn)`` must
    have provisioned before a channel is opened. A copy written here is the run's
    writable log, its rows carrying no writer stamp, so the session probe abstains
    on them. Needs the ``[postgres]`` extra."""

    _writable: ClassVar[bool] = True

    def __init__(self, dsn: str, *, json_default: JsonDefault | None = None) -> None:
        self._dsn = dsn
        self._json_default = json_default

    def __repr__(self) -> str:
        return "PostgresLogs('<dsn>')"  # a DSN can carry a password

    def _open(self, run_id: str, *, create: bool) -> Channel:
        try:
            from .channel.postgres import PostgresChannel
        except ImportError as exc:  # the optional extra isn't installed
            raise ImportError(
                "PostgresLogs needs psycopg: pip install runstate[postgres]"
            ) from exc
        return PostgresChannel(
            self._dsn, run_id, create=create, json_default=self._json_default
        )

    def create(self, run_id: str) -> Channel:
        return self._open(run_id, create=True)

    def attach(self, run_id: str) -> Channel:
        return self._open(run_id, create=False)

    def reader(self, run_id: str) -> Reader:
        return self.attach(run_id)

    def child_env(self) -> Mapping[str, str]:
        # The DSN goes into the child's environment, as it always has: a DSN that
        # carries a password is readable there by the child's own user.
        return {BACKEND_ENV: "postgres", ROOT_ENV: self._dsn}

    def rows(self, run_id: str) -> list[Row]:
        import psycopg

        from .channel.postgres import check_format

        with psycopg.connect(self._dsn) as conn:
            rows = _pg_rows(conn, run_id) if check_format(conn, run_id) else []
        if not rows:
            raise RunNotFound(f"run {run_id!r} has no records (postgres backend)")
        return rows

    def _export(self, run_id: str, *, seal: bool) -> list[Row]:
        if seal:
            raise ValueError(
                "a Postgres log cannot be sealed while it is worked on (#70), so it "
                "is never copied into another writable log: the run would have two"
            )
        rows = self.rows(run_id)
        _refuse_live(run_id, rows)
        return rows

    def _import(self, run_id: str, rows: list[Row], *, digest: str) -> None:
        """The schema first, as a channel's birth provisions it (``ensure_schema``);
        then one transaction: check the destination, which may hold nothing or the
        same rows, insert, re-read and compare digests, commit. A writer birthing
        the run meanwhile collides with the insert on ``(run_id, seq)``: one of the
        two commits, and the copy raises ``CopyRefused`` if it is the writer."""
        import psycopg
        from psycopg import sql

        from .channel.postgres import check_format, ensure_schema

        insert = sql.SQL(
            "INSERT INTO {}.log (run_id, seq, topic, name, request_id, body, created_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s)"
        ).format(sql.Identifier(_PG_SCHEMA))
        ensure_schema(self._dsn)
        try:
            with psycopg.connect(self._dsn) as conn, conn.transaction():
                if check_format(conn, run_id):
                    held = _pg_rows(conn, run_id)
                    if not _extends(rows, held):
                        raise CopyRefused(
                            f"run {run_id!r} in Postgres holds rows the source does "
                            "not extend"
                        )
                    if len(held) == len(rows):
                        return  # already copied
                    raise CopyRefused(
                        f"run {run_id!r} in Postgres holds a strict prefix of the "
                        "source: Postgres holds the run's writable log, which a "
                        "writer may be appending to"
                    )
                with conn.cursor() as cur:
                    cur.executemany(insert, [(run_id, *r) for r in rows])
                _verify(run_id, _pg_rows(conn, run_id), digest, "Postgres")
        except psycopg.errors.UniqueViolation as exc:
            raise CopyRefused(
                f"a writer birthed run {run_id!r} in Postgres during the copy"
            ) from exc

    def _drop(self, run_id: str) -> None:
        """Read, check and delete in one transaction, under the table lock the seal
        takes (``seal_postgres``): an append in flight finishes first, and a later
        one sees no rows, so none lands at the dropped log's next seq."""
        import psycopg
        from psycopg import sql

        from .channel.postgres import check_format

        schema = sql.Identifier(_PG_SCHEMA)
        with psycopg.connect(self._dsn) as conn, conn.transaction():
            if not check_format(conn, run_id):
                return
            conn.execute(
                sql.SQL("LOCK TABLE {}.log IN SHARE ROW EXCLUSIVE MODE").format(schema)
            )
            rows = _pg_rows(conn, run_id)
            if not rows:
                return
            _refuse_live(run_id, rows)
            conn.execute(
                sql.SQL("DELETE FROM {}.log WHERE run_id = %s").format(schema),
                [run_id],
            )


def from_env(
    environ: Mapping[str, str], *, json_default: JsonDefault | None = None
) -> SqliteLogs | PostgresLogs:
    """The place ``child_env`` spelled into ``environ``: the inverse of the two
    reference encodings, and the one place a backend is read as a string. Both
    variables are required, so a process launched without them raises here rather
    than opening a default place."""
    backend, root = environ[BACKEND_ENV], environ[ROOT_ENV]
    if backend == "sqlite":
        return SqliteLogs(root, json_default=json_default)
    if backend == "postgres":
        return PostgresLogs(root, json_default=json_default)
    raise ValueError(
        f"{BACKEND_ENV}={backend!r}: expected 'sqlite' or 'postgres', the places "
        "another process can open"
    )


# ----- the proof: each place satisfies the Protocols it is documented as --------------
if TYPE_CHECKING:
    from .channel import ProcessLogs
    from .launcher import LocalLauncher

    def _proof(
        memory: MemoryLogs, sqlite: SqliteLogs, postgres: PostgresLogs
    ) -> tuple[Logs, ProcessLogs, ProcessLogs, CopyLogs, CopyLogs]:
        """Checked by ``mypy --strict runstate``, never run: it fails when a place
        stops satisfying a Protocol, and, through the unused-ignore check, when a
        place no other process can open starts being accepted by the launcher
        that spawns one."""
        LocalLauncher(logs=memory)  # type: ignore[arg-type]
        return memory, sqlite, postgres, sqlite, postgres
