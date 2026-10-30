"""Where a migration reads and writes, per backend: one Strategy each."""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol
from urllib.request import pathname2url

if TYPE_CHECKING:
    import psycopg

from .. import formats
from . import MigrationError, Planned, Row
from .ambiguity import Entry, History, Json, Migrated, Trace, digest, latest
from .seal import seal_postgres, seal_sqlite


class Store(Protocol):
    def formats_of(self, run_ids: list[str] | None) -> dict[str, str]: ...

    def rows(self, version: str, run_id: str) -> list[Row]:
        """A run's rows at format ``version``, read without sealing: the detection
        pass's input."""
        ...

    def history(self, run_id: str) -> History:
        """The run's entries in the root's manifest, oldest first, and what the store
        holds of its log in other formats (``Trace``)."""
        ...

    def migrate_one(self, planned: Planned, run_id: str) -> None:
        """Migrate one run by one step, and record it in the root's manifest."""
        ...

    def sealed(self, version: str, run_id: str) -> bool:
        """Whether the run's log at format ``version`` is sealed: what a failed
        ``migrate_one`` left, read after the fact."""
        ...

    def facts(self, cited: str) -> Json:
        """The facts statement an entry's ``facts`` cites by its digest, read from the
        root only when a reader needs it, and checked against the digest."""
        ...


# The root's manifest (log-formats.md §6): outside every format's address, so no
# opening check reads it as a format or a legacy log.
SQLITE_MANIFEST = "runstate-migrations.jsonl"
SQLITE_FACTS = "runstate-migrations-facts.jsonl"  # one {digest, facts} line per digest
PG_MANIFEST_SCHEMA = "runstate_migrations"


def _checked(cited: str, text: str | None, where: str) -> Json:
    """A statement's rendering, which must hash to the digest citing it."""
    if text is None:
        raise MigrationError(f"{where} holds no facts statement {cited}")
    if digest(text) != cited:
        raise MigrationError(f"{where}'s facts statement {cited} does not hash to it")
    found: Json = json.loads(text)
    return found


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


def _sealed(path: Path) -> bool:
    """Whether a log file is sealed: every write bit cleared (seal.py)."""
    return not os.stat(path).st_mode & 0o222


def sqlite_traces(root: Path, run_id: str) -> tuple[Trace, ...]:
    """What a sqlite root holds of a run's log beside its manifest: a sealed log at a
    format's address, and a legacy tombstone, the empty read-only file that onboarding
    leaves at the legacy address of a log it moved into format 0.2.0 (log-formats.md
    §7)."""
    out = [
        Trace(version, f"a sealed log at {path}")
        for version, layout in sorted(
            formats.FORMATS.items(), key=lambda kv: formats.parse(kv[0])
        )
        if (path := layout.sqlite_path(root, run_id)).is_file() and _sealed(path)
    ]
    legacy = root / f"{run_id}.db"
    if legacy.is_file() and legacy.stat().st_size == 0 and _sealed(legacy):
        out.append(Trace(formats.LEGACY_FORMAT, f"a legacy tombstone at {legacy}"))
    return tuple(out)


class SqliteStore:
    def __init__(self, root: Path) -> None:
        """Refuses a root that is not a directory: it holds no runs, and a
        migration that found none would report success having done nothing."""
        self._root = Path(root)
        # the root's manifest by run id, read once, then kept with every entry this
        # store appends: the detection pass and each step ask it once per run
        self._entries: dict[str, list[Entry]] | None = None
        # the digests of the statements the root holds, read once, then kept with every
        # one this store appends
        self._digests: set[str] | None = None
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
        return History(
            latest(self._manifest().get(run_id, ())),
            sqlite_traces(self._root, run_id),
        )

    def sealed(self, version: str, run_id: str) -> bool:
        return _sealed(formats.FORMATS[version].sqlite_path(self._root, run_id))

    def facts(self, cited: str) -> Json:
        path = self._root / SQLITE_FACTS
        text = None
        if path.exists():
            with path.open() as f:
                for line in f:
                    if line.strip() and json.loads(line)["digest"] == cited:
                        text = json.loads(line)["facts"]
                        break
        return _checked(cited, text, str(path))

    def _statements(self) -> set[str]:
        if self._digests is None:
            path = self._root / SQLITE_FACTS
            self._digests = set()
            if path.exists():
                with path.open() as f:
                    self._digests = {
                        json.loads(line)["digest"] for line in f if line.strip()
                    }
        return self._digests

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
        WAL frames; transform, which is the dry run's output when the re-read finds
        the rows it read before (the transform is deterministic), so a run the memo
        could not hold is not transformed again here; write the new log to a hidden
        temporary file; append the facts statement the entry cites, if the root does
        not hold it, then the run's entry, to the root's manifest; and rename the log
        into place.

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
        dry: tuple[list[Row], Migrated] | None = None
        if not _sealed(src):
            rows = _read_sqlite(src)
            if step.is_live(rows):
                raise MigrationError("it has a live episode; stop it first")
            # the dry run: a refusal raises before the seal
            dry = (rows, planned.run(rows, history))
            seal_sqlite(src)
        rows = _read_sqlite(src)
        migrated = (
            dry[1] if dry is not None and dry[0] == rows else planned.run(rows, history)
        )
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
                conn.executescript(formats.FORMATS[step.TO].sqlite_ddl)
                conn.execute("BEGIN")
                conn.executemany(
                    "INSERT INTO log (seq, topic, name, request_id, body, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    out,
                )
                conn.execute("COMMIT")
            finally:
                conn.close()
            # One write of one line, appended, each. Concurrent migrators of one root on
            # NFS, where O_APPEND is not atomic, can interleave lines: a known limit.
            # The statement goes first, so every entry's digest resolves; a crash
            # between leaves a statement no entry cites, which is harmless.
            if planned.facts.digest not in self._statements():
                with (self._root / SQLITE_FACTS).open("a") as f:
                    line = {"digest": planned.facts.digest, "facts": planned.facts.text}
                    f.write(json.dumps(line, separators=(",", ":")) + "\n")
                self._statements().add(planned.facts.digest)
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
            # The history before the seal, as the detection pass read it: the seal's
            # own sealed_runs row is no trace of the run's past, and would key the
            # transform apart from the detection pass's (Memo.key).
            history = _pg_history(conn, run_id)
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
            migrated = planned.run(rows, history)
            conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(dst))
            conn.execute(sql.SQL("SET LOCAL search_path TO {}").format(dst))
            for ddl in formats.FORMATS[step.TO].pg_ddl:
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
                    "INSERT INTO {}.facts (digest, facts) VALUES (%s, %s)"
                    " ON CONFLICT DO NOTHING"
                ).format(sql.Identifier(PG_MANIFEST_SCHEMA)),
                [planned.facts.digest, planned.facts.text],
            )
            conn.execute(
                sql.SQL(
                    "INSERT INTO {}.manifest (run_id, entry) VALUES (%s, %s)"
                ).format(sql.Identifier(PG_MANIFEST_SCHEMA)),
                [run_id, planned.entry(run_id, migrated).to_json()],
            )

    def facts(self, cited: str) -> Json:
        import psycopg
        from psycopg import sql

        table = f"{PG_MANIFEST_SCHEMA}.facts"
        with psycopg.connect(self._dsn) as conn:
            (reg,) = conn.execute("SELECT to_regclass(%s)", [table]).fetchall()
            found = (
                None
                if reg[0] is None
                else conn.execute(
                    sql.SQL("SELECT facts FROM {}.facts WHERE digest = %s").format(
                        sql.Identifier(PG_MANIFEST_SCHEMA)
                    ),
                    [cited],
                ).fetchone()
            )
        return _checked(cited, None if found is None else found[0], table)


def _pg_manifest(conn: "psycopg.Connection[Any]") -> None:
    """The root's manifest tables, created on first use: the entries, and the facts
    statements they cite by digest."""
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
    conn.execute(
        sql.SQL(
            "CREATE TABLE IF NOT EXISTS {}.facts (digest text PRIMARY KEY,"
            " facts text NOT NULL)"
        ).format(schema)
    )


def _pg_history(conn: "psycopg.Connection[Any]", run_id: str) -> History:
    from psycopg import sql

    (reg,) = conn.execute(
        "SELECT to_regclass(%s)", [f"{PG_MANIFEST_SCHEMA}.manifest"]
    ).fetchall()
    entries = (
        ()
        if reg[0] is None
        else latest(
            Entry.from_json(text)
            for (text,) in conn.execute(
                sql.SQL(
                    "SELECT entry FROM {}.manifest WHERE run_id = %s ORDER BY id"
                ).format(sql.Identifier(PG_MANIFEST_SCHEMA)),
                [run_id],
            )
        )
    )
    return History(entries, _pg_traces(conn, run_id))


def _pg_traces(conn: "psycopg.Connection[Any]", run_id: str) -> tuple[Trace, ...]:
    """What a database holds of a run's log beside its manifest: a row in a format's
    ``sealed_runs``. Onboarding's legacy tombstone is a whole schema's table, naming no
    run, so it is no trace of one."""
    from psycopg import sql

    out: list[Trace] = []
    for version in sorted(formats.FORMATS, key=formats.parse):
        schema = formats.FORMATS[version].pg_schema()
        (reg,) = conn.execute(
            "SELECT to_regclass(%s)", [f"{schema}.sealed_runs"]
        ).fetchall()
        if reg[0] is None:
            continue
        found = conn.execute(
            sql.SQL("SELECT 1 FROM {}.sealed_runs WHERE run_id = %s").format(
                sql.Identifier(schema)
            ),
            [run_id],
        ).fetchone()
        if found is not None:
            out.append(Trace(version, f"a row of {schema}.sealed_runs"))
    return tuple(out)
