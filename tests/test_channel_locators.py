"""Pins for a place's attach/create split and ``current_channel``
(docs/specs/channel-locators.md).

``Logs.create`` births (open-or-create); ``Logs.attach`` opens an EXISTING run
and raises ``RunNotFound`` on a run with no records, mutating no backing store;
``current_channel`` is the worker's ambient ``create``, on the place
``from_env`` reads back from the ``RUNSTATE_*`` env. These pin the opens
directly -- the conformance suite drives the substrate behaviour through
``create``.
"""

import hashlib
import os
import sqlite3
import uuid
from types import SimpleNamespace

import pytest

from runstate import (
    LOG_FORMAT,
    RunNotFound,
    current_channel,
)
from runstate.formats import FORMATS
from runstate.logs import MemoryLogs, PostgresLogs, SqliteLogs, from_env


@pytest.fixture(params=["memory", "sqlite", "postgres"])
def loc(request, tmp_path):
    """``(backend, logs, run_id)`` for a fresh run per test. memory isolates by
    its own instance and sqlite on ``tmp_path``; postgres runs against
    ``RUNSTATE_TEST_PG_DSN`` with the shared table ensured, or SKIPs, minting a
    uuid run_id for isolation on the one shared ``log`` table."""
    backend = request.param
    if backend == "postgres":
        dsn = os.environ.get("RUNSTATE_TEST_PG_DSN")
        if not dsn:
            pytest.skip(
                "RUNSTATE_TEST_PG_DSN unset; skipping the postgres locator tests"
            )
        from runstate.channel.postgres import ensure_schema

        ensure_schema(dsn)
        logs = PostgresLogs(dsn)
    elif backend == "sqlite":
        logs = SqliteLogs(tmp_path)
    else:
        logs = MemoryLogs()
    return SimpleNamespace(backend=backend, logs=logs, run_id=f"loc-{uuid.uuid4().hex}")


def test_attach_missing_run_raises(loc):
    """A run that was never born has no records -> RunNotFound (never a phantom)."""
    with pytest.raises(RunNotFound):
        loc.logs.attach(loc.run_id)


def test_attach_empty_run_raises(loc):
    """A created-but-empty run is still "no records" -> RunNotFound (sqlite/memory
    conform to the postgres semantic: a run exists iff last_seq() > 0)."""
    loc.logs.create(loc.run_id).close()
    with pytest.raises(RunNotFound):
        loc.logs.attach(loc.run_id)


def test_attach_is_writable(loc):
    """An attached handle READS AND WRITES: birth a run, then attach a *fresh*
    handle and land a control.stop through it (the stop path)."""
    born = loc.logs.create(loc.run_id)
    born.send({"handle": "local://h/1"}, topic="lifecycle.started")
    born.close()

    ch = loc.logs.attach(loc.run_id)
    seq = ch.send({"reason": "diverged"}, topic="control.stop")
    assert seq == 2
    landed = ch.read(topics=["control.stop"])
    assert [e.body for e in landed] == [{"reason": "diverged"}]
    ch.close()


def test_create_then_attach_reads_same_last_seq(loc):
    """create births + writes; attach returns a readable handle on
    the same run whose last_seq() matches."""
    born = loc.logs.create(loc.run_id)
    born.send({"v": 1}, topic="value", name="loss")
    born.send({"v": 2}, topic="value", name="loss")
    expected = born.last_seq()
    born.close()

    ch = loc.logs.attach(loc.run_id)
    assert ch.last_seq() == expected == 2
    ch.close()


def test_current_channel_reads_env_and_births(loc, monkeypatch):
    """current_channel() reads RUNSTATE_RUN_ID and the place's own child_env(),
    and births the run (the place's create), so attach then finds it."""
    if loc.backend == "memory":
        pytest.skip("MemoryLogs is no ProcessLogs: it has no child_env")
    monkeypatch.setenv("RUNSTATE_RUN_ID", loc.run_id)
    for k, v in loc.logs.child_env().items():
        monkeypatch.setenv(k, v)

    ch = current_channel()
    assert ch.send({"v": 1}, topic="value", name="loss") == 1
    ch.close()

    attached = loc.logs.attach(loc.run_id)
    assert attached.last_seq() == 1
    attached.close()


def test_current_channel_has_no_default_place(tmp_path, monkeypatch):
    """Every variable is required: a process launched without the backend, or
    without the root, raises rather than opening a default place."""
    monkeypatch.setenv("RUNSTATE_RUN_ID", "r")
    monkeypatch.delenv("RUNSTATE_CHANNEL_BACKEND", raising=False)
    monkeypatch.setenv("RUNSTATE_CHANNEL_ROOT", str(tmp_path))
    with pytest.raises(KeyError, match="RUNSTATE_CHANNEL_BACKEND"):
        current_channel()
    monkeypatch.setenv("RUNSTATE_CHANNEL_BACKEND", "sqlite")
    monkeypatch.delenv("RUNSTATE_CHANNEL_ROOT")
    with pytest.raises(KeyError, match="RUNSTATE_CHANNEL_ROOT"):
        current_channel()


def test_from_env_inverts_child_env(tmp_path):
    """The two reference encodings round-trip, and a json_default given at the
    decode reaches the place."""
    for logs in (SqliteLogs(tmp_path), PostgresLogs("postgresql://x")):
        back = from_env(logs.child_env())
        assert type(back) is type(logs)
        assert back.child_env() == logs.child_env()


@pytest.mark.parametrize("backend", ["memory", "nope", ""])
def test_from_env_refuses_a_backend_no_process_can_open(backend, tmp_path):
    env = {"RUNSTATE_CHANNEL_BACKEND": backend, "RUNSTATE_CHANNEL_ROOT": str(tmp_path)}
    with pytest.raises(ValueError, match="RUNSTATE_CHANNEL_BACKEND"):
        from_env(env)


def test_attach_leaves_foreign_sqlite_byte_identical(tmp_path):
    """The PR #14 harm, pinned (sqlite): attaching where a stale pointer resolves
    to a FOREIGN valid sqlite db must not create, schema-mutate, or WAL-sidecar
    it. The file stays byte-identical and no -wal/-shm appears."""
    foreign = FORMATS[LOG_FORMAT].sqlite_path(tmp_path, "ghost")
    foreign.parent.mkdir()
    conn = sqlite3.connect(str(foreign))
    conn.execute("CREATE TABLE unrelated (x INTEGER)")
    conn.execute("INSERT INTO unrelated VALUES (1)")
    conn.commit()
    conn.close()
    before = hashlib.sha256(foreign.read_bytes()).digest()

    with pytest.raises(RunNotFound):
        SqliteLogs(str(tmp_path)).attach("ghost")

    assert hashlib.sha256(foreign.read_bytes()).digest() == before
    assert not foreign.with_name("ghost.db-wal").exists()
    assert not foreign.with_name("ghost.db-shm").exists()


def test_attach_corrupt_db_propagates_not_runnotfound(tmp_path):
    """A genuine non-sqlite / corrupt file is NOT a lookup miss: DatabaseError
    propagates rather than being mapped to RunNotFound (only OperationalError --
    missing file / no 'log' table -- is the miss)."""
    junk = FORMATS[LOG_FORMAT].sqlite_path(tmp_path, "junk")
    junk.parent.mkdir()
    junk.write_bytes(b"this is definitely not a sqlite database")
    with pytest.raises(sqlite3.DatabaseError):
        SqliteLogs(str(tmp_path)).attach("junk")


@pytest.mark.parametrize("run_id", ["q?mark", "hash#tag", "pct%20cent"])
def test_attach_roundtrips_uri_reserved_chars(tmp_path, run_id):
    """sqlite attach opens via a ``file:...?mode=rw`` URI, so a run_id (or root)
    containing a URI-reserved char (``?``/``#``/``%``) must be percent-encoded to
    a literal filename char -- else attach mis-parses a run create() birthed fine
    and falsely reports RunNotFound. Round-trip: create then attach must agree."""
    born = SqliteLogs(str(tmp_path)).create(run_id)
    born.send({"v": 1}, topic="value", name="loss")
    born.close()

    ch = SqliteLogs(str(tmp_path)).attach(run_id)
    assert ch.last_seq() == 1
    ch.close()


# ----- sharing and isolation within a place, and between places -----


def _place(backend, tmp_path):
    return MemoryLogs() if backend == "memory" else SqliteLogs(tmp_path)


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_create_shares_run_by_id(backend, tmp_path):
    """Repeated creates of one run id in one place share the run's log, so an
    orchestrator and a worker name the run the same way."""
    logs = _place(backend, tmp_path)
    a = logs.create("run-1")
    b = logs.create("run-1")
    a.send({"v": 1}, topic="value", name="loss")
    assert [e.body for e in b.read()] == [{"v": 1}]


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_create_distinct_runs_are_isolated(backend, tmp_path):
    logs = _place(backend, tmp_path)
    a = logs.create("run-a")
    b = logs.create("run-b")
    a.send({"v": 1}, topic="value", name="loss")
    assert b.read() == []


def test_memory_places_are_isolated():
    """A MemoryLogs is a place of its own: one run id in two instances is two
    logs, and attach finds only its own instance's."""
    one, other = MemoryLogs(), MemoryLogs()
    one.create("r").send({"v": 1}, topic="value", name="x")
    assert other.create("r").read() == []
    assert one.attach("r").last_seq() == 1
    with pytest.raises(RunNotFound):
        MemoryLogs().attach("r")


def test_sqlite_root_spellings_of_one_path_share(tmp_path):
    a = SqliteLogs(str(tmp_path)).create("reg-path")
    b = SqliteLogs(str(tmp_path) + "/.").create("reg-path")
    a.send({"v": 1}, topic="value", name="x")
    assert [e.body for e in b.read()] == [{"v": 1}]


# ----- a reader: the observer's open (#74) -----


def test_reader_missing_or_empty_run_raises(loc):
    with pytest.raises(RunNotFound):
        loc.logs.reader(loc.run_id)
    loc.logs.create(loc.run_id).close()
    with pytest.raises(RunNotFound):
        loc.logs.reader(loc.run_id)


def test_reader_reads_what_attach_reads(loc):
    with loc.logs.create(loc.run_id) as born:
        born.send({"v": 1}, topic="value", name="loss")
        born.send({"reason": "x"}, topic="control.stop", request_id="s")
    with loc.logs.reader(loc.run_id) as r, loc.logs.attach(loc.run_id) as ch:
        assert r.last_seq() == ch.last_seq() == 2
        assert r.read() == ch.read()
        assert r.read(1, topics=["control.>"]) == ch.read(1, topics=["control.>"])
        assert r.latest("value", "loss") == ch.latest("value", "loss")


def _snapshot(tmp_path, rid="snap", *, commits=1):
    """A sealed snapshot of a finished run, as a copy writes one, and its place."""
    from runstate import NoSave, Worker, copy_run

    live, snaps = tmp_path / "live", tmp_path / "snaps"
    live.mkdir(parents=True), snaps.mkdir()
    with SqliteLogs(live).create(rid) as ch:
        with Worker(ch, resume_from=None, sources=[], save=NoSave()) as w:
            for _ in range(commits):
                w.emit("loss", 0.5)
                w.commit()
            w.stopped(completed=True, error=None)
    copy_run(rid, source=SqliteLogs(live), destination=SqliteLogs(snaps))
    return SqliteLogs(snaps), FORMATS[LOG_FORMAT].sqlite_path(snaps, rid)


def test_a_sealed_snapshot_is_read_immutable_and_cannot_send(tmp_path):
    """The reader of a sealed snapshot is a ``SqliteSnapshot``: it has no ``send``,
    its reads equal the channel's, and it leaves the file and its directory as they
    were (no sidecar, no byte changed)."""
    from runstate.channel import SqliteSnapshot

    logs, path = _snapshot(tmp_path)
    before, listing = path.read_bytes(), sorted(p.name for p in path.parent.iterdir())
    with logs.reader("snap") as r, logs.attach("snap") as ch:
        assert type(r) is SqliteSnapshot
        assert not hasattr(r, "send")
        assert r.read() == ch.read() and r.last_seq() == ch.last_seq()
    assert path.read_bytes() == before
    assert sorted(p.name for p in path.parent.iterdir()) == listing


def test_the_folds_read_a_snapshot_through_its_reader(tmp_path):
    from runstate import LatestClaimHead, Outcome, peek_terminal, series

    logs, _ = _snapshot(tmp_path)
    with logs.reader("snap") as r:
        result = peek_terminal(r)
        assert result is not None and result.outcomes == {Outcome.COMPLETED}
        assert [e.value for e in series(r, "loss", head=LatestClaimHead())] == [0.5]


def test_a_snapshot_reader_takes_no_lock(tmp_path):
    """Pinned by what a lock would do: while another connection holds the file
    locked exclusively, a read-only open that locks cannot read it, and the
    reader can."""
    import sqlite3
    from urllib.request import pathname2url

    logs, path = _snapshot(tmp_path)
    os.chmod(path, 0o644)  # to take the lock; the bits are cleared again below
    holder = sqlite3.connect(path, isolation_level=None)
    holder.execute("BEGIN EXCLUSIVE")
    os.chmod(path, 0o444)
    try:
        locking = sqlite3.connect(
            f"file:{pathname2url(str(path))}?mode=ro", uri=True, timeout=0
        )
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            locking.execute("SELECT count(*) FROM log").fetchone()
        locking.close()
        with logs.reader("snap") as r:
            assert r.last_seq() > 0
    finally:
        holder.close()


def test_an_unsealed_log_is_read_through_a_channel(tmp_path):
    """Only a read-write open rolls back a hot journal, so a log still written is
    never opened immutable: its reader is the channel, under the narrow type."""
    from runstate.channel import SqliteChannel

    logs = SqliteLogs(tmp_path)
    logs.create("live").send({"v": 1}, topic="value", name="x")
    with logs.reader("live") as r:
        assert type(r) is SqliteChannel


@pytest.mark.parametrize("sidecar", ["-wal", "-journal"])
def test_a_sealed_file_beside_a_sidecar_is_not_read_immutable(tmp_path, sidecar):
    """Write bits alone are not the seal: a file with a journal beside it may hold
    records an immutable open would not see."""
    from runstate.channel import SqliteChannel

    logs, path = _snapshot(tmp_path)
    path.with_name(path.name + sidecar).write_bytes(b"")
    with logs.reader("snap") as r:
        assert type(r) is SqliteChannel


def test_a_reader_outlives_its_snapshot_being_replaced(tmp_path):
    """A snapshot is replaced by a rename (run-copy.md), so a reader already open
    keeps the file it opened and a new reader sees the new one."""
    logs, path = _snapshot(tmp_path)
    old = logs.reader("snap")
    seen = old.read()
    _, longer = _snapshot(tmp_path / "second", rid="snap", commits=3)
    os.replace(longer, path)
    assert old.read() == seen
    old.close()
    with logs.reader("snap") as new:
        assert new.last_seq() > len(seen)


def test_reader_takes_the_opening_checks(tmp_path):
    """A run held in another format raises, as attach does, and is not read."""
    from runstate import LogFormatMismatch

    old = FORMATS["0.3.0"].sqlite_path(tmp_path, "r1")
    old.parent.mkdir()
    old.write_bytes(b"")
    with pytest.raises(LogFormatMismatch):
        SqliteLogs(tmp_path).reader("r1")


def test_a_foreign_sealed_sqlite_file_is_run_not_found(tmp_path):
    import sqlite3

    path = FORMATS[LOG_FORMAT].sqlite_path(tmp_path, "junk")
    path.parent.mkdir()
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE other (x)")
    con.commit()
    con.close()
    os.chmod(path, 0o444)
    with pytest.raises(RunNotFound):
        SqliteLogs(tmp_path).reader("junk")
