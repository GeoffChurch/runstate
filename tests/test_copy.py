"""Run copy (docs/specs/run-copy.md §9): one run's log copied between backends at
its format, verified by its digest, and dropped.

Every log here is written by the reference ``Worker`` through a place's
``create``, so each copied row is one production writes. The races are made deterministic with a
pause at a named point of the copy, threads and events, and a second connection
holding a lock: never a sleep that hopes."""

import os
import sqlite3
import threading
import time
import uuid
from dataclasses import asdict

import pytest

from runstate import (
    LOG_FORMAT,
    Copied,
    CopyRefused,
    LogFormatMismatch,
    NoSave,
    PostgresLogs,
    Row,
    RunLive,
    RunNotFound,
    SqliteLogs,
    Started,
    Topic,
    Value,
    Worker,
    copy_run,
    drop_run,
    log_digest,
    new_launch_id,
)
from runstate.formats import FORMATS
from runstate.vocabulary.launch import launch_scope

# Sealing is by file permissions, which root bypasses (some CI containers run as
# root): the seal still happens, only "the write is refused" cannot be observed.
_AS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0

_SCHEMA = FORMATS[LOG_FORMAT].pg_schema()


def _rid():
    return f"copy-{uuid.uuid4().hex}"


def _address(root, rid):
    return FORMATS[LOG_FORMAT].sqlite_path(root, rid)


def work(ch, *, commits):
    """One claim that commits ``commits`` iterations, each with a value under a
    non-ASCII name, and exits completed. Launched, so its claim carries a launch id
    in ``request_id``; the values carry none."""
    with launch_scope(new_launch_id()):
        with Worker(ch, resume_from=None, sources=[], save=NoSave()) as w:
            for i in range(commits):
                w.emit("λoss", i / 3)
                w.commit()
            w.stopped(completed=True, error=None)


def claim(ch):
    """A claim that stays live: a worker that has not exited."""
    return Worker(ch, resume_from=None, sources=[], save=NoSave())


def started():
    """The body a claimant's compare-and-append writes."""
    return asdict(
        Started(handle="local://elsewhere/1", t=1.0, resume_from=None, sources=[])
    )


@pytest.fixture(params=["WAL", "DELETE"])
def journal(request, monkeypatch):
    """Both journal modes a SQLite log is written in: WAL, and NFS's DELETE."""
    monkeypatch.setenv("RUNSTATE_SQLITE_JOURNAL_MODE", request.param)
    return request.param


def sqlite_run(root, rid, *, claims=1, commits=3):
    with SqliteLogs(root).create(rid) as ch:
        for _ in range(claims):
            work(ch, commits=commits)


def pg_run(dsn, rid, *, claims=1, commits=3):
    with PostgresLogs(dsn).create(rid) as ch:
        for _ in range(claims):
            work(ch, commits=commits)


def sealed(path):
    return os.stat(path).st_mode & 0o222 == 0


def temporaries(root):
    d = _address(root, "x").parent
    return (
        [p.name for p in d.iterdir() if p.name.endswith(".tmp")] if d.exists() else []
    )


def pg_rows(dsn, rid):
    """The run's rows, read directly: nothing of runstate's in the way."""
    import psycopg

    with psycopg.connect(dsn) as c:
        return [
            Row(*r)
            for r in c.execute(
                f"SELECT seq, topic, name, request_id, body, created_at"
                f" FROM {_SCHEMA}.log WHERE run_id = %s ORDER BY seq",
                [rid],
            )
        ]


def sqlite_rows(path):
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return [
            Row(*r)
            for r in c.execute(
                "SELECT seq, topic, name, request_id, body, created_at FROM log"
                " ORDER BY seq"
            )
        ]
    finally:
        c.close()


# --- the digest's fixed vectors (§4): another language checks against these ------


def test_log_digest_fixed_vectors():
    assert log_digest([]) == (
        "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )
    # NULL name and request_id, an ordinary created_at
    nulls = Row(
        1, "lifecycle.started", None, None, '{"handle":"local://h/1"}', 1730000000.25
    )
    assert log_digest([nulls]) == (
        "sha256:b9c5dd62315dd80281991d9518348a03a166d6a692914c8c13f113706ce5cc9f"
    )
    # non-ASCII name and request_id
    non_ascii = Row(7, "value", "λoss", "réq-☃", '{"value":"\\u00e9"}', 1730000000.5)
    assert log_digest([non_ascii]) == (
        "sha256:afe66c72ecda8b14d33f9d28151701a7e0000f9a4f79afbb66ebf80d64968a61"
    )
    # an empty body, and an empty name, which is not NULL
    assert log_digest([Row(3, "value", "", None, "", 1.0)]) == (
        "sha256:f715eac10ef15762d7ce1d5d2a65f735b22ee3c533a636f8f2e7972470bed17f"
    )
    # negative zero is its own bytes, not zero's
    assert log_digest([Row(1, "value", "n", None, "{}", -0.0)]) == (
        "sha256:253cec3df9935bb44cdb0414659f4d28c8f97d68490cf52a675b3223cc573c0c"
    )
    assert log_digest([Row(1, "value", "n", None, "{}", 0.0)]) == (
        "sha256:c8b9bc1b18ee5c711b713a2fc8a603a088d25201dfd19a44bef774367a7cc402"
    )
    # two rows: their encodings concatenated, in seq order
    two = [
        Row(1, "lifecycle.started", None, "L1", '{"t":0}', 1730000000.25),
        Row(2, "value", "loss", None, '{"value":0.5,"t":null}', 1730000001.125),
    ]
    assert log_digest(two) == (
        "sha256:e4deb7b5b68f32023f7ac2ebc0c6af0f510f94ee9a45b19ae0e214d91a1c5716"
    )


def test_log_digest_encodes_each_row_as_the_table_says():
    """The bytes the digest hashes for one row, spelled out field by field."""
    import hashlib

    row = Row(
        1, "lifecycle.started", None, None, '{"handle":"local://h/1"}', 1730000000.25
    )
    encoded = bytes.fromhex(
        "0000000000000001"  # seq, 8 bytes big-endian
        "00000011"
        + "lifecycle.started".encode().hex()  # topic: length, UTF-8
        + "00"  # name: NULL
        + "00"  # request_id: NULL
        + "00000018"
        + '{"handle":"local://h/1"}'.encode().hex()  # body: length, UTF-8
        + "41d9c76d20100000"  # created_at, binary64 big-endian
    )
    assert log_digest([row]) == "sha256:" + hashlib.sha256(encoded).hexdigest()
    present = Row(7, "value", "λoss", "réq-☃", "{}", 0.5)
    encoded = bytes.fromhex(
        "0000000000000007"
        "00000005"
        + b"value".hex()
        + "01"
        + "00000005"
        + "λoss".encode().hex()  # present: 0x01, length, UTF-8
        + "01"
        + "00000008"
        + "réq-☃".encode().hex()
        + "00000002"
        + b"{}".hex()
        + "3fe0000000000000"
    )
    assert log_digest([present]) == "sha256:" + hashlib.sha256(encoded).hexdigest()


def test_log_digest_takes_rows_in_seq_order():
    a = Row(1, "value", "n", None, "{}", 1.0)
    b = Row(2, "value", "n", None, "{}", 2.0)
    with pytest.raises(ValueError, match="seq order"):
        log_digest([b, a])
    with pytest.raises(ValueError, match="seq order"):
        log_digest([a, a])


# --- round trips (§9) ----------------------------------------------------------


def test_sqlite_to_postgres_to_sqlite_round_trips_byte_identically(
    tmp_path, journal, pg_ready
):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    rid = _rid()
    sqlite_run(a, rid, claims=2)
    original = SqliteLogs(a).rows(rid)
    assert {r.request_id is None for r in original} == {True, False}
    assert any(r.name == "λoss" for r in original)

    pg = PostgresLogs(pg_ready)
    up = copy_run(rid, source=SqliteLogs(a), destination=pg)
    assert pg.rows(rid) == original == pg_rows(pg_ready, rid)
    down = copy_run(rid, source=pg, destination=SqliteLogs(b))
    assert SqliteLogs(b).rows(rid) == original == sqlite_rows(_address(b, rid))
    assert up == down == Copied(log_digest(original), original[-1].seq)
    with PostgresLogs(pg_ready).attach(rid) as ch:
        assert up.last_seq == ch.last_seq()


def test_postgres_to_sqlite_round_trips_byte_identically(tmp_path, journal, pg_ready):
    rid = _rid()
    pg_run(pg_ready, rid, claims=2)
    pg = PostgresLogs(pg_ready)
    copied = copy_run(rid, source=pg, destination=SqliteLogs(tmp_path))
    rows = pg.rows(rid)
    assert sqlite_rows(_address(tmp_path, rid)) == rows == pg_rows(pg_ready, rid)
    assert copied == Copied(log_digest(rows), rows[-1].seq)


def test_rows_equal_what_the_copy_copies_on_each_backend(tmp_path, journal, pg_ready):
    rid = _rid()
    sqlite_run(tmp_path, rid)
    source = SqliteLogs(tmp_path)
    before = source.rows(rid)
    copied = copy_run(rid, source=source, destination=PostgresLogs(pg_ready))
    assert source.rows(rid) == before  # the seal changed no row
    assert PostgresLogs(pg_ready).rows(rid) == before
    assert log_digest(source.rows(rid)) == copied.digest
    assert log_digest(PostgresLogs(pg_ready).rows(rid)) == copied.digest


def test_sqlite_to_sqlite_writes_a_sealed_snapshot_and_leaves_the_source_writable(
    tmp_path, journal
):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    rid = _rid()
    sqlite_run(a, rid)
    copied = copy_run(rid, source=SqliteLogs(a), destination=SqliteLogs(b))
    assert sealed(_address(b, rid)) and not sealed(_address(a, rid))
    assert SqliteLogs(b).rows(rid) == SqliteLogs(a).rows(rid)
    assert copied.digest == log_digest(SqliteLogs(a).rows(rid))


def test_postgres_to_postgres_is_refused_before_anything_is_read(pg_ready, scratch_db):
    rid = _rid()
    pg_run(pg_ready, rid)
    with pytest.raises(ValueError, match="two"):
        copy_run(
            rid, source=PostgresLogs(pg_ready), destination=PostgresLogs(scratch_db)
        )
    with pytest.raises(RunNotFound):
        PostgresLogs(scratch_db).rows(rid)


# --- writer stamps -----------------------------------------------------------


def test_an_imported_run_carries_no_writer_stamp_and_the_probe_abstains(
    tmp_path, pg_ready
):
    import psycopg

    rid = _rid()
    sqlite_run(tmp_path, rid)
    copied = copy_run(
        rid, source=SqliteLogs(tmp_path), destination=PostgresLogs(pg_ready)
    )
    with psycopg.connect(pg_ready) as c:
        stamps = c.execute(
            f"SELECT DISTINCT writer_key, writer_server FROM {_SCHEMA}.log"
            " WHERE run_id = %s",
            [rid],
        ).fetchall()
    assert stamps == [(None, None)]
    with PostgresLogs(pg_ready).attach(rid) as ch:
        assert all(ch.writer_session(s) is None for s in range(1, copied.last_seq + 1))
        seq = ch.send(asdict(Value(value=1, t=None)), topic=Topic.VALUE, name="n")
        assert ch.writer_session(seq) is not None  # an append after it is stamped


def test_after_an_import_the_next_append_lands_at_last_seq_plus_one(tmp_path, pg_ready):
    rid = _rid()
    sqlite_run(tmp_path, rid)
    copied = copy_run(
        rid, source=SqliteLogs(tmp_path), destination=PostgresLogs(pg_ready)
    )
    with PostgresLogs(pg_ready).attach(rid) as ch:
        w = claim(ch)
        assert w.claimed
        assert ch.latest(Topic.LIFECYCLE_STARTED).seq == copied.last_seq + 1
        w.stopped(completed=True, error=None)


# --- refusals ----------------------------------------------------------------


def test_a_live_run_is_refused_in_both_directions(tmp_path, journal, pg_ready):
    rid = _rid()
    with SqliteLogs(tmp_path).create(rid) as ch:
        w = claim(ch)
        with pytest.raises(RunLive) as e:
            copy_run(
                rid, source=SqliteLogs(tmp_path), destination=PostgresLogs(pg_ready)
            )
        assert e.value.run_id == rid
        w.stopped(completed=True, error=None)
    assert not sealed(_address(tmp_path, rid))  # refused before the seal
    with pytest.raises(RunNotFound):
        PostgresLogs(pg_ready).rows(rid)

    rid = _rid()
    with PostgresLogs(pg_ready).create(rid) as ch:
        w = claim(ch)
        with pytest.raises(RunLive):
            copy_run(
                rid, source=PostgresLogs(pg_ready), destination=SqliteLogs(tmp_path)
            )
        w.stopped(completed=True, error=None)
    assert not _address(tmp_path, rid).exists()
    assert temporaries(tmp_path) == []


def test_no_log_at_the_source_is_run_not_found(tmp_path, pg_ready, scratch_db):
    with pytest.raises(RunNotFound):
        copy_run(
            _rid(), source=SqliteLogs(tmp_path), destination=PostgresLogs(pg_ready)
        )
    with pytest.raises(RunNotFound):
        copy_run(
            _rid(), source=PostgresLogs(pg_ready), destination=SqliteLogs(tmp_path)
        )
    # a database whose format schema was never born holds no runs
    with pytest.raises(RunNotFound):
        copy_run(
            _rid(), source=PostgresLogs(scratch_db), destination=SqliteLogs(tmp_path)
        )
    # a born-but-empty sqlite log is no run, as ``attach`` reads it
    rid = _rid()
    SqliteLogs(tmp_path).create(rid).close()
    with pytest.raises(RunNotFound):
        SqliteLogs(tmp_path).rows(rid)


def test_a_run_in_another_format_is_left_to_migrate(tmp_path, pg_ready):
    rid = _rid()
    old = FORMATS["0.3.0"].sqlite_path(tmp_path, rid)
    old.parent.mkdir()
    c = sqlite3.connect(old)
    c.executescript(FORMATS["0.3.0"].sqlite_ddl)
    c.close()
    with pytest.raises(LogFormatMismatch):
        SqliteLogs(tmp_path).rows(rid)
    # nor is a copy written beside it, which would give the run two logs
    pg_run(pg_ready, rid)
    with pytest.raises(LogFormatMismatch):
        copy_run(rid, source=PostgresLogs(pg_ready), destination=SqliteLogs(tmp_path))
    assert not _address(tmp_path, rid).exists()


def test_a_diverging_destination_is_refused(tmp_path, pg_ready, scratch_db):
    """Two databases that each born the run on their own: a snapshot of one, and a
    log of the other, never extend each other."""
    from runstate.channel.postgres import ensure_schema

    ensure_schema(scratch_db)
    rid = _rid()
    pg_run(pg_ready, rid)
    pg_run(scratch_db, rid)
    copy_run(rid, source=PostgresLogs(scratch_db), destination=SqliteLogs(tmp_path))
    snapshot = sqlite_rows(_address(tmp_path, rid))
    with pytest.raises(CopyRefused, match="does not"):
        copy_run(rid, source=PostgresLogs(pg_ready), destination=SqliteLogs(tmp_path))
    assert sqlite_rows(_address(tmp_path, rid)) == snapshot
    assert temporaries(tmp_path) == []
    # and the snapshot into the other database, whose writable log diverges from it
    before = pg_rows(pg_ready, rid)
    with pytest.raises(CopyRefused, match="does not"):
        copy_run(rid, source=SqliteLogs(tmp_path), destination=PostgresLogs(pg_ready))
    assert pg_rows(pg_ready, rid) == before


def test_a_strict_prefix_in_postgres_is_refused(tmp_path, pg_ready):
    """Postgres holds the run's writable log: a source that extends it is not
    copied over it. Built by snapshots of one run at two ends, the earlier one then
    imported into Postgres after a drop."""
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    rid = _rid()
    pg = PostgresLogs(pg_ready)
    pg_run(pg_ready, rid)
    copy_run(rid, source=pg, destination=SqliteLogs(a))
    pg_run(pg_ready, rid)  # a second claim, ended
    copy_run(rid, source=pg, destination=SqliteLogs(b))
    drop_run(rid, logs=pg)
    short = copy_run(rid, source=SqliteLogs(a), destination=pg)
    with pytest.raises(CopyRefused, match="strict prefix"):
        copy_run(rid, source=SqliteLogs(b), destination=pg)
    assert pg_rows(pg_ready, rid) == SqliteLogs(a).rows(rid)
    assert short.last_seq < SqliteLogs(b).rows(rid)[-1].seq


def test_a_writable_sqlite_destination_is_never_replaced(tmp_path, journal):
    """A run's own writable log is at the destination address: copying the run
    onto itself."""
    rid = _rid()
    sqlite_run(tmp_path, rid)
    before = SqliteLogs(tmp_path).rows(rid)
    with pytest.raises(CopyRefused, match="writable"):
        copy_run(rid, source=SqliteLogs(tmp_path), destination=SqliteLogs(tmp_path))
    assert not sealed(_address(tmp_path, rid))
    assert SqliteLogs(tmp_path).rows(rid) == before


# --- retries -------------------------------------------------------------------


def test_an_equal_destination_is_a_no_op_in_both_directions(
    tmp_path, journal, pg_ready
):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    rid = _rid()
    sqlite_run(a, rid)
    pg = PostgresLogs(pg_ready)
    first = copy_run(rid, source=SqliteLogs(a), destination=pg)
    assert copy_run(rid, source=SqliteLogs(a), destination=pg) == first  # a retry
    assert pg_rows(pg_ready, rid) == SqliteLogs(a).rows(rid)

    copy_run(rid, source=pg, destination=SqliteLogs(b))
    inode = os.stat(_address(b, rid)).st_ino
    assert copy_run(rid, source=pg, destination=SqliteLogs(b)) == first
    assert os.stat(_address(b, rid)).st_ino == inode  # not rewritten


def test_a_strict_prefix_snapshot_is_replaced(tmp_path, pg_ready):
    rid = _rid()
    pg = PostgresLogs(pg_ready)
    pg_run(pg_ready, rid)
    first = copy_run(rid, source=pg, destination=SqliteLogs(tmp_path))
    pg_run(pg_ready, rid)  # the run is claimed again, and ends again
    second = copy_run(rid, source=pg, destination=SqliteLogs(tmp_path))
    assert second.last_seq > first.last_seq
    assert sqlite_rows(_address(tmp_path, rid)) == pg.rows(rid)
    assert sealed(_address(tmp_path, rid))
    assert temporaries(tmp_path) == []


# --- the snapshot and the seal -------------------------------------------------


def test_the_snapshot_is_sealed_when_published(tmp_path, pg_ready):
    rid = _rid()
    pg_run(pg_ready, rid)
    copy_run(rid, source=PostgresLogs(pg_ready), destination=SqliteLogs(tmp_path))
    path = _address(tmp_path, rid)
    assert sealed(path)
    for suffix in ("-wal", "-shm", "-journal"):
        assert not path.with_name(path.name + suffix).exists()
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    assert c.execute("PRAGMA journal_mode").fetchone() == ("delete",)
    c.close()
    assert temporaries(tmp_path) == []
    # it attaches and reads, as any log does
    with SqliteLogs(tmp_path).attach(rid) as ch:
        assert ch.last_seq() == pg_rows(pg_ready, rid)[-1].seq
        if not _AS_ROOT:
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                ch.send(asdict(Value(value=1, t=None)), topic=Topic.VALUE, name="n")


@pytest.mark.parametrize("fault", [OSError, KeyboardInterrupt])
def test_a_failed_snapshot_leaves_no_temporary_file(
    tmp_path, pg_ready, monkeypatch, fault
):
    """A failure at the last step before the rename (the seal), injected: the
    address keeps what it held, and the temporary file is gone."""
    import runstate.logs

    rid = _rid()
    pg = PostgresLogs(pg_ready)
    pg_run(pg_ready, rid)
    first = copy_run(rid, source=pg, destination=SqliteLogs(tmp_path))
    pg_run(pg_ready, rid)

    def fail(path):
        assert path.name.endswith(".tmp") and path.exists()
        raise fault("injected")

    monkeypatch.setattr(runstate.logs, "_seal", fail)
    with pytest.raises(fault):
        copy_run(rid, source=pg, destination=SqliteLogs(tmp_path))
    assert temporaries(tmp_path) == []
    assert sqlite_rows(_address(tmp_path, rid))[-1].seq == first.last_seq
    # and on a first write, with nothing at the address
    rid = _rid()
    pg_run(pg_ready, rid)
    with pytest.raises(fault):
        copy_run(rid, source=pg, destination=SqliteLogs(tmp_path))
    assert temporaries(tmp_path) == []
    assert not _address(tmp_path, rid).exists()


def test_the_import_seals_its_source(tmp_path, journal, pg_ready):
    rid = _rid()
    sqlite_run(tmp_path, rid)
    copy_run(rid, source=SqliteLogs(tmp_path), destination=PostgresLogs(pg_ready))
    path = _address(tmp_path, rid)
    assert sealed(path)
    for suffix in ("-wal", "-shm"):
        assert not path.with_name(path.name + suffix).exists()
    if not _AS_ROOT:
        # a stale writer to the SQLite address fails loudly, by either open
        # (``create``'s may already fail, writing the journal mode)
        for locate in (SqliteLogs.attach, SqliteLogs.create):
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                with locate(SqliteLogs(tmp_path), rid) as ch:
                    ch.send(asdict(Value(value=1, t=None)), topic=Topic.VALUE, name="n")


# --- races, each made deterministic --------------------------------------------


def pause_after_first_call(monkeypatch, owner, name):
    """Replace ``owner.name`` so that its first call, once it has returned, sets
    ``reached`` and waits for ``go`` before handing its result back."""
    real = getattr(owner, name)
    reached, go = threading.Event(), threading.Event()
    calls = []

    def paused(*args, **kwargs):
        out = real(*args, **kwargs)
        calls.append(None)
        if len(calls) == 1:
            reached.set()
            assert go.wait(30), "the test never released the pause"
        return out

    monkeypatch.setattr(owner, name, paused)
    return reached, go


def in_thread(fn):
    out = {}

    def run():
        try:
            out["value"] = fn()
        except BaseException as exc:  # handed to the test thread
            out["error"] = exc

    t = threading.Thread(target=run)
    t.start()
    return t, out


def await_lock_waiters(dsn, n):
    """Until ``n`` backends of this database wait on a lock: a condition polled,
    not a duration guessed."""
    import psycopg

    deadline = time.monotonic() + 30
    with psycopg.connect(dsn, autocommit=True) as c:
        while True:
            (k,) = c.execute(
                "SELECT count(*) FROM pg_stat_activity"
                " WHERE datname = current_database() AND wait_event_type = 'Lock'"
            ).fetchone()
            if k >= n:
                return
            assert time.monotonic() < deadline, f"{k} lock waiters, never {n}"
            time.sleep(0.005)


def test_a_writer_birthing_the_run_during_an_import_loses_to_it(
    tmp_path, pg_ready, monkeypatch
):
    """The import has inserted and not committed; a claimant's CAS at 0 waits on
    its first row, then loses when the import commits."""
    import runstate.logs

    rid = _rid()
    sqlite_run(tmp_path, rid)
    source = SqliteLogs(tmp_path).rows(rid)
    writer = PostgresLogs(pg_ready).create(rid)
    try:
        # the import's verify read: after its insert, inside its transaction
        reached, go = pause_after_first_call(monkeypatch, runstate.logs, "_pg_rows")
        t, imported = in_thread(
            lambda: copy_run(
                rid, source=SqliteLogs(tmp_path), destination=PostgresLogs(pg_ready)
            )
        )
        assert reached.wait(30)
        u, birthed = in_thread(
            lambda: writer.send(
                started(), topic=Topic.LIFECYCLE_STARTED, expected_seq=0
            )
        )
        await_lock_waiters(pg_ready, 1)  # the CAS waits on the import's row
        go.set()
        t.join(), u.join()
    finally:
        writer.close()
    assert "error" not in imported and "error" not in birthed
    assert imported["value"].digest == log_digest(source)
    assert birthed["value"] is None  # the claim is provably lost
    assert pg_rows(pg_ready, rid) == source


def test_a_writer_birthing_the_run_during_an_import_wins_over_it(
    tmp_path, pg_ready, monkeypatch
):
    """The import has found the destination empty and not inserted; a claimant
    births the run and commits; the import's insert collides and is refused."""
    import runstate.channel.postgres

    rid = _rid()
    sqlite_run(tmp_path, rid)
    writer = PostgresLogs(pg_ready).create(rid)
    try:
        # the import's destination check, before its insert
        reached, go = pause_after_first_call(
            monkeypatch, runstate.channel.postgres, "check_format"
        )
        t, imported = in_thread(
            lambda: copy_run(
                rid, source=SqliteLogs(tmp_path), destination=PostgresLogs(pg_ready)
            )
        )
        assert reached.wait(30)
        seq = writer.send(started(), topic=Topic.LIFECYCLE_STARTED, expected_seq=0)
        go.set()
        t.join()
    finally:
        writer.close()
    assert seq == 1
    assert isinstance(imported.get("error"), CopyRefused)
    assert [r.seq for r in pg_rows(pg_ready, rid)] == [1]  # the writer's alone


def test_an_export_racing_a_claim_publishes_nothing_from_after_its_read(
    tmp_path, pg_ready, monkeypatch
):
    """A claim that lands after the export's one read: the snapshot is the log as
    read, its last seq before the claim's."""
    import runstate.logs

    rid = _rid()
    pg_run(pg_ready, rid)
    read = pg_rows(pg_ready, rid)
    with PostgresLogs(pg_ready).attach(rid) as ch:
        reached, go = pause_after_first_call(monkeypatch, runstate.logs, "_pg_rows")
        t, exported = in_thread(
            lambda: copy_run(
                rid, source=PostgresLogs(pg_ready), destination=SqliteLogs(tmp_path)
            )
        )
        assert reached.wait(30)
        w = claim(ch)
        assert w.claimed
        claim_seq = ch.latest(Topic.LIFECYCLE_STARTED).seq
        go.set()
        t.join()
        assert "error" not in exported
        assert exported["value"].last_seq == read[-1].seq < claim_seq
        assert sqlite_rows(_address(tmp_path, rid)) == read
        # a claim that lands before the read: the export is refused, as routine
        monkeypatch.undo()
        with pytest.raises(RunLive):
            copy_run(
                rid, source=PostgresLogs(pg_ready), destination=SqliteLogs(tmp_path)
            )
        assert sqlite_rows(_address(tmp_path, rid)) == read  # the stale one stands
        w.stopped(completed=True, error=None)


# --- drop_run -------------------------------------------------------------------


def test_drop_refuses_a_live_run(tmp_path, journal, pg_ready):
    rid = _rid()
    with SqliteLogs(tmp_path).create(rid) as ch:
        w = claim(ch)
        with pytest.raises(RunLive):
            drop_run(rid, logs=SqliteLogs(tmp_path))
        w.stopped(completed=True, error=None)
    assert SqliteLogs(tmp_path).rows(rid)
    rid = _rid()
    with PostgresLogs(pg_ready).create(rid) as ch:
        w = claim(ch)
        with pytest.raises(RunLive):
            drop_run(rid, logs=PostgresLogs(pg_ready))
        w.stopped(completed=True, error=None)
    assert pg_rows(pg_ready, rid)


def test_drop_of_an_absent_run_is_a_no_op(tmp_path, pg_ready, scratch_db):
    drop_run(_rid(), logs=SqliteLogs(tmp_path))
    drop_run(_rid(), logs=PostgresLogs(pg_ready))
    drop_run(_rid(), logs=PostgresLogs(scratch_db))  # no format schema at all
    rid = _rid()
    sqlite_run(tmp_path, rid)
    pg_run(pg_ready, rid)
    for logs in (SqliteLogs(tmp_path), PostgresLogs(pg_ready)):
        drop_run(rid, logs=logs)
        drop_run(rid, logs=logs)  # again: idempotent
        with pytest.raises(RunNotFound):
            logs.rows(rid)


def test_drop_deletes_a_sealed_snapshot(tmp_path, pg_ready):
    rid = _rid()
    pg_run(pg_ready, rid)
    copy_run(rid, source=PostgresLogs(pg_ready), destination=SqliteLogs(tmp_path))
    drop_run(rid, logs=SqliteLogs(tmp_path))
    assert not _address(tmp_path, rid).exists()
    assert pg_rows(pg_ready, rid)  # the other place is its own


def test_drop_on_sqlite_takes_the_sidecars(
    tmp_path, crashed_wal_writer, crashed_journal_writer
):
    """A writer that crashed leaves its -wal and -shm, or a hot -journal."""
    wal, journal = _rid(), _rid()
    _address(tmp_path, wal).parent.mkdir()
    crashed_wal_writer(_address(tmp_path, wal), 3)
    crashed_journal_writer(_address(tmp_path, journal), 3)
    for rid, sidecars in ((wal, ("-wal", "-shm")), (journal, ("-journal",))):
        path = _address(tmp_path, rid)
        assert all(path.with_name(path.name + s).exists() for s in sidecars)
        drop_run(rid, logs=SqliteLogs(tmp_path))
        assert not path.exists()
        assert not any(
            path.with_name(path.name + s).exists() for s in ("-wal", "-shm", "-journal")
        )


@pytest.mark.parametrize("cas", [True, False])
def test_an_append_racing_a_postgres_drop_lands_no_orphan(pg_ready, cas):
    """The drop holds the seal's table lock and waits on a row lock a second
    connection holds; an append arrives and waits on the drop's table lock; then
    the second connection lets go. A CAS expecting the dropped log's last seq
    fails; an unconditional append starts a new log at seq 1 (spec §5, "Its
    limit"), never one at the dropped log's next seq."""
    import psycopg

    rid = _rid()
    pg_run(pg_ready, rid)
    last = pg_rows(pg_ready, rid)[-1].seq
    appender = PostgresLogs(pg_ready).attach(rid)
    holder = psycopg.connect(pg_ready)
    try:
        holder.execute(
            f"SELECT 1 FROM {_SCHEMA}.log WHERE run_id = %s FOR UPDATE", [rid]
        )
        t, dropped = in_thread(lambda: drop_run(rid, logs=PostgresLogs(pg_ready)))
        await_lock_waiters(pg_ready, 1)  # the drop's DELETE, under its table lock
        if cas:
            send = lambda: appender.send(  # noqa: E731
                started(), topic=Topic.LIFECYCLE_STARTED, expected_seq=last
            )
        else:
            send = lambda: appender.send(  # noqa: E731
                asdict(Value(value=1, t=None)), topic=Topic.VALUE, name="n"
            )
        u, appended = in_thread(send)
        await_lock_waiters(pg_ready, 2)  # the append, on the drop's table lock
        holder.commit()
        t.join(), u.join()
    finally:
        holder.close()
        appender.close()
    assert "error" not in dropped and "error" not in appended
    if cas:
        assert appended["value"] is None
        assert pg_rows(pg_ready, rid) == []
    else:
        assert appended["value"] == 1
        assert [r.seq for r in pg_rows(pg_ready, rid)] == [1]
