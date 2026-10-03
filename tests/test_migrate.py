"""log-formats.md §6 with test-only formats and steps."""

import json
import os

import pytest

from runstate import formats, migrations
from runstate.channel.sqlite import SqliteChannel
from runstate.formats import DirectoryLayout
from runstate.migrations import MigrationError, Row, chain, migrate
from runstate.migrations.stores import PostgresStore, SqliteStore


class Tag:
    """A toy step: stamps {"tag": TO} into every body. ``boom_at`` is the
    0-based ``transform`` call that raises. On sqlite, call 0 is the dry run
    before the seal and call 1 the transform after it; postgres makes one."""

    def __init__(self, src, dst, live=False, boom_at=None):
        self.FROM, self.TO, self._live, self._boom = src, dst, live, boom_at
        self._calls = 0

    def is_live(self, rows):
        return self._live

    def transform(self, rows):
        call, self._calls = self._calls, self._calls + 1
        if call == self._boom:
            raise RuntimeError("boom")
        return [
            r._replace(body=json.dumps({**json.loads(r.body), "tag": self.TO}))
            for r in rows
        ]


@pytest.fixture
def toy(monkeypatch):
    for v in ["8.0.0", "8.1.0", "8.2.0"]:
        monkeypatch.setitem(formats.FORMATS, v, DirectoryLayout(v))
    steps = (Tag("8.0.0", "8.1.0"), Tag("8.1.0", "8.2.0"))
    monkeypatch.setattr(migrations, "STEPS", steps)
    return steps


def _seed(root, version, rid, n=3):
    path = DirectoryLayout(version).sqlite_path(root, rid)
    path.parent.mkdir(exist_ok=True)
    ch = SqliteChannel(path)
    for i in range(n):
        ch.send({"i": i}, topic="value", name="x")
    ch.close()
    return path


def _bodies(path):
    import sqlite3

    c = sqlite3.connect(path)
    out = [json.loads(b) for (b,) in c.execute("SELECT body FROM log ORDER BY seq")]
    c.close()
    return out


def test_row_is_a_named_tuple():
    assert Row(1, "value", None, None, "{}", 0.0).seq == 1


def test_chain_follows_declared_edges(toy):
    assert [s.TO for s in chain("8.0.0", "8.2.0")] == ["8.1.0", "8.2.0"]


def test_chain_refuses_no_path_and_forks(toy, monkeypatch):
    with pytest.raises(MigrationError, match="no path"):
        chain("8.2.0", "8.0.0")
    monkeypatch.setattr(migrations, "STEPS", toy + (Tag("8.0.0", "8.2.0"),))
    with pytest.raises(MigrationError, match="two steps"):
        chain("8.0.0", "8.2.0")


def test_migrate_copies_seals_and_preserves_seq(toy, tmp_path):
    old = _seed(tmp_path, "8.0.0", "r1")
    assert migrate(SqliteStore(tmp_path), None, to="8.2.0") == ["r1"]
    new = DirectoryLayout("8.2.0").sqlite_path(tmp_path, "r1")
    assert [b["tag"] for b in _bodies(new)] == ["8.2.0"] * 3
    assert [b["i"] for b in _bodies(new)] == [0, 1, 2]
    assert _bodies(old) == [{"i": 0}, {"i": 1}, {"i": 2}]  # non-destructive
    assert os.stat(old).st_mode & 0o222 == 0  # sealed


@pytest.mark.parametrize("rid", ["a?b", "a#b", "a b"])
def test_migrate_special_run_ids(toy, tmp_path, rid):
    """Review focus 2."""
    _seed(tmp_path, "8.0.0", rid)
    assert migrate(SqliteStore(tmp_path), [rid], to="8.1.0") == [rid]
    assert DirectoryLayout("8.1.0").sqlite_path(tmp_path, rid).exists()


def test_a_live_run_is_refused_and_untouched(toy, monkeypatch, tmp_path):
    old = _seed(tmp_path, "8.0.0", "r1")
    before = old.read_bytes()
    monkeypatch.setattr(migrations, "STEPS", (Tag("8.0.0", "8.1.0", live=True),))
    with pytest.raises(MigrationError, match="live episode"):
        migrate(SqliteStore(tmp_path), ["r1"], to="8.1.0")
    assert os.stat(old).st_mode & 0o200  # not sealed: refused before the seal
    assert old.read_bytes() == before
    assert not DirectoryLayout("8.1.0").sqlite_path(tmp_path, "r1").exists()


def test_a_refusal_by_the_step_comes_before_the_seal(toy, monkeypatch, tmp_path):
    """Ruling 12: the step's transform is dry-run on the pre-seal rows, so a
    deterministic refusal leaves the run untouched and writable, and a re-run
    refuses it the same way instead of finding it sealed."""
    old = _seed(tmp_path, "8.0.0", "r1")
    before = old.read_bytes()
    for _ in range(2):
        monkeypatch.setattr(migrations, "STEPS", (Tag("8.0.0", "8.1.0", boom_at=0),))
        with pytest.raises(RuntimeError, match="boom"):
            migrate(SqliteStore(tmp_path), ["r1"], to="8.1.0")
        assert os.stat(old).st_mode & 0o200  # not sealed
        assert old.read_bytes() == before
        assert not DirectoryLayout("8.1.0").sqlite_path(tmp_path, "r1").exists()


def test_a_failure_after_the_seal_leaves_it_sealed_and_a_retry_completes(
    toy, monkeypatch, tmp_path
):
    old = _seed(tmp_path, "8.0.0", "r1")
    monkeypatch.setattr(migrations, "STEPS", (Tag("8.0.0", "8.1.0", boom_at=1),))
    with pytest.raises(RuntimeError, match="boom"):
        migrate(SqliteStore(tmp_path), ["r1"], to="8.1.0")
    target = DirectoryLayout("8.1.0").sqlite_path(tmp_path, "r1")
    assert not target.exists()
    assert os.stat(old).st_mode & 0o222 == 0  # sealed, per §6
    monkeypatch.setattr(migrations, "STEPS", (Tag("8.0.0", "8.1.0"),))
    assert migrate(SqliteStore(tmp_path), ["r1"], to="8.1.0") == ["r1"]
    assert target.exists()


def test_a_write_path_failure_leaves_no_log_at_the_address(toy, monkeypatch, tmp_path):
    from runstate.migrations import stores

    old = _seed(tmp_path, "8.0.0", "r1")
    target = DirectoryLayout("8.1.0").sqlite_path(tmp_path, "r1")
    monkeypatch.setattr(migrations, "STEPS", (Tag("8.0.0", "8.1.0"),))
    real = os.replace

    def boom(*a, **k):
        raise OSError("disk gone")

    monkeypatch.setattr(stores.os, "replace", boom)
    with pytest.raises(OSError, match="disk gone"):
        migrate(SqliteStore(tmp_path), ["r1"], to="8.1.0")
    assert not target.exists()  # readers open the final address only
    assert os.stat(old).st_mode & 0o222 == 0
    monkeypatch.setattr(stores.os, "replace", real)
    assert migrate(SqliteStore(tmp_path), ["r1"], to="8.1.0") == ["r1"]
    assert target.exists()
    assert not list(target.parent.glob(".*.tmp"))


def test_wal_frames_of_a_crashed_writer_survive(toy, tmp_path, crashed_wal_writer):
    """Review focus 3: a writer that died leaves frames in the WAL; the copy holds them."""
    path = DirectoryLayout("8.0.0").sqlite_path(tmp_path, "r1")
    path.parent.mkdir()
    crashed_wal_writer(path, 50)
    migrate(SqliteStore(tmp_path), ["r1"], to="8.1.0")
    new = DirectoryLayout("8.1.0").sqlite_path(tmp_path, "r1")
    assert [b["i"] for b in _bodies(new)] == list(range(50))


def test_cli_migrate(toy, tmp_path, capsys):
    from runstate.cli import main

    _seed(tmp_path, "8.0.0", "r1")
    assert main(["migrate", str(tmp_path), "--to", "8.1.0"]) == 0
    assert "r1" in capsys.readouterr().out


@pytest.fixture
def pg_toy(pg_ready, monkeypatch):
    """Toy old/new schemas, the old one populated with three rows for run 'r1'."""
    import psycopg
    from psycopg import sql

    from runstate.channel.postgres import (
        _CREATE_INDEX,
        _CREATE_NAME_INDEX,
        _CREATE_TABLE,
    )

    for v in ["8.0.0", "8.1.0"]:
        monkeypatch.setitem(formats.FORMATS, v, DirectoryLayout(v))
    monkeypatch.setattr(migrations, "STEPS", (Tag("8.0.0", "8.1.0"),))
    old = sql.Identifier("runstate_v8_0_0")
    with psycopg.connect(pg_ready, autocommit=True) as conn:
        conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(old))
        conn.execute(sql.SQL("DROP SCHEMA IF EXISTS runstate_v8_1_0 CASCADE"))
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(old))
        with conn.transaction():
            conn.execute(sql.SQL("SET LOCAL search_path TO {}").format(old))
            for ddl in (_CREATE_TABLE, _CREATE_INDEX, _CREATE_NAME_INDEX):
                conn.execute(ddl)
            for i in range(3):
                conn.execute(
                    "INSERT INTO log (run_id, seq, topic, name, request_id, body, created_at)"
                    " VALUES ('r1', %s, 'value', 'x', NULL, %s, %s)",
                    [i, json.dumps({"i": i}), float(i)],
                )
    try:
        yield pg_ready
    finally:
        with psycopg.connect(pg_ready, autocommit=True) as conn:
            conn.execute("DROP SCHEMA IF EXISTS runstate_v8_0_0 CASCADE")
            conn.execute("DROP SCHEMA IF EXISTS runstate_v8_1_0 CASCADE")


def _pg_one(dsn, query):
    import psycopg

    with psycopg.connect(dsn) as conn:
        return conn.execute(query).fetchall()


def test_postgres_migrate_copies_seals_and_preserves_seq(pg_toy):
    assert migrate(PostgresStore(pg_toy), ["r1"], to="8.1.0") == ["r1"]
    new = _pg_one(
        pg_toy,
        "SELECT seq, body FROM runstate_v8_1_0.log WHERE run_id='r1' ORDER BY seq",
    )
    assert [s for s, _ in new] == [0, 1, 2]
    assert [json.loads(b)["tag"] for _, b in new] == ["8.1.0"] * 3
    old = _pg_one(
        pg_toy, "SELECT body FROM runstate_v8_0_0.log WHERE run_id='r1' ORDER BY seq"
    )
    assert [json.loads(b) for (b,) in old] == [{"i": 0}, {"i": 1}, {"i": 2}]
    assert _pg_one(pg_toy, "SELECT run_id FROM runstate_v8_0_0.sealed_runs") == [
        ("r1",)
    ]
    assert PostgresStore(pg_toy).formats_of(["r1"]) == {"r1": "8.1.0"}


def test_postgres_failure_rolls_back_completely(pg_toy, monkeypatch):
    monkeypatch.setattr(migrations, "STEPS", (Tag("8.0.0", "8.1.0", boom_at=0),))
    with pytest.raises(RuntimeError, match="boom"):
        migrate(PostgresStore(pg_toy), ["r1"], to="8.1.0")
    assert _pg_one(pg_toy, "SELECT count(*) FROM runstate_v8_0_0.log") == [(3,)]
    assert _pg_one(pg_toy, "SELECT to_regclass('runstate_v8_0_0.sealed_runs')") == [
        (None,)
    ]


def test_postgres_seals_before_it_reads(pg_toy, monkeypatch):
    from runstate.migrations import stores

    sealed = []
    real = stores.seal_postgres

    def spy(conn, schema, run_id):
        real(conn, schema, run_id)
        sealed.append(run_id)

    class Checked(Tag):
        def is_live(self, rows):
            assert sealed == ["r1"], "rows were read before the seal took its lock"
            return False

    monkeypatch.setattr(stores, "seal_postgres", spy)
    monkeypatch.setattr(migrations, "STEPS", (Checked("8.0.0", "8.1.0"),))
    assert migrate(PostgresStore(pg_toy), ["r1"], to="8.1.0") == ["r1"]


def test_postgres_copy_holds_every_committed_row(pg_toy):
    import psycopg

    with psycopg.connect(pg_toy, autocommit=True) as c:
        c.execute(
            "INSERT INTO runstate_v8_0_0.log (run_id, seq, topic, name, request_id, body,"
            " created_at) VALUES ('r1', 3, 'value', 'x', NULL, '{\"i\": 3}', 3.0)"
        )
    migrate(PostgresStore(pg_toy), ["r1"], to="8.1.0")
    q = "SELECT seq FROM runstate_v8_{}.log WHERE run_id='r1' ORDER BY seq"
    assert _pg_one(pg_toy, q.format("1_0")) == _pg_one(pg_toy, q.format("0_0"))
