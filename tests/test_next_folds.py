"""The incremental folds, staged (commits-and-lineage.md §5, "Bounded reads"; plan
Task 8): each held fold equals its pure read after every record, and a poll reads
only the records above its cursor, so its cost does not grow with the lineage.

Ported from ``tests/test_pending_stops.py``, whose fold was the Watcher's
``pending_stops``; ``PendingStops`` is that fold, public and standalone.
"""

import random

import pytest

from runstate._next.observables import (
    Answers,
    AtNode,
    HeadFold,
    LastElement,
    LatestClaimHead,
    LiveDemand,
    MalformedRecordError,
    PendingStops,
    PrefixLen,
    answers,
    last_element,
    live_demand,
    prefix_len,
    undischarged_stops,
)
from runstate._next.vocabulary.coordinate import Commits
from runstate.channel.memory import MemoryChannel
from tests.lineage_logs import OUTPUT as LINEAGE_LOGS
from tests.test_migration_goldens import OUTPUT as GOLDEN_LOGS
from tests.test_next_reads import (
    ANSWERED,
    LOGS as READS_LOGS,
    TWICE,
    commit,
    started,
    stop,
    subscribe,
    value,
    withdraw,
)


def _logs():
    """Every log as (seq, topic, name, request_id, body) rows."""
    out = {}
    for label, rows in LINEAGE_LOGS.items():
        out[f"lineage:{label}"] = rows
    for label, rows in GOLDEN_LOGS.items():
        out[f"golden:{label}"] = rows
    for label, rows in READS_LOGS.items():
        out[f"reads:{label}"] = [
            (seq, r.topic, r.name, r.request_id, r.body) for seq, r in rows
        ]
    return out


LOGS = _logs()

# The one check a fold does not make (§5, "Well-formedness": "Checked by the pure
# walk only: a seq committed twice along one lineage. In a fold it would cost
# O(depth) per value."). On this log alone the pure reads through the duplicate
# raise where the folds answer.
_PURE_WALK_ONLY = {
    f"reads:{label}" for label, rows in READS_LOGS.items() if rows is TWICE
}


def _outcome(read):
    """A read's value, or the seq it raised at: a fold and its pure read agree on
    both."""
    try:
        return ("value", read())
    except MalformedRecordError as exc:
        return ("raised", exc.seq)


def _agree(label, fold, pure):
    """A fold equals its pure read, values and raises alike (§5: "a fold raises
    wherever its pure read raises"), but where the pure walk's own check raises
    on the log ``_PURE_WALK_ONLY`` exempts."""
    held = _outcome(fold)
    try:
        wanted = ("value", pure())
    except MalformedRecordError as exc:
        if label in _PURE_WALK_ONLY and "committed twice" in exc.detail:
            return True
        wanted = ("raised", exc.seq)
    return held == wanted


@pytest.mark.parametrize("label", list(LOGS))
def test_every_fold_equals_its_pure_read_after_every_record(label):
    """Replayed record by record, each held fold, polled after each record, equals
    its pure read then: ``HeadFold`` and ``LatestClaimHead``, ``PrefixLen`` and
    ``prefix_len``, ``LastElement`` and ``last_element``, ``Answers`` and
    ``answers`` (at the newest claim's head and at every node, so a head that does
    not descend from the last re-derives), ``LiveDemand`` and ``live_demand``,
    ``PendingStops`` and ``undischarged_stops``. Every log, the malformed ones
    included: where the pure read raises, the fold raises at the same record,
    but for the pure walk's own check (``_PURE_WALK_ONLY``). Each stream fold is
    also asked about ``loss``, so a log that commits a non-value and no value of
    any name is still read for one."""
    rows = LOGS[label]
    names = sorted(
        {"loss"} | {name for _, topic, name, _, _ in rows if topic == "value" and name}
    )
    rids = sorted(
        {rid for _, topic, _, rid, _ in rows if topic == "control.subscribe" and rid}
    )
    head_fold = HeadFold()
    counts = {x: PrefixLen(x) for x in [Commits(), *names]}
    lasts = {name: LastElement(name) for name in names}
    answered = {rid: Answers(rid) for rid in rids}
    demand, stops = LiveDemand(), PendingStops()
    ch = MemoryChannel()
    nodes = []
    for seq, topic, name, rid, body in rows:
        assert ch.send(body, topic=topic, name=name, request_id=rid) == seq
        if topic in ("lifecycle.commit", "lifecycle.stopped"):
            nodes.append(seq)
        held = _outcome(lambda: head_fold.poll(ch))
        assert held == _outcome(lambda: LatestClaimHead().resolve(ch)), seq
        heads = [] if held[0] == "raised" else [held[1]]
        for n in nodes:
            at = _outcome(lambda: AtNode(n).resolve(ch))
            if at[0] == "value":
                heads.append(at[1])
        for h in heads:
            for x, fold in counts.items():
                assert _agree(
                    label, lambda: fold.poll(ch, h), lambda: prefix_len(ch, x, head=h)
                ), (seq, h, x)
            for name, fold in lasts.items():
                assert _agree(
                    label,
                    lambda: fold.poll(ch, h),
                    lambda: last_element(ch, name, head=h),
                ), (seq, h, name)
            for r, fold in answered.items():
                assert _agree(
                    label, lambda: fold.poll(ch, h), lambda: answers(ch, r, head=h)
                ), (seq, h, r)
        assert _agree(label, lambda: demand.poll(ch), lambda: live_demand(ch)), seq
        assert _agree(label, lambda: stops.poll(ch), lambda: undischarged_stops(ch))


class _Counting(MemoryChannel):
    """A channel that counts the rows its reads return."""

    def __init__(self):
        super().__init__()
        self.rows = 0

    def read(self, after=0, **kw):
        got = super().read(after, **kw)
        self.rows += len(got)
        return got

    def latest(self, topic, name=None):
        self.rows += 1
        return super().latest(topic, name)


def _send(ch, rec):
    return ch.send(rec.body, topic=rec.topic, name=rec.name, request_id=rec.request_id)


def _iteration(ch, claim, parent):
    """One iteration: a loss, q's sample of lr, and the commit that fires q."""
    v = _send(ch, value("loss", 0.5))
    s = _send(ch, value("lr", 0.1))
    return _send(ch, commit(claim, parent, [v, s], fired=["q"]))


@pytest.mark.parametrize("commits", [10, 10_000])
def test_polled_reads_stay_within_a_bound_as_the_lineage_grows(commits):
    """§5's criterion for a polled read: its cost is independent of lineage length
    and log age, given held state. After 10 and after 10,000 commits, each with a
    stop and its withdrawal, a poll of every fold after one more iteration (three
    records) reads a few rows, the same few, where any pure read would read the
    lineage, and a rescan of the stop fold would read every stop."""
    ch = _Counting()
    claim = _send(ch, started(None, sources=["lr"]))
    _send(ch, subscribe("lr", "q", every={"commits": 1}))
    _send(ch, subscribe(None, "p", until={"time_seconds": 3600}))
    _send(ch, stop("halt"))
    parent = None
    for i in range(commits):
        parent = _iteration(ch, claim, parent)
        _send(ch, stop(f"s{i}"))  # many stops, each spent: a rescan reads them all
        _send(ch, withdraw(f"s{i}"))
    head_fold = HeadFold()
    folds = [PrefixLen("loss"), PrefixLen(Commits()), LastElement("loss"), Answers("q")]
    demand, stops = LiveDemand(), PendingStops()

    def poll():
        h = head_fold.poll(ch)
        return h, [f.poll(ch, h) for f in folds], demand.poll(ch), stops.poll(ch)

    poll()  # the first poll reads everything once
    _iteration(ch, claim, parent)
    ch.rows = 0
    h, (loss, iterations, last, sampled, *_), wanted, pending = poll()
    assert ch.rows <= 30
    assert (loss, iterations, last.seq) == (commits + 1, commits + 1, h.node - 2)
    assert len(sampled) == commits + 1 and sampled[-1].node == h.node
    assert [e.request_id for e in wanted] == ["q", "p"]
    assert [e.request_id for e in pending] == ["halt"]


def test_the_answers_fold_rederives_on_a_head_that_does_not_descend():
    """Held along a branch, it extends; at a head on another branch it re-derives
    and returns the full list, as a callback could not retract an answer; back on
    the first, it re-derives again."""
    ch = MemoryChannel()
    for _, r in ANSWERED:
        _send(ch, r)
    fold = Answers("q")
    first = answers(ch, "q", head=AtNode(8))
    assert fold.poll(ch, AtNode(8).resolve(ch)) == first
    on_11 = fold.poll(ch, AtNode(11).resolve(ch))  # descends from 8: extends
    assert on_11 == answers(ch, "q", head=AtNode(11)) and on_11[: len(first)] == first
    on_15 = fold.poll(ch, AtNode(15).resolve(ch))  # the rewound branch
    assert on_15 == answers(ch, "q", head=AtNode(15))
    assert on_15[-1].node == 15 and 11 not in [e.node for e in on_15]
    assert fold.poll(ch, AtNode(11).resolve(ch)) == on_11
    s = _send(ch, value("lr", 0.03))
    n = _send(ch, commit(1, 15, [s], fired=["q"]))
    extended = fold.poll(ch, AtNode(15).resolve(ch))
    assert extended == on_15  # the held head's list, unchanged by a later node
    on_n = fold.poll(ch, LatestClaimHead().resolve(ch))  # descends from 15: extends
    assert on_n == [*on_15, answers(ch, "q", head=AtNode(n))[-1]]


# ----- PendingStops, ported from tests/test_pending_stops.py -------------------------


def _random_history(rng, n):
    """Stops named and nameless, a claim's exits honoring some ids, naks naming an
    id and naks naming none, withdrawals, and noise."""
    ids = [f"s{i}" for i in range(6)]
    yield {"handle": "local://h/1", "t": 0.0, "resume_from": None, "sources": []}, (
        "lifecycle.started"
    ), None
    for _ in range(n):
        kind = rng.choice(
            ["stop", "stop", "nameless", "stopped", "nak", "nameless-nak", "withdraw"]
            + ["value"]
        )
        if kind == "stop":
            yield {}, "control.stop", rng.choice(ids)
        elif kind == "nameless":
            yield {}, "control.stop", None
        elif kind == "stopped":
            body = {
                "completed": False,
                "error": None,
                "claim_seq": 1,
                "honored": rng.sample(ids, rng.randint(0, 3)),
                "parent": None,
                "values": [],
                "fired": [],
                "expired": [],
                "t": 0.0,
            }
            yield body, "lifecycle.stopped", None
        elif kind == "nak":
            yield {"reason": "malformed", "message": ""}, "lifecycle.nak", rng.choice(
                ids
            )
        elif kind == "nameless-nak":
            yield {"reason": "malformed", "message": ""}, "lifecycle.nak", None
        elif kind == "withdraw":
            yield {"t": 0.0}, "control.withdraw", rng.choice(ids)
        else:
            yield {"value": 1, "t": None}, "value", None


@pytest.mark.parametrize("seed", range(25))
def test_pending_stops_equals_the_pure_fold_on_random_histories(seed):
    rng = random.Random(seed)
    ch = MemoryChannel()
    fold = PendingStops()
    for body, topic, rid in _random_history(rng, 60):
        ch.send(
            body, topic=topic, request_id=rid, name="x" if topic == "value" else None
        )
        if rng.random() < 0.5:  # poll at irregular moments, like a real caller
            assert fold.poll(ch) == undischarged_stops(ch)
    assert fold.poll(ch) == undischarged_stops(ch)


def test_a_spent_stop_id_reused_is_dead_on_arrival():
    ch = MemoryChannel()
    fold = PendingStops()
    ch.send({}, topic="control.stop", request_id="s1")
    ch.send(
        {"reason": "malformed", "message": ""}, topic="lifecycle.nak", request_id="s1"
    )
    assert fold.poll(ch) == []
    ch.send({}, topic="control.stop", request_id="s1")
    assert fold.poll(ch) == [] == undischarged_stops(ch)


def test_pending_stops_reads_only_new_records():
    ch = MemoryChannel()
    fold = PendingStops()
    for i in range(100):
        ch.send({}, topic="control.stop", request_id=f"s{i}")
    fold.poll(ch)
    calls = []
    real_read = ch.read
    ch.read = lambda after=0, **kw: (calls.append(after), real_read(after, **kw))[1]
    ch.send({}, topic="control.stop", request_id="new")
    assert len(fold.poll(ch)) == 101
    assert calls == [100]  # one read, starting after everything already seen
