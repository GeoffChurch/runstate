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
)
from runstate._next.vocabulary.coordinate import Commits
from runstate._next.vocabulary.payloads import Outcome, Topic
from runstate._next.worker import Worker
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
