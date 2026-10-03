import os
import sqlite3
import uuid

import pytest

from runstate.channel.sqlite import SqliteChannel
from runstate.migrations.seal import seal_sqlite

# Sealing is by file permissions, which the root user bypasses (some CI containers run as
# root). The seal still happens; only "the write is refused" cannot be observed as root.
_AS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0


def test_seal_keeps_crashed_wal_frames_and_refuses_writes(
    tmp_path, monkeypatch, crashed_wal_writer
):
    path = tmp_path / "r.db"
    crashed_wal_writer(path, 5)
    assert path.with_name("r.db-wal").exists()  # the frames are still in the WAL
    seal_sqlite(path)
    assert not path.with_name("r.db-wal").exists()
    ro = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    assert ro.execute("SELECT count(*) FROM log").fetchone() == (5,)
    ro.close()
    assert os.stat(path).st_mode & 0o222 == 0
    if not _AS_ROOT:
        # an old writer opening the sealed log fails loudly, under either journal mode
        for mode in ("WAL", "DELETE"):
            monkeypatch.setenv("RUNSTATE_SQLITE_JOURNAL_MODE", mode)
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                with SqliteChannel(path) as ch:
                    ch.send({"late": True}, topic="value", name="n")


def test_sealed_postgres_run_refuses_inserts(pg_ready):
    import psycopg

    from runstate.channel.postgres import ensure_schema
    from runstate.formats import FORMATS, LOG_FORMAT
    from runstate.migrations.seal import seal_postgres

    ensure_schema(pg_ready)
    schema = FORMATS[LOG_FORMAT].pg_schema()
    rid, other = f"seal-{uuid.uuid4().hex}", f"seal-{uuid.uuid4().hex}"
    with psycopg.connect(pg_ready) as c:
        with c.transaction():
            seal_postgres(c, schema, rid)
        with pytest.raises(psycopg.errors.RaiseException, match="sealed"):
            with c.transaction():
                c.execute(
                    f"INSERT INTO {schema}.log VALUES (%s, 1, 'value', 'n', NULL, '{{}}', 0)",
                    [rid],
                )
        with c.transaction():  # other runs are untouched
            c.execute(
                f"INSERT INTO {schema}.log VALUES (%s, 1, 'value', 'n', NULL, '{{}}', 0)",
                [other],
            )


def test_seal_refuses_while_another_connection_holds_the_log(
    tmp_path, crashed_wal_writer
):
    path = tmp_path / "r.db"
    crashed_wal_writer(path, 3)
    holder = sqlite3.connect(path)
    try:
        holder.execute("PRAGMA journal_mode=WAL")
        holder.execute("BEGIN")
        holder.execute(
            "SELECT count(*) FROM log"
        ).fetchone()  # an open read transaction
        with pytest.raises(RuntimeError, match="another connection holds the log open"):
            seal_sqlite(path)
        if not _AS_ROOT:
            assert os.stat(path).st_mode & 0o200  # still writable: nothing was chmodded
    finally:
        holder.close()
