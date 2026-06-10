"""Conformance tests for the v0.2 substrate (a per-run topic log).

Every test runs against all backends via the ``ch`` fixture (see conftest.py).
"""

import threading

import pytest


def test_send_returns_monotonically_increasing_seq(ch):
    s1 = ch.send({"v": 1}, topic="value", name="loss")
    s2 = ch.send({"v": 2}, topic="value", name="loss")
    assert isinstance(s1, int)
    assert s2 > s1


def test_read_returns_envelopes_after_cursor(ch):
    s1 = ch.send({"v": 1}, topic="value", name="loss")
    ch.send({"v": 2}, topic="value", name="loss")

    envs = ch.read(after=0)
    assert [e.body for e in envs] == [{"v": 1}, {"v": 2}]
    assert envs[0].seq == s1
    assert envs[0].topic == "value"
    assert envs[0].name == "loss"
    assert envs[0].request_id is None

    rest = ch.read(after=s1)
    assert [e.body for e in rest] == [{"v": 2}]


def test_read_default_cursor_is_zero(ch):
    ch.send({"v": 1}, topic="value", name="loss")
    assert [e.body for e in ch.read()] == [{"v": 1}]


def test_latest_returns_most_recent_for_topic_and_name(ch):
    ch.send({"v": 1}, topic="value", name="loss")
    ch.send({"v": 2}, topic="value", name="loss")
    ch.send({"v": 9}, topic="value", name="acc")
    assert ch.latest("value", "loss").body == {"v": 2}
    assert ch.latest("value", "acc").body == {"v": 9}
    assert ch.latest("value", "missing") is None


def test_latest_by_topic_only(ch):
    ch.send({"completed": True, "error": None, "final_step": None}, topic="lifecycle.stopped")
    assert ch.latest("lifecycle.stopped").body == {"completed": True, "error": None, "final_step": None}
    assert ch.latest("lifecycle.started") is None


def test_read_filters_by_exact_topics(ch):
    ch.send({}, topic="control.stop")
    ch.send({}, topic="control.subscribe", name="loss")
    ch.send({"v": 1}, topic="value", name="loss")
    got = [e.topic for e in ch.read(topics=["control.stop", "control.subscribe"])]
    assert got == ["control.stop", "control.subscribe"]


def test_read_filters_by_topic_prefix_wildcard(ch):
    ch.send({}, topic="control.stop")
    ch.send({}, topic="control.subscribe", name="loss")
    ch.send({"v": 1}, topic="value", name="loss")
    got = [e.topic for e in ch.read(topics=["control.>"])]
    assert got == ["control.stop", "control.subscribe"]


def test_read_filters_by_name(ch):
    ch.send({"v": 1}, topic="value", name="loss")
    ch.send({"v": 2}, topic="value", name="acc")
    assert [e.body for e in ch.read(name="loss")] == [{"v": 1}]


def test_read_request_ids_filter_includes_unaddressed_broadcasts(ch):
    # visibility: an observer sees its own request_ids PLUS broadcasts (request_id None)
    ch.send({"v": 1}, topic="value", name="loss", request_id="r1")
    ch.send({"v": 2}, topic="value", name="loss", request_id="r2")
    ch.send({"v": 3}, topic="value", name="loss")  # broadcast
    bodies = [e.body for e in ch.read(name="loss", request_ids=["r1"])]
    assert bodies == [{"v": 1}, {"v": 3}]


def test_read_limit(ch):
    for i in range(5):
        ch.send({"i": i}, topic="value", name="loss")
    assert len(ch.read(limit=2)) == 2


def test_reads_are_non_destructive(ch):
    ch.send({"v": 1}, topic="value", name="loss")
    first = [e.body for e in ch.read()]
    second = [e.body for e in ch.read()]
    assert first == second == [{"v": 1}]


def test_body_is_an_immutable_snapshot(ch):
    body = {"v": 1}
    ch.send(body, topic="value", name="loss")
    body["v"] = 999  # mutating the caller's dict must not change the stored record
    assert ch.read()[0].body == {"v": 1}


def test_send_expected_seq_appends_on_match_rejects_on_mismatch(ch):
    s1 = ch.send({"value": 1, "step": 0, "t": 0.0}, topic="value", name="loss")
    # CAS with the correct last seq -> appends, returns the new seq
    s2 = ch.send({"value": 2, "step": 1, "t": 0.0}, topic="value", name="loss",
                 expected_seq=s1)
    assert s2 == s1 + 1
    # CAS with a stale last seq -> rejected (no append), returns None
    rejected = ch.send({"value": 3, "step": 2, "t": 0.0}, topic="value", name="loss",
                       expected_seq=s1)
    assert rejected is None
    assert [e.body["value"] for e in ch.read(topics=["value"])] == [1, 2]


def test_concurrent_cas_admits_exactly_one_winner(open_channel):
    """A concurrent multi-handle CAS must admit exactly one winner.

    ``send(expected_seq=)`` is the primitive the run-episodes self-claim and the
    §12.1 single-spawn guard rest on: of N workers racing to claim a run,
    exactly one wins ``send(..., expected_seq=last)`` and the rest get ``None``.
    The sequential CAS test above can't see the race; this opens N handles on
    the SAME run (separate sqlite connections; the registry-shared memory
    log+lock) and fires them through a barrier. Several trials, because a racy
    CAS only *sometimes* admits >1 winner.
    """
    n = 8
    seed = open_channel()
    for trial in range(10):
        log = seed.read()
        last = log[-1].seq if log else 0  # each trial's winner advances this
        barrier = threading.Barrier(n)
        results: list = [None] * n
        errors: list = []

        def claim(i):
            try:
                ch = open_channel()
                barrier.wait(timeout=10)  # line up so the check+INSERT windows overlap
                results[i] = ch.send(
                    {"who": i}, topic="lifecycle.started", expected_seq=last
                )
            except BaseException as exc:  # a loser must get None back, never an error
                errors.append(exc)
                barrier.abort()  # don't leave peers waiting on a dead participant

        threads = [threading.Thread(target=claim, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        winners = [r for r in results if r is not None]
        # A loser waits out the winner's write lock (busy_timeout) and reads the
        # new seq; it must come back None, never a "database is locked" error.
        assert not errors, f"[trial {trial}] losing claimants raised: {errors!r}"
        assert len(winners) == 1, (
            f"[trial {trial}] expected exactly 1 winner, got {len(winners)}: {results}"
        )


def test_shared_handle_concurrent_sends_are_serialized(ch):
    """ONE channel instance used from several threads must serialize internally.

    This is the ThreadLauncher topology: the worker thread and the orchestrator's
    Watcher hold the SAME handle (launcher.py hands one channel to both sides).
    The multi-handle race test above can't see it — each thread there opens its
    own connection. Here mixed traffic (CAS claims + plain sends) hammers one
    instance: every acknowledged send must actually be in the log (an ack that a
    concurrent CAS's rollback can erase is corruption), at most one CAS wins, and
    nobody sees an error (e.g. sqlite's "cannot start a transaction within a
    transaction" from two CAS sends sharing one connection).
    """
    n = 8
    for trial in range(10):
        log = ch.read()
        last = log[-1].seq if log else 0
        len_before = len(log)
        barrier = threading.Barrier(n)
        results: list = [None] * n
        errors: list = []

        def send(i):
            try:
                barrier.wait(timeout=10)  # overlap the send windows
                if i % 2:
                    results[i] = ch.send({"who": i}, topic="value", name="plain")
                else:
                    results[i] = ch.send(
                        {"who": i}, topic="lifecycle.started", expected_seq=last
                    )
            except BaseException as exc:
                errors.append(exc)
                barrier.abort()  # don't leave peers waiting on a dead participant

        threads = [threading.Thread(target=send, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"[trial {trial}] concurrent sends raised: {errors!r}"
        winners = [results[i] for i in range(0, n, 2) if results[i] is not None]
        # 0 winners is legitimate (a plain send can land first and move MAX(seq))
        assert len(winners) <= 1, f"[trial {trial}] CAS admitted {len(winners)} winners"
        acked = [r for r in results if r is not None]
        assert len(set(acked)) == len(acked), f"[trial {trial}] duplicate seqs: {acked}"
        in_log = {e.seq for e in ch.read()}
        missing = sorted(s for s in acked if s not in in_log)
        assert not missing, f"[trial {trial}] acknowledged sends erased: {missing}"
        # and nothing landed un-acked (a CAS that inserted but reported None)
        assert len(in_log) - len_before == len(acked), (
            f"[trial {trial}] log grew by {len(in_log) - len_before}, acked {len(acked)}"
        )


def test_init_retries_busy_wal_conversion(tmp_path, monkeypatch):
    """sqlite-specific: at db birth, concurrent openers race the one-time WAL
    conversion, whose SHARED->EXCLUSIVE escalation sqlite exempts from the busy
    handler -- so losers see SQLITE_BUSY no matter what busy_timeout says, and
    __init__ must absorb it by retrying (the winner's conversion persists in the
    file, so a retry is a no-op read). Injected: the real collision window is
    microseconds wide.
    """
    import sqlite3

    from runstate.channel import sqlite as sqlite_mod

    real_connect = sqlite3.connect
    busy = sqlite3.OperationalError("database is locked")
    busy.sqlite_errorcode = sqlite3.SQLITE_BUSY
    collisions = {"left": 2}  # first two attempts collide, third succeeds

    class _BusyWalConn:
        def __init__(self, real):
            self._real = real

        def __getattr__(self, attr):
            return getattr(self._real, attr)

        def execute(self, sql, *args):
            if "journal_mode" in sql and collisions["left"] > 0:
                collisions["left"] -= 1
                raise busy
            return self._real.execute(sql, *args)

    monkeypatch.setattr(
        sqlite_mod.sqlite3, "connect", lambda *a, **k: _BusyWalConn(real_connect(*a, **k))
    )
    ch = sqlite_mod.SqliteChannel(tmp_path / "run.db")  # must not raise
    try:
        assert collisions["left"] == 0
        assert ch.send({}, topic="value", name="alive") == 1
    finally:
        ch.close()


def test_cas_wedged_writer_raises_not_false_loss(tmp_path):
    """sqlite-specific: a competing writer that holds the write lock past
    busy_timeout WITHOUT committing leaves the CAS outcome indeterminate. send()
    must surface that as an error, never synthesize a loss -- a holder that later
    rolls back would leave the run claimed by nobody (a silent liveness hole),
    while the raise is loud and retryable.
    """
    import sqlite3

    from runstate.channel.sqlite import SqliteChannel

    ch = SqliteChannel(tmp_path / "run.db")
    seed = ch.send({}, topic="value", name="seed")
    ch._conn.execute("PRAGMA busy_timeout=100")  # keep the wedge wait test-sized
    holder = sqlite3.connect(str(tmp_path / "run.db"))
    try:
        holder.execute("BEGIN IMMEDIATE")  # write lock held, nothing committed
        with pytest.raises(sqlite3.OperationalError) as excinfo:
            ch.send({}, topic="lifecycle.started", expected_seq=seed)
        # pin the errorcode the disambiguation branch keys on
        assert excinfo.value.sqlite_errorcode == sqlite3.SQLITE_BUSY
    finally:
        holder.close()  # implicit rollback of the wedge
        ch.close()


def test_cas_timeout_with_moved_log_is_a_clean_loss(tmp_path):
    """sqlite-specific: busy_timeout exhaustion when the log HAS moved past
    expected_seq is a plain lost claim -> None, same answer as losing the guard.

    The real window (the winner commits between our last busy retry and the
    re-check) is microseconds wide, so the timeout is injected: the guarded
    INSERT raises SQLITE_BUSY while the underlying log has already moved.
    """
    import sqlite3

    from runstate.channel.sqlite import SqliteChannel

    ch = SqliteChannel(tmp_path / "run.db")
    seed = ch.send({}, topic="value", name="seed")
    other = SqliteChannel(tmp_path / "run.db")
    other.send({}, topic="value", name="winner")  # the log moves past `seed`

    real = ch._conn
    busy = sqlite3.OperationalError("database is locked")
    busy.sqlite_errorcode = sqlite3.SQLITE_BUSY

    class _TimesOutOnGuardedInsert:
        def __getattr__(self, attr):
            return getattr(real, attr)

        def execute(self, sql, *args):
            if "INSERT" in sql and "WHERE" in sql:
                raise busy
            return real.execute(sql, *args)

    ch._conn = _TimesOutOnGuardedInsert()
    try:
        assert ch.send({}, topic="lifecycle.started", expected_seq=seed) is None
    finally:
        ch._conn = real
        ch.close()
        other.close()


def test_read_result_is_independent_of_storage(ch):
    ch.send({"v": 1}, topic="value", name="loss")
    got = ch.read()[0]
    got.body["v"] = 999  # mutating a read result must not corrupt the log
    assert ch.read()[0].body == {"v": 1}
    assert ch.latest("value", "loss").body == {"v": 1}


def test_channels_on_the_same_run_share_the_log(open_channel):
    worker = open_channel()
    observer = open_channel()
    worker.send({"completed": True, "error": None, "final_step": None}, topic="lifecycle.stopped")
    assert observer.latest("lifecycle.stopped").body == {"completed": True, "error": None, "final_step": None}
    assert [e.topic for e in observer.read()] == ["lifecycle.stopped"]


def test_sqlite_latest_uses_the_topic_index_not_a_scan(tmp_path):
    # latest(topic) runs on every Watcher poll; it must be an index seek, not a
    # full table scan (regression guard for the (topic, seq) index).
    from runstate.channel.sqlite import SqliteChannel

    ch = SqliteChannel(tmp_path / "run.db")
    plan = ch._conn.execute(
        "EXPLAIN QUERY PLAN SELECT seq FROM log WHERE topic = ?"
        " ORDER BY seq DESC LIMIT 1",
        ("lifecycle.stopped",),
    ).fetchall()
    detail = " ".join(str(row[-1]) for row in plan)
    assert "idx_log_topic_seq" in detail
    assert "SCAN" not in detail.upper()
