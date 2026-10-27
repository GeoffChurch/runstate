"""Where a migration reads and writes, per backend: one Strategy each."""

from __future__ import annotations

import os
import sqlite3
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol
from urllib.request import pathname2url

if TYPE_CHECKING:
    import psycopg

from .. import formats
from ..channel.sqlite import _SCHEMA
from . import MigrationError, Planned, Row
from .ambiguity import Entry, History, latest
from .seal import seal_postgres, seal_sqlite


class Store(Protocol):
    def formats_of(self, run_ids: list[str] | None) -> dict[str, str]: ...

    def rows(self, version: str, run_id: str) -> list[Row]:
        """A run's rows at format ``version``, read without sealing: the detection
        pass's input."""
        ...

    def history(self, run_id: str) -> History:
        """The run's entries in the root's manifest, oldest first."""
        ...

    def migrate_one(self, planned: Planned, run_id: str) -> None:
        """Migrate one run by one step, and record it in the root's manifest."""
        ...

    def sealed(self, version: str, run_id: str) -> bool:
        """Whether the run's log at format ``version`` is sealed: what a failed
        ``migrate_one`` left, read after the fact."""
        ...


# The root's manifest (log-formats.md §6): outside every format's address, so no
# opening check reads it as a format or a legacy log.
SQLITE_MANIFEST = "runstate-migrations.jsonl"
PG_MANIFEST_SCHEMA = "runstate_migrations"


def _read_sqlite(path: Path) -> list[Row]:
    """Every row, in seq order. Opened read-write, never ``mode=ro``: a writer in
    a rollback journal mode that died mid-transaction leaves a hot ``-journal``,
    and only a read-write open rolls it back, which every later opener would do
    too. On a sealed log, read-write opens read-only."""
    conn = sqlite3.connect(f"file:{pathname2url(str(path))}?mode=rw", uri=True)
    try:
        return [
            Row(*r)
            for r in conn.execute(
                "SELECT seq, topic, name, request_id, body, created_at FROM log ORDER BY seq"
            )
        ]
    finally:
        conn.close()


class SqliteStore:
    def __init__(self, root: Path) -> None:
        """Refuses a root that is not a directory: it holds no runs, and a
        migration that found none would report success having done nothing."""
        self._root = Path(root)
        # the root's manifest by run id, read once, then kept with every entry this
        # store appends: the detection pass and each step ask it once per run
        self._entries: dict[str, list[Entry]] | None = None
        if not self._root.is_dir():
            raise MigrationError(
                f"{root} is not a directory, so it holds no sqlite logs. For a "
                f"sqlite root, run `{formats.sqlite_migrate_command(self._root)}` "
                f"on the directory that holds the v<format>/ directories; for a "
                f"postgres database, run `{formats.POSTGRES_MIGRATE_COMMAND}`"
            )

    def formats_of(self, run_ids: list[str] | None) -> dict[str, str]:
        """Each run's newest registered format present under the root."""
        found: dict[str, str] = {}
        for version in sorted(formats.FORMATS, key=formats.parse):
            layout = formats.FORMATS[version]
            if run_ids is None:
                d = layout.sqlite_path(self._root, "x").parent
                rids = [p.stem for p in d.glob("*.db")] if d.is_dir() else []
            else:
                rids = [
                    r for r in run_ids if layout.sqlite_path(self._root, r).exists()
                ]
            for r in rids:
                found[r] = version
        missing = set(run_ids or ()) - set(found)
        if missing:
            raise MigrationError(f"no log in any known format for {sorted(missing)}")
        return found

    def rows(self, version: str, run_id: str) -> list[Row]:
        return _read_sqlite(formats.FORMATS[version].sqlite_path(self._root, run_id))

    def history(self, run_id: str) -> History:
        return latest(self._manifest().get(run_id, ()))

    def sealed(self, version: str, run_id: str) -> bool:
        path = formats.FORMATS[version].sqlite_path(self._root, run_id)
        return not os.stat(path).st_mode & 0o222

    def _manifest(self) -> dict[str, list[Entry]]:
        if self._entries is None:
            self._entries = {}
            path = self._root / SQLITE_MANIFEST
            if path.exists():
                with path.open() as f:
                    for line in f:
                        if line.strip():
                            e = Entry.from_json(line)
                            self._entries.setdefault(e.run_id, []).append(e)
        return self._entries

    def migrate_one(self, planned: Planned, run_id: str) -> None:
        """Refuse untouched a run that is live, or that the step, a chooser or an
        unaccepted divergence refuses: before the seal, read the rows once, ask
        ``is_live``, and dry-run ``transform``, so a deterministic refusal leaves
        the run writable and a re-run refuses it the same way. Then seal (unless an
        earlier failed attempt already did); re-read, since the seal checkpoints any
        WAL frames; transform; write the new log to a hidden temporary file; append
        the run's entry to the root's manifest; and rename the log into place.

        The entry goes before the publish, so a migrated run always has one: a crash
        between the two leaves an entry for an attempt that did not publish, and a
        re-run appends its own, which ``latest`` reads (log-formats.md §6).

        One residual: on a run that is not live, records landing between the
        pre-seal read and the seal can still make the post-seal transform
        refuse. The run is then left sealed and unmigrated, and recovery is
        log-formats.md §8's rollback."""
        step = planned.step
        src = formats.FORMATS[step.FROM].sqlite_path(self._root, run_id)
        dst = formats.FORMATS[step.TO].sqlite_path(self._root, run_id)
        history = self.history(run_id)
        if os.stat(src).st_mode & 0o222:  # not yet sealed
            rows = _read_sqlite(src)
            if step.is_live(rows):
                raise MigrationError("it has a live episode; stop it first")
            # the dry run: a refusal raises before the seal
            planned.run(rows, history)
            seal_sqlite(src)
        migrated = planned.run(_read_sqlite(src), history)
        out = migrated.rows
        entry = planned.entry(run_id, migrated)
        dst.parent.mkdir(exist_ok=True)
        # A temporary file of this call's own, beside the address: two migrators
        # of one run never unlink or publish each other's. Hidden, and not
        # *.db, so no run listing sees it; removed if the write fails.
        # sqlite creates it, so it takes the mode the umask gives a native birth.
        tmp = dst.parent / f".{dst.name}.{uuid.uuid4().hex}.tmp"
        try:
            conn = sqlite3.connect(tmp, isolation_level=None)
            try:
                conn.executescript(_SCHEMA)
                conn.execute("BEGIN")
                conn.executemany(
                    "INSERT INTO log (seq, topic, name, request_id, body, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    out,
                )
                conn.execute("COMMIT")
            finally:
                conn.close()
            # One write of one line, appended. Concurrent migrators of one root on
            # NFS, where O_APPEND is not atomic, can interleave lines: a known limit.
            with (self._root / SQLITE_MANIFEST).open("a") as f:
                f.write(entry.to_json() + "\n")
            self._manifest().setdefault(run_id, []).append(entry)
            os.replace(tmp, dst)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise


class PostgresStore:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def formats_of(self, run_ids: list[str] | None) -> dict[str, str]:
        import psycopg
        from psycopg import sql

        found: dict[str, str] = {}
        with psycopg.connect(self._dsn) as conn:
            for version in sorted(formats.FORMATS, key=formats.parse):
                schema = formats.FORMATS[version].pg_schema()
                reg = conn.execute(
                    "SELECT to_regclass(%s)", [f"{schema}.log"]
                ).fetchone()
                if reg is None or reg[0] is None:
                    continue
                q = sql.SQL("SELECT DISTINCT run_id FROM {}.log").format(
                    sql.Identifier(schema)
                )
                for (r,) in conn.execute(q).fetchall():
                    if run_ids is None or r in run_ids:
                        found[r] = version
        missing = set(run_ids or ()) - set(found)
        if missing:
            raise MigrationError(f"no log in any known format for {sorted(missing)}")
        return found

    def rows(self, version: str, run_id: str) -> list[Row]:
        import psycopg
        from psycopg import sql

        with psycopg.connect(self._dsn) as conn:
            return [
                Row(*r)
                for r in conn.execute(
                    sql.SQL(
                        "SELECT seq, topic, name, request_id, body, created_at"
                        " FROM {}.log WHERE run_id = %s ORDER BY seq"
                    ).format(sql.Identifier(formats.FORMATS[version].pg_schema())),
                    [run_id],
                )
            ]

    def history(self, run_id: str) -> History:
        import psycopg

        with psycopg.connect(self._dsn) as conn:
            return _pg_history(conn, run_id)

    def sealed(self, version: str, run_id: str) -> bool:
        """Whether the run is in the format's ``sealed_runs``. A failed
        ``migrate_one`` rolls its seal back with the rest, so after one this is false
        unless an earlier invocation sealed the run."""
        import psycopg
        from psycopg import sql

        schema = formats.FORMATS[version].pg_schema()
        with psycopg.connect(self._dsn) as conn:
            reg = conn.execute(
                "SELECT to_regclass(%s)", [f"{schema}.sealed_runs"]
            ).fetchone()
            if reg is None or reg[0] is None:
                return False
            found = conn.execute(
                sql.SQL("SELECT 1 FROM {}.sealed_runs WHERE run_id = %s").format(
                    sql.Identifier(schema)
                ),
                [run_id],
            ).fetchone()
            return found is not None

    def migrate_one(self, planned: Planned, run_id: str) -> None:
        """Seal, read, refuse or transform, write the new log and insert the run's
        manifest entry, all in one transaction: a refusal or a failure rolls every
        part of it back."""
        import psycopg
        from psycopg import sql

        step = planned.step
        src = sql.Identifier(formats.FORMATS[step.FROM].pg_schema())
        dst_name = formats.FORMATS[step.TO].pg_schema()
        dst = sql.Identifier(dst_name)
        with psycopg.connect(self._dsn) as conn, conn.transaction():
            # Seal first: its table lock keeps writers out, so the read sees every
            # committed row. A refusal, of a live run or by the step's transform,
            # raises and rolls the seal back.
            seal_postgres(conn, formats.FORMATS[step.FROM].pg_schema(), run_id)
            rows = [
                Row(*r)
                for r in conn.execute(
                    sql.SQL(
                        "SELECT seq, topic, name, request_id, body, created_at"
                        " FROM {}.log WHERE run_id = %s ORDER BY seq"
                    ).format(src),
                    [run_id],
                )
            ]
            if step.is_live(rows):
                raise MigrationError("it has a live episode; stop it first")
            migrated = planned.run(rows, _pg_history(conn, run_id))
            from ..channel.postgres import (
                _CREATE_INDEX,
                _CREATE_NAME_INDEX,
                _CREATE_TABLE,
            )

            conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(dst))
            conn.execute(sql.SQL("SET LOCAL search_path TO {}").format(dst))
            for ddl in (_CREATE_TABLE, _CREATE_INDEX, _CREATE_NAME_INDEX):
                conn.execute(ddl)
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO log (run_id, seq, topic, name, request_id, body, created_at)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    [(run_id, *r) for r in migrated.rows],
                )
            _pg_manifest(conn)
            conn.execute(
                sql.SQL(
                    "INSERT INTO {}.manifest (run_id, entry) VALUES (%s, %s)"
                ).format(sql.Identifier(PG_MANIFEST_SCHEMA)),
                [run_id, planned.entry(run_id, migrated).to_json()],
            )


def _pg_manifest(conn: "psycopg.Connection[Any]") -> None:
    """The root's manifest table, created on first use."""
    from psycopg import sql

    schema = sql.Identifier(PG_MANIFEST_SCHEMA)
    conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(schema))
    conn.execute(
        sql.SQL(
            "CREATE TABLE IF NOT EXISTS {}.manifest (id bigserial PRIMARY KEY,"
            " run_id text NOT NULL, entry text NOT NULL)"
        ).format(schema)
    )
    conn.execute(
        sql.SQL(
            "CREATE INDEX IF NOT EXISTS manifest_run_id ON {}.manifest (run_id)"
        ).format(schema)
    )


def _pg_history(conn: "psycopg.Connection[Any]", run_id: str) -> History:
    from psycopg import sql

    (reg,) = conn.execute(
        "SELECT to_regclass(%s)", [f"{PG_MANIFEST_SCHEMA}.manifest"]
    ).fetchall()
    if reg[0] is None:
        return ()
    return latest(
        Entry.from_json(text)
        for (text,) in conn.execute(
            sql.SQL(
                "SELECT entry FROM {}.manifest WHERE run_id = %s ORDER BY id"
            ).format(sql.Identifier(PG_MANIFEST_SCHEMA)),
            [run_id],
        )
    )
