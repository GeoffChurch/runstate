"""Conformance tests for the v0.2 substrate (a per-run topic log).

Every test runs against all backends via the ``ch`` fixture (see conftest.py).
"""


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
    ch.send({"reason": "completed"}, topic="lifecycle.stopped")
    assert ch.latest("lifecycle.stopped").body == {"reason": "completed"}
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


def test_read_result_is_independent_of_storage(ch):
    ch.send({"v": 1}, topic="value", name="loss")
    got = ch.read()[0]
    got.body["v"] = 999  # mutating a read result must not corrupt the log
    assert ch.read()[0].body == {"v": 1}
    assert ch.latest("value", "loss").body == {"v": 1}


def test_channels_on_the_same_run_share_the_log(open_channel):
    worker = open_channel()
    observer = open_channel()
    worker.send({"reason": "completed"}, topic="lifecycle.stopped")
    assert observer.latest("lifecycle.stopped").body == {"reason": "completed"}
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
