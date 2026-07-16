"""PostgresChannel backend -- the postgres-specific behaviors the cross-backend
conformance suite (test_channel.py) can't express on one shared ``log`` table:
schema provisioning, shared-table run isolation, the liveness lock, and the
Watcher wiring. The four-op conformance itself is driven by test_channel.py /
test_concurrency.py once the fixtures parametrize over the "postgres" backend.

Skipped wholesale when ``psycopg`` is absent; each test additionally skips when
``RUNSTATE_TEST_PG_DSN`` is unset (via the ``pg_dsn`` fixture)."""

import multiprocessing as mp
import threading
import time
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")


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


def _hold_episode_forever(dsn, run_id, started_seq):
    """Child entrypoint: take the episode lock, then block until the parent SIGKILLs
    us. Top-level so the 'spawn' start method can import it (spawn => no inherited
    psycopg connection, per the fork-safety discipline)."""
    from runstate.channel.postgres import PostgresChannel

    ch = PostgresChannel(dsn, run_id=run_id)
    ch.hold_episode(started_seq)
    while True:
        time.sleep(3600)


def test_ensure_schema_creates_log_table(pg_dsn):
    """``ensure_schema(dsn)`` provisions the shared ``log`` table (the DDL the
    channel ``__init__`` deliberately does NOT run -- it only probes)."""
    from runstate.channel.postgres import ensure_schema

    ensure_schema(pg_dsn)
    with psycopg.connect(pg_dsn) as c:
        assert c.execute("select to_regclass('log')").fetchone()[0] == "log"


# ===================== the liveness lock (cycle 4) =====================
# claim = the uniform CAS; the advisory lock is a Watcher-consumed liveness SIGNAL,
# never a claim arbiter. These pin the signal itself (the Watcher wiring is cycle 5).


def test_postgres_channel_satisfies_episode_protocols(pg_ready):
    """PostgresChannel structurally satisfies BOTH viewpoint Protocols (it is the
    backend that can both hold and probe an episode lock)."""
    from runstate.channel.base import EpisodeHolder, EpisodeProbe
    from runstate.channel.postgres import PostgresChannel

    with PostgresChannel(pg_ready, run_id=f"proto-{uuid.uuid4()}") as ch:
        assert isinstance(ch, EpisodeHolder)
        assert isinstance(ch, EpisodeProbe)


def test_other_backends_do_not_advertise_episode_capability(tmp_path):
    """memory/sqlite lack the lock capability -- the Watcher's isinstance dispatch
    must distinguish them (no episode probe), not assume every Channel has it."""
    from runstate.channel.base import EpisodeHolder, EpisodeProbe
    from runstate.channel.memory import MemoryChannel
    from runstate.channel.sqlite import SqliteChannel

    assert not isinstance(MemoryChannel(), EpisodeHolder)
    assert not isinstance(MemoryChannel(), EpisodeProbe)
    with SqliteChannel(tmp_path / "r.db") as sch:
        assert not isinstance(sch, EpisodeHolder)
        assert not isinstance(sch, EpisodeProbe)


def test_hold_episode_and_episode_alive_agree(pg_ready):
    """A real holder and an INDEPENDENT observer agree on the episode's liveness --
    the writer/observer key derivation is bit-identical cross-connection (the seeded
    oracle below can't catch a key mismatch; this can)."""
    from runstate.channel.postgres import PostgresChannel

    run_id = f"live-{uuid.uuid4()}"
    holder = PostgresChannel(pg_ready, run_id=run_id)
    observer = PostgresChannel(pg_ready, run_id=run_id)
    try:
        started_seq = holder.send({}, topic="lifecycle.started", expected_seq=0)
        assert observer.episode_alive(started_seq) is False  # not held yet
        holder.hold_episode(started_seq)
        assert observer.episode_alive(started_seq) is True   # held -> alive, synchronously
    finally:
        observer.close()
        holder.close()  # clean disconnect releases the session lock
    with PostgresChannel(pg_ready, run_id=run_id) as after:
        assert _poll_until(lambda: after.episode_alive(started_seq), False)


def test_lock_answers_liveness_where_resolve_abstains(pg_ready):
    """The genuinely cross-host property, tested on one host: a started whose handle
    names ANOTHER host -> ``resolve`` abstains (None, can't probe a foreign pid
    table), but the held lock answers True; release -> False."""
    from runstate.channel.postgres import PostgresChannel
    from runstate.vocabulary.handle import resolve

    run_id = f"xhost-{uuid.uuid4()}"
    foreign = "local://OTHER-HOST/99999"
    assert resolve(foreign) is None  # resolve cannot answer for a foreign host

    ch = PostgresChannel(pg_ready, run_id=run_id)
    holder = PostgresChannel(pg_ready, run_id=run_id)
    try:
        started_seq = ch.send({"handle": foreign, "t": None},
                              topic="lifecycle.started", expected_seq=0)
        holder.hold_episode(started_seq)
        assert ch.episode_alive(started_seq) is True   # the lock answers where resolve can't
        holder.close()                                  # release
        assert _poll_until(lambda: ch.episode_alive(started_seq), False)
    finally:
        ch.close()


def test_sigkill_releases_episode_lock(pg_ready):
    """A HARD kill (no clean disconnect) drops the holder's connection -> the session
    lock releases -> the episode reads dead. Polls until the child has acquired first
    (avoid a kill-before-acquire false pass)."""
    from runstate.channel.postgres import PostgresChannel

    run_id = f"kill-{uuid.uuid4()}"
    ch = PostgresChannel(pg_ready, run_id=run_id)
    started_seq = ch.send({}, topic="lifecycle.started", expected_seq=0)
    proc = mp.get_context("spawn").Process(
        target=_hold_episode_forever, args=(pg_ready, run_id, started_seq)
    )
    proc.start()
    try:
        assert _poll_until(lambda: ch.episode_alive(started_seq), True), "child never acquired"
        proc.kill()  # SIGKILL -- no clean disconnect
        proc.join(timeout=10)
        assert _poll_until(lambda: ch.episode_alive(started_seq), False), "lock not released on kill"
    finally:
        if proc.is_alive():
            proc.kill()
            proc.join()
        ch.close()


# ===================== Watcher wiring (cycle 5) =====================
# The worker pins its episode after the claim; the Watcher consults the lock as a
# tier-3 sibling (independent of a tracked handle), with a birth grace and the
# heartbeat-staleness floor that a held lock never vetoes.


def test_worker_holds_episode_lock_after_claiming(pg_ready):
    """The one additive core touch: a Worker, after winning the claim CAS, pins its
    episode (iff the channel is an EpisodeHolder) -- so an independent observer reads
    the run alive via the lock, cross-host, where os.kill would abstain."""
    from runstate import Worker
    from runstate.channel.postgres import PostgresChannel

    run_id = f"whold-{uuid.uuid4()}"
    wch = PostgresChannel(pg_ready, run_id=run_id)
    obs = PostgresChannel(pg_ready, run_id=run_id)
    try:
        w = Worker(wch, now=lambda: 0.0)
        assert w.claimed
        started_seq = obs.latest("lifecycle.started").seq
        assert obs.episode_alive(started_seq) is True   # the worker holds it
    finally:
        obs.close()
        wch.close()  # releases the lock


def test_watcher_lock_held_is_alive(pg_ready):
    """Lock held -> a definitive alive signal -> the run reports Running (no terminal
    record, no staleness timeout)."""
    from runstate.channel.postgres import PostgresChannel
    from runstate.watcher import Running, Watcher

    run_id = f"wlive-{uuid.uuid4()}"
    obs = PostgresChannel(pg_ready, run_id=run_id)
    holder = PostgresChannel(pg_ready, run_id=run_id)
    try:
        s = obs.send({}, topic="lifecycle.started", expected_seq=0)
        holder.hold_episode(s)
        w = Watcher(now=lambda: 0.0)
        w.observe(run_id, obs)  # handle-free: the lock is the only definitive probe
        assert isinstance(w.poll(run_id), Running)
    finally:
        holder.close()
        obs.close()


def test_watcher_observed_run_presumed_dead_via_lock_past_grace(pg_ready):
    """The motivating path: an ``observe()``-d (handle-free) cross-host run whose lock
    is not held reads inconclusive WITHIN the birth grace (the CAS->hold_episode
    window) but PRESUMED_DEAD past it -- a definitive death where os.kill can't reach."""
    from runstate.channel.postgres import PostgresChannel
    from runstate.observables import Outcome, RunResult
    from runstate.watcher import Watcher

    run_id = f"wdead-{uuid.uuid4()}"
    obs = PostgresChannel(pg_ready, run_id=run_id)
    try:
        obs.send({}, topic="lifecycle.started", expected_seq=0)  # started, but NO lock held
        clock = {"t": 0.0}
        w = Watcher(now=lambda: clock["t"], episode_grace=1.0)
        w.observe(run_id, obs)
        assert w.poll(run_id).done is False              # within grace -> inconclusive
        clock["t"] = 1.5                                 # past the birth grace
        r = w.poll(run_id)
        assert isinstance(r, RunResult)
        assert r.outcome == Outcome.PRESUMED_DEAD
        assert r.reason == "episode_lock_released"
    finally:
        obs.close()


def test_watcher_staleness_floor_is_not_vetoed_by_a_held_lock(pg_ready):
    """The floor: a held lock makes the run *alive* but falls THROUGH to the staleness
    check -- a worker that stopped beating while still connected still goes
    presumed_dead (heartbeat_stale), the dead-vote an alive-probe never overrides."""
    from runstate.channel.postgres import PostgresChannel
    from runstate.observables import Outcome, RunResult
    from runstate.watcher import Watcher

    run_id = f"wfloor-{uuid.uuid4()}"
    obs = PostgresChannel(pg_ready, run_id=run_id)
    holder = PostgresChannel(pg_ready, run_id=run_id)
    try:
        s = obs.send({}, topic="lifecycle.started", expected_seq=0)
        holder.hold_episode(s)                                       # lock HELD (alive)
        obs.send({"step": 0, "consumed_seq": 0, "t": 0.0}, topic="lifecycle.heartbeat")
        clock = {"t": 0.0}
        w = Watcher(now=lambda: clock["t"], heartbeat_timeout=1.0)
        w.observe(run_id, obs)
        w.poll(run_id)                                              # note the beacon at t=0
        clock["t"] = 2.0                                            # beacon now stale
        r = w.poll(run_id)
        assert isinstance(r, RunResult)
        assert r.outcome == Outcome.PRESUMED_DEAD
        assert r.reason == "heartbeat_stale"                        # floor wins over the held lock
    finally:
        holder.close()
        obs.close()


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
        assert len(seqs) == total                       # no lost writes
        assert seqs == list(range(1, total + 1))        # unique + contiguous
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
    seed = ch.send({}, topic="value", name="seed")          # seq 1
    ch._conn.execute("SET lock_timeout = '300ms'")          # keep the wedge wait test-sized
    wedger = psycopg.connect(pg_ready)                      # autocommit=False
    try:
        wedger.execute(  # an uncommitted (run, seed+1) holds the PK index slot
            "INSERT INTO log (run_id, seq, topic, name, request_id, body, created_at)"
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
        if "INSERT" in sql and "COALESCE(MAX" in sql:       # the unconditional append
            raise psycopg.errors.UniqueViolation("simulated persistent contention")
        return real_execute(sql, *a, **k)

    monkeypatch.setattr(ch._conn, "execute", always_conflict)
    try:
        with pytest.raises(RuntimeError, match="exhausted"):
            ch.send({}, topic="value", name="x")            # unconditional
    finally:
        ch.close()
