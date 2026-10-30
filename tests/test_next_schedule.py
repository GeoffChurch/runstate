"""The condition algebra over counts, staged (docs/specs/commits-and-lineage.md §3,
subscription-v0.4; plan Task 5).

A wire condition is an atom over the coordinates at a node, or an ``any``/``all``
of conditions:
  Atom      := {stream: X, n} | {commits: n} | {time_seconds: s} | {count: c}
  Condition := Atom | {any: [Condition, ...]} | {all: [Condition, ...]}
``parse_schedule`` converts it once into a typed ``Term``: ``Target``, ``Elapsed``,
``Fired``, ``AnyOf``, ``AllOf``. ``from`` and ``until`` hold once the count reaches
n; ``every`` is the delta rule against the newest firing on the lineage (a lineage
atom) or the claim's last firing (a time atom); an atom with no baseline holds.
"""

import json
import re
from pathlib import Path

import pytest

from runstate._next.vocabulary.coordinate import Commits, Target
from runstate._next.vocabulary.schedule import (
    AllOf,
    AnyOf,
    Coordinates,
    Decision,
    Elapsed,
    Fired,
    MalformedSchedule,
    Schedule,
    Subscription,
    Term,
    is_lease,
    parse_schedule,
    parse_stop_trigger,
    references_time,
    satisfied,
    unsatisfiable,
)

_PROTO = Path(__file__).resolve().parent.parent / "protocol"

# The source the subscriptions below target, and the valued sources at a node
# where its register holds a value.
SRC = "src"
VALUED = frozenset({SRC})

QUIET = Decision(fire=False, expired=False)
FIRE = Decision(fire=True, expired=False)
LAST_FIRE = Decision(fire=True, expired=True)
END = Decision(fire=False, expired=True)


def at(commits: int = 0, **streams: int) -> Coordinates:
    return Coordinates(streams=streams, commits=commits)


def term(cond) -> Term:
    """A wire condition, parsed in ``until``'s grammar, the widest."""
    t = parse_schedule({"until": cond}, pure_demand=False).until
    assert t is not None
    return t


def sched(body, *, target: str | None = SRC) -> Schedule:
    return parse_schedule(body, pure_demand=target is None)


def sub(body, *, registered_at: float = 0.0, target: str | None = SRC):
    return Subscription(
        sched(body, target=target), registered_at=registered_at, target=target
    )


def refused(body, *, sources=frozenset()) -> str | None:
    return unsatisfiable(sched(body), sources=sources)


def holds(cond, *, point=None, time_seconds=0.0, count=0) -> bool:
    return satisfied(
        term(cond), at=point or at(), time_seconds=time_seconds, count=count
    )


def drive(s: Subscription, points) -> list[Decision]:
    """Evaluate ``s`` once per node, as the Worker does on one lineage: each point
    is the node's coordinates and the clock, and the lineage baseline is the
    coordinates at the newest node where the subscription fired."""
    baseline = None
    out = []
    for point, now in points:
        d = s.evaluate(at=point, baseline=baseline, now=now, valued=VALUED)
        if d.fire:
            baseline = point
        out.append(d)
    return out


# ----- the parse --------------------------------------------------------------------


def test_a_schedule_parses_once_into_its_typed_terms():
    """The wire dict is converted at the boundary, so nothing past the parse reads
    a string key: the lineage atom is a ``Target``."""
    body = {
        "from": {"stream": "loss", "n": 2},
        "every": {"any": [{"commits": 1}, {"time_seconds": 5}]},
        "until": {"all": [{"count": 3}]},
    }
    assert parse_schedule(body, pure_demand=False) == Schedule(
        from_=Target("loss", 2),
        every=AnyOf((Target(Commits(), 1), Elapsed(5))),
        until=AllOf((Fired(3),)),
    )
    assert parse_schedule({}, pure_demand=False) == Schedule(None, None, None)
    assert parse_stop_trigger({}) is None
    assert parse_stop_trigger({"from": {"commits": 4}}) == Target(Commits(), 4)


def test_a_malformed_message_names_its_key_by_its_path():
    cases = {
        "from.any[1].time_seconds": {
            "from": {"any": [{"commits": 1}, {"time_seconds": -1}]}
        },
        "every.count": {"every": {"count": 1}},
        "until.all[0].n": {"until": {"all": [{"stream": "x", "n": -1}]}},
        "until.all[0].stream": {"until": {"all": [{"stream": "", "n": 1}]}},
        "from.step": {"from": {"step": 1}},
        "every": {"every": None},
        "extra": {"extra": {"commits": 1}},
    }
    for path, body in cases.items():
        with pytest.raises(MalformedSchedule, match=re.escape(f"`{path}`")):
            parse_schedule(body, pure_demand=False)
    with pytest.raises(MalformedSchedule, match="`until`"):
        parse_stop_trigger({"until": {"commits": 1}})


def test_the_boundary_refuses_what_only_a_python_caller_can_spell():
    """JSON spells neither, but a Python caller (``Watcher.broadcast``) can: the
    boundary refuses both with its one error, never a ``TypeError``."""
    for parse in (lambda b: parse_schedule(b, pure_demand=False), parse_stop_trigger):
        with pytest.raises(MalformedSchedule, match="`1`"):
            parse({1: {"commits": 1}, "x": 1})
        with pytest.raises(MalformedSchedule, match="must be an object"):
            parse([{"from": {"commits": 1}}])


def test_a_schedule_refuses_a_count_outside_until():
    """``Schedule`` holds the grammar's one cross-slot rule itself, so a term built
    without the parse cannot put a ``Fired`` atom in ``from`` or ``every``."""
    for kw in ({"from_": Fired(1)}, {"every": AnyOf((Target(Commits(), 1), Fired(1)))}):
        with pytest.raises(MalformedSchedule):
            Schedule(**{"from_": None, "every": None, "until": None, **kw})
    Schedule(from_=None, every=None, until=Fired(1))


def test_an_unknown_atom_is_malformed():
    with pytest.raises(MalformedSchedule):
        term({"step": 1})


def test_nan_is_no_number_here_though_json_schema_admits_it():
    """NaN compares false with every bound, so JSON Schema's ``minimum: 0`` admits
    ``{time_seconds: NaN}``; the parse refuses it, as ``payloads`` refuses a NaN
    clock."""
    pytest.importorskip("jsonschema")
    from jsonschema import Draft202012Validator

    nan = float("nan")
    defs = json.loads((_PROTO / "subscription-v0.4.schema.json").read_text())["$defs"]
    schema = {"$defs": defs, "$ref": "#/$defs/Schedule"}
    assert Draft202012Validator(schema).is_valid({"from": {"time_seconds": nan}})
    with pytest.raises(MalformedSchedule):
        parse_schedule({"from": {"time_seconds": nan}}, pure_demand=False)
    with pytest.raises(ValueError):
        Elapsed(nan)


def test_pure_demand_takes_only_an_until():
    """§3: a pure demand has a lifetime, not a schedule: a ``from``, an ``every`` or
    a ``{count: n}`` atom anywhere in it is malformed. The parse refuses it, so
    what reaches a Subscription is what the Worker did not nak as malformed."""
    for ok in ({}, {"until": {"time_seconds": 5}}, {"until": {"commits": 3}}):
        parse_schedule(ok, pure_demand=True)
    for bad in (
        {"from": {"commits": 1}},
        {"every": {"stream": "loss", "n": 1}},
        {"until": {"count": 2}},
        {"until": {"any": [{"commits": 3}, {"all": [{"count": 1}]}]}},
    ):
        with pytest.raises(MalformedSchedule):
            parse_schedule(bad, pure_demand=True)
        parse_schedule(bad, pure_demand=False)


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
        {"time_seconds": None},
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


def test_a_schedule_and_a_stop_trigger_parse_iff_subscription_v0_4_admits_them():
    """``parse_schedule`` and ``parse_stop_trigger`` succeed if and only if
    subscription-v0.4's ``Schedule`` or ``StopTrigger`` admits the body, so the
    worker needs no jsonschema; every refusal is a ``MalformedSchedule``."""
    pytest.importorskip("jsonschema")
    from jsonschema import Draft202012Validator

    defs = json.loads((_PROTO / "subscription-v0.4.schema.json").read_text())["$defs"]
    check = {
        "control.subscribe": (
            "Schedule",
            lambda b: parse_schedule(b, pure_demand=False),
        ),
        "control.stop": ("StopTrigger", parse_stop_trigger),
    }
    disagree = []
    seen = set()
    for topic, body in _schedule_bodies():
        definition, parse = check[topic]
        admits = Draft202012Validator(
            {"$defs": defs, "$ref": f"#/$defs/{definition}"}
        ).is_valid(body)
        try:
            parse(body)
            parses = True
        except MalformedSchedule:
            parses = False
        if parses != admits:
            disagree.append((topic, body, admits))
        seen.add((topic, admits))
    assert disagree == []
    assert seen == {(t, a) for t in check for a in (True, False)}


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
    s = sub({"from": {"stream": "loss", "n": 2}})
    assert drive(s, [(at(loss=1), 0.0), (at(loss=2), 0.0)]) == [QUIET, LAST_FIRE]
    # an until expires the subscription at the node where it holds, firing nothing
    s = sub({"every": {"commits": 1}, "until": {"stream": "loss", "n": 2}})
    got = drive(s, [(at(1, loss=1), 0.0), (at(2, loss=2), 0.0)])
    assert got == [FIRE, END]


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


# ----- Subscription.evaluate --------------------------------------------------------


def test_every_is_the_delta_rule_under_batching():
    """§3: ``every`` fires when ``count(now) - count(at the last firing) >= n``, not
    rounded to a multiple, with no interpolation inside a jump: a jump of 5
    commits under ``every {commits: 2}`` fires once, and the next firing measures
    from it."""
    s = sub({"every": {"commits": 2}})
    got = drive(s, [(at(1), 0.0), (at(6), 0.0), (at(7), 0.0), (at(8), 0.0)])
    assert got == [FIRE, FIRE, QUIET, FIRE]
    assert s.fires == 3
    # the baseline is what the caller passes: the newest firing on the lineage
    s = sub({"every": {"stream": "loss", "n": 3}})
    for loss, want in ((4, QUIET), (5, FIRE)):
        got1 = s.evaluate(at=at(loss=loss), baseline=at(loss=2), now=0.0, valued=VALUED)
        assert got1 == want


def test_every_delta_is_measured_from_last_fire_not_registration():
    # from commits 3 + every commits 10: the first fire is at `from`, then every 10
    # commits SINCE THAT FIRE -> 13. A `from` offset breaks the tie that a
    # register-at-0 schedule hides.
    s = sub({"from": {"commits": 3}, "every": {"commits": 10}})
    got = drive(s, [(at(c), 0.0) for c in range(20)])
    assert [c for c, d in enumerate(got) if d.fire] == [3, 13]


def test_a_time_atoms_baseline_is_the_claims_last_firing():
    """§3: a time atom's baseline is the wall clock at the last firing, kept by the
    claim (the Subscription), whatever lineage baseline the caller passes."""
    s = sub({"every": {"time_seconds": 60}}, registered_at=100.0)
    assert s.evaluate(at=at(1), baseline=None, now=100.0, valued=VALUED).fire
    assert not s.evaluate(at=at(2), baseline=None, now=159.0, valued=VALUED).fire
    assert s.evaluate(at=at(3), baseline=at(2), now=160.0, valued=VALUED).fire
    assert not s.evaluate(at=at(4), baseline=None, now=200.0, valued=VALUED).fire


def test_the_call_is_the_firing():
    """A ``Decision`` that fires has already counted the firing: two calls at one
    node are two firings, so a caller that retries a write reuses the Decision."""
    s = sub({"every": {"commits": 0}, "until": {"count": 2}})
    point = at(1)
    assert s.evaluate(at=point, baseline=None, now=0.0, valued=VALUED) == FIRE
    assert s.evaluate(at=point, baseline=point, now=0.0, valued=VALUED) == LAST_FIRE
    assert s.fires == 2


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
        s = sub({"from": atom}, registered_at=5.0)
        assert s.evaluate(at=at(), baseline=None, now=5.0, valued=VALUED) == LAST_FIRE
    for atom in [*zeros, {"count": 0}]:
        s = sub({"until": atom}, registered_at=5.0)
        assert s.evaluate(at=at(), baseline=None, now=5.0, valued=VALUED) == END
        assert s.fires == 0
    for atom in zeros:
        s = sub({"every": atom}, registered_at=5.0)
        assert drive(s, [(at(), 5.0)] * 4) == [FIRE] * 4


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
        s = sub({"every": every})
        got = s.evaluate(at=at(5, loss=1), baseline=None, now=1.0, valued=VALUED)
        assert got == FIRE
    # a lineage baseline but no time baseline: the time atom holds, the commits
    # atom is judged against its baseline
    both = {"every": {"all": [{"commits": 10}, {"time_seconds": 60}]}}
    for commits, want in ((13, QUIET), (14, FIRE)):
        got = sub(both).evaluate(at=at(commits), baseline=at(4), now=1.0, valued=VALUED)
        assert got == want


def test_an_until_that_holds_at_registration_is_not_refused():
    """Review §12, item 14 (§3, "Expiry"): one whose ``until`` already holds when
    it registers expires at the next node, firing nothing; whether it holds
    depends on the position, so it is no refusal. Each ``until`` here holds at
    the node below, and fails at the root: a 0 would hold everywhere, and is the
    empty window's (test_an_absent_from_reads_as_from_zero)."""
    point = at(5, loss=2)
    for body in (
        {"until": {"commits": 5}},
        {"until": {"stream": "loss", "n": 2}},
        {"every": {"commits": 1}, "until": {"any": [{"commits": 3}, {"count": 4}]}},
    ):
        assert refused(body) is None
        s = sub(body)
        assert s.evaluate(at=point, baseline=None, now=0.0, valued=VALUED) == END
        assert not holds(body["until"], point=at())


def test_until_is_checked_first_and_a_count_until_expires_at_its_last_firing():
    """``until`` first: a node where it holds fires nothing. The ``{count: n}``
    atom counts the claim's firings at the node, this one's included, as
    ``count(X, node)`` counts up to and including the node (§3, "Expiry"), so the
    nth firing's node expires it, as a one-shot's firing does. Holds 0.3.0's
    test_every_until_count_fires_exactly_n_times."""
    s = sub({"every": {"commits": 1}, "until": {"count": 3}})
    got = drive(s, [(at(c), 0.0) for c in range(5)])
    assert got == [FIRE, FIRE, LAST_FIRE, END, END]


def test_empty_schedule_fires_once_now():
    s = sub({})
    assert s.evaluate(at=at(), baseline=None, now=0.0, valued=VALUED) == LAST_FIRE


def test_from_commits_fires_once_at_that_count():
    s = sub({"from": {"commits": 100}})
    assert s.evaluate(at=at(50), baseline=None, now=0.0, valued=VALUED) == QUIET
    assert s.evaluate(at=at(100), baseline=None, now=0.0, valued=VALUED) == LAST_FIRE


def test_every_commits_recurs_from_registration():
    s = sub({"every": {"commits": 10}})
    got = drive(s, [(at(0), 0.0), (at(5), 0.0), (at(10), 0.0)])
    assert got == [FIRE, QUIET, FIRE]


def test_every_until_commits_stops_at_threshold():
    s = sub({"every": {"commits": 1}, "until": {"commits": 3}})
    got = drive(s, [(at(i), 0.0) for i in range(5)])
    assert got == [FIRE, FIRE, FIRE, END, END]


def test_every_whichever_first_commits_or_seconds():
    s = sub({"every": {"any": [{"commits": 10}, {"time_seconds": 60}]}})
    got = drive(s, [(at(0), 0.0), (at(5), 30.0), (at(5), 60.0), (at(15), 60.0)])
    assert got == [FIRE, QUIET, FIRE, FIRE]


def test_a_pure_demand_never_fires_and_lives_until_its_until():
    """§3: a pure demand (``target`` None) targets nothing, so it never fires and
    never consults ``valued``; it expires where its ``until`` holds, and with no
    ``until`` it never expires."""
    s = sub({"until": {"commits": 3}}, target=None)
    assert s.target is None
    got = [
        s.evaluate(at=at(c), baseline=None, now=0.0, valued=v)
        for c, v in ((1, VALUED), (2, frozenset()), (3, VALUED))
    ]
    assert got == [QUIET, QUIET, END]
    assert s.fires == 0
    forever = sub({}, target=None)
    for c in range(5):
        got1 = forever.evaluate(at=at(c), baseline=None, now=float(c), valued=VALUED)
        assert got1 == QUIET


def test_a_source_with_no_value_yet_does_not_fire_but_a_time_until_expires_it():
    """§3: a source with no value yet does not fire; the subscription waits for
    the first ``set``, and only its ``until`` is evaluated meanwhile."""
    s = sub({"every": {"commits": 1}, "until": {"time_seconds": 60}})
    for c, now, valued, want in (
        (1, 10.0, frozenset(), QUIET),
        (2, 20.0, frozenset({"other"}), QUIET),
        (3, 60.0, frozenset(), END),
    ):
        assert s.evaluate(at=at(c), baseline=None, now=now, valued=valued) == want
    assert s.fires == 0


def test_a_one_shot_whose_source_gains_its_first_value_later_fires_then():
    s = sub({"from": {"commits": 1}})
    got = [
        s.evaluate(at=at(c), baseline=None, now=0.0, valued=v)
        for c, v in ((1, frozenset()), (2, frozenset()), (3, VALUED))
    ]
    assert got == [QUIET, QUIET, LAST_FIRE]


def test_an_every_whose_source_gains_its_first_value_later_fires_at_once_then():
    """The first firing is immediate once the subscription can sample: until then
    ``fires`` and the time baseline do not move, so the time atom has no baseline
    at the first valued node."""
    s = sub({"every": {"time_seconds": 60}, "until": {"count": 2}})
    for now in (10.0, 100.0, 200.0):
        assert not s.evaluate(at=at(1), baseline=None, now=now, valued=frozenset()).fire
    assert s.fires == 0
    assert s.evaluate(at=at(2), baseline=None, now=230.0, valued=VALUED) == FIRE
    assert not s.evaluate(at=at(3), baseline=at(2), now=289.0, valued=VALUED).fire
    assert s.evaluate(at=at(4), baseline=at(2), now=290.0, valued=VALUED) == LAST_FIRE


def test_a_subscriptions_registration_is_read_only():
    """Its schedule, target, clock, class and fire count are the claim's, set at
    registration and moved only by ``evaluate``."""
    s = sub({"until": {"time_seconds": 5}}, registered_at=3.0)
    for field in ("schedule", "target", "registered_at", "lease", "fires"):
        with pytest.raises(AttributeError):
            setattr(s, field, getattr(s, field))
    assert (s.target, s.registered_at, s.lease, s.fires) == (SRC, 3.0, True, 0)
    assert s.schedule == Schedule(None, None, Elapsed(5))


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
    for body in leases:
        assert is_lease(sched(body))
        assert sub(body).lease
    for body in durables:
        assert not is_lease(sched(body))
        assert not sub(body).lease
    assert references_time(sched(leases[0])) and references_time(sched(leases[1]))
    assert not references_time(sched(leases[2]))
    assert not references_time(sched(leases[3]))
    assert not any(references_time(sched(b)) for b in durables)


def test_a_logged_body_that_does_not_parse_is_no_lease():
    """A fold classifies the subscriptions on a log before any worker has refused
    a malformed one. It parses each body first, and one that does not parse is
    malformed, never a lease: the worker's ``nak`` spends it. The parse raises
    only ``MalformedSchedule``, which the fold catches."""
    for junk in ({"from": None}, {"every": [1]}, {"until": {"any": "x"}}, {"x": 1}):
        with pytest.raises(MalformedSchedule):
            parse_schedule(junk, pure_demand=False)


# ----- the refusals -----------------------------------------------------------------


def test_the_two_atom_refusals():
    """§3, "Refusals": a progress atom naming a declared source, anywhere in the
    schedule, since its count depends on who asked; and an empty window, a
    ``from`` that implies the ``until``. Holds 0.3.0's
    test_from_after_until_empty_window_is_unsatisfiable and
    test_from_before_until_is_satisfiable."""
    lr = frozenset({"lr"})
    for body in (
        {"from": {"stream": "lr", "n": 1}},
        {"every": {"any": [{"commits": 2}, {"stream": "lr", "n": 1}]}},
        {"until": {"all": [{"time_seconds": 9}, {"stream": "lr", "n": 0}]}},
    ):
        assert refused(body, sources=lr) is not None
        assert refused(body, sources=frozenset({"grad"})) is None
    for empty in (
        {"from": {"commits": 5}, "until": {"commits": 5}},
        {"from": {"commits": 5}, "until": {"commits": 3}},
        # the gate opens (commits >= 100) only after it has closed (>= 50)
        {"from": {"commits": 100}, "until": {"commits": 50}},
        {"from": {"stream": "loss", "n": 5}, "until": {"stream": "loss", "n": 3}},
        {"from": {"time_seconds": 100}, "until": {"time_seconds": 50}},
    ):
        assert refused(empty) is not None
    for open_ in (
        {"from": {"commits": 5}, "until": {"commits": 6}},
        {"from": {"commits": 50}, "until": {"commits": 100}},
    ):
        assert refused(open_) is None


def test_an_absent_from_reads_as_from_zero():
    """§3, "Refusals" (amended 2026-10-30): a 0 in ``from`` re-spells an absent
    ``from``, so the empty-window check reads an absent one as ``from`` 0 on each
    X, and the two spellings of one schedule get one answer."""
    for until in (
        {"commits": 0},
        {"time_seconds": 0},
        {"stream": "loss", "n": 0},
        {"any": [{"commits": 0}, {"commits": 5}]},
    ):
        assert refused({"until": until}) is not None
        for x in ({"commits": 0}, {"time_seconds": 0}, {"stream": "loss", "n": 0}):
            assert refused({"from": x, "until": until}) is not None
    for until in ({"commits": 1}, {"time_seconds": 0.5}, {"stream": "loss", "n": 1}):
        for body in ({"until": until}, {"from": {"commits": 0}, "until": until}):
            assert refused(body) is None


def test_future_threshold_is_satisfiable():
    # a not-yet-reached `from` just waits; it is no refusal
    assert refused({"from": {"commits": 100}}) is None


def test_until_count_budget_with_commits_from_is_not_empty_window():
    # until = (commits >= 50 AND fired >= 5): at from's corner count = 0, so open
    body = {
        "from": {"commits": 100},
        "until": {"all": [{"commits": 50}, {"count": 5}]},
    }
    assert refused(body) is None


def test_from_after_until_across_dimensions_is_speed_contingent_not_empty():
    # from on one X, until on another: whether it fires depends on the run, so the
    # window is not empty (§3: "on one X")
    for body in (
        {"from": {"commits": 100}, "until": {"time_seconds": 60}},
        {"from": {"commits": 100}, "until": {"stream": "loss", "n": 3}},
        {"from": {"stream": "acc", "n": 9}, "until": {"stream": "loss", "n": 3}},
    ):
        assert refused(body) is None


def test_any_from_punts_rather_than_normalizing():
    # a from with an `any` has many corners; it is not flagged (degrades to a
    # dynamic never-fire) -- this one reduces to commits >= 5, so [5, 50) is open
    body = {
        "from": {"any": [{"commits": 100}, {"commits": 5}]},
        "until": {"commits": 50},
    }
    assert refused(body) is None


def test_empty_window_with_a_conjunctive_from():
    # the window opens at (commits >= 100 AND time >= 60); until commits 50 has
    # closed it by then
    empty = {
        "from": {"all": [{"commits": 100}, {"time_seconds": 60}]},
        "until": {"commits": 50},
    }
    assert refused(empty) is not None
    open_ = {
        "from": {"all": [{"commits": 100}, {"time_seconds": 60}]},
        "until": {"commits": 200},
    }
    assert refused(open_) is None
