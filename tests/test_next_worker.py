"""The staged 0.4.0 Worker (docs/specs/commits-and-lineage.md §2, §4, §6): the claim,
its position, values and the commit (plan Task 10a); the drain, stops, the
heartbeat, the fence and ``steps()`` (Task 10b). The exit and ``discard()`` are
``tests/test_next_exit.py``'s, subscriptions ``tests/test_next_subscriptions.py``'s.

The tests that involve a compare-and-append are parametrized over the backends by
the ``open_run`` factory, whose handles share one run. Earlier claims, and the
records of other writers, are hand-written 0.4.0 records (``tests/test_next_reads``'
builders), as another implementation would write them.
"""

import pytest

from runstate._next.checkpoint import Every, NoSave
from runstate._next.launcher import report_death
from runstate._next.observables import (
    AtNode,
    LatestClaimHead,
    last_element,
    prefix_len,
    series,
    undischarged_stops,
)
from runstate._next.vocabulary.coordinate import Commits, Target
from runstate._next.vocabulary.payloads import Outcome, Topic
from runstate._next.worker import Worker
from runstate.channel import Channel
from runstate.vocabulary.launch import launch_scope
from tests.test_next_death_reports import dead_handle
from tests.test_next_reads import (
    Rec,
    append,
    beat,
    commit,
    started,
    stopped,
    value,
)


def worker(ch, *, resume_from=None, sources=(), save=NoSave(), now=lambda: 0.0):
    """A Worker with the tests' usual arguments: a fresh start, no sources, no
    saves and a frozen clock."""
    return Worker(
        ch, resume_from=resume_from, sources=list(sources), save=save, now=now
    )


def records(ch, *topics):
    """(seq, topic) of every record on ``topics`` (every record when none)."""
    return [(e.seq, e.topic) for e in ch.read(topics=list(topics) or None)]


def nodes(ch):
    """The bodies of the ``lifecycle.commit`` records, in seq order."""
    return [e.body for e in ch.read(topics=[Topic.LIFECYCLE_COMMIT])]


def end(ch, w):
    """End ``w``'s claim from outside, as a supervisor would with evidence, so a
    next worker can claim. (The exit is Task 11's.)"""
    claim = ch.latest(Topic.LIFECYCLE_STARTED).seq
    report_death(ch, claim, outcome=Outcome.KILLED, evidence="test: ended", force=True)


def conforms(ch):
    """Every record on the log conforms to the 0.4.0 schemas."""
    pytest.importorskip("jsonschema")
    from test_schema_v0_4_0 import record_errors

    bad = {
        e.seq: errs
        for e in ch.read()
        if (
            errs := record_errors(
                {
                    "seq": e.seq,
                    "topic": e.topic,
                    "name": e.name,
                    "request_id": e.request_id,
                    "body": e.body,
                }
            )
        )
    }
    assert bad == {}


# ----- the claim (§4, "Construction", "The claim") -----------------------------------


def test_the_claim_writes_resume_from_sources_and_the_launch_id(open_run):
    ch = open_run()
    with launch_scope("launch-1"):
        w = worker(ch, sources=["lr", "grad_norm"])
    assert w.claimed and w.resume_node is None
    e = open_run().latest(Topic.LIFECYCLE_STARTED)
    assert e.body["handle"].startswith("local://")
    assert {k: v for k, v in e.body.items() if k != "handle"} == {
        "t": 0.0,
        "resume_from": None,
        "sources": ["lr", "grad_norm"],
    }
    assert (e.name, e.request_id) == (None, "launch-1")
    w.commit()
    end(ch, w)

    # a launcher-less claim names no launch, and one that resumes names its node
    node = ch.latest(Topic.LIFECYCLE_COMMIT).seq
    w2 = worker(ch, resume_from=AtNode(node), sources=[])
    e = open_run().latest(Topic.LIFECYCLE_STARTED)
    assert (e.body["resume_from"], e.body["sources"], e.request_id) == (node, [], None)
    assert w2.resume_node == node
    conforms(ch)


def test_the_claim_path_reports_a_dead_held_claim_and_claims(open_run):
    """A held claim whose handle is on this host and probes dead: the claim path
    calls ``report_if_dead`` (§3, "The writers"), which reports it ``lost``, and
    starts again, finding the claim ended."""
    ch = open_run()
    handle = dead_handle()
    body = {"handle": handle, "t": 1.0, "resume_from": None, "sources": []}
    assert ch.send(body, topic=Topic.LIFECYCLE_STARTED) == 1
    w = worker(open_run())
    assert w.claimed
    obs = open_run()
    assert records(obs) == [
        (1, Topic.LIFECYCLE_STARTED),
        (2, Topic.SUPERVISOR_DIED),
        (3, Topic.LIFECYCLE_STARTED),
    ]
    report = obs.read(topics=[Topic.SUPERVISOR_DIED])[0].body
    assert (report["claim_seq"], report["outcome"], report["evidence"]) == (
        1,
        "lost",
        f"probe: {handle}",
    )


def test_a_claim_race_loser_writes_nothing_and_its_commit_says_stop(open_run):
    """A held claim whose handle probes alive (this process): ``report_if_dead``
    reports nothing, and the worker loses the claim. A loser writes nothing,
    explicit calls included, and its ``commit()`` returns True."""
    winner = worker(open_run())
    assert winner.claimed
    loser = worker(open_run())
    assert loser.claimed is False
    obs = open_run()
    before = records(obs)
    loser.emit("loss", 1.0)
    loser.set("lr", 0.1)
    loser.include(1)
    loser.rewind(None)
    assert loser.commit() is True
    assert records(obs) == before == [(1, Topic.LIFECYCLE_STARTED)]


def test_a_worker_that_calls_commit_itself_resumes_from_its_node(open_run):
    """A self-driving worker, which calls ``commit()`` from its own callback,
    resumes from the node its checkpoint names: its first commit extends it, and
    ``Commits()`` continues from the log."""
    ch = open_run()
    w = worker(ch)
    for i in range(3):
        w.emit("loss", float(i))
        w.commit()
    node = ch.latest(Topic.LIFECYCLE_COMMIT).seq
    end(ch, w)

    w2 = worker(open_run(), resume_from=AtNode(node))
    assert w2.resume_node == node
    w2.emit("loss", 3.0)
    w2.commit()
    obs = open_run()
    assert nodes(obs)[-1]["parent"] == node
    assert prefix_len(obs, Commits(), head=LatestClaimHead()) == 4
    assert [e.value for e in series(obs, "loss", head=LatestClaimHead())] == [
        0.0,
        1.0,
        2.0,
        3.0,
    ]


# A claim (1) with two commits (2, 3), then an exit (4) the test chooses.
def _claim_then(exit_rec):
    return [(1, started(None)), (2, commit(1, None, [])), (3, commit(1, 2, []))] + (
        [] if exit_rec is None else [(4, exit_rec)]
    )


def test_latest_claim_head_continues_the_previous_claims_head(open_run):
    ch = open_run()
    append(ch, _claim_then(stopped(1, 3, [])))  # a preempted exit: extendable
    w = worker(ch, resume_from=LatestClaimHead())
    assert w.resume_node == 4
    assert ch.latest(Topic.LIFECYCLE_STARTED).body["resume_from"] == 4
    w.commit()
    assert nodes(ch)[-1]["parent"] == 4


def test_latest_claim_head_after_a_death_report_continues_its_position(open_run):
    """A claim a death report ended, with no exit: its head is its position."""
    ch = open_run()
    rows = _claim_then(None) + [
        (
            4,
            Rec(
                "supervisor.died",
                {"claim_seq": 1, "outcome": "killed", "evidence": "x", "t": 5.0},
                None,
                None,
            ),
        )
    ]
    append(ch, rows)
    w = worker(ch, resume_from=LatestClaimHead())
    assert w.resume_node == 3


def test_latest_claim_head_after_an_error_exit_continues_its_parent(open_run):
    ch = open_run()
    append(ch, _claim_then(stopped(1, 3, None, error="boom")))  # torn
    w = worker(ch, resume_from=LatestClaimHead())
    assert w.resume_node == 3
    assert ch.latest(Topic.LIFECYCLE_STARTED).body["resume_from"] == 3


def test_latest_claim_head_on_the_empty_lineage_starts_fresh(open_run):
    ch = open_run()
    w = worker(ch, resume_from=LatestClaimHead())  # nothing ever claimed
    assert w.resume_node is None
    assert ch.latest(Topic.LIFECYCLE_STARTED).body["resume_from"] is None
    end(ch, w)
    # a commitless claim's head is the empty lineage too
    w2 = worker(ch, resume_from=LatestClaimHead())
    assert w2.claimed and w2.resume_node is None


def test_latest_claim_head_raises_on_a_closed_head(open_run):
    ch = open_run()
    append(ch, _claim_then(stopped(1, 3, [], completed=True)))
    with pytest.raises(ValueError, match="closed"):
        worker(ch, resume_from=LatestClaimHead())
    assert ch.last_seq() == 4  # nothing written


# Two branches from node 3: the first claim's commits 3, 4; a rewind to 3; commit 6.
BRANCHED = [
    (1, started(None)),
    (2, commit(1, None, [])),
    (3, commit(1, 2, [])),
    (4, commit(1, 3, [])),
    (
        5,
        Rec(
            "lifecycle.rewind", {"claim_seq": 1, "resume_from": 3, "t": 3.0}, None, None
        ),
    ),
    (6, commit(1, 3, [])),
    (7, stopped(1, 6, [])),
]


def test_at_node_resumes_and_rewinds_at_x(open_run):
    ch = open_run()
    append(ch, BRANCHED)
    w = worker(ch, resume_from=AtNode(4))  # the abandoned branch
    assert w.resume_node == 4
    assert ch.latest(Topic.LIFECYCLE_STARTED).body["resume_from"] == 4
    w.commit()
    assert nodes(ch)[-1]["parent"] == 4
    assert prefix_len(ch, Commits(), head=LatestClaimHead()) == 4

    w.rewind(AtNode(6))
    assert w.resume_node == 6
    claim = ch.latest(Topic.LIFECYCLE_STARTED).seq
    r = ch.latest(Topic.LIFECYCLE_REWIND)
    assert (r.body, r.name, r.request_id) == (
        {"claim_seq": claim, "resume_from": 6, "t": 0.0},
        None,
        None,
    )
    w.commit()
    assert nodes(ch)[-1]["parent"] == 6
    assert prefix_len(ch, Commits(), head=LatestClaimHead()) == 4
    conforms(ch)


# A log whose seqs are each a kind a resume_from or a rewind may not name.
REFUSED = [
    (1, started(None)),
    (2, value("loss", 0.5)),
    (3, commit(1, None, [2])),
    (4, beat(1)),
    (5, stopped(1, 3, None, error="boom")),  # a torn exit
    (6, started(3)),
    (7, stopped(6, 3, [], completed=True)),  # a closed node
]


@pytest.mark.parametrize(
    "seq, why",
    [
        (7, "closed"),
        (5, "torn"),
        (2, "value"),
        (4, "heartbeat"),
        (6, "started"),
        (99, "no record"),
    ],
)
def test_the_worker_refuses_a_resume_from_or_rewind_naming_a_closed_node_a_torn_exit_a_value_or_a_heartbeat(
    open_run, seq, why
):
    ch = open_run()
    append(ch, REFUSED)
    with pytest.raises(ValueError):
        worker(ch, resume_from=AtNode(seq))
    assert ch.last_seq() == 7  # the claim was never written

    w = worker(ch, resume_from=AtNode(3))
    before = ch.last_seq()
    with pytest.raises(ValueError):
        w.rewind(AtNode(seq))
    assert ch.last_seq() == before  # no rewind was written
    assert w.resume_node == 3


def test_a_rewind_writes_its_record_discards_pending_and_resets_registers(open_run):
    ch = open_run()
    w = worker(ch, sources=["lr"])
    w.emit("loss", 1.0)
    w.commit()
    first = ch.latest(Topic.LIFECYCLE_COMMIT).seq
    w.emit("loss", 2.0)
    w.commit()
    w.set("lr", 0.1)
    w.emit("loss", 99.0)  # pending at the rewind: discarded
    discarded = ch.last_seq()
    w.rewind(AtNode(first))
    assert ch.latest(Topic.LIFECYCLE_REWIND).body["resume_from"] == first
    # the register holds no value at the new position until the next set (§4)
    assert w._registers == {}
    w.commit()
    assert nodes(ch)[-1]["values"] == []  # the discarded value is in no node
    assert nodes(ch)[-1]["parent"] == first
    loss = [e.value for e in series(ch, "loss", head=LatestClaimHead())]
    assert loss == [1.0]
    assert ch.read(after=discarded - 1, limit=1)[0].body["value"] == 99.0


def test_rewind_none_reaches_the_root(open_run):
    ch = open_run()
    w = worker(ch)
    w.emit("loss", 1.0)
    w.commit()
    w.commit()
    w.rewind(None)
    assert w.resume_node is None
    assert ch.latest(Topic.LIFECYCLE_REWIND).body["resume_from"] is None
    w.commit()
    assert nodes(ch)[-1]["parent"] is None
    assert prefix_len(ch, Commits(), head=LatestClaimHead()) == 1
    assert prefix_len(ch, "loss", head=LatestClaimHead()) == 0


# ----- values and the commit (§4, "Values", "commit()") ------------------------------


def test_an_emitted_value_is_committed_once_by_the_next_commit(open_run):
    ch = open_run()
    w = worker(ch, now=lambda: 1000.0)
    w.emit("loss", 0.5)
    v = ch.latest(Topic.VALUE)
    # sent at once, with the absolute wall clock: never birth-relative
    assert (v.body, v.name, v.request_id) == ({"value": 0.5, "t": 1000.0}, "loss", None)
    claim = ch.latest(Topic.LIFECYCLE_STARTED).seq
    assert w.commit() is False
    w.commit()
    assert nodes(ch) == [
        {
            "claim_seq": claim,
            "consumed_seq": 0,
            "parent": None,
            "values": [v.seq],
            "fired": [],
            "expired": [],
            "t": 1000.0,
        },
        {
            "claim_seq": claim,
            "consumed_seq": 0,
            "parent": v.seq + 1,
            "values": [],
            "fired": [],
            "expired": [],
            "t": 1000.0,
        },
    ]
    assert [e.value for e in series(ch, "loss", head=LatestClaimHead())] == [0.5]
    conforms(ch)


def test_two_emits_of_one_name_in_one_iteration_are_two_elements(open_run):
    """Review Focus 1 (§2, "Order within a node"): unlike 0.3.0's (name, step)
    cell, which held one value, the second emit does not replace the first."""
    ch = open_run()
    w = worker(ch)
    w.emit("loss", 1.0)
    w.emit("loss", 2.0)
    w.commit()
    assert prefix_len(ch, "loss", head=LatestClaimHead()) == 2
    assert [e.value for e in series(ch, "loss", head=LatestClaimHead())] == [1.0, 2.0]
    assert last_element(ch, "loss", head=LatestClaimHead()).value == 2.0


def test_declared_kinds_and_set_emit_include_misuse_raise(open_run):
    ch = open_run()
    with pytest.raises(ValueError):
        worker(ch, sources=["lr", "lr"])  # sources are unique
    with pytest.raises(ValueError):
        worker(ch, sources=[""])  # and non-empty
    assert ch.last_seq() == 0

    w = worker(ch, sources=["lr"])
    with pytest.raises(ValueError, match="source"):
        w.emit("lr", 0.1)  # a source is sampled on demand, never emitted
    with pytest.raises(ValueError, match="source"):
        w.set("loss", 0.5)  # an undeclared name is eager: emit it
    w.set("lr", 0.1)
    w.emit("loss", 0.5)
    w.commit()
    assert ch.read(topics=[Topic.VALUE]) == [ch.latest(Topic.VALUE)]  # set sent none
    assert prefix_len(ch, "loss", head=LatestClaimHead()) == 1


def test_include_commits_a_value_sent_through_another_handle(open_run):
    ch = open_run()
    w = worker(ch)
    other = open_run()
    seq = other.send({"value": 0.5, "t": 1.0}, topic=Topic.VALUE, name="loss")
    w.emit("loss", 0.25)
    emitted = ch.last_seq()
    w.include(seq)
    w.commit()
    assert nodes(ch)[-1]["values"] == [emitted, seq]  # in the order it added them
    assert [e.value for e in series(ch, "loss", head=LatestClaimHead())] == [
        0.25,
        0.5,
    ]


def _include_case(ch, case):
    """Build ``case``'s log and worker; return the worker and the seq it includes."""
    if case == "committed on the lineage by another claim":
        # A claim (1) that a report ended (2) keeps writing after the next claim
        # (3): its commit (5) names a value (4) above that claim, and the next
        # claim rewinds onto it.
        died = {"claim_seq": 1, "outcome": "killed", "evidence": "x", "t": 1.0}
        append(ch, [(1, started(None)), (2, Rec("supervisor.died", died, None, None))])
        w = worker(ch)
        append(ch, [(4, value("loss", 0.5)), (5, commit(1, None, [4]))])
        w.rewind(AtNode(5))
        return w, 4
    w = worker(ch, sources=["lr"])
    if case == "not a value":
        w.commit()
        return w, ch.last_seq()
    if case == "no record":
        return w, ch.last_seq() + 5
    if case == "a null name":
        return w, ch.send({"value": 1, "t": 1.0}, topic=Topic.VALUE)
    if case == "a request_id":
        return w, ch.send(
            {"value": 1, "t": 1.0}, topic=Topic.VALUE, name="loss", request_id="r"
        )
    if case == "at or below the claim":
        end(ch, w)
        seq = ch.send({"value": 1, "t": 1.0}, topic=Topic.VALUE, name="loss")
        return worker(ch), seq
    if case == "pending":
        w.emit("loss", 1.0)
        return w, ch.last_seq()
    if case == "committed on the lineage by this claim":
        w.emit("loss", 1.0)
        seq = ch.last_seq()
        w.commit()
        return w, seq
    if case == "discarded at a rewind":
        w.emit("loss", 1.0)
        seq = ch.last_seq()
        w.rewind(None)
        return w, seq
    if case == "a declared source":
        return w, ch.send({"value": 1, "t": 1.0}, topic=Topic.VALUE, name="lr")
    raise AssertionError(case)


@pytest.mark.parametrize(
    "case",
    [
        "not a value",
        "no record",
        "a null name",
        "a request_id",
        "at or below the claim",
        "pending",
        "committed on the lineage by this claim",
        "committed on the lineage by another claim",
        "discarded at a rewind",
        "a declared source",
    ],
)
def test_include_refuses_each_unmet_condition(open_run, case):
    ch = open_run()
    w, seq = _include_case(ch, case)
    with pytest.raises(ValueError):
        w.include(seq)
    w.commit()
    # the include added nothing: a pending value is committed once
    assert nodes(ch)[-1]["values"] == ([seq] if case == "pending" else [])


def test_an_unserializable_value_raises_naming_its_name(open_run):
    w = worker(open_run())
    with pytest.raises(TypeError, match="'loss'.*JSON-serializable"):
        w.emit("loss", object())


def test_json_default_coerces_an_emitted_value(tmp_path):
    from runstate.channel import create_channel

    ch = create_channel("r", root=tmp_path, backend="sqlite", json_default=sorted)
    w = worker(ch)
    w.emit("loss", {3, 1, 2})
    assert ch.latest(Topic.VALUE).body["value"] == [1, 2, 3]
    ch.close()


# ----- the save policy (§6, "Save") ---------------------------------------------------


class Recording:
    """A save policy that records each call and the log's tail at that moment."""

    def __init__(self, ch):
        self.ch = ch
        self.calls = []

    def after(self, node, *, commits, exit):
        self.calls.append((node, commits, exit, self.ch.last_seq()))


def test_the_save_policy_runs_after_each_commit_lands(open_run):
    ch = open_run()
    save = Recording(open_run())
    w = worker(ch, save=save)
    for i in range(3):
        w.emit("loss", float(i))
        w.commit()
    commits = [e.seq for e in ch.read(topics=[Topic.LIFECYCLE_COMMIT])]
    # each call names the commit that just landed, the log's newest record then
    assert save.calls == [(c, i + 1, False, c) for i, c in enumerate(commits)]

    # the count is the node's count(Commits()), across a rewind and a resume
    w.rewind(AtNode(commits[0]))
    w.commit()
    assert save.calls[-1][1] == 2
    end(ch, w)
    saved = []
    w2 = worker(ch, resume_from=AtNode(commits[-1]), save=Every(2, saved.append))
    w2.commit()  # the 4th iteration on its lineage: a multiple of 2
    w2.commit()
    assert saved == [ch.read(topics=[Topic.LIFECYCLE_COMMIT])[-2].seq]


def test_every_checks_its_k():
    for k in (0, -1, True, 1.5):
        with pytest.raises(ValueError):
            Every(k, print)
    Every(1, print)


# ----- the drain, stops and the heartbeat (§4, "A drain", "The stop decision") -----


class Clock:
    """A clock a test moves."""

    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t


def send_stop(ch, request_id, **trigger):
    return ch.send(trigger, topic=Topic.CONTROL_STOP, request_id=request_id)


def send_withdraw(ch, request_id):
    return ch.send({"t": 1.0}, topic=Topic.CONTROL_WITHDRAW, request_id=request_id)


def naks(ch):
    """(request_id, reason) of every ``lifecycle.nak``, in seq order."""
    return [
        (e.request_id, e.body["reason"]) for e in ch.read(topics=[Topic.LIFECYCLE_NAK])
    ]


def test_a_heartbeat_commits_nothing_and_splits_no_iteration(open_run):
    ch = open_run()
    w = worker(ch, now=lambda: 7.0)
    w.heartbeat()  # nothing to drain yet: the watermark is 0
    claim = ch.latest(Topic.LIFECYCLE_STARTED).seq
    hb = ch.latest(Topic.LIFECYCLE_HEARTBEAT)
    assert (hb.body, hb.name, hb.request_id) == (
        {"claim_seq": claim, "consumed_seq": 0, "t": 7.0},
        None,
        None,
    )
    w.emit("loss", 1.0)
    stop = send_stop(open_run(), "s", **{"from": {"commits": 99}})
    assert w.heartbeat() is False
    # the watermark is the control record it drained
    assert ch.latest(Topic.LIFECYCLE_HEARTBEAT).body["consumed_seq"] == stop
    w.emit("loss", 2.0)
    w.commit()
    assert nodes(ch)[-1]["parent"] is None  # the heartbeats are no nodes
    assert len(nodes(ch)[-1]["values"]) == 2  # one iteration, both values
    assert nodes(ch)[-1]["consumed_seq"] == stop
    assert prefix_len(ch, Commits(), head=LatestClaimHead()) == 1
    conforms(ch)


def test_heartbeat_drains_a_stop_and_returns_true_mid_iteration(open_run):
    ch = open_run()
    w = worker(ch)
    assert w.heartbeat() is False
    send_stop(open_run(), "s")  # no from: it holds once drained
    w.emit("loss", 1.0)
    assert w.heartbeat() is True
    assert w.stop_pending is True
    assert nodes(ch) == []  # mid-iteration: nothing committed


def test_a_time_from_coming_due_inside_an_iteration_lands_at_a_heartbeat(open_run):
    ch = open_run()
    clock = Clock()
    w = worker(ch, now=clock)
    send_stop(open_run(), "s", **{"from": {"time_seconds": 10}})
    assert w.commit() is False  # drained and registered at t=0
    clock.t = 5.0
    assert w.heartbeat() is False
    clock.t = 10.0
    assert w.heartbeat() is True  # due inside the iteration: no node needed
    assert w.commit() is True


def test_the_stop_decision_is_a_monotone_level_across_a_rewind(open_run):
    """Once a pending stop holds, the decision is True for the rest of the claim,
    a rewind to a position where its ``from`` would not hold included (§4). Each
    stop is evaluated at the node being written, and two stops are an any-join."""
    ch = open_run()
    obs = open_run()
    send_stop(obs, "late", **{"from": {"commits": 5}})
    send_stop(obs, "s", **{"from": {"commits": 2}})
    w = worker(ch)
    assert w.commit() is False  # count 1 at the node being written
    assert w.commit() is True  # count 2: "s" holds; "late" did not clobber it
    assert w.commit() is True  # a level, not a pulse
    w.rewind(None)
    assert w.stop_pending is True
    assert w.heartbeat() is True
    assert w.commit() is True  # count 1 again, and still True


def test_stop_pending_reads_the_decision_without_side_effects(open_run):
    ch = open_run()
    send_stop(open_run(), "s", **{"from": {"commits": 2}})
    w = worker(ch)
    assert w.stop_pending is False  # nothing drained yet
    assert w.commit() is False
    assert w.stop_pending is False  # the decision at the last node
    before = ch.last_seq()
    assert w.stop_pending is False
    assert ch.last_seq() == before  # reading it writes nothing
    assert w.commit() is True
    assert w.stop_pending is True
    assert w.stop_pending is True  # and consumes nothing
    assert w.commit() is True


def test_a_withdrawn_stop_is_spent_before_it_holds(open_run):
    ch = open_run()
    obs = open_run()
    send_stop(obs, "s", **{"from": {"commits": 2}})
    w = worker(ch)
    assert w.commit() is False  # drained, pending
    send_withdraw(obs, "s")
    assert w.commit() is False  # count 2, but the withdrawal spent it
    assert w.commit() is False
    send_stop(obs, "s")  # a spent id is dead on arrival
    assert w.heartbeat() is False
    assert naks(ch) == []


def test_the_claim_folds_the_answers_so_a_spent_stop_is_never_drained(open_run):
    """The claim folds the answers by name, for the whole log (§4, "The claim",
    step 1): a stop an exit honored, a withdrawal named or a nak refused is spent
    wherever the answer sits, a withdrawal before its stop included."""
    ch = open_run()
    obs = open_run()
    append(obs, [(1, started(None))])
    send_stop(obs, "honored")
    send_withdraw(obs, "early")  # before the stop it names
    send_stop(obs, "early")
    send_stop(obs, "refused")
    obs.send(
        {"reason": "malformed", "message": "no"},
        topic=Topic.LIFECYCLE_NAK,
        request_id="refused",
    )
    append(obs, [(7, stopped(1, None, [], honored=["honored"]))])
    send_stop(obs, "honored")  # a re-send of a spent id
    w = worker(ch)
    assert w.heartbeat() is False
    assert w.commit() is False
    assert naks(ch) == [("refused", "malformed")]  # the worker wrote none


@pytest.mark.parametrize(
    "topic, request_id, body, reason",
    [
        ("control.stop", "s", {"from": {"commits": "oops"}}, "malformed"),
        ("control.stop", "s", {"from": {"bogus": 1}}, "malformed"),
        ("control.stop", "s", {"every": {"commits": 1}}, "malformed"),
        ("control.stop", "s", {"until": {"commits": 9}}, "malformed"),
        ("control.stop", "s", {"bogus": 1}, "malformed"),
        ("control.stop", None, {}, "malformed"),  # a stop is named by its id
        ("control.stop", "s", {"from": {"stream": "lr", "n": 1}}, "unsatisfiable"),
        ("control.frobnicate", "s", {}, "unsupported"),
    ],
)
def test_a_bad_stop_or_control_verb_is_refused_and_never_pending(
    open_run, topic, request_id, body, reason
):
    """A request the worker cannot honor is refused by a ``nak`` naming it, which
    spends it, and is never pending: one bad request is never fatal, and does not
    stop the run (§3, the refusals; §4, "A drain")."""
    ch = open_run()
    obs = open_run()
    seq = obs.send(body, topic=topic, request_id=request_id)
    w = worker(ch, sources=["lr"])
    assert w.heartbeat() is False
    assert w.commit() is False  # not poisoned
    assert naks(ch) == [(request_id, reason)]
    assert undischarged_stops(obs) == []  # the nak answers it, by name
    assert ch.latest(Topic.LIFECYCLE_HEARTBEAT).body["consumed_seq"] == seq


# ----- steps(until) (§4, "The drivers") ----------------------------------------------


def test_steps_continues_from_the_log_after_a_resume(open_run):
    """The yielded index is ``count(Commits(), position)`` before each commit: a
    resumed driver continues from the log, run-absolute, and a rewind inside the
    body moves the next index (0.3.0's ``start=`` is removed)."""
    ch = open_run()
    w = worker(ch)
    assert list(w.steps(Target(Commits(), 3))) == [0, 1, 2]
    node = ch.latest(Topic.LIFECYCLE_COMMIT).seq
    end(ch, w)

    first = ch.read(topics=[Topic.LIFECYCLE_COMMIT], limit=1)[0].seq
    w2 = worker(open_run(), resume_from=AtNode(node))
    seen, rewound = [], False
    for i in w2.steps(Target(Commits(), 5)):
        seen.append(i)
        if i == 4 and not rewound:
            w2.rewind(AtNode(first))  # back to count 1: its commit makes 2
            rewound = True
    assert seen == [3, 4, 2, 3, 4]


def test_steps_ends_at_the_target_or_a_stop(open_run):
    ch = open_run()
    obs = open_run()
    w = worker(ch)
    assert list(w.steps(Target(Commits(), 0))) == []  # reached at once
    assert nodes(ch) == []
    for i in w.steps(Target("loss", 3)):
        w.emit("loss", float(i))
        if i == 0:
            w.emit("loss", 0.5)  # two elements in one iteration
    assert prefix_len(ch, "loss", head=LatestClaimHead()) == 3
    assert prefix_len(ch, Commits(), head=LatestClaimHead()) == 2

    send_stop(obs, "s", **{"from": {"commits": 4}})
    assert list(w.steps(None)) == [2, 3]  # ends on the stop, at count 4
    assert list(w.steps(None)) == []  # the decision is a level


def test_steps_refuses_a_target_naming_a_declared_source(open_run):
    """Review Focus 2 (§4, "The drivers"; plan, open question 1): a source's count
    is 0 forever, so such a target would drive without end. Raised at the call,
    before the first iteration."""
    ch = open_run()
    w = worker(ch, sources=["lr"])
    before = ch.last_seq()
    with pytest.raises(ValueError, match="source"):
        w.steps(Target("lr", 1))
    assert ch.last_seq() == before
    assert list(w.steps(Target("loss", 0))) == []  # an eager stream is fine


# ----- the fence (§4, "A released worker fences itself") ----------------------------


def died_rec(claim):
    """A death report's body naming ``claim``."""
    return {"claim_seq": claim, "outcome": "killed", "evidence": "test: x", "t": 1.0}


class Interloping(Channel):
    """A channel handle on which, once ``arm``ed, a record lands through another
    handle just before the next compare-and-append: that CAS loses, and its
    writer must read again."""

    def __init__(self, inner, other):
        self._inner, self._other, self._armed = inner, other, None
        self.fired = False

    def arm(self, body, *, topic, request_id=None):
        self._armed = (body, topic, request_id)

    def send(self, body, *, topic, name=None, request_id=None, expected_seq=None):
        if expected_seq is not None and self._armed is not None:
            (armed, armed_topic, armed_id), self._armed = self._armed, None
            self._other.send(armed, topic=armed_topic, request_id=armed_id)
            self.fired = True
        return self._inner.send(
            body,
            topic=topic,
            name=name,
            request_id=request_id,
            expected_seq=expected_seq,
        )

    def read(self, after=0, *, topics=None, name=None, request_ids=None, limit=None):
        return self._inner.read(
            after, topics=topics, name=name, request_ids=request_ids, limit=limit
        )

    def latest(self, topic, name=None):
        return self._inner.latest(topic, name)

    def last_seq(self):
        return self._inner.last_seq()

    def close(self):
        self._inner.close()


@pytest.mark.parametrize(
    "topic, body",
    [
        ("control.stop", {"from": {"bogus": 1}}),  # malformed
        ("control.stop", {"from": {"stream": "lr", "n": 1}}),  # unsatisfiable
        ("control.frobnicate", {}),  # unsupported
    ],
)
def test_a_stop_refusal_is_cas_and_a_death_report_landing_first_leaves_it_unrefused(
    open_run, topic, body
):
    """A refusal is judged against the claim's declaration, and a ``nak`` names no
    claim, so a released worker must not write one (§4, "A drain"). Its CAS at the
    drain's tail loses to the report, and the drain run again reads the report
    first and refuses nothing: the request is left for a successor."""
    obs = open_run()
    ch = Interloping(open_run(), obs)
    w = worker(ch, sources=["lr"])
    claim = obs.latest(Topic.LIFECYCLE_STARTED).seq
    obs.send(body, topic=topic, request_id="r")
    ch.arm(died_rec(claim), topic=Topic.SUPERVISOR_DIED)
    assert w.heartbeat() is True
    assert ch.fired
    assert w.released is True
    assert naks(obs) == []
    assert records(obs)[-1][1] == Topic.SUPERVISOR_DIED  # nothing after the report


def test_a_drain_that_loses_a_refusals_cas_refuses_once(open_run):
    """A lost CAS re-runs the drain from the record it refused (§4, "A drain"): a
    record that lands first (here a heartbeat of another writer) is read, and the
    refusal is written once, at the new tail."""
    obs = open_run()
    ch = Interloping(open_run(), obs)
    w = worker(ch)
    obs.send({}, topic="control.frobnicate", request_id="a")
    obs.send({}, topic="control.frobnicate", request_id="b")
    ch.arm({"value": 1, "t": 1.0}, topic=Topic.VALUE)
    assert w.heartbeat() is False
    assert ch.fired
    assert naks(obs) == [("a", "unsupported"), ("b", "unsupported")]


def test_a_released_worker_writes_nothing_more(open_run):
    """A drain reads the death reports naming its claim first; once one does, the
    worker latches (§4): every method that writes writes nothing, ``commit()`` and
    ``heartbeat()`` return True, ``stop_pending`` is True, and misuse no longer
    raises, since it cannot know when the report landed."""
    ch = open_run()
    obs = open_run()
    w = worker(ch, sources=["lr"])
    w.emit("loss", 1.0)
    pending = ch.last_seq()
    w.commit()
    claim = obs.latest(Topic.LIFECYCLE_STARTED).seq
    obs.send(died_rec(claim), topic=Topic.SUPERVISOR_DIED)
    send_stop(obs, "s")  # meant for the successor
    assert w.released is False  # not read yet
    assert w.heartbeat() is True
    assert w.released is True
    before = records(obs)
    assert w.commit() is True
    assert w.heartbeat() is True
    assert w.stop_pending is True
    w.emit("lr", 1.0)  # misuse, silently nothing
    w.set("loss", 1.0)
    w.include(pending)
    w.rewind(None)
    assert list(w.steps(None)) == []
    assert records(obs) == before
    assert [e.request_id for e in undischarged_stops(obs)] == ["s"]
