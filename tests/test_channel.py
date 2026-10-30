"""Conformance tests for the v0.2 substrate (a per-run topic log).

Every test runs against all backends via the ``ch`` fixture (see conftest.py).
"""

import pytest


def test_send_returns_monotonically_increasing_seq(ch):
    s1 = ch.send({"v": 1}, topic="value", name="loss")
    s2 = ch.send({"v": 2}, topic="value", name="loss")
    assert isinstance(s1, int)
    assert s2 > s1


def test_seq_is_contiguous_and_one_based(ch):
    # The substrate contract: seq is contiguous, 1-based, per log -- on every
    # backend, not an autoincrement accident. Interleaved reads must not
    # perturb it (reads are non-destructive, never log events).
    seqs = [ch.send({"i": 0}, topic="value", name="loss")]
    for i in range(1, 5):
        ch.read()
        ch.latest("value")
        seqs.append(ch.send({"i": i}, topic="value", name="loss"))
    assert seqs == [1, 2, 3, 4, 5]
    assert [e.seq for e in ch.read()] == [1, 2, 3, 4, 5]


def test_last_seq_is_the_cas_read_half(ch):
    # The fifth op (§4, the admission principle: the surface must be readable
    # in every coordinate it requires callers to assert): 0 = empty (the CAS
    # base case), == the last returned seq, == the record count (contiguity);
    # reads never move it, and asserting it is sufficient to win the CAS.
    assert ch.last_seq() == 0
    s1 = ch.send({"i": 0}, topic="value", name="loss")
    assert ch.last_seq() == s1 == 1
    s2 = ch.send({"i": 1}, topic="value", name="loss")
    assert ch.last_seq() == s2 == 2
    ch.read()
    ch.latest("value")
    assert ch.last_seq() == 2
    assert ch.send({"i": 2}, topic="value", expected_seq=ch.last_seq()) == 3


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
    ch.send(
        {"completed": True, "error": None, "final_step": None, "t": 0.0},
        topic="lifecycle.stopped",
    )
    assert ch.latest("lifecycle.stopped").body == {
        "completed": True,
        "error": None,
        "final_step": None,
        "t": 0.0,
    }
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


def test_read_with_empty_topics_list_returns_empty(ch):
    # topics=[] means "among these zero topics" -- vacuously none, on every
    # backend (not an SQL error from an empty OR-clause).
    ch.send({"v": 1}, topic="value", name="loss")
    assert ch.read(topics=[]) == []


def test_wildcard_prefix_is_literal_not_metacharacters(ch):
    # The pattern grammar is exact-or-".>"-prefix; everything before ".>" is
    # LITERAL. A backend's pattern operator (sqlite GLOB, postgres LIKE) must
    # not let its metacharacters leak into topic matching.
    for topic in ("a?b.x", "aXb.x", "a_b.x", "a%b.x", "a*b.x", "a[b.x"):
        ch.send({}, topic=topic)
    for pattern, hit in (
        ("a?b.>", "a?b.x"),
        ("a_b.>", "a_b.x"),
        ("a%b.>", "a%b.x"),
        ("a*b.>", "a*b.x"),
        ("a[b.>", "a[b.x"),
    ):
        assert [e.topic for e in ch.read(topics=[pattern])] == [hit]


def test_exact_topic_filter_with_metacharacters_is_literal(ch):
    ch.send({}, topic="a*b")
    ch.send({}, topic="aXb")
    ch.send({}, topic="a%b")
    assert [e.topic for e in ch.read(topics=["a*b"])] == ["a*b"]
    assert [e.topic for e in ch.read(topics=["a%b"])] == ["a%b"]


def test_topic_matching_is_case_sensitive(ch):
    ch.send({}, topic="Control.stop")
    ch.send({}, topic="control.stop")
    assert [e.topic for e in ch.read(topics=["control.>"])] == ["control.stop"]
    assert [e.topic for e in ch.read(topics=["Control.stop"])] == ["Control.stop"]


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


def test_read_with_empty_request_ids_returns_only_broadcasts(ch):
    # request_ids=[] means "zero ids of my own": visibility admits ONLY the
    # unaddressed broadcasts, identically on every backend. Pinned explicitly --
    # sqlite's empty "IN ()" is a SQLite-only grammar extension and an empty
    # array parameter's adaptation is driver-dependent, so the backends carry
    # an explicit branch instead of leaning on either.
    ch.send({"v": 1}, topic="value", name="loss", request_id="r1")
    s2 = ch.send({"v": 2}, topic="value", name="loss")  # broadcast
    assert [e.seq for e in ch.read(request_ids=[])] == [s2]


def test_read_limit(ch):
    for i in range(5):
        ch.send({"i": i}, topic="value", name="loss")
    assert len(ch.read(limit=2)) == 2


def test_read_limit_zero_returns_nothing(ch):
    ch.send({"i": 0}, topic="value", name="loss")
    assert ch.read(limit=0) == []


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
    s2 = ch.send(
        {"value": 2, "step": 1, "t": 0.0}, topic="value", name="loss", expected_seq=s1
    )
    assert s2 == s1 + 1
    # CAS with a stale last seq -> rejected (no append), returns None
    rejected = ch.send(
        {"value": 3, "step": 2, "t": 0.0}, topic="value", name="loss", expected_seq=s1
    )
    assert rejected is None
    assert [e.body["value"] for e in ch.read(topics=["value"])] == [1, 2]


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
        sqlite_mod.sqlite3,
        "connect",
        lambda *a, **k: _BusyWalConn(real_connect(*a, **k)),
    )
    ch = sqlite_mod.SqliteChannel(tmp_path / "run.db")  # must not raise
    try:
        assert collisions["left"] == 0
        assert ch.send({}, topic="value", name="alive") == 1
    finally:
        ch.close()


def test_journal_mode_defaults_to_wal(tmp_path, monkeypatch):
    """sqlite-specific: with no override the channel opens WAL (fast local
    concurrent reads -- the orchestrator polls while the worker writes)."""
    from runstate.channel.sqlite import SqliteChannel

    monkeypatch.delenv("RUNSTATE_SQLITE_JOURNAL_MODE", raising=False)
    ch = SqliteChannel(tmp_path / "run.db")
    try:
        assert ch._conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        ch.close()


def test_journal_mode_reads_env_when_unset(tmp_path, monkeypatch):
    """sqlite-specific: the deployment knob. RUNSTATE_SQLITE_JOURNAL_MODE sets the
    mode for every channel in the process, so an NFS cluster exports DELETE once
    instead of threading a param everywhere. Mode names are case-insensitive."""
    from runstate.channel.sqlite import SqliteChannel

    monkeypatch.setenv("RUNSTATE_SQLITE_JOURNAL_MODE", "delete")
    ch = SqliteChannel(tmp_path / "run.db")
    try:
        assert ch._conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    finally:
        ch.close()


def test_journal_mode_rejects_unknown_value(tmp_path, monkeypatch):
    """sqlite-specific: an unrecognized mode is a config error surfaced loudly at
    open, not an opaque sqlite failure later nor a silent fall-through to default."""
    from runstate.channel.sqlite import SqliteChannel

    monkeypatch.setenv("RUNSTATE_SQLITE_JOURNAL_MODE", "bogus")
    with pytest.raises(ValueError, match="journal"):
        SqliteChannel(tmp_path / "run.db")


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


def test_channels_on_the_same_run_share_the_log(open_run):
    worker = open_run()
    observer = open_run()
    worker.send(
        {"completed": True, "error": None, "final_step": None, "t": 0.0},
        topic="lifecycle.stopped",
    )
    assert observer.latest("lifecycle.stopped").body == {
        "completed": True,
        "error": None,
        "final_step": None,
        "t": 0.0,
    }
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


def test_sqlite_latest_with_name_uses_the_name_index_not_a_partition_walk(tmp_path):
    # latest(topic, name=) must SEEK on the name, not post-filter it: a rare,
    # early-only or not-yet-emitted name otherwise walks the whole topic
    # partition on every call, forever (#19).
    from runstate.channel.sqlite import SqliteChannel

    ch = SqliteChannel(tmp_path / "run.db")
    plan = ch._conn.execute(
        "EXPLAIN QUERY PLAN SELECT seq FROM log WHERE topic = ? AND name = ?"
        " ORDER BY seq DESC LIMIT 1",
        ("value", "loss"),
    ).fetchall()
    detail = " ".join(str(row[-1]) for row in plan)
    assert "idx_log_topic_name_seq" in detail
    assert "topic=? AND name=?" in detail  # the name is IN the seek, not after it
    assert "SCAN" not in detail.upper()


def test_sqlite_name_index_does_not_displace_the_topic_index(tmp_path):
    # The two indexes are an ADDITION, not a replacement. (topic, name, seq)
    # cannot serve the name-less latest(topic) -- `name` sits between the
    # equality prefix and the sort key, so ORDER BY seq falls back to a temp
    # B-tree (measured ~2000x worse on 300k records). latest(topic) runs on
    # every Watcher poll, so dropping (topic, seq) would be a severe regression
    # that only shows up under load.
    from runstate.channel.sqlite import SqliteChannel

    ch = SqliteChannel(tmp_path / "run.db")
    names = {
        r[0]
        for r in ch._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='log'"
        )
    }
    assert {"idx_log_topic_seq", "idx_log_topic_name_seq"} <= names

    plan = " ".join(
        str(row[-1])
        for row in ch._conn.execute(
            "EXPLAIN QUERY PLAN SELECT seq FROM log WHERE topic = ?"
            " ORDER BY seq DESC LIMIT 1",
            ("value",),
        )
    )
    assert "TEMP B-TREE" not in plan.upper()  # the tell that the sort is unserved


# --- the memory backend's slice and index (Review Focus 5) ---
# The memory log reads a tail as a slice (seq = index + 1) and answers ``latest``
# from a per-topic index. Every in-process run reads through it (the
# ThreadLauncher's default), and an off-by-one in a slice is silent: the reads
# return envelopes of the right shape, one short or one over. So the two are
# pinned against a full scan of what was sent, computed here and not by the backend.

_TOPICS = ["value", "control.stop", "lifecycle.heartbeat", "control.subscribe"]
_NAMES = [None, "loss", "acc"]
_RIDS = [None, "r1", "r2", None]


def _scan(sent, after, *, topics, name, request_ids, limit):
    def topic_ok(topic):
        if topics is None:
            return True
        return any(
            topic.startswith(p[:-1]) if p.endswith(".>") else topic == p for p in topics
        )

    out = [
        e
        for e in sent
        if e.seq > after
        and topic_ok(e.topic)
        and (name is None or e.name == name)
        and (request_ids is None or e.request_id is None or e.request_id in request_ids)
    ]
    return out if limit is None else out[:limit]


def test_memory_reads_and_latest_agree_with_a_full_scan_at_every_cursor(tmp_path):
    from runstate.channel import Envelope, create_channel

    a = create_channel("run", root=tmp_path, backend="memory")
    b = create_channel("run", root=tmp_path, backend="memory")
    sent = []
    for i in range(23):  # periods 4, 3 and 4 interleave every topic with every name
        handle = (a, b)[i % 2]
        topic, name, rid = _TOPICS[i % 4], _NAMES[i % 3], _RIDS[(i // 2) % 4]
        if i % 5 == 0:  # some by compare-and-append, which takes the same tail
            seq = handle.send(
                {"i": i}, topic=topic, name=name, request_id=rid, expected_seq=i
            )
        else:
            seq = handle.send({"i": i}, topic=topic, name=name, request_id=rid)
        assert seq == i + 1
        sent.append(Envelope(seq, topic, name, rid, {"i": i}))

    filters = [
        dict(topics=t, name=n, request_ids=r)
        for t in (None, ["value"], ["control.>"], ["value", "lifecycle.heartbeat"], [])
        for n in (None, "loss")
        for r in (None, ["r1"], [])
    ]
    for handle in (a, b):
        assert handle.last_seq() == len(sent)
        assert handle.read() == sent
        for after in range(
            -1, handle.last_seq() + 2
        ):  # -1: seq > after, not a slice from the end
            for f in filters:
                for limit in (0, 1, None):
                    got = handle.read(after, limit=limit, **f)
                    assert got == _scan(sent, after, limit=limit, **f), (
                        after,
                        f,
                        limit,
                    )
        for topic in _TOPICS + ["absent"]:
            for name in _NAMES + ["absent"]:
                match = [
                    e
                    for e in sent
                    if e.topic == topic and (name is None or e.name == name)
                ]
                assert handle.latest(topic, name) == (match[-1] if match else None)


def test_a_memory_log_refuses_envelopes_that_are_not_contiguous_from_1():
    from runstate.channel.envelope import Envelope
    from runstate.channel.memory import MemoryChannel, MemoryLog

    def env(*seqs):
        return [Envelope(s, "value", "n", None, {"s": s}) for s in seqs]

    for bad in (env(2), env(1, 3), env(2, 1), env(1, 1), env(0, 1)):
        with pytest.raises(ValueError, match="contiguous"):
            MemoryLog(bad)
    ch = MemoryChannel(MemoryLog(env(1, 2, 3)))
    assert [e.seq for e in ch.read()] == [1, 2, 3]
    assert ch.latest("value", "n") == env(3)[0]
    assert ch.send({"s": 4}, topic="value", name="n", expected_seq=3) == 4
    assert MemoryChannel(MemoryLog([])).last_seq() == 0
