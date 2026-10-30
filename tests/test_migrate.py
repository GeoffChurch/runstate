"""log-formats.md §6 with test-only formats and steps."""

import hashlib
import json
import os
import re
import stat
from typing import NamedTuple

import pytest

from runstate import formats, migrations
from runstate.channel.sqlite import SqliteChannel
from runstate.migrations import Memo, MigrationError, Row, chain, migrate, plan
from runstate.migrations.ambiguity import (
    Limits,
    NO_FACTS,
    Builtin,
    Detected,
    Divergence,
    Facts,
    Fork,
    Migrated,
    Projection,
    Undecided,
    parse_choices,
    render,
    resolutions,
)
from runstate.migrations.stores import PostgresStore, SqliteStore
from tests.format_helpers import toy_layout
from tests.migration_helpers import NO_HISTORY
from tests.migration_consumer import ASKED, UNSETTLED, ToyFacts

# The toy steps declare no facts, projections or divergences: the user has nothing
# to give them. The limits are the command line's, which no toy reads.
ONE = Limits(cap=1, budget=1, run_on=0)
NONE = parse_choices([], assume=[], resolve=[], accept=[], limits=ONE)


class Tag:
    """A toy step: stamps {"tag": TO} into every body. ``boom_at`` is the
    0-based ``transform`` call that raises. Call 0 is the detection pass's dry
    run; the dry run before the seal and the transform after it reuse it while
    the rows they read are the same (``Memo``), so a later call is the transform of
    rows that changed."""

    RENUMBERS = False
    FACTS = NO_FACTS
    PROJECTIONS = ()
    DIVERGENCES = ()

    def __init__(self, src, dst, live=False, boom_at=None):
        self.FROM, self.TO, self._live, self._boom = src, dst, live, boom_at
        self._calls = 0

    def is_live(self, rows):
        return self._live

    def detect(self, rows, *, assumptions, history):
        return Detected({})

    def transform(self, rows, *, resolutions, history):
        call, self._calls = self._calls, self._calls + 1
        if call == self._boom:
            raise RuntimeError("boom")
        return Migrated(self._rows(rows), {}, {})

    def _rows(self, rows):
        return [
            r._replace(body=json.dumps({**json.loads(r.body), "tag": self.TO}))
            for r in rows
        ]


def _planned(step):
    return plan(step, NONE, Memo())


@pytest.fixture
def toy(monkeypatch):
    for v in ["8.0.0", "8.1.0", "8.2.0"]:
        monkeypatch.setitem(formats.FORMATS, v, toy_layout(v))
    steps = (Tag("8.0.0", "8.1.0"), Tag("8.1.0", "8.2.0"))
    monkeypatch.setattr(migrations, "STEPS", steps)
    return steps


def _seed(root, version, rid, n=3):
    path = toy_layout(version).sqlite_path(root, rid)
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
    assert migrate(SqliteStore(tmp_path), None, to="8.2.0", choices=NONE).moved == (
        "r1",
    )
    new = toy_layout("8.2.0").sqlite_path(tmp_path, "r1")
    assert [b["tag"] for b in _bodies(new)] == ["8.2.0"] * 3
    assert [b["i"] for b in _bodies(new)] == [0, 1, 2]
    assert _bodies(old) == [{"i": 0}, {"i": 1}, {"i": 2}]  # non-destructive
    assert os.stat(old).st_mode & 0o222 == 0  # sealed


@pytest.mark.parametrize("rid", ["a?b", "a#b", "a b"])
def test_migrate_special_run_ids(toy, tmp_path, rid):
    """Review focus 2."""
    _seed(tmp_path, "8.0.0", rid)
    assert migrate(SqliteStore(tmp_path), [rid], to="8.1.0", choices=NONE).moved == (
        rid,
    )
    assert toy_layout("8.1.0").sqlite_path(tmp_path, rid).exists()


def test_a_live_run_is_refused_and_untouched(toy, monkeypatch, tmp_path):
    old = _seed(tmp_path, "8.0.0", "r1")
    before = old.read_bytes()
    monkeypatch.setattr(migrations, "STEPS", (Tag("8.0.0", "8.1.0", live=True),))
    (refused,) = migrate(
        SqliteStore(tmp_path), ["r1"], to="8.1.0", choices=NONE
    ).refused
    assert re.match(r"^run 'r1': [^']*live episode", refused.message)
    assert os.stat(old).st_mode & 0o200  # not sealed: refused before the seal
    assert old.read_bytes() == before
    assert not toy_layout("8.1.0").sqlite_path(tmp_path, "r1").exists()


def test_a_refusal_by_the_step_comes_before_the_seal(toy, monkeypatch, tmp_path):
    """Ruling 12: the step's transform is dry-run on the pre-seal rows, so a
    deterministic failure leaves the run untouched and writable, and a re-run
    fails it the same way instead of finding it sealed."""
    old = _seed(tmp_path, "8.0.0", "r1")
    before = old.read_bytes()
    for _ in range(2):
        monkeypatch.setattr(migrations, "STEPS", (Tag("8.0.0", "8.1.0", boom_at=0),))
        outcome = migrate(SqliteStore(tmp_path), ["r1"], to="8.1.0", choices=NONE)
        (failed,) = outcome.errored
        assert not outcome.moved and not outcome.refused
        assert isinstance(failed.error, RuntimeError) and not failed.sealed
        assert failed.message == (
            "run 'r1': RuntimeError: boom (its 8.0.0 log is left unmigrated)"
        )
        assert os.stat(old).st_mode & 0o200  # not sealed
        assert old.read_bytes() == before
        assert not toy_layout("8.1.0").sqlite_path(tmp_path, "r1").exists()


def test_a_failure_after_the_seal_leaves_it_sealed_and_a_retry_completes(
    toy, monkeypatch, tmp_path
):
    """A record that lands after the dry run read the run is read after the seal: the
    transform runs again on what changed, and its failure there leaves the run sealed.
    """
    from runstate.migrations import stores

    old = _seed(tmp_path, "8.0.0", "r1")
    real = stores._read_sqlite
    reads = []

    def landing(path):
        rows = real(path)
        reads.append(path)
        if len(reads) == 3:  # the read after the seal: a record has landed
            rows = [*rows, rows[-1]._replace(seq=rows[-1].seq + 1)]
        return rows

    monkeypatch.setattr(stores, "_read_sqlite", landing)
    monkeypatch.setattr(migrations, "STEPS", (Tag("8.0.0", "8.1.0", boom_at=1),))
    (failed,) = migrate(SqliteStore(tmp_path), ["r1"], to="8.1.0", choices=NONE).errored
    assert failed.sealed and failed.message == (
        "run 'r1': RuntimeError: boom (its 8.0.0 log is sealed and not copied; a "
        "re-run completes it)"
    )
    monkeypatch.setattr(stores, "_read_sqlite", real)
    target = toy_layout("8.1.0").sqlite_path(tmp_path, "r1")
    assert not target.exists()
    assert os.stat(old).st_mode & 0o222 == 0  # sealed, per §6
    monkeypatch.setattr(migrations, "STEPS", (Tag("8.0.0", "8.1.0"),))
    assert migrate(SqliteStore(tmp_path), ["r1"], to="8.1.0", choices=NONE).moved == (
        "r1",
    )
    assert target.exists()


def test_a_write_path_failure_leaves_no_log_at_the_address(toy, monkeypatch, tmp_path):
    from runstate.migrations import stores

    old = _seed(tmp_path, "8.0.0", "r1")
    target = toy_layout("8.1.0").sqlite_path(tmp_path, "r1")
    monkeypatch.setattr(migrations, "STEPS", (Tag("8.0.0", "8.1.0"),))
    real = os.replace

    def boom(*a, **k):
        raise OSError("disk gone")

    monkeypatch.setattr(stores.os, "replace", boom)
    (failed,) = migrate(SqliteStore(tmp_path), ["r1"], to="8.1.0", choices=NONE).errored
    assert failed.sealed and "OSError: disk gone" in failed.message
    assert not target.exists()  # readers open the final address only
    assert os.stat(old).st_mode & 0o222 == 0
    monkeypatch.setattr(stores.os, "replace", real)
    assert migrate(SqliteStore(tmp_path), ["r1"], to="8.1.0", choices=NONE).moved == (
        "r1",
    )
    assert target.exists()
    assert not list(target.parent.glob(".*.tmp"))


def test_a_migrator_overtaken_by_another_still_publishes_a_whole_log(
    toy, monkeypatch, tmp_path
):
    """T5: two migrators of one run, the second running whole while the first is
    about to publish. Each writes a temporary file of its own, so neither
    unlinks nor publishes the other's: both complete, the log at the address is
    whole, and no temporary file is left."""
    from runstate.migrations import stores

    _seed(tmp_path, "8.0.0", "r1")
    target = toy_layout("8.1.0").sqlite_path(tmp_path, "r1")
    step = Tag("8.0.0", "8.1.0")
    real = os.replace
    overtaken = []

    def replace(src, dst):
        if not overtaken:
            overtaken.append(src)
            SqliteStore(tmp_path).migrate_one(_planned(step), "r1")  # the second
        real(src, dst)

    monkeypatch.setattr(stores.os, "replace", replace)
    SqliteStore(tmp_path).migrate_one(_planned(step), "r1")
    assert overtaken
    assert [b["i"] for b in _bodies(target)] == [0, 1, 2]
    assert not list(target.parent.glob(".*.tmp"))


def test_wal_frames_of_a_crashed_writer_survive(toy, tmp_path, crashed_wal_writer):
    """Review focus 3: a writer that died leaves frames in the WAL; the copy holds them."""
    path = toy_layout("8.0.0").sqlite_path(tmp_path, "r1")
    path.parent.mkdir()
    crashed_wal_writer(path, 50)
    migrate(SqliteStore(tmp_path), ["r1"], to="8.1.0", choices=NONE)
    new = toy_layout("8.1.0").sqlite_path(tmp_path, "r1")
    assert [b["i"] for b in _bodies(new)] == list(range(50))


def test_a_hot_journal_of_a_crashed_writer_is_rolled_back(
    toy, tmp_path, crashed_journal_writer
):
    """I4: the pre-seal read opens read-write, so it rolls back a hot journal
    that a read-only open cannot, and the copy holds only committed records."""
    path = toy_layout("8.0.0").sqlite_path(tmp_path, "r1")
    path.parent.mkdir()
    crashed_journal_writer(path, 5)
    assert migrate(SqliteStore(tmp_path), ["r1"], to="8.1.0", choices=NONE).moved == (
        "r1",
    )
    new = toy_layout("8.1.0").sqlite_path(tmp_path, "r1")
    assert [b["i"] for b in _bodies(new)] == list(range(5))
    assert not path.with_name("r1.db-journal").exists()


@pytest.mark.parametrize("kind", ["absent", "a file"])
def test_a_root_that_is_not_a_directory_is_refused(tmp_path, kind):
    """I5: the store listed no runs under a root that does not exist, and
    `migrate` exited 0 having done nothing. It refuses, giving both commands."""
    root = tmp_path / "root"
    if kind == "a file":
        root.write_text("")
    with pytest.raises(MigrationError, match="not a directory") as exc:
        SqliteStore(root)
    assert f"runstate migrate {root}" in str(exc.value)
    assert "runstate migrate '<dsn>' --backend postgres" in str(exc.value)


def test_cli_refuses_a_root_that_is_not_a_directory(tmp_path):
    from runstate.cli import main

    with pytest.raises(MigrationError, match="not a directory"):
        main(["migrate", str(tmp_path / "absent")])


@pytest.mark.parametrize("dsn", ["postgres://u@h/db", "postgresql:///db?host=/tmp/s"])
def test_cli_refuses_a_dsn_without_the_postgres_backend(dsn, capsys):
    """I5: a DSN given without --backend postgres was read as a sqlite root
    with no runs, and `migrate` exited 0 having done nothing."""
    from runstate.cli import main

    with pytest.raises(SystemExit) as exc:
        main(["migrate", dsn])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "--backend postgres" in err


def test_cli_dsn_refusal_does_not_echo_the_password(capsys):
    """The library names a DSN by a placeholder (it can carry a password); the
    CLI's refusal must not print the one it was given."""
    from runstate.cli import main

    with pytest.raises(SystemExit):
        main(["migrate", "postgresql://u:hunter2secret@h/db"])
    err = capsys.readouterr().err
    assert "hunter2secret" not in err and "--backend postgres" in err


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

    for v in ["8.0.0", "8.1.0"]:
        monkeypatch.setitem(formats.FORMATS, v, toy_layout(v))
    monkeypatch.setattr(migrations, "STEPS", (Tag("8.0.0", "8.1.0"),))
    old = sql.Identifier("runstate_v8_0_0")
    with psycopg.connect(pg_ready, autocommit=True) as conn:
        conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(old))
        conn.execute(sql.SQL("DROP SCHEMA IF EXISTS runstate_v8_1_0 CASCADE"))
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(old))
        with conn.transaction():
            conn.execute(sql.SQL("SET LOCAL search_path TO {}").format(old))
            for ddl in toy_layout("8.0.0").pg_ddl:
                conn.execute(ddl)
            for i in range(3):
                conn.execute(
                    "INSERT INTO log (run_id, seq, topic, name, request_id, body, created_at)"
                    " VALUES ('r1', %s, 'value', 'x', NULL, %s, %s)",
                    [i, json.dumps({"i": i}), float(i)],
                )
    _forget_r1(pg_ready)
    try:
        yield pg_ready
    finally:
        with psycopg.connect(pg_ready, autocommit=True) as conn:
            conn.execute("DROP SCHEMA IF EXISTS runstate_v8_0_0 CASCADE")
            conn.execute("DROP SCHEMA IF EXISTS runstate_v8_1_0 CASCADE")
        _forget_r1(pg_ready)


def _forget_r1(dsn):
    """The shared database's manifest, without the toy run's entries."""
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn:
        reg = conn.execute("SELECT to_regclass('runstate_migrations.manifest')")
        if reg.fetchone() != (None,):
            conn.execute("DELETE FROM runstate_migrations.manifest WHERE run_id = 'r1'")


def _pg_one(dsn, query):
    import psycopg

    with psycopg.connect(dsn) as conn:
        return conn.execute(query).fetchall()


def test_postgres_migrate_copies_seals_and_preserves_seq(pg_toy):
    assert migrate(PostgresStore(pg_toy), ["r1"], to="8.1.0", choices=NONE).moved == (
        "r1",
    )
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
    """A failure in the detection pass, and one inside the sealing transaction (the
    transform of rows that changed after the dry run): each fails its run alone, and
    the transaction rolls the seal back, so the run is reported unsealed."""
    monkeypatch.setattr(migrations, "STEPS", (Tag("8.0.0", "8.1.0", boom_at=0),))
    (failed,) = migrate(PostgresStore(pg_toy), ["r1"], to="8.1.0", choices=NONE).errored
    assert (
        failed.message
        == "run 'r1': RuntimeError: boom (its 8.0.0 log is left unmigrated)"
    )
    assert _pg_one(pg_toy, "SELECT count(*) FROM runstate_v8_0_0.log") == [(3,)]
    assert _pg_one(pg_toy, "SELECT to_regclass('runstate_v8_0_0.sealed_runs')") == [
        (None,)
    ]
    # the transform inside the transaction, after the seal: the detection pass read
    # rows that differ (a row the table does not hold), so the memo serves nothing and
    # the second transform call is the one that raises
    from runstate.migrations import stores

    real = stores.PostgresStore.rows
    extra = Row(99, "value", "x", None, "{}", 0.0)
    monkeypatch.setattr(
        stores.PostgresStore, "rows", lambda s, v, r: [*real(s, v, r), extra]
    )
    monkeypatch.setattr(migrations, "STEPS", (Tag("8.0.0", "8.1.0", boom_at=1),))
    outcome = migrate(PostgresStore(pg_toy), ["r1"], to="8.1.0", choices=NONE)
    assert not outcome.moved
    (failed,) = outcome.errored
    assert not failed.sealed  # the transaction rolled the seal back with the rest
    assert _pg_one(pg_toy, "SELECT count(*) FROM runstate_v8_0_0.log") == [(3,)]
    assert _pg_one(pg_toy, "SELECT to_regclass('runstate_v8_0_0.sealed_runs')") == [
        (None,)
    ]
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
    # the store alone: the detection pass reads every run unsealed, by design
    PostgresStore(pg_toy).migrate_one(_planned(Checked("8.0.0", "8.1.0")), "r1")
    assert sealed == ["r1"]


def test_the_memo_serves_the_postgres_transform(pg_toy):
    """The transform in the sealing transaction is the detection pass's: the history
    is read before the seal, whose sealed_runs row would key it apart (Memo.key)."""
    migrate(PostgresStore(pg_toy), ["r1"], to="8.1.0", choices=NONE)
    assert migrations.STEPS[0]._calls == 1


def test_postgres_copy_holds_every_committed_row(pg_toy):
    import psycopg

    with psycopg.connect(pg_toy, autocommit=True) as c:
        c.execute(
            "INSERT INTO runstate_v8_0_0.log (run_id, seq, topic, name, request_id, body,"
            " created_at) VALUES ('r1', 3, 'value', 'x', NULL, '{\"i\": 3}', 3.0)"
        )
    migrate(PostgresStore(pg_toy), ["r1"], to="8.1.0", choices=NONE)
    q = "SELECT seq FROM runstate_v8_{}.log WHERE run_id='r1' ORDER BY seq"
    assert _pg_one(pg_toy, q.format("1_0")) == _pg_one(pg_toy, q.format("0_0"))


@pytest.mark.parametrize("mask", [0o022, 0o002])
def test_a_migrated_log_has_the_mode_a_native_birth_has(toy, tmp_path, mask):
    """A group-shared root (umask 002) must stay readable by the group after a
    migration: the migrated log honors the umask exactly as a birth does."""
    old = os.umask(mask)
    try:
        _seed(tmp_path, "8.0.0", "r1")
        assert migrate(SqliteStore(tmp_path), None, to="8.1.0", choices=NONE).moved == (
            "r1",
        )
        migrated = toy_layout("8.1.0").sqlite_path(tmp_path, "r1")
        born = _seed(tmp_path, "8.2.0", "r2")
        assert stat.S_IMODE(migrated.stat().st_mode) == stat.S_IMODE(
            born.stat().st_mode
        )
    finally:
        os.umask(old)


class Rewrite(Tag):
    """A toy step that rewrites each row with ``edit`` (seq/topic changes) and
    may append ``extra`` rows; ``RENUMBERS`` is per instance."""

    def __init__(self, src, dst, edit=lambda r: r, extra=(), renumbers=False):
        super().__init__(src, dst)
        self._edit, self._extra = edit, tuple(extra)
        self.RENUMBERS = renumbers

    def _rows(self, rows):
        return [self._edit(r) for r in rows] + list(self._extra)


class Dropper(Rewrite):
    def _rows(self, rows):
        return rows[1:]


def _row(seq, topic="value"):
    return Row(seq, topic, "x", None, "{}", 0.0)


_BAD_EDITS = {
    "seq": lambda r: r._replace(seq=r.seq + 10),
    "topic": lambda r: r._replace(topic="other"),
}


@pytest.mark.parametrize("what", sorted(_BAD_EDITS))
def test_a_step_that_does_not_renumber_may_not_change_a_seq_or_topic(
    what, toy, monkeypatch, tmp_path
):
    old = _seed(tmp_path, "8.0.0", "r1")
    before = old.read_bytes()
    step = Rewrite("8.0.0", "8.1.0", edit=_BAD_EDITS[what])
    monkeypatch.setattr(migrations, "STEPS", (step,))
    (refused,) = migrate(
        SqliteStore(tmp_path), ["r1"], to="8.1.0", choices=NONE
    ).refused
    assert re.match(r"^run 'r1': .*does not renumber", refused.message)
    assert os.stat(old).st_mode & 0o200  # refused before the seal
    assert old.read_bytes() == before
    assert not toy_layout("8.1.0").sqlite_path(tmp_path, "r1").exists()


@pytest.mark.parametrize(
    "step, refusal",
    [
        (Rewrite("8.0.0", "8.1.0", extra=[_row(1)]), "its output repeats seq 1"),
        (
            Rewrite("8.0.0", "8.1.0", extra=[_row(0)]),
            "it appended a row at seq 0, at or below the input's last seq 3",
        ),
        (Dropper("8.0.0", "8.1.0"), "its output has no row at seq 1 "),
    ],
    ids=["repeat", "below", "drop"],
)
def test_a_step_that_does_not_renumber_may_not_drop_repeat_or_insert_below(
    step, refusal, toy, monkeypatch, tmp_path
):
    """Each refusal says which: the toy log's seqs are 1 to 3."""
    _seed(tmp_path, "8.0.0", "r1")
    monkeypatch.setattr(migrations, "STEPS", (step,))
    (refused,) = migrate(
        SqliteStore(tmp_path), ["r1"], to="8.1.0", choices=NONE
    ).refused
    assert re.search(rf"does not renumber, but {refusal}", refused.message)


def test_a_step_that_does_not_renumber_may_append_above(toy, monkeypatch, tmp_path):
    _seed(tmp_path, "8.0.0", "r1")
    step = Rewrite("8.0.0", "8.1.0", extra=[_row(4), _row(5, "lifecycle.bound")])
    monkeypatch.setattr(migrations, "STEPS", (step,))
    assert migrate(SqliteStore(tmp_path), ["r1"], to="8.1.0", choices=NONE).moved == (
        "r1",
    )
    assert len(_bodies(toy_layout("8.1.0").sqlite_path(tmp_path, "r1"))) == 5


def test_a_renumbering_steps_new_seqs_reach_the_new_sqlite_log(monkeypatch, tmp_path):
    import sqlite3

    for v in ["0.3.0", "8.1.0"]:
        monkeypatch.setitem(formats.FORMATS, v, toy_layout(v))
    _seed(tmp_path, "0.3.0", "r1")
    step = Rewrite(
        "0.3.0", "8.1.0", edit=lambda r: r._replace(seq=r.seq + 10), renumbers=True
    )
    monkeypatch.setattr(migrations, "STEPS", (step,))
    assert migrate(SqliteStore(tmp_path), ["r1"], to="8.1.0", choices=NONE).moved == (
        "r1",
    )
    new = toy_layout("8.1.0").sqlite_path(tmp_path, "r1")
    c = sqlite3.connect(new)
    assert [q for (q,) in c.execute("SELECT seq FROM log ORDER BY seq")] == [11, 12, 13]
    c.close()


def test_chain_refuses_a_second_renumbering_step(toy, monkeypatch):
    steps = (
        Rewrite("8.0.0", "8.1.0", renumbers=True),
        Rewrite("8.1.0", "8.2.0", renumbers=True),
    )
    monkeypatch.setattr(migrations, "STEPS", steps)
    with pytest.raises(MigrationError, match="more than one step renumbers"):
        chain("8.0.0", "8.2.0")


def test_chain_refuses_a_renumbering_step_that_leaves_another_format(toy, monkeypatch):
    steps = (Rewrite("8.0.0", "8.1.0", renumbers=True), Tag("8.1.0", "8.2.0"))
    monkeypatch.setattr(migrations, "STEPS", steps)
    with pytest.raises(MigrationError, match="not 0.3.0"):
        chain("8.0.0", "8.2.0")


@pytest.mark.parametrize("what", sorted(_BAD_EDITS))
def test_postgres_preservation_refusal_rolls_back(what, pg_toy, monkeypatch):
    step = Rewrite("8.0.0", "8.1.0", edit=_BAD_EDITS[what])
    monkeypatch.setattr(migrations, "STEPS", (step,))
    (refused,) = migrate(
        PostgresStore(pg_toy), ["r1"], to="8.1.0", choices=NONE
    ).refused
    assert re.match(r"^run 'r1': .*does not renumber", refused.message)
    assert _pg_one(pg_toy, "SELECT count(*) FROM runstate_v8_0_0.log") == [(3,)]
    assert _pg_one(pg_toy, "SELECT to_regclass('runstate_v8_0_0.sealed_runs')") == [
        (None,)
    ]
    assert _pg_one(pg_toy, "SELECT to_regclass('runstate_v8_1_0.log')") == [(None,)]


def test_a_renumbering_steps_new_seqs_reach_the_new_postgres_log(pg_toy, monkeypatch):
    step = Rewrite(
        "0.3.0", "8.1.0", edit=lambda r: r._replace(seq=r.seq + 10), renumbers=True
    )
    step.FROM = "8.0.0"  # the toy schema stands in; chain's 0.3.0 rule is tested above
    monkeypatch.setattr(migrations, "RENUMBERING_FROM", "8.0.0")
    monkeypatch.setattr(migrations, "STEPS", (step,))
    assert migrate(PostgresStore(pg_toy), ["r1"], to="8.1.0", choices=NONE).moved == (
        "r1",
    )
    new = _pg_one(
        pg_toy, "SELECT seq FROM runstate_v8_1_0.log WHERE run_id='r1' ORDER BY seq"
    )
    assert new == [(10,), (11,), (12,)]


# ----- the shared mechanism: facts, projections, divergences (§6, "Ambiguity") ------


class Ask(NamedTuple):
    """A toy projection's question: the rows, the forks, and the run's history."""

    rows: tuple
    forks: tuple
    history: tuple


class Say:
    """A built-in toy chooser: reads every fork as ``word``."""

    def __init__(self, word):
        self.word = word

    def choose(self, question):
        return {s: self.word for s in question.forks}


def _words(question, answer):
    """A toy answer's shape: a word for each fork."""
    if not isinstance(answer, dict) or set(answer) != set(question.forks):
        raise ValueError(f"{answer!r} is no word per fork")
    if not all(isinstance(w, str) for w in answer.values()):
        raise ValueError(f"{answer!r} holds a reading that is no word")
    return answer


def _projection(name):
    return Projection(
        name,
        f"which word a toy record of {name} carries.",
        "a word",
        (Builtin("yes", Say("yes"), "say yes"), Builtin("no", Say("no"), "say no")),
        _words,
    )


class Forking(Tag):
    """A toy step with one projection, forking at every value whose ``i`` is in
    ``forks`` unless its facts settle them, and one divergence, firing at every value
    whose ``i`` is in ``diverge``. It stamps each fork's reading into its body."""

    def __init__(self, src, dst, name, *, forks=(), diverge=()):
        super().__init__(src, dst)
        self.FACTS = Facts(ToyFacts, UNSETTLED, lambda f: {"settled": f.settled})
        self.PROJECTIONS = (_projection(name),)
        self.DIVERGENCES = (Divergence(f"{name}-drift", f"{name}'s read drifts."),)
        self._forks, self._diverge = set(forks), set(diverge)
        self.histories = []

    def _forked(self, rows, assumptions):
        if self.FACTS.of(assumptions).settled:
            return ()
        return tuple(r.seq for r in rows if json.loads(r.body).get("i") in self._forks)

    def detect(self, rows, *, assumptions, history):
        (p,) = self.PROJECTIONS
        forks = self._forked(rows, assumptions)
        return Detected({p.name: tuple(Fork(s, ("settled",)) for s in forks)})

    def transform(self, rows, *, resolutions, history):
        self.histories.append(history)
        (p,), (d,) = self.PROJECTIONS, self.DIVERGENCES
        forks = self._forked(rows, resolutions.assumptions)
        read = p.ask(resolutions, Ask(tuple(rows), forks, history)) if forks else {}
        out = [
            r._replace(
                body=json.dumps(
                    {**json.loads(r.body), "tag": self.TO, "read": read.get(r.seq)}
                )
            )
            for r in rows
        ]
        drift = tuple(
            r.seq for r in rows if json.loads(r.body).get("i") in self._diverge
        )
        return Migrated(out, {p.name: dict(read)}, {d.name: drift})


@pytest.fixture
def forking(monkeypatch):
    """Two chained toy steps, each with its own projection: ``a`` forks at i=1 and
    ``b`` at i=2. Runs r1 and r2 hold i = 0, 1, 2; r3 holds only i = 0."""
    for v in ["8.0.0", "8.1.0", "8.2.0"]:
        monkeypatch.setitem(formats.FORMATS, v, toy_layout(v))
    steps = (
        Forking("8.0.0", "8.1.0", "a", forks=[1]),
        Forking("8.1.0", "8.2.0", "b", forks=[2]),
    )
    monkeypatch.setattr(migrations, "STEPS", steps)
    return steps


def _choose(**given):
    return parse_choices(
        migrations.STEPS,
        assume=given.get("assume", []),
        resolve=given.get("resolve", []),
        accept=given.get("accept", []),
        limits=given.get("limits", Limits(cap=7, budget=70, run_on=3)),
    )


def _three(root):
    _seed(root, "8.0.0", "r1")
    _seed(root, "8.0.0", "r2")
    _seed(root, "8.0.0", "r3", n=1)


def _untouched(root):
    """Nothing written: no run sealed, no new format, no manifest."""
    for rid in ["r1", "r2", "r3"]:
        assert os.stat(toy_layout("8.0.0").sqlite_path(root, rid)).st_mode & 0o200
    assert sorted(p.name for p in root.iterdir()) == ["v8.0.0"]


def _manifest(root):
    path = root / "runstate-migrations.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_the_command_line_refuses_what_names_nothing(forking):
    for given, refusal in [
        ({"resolve": ["c=yes"]}, "no step declares the projection 'c'"),
        ({"resolve": ["a"]}, "spell it <projection>=<chooser>"),
        ({"resolve": ["a=maybe"]}, "no such built-in; they are yes, no, refuse"),
        ({"resolve": ["a=yes", "a=no"]}, "--resolve a is given twice"),
        ({"resolve": ["a=tests.nowhere:X"]}, "cannot import it"),
        ({"resolve": ["a=tests.migration_consumer:MISSING"]}, "cannot import it"),
        ({"resolve": ["a=tests.migration_consumer:NOT_A_CHOOSER"]}, "is no chooser"),
        ({"accept": ["c-drift"]}, "no step declares the divergence 'c-drift'"),
        (
            {"limits": Limits(cap=0, budget=1, run_on=0)},
            "--cap 0: a step must be let hold one state at least",
        ),
        (
            {"limits": Limits(cap=5, budget=4, run_on=0)},
            "--budget 4: below --cap 5",
        ),
        (
            {"limits": Limits(cap=1, budget=1, run_on=-1)},
            "--run-on -1: a count of labels",
        ),
        ({"accept": ["a-drift", "a-drift"]}, "an --accept is given twice"),
        (
            {"assume": ["tests.migration_consumer:NOT_A_CHOOSER"]},
            "states no step's facts",
        ),
        (
            {
                "assume": [
                    "tests.migration_consumer:SETTLED",
                    "tests.migration_consumer:UNSETTLED",
                ]
            },
            "two --assume name step 8.0.0 -> 8.1.0's facts",
        ),
    ]:
        with pytest.raises(MigrationError, match=re.escape(refusal)):
            _choose(**given)


def test_two_steps_may_not_declare_one_name(forking, monkeypatch):
    twin = Forking("8.1.0", "8.2.0", "a")
    monkeypatch.setattr(migrations, "STEPS", (forking[0], twin))
    with pytest.raises(MigrationError, match="two steps declare the projection a"):
        _choose()


def test_a_chooser_named_by_module_and_attr_is_the_object_it_names(forking):
    choices = _choose(resolve=["a=tests.migration_consumer:ASKED", "b=refuse"])
    assert choices.resolved["a"].chooser is ASKED
    assert choices.resolved["a"].spelling == "tests.migration_consumer:ASKED"
    assert choices.resolved["b"].spelling == "refuse"
    assert choices.limits == Limits(cap=7, budget=70, run_on=3)


def test_detection_takes_no_chooser_and_finds_the_same_forks_whatever_the_choices(
    forking, tmp_path
):
    """detect is given the assumptions alone, so no chooser can move a fork."""
    _three(tmp_path)
    rows = SqliteStore(tmp_path).rows("8.0.0", "r1")
    step = forking[0]
    found = [
        step.detect(
            rows, assumptions=resolutions(step, c).assumptions, history=NO_HISTORY
        )
        for c in [_choose(), _choose(resolve=["a=yes"]), _choose(resolve=["a=no"])]
    ]
    assert found == [Detected({"a": (Fork(2, ("settled",)),)})] * 3


def test_the_detection_pass_writes_nothing_while_a_fork_has_no_chooser(
    forking, tmp_path
):
    _three(tmp_path)
    with pytest.raises(Undecided) as exc:
        migrate(SqliteStore(tmp_path), None, to="8.2.0", choices=_choose())
    _untouched(tmp_path)
    report = exc.value.report
    ((label, p),) = report.forks
    assert (label, p.name) == ("8.0.0 -> 8.1.0", "a")
    # b's forks are found on a's output, so b is undecided until a is decided
    assert report.undecided == {"8.1.0 -> 8.2.0": {"r1", "r2"}}
    text = str(exc.value)
    assert text.count("a (8.0.0 -> 8.1.0): which word") == 1  # one summary
    assert "forks: 2 in 2 runs, e.g. r1 seq 2, r2 seq 2" in text
    assert "built-ins: yes     say yes" in text
    assert "refuse  refuse every run with a fork of it" in text
    assert "settled by stating: settled (2)" in text
    assert "add: --resolve a=<built-in or module:attr>" in text
    assert (
        "undecided until the questions above are answered" in text
        and "8.1.0 -> 8.2.0" in text
    )


def test_a_later_steps_forks_are_asked_once_the_earlier_ones_are_decided(
    forking, tmp_path
):
    _three(tmp_path)
    with pytest.raises(Undecided) as exc:
        migrate(
            SqliteStore(tmp_path), None, to="8.2.0", choices=_choose(resolve=["a=yes"])
        )
    _untouched(tmp_path)
    assert [p.name for _, p in exc.value.report.forks] == ["b"]
    assert not exc.value.report.undecided
    choices = _choose(resolve=["a=yes", "b=no"])
    outcome = migrate(SqliteStore(tmp_path), None, to="8.2.0", choices=choices)
    assert outcome.moved == ("r1", "r2", "r3") and not outcome.refused
    final = _bodies(toy_layout("8.2.0").sqlite_path(tmp_path, "r1"))
    assert [b["read"] for b in final] == [None, None, "no"]


def test_at_most_three_example_sites_are_shown(forking, tmp_path):
    for k in range(5):
        _seed(tmp_path, "8.0.0", f"r{k}")
    with pytest.raises(Undecided) as exc:
        migrate(SqliteStore(tmp_path), None, to="8.1.0", choices=_choose())
    assert "forks: 5 in 5 runs, e.g. r0 seq 2, r1 seq 2, r2 seq 2\n" in str(exc.value)


def test_a_stated_fact_settles_the_forks_and_is_recorded(forking, tmp_path):
    _three(tmp_path)
    choices = _choose(assume=["tests.migration_consumer:SETTLED"])
    outcome = migrate(SqliteStore(tmp_path), None, to="8.1.0", choices=choices)
    assert outcome.moved == ("r1", "r2", "r3")
    entry = _manifest(tmp_path)[0]
    assert entry["assumed"] == "tests.migration_consumer:SETTLED"
    assert SqliteStore(tmp_path).facts(entry["facts"]) == {"settled": True}
    assert entry["readings"] == {"a": {}}


def test_a_projection_no_run_forks_needs_no_chooser(forking, tmp_path):
    _seed(tmp_path, "8.0.0", "r3", n=1)
    outcome = migrate(SqliteStore(tmp_path), None, to="8.2.0", choices=_choose())
    assert outcome.moved == ("r3",)
    # recorded as no choice, apart from an explicit refuse
    assert [e["resolved"] for e in _manifest(tmp_path)] == [{"a": None}, {"b": None}]


def test_an_explicit_refuse_refuses_only_the_runs_with_a_fork(forking, tmp_path):
    _three(tmp_path)
    outcome = migrate(
        SqliteStore(tmp_path), None, to="8.1.0", choices=_choose(resolve=["a=refuse"])
    )
    assert outcome.moved == ("r3",)
    assert [r.message for r in outcome.refused] == [
        "run 'r1': --resolve a=refuse refused it: its chooser is refuse",
        "run 'r2': --resolve a=refuse refused it: its chooser is refuse",
    ]
    for rid in ["r1", "r2"]:  # untouched
        assert os.stat(toy_layout("8.0.0").sqlite_path(tmp_path, rid)).st_mode & 0o200
        assert not toy_layout("8.1.0").sqlite_path(tmp_path, rid).exists()
    assert [e["run_id"] for e in _manifest(tmp_path)] == ["r3"]  # none for a refusal


def test_a_chooser_sees_its_forks_and_the_run_s_history(forking, tmp_path):
    """The later step receives the earlier step's entry, chosen in this invocation,
    and in a later one from the root's manifest."""
    _three(tmp_path)
    ASKED.questions.clear()
    choices = _choose(resolve=["a=tests.migration_consumer:ASKED", "b=yes"])
    migrate(SqliteStore(tmp_path), ["r1"], to="8.1.0", choices=choices)
    assert {q.forks for q in ASKED.questions} == {(2,)}
    forking[1].histories.clear()
    migrate(SqliteStore(tmp_path), ["r1"], to="8.2.0", choices=choices)
    for history in forking[1].histories:
        (earlier,) = history.entries
        assert (earlier.from_format, earlier.to_format) == ("8.0.0", "8.1.0")
        assert earlier.resolved == {"a": "tests.migration_consumer:ASKED"}
        assert earlier.readings == {"a": {2: "asked-2"}}
    forking[1].histories.clear()
    migrate(SqliteStore(tmp_path), ["r2"], to="8.2.0", choices=choices)
    assert {h.entries[0].readings["a"][2] for h in forking[1].histories} == {"asked-2"}


def test_a_divergence_halts_the_pass_until_it_is_accepted(
    forking, monkeypatch, tmp_path
):
    """The owner's ruling: an unaccepted divergence is a question the user has not
    answered, so the pass writes nothing, as for an unresolved fork."""
    step = Forking("8.0.0", "8.1.0", "a", diverge=[2])
    monkeypatch.setattr(migrations, "STEPS", (step,))
    _three(tmp_path)
    with pytest.raises(Undecided) as exc:
        migrate(SqliteStore(tmp_path), None, to="8.1.0", choices=_choose())
    _untouched(tmp_path)
    text = str(exc.value)
    assert "a-drift (8.0.0 -> 8.1.0): a's read drifts." in text
    assert "sites: 2 in 2 runs, e.g. r1 seq 3, r2 seq 3" in text
    assert "add: --accept a-drift" in text
    outcome = migrate(
        SqliteStore(tmp_path), None, to="8.1.0", choices=_choose(accept=["a-drift"])
    )
    assert outcome.moved == ("r1", "r2", "r3") and not outcome.refused
    assert {e["run_id"]: e["accepted"] for e in _manifest(tmp_path)} == {
        "r1": {"a-drift": [3]},
        "r2": {"a-drift": [3]},
        "r3": {},
    }


def test_the_pass_keeps_the_refusals_it_found(forking, monkeypatch, tmp_path):
    """A run refused whatever is chosen is reported in the same round as the
    questions, not on the next invocation."""
    step = Forking("8.0.0", "8.1.0", "a", forks=[1])
    step.is_live = lambda rows: len(rows) == 1
    monkeypatch.setattr(migrations, "STEPS", (step,))
    _three(tmp_path)
    with pytest.raises(Undecided) as exc:
        migrate(SqliteStore(tmp_path), None, to="8.1.0", choices=_choose())
    assert exc.value.report.refused == {
        "r3": "run 'r3': it has a live episode; stop it first"
    }
    assert "refused whatever is chosen:\n  run 'r3': it has a live" in str(exc.value)


def test_the_manifest_holds_one_entry_per_run_per_step(forking, tmp_path):
    _three(tmp_path)
    choices = _choose(
        resolve=["a=yes", "b=no"], limits=Limits(cap=11, budget=12, run_on=1)
    )
    migrate(SqliteStore(tmp_path), None, to="8.2.0", choices=choices)
    entries = _manifest(tmp_path)
    assert sorted((e["run_id"], e["from"], e["to"]) for e in entries) == [
        (rid, a, b)
        for rid in ["r1", "r2", "r3"]
        for a, b in [("8.0.0", "8.1.0"), ("8.1.0", "8.2.0")]
    ]
    (r1a,) = [e for e in entries if e["run_id"] == "r1" and e["from"] == "8.0.0"]
    assert (r1a["cap"], r1a["budget"], r1a["run_on"]) == (11, 12, 1)
    assert r1a["assumed"] is None
    assert SqliteStore(tmp_path).facts(r1a["facts"]) == {"settled": False}
    assert r1a["resolved"] == {"a": "yes"} and r1a["readings"] == {"a": {"2": "yes"}}
    assert r1a["runstate"] and r1a["at"] > 0
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", r1a["step_source"])


def test_the_facts_statement_is_written_once_per_root_and_cited_by_digest(
    forking, tmp_path
):
    """Every entry cites the statement by the digest of its canonical rendering; the
    root holds the rendering once however many runs and steps cite it, and a reader
    resolves it, checked, only when it asks."""
    _three(tmp_path)
    migrate(
        SqliteStore(tmp_path),
        None,
        to="8.2.0",
        choices=_choose(assume=["tests.migration_consumer:SETTLED"], resolve=["b=no"]),
    )
    cited = {e["facts"] for e in _manifest(tmp_path)}
    assert len(_manifest(tmp_path)) == 6 and len(cited) == 1
    (digest,) = cited
    text = json.dumps({"settled": True}, sort_keys=True, separators=(",", ":"))
    assert digest == "sha256:" + hashlib.sha256(text.encode()).hexdigest()
    lines = (tmp_path / "runstate-migrations-facts.jsonl").read_text().splitlines()
    assert [json.loads(x) for x in lines] == [{"digest": digest, "facts": text}]
    assert SqliteStore(tmp_path).facts(digest) == {"settled": True}
    with pytest.raises(MigrationError, match="holds no facts statement"):
        SqliteStore(tmp_path).facts("sha256:" + "0" * 64)
    forged = json.dumps({"digest": "sha256:" + "1" * 64, "facts": text})
    with (tmp_path / "runstate-migrations-facts.jsonl").open("a") as f:
        f.write(forged + "\n")
    with pytest.raises(MigrationError, match="does not hash to it"):
        SqliteStore(tmp_path).facts("sha256:" + "1" * 64)


def test_the_entry_is_written_before_the_publish_and_the_latest_is_read(
    forking, monkeypatch, tmp_path
):
    """A crash between the two leaves an entry for an attempt that did not publish,
    never a migrated run without one; the re-run's entry is the one read."""
    from runstate.migrations import stores

    _seed(tmp_path, "8.0.0", "r1")
    target = toy_layout("8.1.0").sqlite_path(tmp_path, "r1")
    real = os.replace

    def crash(*a, **k):
        raise OSError("crashed before the publish")

    monkeypatch.setattr(stores.os, "replace", crash)
    (failed,) = migrate(
        SqliteStore(tmp_path), None, to="8.1.0", choices=_choose(resolve=["a=yes"])
    ).errored
    assert failed.sealed and isinstance(failed.error, OSError)
    assert not target.exists()
    assert [e["readings"] for e in _manifest(tmp_path)] == [{"a": {"2": "yes"}}]
    monkeypatch.setattr(stores.os, "replace", real)
    migrate(SqliteStore(tmp_path), None, to="8.1.0", choices=_choose(resolve=["a=no"]))
    assert target.exists() and len(_manifest(tmp_path)) == 2
    (entry,) = SqliteStore(tmp_path).history("r1").entries
    assert entry.readings == {"a": {2: "no"}}


@pytest.mark.parametrize(
    "chooser, refusal",
    [
        ("GARBLED", "answered in another shape: {} is no word per fork"),
        ("RAISING", "raised RuntimeError: a consumer's bug"),
        ("ASKED_ELSEWHERE", "raised AttributeError"),
    ],
)
def test_a_choosers_failure_refuses_its_runs_and_the_others_migrate(
    forking, tmp_path, chooser, refusal
):
    """A consumer's chooser is code runstate did not write: a wrong shape, an
    exception, or a chooser written for another question refuses the run, naming the
    run, the projection and the chooser, and never escapes as a traceback."""
    _three(tmp_path)
    flag = f"--resolve a=tests.migration_consumer:{chooser}"
    outcome = migrate(
        SqliteStore(tmp_path), None, to="8.1.0", choices=_choose(resolve=[flag[10:]])
    )
    assert outcome.moved == ("r3",)
    for run, refused in zip(["r1", "r2"], outcome.refused, strict=True):
        assert refused.message.startswith(f"run '{run}': {flag} {refusal}")


def test_a_class_is_no_chooser(forking):
    with pytest.raises(MigrationError, match="Asked is a class; name an instance"):
        _choose(resolve=["a=tests.migration_consumer:Asked"])


def test_a_run_is_transformed_once_while_its_rows_stand(toy, monkeypatch, tmp_path):
    """The detection pass's transform, the dry run before the seal and the transform
    after it read the same rows: the step runs once."""
    _seed(tmp_path, "8.0.0", "r1")
    step = Tag("8.0.0", "8.1.0")
    monkeypatch.setattr(migrations, "STEPS", (step,))
    assert migrate(SqliteStore(tmp_path), ["r1"], to="8.1.0", choices=NONE).moved == (
        "r1",
    )
    assert step._calls == 1


def test_a_step_without_facts_takes_none():
    with pytest.raises(MigrationError, match="a step without facts takes none"):
        parse_choices(
            migrations.STEPS,
            assume=["runstate.migrations.ambiguity:NO_FACTS.unstated"],
            resolve=[],
            accept=[],
            limits=ONE,
        )


def test_the_cli_takes_the_users_word(forking, tmp_path, capsys):
    from runstate.cli import main

    _three(tmp_path)
    assert main(["migrate", str(tmp_path), "--to", "8.2.0"]) == 1
    err = capsys.readouterr().err
    assert "nothing was migrated" in err and "--resolve a=<built-in" in err
    _untouched(tmp_path)
    with pytest.raises(SystemExit):
        main(["migrate", str(tmp_path), "--resolve", "z=yes"])
    assert "no step declares the projection 'z'" in capsys.readouterr().err
    argv = ["migrate", str(tmp_path), "--to", "8.1.0", "--resolve", "a=refuse"]
    assert main([*argv, "--cap", "5", "--budget", "50", "--run-on", "4"]) == 1
    out = capsys.readouterr()
    assert out.out.split() == ["r3"]
    assert "refused: run 'r1'" in out.err
    e = _manifest(tmp_path)[0]
    assert (e["cap"], e["budget"], e["run_on"]) == (5, 50, 4)
    assert main(["migrate", str(tmp_path), "--to", "8.1.0", "--resolve", "a=no"]) == 0
    assert capsys.readouterr().out.split() == ["r1", "r2"]


def test_the_cli_asks_for_a_divergence(forking, monkeypatch, tmp_path, capsys):
    from runstate.cli import BUDGET, CAP, RUN_ON, main

    monkeypatch.setattr(
        migrations, "STEPS", (Forking("8.0.0", "8.1.0", "a", diverge=[0]),)
    )
    _seed(tmp_path, "8.0.0", "r3", n=1)
    assert main(["migrate", str(tmp_path), "--to", "8.1.0"]) == 1
    err = capsys.readouterr().err
    assert "nothing was migrated" in err and "add: --accept a-drift" in err
    assert main(["migrate", str(tmp_path), "--to", "8.1.0", "--accept", "a-drift"]) == 0
    e = _manifest(tmp_path)[0]
    assert (e["cap"], e["budget"], e["run_on"]) == (CAP, BUDGET, RUN_ON)


def test_postgres_writes_the_manifest_entry_in_the_sealing_transaction(
    pg_toy, monkeypatch
):
    from runstate.migrations import stores

    step = Forking("8.0.0", "8.1.0", "a", forks=[1])
    monkeypatch.setattr(migrations, "STEPS", (step,))
    count = "SELECT count(*) FROM runstate_migrations.manifest WHERE run_id = 'r1'"

    def none_yet():
        reg = _pg_one(pg_toy, "SELECT to_regclass('runstate_migrations.manifest')")
        return reg == [(None,)] or _pg_one(pg_toy, count) == [(0,)]

    real = stores._pg_manifest

    def boom(conn):
        real(conn)
        raise RuntimeError("after the manifest table, before the insert commits")

    monkeypatch.setattr(stores, "_pg_manifest", boom)
    (failed,) = migrate(
        PostgresStore(pg_toy),
        ["r1"],
        to="8.1.0",
        choices=_choose(resolve=["a=yes"]),
    ).errored
    assert isinstance(failed.error, RuntimeError) and not failed.sealed
    assert none_yet()
    assert _pg_one(pg_toy, "SELECT to_regclass('runstate_v8_1_0.log')") == [(None,)]
    monkeypatch.setattr(stores, "_pg_manifest", real)
    outcome = migrate(
        PostgresStore(pg_toy), ["r1"], to="8.1.0", choices=_choose(resolve=["a=yes"])
    )
    assert outcome.moved == ("r1",)
    (entry,) = PostgresStore(pg_toy).history("r1").entries
    assert (entry.from_format, entry.resolved) == ("8.0.0", {"a": "yes"})
    assert entry.readings == {"a": {1: "yes"}}
    # the facts statement, cited by digest and written once in the same transaction
    assert PostgresStore(pg_toy).facts(entry.facts) == {"settled": False}
    held = "SELECT count(*) FROM runstate_migrations.facts WHERE digest = %s"
    assert _pg_one(pg_toy, held.replace("%s", f"'{entry.facts}'")) == [(1,)]
    with pytest.raises(MigrationError, match="holds no facts statement"):
        PostgresStore(pg_toy).facts("sha256:" + "0" * 64)


def _migrated(n):
    return Migrated([Row(k, "value", "x", None, "{}", 0.0) for k in range(n)], {}, {})


def test_a_full_memo_takes_no_more_and_keeps_what_it_took(monkeypatch):
    """The detection pass fills the memo in run order and the migrate pass reads it in
    the same order: once full it takes no more, so the runs it took first still reuse
    their transforms."""
    monkeypatch.setattr(Memo, "ROWS", 5)
    memo = Memo()
    memo.put("a", _migrated(3))
    memo.put("b", _migrated(3))  # past the bound: not taken
    memo.put("c", _migrated(2))
    assert memo.get("a") is not None and memo.get("c") is not None
    assert memo.get("b") is None


def test_an_unexpected_error_fails_its_run_alone(toy, monkeypatch, tmp_path):
    """A bug in a step is no refusal, and it ends no invocation: the run it failed is
    reported with the error and left as the error found it (unmigrated here, before
    any seal), and the other runs go on. The second run's detection transform raises:
    the first and third are moved."""
    _three(tmp_path)
    monkeypatch.setattr(migrations, "STEPS", (Tag("8.0.0", "8.1.0", boom_at=1),))
    outcome = migrate(SqliteStore(tmp_path), None, to="8.1.0", choices=NONE)
    assert outcome.moved == ("r1", "r3") and not outcome.refused
    (failed,) = outcome.errored
    assert isinstance(failed.error, RuntimeError) and failed.at == "8.0.0"
    assert failed.message == (
        "run 'r2': RuntimeError: boom (its 8.0.0 log is left unmigrated)"
    )
    old = toy_layout("8.0.0").sqlite_path(tmp_path, "r2")
    assert os.stat(old).st_mode & 0o200  # not sealed
    assert not toy_layout("8.1.0").sqlite_path(tmp_path, "r2").exists()
    for rid in ("r1", "r3"):
        assert toy_layout("8.1.0").sqlite_path(tmp_path, rid).exists()


def test_an_unexpected_error_in_the_detection_pass_stays_in_the_report(
    forking, monkeypatch, tmp_path
):
    """Where other runs leave questions, the run a bug failed is reported beside them in
    the same round, and nothing is migrated."""
    _three(tmp_path)
    step = migrations.STEPS[0]
    detect = step.detect

    def failing(rows, *, assumptions, history):
        if len(rows) == 1:  # r3's one row
            raise RuntimeError("boom")
        return detect(rows, assumptions=assumptions, history=history)

    monkeypatch.setattr(step, "detect", failing)
    with pytest.raises(Undecided) as exc:
        migrate(SqliteStore(tmp_path), None, to="8.2.0", choices=_choose())
    text = str(exc.value)
    assert "--resolve a=<built-in" in text
    why = "run 'r3': RuntimeError: boom (its 8.0.0 log is left unmigrated)"
    assert text.endswith(f"failed on an unexpected error, a bug in a step:\n  {why}")
    assert exc.value.report.errored == {"r3": why}
    _untouched(tmp_path)


def test_the_cli_reports_a_failed_run_with_its_traceback(
    toy, monkeypatch, tmp_path, capsys
):
    from runstate.cli import main

    _three(tmp_path)
    monkeypatch.setattr(migrations, "STEPS", (Tag("8.0.0", "8.1.0", boom_at=1),))
    assert main(["migrate", str(tmp_path), "--to", "8.1.0"]) == 1
    out, err = capsys.readouterr()
    assert out.split() == ["r1", "r3"]
    assert (
        "error: run 'r2': RuntimeError: boom (its 8.0.0 log is left unmigrated)" in err
    )
    assert "Traceback (most recent call last)" in err
    assert 'raise RuntimeError("boom")' in err


@pytest.mark.parametrize("rows", [Memo.ROWS, 0])
def test_a_run_is_transformed_once_and_past_the_memo_s_bound_twice(
    rows, monkeypatch, tmp_path
):
    """The detection pass transforms a run, and the memo serves its dry run and its
    transform after the seal. Past the memo's bound the dry run transforms it again,
    and the transform after the seal takes the dry run's output, the rows the seal's
    re-read found being those it read: twice, never three times (final review M1)."""
    step = Tag("8.0.0", "8.1.0")
    for v in ["8.0.0", "8.1.0"]:
        monkeypatch.setitem(formats.FORMATS, v, toy_layout(v))
    monkeypatch.setattr(migrations, "STEPS", (step,))
    monkeypatch.setattr(Memo, "ROWS", rows)
    for rid in ("r1", "r2", "r3"):
        _seed(tmp_path, "8.0.0", rid)
    out = migrate(SqliteStore(tmp_path), None, to="8.1.0", choices=NONE)
    assert out.moved == ("r1", "r2", "r3")
    assert step._calls == (3 if rows else 6)


def test_the_code_that_migrates_is_named_before_any_run_is_sealed(
    toy, monkeypatch, tmp_path
):
    """An uninstalled runstate names no version, so the invocation is refused, before
    the detection pass and any seal (final review M2)."""
    from importlib import metadata

    def missing(name):
        raise metadata.PackageNotFoundError(name)

    migrations._runstate.cache_clear()
    monkeypatch.setattr(migrations.metadata, "distribution", missing)
    old = _seed(tmp_path, "8.0.0", "r1")
    try:
        with pytest.raises(MigrationError, match="runstate is not installed"):
            migrate(SqliteStore(tmp_path), ["r1"], to="8.2.0", choices=NONE)
    finally:
        migrations._runstate.cache_clear()
    assert os.stat(old).st_mode & stat.S_IWUSR  # not sealed


def test_one_memo_serves_every_step_of_an_invocation(toy, monkeypatch, tmp_path):
    """The memo's bound is the invocation's: one memo, shared by every step's plan,
    keyed by the step too, so two steps handed the same rows under the same history
    never read each other's transform."""
    made = []

    class Counted(Memo):
        def __init__(self):
            super().__init__()
            made.append(self)

    monkeypatch.setattr(migrations, "Memo", Counted)
    _seed(tmp_path, "8.0.0", "r1")
    assert migrate(SqliteStore(tmp_path), ["r1"], to="8.2.0", choices=NONE).moved == (
        "r1",
    )
    assert len(made) == 1
    rows = [Row(1, "value", "x", None, "{}", 0.0)]
    first, second = toy
    assert Memo.key(first, rows, NO_HISTORY) != Memo.key(second, rows, NO_HISTORY)
    assert Memo.key(first, rows, NO_HISTORY) == Memo.key(first, list(rows), NO_HISTORY)


# ----- a destination takes its format's tables (log-formats.md §3) -----------------

# An index only the patched 0.3.0 states, so the destination can be told apart from
# one created with the live channel's tables, which are 0.2.0's today.
_MARK_SQLITE = "CREATE INDEX IF NOT EXISTS idx_mark ON log (created_at);"
_MARK_PG = "CREATE INDEX IF NOT EXISTS idx_mark ON log (created_at)"


def _sqlite_catalog(path):
    import sqlite3

    conn = sqlite3.connect(path)
    try:
        return sorted(
            conn.execute(
                "SELECT type, name, tbl_name, sql FROM sqlite_master"
                " WHERE name NOT LIKE 'sqlite_%'"
            ).fetchall()
        )
    finally:
        conn.close()


def _pg_catalog(dsn, schema):
    """The schema's columns and its indexes, the schema's name taken out of each
    index's definition so two schemas compare."""
    import psycopg

    with psycopg.connect(dsn) as c:
        cols = c.execute(
            "SELECT table_name, column_name, data_type, is_nullable, ordinal_position"
            " FROM information_schema.columns WHERE table_schema = %s ORDER BY 1, 5",
            [schema],
        ).fetchall()
        idx = c.execute(
            "SELECT indexname, replace(indexdef, %s, '') FROM pg_indexes"
            " WHERE schemaname = %s ORDER BY 1",
            [f"{schema}.", schema],
        ).fetchall()
    return cols, idx


def _pg_reference(dsn, ddl):
    """The catalog the statements make, in a scratch schema of their own."""
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute("CREATE SCHEMA ddl_reference")
        with c.transaction():
            c.execute("SET LOCAL search_path TO ddl_reference")
            for stmt in ddl:
                c.execute(stmt)
    try:
        return _pg_catalog(dsn, "ddl_reference")
    finally:
        with psycopg.connect(dsn, autocommit=True) as c:
            c.execute("DROP SCHEMA ddl_reference CASCADE")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
def test_a_destination_takes_its_formats_ddl(backend, request, tmp_path, monkeypatch):
    """Open question 9, where the destination is the current format: a 0.2.0 ->
    0.3.0 migration's destination has exactly the tables and indexes of
    FORMATS["0.3.0"]'s DDL for its backend, and a channel's birth creates
    FORMATS[LOG_FORMAT]'s. The patched 0.3.0 states one index more than 0.2.0, so a
    writer that took 0.2.0's tables, or tables of its own, fails. 0.3.0 is both the
    step's TO and LOG_FORMAT here, so this case cannot tell a destination that takes
    FORMATS[LOG_FORMAT]'s tables from one that takes its own: the next test does."""
    import dataclasses
    import sqlite3

    from runstate import attach_channel, create_channel

    if backend == "postgres":
        dsn = request.getfixturevalue("scratch_db")  # SKIPs without a DSN
    v020 = formats.FORMATS["0.2.0"]
    v030 = dataclasses.replace(
        formats.FORMATS["0.3.0"],
        sqlite_ddl=v020.sqlite_ddl + _MARK_SQLITE,
        pg_ddl=v020.pg_ddl + (_MARK_PG,),
    )
    monkeypatch.setitem(formats.FORMATS, "0.3.0", v030)
    assert formats.LOG_FORMAT == "0.3.0"
    nothing = parse_choices(
        migrations.STEPS, assume=[], resolve=[], accept=[], limits=ONE
    )
    rows = [(i, "value", "x", None, json.dumps({"i": i}), float(i)) for i in (1, 2, 3)]

    if backend == "sqlite":
        src = v020.sqlite_path(tmp_path, "r1")
        src.parent.mkdir()
        conn = sqlite3.connect(src)
        conn.executescript(v020.sqlite_ddl)
        conn.executemany("INSERT INTO log VALUES (?, ?, ?, ?, ?, ?)", rows)
        conn.commit()
        conn.close()
        reference = tmp_path / "reference.db"
        conn = sqlite3.connect(reference)
        conn.executescript(v030.sqlite_ddl)
        conn.close()

        assert migrate(SqliteStore(tmp_path), None, to="0.3.0", choices=nothing).moved
        assert _sqlite_catalog(v030.sqlite_path(tmp_path, "r1")) == _sqlite_catalog(
            reference
        )
        create_channel("r2", root=tmp_path).close()
        assert _sqlite_catalog(v030.sqlite_path(tmp_path, "r2")) == _sqlite_catalog(
            reference
        )
        return

    import psycopg

    from runstate.channel.postgres import ensure_schema

    reference = _pg_reference(dsn, v030.pg_ddl)
    ensure_schema(dsn)  # the birth of every channel on the database
    assert _pg_catalog(dsn, v030.pg_schema()) == reference
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute(f"DROP SCHEMA {v030.pg_schema()} CASCADE")
        c.execute(f"CREATE SCHEMA {v020.pg_schema()}")
        with c.transaction():
            c.execute(f"SET LOCAL search_path TO {v020.pg_schema()}")
            for stmt in v020.pg_ddl:
                c.execute(stmt)
            for row in rows:
                c.execute("INSERT INTO log VALUES ('r1', %s, %s, %s, %s, %s, %s)", row)
    assert migrate(PostgresStore(dsn), None, to="0.3.0", choices=nothing).moved
    assert _pg_catalog(dsn, v030.pg_schema()) == reference
    with attach_channel("r1", root=dsn, backend="postgres") as ch:
        assert ch.last_seq() == 3


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
def test_a_destination_of_an_older_format_takes_its_own_ddl(
    backend, request, tmp_path, monkeypatch
):
    """Open question 9's mistake itself: a migration whose destination is NOT the
    current format must still take the destination's tables, never LOG_FORMAT's,
    since a change to the current format's table (the session stamp) would otherwise
    reach an older format's destination. A toy 8.0.0 -> 8.1.0 step, LOG_FORMAT still
    0.3.0, and 8.1.0 states a marker index no other format has."""
    import dataclasses
    import sqlite3

    to = dataclasses.replace(
        toy_layout("8.1.0"),
        sqlite_ddl=toy_layout("8.1.0").sqlite_ddl + _MARK_SQLITE,
        pg_ddl=toy_layout("8.1.0").pg_ddl + (_MARK_PG,),
    )
    monkeypatch.setitem(formats.FORMATS, "8.0.0", toy_layout("8.0.0"))
    monkeypatch.setitem(formats.FORMATS, "8.1.0", to)
    monkeypatch.setattr(migrations, "STEPS", (Tag("8.0.0", "8.1.0"),))
    assert formats.LOG_FORMAT != "8.1.0"
    rows = [(i, "value", "x", None, json.dumps({"i": i}), float(i)) for i in (1, 2, 3)]

    if backend == "sqlite":
        src = toy_layout("8.0.0").sqlite_path(tmp_path, "r1")
        src.parent.mkdir()
        conn = sqlite3.connect(src)
        conn.executescript(toy_layout("8.0.0").sqlite_ddl)
        conn.executemany("INSERT INTO log VALUES (?, ?, ?, ?, ?, ?)", rows)
        conn.commit()
        conn.close()
        reference = tmp_path / "reference.db"
        conn = sqlite3.connect(reference)
        conn.executescript(to.sqlite_ddl)
        conn.close()
        assert migrate(SqliteStore(tmp_path), None, to="8.1.0", choices=NONE).moved
        dst = to.sqlite_path(tmp_path, "r1")
        assert _sqlite_catalog(dst) == _sqlite_catalog(reference)
        assert ("index", "idx_mark") in {r[:2] for r in _sqlite_catalog(dst)}
        return

    import psycopg

    dsn = request.getfixturevalue("scratch_db")  # SKIPs without a DSN
    reference = _pg_reference(dsn, to.pg_ddl)
    old = toy_layout("8.0.0").pg_schema()
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute(f"CREATE SCHEMA {old}")
        with c.transaction():
            c.execute(f"SET LOCAL search_path TO {old}")
            for stmt in toy_layout("8.0.0").pg_ddl:
                c.execute(stmt)
            for row in rows:
                c.execute("INSERT INTO log VALUES ('r1', %s, %s, %s, %s, %s, %s)", row)
    assert migrate(PostgresStore(dsn), None, to="8.1.0", choices=NONE).moved
    got = _pg_catalog(dsn, to.pg_schema())
    assert got == reference
    assert "idx_mark" in {name for name, _ in got[1]}
