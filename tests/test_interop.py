"""The interop conformance suite (docs/specs/commits-and-lineage.md §9): one test per
item another implementation must match, each named ``test_interop_<item>``, and each
the §10 test for its item, never a second copy of one (plan, "Interop").

The substrate contract, item 5's first half, is ``tests/test_concurrency.py``'s.
These read the hand-written 0.4.0 logs of ``tests/test_reads.py``, which
checks each against the 0.4.0 schemas.
"""

import ast
import threading
from pathlib import Path

import pytest

from runstate import claims, observables, walk
from runstate.launcher import (
    LocalLauncher,
    ThreadLauncher,
    report_death,
    report_if_dead,
)
from runstate.observables import (
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
from runstate.vocabulary.coordinate import Commits
from runstate.vocabulary.payloads import Topic
from tests.test_death_reports import (
    FOREIGN,
    LIVE,
    child,
    claim,
    dead_handle,
    deaths,
    died_body,
    thread,
    undated,
    until,
)
from tests.test_reads import (
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
    refusal, never by a death report; a subscription by a withdrawal or a
    refusal, a lease also by a node's ``expired`` on any lineage, its claim's, and
    a durable subscription by one on the head's lineage (§2, amended 2026-10-30).
    A spent id stays spent, and a
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
    for module in (observables, walk, claims):
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
    th = threads.launch("thread", thread(lambda c: release.wait(30)))
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


def test_interop_stop_from_latch_and_honored(open_run):
    """§9 item 8: a stop's ``from`` is evaluated at each node, with the counts at
    the node being written, and at each heartbeat, with the counts at the position
    and the heartbeat's clock; the decision, once a stop holds, is latched for the
    rest of the claim, a rewind included; and the exit's ``honored`` names every
    stop that held at a node or a heartbeat of the claim, even one withdrawn
    since, and every stop pending at the exit. A stop withdrawn before it held is
    spent, and named nowhere."""
    from runstate.vocabulary.payloads import Topic as T
    from tests.test_worker import Clock, send_stop, send_withdraw, worker

    ch, obs, clock = open_run(), open_run(), Clock()
    send_stop(obs, "at_node", **{"from": {"commits": 2}})
    send_stop(obs, "at_beat", **{"from": {"time_seconds": 10}})
    send_stop(obs, "pending", **{"from": {"commits": 99}})
    send_stop(obs, "withdrawn", **{"from": {"commits": 99}})
    with worker(ch, now=clock) as w:
        assert w.commit() is False  # count 1 at the node; t 0
        send_withdraw(obs, "withdrawn")
        clock.t = 10.0
        w.emit("loss", 1.0)
        assert w.heartbeat() is True  # due at the heartbeat, inside the iteration
        assert w.commit() is True  # count 2 at the node being written
        send_withdraw(obs, "at_beat")  # too late: it held
        w.rewind(None)
        assert w.stop_pending is True  # latched across the rewind
        assert w.heartbeat() is True
    body = obs.latest(T.LIFECYCLE_STOPPED).body
    assert set(body["honored"]) == {"at_beat", "at_node", "pending"}  # a set (§3)
    assert undischarged_stops(obs) == []


def test_interop_fired_expired_and_one_sample(open_run):
    """§9 item 6: firing and expiry are facts at a node, its ``fired`` naming the
    subscriptions that fired there and its ``expired`` those whose schedule ended
    there, the exit node's included; and a source fires once per node: one sample
    of its register (``name`` the source, ``request_id`` null), written before the
    node, committed after the node's pending values, however many of its
    subscriptions fire there. ``answers`` reads ``fired``."""
    from runstate.vocabulary.payloads import Topic as T
    from tests.test_subscriptions import samples, sub
    from tests.test_worker import worker

    ch, obs = open_run(), open_run()
    sub(obs, "a")  # a one-shot
    sub(obs, "b", every={"commits": 1})
    sub(obs, "c", source="lr", every={"commits": 0})
    with worker(ch, sources=["loss", "lr"]) as w:
        w.emit("acc", 0.9)
        w.set("loss", 0.5)
        w.set("lr", 0.1)
        w.commit()
        w.set("lr", 0.2)
    nodes = obs.read(topics=[T.LIFECYCLE_COMMIT, T.LIFECYCLE_STOPPED])
    commit_, exit_ = nodes[0].body, nodes[1].body
    acc = obs.latest(T.VALUE, "acc").seq
    loss = obs.read(topics=[T.VALUE], name="loss")
    lr = obs.read(topics=[T.VALUE], name="lr")
    assert [(e.name, e.request_id) for e in loss + lr] == [
        ("loss", None),
        ("lr", None),
        ("lr", None),
    ]
    assert commit_["values"][0] == acc  # the pending values, then the samples
    assert sorted(commit_["values"][1:]) == [loss[0].seq, lr[0].seq]
    assert all(e.seq < nodes[0].seq for e in loss + lr[:1])
    assert (sorted(commit_["fired"]), commit_["expired"]) == (["a", "b", "c"], ["a"])
    assert (exit_["fired"], exit_["expired"], exit_["values"]) == (
        ["c"],
        [],
        [lr[1].seq],
    )
    assert samples(obs) == [0.5]  # two fired on loss: one sample
    assert [e.value for e in answers(obs, "a", head=LatestClaimHead())] == [0.5]
    assert [e.value for e in answers(obs, "c", head=LatestClaimHead())] == [0.1, 0.2]


def test_interop_leases_durables_and_demand(open_run):
    """§9 item 7: a lease is per claim, bound by ``lifecycle.bound``, its fire
    count kept across a rewind and void once its claim has ended, while its
    lineage atoms' baselines are read on the lineage; a durable subscription is per
    lineage, re-armed on a lineage that did not expire it; and demand reads the
    same scopes, a lease spent by its claim's expiry and a durable subscription
    live while the head's lineage has not expired it (§2, amended 2026-10-30), and
    is empty on a closed head, where a subscription is never answered and never
    refused."""
    from runstate.vocabulary.payloads import Topic as T
    from tests.test_subscriptions import bindings, claim_of, demand, sub
    from tests.test_worker import naks, worker

    ch, obs = open_run(), open_run()
    sub(obs, "lease", every={"commits": 2}, until={"count": 3})
    sub(obs, "held", every={"commits": 1}, until={"time_seconds": 100})
    sub(obs, "durable")  # a one-shot
    w = worker(ch, sources=["loss"])
    w.set("loss", 1.0)
    w.commit()  # c1: every one fires: durable expires on this lineage
    c1 = obs.latest(T.LIFECYCLE_COMMIT).seq
    w.commit()  # c2: the lease's delta from c1 is 1
    w.rewind(AtNode(c1))  # the lineage's newest firing is still c1's
    for _ in range(3):  # c3 on [c1]; then c4 and c5, each on the root
        w.set("loss", 2.0)
        w.commit()
        w.rewind(None)
    nodes = [e.body for e in obs.read(topics=[T.LIFECYCLE_COMMIT])]
    assert [(sorted(b["fired"]), sorted(b["expired"])) for b in nodes] == [
        (["durable", "held", "lease"], ["durable"]),
        (["held"], []),
        (["held"], []),  # c3: commits 2 on [c1], 1 past c1's firing
        (["durable", "held", "lease"], ["durable"]),  # no firing on the lineage
        (["durable", "held", "lease"], ["durable", "lease"]),  # the third firing
    ]
    assert bindings(obs) == [("lease", claim_of(obs)), ("held", claim_of(obs))]
    # the lease is spent by its claim's expiry; the durable one is live on the
    # root's lineage, which the last rewind left the head on
    assert demand(obs) == ["durable", "held"] and w.pinned is True
    w.stopped(completed=False, error=None)
    assert demand(obs) == ["durable"]  # held is void with its claim
    w2 = worker(open_run(), resume_from=None, sources=["loss"])
    w2.set("loss", 4.0)
    w2.commit()  # the root, which expires nothing: the durable one fires again
    assert obs.latest(T.LIFECYCLE_COMMIT).body["fired"] == ["durable"]
    w2.stopped(completed=True, error=None)
    sub(obs, "late", every={"commits": 1})  # posted to a closed head
    assert live_demand(obs) == [] and naks(obs) == []


def test_interop_atoms(open_run):
    """§9 item 9: ``from`` and ``until`` hold once ``count(X, node) >= n``, with the
    counts at the node being written, its pending values included; ``every`` is
    the delta rule from the last firing, not rounded to a multiple; ``{commits:
    n}`` counts iterations; a 0 in ``every`` fires at every evaluation, the exit
    included; and an ``until`` that holds at registration expires at the next
    node, unrefused, firing nothing."""
    from runstate.vocabulary.payloads import Topic as T
    from tests.test_subscriptions import sub
    from tests.test_worker import naks, worker

    ch, obs = open_run(), open_run()
    sub(obs, "from_c1", **{"from": {"commits": 1}})
    sub(obs, "from_x3", **{"from": {"stream": "x", "n": 3}})
    sub(obs, "every_x3", every={"stream": "x", "n": 3})
    sub(obs, "until_c2", every={"commits": 1}, until={"commits": 2})
    sub(obs, "zero", every={"commits": 0})
    with worker(ch, sources=["loss"]) as w:
        w.set("loss", 1.0)
        # x counts 1, 3, 4, 5 at the nodes: from 1 to 3 crosses a multiple of 3
        # with a delta of 2, so "every 3" tells the delta rule from rounding
        for batch in (1, 2, 1, 1):
            for _ in range(batch):
                w.emit("x", 0)
            w.commit()
        sub(obs, "held", until={"commits": 1})
        w.commit()  # c5: held already holds; it expires here, unfired
        w.emit("x", 0)
        w.emit("x", 0)  # x counts 7 at the exit
    nodes = [e.body for e in obs.read(topics=[T.LIFECYCLE_COMMIT, T.LIFECYCLE_STOPPED])]

    def at(r):
        return [i for i, b in enumerate(nodes) if r in b["fired"]]

    assert at("from_c1") == [0]  # commits 1 at the first node being written
    assert at("from_x3") == [1]  # x counts 3 there, its own pending values included
    # 1, then 4 (+3), then 7 (+3) at the exit; not 3, a multiple only 2 past 1
    assert at("every_x3") == [0, 2, 5]
    assert at("until_c2") == [0] and "until_c2" in nodes[1]["expired"]
    assert at("zero") == [0, 1, 2, 3, 4, 5]  # the exit included
    assert at("held") == [] and nodes[4]["expired"] == ["held"]
    assert naks(obs) == []


def test_interop_refusals(open_run):
    """§9 item 10: the three ``unsatisfiable`` refusals, judged against the
    evaluating claim's declaration (a target it does not declare a source, a
    progress atom naming one it does, an empty window with an absent ``from`` read
    as ``from`` 0); and the ``malformed`` refusals of a re-send that changes its
    target or its class, and of a pure demand with a ``from``, an ``every`` or a
    ``count`` atom. Each refusal is a ``nak`` naming the id, which spends it."""
    from tests.test_subscriptions import demand, sub
    from tests.test_worker import naks, worker

    ch, obs = open_run(), open_run()
    sub(obs, "not_a_source", source="acc")
    sub(obs, "counts_a_source", every={"stream": "lr", "n": 1})
    sub(obs, "empty", until={"commits": 0})
    sub(obs, "pure_from", source=None, **{"from": {"commits": 1}})
    sub(obs, "pure_every", source=None, every={"commits": 1})
    sub(obs, "pure_count", source=None, until={"count": 1})
    sub(obs, "target", every={"commits": 1})
    sub(obs, "class", every={"commits": 1})
    w = worker(ch, sources=["loss", "lr"])
    w.set("loss", 1.0)
    w.commit()
    sub(obs, "target", source="lr", every={"commits": 1})
    sub(obs, "class", every={"commits": 1}, until={"time_seconds": 60})
    w.commit()
    U, M = "unsatisfiable", "malformed"
    assert naks(obs) == [
        ("not_a_source", U),
        ("counts_a_source", U),
        ("empty", U),
        ("pure_from", M),
        ("pure_every", M),
        ("pure_count", M),
        ("target", M),
        ("class", M),
    ]
    assert demand(obs) == [] and w.pinned is False


def test_interop_pure_demand_lifetime(open_run):
    """§9 item 11: a pure demand targets nothing, so it never fires and no node's
    ``fired`` names it, and it writes no sample. It lives until its ``until``
    holds at a node, which names it in ``expired``; or a withdrawal or a refusal
    names it; or, as a lease (a time ``until``), its claim ends."""
    from runstate.vocabulary.payloads import Topic as T
    from tests.test_subscriptions import demand, sub
    from tests.test_worker import send_withdraw, worker

    ch, obs = open_run(), open_run()
    sub(obs, "until", source=None, until={"commits": 2})
    sub(obs, "withdrawn", source=None)
    sub(obs, "lease", source=None, until={"time_seconds": 100})
    sub(obs, "refused", source=None, every={"commits": 1})
    w = worker(ch, sources=[])
    w.commit()
    assert w.pinned is True
    assert set(demand(obs)) == {"until", "withdrawn", "lease"}
    send_withdraw(obs, "withdrawn")
    w.commit()  # until holds at this node
    nodes = [e.body for e in obs.read(topics=[T.LIFECYCLE_COMMIT])]
    assert [(b["fired"], b["expired"]) for b in nodes] == [([], []), ([], ["until"])]
    assert w.pinned is True and demand(obs) == ["lease"]
    w.stopped(completed=False, error=None)
    assert demand(obs) == []  # the lease is void with its claim
    assert w.pinned is False
    assert obs.read(topics=[T.VALUE]) == []


def test_interop_fence_and_cas_drain(open_run):
    """§9 item 14: each record a drain writes, a ``lifecycle.bound`` or a
    ``lifecycle.nak``, is compare-and-appended at the tail the drain last read, its
    own writes included; so a death report that lands first is read on the re-run,
    and the worker, released, writes nothing more: no binding, no refusal, no
    sample, no commit, no exit."""
    from runstate.vocabulary.payloads import Topic as T
    from tests.live_helpers import Interloping, Wrapping
    from tests.test_reads import died
    from tests.test_subscriptions import claim_of, sub
    from tests.test_worker import worker

    class Recording(Wrapping):
        """Records each bound's and nak's ``expected_seq``, and the tail then."""

        def __init__(self, inner):
            super().__init__(inner)
            self.writes = []

        def send(self, body, *, topic, name=None, request_id=None, expected_seq=None):
            if topic in (T.LIFECYCLE_BOUND, T.LIFECYCLE_NAK):
                self.writes.append((topic, expected_seq, self.last_seq()))
            return super().send(
                body,
                topic=topic,
                name=name,
                request_id=request_id,
                expected_seq=expected_seq,
            )

    obs = open_run()
    rec = Recording(open_run())
    with worker(rec, sources=["loss"]) as w:
        sub(obs, "lease", every={"commits": 1}, until={"time_seconds": 100})
        sub(obs, "refused", source="acc")
        sub(obs, "lease2", every={"commits": 1}, until={"count": 9})
        w.set("loss", 1.0)
        w.commit()
    topics = [T.LIFECYCLE_BOUND, T.LIFECYCLE_NAK, T.LIFECYCLE_BOUND]
    assert [t for t, _, _ in rec.writes] == topics
    assert all(cas == tail for _, cas, tail in rec.writes)  # each at the tail
    # a death report lands just before the next claim's first write's CAS
    ip = Interloping(open_run(), obs)
    w2 = worker(ip, resume_from=LatestClaimHead(), sources=["loss"])
    sub(obs, "late", every={"commits": 1}, until={"time_seconds": 100})
    sub(obs, "refused2", source="acc")
    report = died(claim_of(obs), "killed")
    ip.arm(report.body, topic=report.topic)
    before = obs.last_seq()
    w2.set("loss", 2.0)
    assert w2.commit() is True and w2.released
    assert w2.heartbeat() is True and w2.retire() is True
    w2.emit("acc", 1.0)
    w2.stopped(completed=False, error=None)
    assert [e.topic for e in obs.read(after=before)] == [T.SUPERVISOR_DIED]
