"""The interop conformance suite (docs/specs/commits-and-lineage.md §9): one test per
item another implementation must match, each named ``test_interop_<item>``, and each
the §10 test for its item, never a second copy of one (plan, "Interop").

The substrate contract, item 5's first half, is ``tests/test_concurrency.py``'s.
These read the hand-written 0.4.0 logs of ``tests/test_next_reads.py``, which
checks each against the 0.4.0 schemas.
"""

import pytest

from runstate._next.observables import (
    AtNode,
    Element,
    Head,
    LatestClaimHead,
    MalformedRecordError,
    prefix_len,
    series,
)
from runstate._next.vocabulary.coordinate import Commits
from tests.test_next_reads import (
    BAD_BODY,
    CLOSED_DISCARDED,
    COMMITLESS,
    COMMITLESS_ERRORED,
    DISPLACED,
    EXIT_AFTER_A_REWIND,
    IN_LINEAGE_ORDER,
    KIND_CHANGED,
    LATER_PARENT,
    LATER_VALUE,
    PARENT_CLOSED,
    PARENT_TORN,
    RESUME_A_HEARTBEAT,
    RESUME_CLOSED,
    RESUME_TORN,
    RESUMED,
    ROLLBACK,
    TWICE,
    TWO_BRANCHES,
    UNCOMMITTED,
    _LateW,
    channel,
    head,
    lineage,
)


def _raises_at(read, seq):
    with pytest.raises(MalformedRecordError) as exc:
        read()
    assert exc.value.seq == seq


def test_interop_node_rule():
    """§9 item 1: ``parent`` and ``resume_from`` name an extendable node, and
    ``parent`` is the claim's position; a node follows what it names; order within
    a node; a seq at most once along a lineage, though on two lineages it is an
    element of each."""
    # a lineage is the chain by parent, through a resume and a rewind
    assert lineage(channel(RESUMED), head=LatestClaimHead()) == [3, 5, 13, 14]
    assert lineage(channel(EXIT_AFTER_A_REWIND), head=LatestClaimHead()) == [2, 5]
    # parent and resume_from name an extendable node
    _raises_at(lambda: lineage(channel(PARENT_TORN), head=AtNode(5)), 5)
    _raises_at(lambda: head(RESUME_A_HEARTBEAT), 3)
    # parent is the claim's position: a rollback written as a commit
    _raises_at(lambda: head(ROLLBACK), 4)
    # a node follows what it names
    _raises_at(lambda: lineage(channel(LATER_VALUE), head=AtNode(4)), 2)
    _raises_at(lambda: lineage(channel(LATER_PARENT), head=AtNode(5)), 3)
    # order within a node is its values' order, which no reordering can move
    ch = channel(IN_LINEAGE_ORDER)
    assert [e.seq for e in series(ch, "loss", head=LatestClaimHead())] == [4, 2, 3]
    # a seq at most once along a lineage; on two lineages, one element of each
    _raises_at(lambda: lineage(channel(TWICE), head=LatestClaimHead()), 4)
    ch = channel(TWO_BRANCHES)
    assert series(ch, "loss", head=LatestClaimHead()) == [Element(5, 2, 0.9, 1.5)]
    assert series(ch, "loss", head=AtNode(3)) == [Element(3, 2, 0.9, 1.5)]


def test_interop_closed_torn_extendable():
    """§9 item 2: a node's kind is read from the exit's ``completed`` and
    ``values``. A completed exit is closed, whatever its values: it settles the
    newest claim's read and nothing extends it. An error exit, and an exit that
    did not complete and discarded its iteration, are torn: nothing extends them.
    A preempted exit that kept its values is extendable: a claim resumes from it,
    and it adds no iteration."""
    # closed, with values and without
    assert head(UNCOMMITTED).settled
    ch = channel(CLOSED_DISCARDED)
    assert AtNode(4).resolve(ch).settled
    _raises_at(lambda: head(CLOSED_DISCARDED), 5)
    _raises_at(lambda: head(RESUME_CLOSED), 4)
    _raises_at(lambda: lineage(channel(PARENT_CLOSED), head=AtNode(5)), 5)
    # torn: an error exit, and a discard that did not complete
    assert not head(COMMITLESS_ERRORED).settled
    _raises_at(lambda: lineage(channel(PARENT_TORN), head=AtNode(5)), 5)
    _raises_at(lambda: head(RESUME_TORN), 4)
    # extendable: B resumes from A's preempted exit, which adds no iteration
    ch = channel(RESUMED)
    assert lineage(ch, head=AtNode(10)) == [3, 5, 7, 10]
    assert prefix_len(ch, Commits(), head=AtNode(7)) == 2
    assert prefix_len(ch, "loss", head=AtNode(7)) == 3


def test_interop_membership_by_declaration():
    """§9 item 3: a committed value is an element of its name's stream if and only
    if the claim that committed it did not declare the name a source, decided by
    the envelope: a malformed body still counts, and raises only when read."""
    ch = channel(KIND_CHANGED)
    assert prefix_len(ch, "lr", head=AtNode(6)) == 0  # A's source: never counted
    assert series(ch, "lr", head=LatestClaimHead()) == [Element(9, 8, 0.05, 1.5)]
    assert prefix_len(ch, "loss", head=LatestClaimHead()) == 1
    ch = channel(BAD_BODY)
    assert prefix_len(ch, "loss", head=LatestClaimHead()) == 2
    _raises_at(lambda: series(ch, "loss", head=LatestClaimHead()), 2)


def test_interop_head_and_settledness():
    """§9 item 4: the newest claim's head is its readable exit, else its position:
    its newest commit, or the node its claim or rewind resumed from, an older
    claim's node included; a displaced worker's records do not move it. A read is
    settled under ``AtNode`` always, and under ``LatestClaimHead`` if and only if
    the head is closed."""
    assert head(RESUMED[:5]) == Head(5, False, 5)  # the newest commit
    assert head(COMMITLESS) == Head(3, False, 8)  # resumed from A's commit
    assert head(RESUMED[:9]) == Head(7, False, 9)  # resumed from A's exit
    assert head(RESUMED[:10]) == Head(10, False, 10)
    assert head(RESUMED[:11]) == Head(5, False, 11)  # a rewind
    assert head(RESUMED[:7]) == Head(7, False, 7)  # a preempted exit
    assert head(COMMITLESS_ERRORED) == Head(9, False, 9)  # a torn exit
    assert head(DISPLACED) == Head(3, False, 7)  # A's records after B's claim
    assert head(RESUMED) == Head(14, True, 14)  # closed
    assert AtNode(7).resolve(channel(DISPLACED)) == Head(7, True, 7)
    assert AtNode(10).resolve(channel(RESUMED)) == Head(10, True, 14)


def test_interop_well_formedness_is_completeness():
    """§9 item 5, its second half: a read never waits. At any W, a node naming a
    seq at or above its own raises, whether or not that seq has landed: the read
    does not wait for it to become readable."""
    for w in (2, 3, 4):  # the named seq 3 above W, at W, and below it
        ch = _LateW(LATER_VALUE[:w], w)
        _raises_at(lambda: prefix_len(ch, "loss", head=AtNode(2)), 2)
        _raises_at(lambda: prefix_len(ch, Commits(), head=AtNode(2)), 2)
