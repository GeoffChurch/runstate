"""Sealing: a sealed log is readable and refuses every write
(docs/specs/log-formats.md §5)."""

from __future__ import annotations

import os
import sqlite3
import stat
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import psycopg


def seal_sqlite(path: Path) -> None:
    """Checkpoint, leave WAL so the file opens read-only with no sidecars, then
    clear every write bit. An old writer's next append fails with 'attempt to
    write a readonly database'."""
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("PRAGMA journal_mode=DELETE")
    finally:
        conn.close()
    mode = os.stat(path).st_mode
    os.chmod(path, mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


_SEALED_TABLE = (
    "CREATE TABLE IF NOT EXISTS {schema}.sealed_runs (run_id text PRIMARY KEY)"
)

# The function body holds `;` inside $$ ... $$, so each statement is its own template.
_REFUSE_FUNCTION = """
CREATE OR REPLACE FUNCTION {schema}.refuse_sealed() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM {schema}.sealed_runs WHERE run_id = NEW.run_id) THEN
    RAISE EXCEPTION 'runstate: run % is sealed in this log format', NEW.run_id;
  END IF;
  RETURN NEW;
END $$
"""

# CREATE OR REPLACE TRIGGER needs Postgres 14+.
_REFUSE_TRIGGER = """
CREATE OR REPLACE TRIGGER refuse_sealed BEFORE INSERT ON {schema}.log
  FOR EACH ROW EXECUTE FUNCTION {schema}.refuse_sealed()
"""


def seal_postgres(conn: "psycopg.Connection[Any]", schema: str, run_id: str) -> None:
    """Seal one run in one format's schema. Call inside the caller's transaction;
    the table lock blocks concurrent inserts until the caller commits, so nothing
    slips in between the seal and the copy."""
    from psycopg import sql

    ident = sql.Identifier(schema)
    conn.execute(sql.SQL("LOCK TABLE {}.log IN SHARE ROW EXCLUSIVE MODE").format(ident))
    for template in (_SEALED_TABLE, _REFUSE_FUNCTION, _REFUSE_TRIGGER):
        conn.execute(sql.SQL(template).format(schema=ident))
    conn.execute(
        sql.SQL("INSERT INTO {}.sealed_runs VALUES (%s) ON CONFLICT DO NOTHING").format(
            ident
        ),
        [run_id],
    )
