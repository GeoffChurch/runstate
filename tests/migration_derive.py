"""The writer facts a simulator history satisfies, derived from its events (Task 19's
``derive.py``, taken over by the oracle harness, ``tests/test_migration_oracle.py``).

``observe(history)`` lists, per claim and per fact, the fact's elements in that claim:
for each, the classes its behaviour is consistent with (one, except where classes
coincide on it) and the class the history declares. Each third party's ``stopped`` gets
the same for the third-party facts, each record its own writer, named by its seq. It
also gives the oracle classifier's answers (each stop's author and act, each value's
role), read off the history.

It reads only the history's fields and its walk, which is the claim that each fact is a
predicate over the history. It never imports the step: with the simulator, it is the
harness's half.

- ``holds(statement, elements)``: a statement is true when every element has a class in
  it, the engine's check.
- ``declared(elements)``: the statement the history's own fields make, the canonical
  true one the harness uses.
- ``configuration(...)``: an oracle configuration from observations, one version per
  claim and per third party.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Hashable, Sequence
from dataclasses import fields, replace
from enum import Enum
from typing import Any, NamedTuple, assert_never

import runstate.migrations.v0_3_0_to_v0_4_0_facts as V
from runstate.migrations import Row
from tests.migration_sim import history as H
from tests.migration_sim.render import progress, render


class Element(NamedTuple):
    classes: frozenset[Any]  # the classes its behaviour is consistent with
    declared: Any  # the class the history's fields name (one of ``classes``)


Facts = dict[str, list[Element]]  # fact name -> its elements


def one(c: Any) -> Element:
    return Element(frozenset({c}), c)


class Observation(NamedTuple):
    claims: dict[int, Facts]  # claim seq -> worker facts' elements
    third: dict[str, Facts]  # "tp@<seq>" -> that third party's facts' elements
    # seq of a stopped -> OwnStop(), or ThirdStop("tp@<seq>", act)
    stops: dict[int, V.StopAnswer]
    roles: dict[int, V.ValueRole]  # seq of a value -> its role
    starts: tuple[int, ...]  # each claim's started seq, by claim index


_WORKER = (
    H.Begin,
    H.Emit,
    H.Beat,
    H.Beacon,
    H.PassSave,
    H.Finish,
    H.Save,
    H.Rewind,
    H.Exit,
    H.Halt,
)
_PHASE = {
    "loading": V.Phase.LOADING,
    "iterating": V.Phase.PASS,
    "between": V.Phase.BETWEEN,
    "exited": V.Phase.TEARDOWN,
}
_ROLE = {
    "loading": V.Role.LOADING,
    "iterating": V.Role.PASS,
    "between": V.Role.BETWEEN,
    "exited": V.Role.TEARDOWN,
}
_SAVE_AT = {
    "loading": V.SaveAt.BEFORE_LOOP,
    "iterating": V.SaveAt.PARTIAL,
    "between": V.SaveAt.BETWEEN,
    "exited": V.SaveAt.AFTER_EXIT,
}


def _loaded_step(
    events: tuple[H.Event, ...], i: int, origin: H.Origin | H.SaveRef | None
) -> int | None:
    """The step of the state a load or rewind at event ``i`` takes: None for a fresh
    start."""
    if origin is None or isinstance(origin, H.Fresh):
        return None
    if isinstance(origin, H.FromOutside):
        return origin.step
    ref = origin.save if isinstance(origin, H.FromSave) else origin
    return H.loadable(events[:i])[ref]


def _floor(step: int, resume: H.Resume) -> int:
    """The lowest label the next pass may take after a load or a rewind of a saved
    state at ``step`` (``resume``): the step after it, or the step itself."""
    return step + 1 if resume is H.Resume.AFTER else step


def _by_value(declared: Enum, values: dict[Any, Any]) -> Element:
    """An encoding's element: every convention that writes the value the declared one
    writes."""
    return Element(
        frozenset(k for k, v in values.items() if v == values[declared]), declared
    )


def _clock(claim: H.Claim, loaded: int | None) -> Element:
    """``loading``'s element: a claim whose pre-pass clock is read. Both classes read
    alike over a state at 0."""
    decl = V.Loading(claim.clock.value)
    return Element(frozenset(V.Loading) if not loaded else frozenset({decl}), decl)


def _renders_alike(h: H.History, i: int, **change: Any) -> bool:
    """Whether event ``i`` with ``change`` renders the same log: the changed choice is
    not exercised there."""
    other = H.History(
        h.events[:i] + (replace(h.events[i], **change),) + h.events[i + 1 :], h.born
    )
    return render(other).rows == render(h).rows


def observe(h: H.History) -> Observation:
    rendered = render(h)
    walk = rendered.walk
    events = h.events
    seqs = rendered.seqs
    obs: dict[int, Facts] = defaultdict(lambda: defaultdict(list))
    third: dict[str, Facts] = {}
    stops_by: dict[int, V.StopAnswer] = {}
    roles: dict[int, V.ValueRole] = {}
    born_0_3_0 = h.born is H.Born.V0_3_0
    starts: list[int] = []

    phase: dict[int, str] = {}
    floor: dict[int, int] = {}
    loaded: dict[int, int | None] = {}
    claim_of: dict[int, H.Claim] = {}
    acts_in_pass: dict[int, int] = {}
    valued: dict[int, bool] = {}  # a value with a step in the open pass
    ticked_in: dict[int, bool] = {}  # a tick in the open pass, before its close
    beaten: dict[int, bool] = {}
    saved: dict[int, bool] = {}
    pass_save: dict[int, H.PassSave | None] = {}
    begun: dict[int, int | None] = {}
    finished: dict[int, int | None] = {}
    ticked: dict[int, int | None] = {}
    last_act: dict[int, tuple[int, H.Event] | None] = {}
    exited: set[int] = set()  # its worker's own stopped is written
    exit_node: set[int] = set()  # ... and it ended the claim first: an exit node
    released: set[int] = set()  # a release or a takeover ended it
    claim_seq: dict[int, int] = {}
    stops: list[tuple[int, str]] = []  # (seq, id)
    named: set[str] = set()
    control = 0
    latest = -1

    def cut(c: int) -> None:
        """The open pass of claim c ends other than by its close: cut-save's element, if
        it saved inside."""
        ps = pass_save[c]
        if phase[c] != "iterating" or ps is None or c in released:
            return
        decl = V.CutSave(claim_of[c].cut_save.value)
        finishes = {V.CutSave.SAVE_START} | ({V.CutSave.LANDED} if ps.landed else set())
        same = finishes if decl in finishes else set(V.CutSave) - finishes
        obs[claim_seq[c]]["cut_save"].append(Element(frozenset(same), decl))

    for i, ev in enumerate(events):
        seq = seqs[i]
        if isinstance(ev, H.Claim):
            c = ev.claim
            assert seq is not None
            if c > 0 and latest == c - 1:
                prev = c - 1
                if ev.gate is H.Gate.TAKEOVER:
                    if phase[prev] != "halted":
                        cut(prev)
                        released.add(prev)
                    obs[claim_seq[prev]]["takeover"].append(
                        one(
                            V.Takeover.DEAD
                            if phase[prev] == "halted"
                            else V.Takeover.LIVE
                        )
                    )
            latest = c
            claim_of[c] = ev
            claim_seq[c] = seq
            starts.append(seq)
            phase[c] = "loading"
            loaded[c] = _loaded_step(events, i, ev.origin)
            ls = loaded[c]
            floor[c] = 0 if ls is None else _floor(ls, ev.resume)
            acts_in_pass[c], valued[c], ticked_in[c], beaten[c], saved[c] = (
                0,
                False,
                False,
                False,
                False,
            )
            pass_save[c], begun[c], finished[c], ticked[c], last_act[c] = (
                None,
                None,
                None,
                None,
                None,
            )
            load = next(x for x in walk.loads if x.claim == c)
            o = obs[seq]
            if isinstance(ev.origin, H.Fresh):
                o["load"].append(
                    one(V.Load.FRESH if load.newest is None else V.Load.RESTART)
                )
            elif isinstance(ev.origin, H.FromOutside):
                o["load"].append(one(V.Load.OUTSIDE))
            else:
                o["load"].append(
                    one(V.Load.NEWEST if load.loaded == load.newest else V.Load.OLDER)
                )
            if ls is not None:  # a saved state loaded: where its first pass resumes
                o["resume"].append(one(V.Resume(ev.resume.value)))
            continue
        if isinstance(ev, H.StopRequest):
            assert seq is not None
            stops.append((seq, ev.request_id))
            control = seq
            continue
        if isinstance(ev, (H.Stray, H.ThirdPartyValue)):
            # a value no claim's worker wrote: before a claim's started, or mid-claim (a
            # third party's register)
            assert seq is not None
            roles[seq] = V.ValueRole(V.Role.STRAY, True)
            if latest >= 0:
                obs[claim_seq[latest]]["strays"].append(
                    one(V.Stray.STEPLESS if ev.step is None else V.Stray.STEPPED)
                )
            continue
        if isinstance(ev, H.ThirdPartyStop):
            assert seq is not None
            n = ev.names
            if n is None:
                state = V.ClaimState.UNNAMED
            elif n in exit_node:
                state = V.ClaimState.EXITED
            elif n in exited or n in released or n < latest:
                state = V.ClaimState.ENDED
            elif phase[n] == "halted":
                state = V.ClaimState.DEAD
            else:
                state = V.ClaimState.LIVE
            t: Facts = defaultdict(list)
            intent = ev.intent
            record_act: V.RecordAct
            if isinstance(intent, H.Release):
                act = V.Act.RELEASE
                if (
                    state is V.ClaimState.EXITED
                ):  # via's element: a release of an exited claim
                    t["via"].append(
                        one(
                            V.Via.IF_HELD
                            if intent.via is H.Via.HELPER
                            else V.Via.ALWAYS
                        )
                    )
                t["outcome"].append(one(V.Outcome(intent.outcome.value)))
                record_act = V.Release(V.Outcome(intent.outcome.value))
                if n is not None and state is V.ClaimState.LIVE:
                    cut(n)
                if n is not None:
                    released.add(n)
            elif isinstance(intent, H.Report):
                if intent.reap is None:
                    act = V.Act.NOTE
                    record_act = V.Note()
                else:
                    act = V.Act.REAP
                    how = intent.reap
                    record_act = V.Reap(
                        V.Killed(how.signal)
                        if isinstance(how, H.Killed)
                        else V.Exited(how.code)
                    )
            else:
                assert_never(intent)
            t["acts"].append(one(V.Acts(state, act)))
            t["final_step"].append(
                _by_value(
                    V.ThirdFinal(ev.final_step.value),
                    {
                        V.ThirdFinal.NULL: None,
                        V.ThirdFinal.PROGRESS: progress(
                            rendered.rows[: seq - 1], positional=not born_0_3_0
                        ),
                    },
                )
            )
            if born_0_3_0:  # in a log born in 0.2.0 the earlier step filled `honored`
                undischarged = frozenset(x for _, x in stops if x not in named)
                t["honored"].append(
                    _by_value(
                        V.ThirdHonored(ev.honors.value),
                        {
                            V.ThirdHonored.NONE: frozenset(),
                            V.ThirdHonored.UNDISCHARGED: undischarged,
                        },
                    )
                )
            third[f"tp@{seq}"] = t
            stops_by[seq] = V.ThirdStop(f"tp@{seq}", record_act)
            named.update(rendered.honored[i])
            continue
        if not isinstance(ev, _WORKER):
            continue
        c = ev.claim
        o = obs[claim_seq[c]]
        if isinstance(ev, H.Begin):
            if phase[c] == "between":  # the pass before was continued past
                o["beat_cov"].append(
                    one(V.Covered.BEATEN if beaten[c] else V.Covered.UNBEATEN)
                )
                o["save_cov"].append(
                    one(V.Saved.SAVED if saved[c] else V.Saved.UNSAVED)
                )
            o["stride"].append(
                one(V.Stride.NEXT if ev.step == floor[c] else V.Stride.SKIP)
            )
            floor[c] = ev.step + 1
            phase[c] = "iterating"
            acts_in_pass[c], valued[c], ticked_in[c], beaten[c], saved[c] = (
                0,
                False,
                False,
                False,
                False,
            )
            pass_save[c] = None
            begun[c] = ev.step
        elif isinstance(ev, H.Emit):
            assert seq is not None
            if not ev.stepless:
                o["values"].append(one(_PHASE[phase[c]]))
                if phase[c] == "iterating":
                    valued[c] = True
                if phase[c] == "loading":
                    o["loading"].append(_clock(claim_of[c], loaded[c]))
            roles[seq] = V.ValueRole(_ROLE[phase[c]], ev.stamp is not H.Stamp.METRIC)
        elif isinstance(ev, (H.Beat, H.Beacon)):
            assert seq is not None
            if isinstance(ev, H.Beat):
                if phase[c] == "iterating":
                    o["beat_pos"].append(
                        one(
                            V.BeatPos.OPENING
                            if acts_in_pass[c] == 0
                            else V.BeatPos.INSIDE
                        )
                    )
                    ticked_in[c] = True
                else:
                    o["beat_pos"].append(one(V.BeatPos.AFTER_CLOSE))
                beaten[c] = True
                ticked[c] = begun[c]
            claimed_since = control if control > claim_seq[c] else 0
            o["watermark"].append(
                _by_value(
                    V.Watermark(ev.watermark.value),
                    {
                        V.Watermark.CONTROL: control,
                        V.Watermark.CLAIMED: claimed_since,
                        V.Watermark.TAIL: seq - 1,
                    },
                )
            )
        elif isinstance(ev, H.PassSave):
            o["save_at"].append(one(V.SaveAt.IN_PASS))
            pass_save[c] = ev
            if ev.landed:
                saved[c] = True
        elif isinstance(ev, H.Finish):
            last = last_act[c]
            at_value = (
                last is not None
                and isinstance(last[1], H.Emit)
                and not last[1].stepless
            )
            o["closing"].append(
                one(V.Closing.AT_VALUE if at_value else V.Closing.OTHER)
            )
            if (
                not at_value and not ticked_in[c]
            ):  # marker's element: what closing and beat-pos leave open
                o["marker"].append(
                    one(V.Marker.RECORDED if valued[c] else V.Marker.SILENT)
                )
            if at_value and last is not None:
                pseq = seqs[last[0]]
                assert pseq is not None
                roles[pseq] = roles[pseq]._replace(role=V.Role.CLOSING)
            if ev.saved:
                o["save_at"].append(one(V.SaveAt.AT_CLOSE))
                saved[c] = True
            phase[c] = "between"
            finished[c] = begun[c]
        elif isinstance(ev, H.Save):
            o["save_at"].append(one(_SAVE_AT[phase[c]]))
            if phase[c] == "between":
                saved[c] = True
        elif isinstance(ev, H.Rewind):
            step = _loaded_step(events, i, ev.target)
            floor[c] = 0 if step is None else _floor(step, ev.resume)
            if ev.target is None:
                o["rewinds"].append(one(V.Rewinds.ROOT))
            else:
                newest = H.newest_save(events[:i])
                o["rewinds"].append(
                    one(V.Rewinds.NEWEST if ev.target == newest else V.Rewinds.OLDER)
                )
                o["resume"].append(one(V.Resume(ev.resume.value)))
        elif isinstance(ev, H.Exit):
            assert seq is not None
            stops_by[seq] = V.OwnStop()
            cut(c)
            if ev.error is None:
                o["loop_end"].append(
                    one(
                        V.LoopEnd.IN_PASS
                        if phase[c] == "iterating"
                        else V.LoopEnd.AFTER_CLOSE
                    )
                )
                if phase[c] == "iterating":
                    o["discards"].append(
                        one(V.Discards.DISCARDS if ev.discards else V.Discards.KEEPS)
                    )
                if phase[c] == "between":
                    o["leave_save"].append(
                        one(V.Saved.SAVED if saved[c] else V.Saved.UNSAVED)
                    )
            idle = 0 if claim_of[c].clock is H.Clock.ZERO else (loaded[c] or 0)
            conventions = {
                V.FinalStep.YIELDED: begun[c],
                V.FinalStep.FINISHED: finished[c],
                V.FinalStep.TICKED: ticked[c],
                V.FinalStep.COUNTER: finished[c] if finished[c] is not None else idle,
            }
            name = "final_step" if ev.error is None else "error_final_step"
            o[name].append(_by_value(V.FinalStep(ev.final_step.value), conventions))
            if ev.final_step is H.FinalStep.COUNTER and finished[c] is None:
                # the counter reads the pre-pass clock
                o["loading"].append(_clock(claim_of[c], loaded[c]))
            if born_0_3_0:  # in a log born in 0.2.0 the earlier step filled `honored`
                decl_h = V.Honored(ev.honors.value)
                other = H.Drain.TICKS if ev.honors is H.Drain.EXIT else H.Drain.EXIT
                alike = {decl_h} | (
                    {V.Honored(other.value)}
                    if _renders_alike(h, i, honors=other)
                    else set()
                )
                o["honored"].append(Element(frozenset(alike), decl_h))
            if c not in released:
                exit_node.add(c)
            phase[c] = "exited"
            exited.add(c)
            named.update(rendered.honored[i])
        elif isinstance(ev, H.Halt):
            cut(c)
            phase[c] = "halted"
        if not isinstance(ev, H.Begin):
            acts_in_pass[c] += 1
        last_act[c] = (i, ev)
    # fold: an element wherever the other fold would render the log differently
    # (render.py reads it at drains)
    for i, ev in enumerate(events):
        if isinstance(ev, H.Claim) and stops:
            flip = H.Fold.AT_DRAIN if ev.fold is H.Fold.AT_CLAIM else H.Fold.AT_CLAIM
            if not _renders_alike(h, i, fold=flip):
                obs[claim_seq[ev.claim]]["fold"].append(one(V.Fold(ev.fold.value)))
    return Observation(
        {k: dict(v) for k, v in obs.items()}, third, stops_by, roles, tuple(starts)
    )


def holds(statement: frozenset[Any], elements: list[Element]) -> bool:
    return all(e.classes & statement for e in elements)


def declared(elements: list[Element]) -> frozenset[Any]:
    return frozenset(e.declared for e in elements)


# ----- the oracle configuration -------------------------------------------------------


def worker_facts(facts: Facts) -> V.WorkerFacts:
    """The statement a claim's elements declare: each fact the classes its elements
    name, and none where it has none (the element never occurs)."""
    return V.WorkerFacts(
        **{f.name: declared(facts.get(f.name, [])) for f in fields(V.WorkerFacts)}
    )


def third_facts(facts: Facts) -> V.ThirdPartyFacts:
    return V.ThirdPartyFacts(
        **{f.name: declared(facts.get(f.name, [])) for f in fields(V.ThirdPartyFacts)}
    )


def union_workers(a: V.WorkerFacts, b: V.WorkerFacts) -> V.WorkerFacts:
    return V.WorkerFacts(
        **{
            f.name: getattr(a, f.name) | getattr(b, f.name)
            for f in fields(V.WorkerFacts)
        }
    )


def union_third(a: V.ThirdPartyFacts, b: V.ThirdPartyFacts) -> V.ThirdPartyFacts:
    return V.ThirdPartyFacts(
        **{
            f.name: getattr(a, f.name) | getattr(b, f.name)
            for f in fields(V.ThirdPartyFacts)
        }
    )


class OracleReader:
    """The oracle classifier for one log: the answers read off the histories, a set per
    record."""

    def __init__(
        self,
        stops: dict[int, frozenset[V.StopAnswer]],
        roles: dict[int, frozenset[V.ValueRole]],
    ) -> None:
        self._stops = stops
        self._roles = roles

    def started(self, seq: int) -> frozenset[Hashable]:
        return frozenset({f"claim@{seq}"})

    def stopped(self, seq: int) -> frozenset[V.StopAnswer]:
        return self._stops[seq]

    def value(self, seq: int) -> frozenset[V.ValueRole]:
        return self._roles[seq]


class Oracle:
    """The oracle classifier (``V.Classifier``): one reader, for the log its histories
    write."""

    def __init__(self, reader: OracleReader) -> None:
        self._reader = reader

    def read(self, log: Sequence[Row]) -> OracleReader:
        return self._reader


def configuration(observations: Sequence[Observation]) -> V.Configuration:
    """The facts and answers true of every one of ``observations``, histories that write
    one log: per claim and per third party, the union of their declared statements; per
    record, the union of their classifier answers.
    """
    workers: dict[Hashable, V.WorkerFacts] = {}
    third: dict[Hashable, V.ThirdPartyFacts] = {}
    stops: dict[int, set[V.StopAnswer]] = defaultdict(set)
    roles: dict[int, set[V.ValueRole]] = defaultdict(set)
    for ob in observations:
        for seq in ob.starts:
            wf = worker_facts(ob.claims.get(seq, {}))
            key = f"claim@{seq}"
            workers[key] = union_workers(workers[key], wf) if key in workers else wf
        for key, facts in ob.third.items():
            tf = third_facts(facts)
            third[key] = union_third(third[key], tf) if key in third else tf
        for seq, a in ob.stops.items():
            stops[seq].add(a)
        for seq, r in ob.roles.items():
            roles[seq].add(r)
    # a third party one history names may be a worker's exit in another: each needs
    # facts for every key named
    for seq, answers in stops.items():
        for a in answers:
            if isinstance(a, V.ThirdStop) and a.writer not in third:
                third[a.writer] = V.ThirdPartyFacts(
                    frozenset(), frozenset(), frozenset(), frozenset(), frozenset()
                )
    # true of every history by construction: each element's declared class is among
    # the classes its behaviour is consistent with, and a union only widens. ``holds``
    # checks it, as the engine checks a statement against a log.
    for ob in observations:
        for seq, facts in ob.claims.items():
            statement = workers[f"claim@{seq}"]
            assert all(holds(getattr(statement, k), es) for k, es in facts.items())
        for key, facts in ob.third.items():
            assert all(holds(getattr(third[key], k), es) for k, es in facts.items())
    reader = OracleReader(
        {k: frozenset(v) for k, v in stops.items()},
        {k: frozenset(v) for k, v in roles.items()},
    )
    return V.Configuration(workers, third, Oracle(reader))


def born(h: H.History) -> V.Born:
    """The earlier step's readings the simulator renders by: positional, both."""
    if h.born is H.Born.V0_3_0:
        return V.Born030()
    return V.Born020()
