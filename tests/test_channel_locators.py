"""Pins for the attach/create/current locator split (docs/specs/channel-locators.md).

``create_channel`` births (open-or-create); ``attach_channel`` opens an EXISTING
run and raises ``RunNotFound`` on a run with no records, mutating no backing
store; ``current_channel`` is the worker's ambient ``create_channel`` off the
``RUNSTATE_*`` env. These pin the new surface directly -- the migrated
conformance suite (Task 2) still drives the substrate behaviour under
``create_channel``.
"""

import hashlib
import os
import sqlite3
import uuid
from types import SimpleNamespace

import pytest

from runstate import RunNotFound, attach_channel, create_channel, current_channel


@pytest.fixture(params=["memory", "sqlite", "postgres"])
def loc(request, tmp_path):
    """``(backend, root, run_id)`` for a fresh run per test. memory/sqlite isolate
    on ``tmp_path`` (+ the autouse registry reset); postgres runs against
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
        root = dsn
    else:
        root = str(tmp_path)
    return SimpleNamespace(backend=backend, root=root, run_id=f"loc-{uuid.uuid4().hex}")


def test_attach_missing_run_raises(loc):
    """A run that was never born has no records -> RunNotFound (never a phantom)."""
    with pytest.raises(RunNotFound):
        attach_channel(loc.run_id, root=loc.root, backend=loc.backend)


def test_attach_empty_run_raises(loc):
    """A created-but-empty run is still "no records" -> RunNotFound (sqlite/memory
    conform to the postgres semantic: a run exists iff last_seq() > 0)."""
    create_channel(loc.run_id, root=loc.root, backend=loc.backend).close()
    with pytest.raises(RunNotFound):
        attach_channel(loc.run_id, root=loc.root, backend=loc.backend)


def test_attach_is_writable(loc):
    """An attached handle READS AND WRITES: birth a run, then attach a *fresh*
    handle and land a control.stop through it (the stop path)."""
    born = create_channel(loc.run_id, root=loc.root, backend=loc.backend)
    born.send({"handle": "local://h/1"}, topic="lifecycle.started")
    born.close()

    ch = attach_channel(loc.run_id, root=loc.root, backend=loc.backend)
    seq = ch.send({"reason": "diverged"}, topic="control.stop")
    assert seq == 2
    landed = ch.read(topics=["control.stop"])
    assert [e.body for e in landed] == [{"reason": "diverged"}]
    ch.close()


def test_create_then_attach_reads_same_last_seq(loc):
    """create_channel births + writes; attach_channel returns a readable handle on
    the same run whose last_seq() matches."""
    born = create_channel(loc.run_id, root=loc.root, backend=loc.backend)
    born.send({"v": 1}, topic="value", name="loss")
    born.send({"v": 2}, topic="value", name="loss")
    expected = born.last_seq()
    born.close()

    ch = attach_channel(loc.run_id, root=loc.root, backend=loc.backend)
    assert ch.last_seq() == expected == 2
    ch.close()


def test_current_channel_reads_env_and_births(loc, monkeypatch):
    """current_channel() reads RUNSTATE_RUN_ID / _CHANNEL_ROOT / _CHANNEL_BACKEND
    and births the run (delegates to create_channel), so attach then finds it."""
    monkeypatch.setenv("RUNSTATE_RUN_ID", loc.run_id)
    monkeypatch.setenv("RUNSTATE_CHANNEL_ROOT", loc.root)
    monkeypatch.setenv("RUNSTATE_CHANNEL_BACKEND", loc.backend)

    ch = current_channel()
    assert ch.send({"v": 1}, topic="value", name="loss") == 1
    ch.close()

    attached = attach_channel(loc.run_id, root=loc.root, backend=loc.backend)
    assert attached.last_seq() == 1
    attached.close()


def test_attach_leaves_foreign_sqlite_byte_identical(tmp_path):
    """The PR #14 harm, pinned (sqlite): attaching where a stale pointer resolves
    to a FOREIGN valid sqlite db must not create, schema-mutate, or WAL-sidecar
    it. The file stays byte-identical and no -wal/-shm appears."""
    foreign = (
        tmp_path / "ghost.db"
    )  # attach("ghost", root=tmp_path) -> tmp_path/ghost.db
    conn = sqlite3.connect(str(foreign))
    conn.execute("CREATE TABLE unrelated (x INTEGER)")
    conn.execute("INSERT INTO unrelated VALUES (1)")
    conn.commit()
    conn.close()
    before = hashlib.sha256(foreign.read_bytes()).digest()

    with pytest.raises(RunNotFound):
        attach_channel("ghost", root=str(tmp_path), backend="sqlite")

    assert hashlib.sha256(foreign.read_bytes()).digest() == before
    assert not (tmp_path / "ghost.db-wal").exists()
    assert not (tmp_path / "ghost.db-shm").exists()


def test_attach_corrupt_db_propagates_not_runnotfound(tmp_path):
    """A genuine non-sqlite / corrupt file is NOT a lookup miss: DatabaseError
    propagates rather than being mapped to RunNotFound (only OperationalError --
    missing file / no 'log' table -- is the miss)."""
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"this is definitely not a sqlite database")
    with pytest.raises(sqlite3.DatabaseError):
        attach_channel("junk", root=str(tmp_path), backend="sqlite")


@pytest.mark.parametrize("run_id", ["q?mark", "hash#tag", "pct%20cent"])
def test_attach_roundtrips_uri_reserved_chars(tmp_path, run_id):
    """sqlite attach opens via a ``file:...?mode=rw`` URI, so a run_id (or root)
    containing a URI-reserved char (``?``/``#``/``%``) must be percent-encoded to
    a literal filename char -- else attach mis-parses a run create() birthed fine
    and falsely reports RunNotFound. Round-trip: create then attach must agree."""
    born = create_channel(run_id, root=str(tmp_path), backend="sqlite")
    born.send({"v": 1}, topic="value", name="loss")
    born.close()

    ch = attach_channel(run_id, root=str(tmp_path), backend="sqlite")
    assert ch.last_seq() == 1
    ch.close()


# ----- create_channel sharing / isolation / dispatch (migrated from the old
# locator test file: the registry-co-location + backend-dispatch ladder) -----


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_create_channel_shares_run_by_id(backend, tmp_path):
    """Repeated create_channel on the same (root, run_id) share the run's log, so
    an orchestrator and a worker name the run the same way."""
    a = create_channel("run-1", root=tmp_path, backend=backend)
    b = create_channel("run-1", root=tmp_path, backend=backend)
    a.send({"v": 1}, topic="value", name="loss")
    assert [e.body for e in b.read()] == [{"v": 1}]


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_create_channel_distinct_runs_are_isolated(backend, tmp_path):
    a = create_channel("run-a", root=tmp_path, backend=backend)
    b = create_channel("run-b", root=tmp_path, backend=backend)
    a.send({"v": 1}, topic="value", name="loss")
    assert b.read() == []


def test_create_channel_unknown_backend_raises(tmp_path):
    with pytest.raises(ValueError):
        create_channel("r", root=tmp_path, backend="nope")


def test_memory_root_none_is_not_the_string_none():
    # None is a registry sentinel, never str()'d into the namespace "None"
    a = create_channel("reg-none", backend="memory")
    b = create_channel("reg-none", root="None", backend="memory")
    a.send({"v": 1}, topic="value", name="x")
    assert b.read() == []


def test_memory_root_spellings_of_one_path_share(tmp_path):
    # memory mirrors sqlite's identity: two spellings of one location = one log
    a = create_channel("reg-path", root=str(tmp_path), backend="memory")
    b = create_channel("reg-path", root=str(tmp_path) + "/.", backend="memory")
    a.send({"v": 1}, topic="value", name="x")
    assert [e.body for e in b.read()] == [{"v": 1}]
