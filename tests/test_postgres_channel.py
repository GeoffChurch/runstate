"""PostgresChannel backend -- the postgres-specific behaviors the cross-backend
conformance suite (test_channel.py) can't express on one shared ``log`` table:
schema provisioning, shared-table run isolation, and the session probe. The four-op conformance itself is driven by test_channel.py /
test_concurrency.py once the fixtures parametrize over the "postgres" backend.

Skipped wholesale when ``psycopg`` is absent; each test additionally skips when
``RUNSTATE_TEST_PG_DSN`` is unset (via the ``pg_dsn`` fixture)."""

import multiprocessing as mp
import os
import threading
import time
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")

from runstate.formats import FORMATS, LOG_FORMAT  # noqa: E402


def _poll_until(fn, target, *, timeout=10.0, interval=0.05):
    """Poll ``fn()`` until it equals ``target`` or the timeout elapses; return the
    final match. Liveness transitions (a lock acquire; release on disconnect) are
    not instantaneous from a *separate* connection's view, so the lock tests
    converge rather than sample once."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if fn() == target:
            return True
        time.sleep(interval)
    return fn() == target


def test_ensure_schema_creates_log_table(pg_dsn):
    """``ensure_schema(dsn)`` provisions the shared ``log`` table in the current
    format's schema (the DDL the channel ``__init__`` deliberately does NOT run
    -- it only probes)."""
    from runstate.channel.postgres import ensure_schema

    table = f"{FORMATS[LOG_FORMAT].pg_schema()}.log"
    ensure_schema(pg_dsn)
    with psycopg.connect(pg_dsn) as c:
        assert c.execute("select to_regclass(%s)::text", [table]).fetchone() == (table,)


def test_a_failed_open_closes_its_connection(pg_ready, monkeypatch):
    """T3: every step of the open after the connect sits inside one close-on-
    failure guard, so a failure anywhere in it (here, setting the search path)
    leaks no connection."""
    from psycopg import sql

    from runstate.channel import postgres

    class Boom(Exception):
        pass

    opened = []
    real = postgres.psycopg.connect

    def connect(*args, **kwargs):
        conn = real(*args, **kwargs)
        opened.append(conn)
        execute = conn.execute

        def failing(query, *a, **k):
            text = query.as_string(conn) if isinstance(query, sql.Composable) else query
            if "search_path" in text:
                raise Boom
            return execute(query, *a, **k)

        conn.execute = failing
        return conn

    monkeypatch.setattr(postgres.psycopg, "connect", connect)
    with pytest.raises(Boom):
        postgres.PostgresChannel(pg_ready, f"leak-{uuid.uuid4().hex}")
    assert len(opened) == 1 and opened[0].closed


def test_ensure_schema_creates_both_indexes(pg_dsn):
    """Both, not one. ``(run_id, topic, name, seq)`` serves latest(topic, name=)
    -- measured 2504 shared buffers through a top-N heapsort vs 4 through an
    Index Only Scan Backward (#19) -- but it cannot serve the name-less
    latest(topic), because ``name`` sits between the equality prefix and the
    sort key. Unlike sqlite, ensure_schema reaches rows that already exist, so
    this is also the Postgres side of the migration."""
    from runstate.channel.postgres import ensure_schema

    ensure_schema(pg_dsn)
    with psycopg.connect(pg_dsn) as c:  # the current format's table, not any `log`
        names = {
            r[0]
            for r in c.execute(
                "select indexname from pg_indexes"
                " where schemaname = %s and tablename = 'log'",
                [FORMATS[LOG_FORMAT].pg_schema()],
            )
        }
    assert {"idx_log_run_topic_seq", "idx_log_run_topic_name_seq"} <= names


def test_latest_with_name_is_index_served_not_sorted(pg_ready):
    """The #19 property itself: the name participates in the seek. Without the
    index the planner sorts the whole (run_id, topic) partition, so a rare,
    early-only or absent name pays that on every call, forever."""
    run = f"idx-{uuid.uuid4().hex[:8]}"
    log = f"{FORMATS[LOG_FORMAT].pg_schema()}.log"
    with psycopg.connect(pg_ready) as c:
        c.execute(
            f"insert into {log} (run_id, seq, topic, name, request_id, body, created_at)"
            " select %s, g, 'value', 'm'||(g %% 40), null, '{}', 0.0"
            " from generate_series(1, 5000) g",
            (run,),
        )
        c.execute(f"analyze {log}")
        plan = "\n".join(
            r[0]
            for r in c.execute(
                f"explain select seq from {log} where run_id = %s and topic = 'value'"
                " and name = 'm7' order by seq desc limit 1",
                (run,),
            )
        )
    assert "idx_log_run_topic_name_seq" in plan
    assert "Sort" not in plan  # the tell that the partition is being ordered


# ===================== backend-specific (cycle 6) =====================
# The shared-table consequences the cross-backend conformance suite can't express.


def test_shared_table_isolates_runs_by_seq(pg_ready):
    """Two runs interleaved on the ONE shared ``log`` table keep independent 1-based
    seqs -- the per-run ``MAX+1`` + ``WHERE run_id`` scoping, not a global counter
    (which would gap each run and break content-pinning)."""
    from runstate.channel.postgres import PostgresChannel

    a = PostgresChannel(pg_ready, run_id=f"iso-a-{uuid.uuid4()}")
    b = PostgresChannel(pg_ready, run_id=f"iso-b-{uuid.uuid4()}")
    try:
        a.send({"r": "a1"}, topic="value")
        b.send({"r": "b1"}, topic="value")
        a.send({"r": "a2"}, topic="value")
        b.send({"r": "b2"}, topic="value")
        b.send({"r": "b3"}, topic="value")
        assert [e.seq for e in a.read()] == [1, 2]
        assert [e.seq for e in b.read()] == [1, 2, 3]
        assert [e.body["r"] for e in a.read()] == ["a1", "a2"]
        assert [e.body["r"] for e in b.read()] == ["b1", "b2", "b3"]
    finally:
        a.close()
        b.close()


def test_unconditional_contention_produces_unique_contiguous_seqs(pg_ready):
    """N connections x M plain (unconditional) sends on ONE run -> seqs exactly
    1..N*M, no loss/dup. This is the cross-host ``control.*`` write path (several
    writers, no shared internal lock): the PK is the sole arbiter and the optimistic
    ``MAX+1`` retry on UniqueViolation converges."""
    from runstate.channel.postgres import PostgresChannel

    run_id = f"uncont-{uuid.uuid4()}"
    writers, n = 4, 50
    chans = [PostgresChannel(pg_ready, run_id=run_id) for _ in range(writers)]
    try:

        def hammer(c):
            for i in range(n):
                c.send({"i": i}, topic="value", name="x")

        threads = [threading.Thread(target=hammer, args=(c,)) for c in chans]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        total = writers * n
        seqs = sorted(e.seq for e in chans[0].read())
        assert len(seqs) == total  #                      no lost writes
        assert seqs == list(range(1, total + 1))  #       unique + contiguous
    finally:
        for c in chans:
            c.close()


def test_cas_wedged_writer_raises_not_false_loss(pg_ready):
    """A rival holding an UNCOMMITTED ``(run, expected+1)`` leaves the CAS outcome
    indeterminate: our guarded INSERT blocks on the PK index, hits ``lock_timeout``,
    and must RAISE (never synthesize a loss -- a rival that then rolls back would
    leave the run claimed by nobody)."""
    from runstate.channel.postgres import PostgresChannel

    run_id = f"wedge-{uuid.uuid4()}"
    ch = PostgresChannel(pg_ready, run_id=run_id)
    seed = ch.send({}, topic="value", name="seed")  #         seq 1
    ch._conn.execute(
        "SET lock_timeout = '300ms'"
    )  #         keep the wedge wait test-sized
    wedger = psycopg.connect(pg_ready)  #                     autocommit=False
    try:
        wedger.execute(  # an uncommitted (run, seed+1) holds the PK index slot
            f"INSERT INTO {FORMATS[LOG_FORMAT].pg_schema()}.log"
            " (run_id, seq, topic, name, request_id, body, created_at)"
            " VALUES (%s, %s, 'lifecycle.started', NULL, NULL, '{}', 0)",
            (run_id, seed + 1),
        )
        with pytest.raises(psycopg.errors.LockNotAvailable):
            ch.send({}, topic="lifecycle.started", expected_seq=seed)
    finally:
        wedger.rollback()
        wedger.close()
        ch.close()


def test_unconditional_send_bound_exhaustion_raises(pg_ready, monkeypatch):
    """Unbounded retry would mask a pathological fault, so the unconditional send is
    bounded -- and exhaustion RAISES (a fault), never returns a false ``None``.
    Injected: persistent UniqueViolations drive the loop to its (lowered) bound."""
    from runstate.channel import postgres as pg_mod
    from runstate.channel.postgres import PostgresChannel

    ch = PostgresChannel(pg_ready, run_id=f"exhaust-{uuid.uuid4()}")
    monkeypatch.setattr(pg_mod, "_SEND_RETRY_BOUND", 3)
    real_execute = ch._conn.execute

    def always_conflict(sql, *a, **k):
        if "INSERT" in sql and "COALESCE(MAX" in sql:  #      the unconditional append
            raise psycopg.errors.UniqueViolation("simulated persistent contention")
        return real_execute(sql, *a, **k)

    monkeypatch.setattr(ch._conn, "execute", always_conflict)
    try:
        with pytest.raises(RuntimeError, match="exhausted"):
            ch.send({}, topic="value", name="x")  #           unconditional
    finally:
        ch.close()


# ===================== the session probe (commits-and-lineage.md §3, §11) =====================
# Each connection takes a fresh random key by try-lock before its first write, and the
# backend stamps every row with its writer's key and server. The probe asks whether the
# key on a claim's row is still held: evidence for a death report (which
# report_if_dead writes), never a claim arbiter -- the CAS stays the only one.


def _held_keys(conn, pid):
    """The advisory keys that backend ``pid`` holds, read from pg_locks: a bigint key
    is stored split as (classid=high32, objid=low32, objsubid=1), and int8's ``<<``
    wraps, so the reassembled value is the signed key itself."""
    rows = conn.execute(
        "SELECT ((classid::bigint << 32) | objid::bigint) FROM pg_locks"
        " WHERE locktype = 'advisory' AND granted AND objsubid = 1 AND pid = %s",
        [pid],
    ).fetchall()
    return {k for (k,) in rows}


def _server_identity(dsn):
    """The server's system identifier and its current WAL timeline, the timeline read
    from the WAL file name's first 8 hex digits: a second source, not the channel's."""
    with psycopg.connect(dsn) as c:
        sysid, walfile = c.execute(
            "SELECT system_identifier, pg_walfile_name(pg_current_wal_lsn())"
            " FROM pg_control_system()"
        ).fetchone()
    return f"{sysid}:{int(walfile[:8], 16)}"


def _write_and_wait(dsn, run_id, ready):
    """Child entrypoint: open a channel, write one record, report its seq, then block
    until the parent SIGKILLs it. Top-level for the 'spawn' start method."""
    from runstate.channel.postgres import PostgresChannel

    ch = PostgresChannel(dsn, run_id=run_id)
    ready.put(ch.send({}, topic="lifecycle.started", expected_seq=0))
    while True:
        time.sleep(3600)


def test_postgres_channel_is_a_session_probe_and_the_others_are_not(pg_ready, tmp_path):
    from runstate.channel import SessionProbe
    from runstate.channel.memory import MemoryChannel
    from runstate.channel.postgres import PostgresChannel
    from runstate.channel.sqlite import SqliteChannel

    with PostgresChannel(pg_ready, run_id=f"probe-{uuid.uuid4()}") as ch:
        assert isinstance(ch, SessionProbe)
    assert not isinstance(MemoryChannel(), SessionProbe)
    with SqliteChannel(tmp_path / "r.db") as sch:
        assert not isinstance(sch, SessionProbe)


def test_every_row_carries_its_writers_session(pg_ready):
    from runstate.channel.postgres import PostgresChannel

    run_id = f"stamp-{uuid.uuid4()}"
    a = PostgresChannel(pg_ready, run_id=run_id)
    b = PostgresChannel(pg_ready, run_id=run_id)
    try:
        seqs = {
            a: [a.send({"i": 1}, topic="value", name="x")],
            b: [b.send({"i": 2}, topic="value", name="x")],
        }
        seqs[a].append(a.send({"i": 3}, topic="value", name="x", expected_seq=2))
        sessions = {}
        with psycopg.connect(pg_ready) as admin:
            for ch, written in seqs.items():
                (session,) = {a.writer_session(s) for s in written}
                assert session is not None
                assert session.key in _held_keys(admin, ch._conn.info.backend_pid)
                sessions[ch] = session
        assert sessions[a].key != sessions[b].key
        assert sessions[a].server == sessions[b].server == _server_identity(pg_ready)
    finally:
        a.close()
        b.close()


def test_a_connection_takes_its_key_before_its_first_write(pg_ready):
    from runstate.channel.postgres import PostgresChannel

    with PostgresChannel(pg_ready, run_id=f"pre-{uuid.uuid4()}") as ch:
        with psycopg.connect(pg_ready) as admin:
            assert len(_held_keys(admin, ch._conn.info.backend_pid)) == 1
        assert ch.last_seq() == 0  # nothing written


def _drawing(keys):
    """A stand-in for the channel's ``secrets``, whose ``token_bytes`` yields ``keys``
    in order, as the 8 signed big-endian bytes the open turns back into a key."""
    from types import SimpleNamespace

    draws = iter(keys)
    return SimpleNamespace(
        token_bytes=lambda n: next(draws).to_bytes(n, "big", signed=True)
    )


def test_a_key_another_connection_holds_is_drawn_again(pg_ready, monkeypatch):
    """The try-lock fails on a key held elsewhere, and the open draws a fresh one; a
    key space that stays held through every draw fails the open loudly."""
    from itertools import repeat

    from runstate.channel import postgres

    held, fresh = (int.from_bytes(os.urandom(8), "big", signed=True) for _ in range(2))
    with psycopg.connect(pg_ready, autocommit=True) as holder:
        holder.execute("SELECT pg_advisory_lock(%s::bigint)", [held])
        monkeypatch.setattr(postgres, "secrets", _drawing([held, fresh]))
        with postgres.PostgresChannel(pg_ready, run_id=f"redraw-{uuid.uuid4()}") as ch:
            assert ch._key == fresh
        monkeypatch.setattr(postgres, "secrets", _drawing(repeat(held)))
        with pytest.raises(RuntimeError, match="no free advisory-lock key"):
            postgres.PostgresChannel(pg_ready, run_id=f"redraw-{uuid.uuid4()}")


def test_writer_session_of_a_seq_with_no_record_raises(pg_ready):
    """A seq the run has not reached names no record, which is not an unstamped one:
    the probe raises rather than answer None, the copied row's abstention."""
    from runstate.channel.postgres import PostgresChannel

    with PostgresChannel(pg_ready, run_id=f"nosuch-{uuid.uuid4()}") as ch:
        with pytest.raises(LookupError, match="no record at seq 1"):
            ch.writer_session(1)
        ch.send({}, topic="lifecycle.started", expected_seq=0)
        with pytest.raises(LookupError, match="no record at seq 2"):
            ch.writer_session(2)


def test_a_server_below_the_version_floor_is_refused(pg_ready, monkeypatch):
    """Every row's stamp calls pg_split_walfile_name, which PostgreSQL 16 introduced:
    on an older server the open refuses, before any write, rather than letting the
    first claim fail with UndefinedFunction. Simulated by raising the floor above this
    server's version."""
    from runstate.channel import postgres

    monkeypatch.setattr(postgres, "_SERVER_VERSION_FLOOR", 990000)
    with pytest.raises(RuntimeError, match="PostgreSQL 99 .*pg_split_walfile_name"):
        postgres.PostgresChannel(pg_ready, run_id=f"floor-{uuid.uuid4()}")


def test_session_ended_reads_held_then_ended_after_close(pg_ready):
    from runstate.channel.postgres import PostgresChannel

    run_id = f"ended-{uuid.uuid4()}"
    writer = PostgresChannel(pg_ready, run_id=run_id)
    probe = PostgresChannel(pg_ready, run_id=run_id)
    try:
        seq = writer.send({}, topic="lifecycle.started", expected_seq=0)
        session = probe.writer_session(seq)
        assert probe.session_ended(session) is False
        writer.close()
        assert _poll_until(lambda: probe.session_ended(session), True)
    finally:
        writer.close()
        probe.close()


def test_a_killed_writers_session_reads_ended(pg_ready):
    from runstate.channel.postgres import PostgresChannel

    run_id = f"killed-{uuid.uuid4()}"
    ctx = mp.get_context("spawn")
    ready = ctx.Queue()
    proc = ctx.Process(target=_write_and_wait, args=(pg_ready, run_id, ready))
    proc.start()
    probe = PostgresChannel(pg_ready, run_id=run_id)
    try:
        seq = ready.get(timeout=30)
        session = probe.writer_session(seq)
        assert probe.session_ended(session) is False
        proc.kill()  # SIGKILL: no clean disconnect
        proc.join(timeout=10)
        assert _poll_until(lambda: probe.session_ended(session), True)
    finally:
        if proc.is_alive():
            proc.kill()
            proc.join()
        probe.close()


def test_session_ended_abstains_on_another_servers_stamp_and_an_unstamped_row(
    pg_ready,
):
    from runstate.channel import Session
    from runstate.channel.postgres import PostgresChannel

    run_id = f"abstain-{uuid.uuid4()}"
    with PostgresChannel(pg_ready, run_id=run_id) as ch:
        seq = ch.send({}, topic="lifecycle.started", expected_seq=0)
        mine = ch.writer_session(seq)
        # a row as the migration store inserts it: its columns named, no stamp
        with psycopg.connect(pg_ready, autocommit=True) as c:
            c.execute(
                f"INSERT INTO {FORMATS[LOG_FORMAT].pg_schema()}.log"
                " (run_id, seq, topic, name, request_id, body, created_at)"
                " VALUES (%s, 2, 'value', 'x', NULL, '{}', 0)",
                [run_id],
            )
        assert ch.writer_session(2) is None
        assert ch.session_ended(Session(server="elsewhere", key=mine.key)) is None
        assert ch.session_ended(mine) is False


def test_session_ended_ignores_a_lock_in_another_database(pg_ready):
    """`pg_locks` is cluster-wide, and advisory keys are per database: a stranger
    holding the very same key in another database says nothing of this one's writer."""
    from runstate.channel.postgres import PostgresChannel

    other_db = f"rs_other_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(pg_ready, autocommit=True) as admin:
        try:
            admin.execute(f'CREATE DATABASE "{other_db}"')
        except psycopg.errors.InsufficientPrivilege:
            pytest.skip("test role cannot CREATEDB")
    run_id = f"crossdb-{uuid.uuid4()}"
    writer = probe = stranger = None
    try:  # the opens too, so a failed one still drops the database
        writer = PostgresChannel(pg_ready, run_id=run_id)
        probe = PostgresChannel(pg_ready, run_id=run_id)
        session = probe.writer_session(
            writer.send({}, topic="lifecycle.started", expected_seq=0)
        )
        other_dsn = psycopg.conninfo.make_conninfo(pg_ready, dbname=other_db)
        stranger = psycopg.connect(other_dsn, autocommit=True)
        writer.close()  # release the key here, so only the stranger holds it
        assert stranger.execute(
            "SELECT pg_try_advisory_lock(%s)", [session.key]
        ).fetchone() == (True,)
        assert _poll_until(lambda: probe.session_ended(session), True)
    finally:
        for conn in (stranger, writer, probe):
            if conn is not None:
                conn.close()
        with psycopg.connect(pg_ready, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{other_db}" WITH (FORCE)')


def test_the_channel_sets_synchronous_commit_on(scratch_db):
    """A database default of ``off`` acknowledges a commit before it is durable, and a
    crash then loses rows readers saw and reuses their seqs. The channel sets it on its
    own connection, whatever the database or role says."""
    from runstate.channel.postgres import PostgresChannel, ensure_schema

    with psycopg.connect(scratch_db, autocommit=True) as c:
        dbname = c.execute("SELECT current_database()").fetchone()[0]
        c.execute(f'ALTER DATABASE "{dbname}" SET synchronous_commit = off')
    ensure_schema(scratch_db)
    with psycopg.connect(scratch_db) as c:  # the default a plain connection gets
        assert c.execute("SHOW synchronous_commit").fetchone() == ("off",)
    with PostgresChannel(scratch_db, run_id="r1") as ch:
        assert ch._conn.execute("SHOW synchronous_commit").fetchone() == ("on",)


def _fsync_on_a_fresh_connection(dsn):
    with psycopg.connect(dsn) as c:
        return c.execute("SHOW fsync").fetchone()[0]


def test_a_server_with_fsync_off_is_refused(pg_ready):
    """With ``fsync = off`` a crash lost 14 to 2,386 already-visible rows in 40 of 40
    trials and reused their seqs (review §12, item 15). The setting is the server's,
    so the channel cannot fix it; it refuses to open.

    Opt-in (``RUNSTATE_TEST_PG_ALTER_SYSTEM=1``), because it changes server-wide,
    persisted config with ``ALTER SYSTEM``: while it runs, every open on the server
    fails, a second suite's included, and a hard kill before the restore leaves
    ``fsync = off`` for every later run on the cluster. Set it only for a throwaway
    server, as CI's is. The restore is in a ``finally`` that covers the alter, and is
    waited for, so no later test inherits it."""
    from runstate.channel.postgres import PostgresChannel

    if os.environ.get("RUNSTATE_TEST_PG_ALTER_SYSTEM") != "1":
        pytest.skip(
            "alters server-wide config; set RUNSTATE_TEST_PG_ALTER_SYSTEM=1 "
            "on a throwaway server to run it"
        )
    with psycopg.connect(pg_ready) as c:  # asked, so a refusal alters nothing
        (allowed,) = c.execute(
            "SELECT has_parameter_privilege('fsync', 'ALTER SYSTEM')"
        ).fetchone()
    if not allowed:
        pytest.skip("test role cannot ALTER SYSTEM")
    try:
        with psycopg.connect(pg_ready, autocommit=True) as admin:
            admin.execute("ALTER SYSTEM SET fsync = off")
            admin.execute("SELECT pg_reload_conf()")
        assert _poll_until(lambda: _fsync_on_a_fresh_connection(pg_ready), "off")
        with pytest.raises(RuntimeError, match="fsync"):
            PostgresChannel(pg_ready, run_id=f"fsync-{uuid.uuid4()}")
    finally:
        with psycopg.connect(pg_ready, autocommit=True) as admin:
            admin.execute("ALTER SYSTEM RESET fsync")
            admin.execute("SELECT pg_reload_conf()")
        assert _poll_until(lambda: _fsync_on_a_fresh_connection(pg_ready), "on")


def test_the_stamp_reads_the_live_wal_timeline_not_the_checkpoints():
    """A promotion advances the WAL timeline at once, but since PostgreSQL 13 it only
    requests a spread checkpoint, so pg_control_checkpoint()'s timeline stayed the old
    one for more than 100 s after it: a probe on the promoted server then took itself
    for the old primary and read a live writer's session ended. With no standby to
    promote here, this pins the source structurally: every statement that stamps a row
    or reads the probe's identity reads the live WAL insertion point."""
    from runstate.channel import postgres

    assert "pg_current_wal_lsn()" in postgres._IDENTITY
    assert "pg_control_checkpoint" not in postgres._IDENTITY
    for stmt in (postgres._CAS, postgres._UNCONDITIONAL, postgres._SERVER):
        assert postgres._IDENTITY in stmt


def test_session_ended_answers_where_resolve_abstains(pg_ready):
    """The cross-host property, tested on one host: a claim whose handle names
    ANOTHER host -> ``resolve`` abstains (it cannot read a foreign pid table), but the
    session probe answers: held while the writer's connection lives, ended after."""
    from runstate.channel.postgres import PostgresChannel
    from runstate.vocabulary.handle import resolve

    foreign = "local://OTHER-HOST/99999"
    assert resolve(foreign) is None
    run_id = f"xhost-{uuid.uuid4()}"
    writer = PostgresChannel(pg_ready, run_id=run_id)
    probe = PostgresChannel(pg_ready, run_id=run_id)
    try:
        seq = writer.send(
            {"handle": foreign, "t": None}, topic="lifecycle.started", expected_seq=0
        )
        session = probe.writer_session(seq)
        assert probe.session_ended(session) is False
        writer.close()
        assert _poll_until(lambda: probe.session_ended(session), True)
    finally:
        writer.close()
        probe.close()
