"""The condition algebra over counts, staged (docs/specs/commits-and-lineage.md §3,
subscription-v0.4; plan Task 5).

A Condition is an atom over the coordinates at a node, or an ``any``/``all`` of
Conditions:
  Atom      := {stream: X, n} | {commits: n} | {time_seconds: s} | {count: c}
  Condition := Atom | {any: [Condition, ...]} | {all: [Condition, ...]}
``from`` and ``until`` hold once the count reaches n; ``every`` is the delta rule
against the newest firing on the lineage (a lineage atom) or the claim's last
firing (a time atom); an atom with no baseline holds.
"""

import json
from pathlib import Path

import pytest

from runstate._next.vocabulary.coordinate import Commits
from runstate._next.vocabulary.schedule import (
    Coordinates,
    Decision,
    Subscription,
    is_lease,
    malformed_schedule,
    malformed_stop_trigger,
    references_time,
    satisfied,
    unsatisfiable,
)

_PROTO = Path(__file__).resolve().parent.parent / "protocol"


def at(commits: int = 0, **streams: int) -> Coordinates:
    return Coordinates(streams=streams, commits=commits)


def holds(cond, *, point=None, time_seconds=0.0, count=0) -> bool:
    return satisfied(cond, at=point or at(), time_seconds=time_seconds, count=count)


def drive(sub: Subscription, points) -> list[Decision]:
    """Evaluate ``sub`` once per node, as the Worker does on one lineage: each
    point is the node's coordinates and the clock, and the lineage baseline is the
    coordinates at the newest node where the subscription fired."""
    baseline = None
    out = []
    for point, now in points:
        d = sub.evaluate(at=point, baseline=baseline, now=now)
        if d.fire:
            baseline = point
        out.append(d)
    return out


# ----- satisfied: from and until as counts ------------------------------------------


def test_from_and_until_hold_as_counts():
    """§3: ``from`` and ``until`` hold once ``count(X, node) >= n``; an absent stream
    counts 0 (§2: empty is 0, with no sentinel)."""
    assert holds({"stream": "loss", "n": 3}, point=at(loss=3))
    assert holds({"stream": "loss", "n": 3}, point=at(loss=7))
    assert not holds({"stream": "loss", "n": 3}, point=at(loss=2))
    assert not holds({"stream": "loss", "n": 1}, point=at(5, acc=4))
    assert holds({"stream": "loss", "n": 0}, point=at())
    # a one-shot from fires at the node where the count reaches n, and expires there
    sub = Subscription({"from": {"stream": "loss", "n": 2}}, registered_at=0.0)
    assert drive(sub, [(at(loss=1), 0.0), (at(loss=2), 0.0)]) == [
        (False, False),
        (True, True),
    ]
    # an until expires the subscription at the node where it holds, firing nothing
    sub = Subscription(
        {"every": {"commits": 1}, "until": {"stream": "loss", "n": 2}},
        registered_at=0.0,
    )
    got = drive(sub, [(at(1, loss=1), 0.0), (at(2, loss=2), 0.0)])
    assert got == [(True, False), (False, True)]


def test_the_commits_atom_reads_the_lineage_clock():
    """``{commits: n}`` reads ``count(Commits(), node)``; a stream that happens to
    be named "commits" is a stream like any other (§2)."""
    assert holds({"commits": 2}, point=at(2))
    assert not holds({"commits": 2}, point=at(1, loss=9))
    assert not holds({"stream": "commits", "n": 1}, point=at(5))
    point = Coordinates(streams={"commits": 1}, commits=0)
    assert holds({"stream": "commits", "n": 1}, point=point)
    assert not holds({"commits": 1}, point=point)
    point = at(4, loss=2)
    assert point.of(Commits()) == 4
    assert point.of("loss") == 2
    assert point.of("absent") == 0


def test_stream_and_commits_thresholds():
    """Ported from 0.3.0's ``test_step_threshold``: the step atom is gone (§3,
    "Removed"); its two successors are ``>=`` thresholds over counts."""
    for cond, x in (({"commits": 100}, "commits"), ({"stream": "s", "n": 100}, "s")):

        def point(n: int) -> Coordinates:
            return at(n) if x == "commits" else at(**{x: n})

        assert holds(cond, point=point(100))
        assert holds(cond, point=point(150))
        assert not holds(cond, point=point(99))


def test_time_and_count_thresholds():
    assert holds({"time_seconds": 60}, time_seconds=60)
    assert not holds({"time_seconds": 60}, time_seconds=59)
    assert holds({"count": 3}, count=3)
    assert not holds({"count": 3}, count=2)


def test_any_and_all_combine_counts():
    cond = {"any": [{"stream": "loss", "n": 3}, {"commits": 5}]}
    assert holds(cond, point=at(0, loss=3))
    assert holds(cond, point=at(5))
    assert not holds(cond, point=at(4, loss=2))
    cond = {"all": [{"stream": "loss", "n": 3}, {"commits": 5}]}
    assert holds(cond, point=at(5, loss=3))
    assert not holds(cond, point=at(4, loss=3))
    assert not holds(cond, point=at(5, loss=2))


def test_any_is_whichever_crosses_first():
    cond = {"any": [{"commits": 10}, {"time_seconds": 60}]}
    assert holds(cond, point=at(10))
    assert holds(cond, time_seconds=60)
    assert not holds(cond, point=at(9), time_seconds=59)


def test_all_is_whichever_crosses_last():
    cond = {"all": [{"commits": 10}, {"time_seconds": 60}]}
    assert holds(cond, point=at(10), time_seconds=60)
    assert not holds(cond, point=at(10), time_seconds=59)
    assert not holds(cond, point=at(9), time_seconds=60)


def test_nested_any_all():
    cond = {"all": [{"commits": 100}, {"any": [{"time_seconds": 60}, {"count": 5}]}]}
    assert holds(cond, point=at(100), time_seconds=60)
    assert holds(cond, point=at(100), count=5)
    assert not holds(cond, point=at(100))
    assert not holds(cond, point=at(99), time_seconds=60, count=5)


def test_an_unknown_atom_raises():
    with pytest.raises(ValueError):
        holds({"step": 1})


# ----- Subscription.evaluate --------------------------------------------------------


def test_every_is_the_delta_rule_under_batching():
    """§3: ``every`` fires when ``count(now) - count(at the last firing) >= n``, not
    rounded to a multiple, with no interpolation inside a jump: a jump of 5
    commits under ``every {commits: 2}`` fires once, and the next firing measures
    from it."""
    sub = Subscription({"every": {"commits": 2}}, registered_at=0.0)
    got = drive(sub, [(at(1), 0.0), (at(6), 0.0), (at(7), 0.0), (at(8), 0.0)])
    assert [d.fire for d in got] == [True, True, False, True]
    assert not any(d.expired for d in got)
    assert sub.fires == 3
    # the baseline is what the caller passes: the newest firing on the lineage
    sub = Subscription({"every": {"stream": "loss", "n": 3}}, registered_at=0.0)
    assert sub.evaluate(at=at(loss=4), baseline=at(loss=2), now=0.0) == (False, False)
    assert sub.evaluate(at=at(loss=5), baseline=at(loss=2), now=0.0) == (True, False)


def test_every_delta_is_measured_from_last_fire_not_registration():
    # from commits 3 + every commits 10: the first fire is at `from`, then every 10
    # commits SINCE THAT FIRE -> 13. A `from` offset breaks the tie that a
    # register-at-0 schedule hides.
    s = Subscription(
        {"from": {"commits": 3}, "every": {"commits": 10}}, registered_at=0.0
    )
    got = drive(s, [(at(c), 0.0) for c in range(20)])
    assert [c for c, d in enumerate(got) if d.fire] == [3, 13]


def test_a_time_atoms_baseline_is_the_claims_last_firing():
    """§3: a time atom's baseline is the wall clock at the last firing, kept by the
    claim (the Subscription), whatever lineage baseline the caller passes."""
    sub = Subscription({"every": {"time_seconds": 60}}, registered_at=100.0)
    assert sub.evaluate(at=at(1), baseline=None, now=100.0).fire
    assert not sub.evaluate(at=at(2), baseline=None, now=159.0).fire
    assert sub.evaluate(at=at(3), baseline=at(2), now=160.0).fire
    assert not sub.evaluate(at=at(4), baseline=None, now=200.0).fire


def test_a_zero_in_each_atom_holds_at_once():
    """§3: every threshold takes n >= 0, and a 0 holds at once. In ``from`` it
    re-spells an absent ``from``; in ``until`` an ``until`` that holds; in
    ``every`` it fires at every evaluation."""
    zeros = [
        {"stream": "loss", "n": 0},
        {"commits": 0},
        {"time_seconds": 0},
    ]
    for atom in zeros:
        sub = Subscription({"from": atom}, registered_at=5.0)
        assert sub.evaluate(at=at(), baseline=None, now=5.0) == (True, True)
    for atom in [*zeros, {"count": 0}]:
        sub = Subscription({"until": atom}, registered_at=5.0)
        assert sub.evaluate(at=at(), baseline=None, now=5.0) == (False, True)
        assert sub.fires == 0
    for atom in zeros:
        sub = Subscription({"every": atom}, registered_at=5.0)
        got = drive(sub, [(at(), 5.0)] * 4)
        assert got == [(True, False)] * 4


def test_an_atom_with_no_baseline_holds_so_the_first_firing_is_immediate():
    """§3: an atom with no baseline holds. The lineage atoms have none until the
    subscription fires on the lineage; the time atom none until it fires in the
    claim (a re-send registers afresh, dropping it). Each atom is judged on its
    own baseline."""
    for every in (
        {"commits": 100},
        {"stream": "loss", "n": 50},
        {"time_seconds": 3600},
    ):
        sub = Subscription({"every": every}, registered_at=0.0)
        assert sub.evaluate(at=at(5, loss=1), baseline=None, now=1.0) == (True, False)
    # a lineage baseline but no time baseline: the time atom holds, the commits
    # atom is judged against its baseline
    both = {"every": {"all": [{"commits": 10}, {"time_seconds": 60}]}}
    assert (
        not Subscription(both, registered_at=0.0)
        .evaluate(at=at(13), baseline=at(4), now=1.0)
        .fire
    )
    assert (
        Subscription(both, registered_at=0.0)
        .evaluate(at=at(14), baseline=at(4), now=1.0)
        .fire
    )


def test_an_until_that_holds_at_registration_is_not_refused():
    """Review §12, item 14 (§3, "Expiry"): one whose ``until`` already holds when
    it registers expires at the next node, firing nothing; whether it holds
    depends on the position, so it is no refusal."""
    for schedule in (
        {"until": {"commits": 3}},
        {"until": {"stream": "loss", "n": 0}},
        {"until": {"count": 0}},
        {"every": {"commits": 1}, "until": {"time_seconds": 0}},
    ):
        assert malformed_schedule(schedule, pure_demand=False) is None
        assert unsatisfiable(schedule, sources=frozenset()) is None
        sub = Subscription(schedule, registered_at=0.0)
        assert sub.evaluate(at=at(5), baseline=None, now=0.0) == (False, True)


def test_until_is_checked_first_and_a_count_until_expires_at_its_last_firing():
    """``until`` first: a node where it holds fires nothing. The ``{count: n}``
    atom counts the claim's firings at the node, this one's included, as
    ``count(X, node)`` counts up to and including the node (§2), so the nth
    firing's node expires it, as a one-shot's firing does."""
    sub = Subscription(
        {"every": {"commits": 1}, "until": {"count": 3}}, registered_at=0.0
    )
    got = drive(sub, [(at(c), 0.0) for c in range(1, 6)])
    assert [d.fire for d in got] == [True, True, True, False, False]
    assert [d.expired for d in got] == [False, False, True, True, True]


def test_empty_schedule_fires_once_now():
    sub = Subscription({}, registered_at=0.0)
    assert sub.evaluate(at=at(), baseline=None, now=0.0) == (True, True)


def test_from_commits_fires_once_at_that_count():
    sub = Subscription({"from": {"commits": 100}}, registered_at=0.0)
    assert sub.evaluate(at=at(50), baseline=None, now=0.0) == (False, False)
    assert sub.evaluate(at=at(100), baseline=None, now=0.0) == (True, True)


def test_every_commits_recurs_from_registration():
    sub = Subscription({"every": {"commits": 10}}, registered_at=0.0)
    got = drive(sub, [(at(0), 0.0), (at(5), 0.0), (at(10), 0.0)])
    assert [d.fire for d in got] == [True, False, True]
    assert not got[2].expired


def test_every_until_count_fires_exactly_n_times():
    sub = Subscription(
        {"every": {"commits": 1}, "until": {"count": 3}}, registered_at=0.0
    )
    results = drive(sub, [(at(i), 0.0) for i in range(5)])
    assert [d.fire for d in results] == [True, True, True, False, False]
    assert results[2].expired


def test_every_until_commits_stops_at_threshold():
    sub = Subscription(
        {"every": {"commits": 1}, "until": {"commits": 3}}, registered_at=0.0
    )
    results = drive(sub, [(at(i), 0.0) for i in range(5)])
    assert [d.fire for d in results] == [True, True, True, False, False]
    assert results[3].expired


def test_every_whichever_first_commits_or_seconds():
    sub = Subscription(
        {"every": {"any": [{"commits": 10}, {"time_seconds": 60}]}},
        registered_at=0.0,
    )
    got = drive(sub, [(at(0), 0.0), (at(5), 30.0), (at(5), 60.0), (at(15), 60.0)])
    assert [d.fire for d in got] == [True, False, True, True]


def test_a_subscription_refuses_a_malformed_schedule():
    """A Subscription is built from a schedule ``malformed_schedule`` passed; one
    it did not pass would mis-evaluate, so construction refuses it."""
    for bad in ({"every": {"count": 1}}, {"from": {"step": 1}}, {"until": None}):
        with pytest.raises(ValueError):
            Subscription(bad, registered_at=0.0)


# ----- lease or durable -------------------------------------------------------------


def test_a_lease_is_a_schedule_with_a_time_or_count_atom():
    """§3: a schedule with any ``time_seconds`` or ``count`` atom is a lease; one
    whose atoms are all ``{stream, n}`` or ``{commits: n}`` is durable. Only a
    time atom references time."""
    leases = [
        {"from": {"time_seconds": 5}},
        {"every": {"any": [{"commits": 10}, {"time_seconds": 60}]}},
        {"until": {"all": [{"commits": 3}, {"count": 2}]}},
        {"until": {"count": 1}},
    ]
    durables = [
        {},
        {"from": {"commits": 1}, "every": {"stream": "loss", "n": 2}},
        {"until": {"any": [{"commits": 9}, {"stream": "x", "n": 0}]}},
    ]
    for s in leases:
        assert is_lease(s)
        assert Subscription(s, registered_at=0.0).lease
    for s in durables:
        assert not is_lease(s)
        assert not Subscription(s, registered_at=0.0).lease
    assert references_time(leases[0]) and references_time(leases[1])
    assert not references_time(leases[2]) and not references_time(leases[3])
    assert not any(references_time(s) for s in durables)


def test_the_lease_test_is_total_over_a_logged_body():
    """A reader classifies the subscriptions on a log before any worker has
    refused a malformed one, so the class of a malformed body is an answer, not a
    raise: it references nothing it cannot parse."""
    for junk in ({"from": None}, {"every": [1]}, {"until": {"any": "x"}}, {"x": 1}):
        assert is_lease(junk) is False
        assert references_time(junk) is False


# ----- the grammar ------------------------------------------------------------------


def test_pure_demand_takes_only_an_until():
    """§3: a pure demand has a lifetime, not a schedule: a ``from``, an ``every`` or
    a ``{count: n}`` atom anywhere in it is malformed."""
    for ok in ({}, {"until": {"time_seconds": 5}}, {"until": {"commits": 3}}):
        assert malformed_schedule(ok, pure_demand=True) is None
    for bad in (
        {"from": {"commits": 1}},
        {"every": {"stream": "loss", "n": 1}},
        {"until": {"count": 2}},
        {"until": {"any": [{"commits": 3}, {"all": [{"count": 1}]}]}},
    ):
        assert malformed_schedule(bad, pure_demand=True) is not None
        assert malformed_schedule(bad, pure_demand=False) is None


def _schedule_bodies():
    """Schedules and stop triggers, from test_schema_v0_4_0 and at the edges of
    each atom's grammar."""
    from test_schema_v0_4_0 import NEGATIVE, VALID

    for r in [*VALID.values(), *NEGATIVE.values()]:
        if r["topic"] in ("control.subscribe", "control.stop"):
            yield r["topic"], r["body"]
    conds = [
        {"stream": "x", "n": 0},
        {"stream": "x", "n": 2**53 - 1},
        {"stream": "x", "n": 2**53},
        {"stream": "x", "n": -1},
        {"stream": "x", "n": True},
        {"stream": "", "n": 1},
        {"stream": 3, "n": 1},
        {"stream": "x"},
        {"n": 1},
        {"stream": "x", "n": 1, "extra": 1},
        {"stream": "x", "n": 1, "commits": 1},
        {"commits": 0},
        {"commits": 2**53},
        {"commits": "1"},
        {"commits": None},
        {"commits": 1, "time_seconds": 1},
        {"time_seconds": 0.5},
        {"time_seconds": -0.5},
        {"time_seconds": True},
        {"count": 0},
        {"count": 2**60},
        {"count": -1},
        {"count": 1.5},
        {"step": 1},
        {},
        {"any": []},
        {"all": [{"commits": 1}, {"count": 1}]},
        {"any": [{"commits": 1}, {"stream": "y", "n": 2}]},
        {"any": {"commits": 1}},
        {"all": [None]},
        None,
        [],
        "commits",
    ]
    for cond in conds:
        for slot in ("from", "every", "until"):
            yield "control.subscribe", {slot: cond}
        yield "control.stop", {"from": cond}
    yield "control.subscribe", {"from": {"commits": 1}, "extra": {"commits": 1}}
    yield "control.stop", {"until": {"commits": 1}}
    yield "control.stop", {"every": {"commits": 1}}


def test_malformed_schedule_and_stop_trigger_are_the_subscription_v0_4_grammar():
    """Each is None if and only if subscription-v0.4's ``Schedule`` or
    ``StopTrigger`` admits the body, so the worker needs no jsonschema."""
    pytest.importorskip("jsonschema")
    from jsonschema import Draft202012Validator

    defs = json.loads((_PROTO / "subscription-v0.4.schema.json").read_text())["$defs"]
    check = {
        "control.subscribe": (
            "Schedule",
            lambda b: malformed_schedule(b, pure_demand=False),
        ),
        "control.stop": ("StopTrigger", malformed_stop_trigger),
    }
    disagree = []
    seen = set()
    for topic, body in _schedule_bodies():
        definition, malformed = check[topic]
        admits = Draft202012Validator(
            {"$defs": defs, "$ref": f"#/$defs/{definition}"}
        ).is_valid(body)
        if (malformed(body) is None) != admits:
            disagree.append((topic, body, admits))
        seen.add((topic, admits))
    assert disagree == []
    assert seen == {(t, a) for t in check for a in (True, False)}


# ----- the refusals -----------------------------------------------------------------


def test_the_two_atom_refusals():
    """§3, "Refusals": a progress atom naming a declared source, anywhere in the
    schedule, since its count depends on who asked; and an empty window, a
    ``from`` that implies the ``until``."""
    lr = frozenset({"lr"})
    for schedule in (
        {"from": {"stream": "lr", "n": 1}},
        {"every": {"any": [{"commits": 2}, {"stream": "lr", "n": 1}]}},
        {"until": {"all": [{"time_seconds": 9}, {"stream": "lr", "n": 0}]}},
    ):
        assert unsatisfiable(schedule, sources=lr) is not None
        assert unsatisfiable(schedule, sources=frozenset({"grad"})) is None
    for empty in (
        {"from": {"commits": 5}, "until": {"commits": 5}},
        {"from": {"commits": 5}, "until": {"commits": 3}},
        {"from": {"stream": "loss", "n": 5}, "until": {"stream": "loss", "n": 3}},
        {"from": {"time_seconds": 100}, "until": {"time_seconds": 50}},
    ):
        assert unsatisfiable(empty, sources=frozenset()) is not None
    assert (
        unsatisfiable(
            {"from": {"commits": 5}, "until": {"commits": 6}}, sources=frozenset()
        )
        is None
    )


def test_future_threshold_is_satisfiable():
    # a not-yet-reached `from` just waits; it is no refusal
    assert unsatisfiable({"from": {"commits": 100}}, sources=frozenset()) is None


def test_from_after_until_empty_window_is_unsatisfiable():
    # the gate opens (commits >= 100) only after it has already closed (>= 50)
    schedule = {"from": {"commits": 100}, "until": {"commits": 50}}
    assert unsatisfiable(schedule, sources=frozenset()) is not None


def test_from_before_until_is_satisfiable():
    schedule = {"from": {"commits": 50}, "until": {"commits": 100}}
    assert unsatisfiable(schedule, sources=frozenset()) is None


def test_until_count_budget_with_commits_from_is_not_empty_window():
    # until = (commits >= 50 AND fired >= 5): at from's corner count = 0, so open
    sched = {
        "from": {"commits": 100},
        "until": {"all": [{"commits": 50}, {"count": 5}]},
    }
    assert unsatisfiable(sched, sources=frozenset()) is None


def test_from_after_until_across_dimensions_is_speed_contingent_not_empty():
    # from on one X, until on another: whether it fires depends on the run, so the
    # window is not empty (§3: "on one X")
    for sched in (
        {"from": {"commits": 100}, "until": {"time_seconds": 60}},
        {"from": {"commits": 100}, "until": {"stream": "loss", "n": 3}},
        {"from": {"stream": "acc", "n": 9}, "until": {"stream": "loss", "n": 3}},
    ):
        assert unsatisfiable(sched, sources=frozenset()) is None


def test_any_from_punts_rather_than_normalizing():
    # a from with an `any` has many corners; it is not flagged (degrades to a
    # dynamic never-fire) -- this one reduces to commits >= 5, so [5, 50) is open
    sched = {
        "from": {"any": [{"commits": 100}, {"commits": 5}]},
        "until": {"commits": 50},
    }
    assert unsatisfiable(sched, sources=frozenset()) is None


def test_empty_window_with_a_conjunctive_from():
    # the window opens at (commits >= 100 AND time >= 60); until commits 50 has
    # closed it by then
    empty = {
        "from": {"all": [{"commits": 100}, {"time_seconds": 60}]},
        "until": {"commits": 50},
    }
    assert unsatisfiable(empty, sources=frozenset()) is not None
    open_ = {
        "from": {"all": [{"commits": 100}, {"time_seconds": 60}]},
        "until": {"commits": 200},
    }
    assert unsatisfiable(open_, sources=frozenset()) is None
