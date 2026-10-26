"""log-formats.md §6 with test-only formats and steps."""

import json
import os
import re
import stat
from typing import NamedTuple

import pytest

from runstate import formats, migrations
from runstate.channel.sqlite import SqliteChannel
from runstate.formats import DirectoryLayout
from runstate.migrations import MigrationError, Planned, Row, chain, migrate
from runstate.migrations.ambiguity import (
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
from tests.migration_consumer import ASKED, UNSETTLED, ToyFacts

# The toy steps declare no facts, projections or divergences: the user has nothing
# to give them. The cap is the command line's, which no toy reads.
NONE = parse_choices([], assume=[], resolve=[], accept=[], cap=1)


class Tag:
    """A toy step: stamps {"tag": TO} into every body. ``boom_at`` is the
    0-based ``transform`` call that raises. Call 0 is the detection pass's dry
    run; on sqlite, call 1 is the dry run before the seal and call 2 the
    transform after it; postgres makes one more, inside its transaction."""

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
    return Planned(step, resolutions(step, NONE), frozenset())


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
    assert migrate(SqliteStore(tmp_path), None, to="8.2.0", choices=NONE).moved == (
        "r1",
    )
    new = DirectoryLayout("8.2.0").sqlite_path(tmp_path, "r1")
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
    assert DirectoryLayout("8.1.0").sqlite_path(tmp_path, rid).exists()


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
            migrate(SqliteStore(tmp_path), ["r1"], to="8.1.0", choices=NONE)
        assert os.stat(old).st_mode & 0o200  # not sealed
        assert old.read_bytes() == before
        assert not DirectoryLayout("8.1.0").sqlite_path(tmp_path, "r1").exists()


def test_a_failure_after_the_seal_leaves_it_sealed_and_a_retry_completes(
    toy, monkeypatch, tmp_path
):
    old = _seed(tmp_path, "8.0.0", "r1")
    monkeypatch.setattr(migrations, "STEPS", (Tag("8.0.0", "8.1.0", boom_at=2),))
    with pytest.raises(RuntimeError, match="boom"):
        migrate(SqliteStore(tmp_path), ["r1"], to="8.1.0", choices=NONE)
    target = DirectoryLayout("8.1.0").sqlite_path(tmp_path, "r1")
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
    target = DirectoryLayout("8.1.0").sqlite_path(tmp_path, "r1")
    monkeypatch.setattr(migrations, "STEPS", (Tag("8.0.0", "8.1.0"),))
    real = os.replace

    def boom(*a, **k):
        raise OSError("disk gone")

    monkeypatch.setattr(stores.os, "replace", boom)
    with pytest.raises(OSError, match="disk gone"):
        migrate(SqliteStore(tmp_path), ["r1"], to="8.1.0", choices=NONE)
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
    target = DirectoryLayout("8.1.0").sqlite_path(tmp_path, "r1")
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
    path = DirectoryLayout("8.0.0").sqlite_path(tmp_path, "r1")
    path.parent.mkdir()
    crashed_wal_writer(path, 50)
    migrate(SqliteStore(tmp_path), ["r1"], to="8.1.0", choices=NONE)
    new = DirectoryLayout("8.1.0").sqlite_path(tmp_path, "r1")
    assert [b["i"] for b in _bodies(new)] == list(range(50))


def test_a_hot_journal_of_a_crashed_writer_is_rolled_back(
    toy, tmp_path, crashed_journal_writer
):
    """I4: the pre-seal read opens read-write, so it rolls back a hot journal
    that a read-only open cannot, and the copy holds only committed records."""
    path = DirectoryLayout("8.0.0").sqlite_path(tmp_path, "r1")
    path.parent.mkdir()
    crashed_journal_writer(path, 5)
    assert migrate(SqliteStore(tmp_path), ["r1"], to="8.1.0", choices=NONE).moved == (
        "r1",
    )
    new = DirectoryLayout("8.1.0").sqlite_path(tmp_path, "r1")
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
    monkeypatch.setattr(migrations, "STEPS", (Tag("8.0.0", "8.1.0", boom_at=0),))
    with pytest.raises(RuntimeError, match="boom"):
        migrate(PostgresStore(pg_toy), ["r1"], to="8.1.0", choices=NONE)
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
    # the store alone: the detection pass reads every run unsealed, by design
    PostgresStore(pg_toy).migrate_one(_planned(Checked("8.0.0", "8.1.0")), "r1")
    assert sealed == ["r1"]


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
        migrated = DirectoryLayout("8.1.0").sqlite_path(tmp_path, "r1")
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
    assert not DirectoryLayout("8.1.0").sqlite_path(tmp_path, "r1").exists()


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
    assert len(_bodies(DirectoryLayout("8.1.0").sqlite_path(tmp_path, "r1"))) == 5


def test_a_renumbering_steps_new_seqs_reach_the_new_sqlite_log(monkeypatch, tmp_path):
    import sqlite3

    for v in ["0.3.0", "8.1.0"]:
        monkeypatch.setitem(formats.FORMATS, v, DirectoryLayout(v))
    _seed(tmp_path, "0.3.0", "r1")
    step = Rewrite(
        "0.3.0", "8.1.0", edit=lambda r: r._replace(seq=r.seq + 10), renumbers=True
    )
    monkeypatch.setattr(migrations, "STEPS", (step,))
    assert migrate(SqliteStore(tmp_path), ["r1"], to="8.1.0", choices=NONE).moved == (
        "r1",
    )
    new = DirectoryLayout("8.1.0").sqlite_path(tmp_path, "r1")
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


def _projection(name):
    return Projection(
        name,
        f"which word a toy record of {name} carries.",
        "a word",
        (Builtin("yes", Say("yes"), "say yes"), Builtin("no", Say("no"), "say no")),
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
        monkeypatch.setitem(formats.FORMATS, v, DirectoryLayout(v))
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
        cap=given.get("cap", 7),
    )


def _three(root):
    _seed(root, "8.0.0", "r1")
    _seed(root, "8.0.0", "r2")
    _seed(root, "8.0.0", "r3", n=1)


def _untouched(root):
    """Nothing written: no run sealed, no new format, no manifest."""
    for rid in ["r1", "r2", "r3"]:
        assert os.stat(DirectoryLayout("8.0.0").sqlite_path(root, rid)).st_mode & 0o200
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
        ({"cap": 0}, "--cap 0: a step must be let hold one state at least"),
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
    assert choices.cap == 7


def test_detection_takes_no_chooser_and_finds_the_same_forks_whatever_the_choices(
    forking, tmp_path
):
    """detect is given the assumptions alone, so no chooser can move a fork."""
    _three(tmp_path)
    rows = SqliteStore(tmp_path).rows("8.0.0", "r1")
    step = forking[0]
    found = [
        step.detect(rows, assumptions=resolutions(step, c).assumptions, history=())
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
    assert "undecided until these are resolved" in text and "8.1.0 -> 8.2.0" in text


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
    final = _bodies(DirectoryLayout("8.2.0").sqlite_path(tmp_path, "r1"))
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
    assert entry["facts"] == {"settled": True}
    assert entry["readings"] == {"a": {}}


def test_a_projection_no_run_forks_needs_no_chooser(forking, tmp_path):
    _seed(tmp_path, "8.0.0", "r3", n=1)
    outcome = migrate(SqliteStore(tmp_path), None, to="8.2.0", choices=_choose())
    assert outcome.moved == ("r3",)
    assert [e["resolved"] for e in _manifest(tmp_path)] == [
        {"a": "refuse"},
        {"b": "refuse"},
    ]


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
        assert (
            os.stat(DirectoryLayout("8.0.0").sqlite_path(tmp_path, rid)).st_mode & 0o200
        )
        assert not DirectoryLayout("8.1.0").sqlite_path(tmp_path, rid).exists()
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
        (earlier,) = history
        assert (earlier.from_format, earlier.to_format) == ("8.0.0", "8.1.0")
        assert earlier.resolved == {"a": "tests.migration_consumer:ASKED"}
        assert earlier.readings == {"a": {2: "asked-2"}}
    forking[1].histories.clear()
    migrate(SqliteStore(tmp_path), ["r2"], to="8.2.0", choices=choices)
    assert {h[0].readings["a"][2] for h in forking[1].histories} == {"asked-2"}


def test_a_divergence_refuses_its_runs_until_it_is_accepted(
    forking, monkeypatch, tmp_path
):
    step = Forking("8.0.0", "8.1.0", "a", diverge=[2])
    monkeypatch.setattr(migrations, "STEPS", (step,))
    _three(tmp_path)
    outcome = migrate(SqliteStore(tmp_path), None, to="8.1.0", choices=_choose())
    assert outcome.moved == ("r3",)
    assert [r.run_id for r in outcome.refused] == ["r1", "r2"]
    assert "the divergence a-drift fires at seq 3" in outcome.refused[0].message
    assert "Add --accept a-drift" in outcome.refused[0].message
    text = render(outcome.report)
    assert "a-drift (8.0.0 -> 8.1.0): a's read drifts." in text
    assert "sites: 2 in 2 runs, e.g. r1 seq 3, r2 seq 3" in text
    assert "refused until: --accept a-drift" in text
    outcome = migrate(
        SqliteStore(tmp_path), None, to="8.1.0", choices=_choose(accept=["a-drift"])
    )
    assert outcome.moved == ("r1", "r2") and not outcome.refused
    assert {e["run_id"]: e["accepted"] for e in _manifest(tmp_path)} == {
        "r3": {},
        "r1": {"a-drift": [3]},
        "r2": {"a-drift": [3]},
    }


def test_the_manifest_holds_one_entry_per_run_per_step(forking, tmp_path):
    _three(tmp_path)
    choices = _choose(resolve=["a=yes", "b=no"], cap=11)
    migrate(SqliteStore(tmp_path), None, to="8.2.0", choices=choices)
    entries = _manifest(tmp_path)
    assert sorted((e["run_id"], e["from"], e["to"]) for e in entries) == [
        (rid, a, b)
        for rid in ["r1", "r2", "r3"]
        for a, b in [("8.0.0", "8.1.0"), ("8.1.0", "8.2.0")]
    ]
    (r1a,) = [e for e in entries if e["run_id"] == "r1" and e["from"] == "8.0.0"]
    assert r1a["cap"] == 11 and r1a["assumed"] is None
    assert r1a["facts"] == {"settled": False}
    assert r1a["resolved"] == {"a": "yes"} and r1a["readings"] == {"a": {"2": "yes"}}
    assert r1a["runstate"] and r1a["at"] > 0


def test_a_reading_refused_by_the_step_leaves_the_other_runs_migrated(
    forking, monkeypatch, tmp_path
):
    _three(tmp_path)
    step = forking[0]

    def strict(rows, *, resolutions, history):
        migrated = Forking.transform(
            step, rows, resolutions=resolutions, history=history
        )
        if set(migrated.readings["a"]) != set(
            step._forked(rows, resolutions.assumptions)
        ):
            raise MigrationError("the chooser read no fork")
        return migrated

    monkeypatch.setattr(step, "transform", strict)
    choices = _choose(resolve=["a=tests.migration_consumer:GARBLED"])
    outcome = migrate(SqliteStore(tmp_path), None, to="8.1.0", choices=choices)
    assert outcome.moved == ("r3",)
    assert [r.message for r in outcome.refused] == [
        "run 'r1': the chooser read no fork",
        "run 'r2': the chooser read no fork",
    ]


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
    assert main([*argv, "--cap", "5"]) == 1
    out = capsys.readouterr()
    assert out.out.split() == ["r3"]
    assert "refused: run 'r1'" in out.err
    assert _manifest(tmp_path)[0]["cap"] == 5
    assert main(["migrate", str(tmp_path), "--to", "8.1.0", "--resolve", "a=no"]) == 0
    assert capsys.readouterr().out.split() == ["r1", "r2"]


def test_the_cli_reports_a_divergence_it_refused(
    forking, monkeypatch, tmp_path, capsys
):
    from runstate.cli import CAP, main

    monkeypatch.setattr(
        migrations, "STEPS", (Forking("8.0.0", "8.1.0", "a", diverge=[0]),)
    )
    _seed(tmp_path, "8.0.0", "r3", n=1)
    assert main(["migrate", str(tmp_path), "--to", "8.1.0"]) == 1
    assert "refused until: --accept a-drift" in capsys.readouterr().err
    assert main(["migrate", str(tmp_path), "--to", "8.1.0", "--accept", "a-drift"]) == 0
    assert _manifest(tmp_path)[0]["cap"] == CAP


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
    with pytest.raises(RuntimeError):
        migrate(
            PostgresStore(pg_toy),
            ["r1"],
            to="8.1.0",
            choices=_choose(resolve=["a=yes"]),
        )
    assert none_yet()
    assert _pg_one(pg_toy, "SELECT to_regclass('runstate_v8_1_0.log')") == [(None,)]
    monkeypatch.setattr(stores, "_pg_manifest", real)
    outcome = migrate(
        PostgresStore(pg_toy), ["r1"], to="8.1.0", choices=_choose(resolve=["a=yes"])
    )
    assert outcome.moved == ("r1",)
    (entry,) = PostgresStore(pg_toy).history("r1")
    assert (entry.from_format, entry.resolved) == ("8.0.0", {"a": "yes"})
    assert entry.readings == {"a": {1: "yes"}}
