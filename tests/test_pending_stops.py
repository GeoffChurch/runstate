"""reference-by-name §5: the Watcher's incremental pending stops equal the pure
fold after every record, on random histories."""

import random

import pytest

from runstate import Watcher, create_channel, undischarged_stops
from runstate.vocabulary.payloads import Topic


def _random_history(rng, n):
    """Stops named and nameless, stoppeds honoring some ids, naks naming an id
    and naks naming none (a worker's refusal of a nameless request), and noise."""
    ids = [f"s{i}" for i in range(6)]
    for _ in range(n):
        kind = rng.choice(
            ["stop", "stop", "nameless", "stopped", "nak", "nameless-nak", "value"]
        )
        if kind == "stop":
            yield {}, Topic.CONTROL_STOP, rng.choice(ids)
        elif kind == "nameless":
            yield {}, Topic.CONTROL_STOP, None
        elif kind == "stopped":
            honored = rng.sample(ids, rng.randint(0, 3))
            body = {
                "completed": False,
                "error": None,
                "final_step": None,
                "t": 0.0,
                "claim_seq": None,
                "honored": honored,
            }
            yield body, Topic.LIFECYCLE_STOPPED, None
        elif kind == "nak":
            yield {
                "reason": "malformed",
                "message": "",
            }, Topic.LIFECYCLE_NAK, rng.choice(ids)
        elif kind == "nameless-nak":
            yield {
                "reason": "malformed",
                "message": "stop requires a request_id",
            }, Topic.LIFECYCLE_NAK, None
        else:
            yield {"value": 1, "step": 0, "t": None}, "value", None


@pytest.mark.parametrize("seed", range(25))
def test_incremental_equals_pure_after_every_record(tmp_path, seed):
    rng = random.Random(seed)
    ch = create_channel(f"r{seed}", root=tmp_path, backend="memory")
    w = Watcher()
    w.observe(f"r{seed}", ch)
    for body, topic, rid in _random_history(rng, 60):
        ch.send(
            body, topic=topic, request_id=rid, name="x" if topic == "value" else None
        )
        if rng.random() < 0.5:  # poll at irregular moments, like a real caller
            got = [e.seq for e in w.pending_stops(f"r{seed}")]
            assert got == [e.seq for e in undischarged_stops(ch)]
    assert [e.seq for e in w.pending_stops(f"r{seed}")] == [
        e.seq for e in undischarged_stops(ch)
    ]


def test_a_spent_id_reused_is_dead_on_arrival(tmp_path):
    ch = create_channel("r", root=tmp_path, backend="memory")
    w = Watcher()
    w.observe("r", ch)
    ch.send({}, topic=Topic.CONTROL_STOP, request_id="s1")
    ch.send(
        {"reason": "malformed", "message": ""},
        topic=Topic.LIFECYCLE_NAK,
        request_id="s1",
    )
    assert w.pending_stops("r") == []
    ch.send({}, topic=Topic.CONTROL_STOP, request_id="s1")
    assert w.pending_stops("r") == [] == undischarged_stops(ch)


def test_reads_only_new_records(tmp_path):
    ch = create_channel("r", root=tmp_path, backend="memory")
    w = Watcher()
    w.observe("r", ch)
    for i in range(100):
        ch.send({}, topic=Topic.CONTROL_STOP, request_id=f"s{i}")
    w.pending_stops("r")
    calls = []
    real_read = ch.read
    ch.read = lambda **kw: (calls.append(kw.get("after")), real_read(**kw))[1]  # type: ignore[method-assign]
    ch.send({}, topic=Topic.CONTROL_STOP, request_id="new")
    assert len(w.pending_stops("r")) == 101
    assert calls == [100]  # one read, starting after everything already seen
