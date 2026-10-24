"""The pure 0.4.0 reads: the head, the lineage, counts and series
(commits-and-lineage.md §2, §5; §10's "Nodes and commits", "Position",
"Settledness" and "Reads", the pure subset).

Every log here is hand-written 0.4.0 (lifecycle-v0.6, subscription-v0.4,
value-v0.3) in a MemoryChannel, and goes through ``_log``, so
``test_every_log_is_valid_0_4_0`` checks each against the 0.4.0 schemas: every
row is valid except the rows ``UNREADABLE`` names, which a test makes
unreadable on purpose. The migration step's goldens are real 0.4.0 logs too,
and the last tests read some as the corpus runner will."""

from typing import Any, NamedTuple

import pytest

from runstate.channel.memory import MemoryChannel
from runstate.lineage import (
    AtNode,
    Commits,
    Element,
    Head,
    LatestClaimHead,
    _Commit,
    _Rewind,
    _Started,
    _Stopped,
    _Value,
    count,
    last_element,
    lineage,
    prefix_len,
    series,
)
from runstate.observables import MalformedRecordError


class Rec(NamedTuple):
    topic: str
    body: dict[str, Any]
    name: str | None
    request_id: str | None


def started(resume_from, *, sources=(), t=1.0):
    body = {
        "handle": "local://n1/11",
        "t": t,
        "resume_from": resume_from,
        "sources": list(sources),
    }
    return Rec("lifecycle.started", body, None, None)


def commit(claim, parent, values, *, fired=(), t=2.0):
    body = {
        "claim_seq": claim,
        "consumed_seq": 0,
        "parent": parent,
        "values": list(values),
        "fired": list(fired),
        "expired": [],
        "t": t,
    }
    return Rec("lifecycle.commit", body, None, None)


def rewind(claim, resume_from, *, t=3.0):
    body = {"claim_seq": claim, "resume_from": resume_from, "t": t}
    return Rec("lifecycle.rewind", body, None, None)


def stopped(claim, parent, values, *, completed=False, error=None, t=4.0):
    body = {
        "completed": completed,
        "error": error,
        "claim_seq": claim,
        "honored": [],
        "parent": parent,
        "values": None if values is None else list(values),
        "fired": [],
        "expired": [],
        "t": t,
    }
    return Rec("lifecycle.stopped", body, None, None)


def beat(claim, *, t=2.5):
    body = {"claim_seq": claim, "consumed_seq": 0, "t": t}
    return Rec("lifecycle.heartbeat", body, None, None)


def value(name, v, *, t=1.5):
    return Rec("value", {"value": v, "t": t}, name, None)


def subscribe(source, request_id):
    return Rec("control.subscribe", {}, source, request_id)


def append(ch, rows):
    for seq, r in rows:
        assert (
            ch.send(r.body, topic=r.topic, name=r.name, request_id=r.request_id) == seq
        )


def channel(rows):
    ch = MemoryChannel()
    append(ch, rows)
    return ch


LOGS: dict[str, list[tuple[int, Rec]]] = {}


def _log(label, *rows):
    LOGS[label] = list(rows)
    return LOGS[label]


# ----- the logs ---------------------------------------------------------------------

# A resumes from nothing, B from A's extendable exit, then rewinds to A's
# second commit and closes the new branch.
RESUMED = _log(
    "resumed",
    (1, started(None)),  # claim A, a fresh start
    (2, value("loss", 0.9)),
    (3, commit(1, None, [2])),  # iteration 1
    (4, value("loss", 0.8)),
    (5, commit(1, 3, [4])),  # iteration 2
    (6, value("loss", 0.7)),  # a post-loop value
    (7, stopped(1, 5, [6])),  # A's preempted exit: extendable
    (8, started(7)),  # claim B resumes from the exit
    (9, value("loss", 0.6)),
    (10, commit(8, 7, [9])),  # iteration 3, through the exit
    (11, rewind(8, 5)),  # back to A's iteration 2
    (12, value("loss", 0.5)),
    (13, commit(8, 5, [12])),  # iteration 3 again, on a new branch
    (14, stopped(8, 13, [], completed=True)),  # closed
)

# A dies torn; B resumes from A's commit and has committed nothing yet.
COMMITLESS = _log(
    "commitless",
    (1, started(None)),
    (2, value("loss", 0.9)),
    (3, commit(1, None, [2])),
    (4, value("loss", 0.8)),  # pending at the error exit
    (5, stopped(1, 3, None, error="CUDA out of memory")),  # torn
    (6, started(3)),  # B, loading
    (7, value("status", "loading")),  # a register: no node names it
    (8, beat(6)),
)
COMMITLESS_ERRORED = _log(
    "commitless, then errored",
    *COMMITLESS,
    (9, stopped(6, 3, None, error="OSError")),
)

# B claims over A and resumes from A's commit; A runs on and completes.
DISPLACED = _log(
    "displaced",
    (1, started(None)),  # A
    (2, value("loss", 0.9)),
    (3, commit(1, None, [2])),
    (4, started(3)),  # B
    (5, value("loss", 0.8)),  # A's, still running
    (6, commit(1, 3, [5])),  # names A's claim
    (7, stopped(1, 6, [], completed=True)),  # A's completed exit
)

TO_THE_ROOT = _log(
    "a rewind to the root",
    (1, started(None)),
    (2, value("loss", 0.9)),
    (3, commit(1, None, [2])),
    (4, rewind(1, None)),
)
FROM_THE_ROOT = _log(
    "a commit after a rewind to the root",
    *TO_THE_ROOT,
    (5, value("loss", 0.8)),
    (6, commit(1, None, [5])),
)

EXIT_AFTER_A_REWIND = _log(
    "an exit after a rewind",
    (1, started(None)),
    (2, commit(1, None, [])),
    (3, commit(1, 2, [])),
    (4, rewind(1, 2)),
    (5, stopped(1, 2, [])),  # the position is the rewind's target
)

UNCOMMITTED = _log(
    "uncommitted values",
    (1, started(None)),
    (2, value("loss", 0.9)),
    (3, commit(1, None, [2])),
    (4, value("loss", 0.8)),  # pending at the rewind: discarded
    (5, rewind(1, 3)),
    (6, value("loss", 0.7)),
    (7, commit(1, 3, [6])),
    (8, stopped(1, 7, [], completed=True)),
    (9, value("status", "done")),  # after the exit
)

IN_LINEAGE_ORDER = _log(
    "in lineage order",
    (1, started(None)),
    (2, value("loss", 0.9)),
    (3, value("loss", 0.8)),
    (4, value("loss", 0.7)),
    (5, commit(1, None, [4, 2])),  # the writer's own order: 4, then 2
    (6, commit(1, 5, [3])),  # an older value, committed later
)

# lr is A's source, sampled when q fires, and an eager stream of B's.
KIND_CHANGED = _log(
    "a name that changed kind",
    (1, started(None, sources=["lr"])),
    (2, subscribe("lr", "q")),
    (3, value("lr", 0.1)),  # A's sample
    (4, value("loss", 0.9)),
    (5, commit(1, None, [4, 3], fired=["q"])),  # the pending values, then samples
    (6, stopped(1, 5, [])),
    (7, started(6)),  # B declares no source
    (8, value("lr", 0.05)),
    (9, commit(7, 6, [8])),
)

TWO_BRANCHES = _log(
    "one value on two branches",
    (1, started(None)),
    (2, value("loss", 0.9)),
    (3, commit(1, None, [2])),
    (4, rewind(1, None)),
    (5, commit(1, None, [2])),  # committed again, on another lineage
)

# --- malformed, each at its stated check (§5, "Well-formedness")

LATER_VALUE = _log(
    "values names a later seq",
    (1, started(None)),
    (2, commit(1, None, [3])),
    (3, value("loss", 0.9)),
    (4, commit(1, 2, [])),
)
LATER_PARENT = _log(
    "a parent at a later seq",
    (1, started(None)),
    (2, commit(1, None, [])),
    (3, commit(1, 4, [])),  # names a later, extendable node
    (4, commit(1, 2, [])),
    (5, commit(1, 3, [])),
)
RESUME_LATER = _log(
    "a started resuming from a later node",
    (1, started(None)),
    (2, commit(1, None, [])),
    (3, started(4)),
    (4, commit(1, 2, [])),  # the displaced worker's
)
REWIND_LATER = _log(
    "a rewind to a later node",
    (1, started(None)),
    (2, commit(1, None, [])),
    (3, rewind(1, 4)),
    (4, commit(1, 2, [])),
)
PARENT_A_VALUE = _log(
    "a parent naming a value",
    (1, started(None)),
    (2, value("loss", 0.9)),
    (3, commit(1, 2, [])),
)
PARENT_CLOSED = _log(
    "a parent naming a closed exit",
    (1, started(None)),
    (2, commit(1, None, [])),
    (3, stopped(1, 2, [], completed=True)),
    (4, started(None)),
    (5, commit(4, 3, [])),
)
PARENT_TORN = _log(
    "a parent naming a torn exit",
    (1, started(None)),
    (2, commit(1, None, [])),
    (3, stopped(1, 2, None, error="boom")),
    (4, started(None)),
    (5, commit(4, 3, [])),
)
RESUME_CLOSED = _log(
    "a resume_from naming a closed exit",
    (1, started(None)),
    (2, commit(1, None, [])),
    (3, stopped(1, 2, [], completed=True)),
    (4, started(3)),
)
RESUME_TORN = _log(
    "a resume_from naming a torn exit",
    (1, started(None)),
    (2, commit(1, None, [])),
    (3, stopped(1, 2, None)),  # torn: it discarded its open iteration
    (4, started(3)),
)
RESUME_A_HEARTBEAT = _log(
    "a resume_from naming a heartbeat",
    (1, started(None)),
    (2, beat(1)),
    (3, started(2)),
)
REWIND_TO_A_VALUE = _log(
    "a rewind naming a value",
    (1, started(None)),
    (2, value("loss", 0.9)),
    (3, rewind(1, 2)),
)
ROLLBACK = _log(
    "a rollback written as a commit with no rewind",
    (1, started(None)),
    (2, commit(1, None, [])),
    (3, commit(1, 2, [])),
    (4, commit(1, 2, [])),  # the claim's position is 3
)
EXIT_BEFORE_THE_REWIND = _log(
    "an exit whose parent is the position before a rewind",
    (1, started(None)),
    (2, commit(1, None, [])),
    (3, commit(1, 2, [])),
    (4, rewind(1, 2)),
    (5, stopped(1, 3, [])),
)
TWICE = _log(
    "a seq committed twice along one lineage",
    (1, started(None)),
    (2, value("loss", 0.9)),
    (3, commit(1, None, [2])),
    (4, commit(1, 3, [2])),
)
NAMES_A_HEARTBEAT = _log(
    "values names a heartbeat",
    (1, started(None)),
    (2, beat(1)),
    (3, commit(1, None, [2])),
)
BAD_VALUE = Rec("value", {"value": 0.9, "step": 0, "t": 2.0}, "loss", None)
BAD_BODY = _log(
    "a malformed value body",
    (1, started(None)),
    (2, BAD_VALUE),
    (3, value("loss", 0.8)),
    (4, commit(1, None, [2, 3])),
)
BAD_BODY_LAST = _log(
    "a malformed value body, last",
    (1, started(None)),
    (2, BAD_VALUE),
    (3, value("loss", 0.8)),
    (4, commit(1, None, [3, 2])),
)
CLAIM_LATER = _log(
    "a node naming a later claim",
    (1, started(None)),
    (2, value("loss", 0.9)),
    (3, commit(4, None, [2])),  # a record follows what it names
    (4, started(3)),
    (5, commit(4, 3, [])),
)
CLAIM_NOT_A_CLAIM = _log(
    "a node naming a value as its claim",
    (1, started(None)),
    (2, value("loss", 0.9)),
    (3, commit(2, None, [2])),
)

# --- the head fold's decisions where §2 and §5 say "at most one" or say nothing

UNREADABLE_EXIT = _log(
    "an unreadable exit",
    (1, started(None)),
    (2, commit(1, None, [])),
    (
        3,
        Rec(
            "lifecycle.stopped",
            {**stopped(1, 2, []).body, "completed": "yes"},
            None,
            None,
        ),
    ),
)
TWO_EXITS = _log(
    "two exits of one claim",
    (1, started(None)),
    (2, commit(1, None, [])),
    (3, stopped(1, 2, [])),
    (4, stopped(1, 2, [], completed=True)),
)
AFTER_THE_EXIT = _log(
    "a commit after its claim's exit",
    (1, started(None)),
    (2, commit(1, None, [])),
    (3, stopped(1, 2, [])),
    (4, commit(1, 2, [])),
)
NAMES_NO_CLAIM = _log(
    "a commit naming no claim",
    (1, started(None)),
    (2, commit(1, None, [])),
    (
        3,
        Rec(
            "lifecycle.commit",
            {k: v for k, v in commit(1, 2, []).body.items() if k != "claim_seq"},
            None,
            None,
        ),
    ),
)
ANOTHER_CLAIMS_JUNK = _log(
    "another claim's unreadable commit",
    *DISPLACED[:4],
    (5, Rec("lifecycle.commit", {**commit(1, 3, []).body, "values": "x"}, None, None)),
)
UNREADABLE_CLAIM = _log(
    "an unreadable claim",
    (1, Rec("lifecycle.started", {"handle": "local://n1/11", "t": 1.0}, None, None)),
)

UNREADABLE = {
    "a malformed value body": {2},
    "a malformed value body, last": {2},
    "an unreadable exit": {3},
    "a commit naming no claim": {3},
    "another claim's unreadable commit": {5},
    "an unreadable claim": {1},
}


def test_every_log_is_valid_0_4_0():
    """Every row of every log conforms to 0.4.0, but for the rows a test makes
    unreadable on purpose."""
    pytest.importorskip("jsonschema")
    from test_schema_v0_4_0 import record_errors

    invalid = {}
    for label, rows in LOGS.items():
        ch = channel(rows)
        bad = {
            e.seq
            for e in ch.read()
            if record_errors(
                {
                    "seq": e.seq,
                    "topic": e.topic,
                    "name": e.name,
                    "request_id": e.request_id,
                    "body": e.body,
                }
            )
        }
        if bad:
            invalid[label] = bad
    assert invalid == UNREADABLE


# ----- the head ---------------------------------------------------------------------


def head(rows):
    return LatestClaimHead().resolve(channel(rows))


def test_an_empty_log_and_a_fresh_claim_hold_the_empty_lineage():
    assert head([]) == Head(None, False, 0)
    assert head(RESUMED[:1]) == Head(None, False, 1)


def test_the_head_follows_the_newest_claims_commits():
    assert head(RESUMED[:5]) == Head(5, False, 5)


def test_a_commitless_claims_head_is_its_resume_from_an_older_claims_node():
    assert head(COMMITLESS) == Head(3, False, 8)


def test_a_claims_readable_exit_is_its_head():
    assert head(COMMITLESS_ERRORED) == Head(9, False, 9)  # torn
    assert head(RESUMED[:7]) == Head(7, False, 7)  # preempted


def test_a_resume_from_the_previous_claims_exit_is_the_head_until_a_commit():
    assert head(RESUMED[:9]) == Head(7, False, 9)
    assert head(RESUMED[:10]) == Head(10, False, 10)


def test_a_trailing_rewind_moves_the_head_to_its_target():
    assert head(RESUMED[:11]) == Head(5, False, 11)
    assert head(TO_THE_ROOT) == Head(None, False, 4)
    assert head(FROM_THE_ROOT) == Head(6, False, 6)


def test_an_exits_parent_after_a_rewind_is_the_target():
    assert head(EXIT_AFTER_A_REWIND) == Head(5, False, 5)
    assert lineage(channel(EXIT_AFTER_A_REWIND), head=LatestClaimHead()) == [2, 5]


def test_a_displaced_workers_records_do_not_move_the_newest_claims_head():
    assert head(DISPLACED) == Head(3, False, 7)


def test_the_head_reads_nothing_above_its_w():
    ch = channel(RESUMED[:9])
    h = LatestClaimHead().resolve(ch)
    append(ch, RESUMED[9:])
    assert h == Head(7, False, 9)
    assert lineage(ch, head=h) == [3, 5, 7]


def test_a_head_resolves_to_itself_so_a_poll_reads_one_head():
    ch = channel(RESUMED[:10])
    h = LatestClaimHead().resolve(ch)
    assert h.resolve(ch) is h
    append(ch, RESUMED[10:])
    assert lineage(ch, head=h) == [3, 5, 7, 10]
    assert lineage(ch, head=LatestClaimHead()) == [3, 5, 13, 14]


def test_a_head_holds_its_invariants():
    with pytest.raises(ValueError):
        Head(None, True, 3)  # the empty lineage is not settled
    with pytest.raises(ValueError):
        Head(4, False, 3)  # nothing above w
    with pytest.raises(ValueError):
        Head(0, True, 3)  # no seq 0


# ----- settledness ------------------------------------------------------------------


def test_a_completed_exit_closes_its_lineage():
    assert head(RESUMED).settled
    assert head(UNCOMMITTED) == Head(8, True, 9)


def test_a_preempted_or_errored_exit_does_not():
    assert not head(RESUMED[:7]).settled
    assert not head(COMMITLESS_ERRORED).settled


def test_a_displaced_workers_completed_exit_does_not_settle_the_newest_claims_read():
    assert not head(DISPLACED).settled
    assert AtNode(7).resolve(channel(DISPLACED)) == Head(7, True, 7)
    assert lineage(channel(DISPLACED), head=AtNode(7)) == [3, 6, 7]


def test_at_node_is_settled_and_permanent():
    ch = channel(RESUMED[:10])
    before = AtNode(10).resolve(ch)
    reads = (lineage(ch, head=before), series(ch, "loss", head=before))
    append(ch, RESUMED[10:])
    after = AtNode(10).resolve(ch)
    assert (before, after) == (Head(10, True, 10), Head(10, True, 14))
    assert (lineage(ch, head=after), series(ch, "loss", head=after)) == reads


def test_at_node_reads_any_node_an_abandoned_branch_a_torn_exit_and_an_exit():
    ch = channel(RESUMED)
    assert lineage(ch, head=AtNode(10)) == [3, 5, 7, 10]
    assert lineage(ch, head=AtNode(7)) == [3, 5, 7]
    assert lineage(channel(COMMITLESS_ERRORED), head=AtNode(5)) == [3, 5]


@pytest.mark.parametrize("seq", [0, 2, 1, 8, 11, 15, -1])
def test_at_node_raises_on_a_seq_that_is_not_a_node_or_is_above_w(seq):
    with pytest.raises(ValueError):
        AtNode(seq).resolve(channel(RESUMED))


def test_at_node_raises_on_a_heartbeat():
    with pytest.raises(ValueError):
        AtNode(8).resolve(channel(COMMITLESS))


def test_a_hand_built_head_that_is_no_node_raises():
    with pytest.raises(ValueError):
        lineage(channel(RESUMED), head=Head(2, False, 14))


# ----- nodes and commits ------------------------------------------------------------


def test_commits_count_iterations_across_a_resume_and_an_extended_exit():
    """The exit B resumed from is on its lineage and adds no iteration; its
    value is still an element."""
    ch = channel(RESUMED)
    branch = lineage(ch, head=AtNode(10))
    assert [count(ch, Commits(), node=n) for n in branch] == [1, 2, 2, 3]
    assert [count(ch, "loss", node=n) for n in branch] == [1, 2, 3, 4]
    assert prefix_len(ch, Commits(), head=LatestClaimHead()) == 3
    assert prefix_len(ch, "loss", head=LatestClaimHead()) == 3


def test_a_count_at_an_exit_takes_its_values_and_no_iteration():
    ch = channel(RESUMED)
    assert count(ch, Commits(), node=7) == count(ch, Commits(), node=5) == 2
    assert count(ch, "loss", node=7) == count(ch, "loss", node=5) + 1
    assert count(ch, "loss", node=14) == count(ch, "loss", node=13) == 3


def test_resume_and_rewind_form_the_parent_chains():
    ch = channel(RESUMED)
    assert lineage(ch, head=LatestClaimHead()) == [3, 5, 13, 14]
    assert series(ch, "loss", head=LatestClaimHead()) == [
        Element(3, 2, 0.9, 1.5),
        Element(5, 4, 0.8, 1.5),
        Element(13, 12, 0.5, 1.5),
    ]


def test_a_value_no_node_names_is_excluded():
    ch = channel(UNCOMMITTED)
    assert [e.seq for e in series(ch, "loss", head=LatestClaimHead())] == [2, 6]
    assert series(ch, "status", head=LatestClaimHead()) == []
    assert count(ch, "status", node=8) == 0
    ch = channel(COMMITLESS_ERRORED)
    assert [e.seq for e in series(ch, "loss", head=LatestClaimHead())] == [2]
    assert prefix_len(ch, "status", head=LatestClaimHead()) == 0


def test_a_value_committed_on_two_lineages_is_one_element_of_each():
    ch = channel(TWO_BRANCHES)
    assert series(ch, "loss", head=LatestClaimHead()) == [Element(5, 2, 0.9, 1.5)]
    assert series(ch, "loss", head=AtNode(3)) == [Element(3, 2, 0.9, 1.5)]


def test_order_within_a_node_follows_values():
    ch = channel(IN_LINEAGE_ORDER)
    assert [e.seq for e in series(ch, "loss", head=AtNode(5))] == [4, 2]
    assert [e.seq for e in series(ch, "loss", head=LatestClaimHead())] == [4, 2, 3]


def test_last_element_is_the_last_in_lineage_order_not_the_newest_by_seq():
    ch = channel(IN_LINEAGE_ORDER)
    assert last_element(ch, "loss", head=LatestClaimHead()) == Element(6, 3, 0.8, 1.5)
    assert last_element(ch, "loss", head=AtNode(5)) == Element(5, 2, 0.9, 1.5)


def test_the_empty_lineage_counts_0_and_has_no_last_element():
    ch = channel(TO_THE_ROOT)
    assert lineage(ch, head=LatestClaimHead()) == []
    assert prefix_len(ch, Commits(), head=LatestClaimHead()) == 0
    assert prefix_len(ch, "loss", head=LatestClaimHead()) == 0
    assert series(ch, "loss", head=LatestClaimHead()) == []
    assert last_element(ch, "loss", head=LatestClaimHead()) is None
    assert count(ch, "loss", node=None) == count(ch, Commits(), node=None) == 0


def test_counts_and_indices_are_len_and_range_of_one_convention():
    ch = channel(RESUMED)
    for h in (AtNode(10), LatestClaimHead()):
        for n in lineage(ch, head=h):
            elements = series(ch, "loss", head=AtNode(n))
            assert count(ch, "loss", node=n) == len(elements)
            assert prefix_len(ch, "loss", head=AtNode(n)) == len(elements)
            assert last_element(ch, "loss", head=AtNode(n)) == elements[-1]


def test_membership_is_by_the_committing_claims_declaration():
    ch = channel(KIND_CHANGED)
    assert series(ch, "lr", head=LatestClaimHead()) == [Element(9, 8, 0.05, 1.5)]
    assert count(ch, "lr", node=9) == 1
    assert count(ch, "loss", node=9) == 1


def test_a_name_that_was_a_source_throughout_counts_0():
    ch = channel(KIND_CHANGED)
    assert count(ch, "lr", node=5) == 0
    assert series(ch, "lr", head=AtNode(6)) == []
    assert last_element(ch, "lr", head=AtNode(6)) is None


# ----- well-formedness, each at its stated check -------------------------------------


@pytest.mark.parametrize(
    "rows, at, bad",
    [
        (LATER_VALUE, AtNode(4), 2),
        (LATER_VALUE, LatestClaimHead(), 2),
        (LATER_PARENT, AtNode(5), 3),
        (PARENT_A_VALUE, AtNode(3), 3),
        (PARENT_CLOSED, AtNode(5), 5),
        (PARENT_TORN, AtNode(5), 5),
        (TWICE, LatestClaimHead(), 4),
    ],
)
def test_a_walk_checks_every_node_it_walks(rows, at, bad):
    """A parent or a values seq at or above its node's own, a parent that is
    not an extendable node, and a seq committed twice along one lineage."""
    ch = channel(rows)
    for read in (
        lambda: lineage(ch, head=at),
        lambda: prefix_len(ch, Commits(), head=at),
        lambda: series(ch, "loss", head=at),
    ):
        with pytest.raises(MalformedRecordError) as exc:
            read()
        assert exc.value.seq == bad


@pytest.mark.parametrize(
    "rows, bad",
    [
        (RESUME_LATER, 3),
        (REWIND_LATER, 3),
        (RESUME_CLOSED, 4),
        (RESUME_TORN, 4),
        (RESUME_A_HEARTBEAT, 3),
        (REWIND_TO_A_VALUE, 3),
        (ROLLBACK, 4),
        (EXIT_BEFORE_THE_REWIND, 5),
        (PARENT_CLOSED, 5),
    ],
)
def test_the_head_fold_checks_the_newest_claims_records(rows, bad):
    """A resume_from at or above its own seq or naming no extendable node, and
    a node whose parent is not its claim's position before it."""
    with pytest.raises(MalformedRecordError) as exc:
        head(rows)
    assert exc.value.seq == bad


def test_a_parent_that_is_not_its_claims_position_is_checked_by_the_head_fold_only():
    """It stays within one writer's order: a walk across writers cannot see it."""
    assert lineage(channel(ROLLBACK), head=AtNode(4)) == [2, 4]
    assert lineage(channel(EXIT_BEFORE_THE_REWIND), head=AtNode(5)) == [2, 3, 5]


def test_a_seq_committed_twice_is_the_walks_check():
    ch = channel(TWICE)
    assert head(TWICE) == Head(4, False, 4)
    assert lineage(ch, head=AtNode(3)) == [3]


def test_a_values_seq_naming_a_non_value_raises_only_when_resolved():
    ch = channel(NAMES_A_HEARTBEAT)
    assert lineage(ch, head=LatestClaimHead()) == [3]
    assert prefix_len(ch, Commits(), head=LatestClaimHead()) == 1
    for read in (
        lambda: count(ch, "loss", node=3),
        lambda: series(ch, "loss", head=LatestClaimHead()),
        lambda: last_element(ch, "loss", head=LatestClaimHead()),
    ):
        with pytest.raises(MalformedRecordError) as exc:
            read()
        assert exc.value.seq == 3


def test_a_malformed_value_body_raises_on_read_never_on_count():
    ch = channel(BAD_BODY)
    assert prefix_len(ch, "loss", head=LatestClaimHead()) == 2
    assert last_element(ch, "loss", head=LatestClaimHead()) == Element(4, 3, 0.8, 1.5)
    with pytest.raises(MalformedRecordError) as exc:
        series(ch, "loss", head=LatestClaimHead())
    assert exc.value.seq == 2
    ch = channel(BAD_BODY_LAST)
    assert count(ch, "loss", node=4) == 2
    with pytest.raises(MalformedRecordError) as exc:
        last_element(ch, "loss", head=LatestClaimHead())
    assert exc.value.seq == 2


@pytest.mark.parametrize(
    "rows, at", [(CLAIM_LATER, LatestClaimHead()), (CLAIM_NOT_A_CLAIM, AtNode(3))]
)
def test_membership_resolves_the_committing_claim_which_must_be_a_claim_below_it(
    rows, at
):
    ch = channel(rows)
    assert prefix_len(ch, Commits(), head=at) == len(lineage(ch, head=at))
    with pytest.raises(MalformedRecordError) as exc:
        series(ch, "loss", head=at)
    assert exc.value.seq == 3


# ----- readable means the schema's ---------------------------------------------------

_DEFINITIONS = {
    "lifecycle.started": ("lifecycle-v0.6", "Started", _Started),
    "lifecycle.commit": ("lifecycle-v0.6", "Commit", _Commit),
    "lifecycle.rewind": ("lifecycle-v0.6", "Rewind", _Rewind),
    "lifecycle.stopped": ("lifecycle-v0.6", "Stopped", _Stopped),
    "value": ("value-v0.3", "Value", _Value),
}


def _edges():
    """Bodies one field off a valid one, at the edges the parse mirrors, and
    with a key added or the first removed."""
    off = {
        "lifecycle.started": [
            ("handle", 5),
            ("t", -1.0),
            ("t", True),
            ("resume_from", True),
            ("resume_from", 0),
            ("sources", "lr"),
            ("sources", ["lr", "lr"]),
        ],
        "lifecycle.commit": [
            ("claim_seq", True),
            ("claim_seq", None),
            ("consumed_seq", -1),
            ("consumed_seq", True),
            ("parent", 0),
            ("values", "x"),
            ("values", [True]),
            ("fired", ["q", "q"]),
            ("expired", [""]),
            ("t", "1"),
        ],
        "lifecycle.rewind": [
            ("claim_seq", None),
            ("resume_from", -3),
            ("t", None),
        ],
        "lifecycle.stopped": [
            ("completed", "yes"),
            ("error", 5),
            ("claim_seq", True),
            ("honored", ["s", "s"]),
            ("parent", 0),
            ("values", [2, 2]),
            ("values", None),
            ("fired", [""]),
            ("t", -1.0),
        ],
        "value": [("t", -1.0), ("t", "x"), ("t", True), ("t", None)],
    }
    bodies = {
        "lifecycle.started": started(None, sources=["lr"]).body,
        "lifecycle.commit": commit(1, 2, [3], fired=["q"]).body,
        "lifecycle.rewind": rewind(1, 2).body,
        "lifecycle.stopped": stopped(1, 2, [3]).body,
        "value": value("loss", 0.9).body,
    }
    for topic, changes in off.items():
        for field, v in changes:
            yield topic, {**bodies[topic], field: v}
        yield topic, {**bodies[topic], "extra": 1}
        yield topic, {k: v for k, v in list(bodies[topic].items())[1:]}
    torn = {**bodies["lifecycle.stopped"], "values": None}
    yield "lifecycle.stopped", {**torn, "completed": True}  # closed, not torn
    yield "lifecycle.stopped", {**torn, "error": "boom"}
    yield "lifecycle.stopped", {**torn, "completed": True, "error": "boom"}
    yield "lifecycle.stopped", {**torn, "expired": ["q"]}


def _bodies():
    from test_schema_v0_4_0 import NEGATIVE, VALID

    for r in [*VALID.values(), *NEGATIVE.values()]:
        if r["topic"] in _DEFINITIONS:
            yield r["topic"], r["body"]
    yield from _edges()


def test_a_body_is_readable_here_if_and_only_if_its_schema_definition_admits_it():
    """``MalformedRecordError`` and "absent for the head" rest on one meaning of
    readable: the 0.4.0 schema's, body by body."""
    pytest.importorskip("jsonschema")
    import json
    from pathlib import Path

    from jsonschema import Draft202012Validator

    protocol = Path(__file__).resolve().parent.parent / "protocol"
    disagree = []
    for topic, body in _bodies():
        schema, definition, cls = _DEFINITIONS[topic]
        defs = json.loads((protocol / f"{schema}.schema.json").read_text())["$defs"]
        admits = Draft202012Validator(
            {"$defs": defs, "$ref": f"#/$defs/{definition}"}
        ).is_valid(body)
        try:
            cls(**body)
            parses = True
        except (TypeError, ValueError):
            parses = False
        if parses != admits:
            disagree.append((topic, body, admits))
    assert disagree == []


def test_a_whole_number_float_is_no_seq_here_though_json_schema_admits_it():
    """JSON Schema's ``integer`` admits 3.0; these reads, like
    ``vocabulary.payloads`` and the migration step, refuse it. No writer of a
    0.4.0 log writes one: Python's ``json`` writes an int as an int."""
    with pytest.raises(ValueError):
        _Commit(**{**commit(1, 2, [3]).body, "parent": 2.0})


# ----- the head fold's decisions -----------------------------------------------------


def test_an_unreadable_exit_is_absent_for_the_head():
    assert head(UNREADABLE_EXIT) == Head(2, False, 3)
    with pytest.raises(MalformedRecordError) as exc:
        AtNode(3).resolve(channel(UNREADABLE_EXIT))
    assert exc.value.seq == 3


@pytest.mark.parametrize(
    "rows, bad",
    [
        (TWO_EXITS, 4),
        (AFTER_THE_EXIT, 4),
        (NAMES_NO_CLAIM, 3),
        (UNREADABLE_CLAIM, 1),
    ],
)
def test_the_head_fold_refuses_to_guess(rows, bad):
    """The exit is its claim's last node, and there is at most one per claim; a
    position-bearing record that names no claim may be the newest claim's; an
    unreadable claim states no resume_from."""
    with pytest.raises(MalformedRecordError) as exc:
        head(rows)
    assert exc.value.seq == bad


def test_another_claims_unreadable_record_does_not_poison_the_newest_claim():
    assert head(ANOTHER_CLAIMS_JUNK) == Head(3, False, 5)


# ----- the migration's goldens, read as the corpus runner will -----------------------


def _golden(rid):
    from test_migration_v0_3_0_to_v0_4_0 import OUTPUT

    ch = MemoryChannel()
    for seq, topic, name, request_id, body in OUTPUT[rid]:
        assert ch.send(body, topic=topic, name=name, request_id=request_id) == seq
    return ch


def _golden_ids():
    from test_migration_v0_3_0_to_v0_4_0 import OUTPUT

    return list(OUTPUT)


@pytest.mark.parametrize("rid", _golden_ids())
def test_every_goldens_head_lineage_and_series_read(rid):
    ch = _golden(rid)
    h = LatestClaimHead().resolve(ch)
    nodes = lineage(ch, head=h)
    names = {e.name for e in ch.read(topics=["value"])}
    for name in names:
        elements = series(ch, name, head=h)
        assert [e.node for e in elements] == sorted(
            (e.node for e in elements), key=nodes.index
        )
        assert prefix_len(ch, name, head=h) == len(elements)
    assert prefix_len(ch, Commits(), head=h) == sum(
        1 for e in ch.read(topics=["lifecycle.commit"]) if e.seq in nodes
    )


def test_the_displaced_golden_reads_the_newest_claims_lineage():
    ch = _golden("displaced")
    assert LatestClaimHead().resolve(ch) == Head(12, True, 12)
    assert lineage(ch, head=LatestClaimHead()) == [3, 4, 8, 10, 12]
    assert prefix_len(ch, Commits(), head=LatestClaimHead()) == 4
    assert series(ch, "loss", head=LatestClaimHead()) == [
        Element(3, 2, 0.9, 2.0),
        Element(8, 6, 0.8, 6.0),
        Element(10, 9, 0.7, 8.0),
    ]
    assert lineage(ch, head=AtNode(11)) == [3, 4, 7, 11]  # the displaced exit
    assert count(ch, Commits(), node=11) == 3


def test_the_loading_crash_golden_reads_past_the_torn_retry():
    ch = _golden("resumed_past_a_loading_crash")
    assert LatestClaimHead().resolve(ch) == Head(15, True, 15)
    assert lineage(ch, head=LatestClaimHead()) == [3, 5, 7, 14, 15]
    assert [e.value for e in series(ch, "loss", head=LatestClaimHead())] == [
        0.9,
        0.8,
        0.7,
        0.6,
    ]
    assert series(ch, "status", head=LatestClaimHead()) == []  # the retry's loading
    assert lineage(ch, head=AtNode(11)) == [3, 5, 7, 11]  # the retry's torn exit
    assert count(ch, Commits(), node=11) == 3
