"""Where a migration reads and writes, per backend: one Strategy each."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Protocol
from urllib.request import pathname2url

from .. import formats
from ..channel.sqlite import _SCHEMA
from . import MigrationError, Row, Step
from .seal import seal_postgres, seal_sqlite


class Store(Protocol):
    def formats_of(self, run_ids: list[str] | None) -> dict[str, str]: ...

    def migrate_one(self, step: Step, run_id: str) -> None: ...


def _read_sqlite(path: Path) -> list[Row]:
    conn = sqlite3.connect(f"file:{pathname2url(str(path))}?mode=ro", uri=True)
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
        self._root = Path(root)

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

    def migrate_one(self, step: Step, run_id: str) -> None:
        """Refuse a live run untouched; seal (unless an earlier failed attempt
        already did); re-read, since the seal checkpoints any WAL frames; then
        write the new log to a hidden temporary file and rename it into place."""
        src = formats.FORMATS[step.FROM].sqlite_path(self._root, run_id)
        dst = formats.FORMATS[step.TO].sqlite_path(self._root, run_id)
        if os.stat(src).st_mode & 0o222:  # not yet sealed
            if step.is_live(_read_sqlite(src)):
                raise MigrationError(
                    f"run {run_id!r} has a live episode; stop it first"
                )
            seal_sqlite(src)
        out = step.transform(_read_sqlite(src))
        dst.parent.mkdir(exist_ok=True)
        tmp = dst.with_name(f".{dst.name}.tmp")
        tmp.unlink(missing_ok=True)
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
        os.replace(tmp, dst)


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

    def migrate_one(self, step: Step, run_id: str) -> None:
        import psycopg
        from psycopg import sql

        src = sql.Identifier(formats.FORMATS[step.FROM].pg_schema())
        dst_name = formats.FORMATS[step.TO].pg_schema()
        dst = sql.Identifier(dst_name)
        with psycopg.connect(self._dsn) as conn, conn.transaction():
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
                raise MigrationError(
                    f"run {run_id!r} has a live episode; stop it first"
                )
            seal_postgres(conn, formats.FORMATS[step.FROM].pg_schema(), run_id)
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
                    [(run_id, *r) for r in step.transform(rows)],
                )
