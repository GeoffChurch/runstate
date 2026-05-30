"""The v0.2 substrate: a per-run, append-only topic log (SqliteChannel)."""

from runstate.channel.sqlite import SqliteChannel


def test_send_returns_monotonically_increasing_seq(tmp_path):
    ch = SqliteChannel(tmp_path / "run.db")
    s1 = ch.send({"v": 1}, topic="value", name="loss")
    s2 = ch.send({"v": 2}, topic="value", name="loss")
    assert isinstance(s1, int)
    assert s2 > s1


def test_read_returns_envelopes_after_cursor(tmp_path):
    ch = SqliteChannel(tmp_path / "run.db")
    s1 = ch.send({"v": 1}, topic="value", name="loss")
    ch.send({"v": 2}, topic="value", name="loss")

    envs = ch.read(after=0)
    assert [e.body for e in envs] == [{"v": 1}, {"v": 2}]
    assert envs[0].seq == s1
    assert envs[0].topic == "value"
    assert envs[0].name == "loss"
    assert envs[0].request_id is None

    # caller-owned cursor: read strictly after the first envelope
    rest = ch.read(after=s1)
    assert [e.body for e in rest] == [{"v": 2}]


def test_read_default_cursor_is_zero(tmp_path):
    ch = SqliteChannel(tmp_path / "run.db")
    ch.send({"v": 1}, topic="value", name="loss")
    assert [e.body for e in ch.read()] == [{"v": 1}]


def test_latest_returns_most_recent_for_topic_and_name(tmp_path):
    ch = SqliteChannel(tmp_path / "run.db")
    ch.send({"v": 1}, topic="value", name="loss")
    ch.send({"v": 2}, topic="value", name="loss")
    ch.send({"v": 9}, topic="value", name="acc")
    assert ch.latest("value", "loss").body == {"v": 2}
    assert ch.latest("value", "acc").body == {"v": 9}
    assert ch.latest("value", "missing") is None


def test_latest_by_topic_only(tmp_path):
    ch = SqliteChannel(tmp_path / "run.db")
    ch.send({"reason": "completed"}, topic="lifecycle.stopped")
    assert ch.latest("lifecycle.stopped").body == {"reason": "completed"}
    assert ch.latest("lifecycle.started") is None


def test_read_filters_by_exact_topics(tmp_path):
    ch = SqliteChannel(tmp_path / "run.db")
    ch.send({}, topic="control.stop")
    ch.send({}, topic="control.subscribe", name="loss")
    ch.send({"v": 1}, topic="value", name="loss")
    got = [e.topic for e in ch.read(topics=["control.stop", "control.subscribe"])]
    assert got == ["control.stop", "control.subscribe"]


def test_read_filters_by_topic_prefix_wildcard(tmp_path):
    ch = SqliteChannel(tmp_path / "run.db")
    ch.send({}, topic="control.stop")
    ch.send({}, topic="control.subscribe", name="loss")
    ch.send({"v": 1}, topic="value", name="loss")
    got = [e.topic for e in ch.read(topics=["control.>"])]
    assert got == ["control.stop", "control.subscribe"]


def test_read_filters_by_name(tmp_path):
    ch = SqliteChannel(tmp_path / "run.db")
    ch.send({"v": 1}, topic="value", name="loss")
    ch.send({"v": 2}, topic="value", name="acc")
    assert [e.body for e in ch.read(name="loss")] == [{"v": 1}]


def test_read_request_ids_filter_includes_unaddressed_broadcasts(tmp_path):
    # visibility: an observer sees its own request_ids PLUS broadcasts (request_id None)
    ch = SqliteChannel(tmp_path / "run.db")
    ch.send({"v": 1}, topic="value", name="loss", request_id="r1")
    ch.send({"v": 2}, topic="value", name="loss", request_id="r2")
    ch.send({"v": 3}, topic="value", name="loss")  # broadcast
    bodies = [e.body for e in ch.read(name="loss", request_ids=["r1"])]
    assert bodies == [{"v": 1}, {"v": 3}]


def test_read_limit(tmp_path):
    ch = SqliteChannel(tmp_path / "run.db")
    for i in range(5):
        ch.send({"i": i}, topic="value", name="loss")
    assert len(ch.read(limit=2)) == 2


def test_reads_are_non_destructive(tmp_path):
    ch = SqliteChannel(tmp_path / "run.db")
    ch.send({"v": 1}, topic="value", name="loss")
    first = [e.body for e in ch.read()]
    second = [e.body for e in ch.read()]
    assert first == second == [{"v": 1}]
