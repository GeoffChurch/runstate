"""The staged 0.4.0 Worker's subscriptions, ``serve()`` and ``retire()``
(docs/specs/commits-and-lineage.md §2, "Requests"; §3, subscription-v0.4; §4, "A
drain", "At every position change", "The exit", "The drivers"; plan Task 12).

A subscription samples a source at the nodes where it fires, and the node names it:
``fired`` the subscriptions that fired there, ``expired`` those whose schedule
ended there. A lease (a time or count atom) is the claim's, bound by
``lifecycle.bound`` before it registers; a durable one is the lineage's.

The tests that involve a compare-and-append are parametrized over the backends by
the ``open_run`` factory, whose handles share one run.

The §9 interop tests for items 6, 7, 9, 10, 11 and 14 are
``tests/test_interop.py``'s.
"""

import threading
import time

import pytest

from runstate.checkpoint import NoSave
from runstate.launcher import (
    ThreadLauncher,
    ThreadTarget,
    ensure_served,
    report_death,
)
from runstate.observables import (
    AtNode,
    LatestClaimHead,
    LiveDemand,
    answers,
    live_demand,
    peek_terminal,
)
from runstate.vocabulary.coordinate import Commits, Target
from runstate.vocabulary.payloads import Outcome, Topic
from runstate.worker import Worker
from tests.live_helpers import Interloping
from tests.test_death_reports import claim, dead_handle
from tests.test_reads import append, died
from tests.test_worker import (
    Clock,
    conforms,
    naks,
    send_stop,
    send_withdraw,
    worker,
)

# ----- helpers -----------------------------------------------------------------------


def sub(ch, request_id, *, source="loss", **schedule):
    """Send a ``control.subscribe`` for ``source`` (``None``: pure demand)."""
    return ch.send(
        schedule, topic=Topic.CONTROL_SUBSCRIBE, name=source, request_id=request_id
    )


def samples(ch, name="loss"):
    """The values of the ``value`` records named ``name``, in seq order."""
    return [e.body["value"] for e in ch.read(topics=[Topic.VALUE], name=name)]


def node_bodies(ch):
    """The bodies of the nodes, the commits and the exits, in seq order."""
    topics = [Topic.LIFECYCLE_COMMIT, Topic.LIFECYCLE_STOPPED]
    return [e.body for e in ch.read(topics=topics)]


def firing(ch):
    """(fired, expired) of each node, each sorted, in seq order."""
    return [(sorted(b["fired"]), sorted(b["expired"])) for b in node_bodies(ch)]


def bindings(ch):
    """(request_id, claim_seq) of every ``lifecycle.bound``, in seq order."""
    return [
        (e.request_id, e.body["claim_seq"])
        for e in ch.read(topics=[Topic.LIFECYCLE_BOUND])
    ]


def demand(ch):
    """The ids ``live_demand`` names, sorted."""
    return sorted(e.request_id for e in live_demand(ch))


def claim_of(ch):
    """The seq of the newest claim."""
    return ch.latest(Topic.LIFECYCLE_STARTED).seq


def last_commit(ch):
    return ch.latest(Topic.LIFECYCLE_COMMIT).seq


def answered(ch, request_id, *, head=None):
    """The values of ``answers`` at ``head`` (default the latest claim's)."""
    head = LatestClaimHead() if head is None else head
    return [e.value for e in answers(ch, request_id, head=head)]


def end(ch):
    """End the newest claim from outside, as a supervisor would."""
    report_death(
        ch, claim_of(ch), outcome=Outcome.KILLED, evidence="test: ended", force=True
    )


LEASE = {"every": {"commits": 1}, "until": {"time_seconds": 100}}


# ----- the answer fold agrees with the Worker (§5) ---------------------------------


def test_live_demand_agrees_with_the_worker(open_run):
    """After every commit, ``live_demand`` names exactly the subscriptions the Worker
    holds live for its claim: a time lease until its ``until``, a durable one until
    its own, a one-shot until it fires, a pure demand until its ``until``, one on a
    source with no value yet throughout; a refused one and a withdrawn one never
    after their answer. Both are per the head's lineage for a durable subscription
    (§2, amended 2026-10-30): after a rewind to the root, and in a claim resuming
    from an older node, each durable one is live again until the new lineage
    expires it, and a lease stays spent."""
    ch, obs, clock = open_run(), open_run(), Clock()
    sub(obs, "lease", every={"commits": 1}, until={"time_seconds": 10})
    sub(obs, "durable", every={"commits": 2}, until={"commits": 3})
    sub(obs, "once")
    sub(obs, "pure", source=None, until={"commits": 2})
    sub(obs, "dry", source="lr", every={"commits": 1})
    sub(obs, "refused", source="acc")
    sub(obs, "withdrawn", every={"commits": 1})
    seen = []

    def agree(w):
        assert w._live() == set(demand(obs)), len(seen)
        seen.append(sorted(w._live()))

    with worker(ch, sources=["loss", "lr"], now=clock) as w:
        w.set("loss", 1.0)
        for i in range(5):
            clock.t = 3.0 * i
            if i == 2:
                send_withdraw(obs, "withdrawn")
            w.commit()
            agree(w)
        assert seen[0] == ["dry", "durable", "lease", "pure", "withdrawn"]
        assert seen[-1] == ["dry"]
        c = [e.seq for e in obs.read(topics=[Topic.LIFECYCLE_COMMIT])]
        # A rewind (§2, "Per lineage"): the durable subscriptions lineage A expired
        # ("once" at c0, "pure" at c1, "durable" at c2) are live on B, the root's;
        # the lease's expiry was its claim's.
        w.rewind(None)
        agree(w)
        assert seen[-1] == ["dry", "durable", "once", "pure"]
        w.set("loss", 1.0)
        for _ in range(3):
            w.commit()
            agree(w)
        assert seen[-3:] == [
            ["dry", "durable", "pure"],  # B1: "once" fired and expired
            ["dry", "durable"],  # B2: commits 2, "pure" expired
            ["dry"],  # B3: commits 3, "durable" expired on B too
        ]
    # A claim that resumes from an older node, c0: its lineage expired "once"
    # only, so "durable" and "pure" are live there again.
    w2 = worker(open_run(), resume_from=AtNode(c[0]), sources=["loss", "lr"])
    w2.heartbeat()  # a drain: the claim registers what it serves
    agree(w2)
    assert seen[-1] == ["dry", "durable", "pure"]
    w2.set("loss", 1.0)
    for _ in range(2):
        w2.commit()
        agree(w2)
    assert seen[-2:] == [["dry", "durable"], ["dry"]]  # commits 2, then 3


# ----- registration (§3, subscription-v0.4; §4, "A drain") -------------------------


def test_a_one_shot_samples_the_register_once_and_expires(open_run):
    ch, obs = open_run(), open_run()
    sub(obs, "r1")
    w = worker(ch, sources=["loss"])
    w.set("loss", 0.5)
    w.commit()
    w.commit()
    vals = obs.read(topics=[Topic.VALUE])
    assert [(v.name, v.request_id, v.body) for v in vals] == [
        ("loss", None, {"value": 0.5, "t": 0.0})
    ]
    assert node_bodies(obs)[0]["values"] == [vals[0].seq]
    assert firing(obs) == [(["r1"], ["r1"]), ([], [])]
    assert answered(obs, "r1") == [0.5]
    conforms(obs)


def test_two_subscriptions_firing_on_one_source_at_one_node_write_one_sample(
    open_run,
):
    ch, obs = open_run(), open_run()
    sub(obs, "a")
    sub(obs, "b", every={"commits": 1})
    w = worker(ch, sources=["loss"])
    w.emit("acc", 0.9)
    w.set("loss", 0.5)
    w.commit()
    assert samples(obs) == [0.5]  # one sample, though two fired
    sample = obs.latest(Topic.VALUE, "loss").seq
    node = node_bodies(obs)[0]
    assert node["values"] == [obs.latest(Topic.VALUE, "acc").seq, sample]
    assert sorted(node["fired"]) == ["a", "b"] and node["expired"] == ["a"]
    assert answered(obs, "a") == answered(obs, "b") == [0.5]


def test_a_source_with_no_value_yet_does_not_fire(open_run):
    ch, obs = open_run(), open_run()
    sub(obs, "r1")
    w = worker(ch, sources=["loss"])
    w.commit()  # no value: the subscription waits for the first set
    w.set("loss", 1.0)
    w.commit()
    assert firing(obs) == [([], []), (["r1"], ["r1"])]
    assert samples(obs) == [1.0]


def test_counts_are_taken_at_the_node_being_written(open_run):
    """``from {commits: 1}`` fires at the first commit, and a ``{stream, n}`` atom
    counts the node's own pending values (§3, "Where")."""
    ch, obs = open_run(), open_run()
    sub(obs, "c", **{"from": {"commits": 1}})
    sub(obs, "s", **{"from": {"stream": "x", "n": 2}})
    w = worker(ch, sources=["loss"])
    w.set("loss", 1.0)
    w.emit("x", 0)
    w.commit()  # commits 1 at the node; x counts 1
    w.emit("x", 1)
    w.commit()  # x counts 2 at the node being written
    assert firing(obs) == [(["c"], ["c"]), (["s"], ["s"])]


def test_a_future_from_waits_and_a_past_one_fires_at_once(open_run):
    """A ``from`` the position has passed fires at the next node; one ahead of it
    waits, unrefused (§3: every threshold is a ``>=``)."""
    ch, obs = open_run(), open_run()
    w = worker(ch, sources=["loss"])
    w.commit()
    w.commit()
    sub(obs, "past", **{"from": {"commits": 1}})
    sub(obs, "future", **{"from": {"commits": 5}})
    w.set("loss", 1.0)
    for _ in range(3):
        w.commit()
    assert naks(obs) == []
    assert firing(obs)[2:] == [(["past"], ["past"]), ([], []), (["future"], ["future"])]


def test_every_is_the_delta_rule_under_batching(open_run):
    """``every`` fires when ``count(now) - count(at the last firing) >= n``, not
    rounded to a multiple, with no interpolation inside a jump."""
    ch, obs = open_run(), open_run()
    sub(obs, "x10", every={"stream": "x", "n": 10})
    sub(obs, "c2", every={"commits": 2})
    w = worker(ch, sources=["loss"])
    w.set("loss", 1.0)
    for _ in range(7):
        for _ in range(4):
            w.emit("x", 0)
        w.commit()
    x10 = [i for i, b in enumerate(node_bodies(obs)) if "x10" in b["fired"]]
    c2 = [i for i, b in enumerate(node_bodies(obs)) if "c2" in b["fired"]]
    assert x10 == [0, 3, 6]  # x counts 4, 16, 28 there
    assert c2 == [0, 2, 4, 6]


def test_every_zero_fires_at_every_evaluation_the_exit_included(open_run):
    ch, obs = open_run(), open_run()
    sub(obs, "all", every={"commits": 0})
    with worker(ch, sources=["loss"]) as w:
        for v in (1.0, 2.0):
            w.set("loss", v)
            w.commit()
        w.heartbeat()  # not an evaluation
        w.set("loss", 3.0)
    assert firing(obs) == [(["all"], [])] * 3
    assert samples(obs) == [1.0, 2.0, 3.0]
    assert obs.latest(Topic.LIFECYCLE_STOPPED).body["values"] == [
        obs.latest(Topic.VALUE, "loss").seq
    ]


def test_an_until_that_already_holds_expires_at_the_next_node(open_run):
    """Not refused (0.3.0 refused it as unsatisfiable): whether it holds depends on
    the position. It expires at the next node, firing nothing (§3, "Expiry")."""
    ch, obs = open_run(), open_run()
    w = worker(ch, sources=["loss"])
    w.commit()
    w.commit()
    sub(obs, "late", until={"commits": 1})
    w.set("loss", 1.0)
    w.commit()
    w.commit()
    assert naks(obs) == []
    assert firing(obs)[2:] == [([], ["late"]), ([], [])]
    assert samples(obs) == []
    assert demand(obs) == []


def test_pure_demand_never_fires_and_keeps_serve_alive_until_its_until_or_withdrawal(
    open_run,
):
    ch, obs = open_run(), open_run()
    sub(obs, "until", source=None, until={"commits": 3})
    with worker(ch, sources=["loss"]) as w:
        served = list(w.serve())
    assert served == [0, 1, 2]
    assert firing(obs) == [([], []), ([], []), ([], ["until"]), ([], [])]
    exit_ = obs.latest(Topic.LIFECYCLE_STOPPED).body
    assert (exit_["completed"], exit_["error"], exit_["values"]) == (False, None, [])
    # with no until, it lasts until withdrawn
    sub(obs, "forever", source=None)
    with worker(open_run(), resume_from=LatestClaimHead(), sources=[]) as w:
        served = []
        for i in w.serve():
            served.append(i)
            if i == 4:
                send_withdraw(obs, "forever")
    assert served == [3, 4]
    assert obs.read(topics=[Topic.VALUE]) == []
    assert all(fired == [] for fired, _ in firing(obs))
    assert demand(obs) == []


@pytest.mark.parametrize(
    "schedule, key",
    [
        ({"from": {"commits": 1}}, "from"),
        ({"every": {"commits": 1}}, "every"),
        ({"until": {"count": 1}}, "count"),
    ],
)
def test_pure_demand_with_from_every_or_count_is_refused_malformed_and_spent(
    open_run, schedule, key
):
    ch, obs = open_run(), open_run()
    sub(obs, "pure", source=None, **schedule)
    with worker(ch) as w:
        w.commit()
        sub(obs, "pure", source=None)  # a re-send of the spent id: skipped
        w.commit()
        assert w.pinned is False
    assert naks(obs) == [("pure", "malformed")]
    assert key in obs.latest(Topic.LIFECYCLE_NAK).body["message"]
    assert demand(obs) == []


def test_a_subscription_the_claims_declaration_cannot_serve_is_refused_unsatisfiable(
    open_run,
):
    """Judged against the claim's declaration (§3, "Refusals"): a target that is
    no declared source (an eager stream is read, not subscribed); a progress atom
    naming a declared source (its count depends on who asked); and an empty window,
    an absent ``from`` read as ``from`` 0."""
    ch, obs = open_run(), open_run()
    sub(obs, "eager", source="acc")
    sub(obs, "counted", **{"from": {"stream": "lr", "n": 1}})
    sub(obs, "empty", **{"from": {"commits": 5}}, until={"commits": 3})
    sub(obs, "empty0", until={"commits": 0})
    w = worker(ch, sources=["loss", "lr"])
    w.set("loss", 1.0)
    w.commit()
    assert naks(obs) == [
        ("eager", "unsatisfiable"),
        ("counted", "unsatisfiable"),
        ("empty", "unsatisfiable"),
        ("empty0", "unsatisfiable"),
    ]
    assert samples(obs) == [] and demand(obs) == []


@pytest.mark.parametrize(
    "schedule, key",
    [
        ({"frm": {"commits": 5}}, "frm"),  # a misspelled slot
        ({"from": None}, "from"),  # a null slot: absent is absent
        ({"from": {"frobnicate": 1}}, "frobnicate"),  # an unknown atom
        ({"from": {"step": 100}}, "step"),  # the removed step atom
        ({"every": {"any": []}}, "any"),  # minItems 1
        ({"every": {"all": []}}, "all"),
        ({"from": {"commits": True}}, "commits"),  # a bool posing as a number
        ({"from": {"commits": -1}}, "commits"),  # every threshold is n >= 0
        ({"from": {"count": 1}}, "count"),  # count only in until
        ({"every": {"count": 2}}, "count"),
        ({"from": {"any": [{"count": 1}, {"commits": 5}]}}, "count"),
    ],
)
def test_a_malformed_subscribe_is_refused_naming_its_key_and_the_worker_keeps_serving(
    open_run, schedule, key
):
    ch, obs = open_run(), open_run()
    sub(obs, "bad", **schedule)
    sub(obs, "ok", every={"commits": 1})
    w = worker(ch, sources=["loss"])
    w.set("loss", 1.0)
    assert w.commit() is False  # one bad request is never fatal
    assert w.commit() is False
    assert naks(obs) == [("bad", "malformed")]
    assert key in obs.latest(Topic.LIFECYCLE_NAK).body["message"]
    assert samples(obs) == [1.0, 1.0]
    assert demand(obs) == ["ok"]


@pytest.mark.parametrize("request_id", [None, ""])
def test_a_nameless_subscribe_is_refused_once_and_registers_nothing(
    open_run, request_id
):
    ch, obs = open_run(), open_run()
    sub(obs, request_id, every={"commits": 1})
    w = worker(ch, sources=["loss"])
    w.set("loss", 1.0)
    w.commit()
    w.commit()
    assert naks(obs) == [(None, "malformed")]
    assert samples(obs) == [] and w.pinned is False


def test_a_full_schedule_registers_and_serves(open_run):
    """The boundary passes the whole grammar: three slots, nested ``any`` and
    ``all``, and a ``count`` atom in ``until``, where it is grammatical."""
    ch, obs = open_run(), open_run()
    schedule = {
        "from": {"commits": 1},
        "every": {"any": [{"commits": 2}, {"time_seconds": 60.0}]},
        "until": {"all": [{"commits": 100}, {"count": 3}]},
    }
    sub(obs, "full", **schedule)
    w = worker(ch, sources=["loss"])
    w.set("loss", 0.5)
    w.commit()
    assert naks(obs) == []
    assert firing(obs) == [(["full"], [])]
    assert bindings(obs) == [("full", claim_of(obs))]  # a count atom: a lease


def test_a_withdrawn_subscription_is_spent_wherever_its_withdrawal_sits(open_run):
    """A withdrawal spends what it names, by name: the subscription it follows,
    and one it precedes. A later send reusing a spent id is dead on arrival, with
    no refusal: the replacement idiom is a fresh id."""
    ch, obs = open_run(), open_run()
    sub(obs, "r1", every={"commits": 1})
    send_withdraw(obs, "early")  # before the subscribe it names
    sub(obs, "early", every={"commits": 1})
    w = worker(ch, sources=["loss"])
    w.set("loss", 1.0)
    w.commit()
    send_withdraw(obs, "r1")
    w.commit()
    sub(obs, "r1", every={"commits": 1})  # the spent id again
    sub(obs, "r2", every={"commits": 1})  # the replacement idiom
    w.commit()
    assert [b["fired"] for b in node_bodies(obs)] == [["r1"], [], ["r2"]]
    assert naks(obs) == []  # silently: no refusal
    assert w.pinned is True  # by r2 alone
    assert demand(obs) == ["r2"]


def test_expiry_is_named_by_its_node_and_writes_no_record(open_run):
    """A schedule ends at a node, which names it in ``expired``: an ``until`` that
    holds, and a one-shot that fires. No record is written for it, and a refused
    subscription is answered by its ``nak`` alone, never by an ``expired``."""
    ch, obs, clock = open_run(), open_run(), Clock()
    sub(obs, "until", every={"commits": 1}, until={"time_seconds": 10})
    sub(obs, "once")
    sub(obs, "bad", every={"bogus": 1})
    w = worker(ch, sources=["loss"], now=clock)
    w.set("loss", 1.0)
    w.commit()
    clock.t = 11.0
    w.commit()
    w.commit()
    assert firing(obs) == [(["once", "until"], ["once"]), ([], ["until"]), ([], [])]
    assert naks(obs) == [("bad", "malformed")]
    writes = {e.topic for e in obs.read() if not e.topic.startswith("control.")}
    assert writes == {
        Topic.LIFECYCLE_STARTED,
        Topic.LIFECYCLE_BOUND,
        Topic.LIFECYCLE_NAK,
        Topic.LIFECYCLE_COMMIT,
        Topic.VALUE,
    }


def test_a_refused_subscribe_is_not_refused_again_by_the_next_claim(open_run):
    ch, obs = open_run(), open_run()
    sub(obs, "r1", source="acc")  # no declared source
    with worker(ch) as w:
        w.commit()
    with worker(open_run(), resume_from=LatestClaimHead()) as w:
        w.commit()
    assert naks(obs) == [("r1", "unsatisfiable")]


def test_a_resend_with_another_target_or_class_is_refused_and_spent(open_run):
    """The first send fixes the subscription: a re-send that changes its target,
    or its class (here a durable one tightened into a lease, the A1 attack), is
    refused ``malformed``, and the refusal spends the id: the registration is
    dropped, and nothing resurrects it."""
    ch, obs = open_run(), open_run()
    sub(obs, "target", every={"commits": 1})
    sub(obs, "class", every={"commits": 1})
    w = worker(ch, sources=["loss", "lr"])
    w.set("loss", 1.0)
    w.set("lr", 0.1)
    w.commit()
    sub(obs, "target", source="lr", every={"commits": 1})
    sub(obs, "class", every={"commits": 1}, until={"time_seconds": 100})
    w.commit()
    sub(obs, "target", every={"commits": 1})  # spent: skipped
    w.commit()
    assert naks(obs) == [("target", "malformed"), ("class", "malformed")]
    assert [sorted(b["fired"]) for b in node_bodies(obs)] == [
        ["class", "target"],
        [],
        [],
    ]
    assert bindings(obs) == [] and w.pinned is False
    assert demand(obs) == []


def test_an_accepted_resend_restarts_a_lease_clock_and_fire_count_and_keeps_lineage_baselines(
    open_run,
):
    """An accepted re-send registers afresh for what the claim holds: a lease's
    time atoms count from the re-send and its fire count returns to 0, while its
    lineage baselines stay, read on the lineage; it is not bound again. A durable
    re-send replaces its schedule, its lineage state kept."""
    ch, obs, clock = open_run(), open_run(), Clock()
    count = {"every": {"commits": 2}, "until": {"count": 2}}
    sub(obs, "count", **count)
    sub(obs, "clock", every={"commits": 1}, until={"time_seconds": 10})
    sub(obs, "durable", every={"commits": 1})
    w = worker(ch, sources=["loss"], now=clock)
    w.set("loss", 1.0)
    w.commit()  # c1: all three fire, the first firing immediate
    clock.t = 8.0
    sub(obs, "count", **count)
    sub(obs, "clock", every={"commits": 1}, until={"time_seconds": 10})
    sub(obs, "durable", every={"commits": 1}, until={"commits": 2})
    for t in (8.0, 12.0, 13.0, 19.0):
        clock.t = t
        w.commit()  # c2 .. c5; c2's drain registers the re-sends at 8
    nodes = node_bodies(obs)
    # the baseline is c1's firing, on the lineage: c2's delta is 1, so no fire;
    # the fire count restarted, so the firing at c3 is its first, and c5 its second
    assert [("count" in b["fired"], "count" in b["expired"]) for b in nodes] == [
        (True, False),
        (False, False),
        (True, False),
        (False, False),
        (True, True),
    ]
    # the clock counts from the re-send's registration at 8, not from 0: 12 is
    # short of 10 past it, and 19 is not
    assert [n for n, b in enumerate(nodes) if "clock" in b["expired"]] == [4]
    # the durable re-send's until ends it at c2, its baseline still c1's firing
    assert [("durable" in b["fired"], "durable" in b["expired"]) for b in nodes][
        :2
    ] == [(True, False), (False, True)]
    assert sorted(r for r, _ in bindings(obs)) == ["clock", "count"]  # once each
    assert naks(obs) == []


def test_a_durable_subscription_crosses_claims_and_a_lease_does_not(open_run):
    """A durable subscription lives per lineage, so the next claim on it serves it;
    a lease is per claim, so it is void once its claim has ended. Any time atom
    anywhere makes the schedule a lease, so a mixed one is bound and voided, and
    its durable twin carries over."""
    ch, obs = open_run(), open_run()
    sub(obs, "durable", every={"commits": 1})
    sub(obs, "lease", **LEASE)
    either = [{"commits": 1000}, {"time_seconds": 100}]
    sub(obs, "mixed", every={"commits": 1}, until={"any": either})
    sub(obs, "twin", every={"commits": 1}, until={"commits": 1000})
    with worker(ch, sources=["loss"]) as w:
        w.set("loss", 1.0)
        w.commit()
    first = claim_of(obs)
    assert sorted(bindings(obs)) == [("lease", first), ("mixed", first)]
    w2 = worker(open_run(), resume_from=LatestClaimHead(), sources=["loss"])
    w2.set("loss", 2.0)
    w2.commit()
    fired = [r for b in node_bodies(obs) for r in b["fired"]]
    assert sorted(fired) == ["durable", "durable", "lease", "mixed", "twin", "twin"]
    assert demand(obs) == ["durable", "twin"]


def test_a_lease_is_void_only_through_its_binding(open_run):
    """A lease is void only through the claim that registered it, recorded as
    ``lifecycle.bound``: claims that died before draining it never took it in, so
    they bind and void nothing, and the first claim to drain it binds it to itself
    and serves it. Once that claim ends, by a death report or its own exit, the
    lease is void for every later claim, silently: no ``nak``, no record."""
    ch, obs = open_run(), open_run()
    sub(obs, "L", **LEASE)
    worker(open_run())  # claims, and dies before any drain
    end(obs)
    worker(open_run())
    end(obs)
    assert demand(obs) == ["L"]  # nobody took it in: not void
    w = worker(ch, sources=["loss"])
    w.set("loss", 1.0)
    w.commit()
    assert bindings(obs) == [("L", claim_of(obs))]
    assert demand(obs) == ["L"] and w.pinned is True  # its claim is held
    end(obs)  # it crashed
    assert demand(obs) == []
    w2 = worker(open_run(), sources=["loss"])
    w2.set("loss", 2.0)
    w2.commit()
    assert samples(obs) == [1.0] and w2.pinned is False  # not re-anchored
    assert naks(obs) == []
    # a lease registered during a claim that then exits cleanly
    sub(obs, "L2", **LEASE)
    w2.commit()
    w2.stopped(completed=False, error=None)
    assert demand(obs) == []  # no wasted relaunch after a clean stop


def test_answers_through_time_atoms_a_rewind_and_a_resume(open_run):
    """``answers`` reads ``fired``: one sample per firing, on the head's lineage,
    a time ``every`` recurring on its own clock, through a rewind and a resume."""
    ch, obs, clock = open_run(), open_run(), Clock()
    sub(obs, "d", every={"commits": 1})
    sub(obs, "t", every={"time_seconds": 5})
    w = worker(ch, sources=["loss"], now=clock)
    for t, v in ((0.0, 0.0), (3.0, 1.0), (6.0, 2.0)):
        clock.t = t
        w.set("loss", v)
        w.commit()
    c1 = ch.read(topics=[Topic.LIFECYCLE_COMMIT])[0].seq
    assert answered(obs, "d") == [0.0, 1.0, 2.0]
    assert answered(obs, "t") == [0.0, 2.0]  # recurs on its clock
    w.rewind(AtNode(c1))
    clock.t = 7.0
    w.set("loss", 3.0)
    w.commit()
    assert answered(obs, "d") == [0.0, 3.0]  # the lineage's answers
    assert answered(obs, "t") == [0.0]
    w.stopped(completed=False, error=None)
    w2 = worker(open_run(), resume_from=LatestClaimHead(), sources=["loss"])
    w2.set("loss", 4.0)
    w2.commit()
    assert answered(obs, "d") == [0.0, 3.0, 4.0]
    assert answered(obs, "t") == [0.0]  # void with its claim


def test_a_durable_subscription_is_answered_once_per_lineage_and_not_on_a_lineage_that_expired_it(
    open_run,
):
    """A one-shot durable subscription answered on a branch a rewind abandons is
    answered again on the new one, once; on a lineage that expired it, never, a
    later claim continuing it included. ``live_demand`` agrees: live while the
    head's lineage has not expired it."""
    ch, obs = open_run(), open_run()
    sub(obs, "once")
    w = worker(ch, sources=["loss"])
    w.set("loss", 1.0)
    w.commit()
    c1 = last_commit(obs)
    w.commit()
    w.rewind(None)
    assert demand(obs) == ["once"]  # the root's lineage has not expired it
    w.set("loss", 2.0)
    w.commit()
    c3 = last_commit(obs)
    w.commit()
    w.rewind(AtNode(c1))
    w.set("loss", 3.0)
    w.commit()
    assert samples(obs) == [1.0, 2.0]
    assert answered(obs, "once", head=AtNode(c3)) == [2.0]
    assert answered(obs, "once") == [1.0]
    assert demand(obs) == []  # spent: c1, on the head's lineage, expired it
    w.stopped(completed=False, error=None)
    w2 = worker(open_run(), resume_from=LatestClaimHead(), sources=["loss"])
    w2.set("loss", 4.0)
    w2.commit()
    assert samples(obs) == [1.0, 2.0]  # not resurrected on its lineage


def test_lineage_baselines_rebuild_after_a_rewind_for_both_classes_and_a_lease_keeps_its_time_baseline(
    open_run,
):
    ch, obs, clock = open_run(), open_run(), Clock()
    sub(obs, "d", every={"commits": 2})
    sub(obs, "L", every={"commits": 2}, until={"time_seconds": 1000})
    sub(obs, "T", every={"time_seconds": 10})
    w = worker(ch, sources=["loss"], now=clock)
    w.set("loss", 1.0)
    for t in (0.0, 1.0, 2.0):  # c1 c2 c3
        clock.t = t
        w.commit()
    c2 = ch.read(topics=[Topic.LIFECYCLE_COMMIT])[1].seq
    w.rewind(AtNode(c2))
    w.set("loss", 1.0)
    for t in (3.0, 11.0):  # c4 c5
        clock.t = t
        w.commit()
    nodes = node_bodies(obs)
    assert [sorted(b["fired"]) for b in nodes] == [
        ["L", "T", "d"],  # c1: the first firing is immediate
        [],
        ["L", "d"],  # c3: commits 3, 2 past c1's firing
        # c4: commits 3 on the new lineage, whose newest firing is c1's, so 2
        # past it; the time baseline is the claim's, 0, which the rewind kept
        ["L", "d"],
        ["T"],  # c5: 11 since the claim's last firing of T
    ]


def test_a_lease_keeps_its_clock_fire_count_and_expiry_across_a_rewind_and_is_void_once_its_claim_ends(
    open_run,
):
    ch, obs, clock = open_run(), open_run(), Clock()
    sub(obs, "count", every={"commits": 1}, until={"count": 2})
    sub(obs, "clock", every={"commits": 1}, until={"time_seconds": 10})
    sub(obs, "held", **LEASE)
    w = worker(ch, sources=["loss"], now=clock)
    w.set("loss", 1.0)
    w.commit()  # c1: the first firing of each
    clock.t = 5.0
    w.rewind(None)
    w.set("loss", 1.0)
    clock.t = 11.0
    w.commit()  # c2: count's second firing ends it; clock 11 since registering
    w.rewind(None)
    w.set("loss", 1.0)
    w.commit()  # c3: neither re-arms, though the lineage holds no expiry
    nodes = node_bodies(obs)
    assert [(sorted(b["fired"]), sorted(b["expired"])) for b in nodes] == [
        (["clock", "count", "held"], []),
        (["count", "held"], ["clock", "count"]),
        (["held"], []),
    ]
    w.stopped(completed=False, error=None)
    w2 = worker(open_run(), resume_from=LatestClaimHead(), sources=["loss"])
    w2.set("loss", 2.0)
    w2.commit()
    assert node_bodies(obs)[-1]["fired"] == [] and w2.pinned is False
    assert [r for r, _ in bindings(obs)] == ["count", "clock", "held"]  # unrebound
    assert demand(obs) == []


def test_a_count_lease_whose_service_dies_at_its_first_firing_stops_the_waker_after_one_launch(
    tmp_path,
):
    """A count lease is the claim's: its budget is never refunded to a later claim.
    The service fires it once and dies; its claim's end voids the lease, so the
    waker, holding its ``LiveDemand``, launches once and never again. The same
    holds for a lease nobody will renew behind a crash-birth that never drained
    it: one launch."""
    launcher = ThreadLauncher(root=tmp_path)
    obs = launcher.create_channel("svc")
    launches = []

    def service(ch):
        launches.append(ch)
        with Worker(
            ch, resume_from=LatestClaimHead(), sources=["load"], save=NoSave()
        ) as w:
            for i in w.serve():
                if i == 1:
                    raise RuntimeError("the service dies after its first firing")
                w.set("load", 1.0)

    target = ThreadTarget(service, args=(), kwargs={})
    demand_fold = LiveDemand()
    assert ensure_served(launcher, "svc", target, demand=demand_fold) is None
    sub(obs, "lease", source="load", every={"commits": 1}, until={"count": 3})
    append(obs, [(2, claim(dead_handle()))])  # a crash-birth that never drained it
    for _ in range(5):
        h = ensure_served(launcher, "svc", target, demand=demand_fold)
        if h is None:
            break
        h.wait()
    assert len(launches) == 1
    assert samples(obs, "load") == [1.0]  # one firing, of a budget of 3
    assert peek_terminal(obs).outcomes == frozenset({Outcome.ERRORED})
    assert demand(obs) == []


def test_ensure_served_launches_a_service_that_serves_its_demand_and_retires(
    tmp_path,
):
    """Lazy launch: no demand, no launch; demand with no held claim, a launch,
    whose ``serve()`` serves it and retires at zero."""
    launcher = ThreadLauncher(root=tmp_path)
    obs = launcher.create_channel("svc")

    def service(ch):
        with Worker(
            ch, resume_from=LatestClaimHead(), sources=["load"], save=NoSave()
        ) as w:
            for i in w.serve():
                w.set("load", float(i))

    target = ThreadTarget(service, args=(), kwargs={})
    fold = LiveDemand()
    assert ensure_served(launcher, "svc", target, demand=fold) is None
    sub(obs, "d", source="load", every={"commits": 1}, until={"commits": 4})
    h = ensure_served(launcher, "svc", target, demand=fold)
    assert h is not None
    h.wait()
    assert samples(obs, "load") == [0.0, 1.0, 2.0]
    assert peek_terminal(obs).outcomes == frozenset({Outcome.PREEMPTED})
    assert ensure_served(launcher, "svc", target, demand=fold) is None


# ----- the drain's compare-and-append (§4, "A drain") -------------------------------


def test_a_lease_binding_is_cas_and_a_death_report_landing_first_leaves_it_unbound(
    open_run,
):
    obs = open_run()
    ip = Interloping(open_run(), obs)
    w = worker(ip, sources=["loss"])
    claim = claim_of(obs)
    sub(obs, "lease", **LEASE)
    report = died(claim, "killed")
    ip.arm(report.body, topic=report.topic)
    w.set("loss", 1.0)
    assert w.commit() is True and w.released
    assert ip.fired and bindings(obs) == []
    assert demand(obs) == ["lease"]  # unbound, so not void
    successor = worker(open_run(), sources=["loss"])
    successor.set("loss", 2.0)
    successor.commit()
    assert bindings(obs) == [("lease", claim_of(obs))]
    assert samples(obs) == [2.0]


def test_a_refusal_is_cas_and_a_death_report_landing_first_leaves_the_request_for_a_successor(
    open_run,
):
    obs = open_run()
    ip = Interloping(open_run(), obs)
    w = worker(ip, sources=["loss"])
    sub(obs, "acc", source="acc", every={"commits": 1})  # no source of this claim
    report = died(claim_of(obs), "killed")
    ip.arm(report.body, topic=report.topic)
    assert w.commit() is True and w.released
    assert naks(obs) == []
    successor = worker(open_run(), sources=["acc"])  # whose sources serve it
    successor.set("acc", 0.9)
    successor.commit()
    assert samples(obs, "acc") == [0.9] and naks(obs) == []


def test_a_drain_that_loses_its_cas_binds_and_registers_a_lease_exactly_once(
    open_run,
):
    """Review Focus 3: a concurrent control writer lands a stop just before the
    worker's first ``bound`` CAS, which loses. The drain runs again from the
    lease: it reads the stop, binds the lease once, at the new tail, and registers
    it only then, so the lease fires once, at the node that commits the drain."""
    obs = open_run()
    ip = Interloping(open_run(), obs)
    w = worker(ip, sources=["loss"])
    sub(obs, "lease", **LEASE)
    ip.arm({}, topic=Topic.CONTROL_STOP, request_id="s")
    w.set("loss", 1.0)
    assert w.commit() is True  # the stop the re-drain read holds at once
    assert ip.fired and ip.cas == 3  # the claim, the lost bound, the bound
    assert bindings(obs) == [("lease", claim_of(obs))]
    stop = obs.read(topics=[Topic.CONTROL_STOP])[0].seq
    assert obs.latest(Topic.LIFECYCLE_BOUND).seq > stop  # at the tail it read
    assert samples(obs) == [1.0] and firing(obs) == [(["lease"], [])]
    w.stopped(completed=False, error=None)
    assert obs.latest(Topic.LIFECYCLE_STOPPED).body["honored"] == ["s"]
    assert len(bindings(obs)) == 1


def test_a_released_worker_binds_no_lease(open_run):
    ch, obs = open_run(), open_run()
    w = worker(ch, sources=["loss"])
    sub(obs, "lease", **LEASE)
    end(obs)
    w.set("loss", 1.0)
    assert w.commit() is True and w.released
    assert bindings(obs) == [] and samples(obs) == []
    assert w.retire() is True and w.pinned is False


# ----- what evaluates (§3, "When"; §4, "The exit") ----------------------------------


def test_a_heartbeat_fires_no_subscription(open_run):
    ch, obs = open_run(), open_run()
    sub(obs, "all", every={"commits": 0})
    w = worker(ch, sources=["loss"])
    w.set("loss", 1.0)
    w.heartbeat()
    assert samples(obs) == []
    assert w.pinned is True  # the heartbeat's drain registered it
    w.commit()
    assert samples(obs) == [1.0]


def test_a_torn_exit_evaluates_no_subscription(open_run):
    ch, obs = open_run(), open_run()
    sub(obs, "all", every={"commits": 0})
    with worker(ch, sources=["loss"]) as w:
        w.set("loss", 1.0)
        w.commit()
        w.discard()
    with pytest.raises(RuntimeError, match="boom"):
        with worker(open_run(), resume_from=LatestClaimHead(), sources=["loss"]) as w:
            w.set("loss", 2.0)
            w.commit()
            raise RuntimeError("boom")
    exits = [e.body for e in obs.read(topics=[Topic.LIFECYCLE_STOPPED])]
    assert [(b["values"], b["fired"], b["expired"]) for b in exits] == [
        (None, [], [])
    ] * 2
    assert samples(obs) == [1.0, 2.0]  # the commits' alone


def test_an_exit_that_kept_its_iteration_fires_on_its_post_loop_values(open_run):
    ch, obs = open_run(), open_run()
    sub(obs, "epoch", **{"from": {"stream": "epoch", "n": 1}})
    with worker(ch, sources=["loss"]) as w:
        w.set("loss", 1.0)
        for _ in w.steps(Target(Commits(), 2)):
            pass
        w.emit("epoch", 1)  # post-loop
        w.set("loss", 0.5)
    exit_ = obs.latest(Topic.LIFECYCLE_STOPPED).body
    epoch, sample = obs.latest(Topic.VALUE, "epoch").seq, obs.latest(Topic.VALUE).seq
    assert exit_["values"] == [epoch, sample]  # the pending values, then the sample
    assert (exit_["fired"], exit_["expired"]) == (["epoch"], ["epoch"])
    assert samples(obs) == [0.5]


def test_the_exit_registers_no_subscription_so_the_next_claim_serves_a_late_lease(
    open_run,
):
    ch, obs = open_run(), open_run()
    with worker(ch, sources=["loss"]) as w:
        w.set("loss", 1.0)
        w.commit()
        sub(obs, "late", **LEASE)  # after the last commit
    assert bindings(obs) == [] and firing(obs)[-1] == ([], [])
    assert demand(obs) == ["late"]
    w2 = worker(open_run(), resume_from=LatestClaimHead(), sources=["loss"])
    w2.set("loss", 2.0)
    w2.commit()
    assert bindings(obs) == [("late", claim_of(obs))]
    assert samples(obs) == [2.0]


def test_a_failure_writing_the_exit_samples_gives_a_torn_error_exit(open_run):
    ch, obs = open_run(), open_run()
    sub(obs, "all", every={"commits": 0})
    with pytest.raises(TypeError, match="'loss'.*JSON-serializable"):
        with worker(ch, sources=["loss"]) as w:
            w.set("loss", 1.0)
            w.commit()
            w.set("loss", object())  # its exit sample cannot be written
    exit_ = obs.latest(Topic.LIFECYCLE_STOPPED).body
    assert exit_["completed"] is False and "'loss'" in exit_["error"]
    assert (exit_["values"], exit_["fired"], exit_["expired"]) == (None, [], [])
    assert peek_terminal(obs).outcomes == frozenset({Outcome.ERRORED})


def test_a_lost_exit_cas_does_not_evaluate_again(open_run):
    obs = open_run()
    ip = Interloping(open_run(), obs)
    sub(obs, "all", every={"commits": 0})
    w = worker(ip, sources=["loss"])
    w.set("loss", 1.0)
    w.commit()
    ip.arm({"value": 9, "t": 0.0}, topic=Topic.VALUE, name="other")
    w.stopped(completed=False, error=None)
    assert ip.fired
    assert samples(obs) == [1.0, 1.0]  # the commit's and the exit's, once
    exit_ = obs.latest(Topic.LIFECYCLE_STOPPED).body
    assert exit_["fired"] == ["all"]
    assert exit_["values"] == [obs.latest(Topic.VALUE, "loss").seq]


# ----- pinned, retire() and serve() (§4, "The exit", "The drivers") ----------------


def test_pinned_is_some_lease_of_the_claim_or_durable_subscription_live_on_its_lineage(
    open_run,
):
    """Some subscription is live for this claim: a lease of the claim, or a durable
    one live on its lineage. It reads what the drains registered."""
    ch, obs, clock = open_run(), open_run(), Clock()
    w = worker(ch, sources=["loss"], now=clock)
    assert w.pinned is False
    sub(obs, "r1", every={"time_seconds": 1})
    w.heartbeat()
    assert w.pinned is True
    send_withdraw(obs, "r1")
    w.commit()
    assert w.pinned is False
    sub(obs, "r2", every={"time_seconds": 1}, until={"time_seconds": 10})
    w.set("loss", 1.0)
    w.commit()
    assert w.pinned is True
    clock.t = 11.0
    w.commit()  # the lease lapses inside this commit
    assert w.pinned is False
    sub(obs, "once")
    w.commit()
    assert w.pinned is False  # durable, and expired on this lineage
    w.rewind(None)
    assert w.pinned is True  # re-armed on the new lineage


def test_retire_wins_on_a_quiet_log(open_run):
    ch, obs = open_run(), open_run()
    w = worker(ch)
    w.commit()
    assert w.retire() is True
    e = obs.latest(Topic.LIFECYCLE_STOPPED)
    assert e.body == {
        "completed": False,
        "error": None,
        "claim_seq": claim_of(obs),
        "honored": [],
        "parent": last_commit(obs),
        "values": [],
        "fired": [],
        "expired": [],
        "t": 0.0,
    }
    w.stopped(completed=False, error=None)  # the with block's exit: nothing
    assert w.retire() is True
    assert len(obs.read(topics=[Topic.LIFECYCLE_STOPPED])) == 1


def test_retire_returns_false_when_the_tail_holds_demand(open_run):
    ch, obs = open_run(), open_run()
    w = worker(ch, sources=["loss"])
    w.commit()
    sub(obs, "r1", every={"time_seconds": 1})  # after the last commit
    assert w.retire() is False  # its drain registers it
    assert w.pinned is True
    assert bindings(obs) == [("r1", claim_of(obs))]
    assert obs.latest(Topic.LIFECYCLE_STOPPED) is None


def test_retire_loses_its_cas_to_a_raced_subscribe(open_run):
    """The subscribe lands between retire's last read and its CAS: the CAS loses,
    and the next read registers the subscriber instead of exiting."""
    obs = open_run()
    ip = Interloping(open_run(), obs)
    w = worker(ip, sources=["loss"])
    w.commit()
    ip.arm(
        {"every": {"commits": 1}},
        topic=Topic.CONTROL_SUBSCRIBE,
        name="loss",
        request_id="raced",
    )
    assert w.retire() is False
    assert ip.fired and w.pinned is True
    assert obs.latest(Topic.LIFECYCLE_STOPPED) is None  # nobody exited


def test_retire_refuses_a_malformed_tail_then_wins(open_run):
    """A malformed subscribe in the tail: retire's drain refuses it, an append of
    its own, and the exit then lands at the tail it read."""
    ch, obs = open_run(), open_run()
    w = worker(ch, sources=["loss"])
    w.commit()
    sub(obs, "bad", **{"from": {"count": 1}})
    assert w.retire() is True
    log = obs.read()
    assert log[-1].topic == Topic.LIFECYCLE_STOPPED
    assert naks(obs) == [("bad", "malformed")]


def test_retires_exit_fires_nothing_and_is_extendable(open_run):
    ch, obs = open_run(), open_run()
    sub(obs, "r", every={"commits": 0}, until={"commits": 2})
    with worker(ch, sources=["loss"]) as w:
        for i in w.serve():
            w.set("loss", float(i))
    assert firing(obs) == [(["r"], []), ([], ["r"]), ([], [])]
    exit_ = obs.latest(Topic.LIFECYCLE_STOPPED)
    assert (exit_.body["completed"], exit_.body["error"]) == (False, None)
    assert exit_.body["values"] == []  # extendable: committed, nothing to commit
    w2 = worker(open_run(), resume_from=LatestClaimHead())
    assert w2.resume_node == exit_.seq  # a successor continues from it


def test_serve_serves_a_prestaged_lease_until_it_lapses_then_retires_once(open_run):
    """A prestaged lease is served from the first iteration, until it lapses; the
    careful death is then the claim's one exit."""
    ch, obs, clock = open_run(), open_run(), Clock()
    sub(obs, "r1", source="cpu", every={"time_seconds": 1}, until={"time_seconds": 10})
    served = []
    with worker(ch, sources=["cpu"], now=clock) as w:
        for i in w.serve():
            w.set("cpu", float(i))
            served.append(i)
            clock.t += 6.0  # two iterations outlive the lease
    assert served == [0, 1]  # registered at 0: the lease lapses at the commit at 12
    assert samples(obs, "cpu") == [0.0]
    assert len(obs.read(topics=[Topic.LIFECYCLE_STOPPED])) == 1
    assert firing(obs) == [(["r1"], []), ([], ["r1"]), ([], [])]
    conforms(obs)


def test_serve_ends_on_a_stop(open_run):
    ch, obs = open_run(), open_run()
    sub(obs, "r1", source="cpu", every={"time_seconds": 1})  # stays pinned
    seen = []
    with worker(ch, sources=["cpu"]) as w:
        for i in w.serve():
            w.set("cpu", 0.0)
            seen.append(i)
            if i == 1:
                send_stop(obs, "s1")
    assert seen == [0, 1]  # the stop drains at i == 1's own commit
    exits = obs.read(topics=[Topic.LIFECYCLE_STOPPED])
    assert len(exits) == 1 and exits[0].body["honored"] == ["s1"]


def test_serve_retire_and_stopped_of_a_claim_race_loser_do_nothing(open_run):
    ch, obs = open_run(), open_run()
    sub(obs, "r1", every={"commits": 1})
    winner = worker(ch)
    assert winner.claimed
    before = obs.last_seq()
    with worker(open_run()) as loser:
        assert loser.claimed is False
        assert list(loser.serve()) == []
        assert loser.retire() is True and loser.pinned is False
        loser.stopped(completed=True, error=None)
    assert obs.last_seq() == before


# ----- end to end, on a thread (tests/test_inproc_integration.py's) ----------------


def train(ch, *, until):
    """A chunk of a training run: its loss a source, completed at the target."""
    with Worker(
        ch, resume_from=LatestClaimHead(), sources=["loss"], save=NoSave()
    ) as w:
        for i in w.steps(until):
            w.set("loss", 1.0 / (i + 1))
        if not w.stop_pending:
            w.stopped(completed=True, error=None)


def test_a_subscription_is_served_end_to_end_on_a_thread(tmp_path):
    launcher = ThreadLauncher(root=tmp_path)
    obs = launcher.create_channel("run")
    sub(obs, "obs-1", every={"commits": 1})  # prestaged: drained at the first commit
    target = ThreadTarget(train, args=(), kwargs={"until": Target(Commits(), 5)})
    launcher.launch("run", target).wait()
    assert answered(obs, "obs-1") == [1.0, 1 / 2, 1 / 3, 1 / 4, 1 / 5]
    result = peek_terminal(obs)
    assert result.outcomes == frozenset({Outcome.COMPLETED})
    assert result.node == obs.latest(Topic.LIFECYCLE_STOPPED).seq


def test_a_stop_ends_a_thread_worker_end_to_end(tmp_path):
    launcher = ThreadLauncher(root=tmp_path)
    obs = launcher.create_channel("run2")
    send_stop(obs, "obs-1", **{"from": {"commits": 2}})
    target = ThreadTarget(train, args=(), kwargs={"until": Target(Commits(), 5)})
    launcher.launch("run2", target).wait()
    exit_ = obs.latest(Topic.LIFECYCLE_STOPPED).body
    assert (exit_["completed"], exit_["honored"]) == (False, ["obs-1"])
    assert len(obs.read(topics=[Topic.LIFECYCLE_COMMIT])) == 2
    assert peek_terminal(obs).outcomes == frozenset({Outcome.PREEMPTED})


def test_a_double_launch_leaves_one_claim_and_the_winners_own_verdict(tmp_path):
    """Two launches into one run: one claim wins, the loser writes no claim, and
    both launches end with their ``terminated``; the verdict is the winner's own
    exit, since a ``terminated`` enters no claimed run's verdict (§5)."""
    launcher = ThreadLauncher(root=tmp_path)
    obs = launcher.create_channel("race")
    go = threading.Event()

    def body(ch):
        with Worker(ch, resume_from=None, sources=[], save=NoSave()) as w:
            if w.claimed:
                go.wait(timeout=30)
            w.commit()

    target = ThreadTarget(body, args=(), kwargs={})
    h1 = launcher.launch("race", target)
    deadline = time.monotonic() + 30
    while obs.latest(Topic.LIFECYCLE_STARTED) is None:
        assert time.monotonic() < deadline, "the first launch never claimed"
        time.sleep(0.01)
    h2 = launcher.launch("race", target)
    h2.wait()
    go.set()
    h1.wait()
    starteds = obs.read(topics=[Topic.LIFECYCLE_STARTED])
    assert [e.request_id for e in starteds] == [h1.launch_id]
    terms = obs.read(topics=[Topic.SUPERVISOR_TERMINATED])
    assert {e.request_id for e in terms} == {h1.launch_id, h2.launch_id}
    assert obs.read(topics=[Topic.SUPERVISOR_DIED]) == []
    assert peek_terminal(obs).outcomes == frozenset({Outcome.PREEMPTED})


# ----- a commit's cost by what is registered (§5, "Bounded reads") ------------------


def test_a_commits_cost_does_not_grow_with_the_durables_expired_on_its_lineage():
    """A durable subscription the lineage expired stays registered, live again on
    a lineage that does not expire it; but a commit after the one that expired them
    takes the same steps after 10 such as after 1,000: the claim holds the live
    ones, and changes them by what each node and each move changes."""
    from runstate.channel.memory import MemoryChannel
    from tests.test_folds import _steps

    steps = []
    for n in (10, 1_000):
        ch = MemoryChannel()
        for i in range(n):
            sub(ch, f"d{i}")  # durable one-shots
        w = worker(ch, sources=["loss"])
        w.set("loss", 1.0)
        w.commit()  # every one fires and expires here
        assert w.pinned is False
        steps.append(_steps(w.commit))
        w.rewind(None)
        assert len(w._live()) == n  # live again off the lineage that expired them
    assert steps[0] == steps[1]
