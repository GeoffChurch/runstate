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
