"""The interop conformance suite (docs/specs/commits-and-lineage.md §9): one test per
item another implementation must match, each named ``test_interop_<item>``, and each
the §10 test for its item, never a second copy of one (plan, "Interop").

The substrate contract, item 5's first half, is ``tests/test_concurrency.py``'s.
These read the hand-written 0.4.0 logs of ``tests/test_next_reads.py``, which
checks each against the 0.4.0 schemas.
"""

import ast
import threading
from pathlib import Path

import pytest

from runstate._next import observables, walk
from runstate._next.launcher import (
    LocalLauncher,
    ThreadLauncher,
    report_death,
    report_if_dead,
)
from runstate._next.observables import (
    Answers,
    AtNode,
    DeathReport,
    Element,
    Head,
    HeadFold,
    LastElement,
    LatestClaimHead,
    LiveDemand,
    MalformedRecordError,
    PendingStops,
    PrefixLen,
    aligned,
    answers,
    current_heartbeat,
    last_activity,
    last_element,
    latest_episode,
    live_demand,
    live_episode,
    peek_terminal,
    prefix,
    prefix_len,
    series,
    streams,
    undischarged_stops,
)
from runstate._next.vocabulary.coordinate import Commits
from runstate._next.vocabulary.payloads import Topic
from tests.test_next_death_reports import (
    FOREIGN,
    LIVE,
    child,
    claim,
    dead_handle,
    deaths,
    died_body,
    undated,
    until,
)
from tests.test_next_reads import (
    A_CLAIMS_LAUNCH_ENDED,
    BAD_BODY,
    C,
    DEATH_THEN_EXIT,
    DEMAND,
    E,
    EXIT_THEN_DEATH,
    HELD,
    HONORED_BY_A_DISPLACED_EXIT,
    K,
    L,
    LEASED,
    LOST_BESIDE_PREEMPTED,
    ONLY_LOST,
    ONLY_UNREADABLE,
    P,
    SEVERAL_DEATHS,
    SEVERAL_DEATHS_REVERSED,
    STOPS,
    UNCLAIMED,
    UNREADABLE_DEATH,
    UNREADABLE_EXIT,
    by_deaths,
    by_exit,
    by_launch,
    pending,
    stop,
    subscribe,
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
    assert head(UNCOMMITTED) == Head(8, True, 9)
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


def test_interop_ended_and_the_verdict_by_viewpoint():
    """§9 item 12: ``ended`` is one predicate, a claim's exit or a death report
    naming it, an unreadable one whose ``claim_seq`` parses included, and the gate,
    lease voiding and the verdict all read it. The verdict is by viewpoint: the
    claim's readable exit outranks its death reports whatever their order; else
    the reports' outcomes as a set, an unreadable record ending the claim counting
    as ``lost``, less a ``lost`` beside another; an unclaimed run takes its
    launch's ``terminated``, which ends nothing for a claimed run."""
    # the self-report outranks the death reports, whichever came first
    assert peek_terminal(channel(EXIT_THEN_DEATH)) == by_exit(3, 1, P)
    assert peek_terminal(channel(DEATH_THEN_EXIT)) == by_exit(4, 1, C)
    # several reports: a set, whatever their order, less lost beside another
    reports = [
        DeathReport(3, K, "signal 9", 5.0),
        DeathReport(4, E, "slurm: NODE_FAIL", 5.0),
        DeathReport(5, L, "probe: pid gone", 5.0),
    ]
    assert peek_terminal(channel(SEVERAL_DEATHS)) == by_deaths(1, {K, E}, reports)
    other = peek_terminal(channel(SEVERAL_DEATHS_REVERSED))
    assert other.outcomes == {K, E}
    assert [(r.seq, r.outcome) for r in other.reports] == [(3, L), (4, E), (5, K)]
    assert peek_terminal(channel(LOST_BESIDE_PREEMPTED)) == by_deaths(
        1,
        {P},
        [DeathReport(3, L, "exit 0", 5.0), DeathReport(4, P, "slurm: PREEMPTED", 5.0)],
    )
    assert peek_terminal(channel(ONLY_LOST)).outcomes == {L}
    # an unreadable record that names the claim ends it, as a lost
    for rows, malformed in (
        (UNREADABLE_EXIT, (3,)),
        (UNREADABLE_DEATH, (3,)),
        (ONLY_UNREADABLE, (3, 4)),
    ):
        assert peek_terminal(channel(rows)) == by_deaths(
            1, {L}, [], malformed=malformed
        )
    # an unclaimed run's launch, and a claimed run's, which ends nothing
    assert peek_terminal(channel(UNCLAIMED)) == by_launch(E)
    assert peek_terminal(channel(A_CLAIMS_LAUNCH_ENDED)) is None
    # one predicate: the gate is closed exactly where the verdict is in
    for rows in (HELD, EXIT_THEN_DEATH, SEVERAL_DEATHS, ONLY_UNREADABLE):
        ch = channel(rows)
        assert (live_episode(ch) is None) == (peek_terminal(ch) is not None)
    assert live_episode(channel(A_CLAIMS_LAUNCH_ENDED)) == "local://n1/11"
    # and lease voiding: the lease is live until the unreadable report ends its claim
    before, after = _LateW(LEASED, 4), channel(LEASED)
    assert [e.request_id for e in live_demand(before)] == ["lease"]
    assert live_episode(before) is not None and peek_terminal(before) is None
    assert live_demand(after) == [] and live_episode(after) is None
    assert peek_terminal(after) == by_deaths(2, {L}, [], malformed=(5,))


def test_interop_answer_fold_by_name():
    """§9 item 13: a request is spent by what names it, wherever that sits: a stop
    by an exit's ``honored`` (a displaced worker's included), a withdrawal or a
    refusal, never by a death report; a subscription by a node's ``expired``, on
    any lineage, a withdrawal or a refusal. A spent id stays spent, and a
    subscription whose schedule does not parse is live until a refusal names it,
    as a malformed stop is pending until its refusal."""
    assert pending(channel(STOPS)) == [5]
    assert pending(channel(HONORED_BY_A_DISPLACED_EXIT)) == []
    live = live_demand(_LateW(DEMAND, 12))
    assert [(e.seq, e.request_id) for e in live] == [(5, "live"), (6, "malformed")]
    live = live_demand(channel(DEMAND))
    assert [(e.seq, e.request_id) for e in live] == [(5, "live")]


def test_interop_death_report_from_evidence(tmp_path):
    """§9 item 15: a death report rests on evidence, never inference, and a ``lost``
    one, which carries no cause, lands only while its claim is held. A read never
    writes one: no read, no fold and no handle's ``is_alive()``; a reap, which
    yields the exit status once, is where a launcher writes it (§3)."""
    # a lost report only while held: checked at the W of its CAS
    ch = channel([(1, claim(FOREIGN))])
    assert report_death(ch, 1, outcome=L, evidence="probe: x", force=False) == 2
    assert report_death(ch, 1, outcome=L, evidence="probe: y", force=False) is None
    # a report with a cause lands whether or not the claim has ended
    assert report_death(ch, 1, outcome=K, evidence="slurm:x", force=False) == 3
    assert peek_terminal(ch).outcomes == {K}
    # evidence, never inference: a live handle, or one another host owns, reports
    # nothing however stale the claim; a dead same-host handle reports lost
    for handle in (LIVE, FOREIGN):
        ch = channel([(1, claim(handle, t=0.0))])
        assert report_if_dead(ch) is None and ch.last_seq() == 1
    handle = dead_handle()
    rows = [
        (1, subscribe(None, "lease", until={"time_seconds": 60})),
        (2, stop("halt")),
        (3, claim(handle, t=0.0)),
    ]
    # no read, fold or is_alive() writes one, though the claim's handle is dead
    ch = channel(rows)
    head = HeadFold().poll(ch)
    for read in (
        peek_terminal,
        live_episode,
        latest_episode,
        live_demand,
        undischarged_stops,
        last_activity,
        lambda c: current_heartbeat(c, latest_episode(c)),
        lambda c: LiveDemand().poll(c),
        lambda c: PendingStops().poll(c),
        lambda c: PrefixLen(Commits()).poll(c, head),
        lambda c: LastElement("loss").poll(c, head),
        lambda c: Answers("lease").poll(c, head),
        lambda c: prefix_len(c, Commits(), head=LatestClaimHead()),
        lambda c: prefix(c, "loss", 1, head=LatestClaimHead()),
        lambda c: series(c, "loss", head=LatestClaimHead()),
        lambda c: last_element(c, "loss", head=LatestClaimHead()),
        lambda c: answers(c, "lease", head=LatestClaimHead()),
        lambda c: streams(c, head=LatestClaimHead()),
        lambda c: aligned(c, ["loss"], head=LatestClaimHead(), axis=Commits()),
    ):
        read(ch)
    assert ch.last_seq() == 3 and live_episode(ch) == handle
    # and structurally: the reads' modules never append
    for module in (observables, walk):
        tree = ast.parse(Path(module.__file__).read_text())
        sends = [
            n.lineno
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "send"
        ]
        assert sends == [], (module.__name__, sends)
    assert report_if_dead(ch) == 4  # the party that wants it recorded calls this
    # a thread handle's is_alive() is pure: the thread's end is its reap
    release = threading.Event()
    threads = ThreadLauncher(root=tmp_path)
    th = threads.launch("thread", lambda c: release.wait(30))
    tch = threads.attach_channel("thread")
    before = tch.last_seq()
    assert th.is_alive() and tch.last_seq() == before
    release.set()
    th.wait()
    # a handle's is_alive() is pure, though the process has ended; its reap writes
    with LocalLauncher(root=tmp_path) as launcher:
        h = launcher.launch("run", child("exit", "0"))
        until(lambda: not h.is_alive())
        ch = launcher.attach_channel("run")
        claim_seq = ch.latest(Topic.LIFECYCLE_STARTED).seq
        assert deaths(ch) == [] and ch.read(topics=["supervisor.terminated"]) == []
        h.wait()
        assert undated(deaths(ch)) == [died_body(claim_seq, L, "reap: exit 0")]
        ch.close()
