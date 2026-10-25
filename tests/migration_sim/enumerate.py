"""Bounded, streaming enumeration of histories.

``histories`` yields every conforming history its bounds and its ``Space``
allow, one at a time. A history is built claim by claim, in claim order:

1. **the claim**: its launch shape; its origin (a fresh start, every node the
   claims before it saved, or a checkpoint from outside at step 0 or at any of
   their nodes' steps); whether it first writes a value at its clock, and what
   its clock reads then;
2. **its iterations**: 0 to ``steps`` of them, each one of ``space.shapes``,
   labelled from the origin's floor upward by one of ``space.strides`` each,
   with one of ``space.mid_claim`` (a third party's ``stopped`` naming the live
   claim) just before its last one's body, after a tick-first loop's tick; and
   its native counterpart's drain cadence, one of ``space.cadences``;
3. **its end**: its last iteration stopped at one of ``space.cuts``, then one of
   ``space.endings``, then one of ``space.third_parties`` (a third party's
   ``stopped`` naming it); and, for every claim but the last, one of
   ``space.successors`` (the next claim in its own process, or in this one's
   after this worker exits);
4. **or its displacement**, for every claim but the last: one of
   ``space.displaced``. Something lets the next claim in while this worker
   lives (a release, a note with a takeover, or a takeover alone), and its
   tail, which may finish its iteration, run more and end, follows that
   claim's ``started``.

Before the run, one of ``space.before_run`` (a third party's ``stopped`` naming
no claim). Then 0 to ``stops`` stop requests go into the slots
``space.stop_slots`` names, in every combination.

Histories the walk refuses (``history.NotConforming``) are pruned, a prefix as
soon as it is refused, so the generator yields only conforming ones. ``Space``
holds no defaults: a caller names every choice it ranges over, as the presets
at the end do.
"""

from __future__ import annotations

import itertools
from collections.abc import Iterator
from dataclasses import dataclass
from enum import Enum
from typing import NamedTuple, TypeAlias, assert_never

from .build import (
    VALUE,
    Cut,
    Dies,
    Ending,
    Saving,
    Shape,
    Step,
    Stops,
    Tick,
    ending,
    halt_of,
    iteration,
    opening,
    own_process,
    rest,
)
from .history import (
    Cadence,
    Claim,
    Clock,
    Drain,
    Emit,
    Event,
    Exited,
    FinalStep,
    FromOutside,
    FromSave,
    Fresh,
    Gate,
    Halt,
    History,
    Honors,
    Intent,
    Killed,
    Launched,
    NodeRef,
    NotConforming,
    Origin,
    Outcome,
    Process,
    Reaped,
    Release,
    Report,
    StopRequest,
    ThirdPartyFinal,
    ThirdPartyStop,
    Via,
    Watermark,
    conforms,
    loadable,
)


class Origins(Enum):
    """Which origins a claim tries."""

    FRESH = "fresh"
    SAVED = "saved"  # each node the claims before it saved
    OUTSIDE = "outside"  # from outside, at step 0 or at any node's step so far


class LaunchShape(Enum):
    """A claim's launcher records."""

    NONE = "none"
    REAPED = "reaped"  # launched the claimant's process, reaped it after its end
    UNREAPED = "unreaped"  # launched it, never reaped it
    WRAPPED = "wrapped"  # a wrapper process launched it, reaped with exit 0


class Successor(Enum):
    """Where the next claim runs, after this one's worker exits."""

    OWN_PROCESS = "own-process"
    SAME_PROCESS = "same-process"  # this one's: two Workers run in turn by one process


@dataclass(frozen=True)
class ThirdParty:
    """A third party's ``stopped``, as the space places it."""

    intent: Intent
    error: str | None
    final_step: ThirdPartyFinal
    honors: Honors


@dataclass(frozen=True)
class Takeover:
    """The next claim's gate judges the live worker dead with nothing on the
    log: a false-dead probe, or a stale heartbeat."""


Displacer: TypeAlias = Release | Report | Takeover


@dataclass(frozen=True)
class Tail:
    """What a displaced worker does after the next claim's ``started``."""

    value: bool  # it first writes a value at its clock
    completes: bool  # it finishes the iteration it was in
    steps: tuple[Shape, ...]  # then it runs these iterations
    end: Ending


@dataclass(frozen=True)
class Displaced:
    """A live worker the next claim displaces: a release (the gate is free), a
    note (a report naming it, then a takeover), or a takeover alone."""

    by: Displacer
    tail: Tail


class StopSlot(Enum):
    """Where a stop request may go."""

    BEFORE_CLAIM = "before-claim"  # just before a claim's started
    MID_CLAIM = "mid-claim"  # just before a claim's last iteration begins
    AFTER_CLAIM = "after-claim"  # after a claim's end and its third party


@dataclass(frozen=True)
class Space:
    """The options enumeration ranges over; each field lists the ones tried."""

    origins: tuple[Origins, ...]
    loading: tuple[bool, ...]
    clocks: tuple[Clock, ...]  # read only by a loading value of a resumed claim
    cadences: tuple[Cadence, ...]
    strides: tuple[int, ...]
    shapes: tuple[Shape, ...]
    mid_claim: tuple[ThirdParty | None, ...]
    cuts: tuple[Cut, ...]
    endings: tuple[Ending, ...]
    third_parties: tuple[ThirdParty | None, ...]
    successors: tuple[Successor, ...]
    displaced: tuple[Displaced, ...]
    before_run: tuple[ThirdParty | None, ...]
    launches: tuple[LaunchShape, ...]
    stop_slots: tuple[StopSlot, ...]


@dataclass(frozen=True)
class _Slot:
    """A place a stop request may go; removed before the history is built."""

    kind: StopSlot


_Item: TypeAlias = Event | _Slot


class _Entry(NamedTuple):
    """How the next claim enters, as the claim before it left."""

    process: Process
    gate: Gate
    tail: list[Event]  # a displaced worker's rest, after the claim's started


class _Run(NamedTuple):
    """A claim's iterations, the third party before its last, and where its
    last one stops."""

    steps: list[Step]
    mid: ThirdParty | None
    cut: Cut


def histories(
    *, claims: int, steps: int, stops: int, space: Space
) -> Iterator[History]:
    """Every conforming history of exactly ``claims`` claims, each running at
    most ``steps`` iterations, with at most ``stops`` stop requests."""
    for before in space.before_run:
        start: list[_Item] = []
        if before is not None:
            start.append(_third_party(None, before))
        entry = _Entry(own_process(0), Gate.FREE, [])
        for items in _claims(0, claims, steps, space, start, entry):
            for events in _with_stops(items, stops, space.stop_slots):
                history = History(tuple(events))
                if conforms(history):
                    yield history


def _claims(
    c: int, claims: int, steps: int, space: Space, items: list[_Item], entry: _Entry
) -> Iterator[list[_Item]]:
    """Extend ``items`` with claims ``c`` to ``claims - 1``."""
    if c == claims:
        yield items
        return
    try:
        saved = loadable(tuple(e for e in items if not isinstance(e, _Slot)))
    except NotConforming:
        return  # a prefix no conforming writer produces: prune it
    last = c == claims - 1
    for launch, origin, loading in itertools.product(
        space.launches, _origins(saved, space.origins), space.loading
    ):
        launch_id = None if launch is LaunchShape.NONE else f"L{c}"
        for clock, cadence in itertools.product(
            _clocks(origin, loading, space.clocks), space.cadences
        ):
            claimed = _claim_items(
                c, entry, launch, launch_id, origin, clock, cadence, loading
            )
            for its in _iterations(origin, saved, steps, space):
                body = claimed + _body(c, its)
                for end in space.endings:
                    if not _ends(its, end):
                        continue
                    done = body + ending(c, end)
                    for third in space.third_parties:
                        after = done + _reap(launch, launch_id, end)
                        if third is not None:
                            after.append(_third_party(c, third))
                        after.append(_Slot(StopSlot.AFTER_CLAIM))
                        for nxt in [None] if last else space.successors:
                            if nxt is Successor.SAME_PROCESS:
                                if not isinstance(end, Stops) or launch_id is not None:
                                    continue
                                halt = done[-1]
                                assert isinstance(halt, Halt)
                                shared = (
                                    done[:-1] + [Halt(c, None)] + after[len(done) :]
                                )
                                into = _Entry(entry.process, Gate.FREE, [])
                                yield from _claims(
                                    c + 1, claims, steps, space, items + shared, into
                                )
                            else:
                                into = _Entry(own_process(c + 1), Gate.FREE, [])
                                yield from _claims(
                                    c + 1, claims, steps, space, items + after, into
                                )
                if last:
                    continue
                for d in space.displaced:
                    gate = Gate.FREE if isinstance(d.by, Release) else Gate.TAKEOVER
                    displaced = body.copy()
                    if isinstance(d.by, (Release, Report)):
                        displaced.append(
                            ThirdPartyStop(
                                c, d.by, None, ThirdPartyFinal.PROGRESS, Honors.NONE
                            )
                        )
                    tail = _tail(c, its, origin, saved, d.tail)
                    tail += _reap(launch, launch_id, d.tail.end)
                    into = _Entry(own_process(c + 1), gate, tail)
                    yield from _claims(
                        c + 1, claims, steps, space, items + displaced, into
                    )


def _claim_items(
    c: int,
    entry: _Entry,
    launch: LaunchShape,
    launch_id: str | None,
    origin: Origin,
    clock: Clock,
    cadence: Cadence,
    loading: bool,
) -> list[_Item]:
    """Claim ``c``'s start: its launch, its claim, the previous claim's
    displaced tail, and its loading value."""
    items: list[_Item] = [_Slot(StopSlot.BEFORE_CLAIM)]
    if launch_id is not None:
        launched = own_process(c) if launch is not LaunchShape.WRAPPED else _wrapper(c)
        items.append(Launched(launch_id, launched))
    items.append(Claim(c, entry.process, entry.gate, origin, clock, launch_id, cadence))
    items += entry.tail
    if loading:
        items.append(Emit(c, VALUE, stepless=False))
    return items


def _body(c: int, its: _Run) -> list[_Item]:
    """Claim ``c``'s iterations, the last stopped at its cut, with the stop slot
    before the last and the third party just before the last one's body."""
    items: list[_Item] = []
    for k, it in enumerate(its.steps):
        if k < len(its.steps) - 1:
            items += iteration(c, it, cut=Cut.DONE)
            continue
        items.append(_Slot(StopSlot.MID_CLAIM))
        head = opening(c, it)
        items += head
        if its.mid is not None:
            items.append(_third_party(c, its.mid))
        items += iteration(c, it, cut=its.cut)[len(head) :]
    return items


def _tail(
    c: int, its: _Run, origin: Origin, saved: dict[NodeRef, int], tail: Tail
) -> list[Event]:
    events: list[Event] = [Emit(c, VALUE, stepless=False)] if tail.value else []
    if its.steps:
        if tail.completes:
            events += rest(c, its.steps[-1], cut=its.cut)
        label = its.steps[-1].step + 1
    else:
        label = _floor(origin, saved)
    for k, shape in enumerate(tail.steps):
        events += iteration(c, Step(label + k, shape), cut=Cut.DONE)
    return events + ending(c, tail.end)


def _ends(its: _Run, end: Ending) -> bool:
    """Whether ``end`` can follow these iterations: only a clean exit inside an
    open iteration discards it."""
    if isinstance(end, Stops) and end.discards:
        return (
            end.error is None
            and bool(its.steps)
            and its.cut in (Cut.MID_BODY, Cut.MID_SAVE)
        )
    return True


def _wrapper(c: int) -> Process:
    return Process(own_process(c).host, 2000 + c)


def _reap(launch: LaunchShape, launch_id: str | None, end: Ending) -> list[Event]:
    if launch_id is None or launch is LaunchShape.UNREAPED:
        return []
    if launch is LaunchShape.WRAPPED:
        return [Reaped(launch_id, Exited(0))]
    return [Reaped(launch_id, halt_of(end))]


def _origins(saved: dict[NodeRef, int], kinds: tuple[Origins, ...]) -> list[Origin]:
    origins: list[Origin] = []
    for kind in kinds:
        if kind is Origins.FRESH:
            origins.append(Fresh())
        elif kind is Origins.SAVED:
            origins += [FromSave(node) for node in saved]
        elif kind is Origins.OUTSIDE:
            origins += [FromOutside(r) for r in sorted({0, *saved.values()})]
        else:
            assert_never(kind)
    return origins


def _clocks(
    origin: Origin, loading: bool, clocks: tuple[Clock, ...]
) -> tuple[Clock, ...]:
    """The clocks that differ: only a loading value of a resumed claim reads
    one."""
    if loading and not isinstance(origin, Fresh):
        return clocks
    return clocks[:1]


def _iterations(
    origin: Origin, saved: dict[NodeRef, int], steps: int, space: Space
) -> Iterator[_Run]:
    """Each run of 0 to ``steps`` iterations from the origin's floor, with each
    third party before its last and each cut its last can take."""
    floor = _floor(origin, saved)
    for n in range(steps + 1):
        for shapes in itertools.product(space.shapes, repeat=n):
            for strides in itertools.product(space.strides, repeat=n):
                labels = list(itertools.accumulate(strides, initial=floor - 1))[1:]
                its = [Step(s, shape) for s, shape in zip(labels, shapes)]
                for mid in space.mid_claim if its else (None,):
                    for cut in _cuts(its, space.cuts):
                        yield _Run(its, mid, cut)


def _floor(origin: Origin, saved: dict[NodeRef, int]) -> int:
    if isinstance(origin, Fresh):
        return 0
    if isinstance(origin, FromOutside):
        return origin.step + 1
    if isinstance(origin, FromSave):
        return saved[origin.node] + 1
    assert_never(origin)


def _cuts(its: list[Step], cuts: tuple[Cut, ...]) -> list[Cut]:
    """The cuts that differ for these iterations: none without one, and a
    mid-save cut only where the last iteration records its save."""
    if not its:
        return [Cut.DONE]
    last = its[-1].shape
    return [
        cut for cut in cuts if cut is not Cut.MID_SAVE or last.save is Saving.RECORDED
    ]


def _third_party(names: int | None, third: ThirdParty) -> ThirdPartyStop:
    return ThirdPartyStop(
        names, third.intent, third.error, third.final_step, third.honors
    )


def _with_stops(
    items: list[_Item], stops: int, slots: tuple[StopSlot, ...]
) -> Iterator[list[Event]]:
    """``items`` with 0 to ``stops`` stop requests in its open slots, in every
    combination, and the slots removed."""
    open_slots = [
        i for i, x in enumerate(items) if isinstance(x, _Slot) and x.kind in slots
    ]
    for k in range(stops + 1):
        for at in itertools.combinations_with_replacement(open_slots, k):
            events: list[Event] = []
            n = 0
            for i, x in enumerate(items):
                for _ in range(at.count(i)):
                    events.append(StopRequest(f"x{n}"))
                    n += 1
                if not isinstance(x, _Slot):
                    events.append(x)
            yield events


# ----- presets: whole spaces, each for one question -------------------------------------

STEPS = Shape(
    valued=True,
    tick=Tick.AFTER_BODY,
    watermark=Watermark.CONTROL,
    save=Saving.NONE,
    trailing=False,
)
"""The reference ``steps()`` worker that emits each iteration and saves nothing."""

SAVES = Shape(
    valued=True,
    tick=Tick.AFTER_BODY,
    watermark=Watermark.CONTROL,
    save=Saving.IN_BODY,
    trailing=False,
)
"""The ``steps()`` worker that emits each iteration and saves in its body."""

UNLOGGED = Shape(
    valued=False,
    tick=Tick.NONE,
    watermark=Watermark.CONTROL,
    save=Saving.IN_BODY,
    trailing=False,
)
"""An iteration that writes nothing: no value, no tick, and a save."""

KILL = Dies(Killed(9))
COMPLETES = Stops(
    True, None, FinalStep.YIELDED, Drain.EXIT, False, saves=False, teardown=False
)
PREEMPTED = Stops(
    False, None, FinalStep.YIELDED, Drain.EXIT, False, saves=False, teardown=False
)
ERRORS = Stops(
    False, "boom", FinalStep.YIELDED, Drain.EXIT, False, saves=False, teardown=False
)
DISCARDS = Stops(
    False, None, FinalStep.YIELDED, Drain.EXIT, True, saves=False, teardown=False
)


def _plain(
    *,
    origins: tuple[Origins, ...],
    loading: tuple[bool, ...],
    shapes: tuple[Shape, ...],
    cuts: tuple[Cut, ...],
    endings: tuple[Ending, ...],
) -> Space:
    """A space with no third party, displacement, launch or stop."""
    return Space(
        origins=origins,
        loading=loading,
        clocks=(Clock.LOADED,),
        cadences=(Cadence.COMMITS,),
        strides=(1,),
        shapes=shapes,
        mid_claim=(None,),
        cuts=cuts,
        endings=endings,
        third_parties=(None,),
        successors=(Successor.OWN_PROCESS,),
        displaced=(),
        before_run=(None,),
        launches=(LaunchShape.NONE,),
        stop_slots=(),
    )


WORKER_SHAPES = _plain(
    origins=(Origins.FRESH,),
    loading=(False, True),
    shapes=(
        STEPS,
        Shape(True, Tick.BEFORE_BODY, Watermark.CONTROL, Saving.NONE, trailing=False),
        UNLOGGED,
        Shape(False, Tick.AFTER_BODY, Watermark.CONTROL, Saving.NONE, trailing=True),
        Shape(
            True, Tick.AFTER_BODY, Watermark.CONTROL, Saving.RECORDED, trailing=False
        ),
    ),
    cuts=(Cut.MID_BODY, Cut.MID_SAVE, Cut.BEFORE_TICK, Cut.DONE),
    endings=(
        KILL,
        COMPLETES,
        PREEMPTED,
        ERRORS,
        DISCARDS,
        Stops(
            False,
            None,
            FinalStep.FINISHED,
            Drain.EXIT,
            False,
            saves=False,
            teardown=False,
        ),
        Stops(
            False,
            None,
            FinalStep.TICKED,
            Drain.EXIT,
            False,
            saves=False,
            teardown=False,
        ),
    ),
)
"""One claim, fresh: how an iteration is laid out (tick after or before the
body, or none; a value in the body or after the tick; a save recorded or not),
where the worker stops in its last one (mid-body, mid-save, before its tick, or
after it), and how it ends (killed, or its own stopped: completed, preempted or
errored, with each final_step convention; or a clean exit its native
counterpart discards). Run with ``claims=1, steps=2, stops=0``."""

RESUMES = Space(
    origins=(Origins.FRESH, Origins.SAVED, Origins.OUTSIDE),
    loading=(False, True),
    clocks=(Clock.LOADED,),
    cadences=(Cadence.COMMITS,),
    strides=(1,),
    shapes=(SAVES, UNLOGGED),
    mid_claim=(None,),
    cuts=(Cut.BEFORE_TICK,),
    endings=(KILL,),
    third_parties=(None,),
    successors=(Successor.OWN_PROCESS,),
    displaced=(),
    before_run=(None,),
    launches=(LaunchShape.NONE,),
    stop_slots=(),
)
"""Two claims, each killed after finishing and saving its last iteration, before
its tick: where the second resumed from (fresh, each save, outside), with
loading values and unlogged iterations (a resume gap, a run-on). Run with
``claims=2, steps=2, stops=0``."""

LOADING = Space(
    origins=(Origins.FRESH, Origins.SAVED, Origins.OUTSIDE),
    loading=(True,),
    clocks=(Clock.LOADED, Clock.ZERO),
    cadences=(Cadence.COMMITS,),
    strides=(1,),
    shapes=(SAVES, UNLOGGED),
    mid_claim=(None,),
    cuts=(Cut.MID_BODY, Cut.BEFORE_TICK),
    endings=(KILL, PREEMPTED),
    third_parties=(None,),
    successors=(Successor.OWN_PROCESS,),
    displaced=(),
    before_run=(None,),
    launches=(LaunchShape.NONE,),
    stop_slots=(),
)
"""Two claims that each write a value before their first iteration, its clock
reading the loaded step or 0 (mycooc's counter), then stop mid-body or before
a tick. Run with ``claims=2, steps=1, stops=0``."""

_RELEASE = ThirdParty(
    Release(Outcome.LOST, Via.HELPER), None, ThirdPartyFinal.NULL, Honors.NONE
)
_NOTE = ThirdParty(Report(None), "note", ThirdPartyFinal.PROGRESS, Honors.NONE)

THIRD_PARTIES = Space(
    origins=(Origins.FRESH, Origins.SAVED),
    loading=(False,),
    clocks=(Clock.LOADED,),
    cadences=(Cadence.COMMITS,),
    strides=(1,),
    shapes=(SAVES,),
    mid_claim=(None,),
    cuts=(Cut.MID_BODY,),
    endings=(
        KILL,
        PREEMPTED,
        Stops(
            False, None, FinalStep.YIELDED, Drain.EXIT, False, saves=True, teardown=True
        ),
    ),
    third_parties=(
        None,
        _RELEASE,
        ThirdParty(
            Release(Outcome.ERRORED, Via.DIRECT),
            "NODE_FAIL",
            ThirdPartyFinal.NULL,
            Honors.NONE,
        ),
        _NOTE,
        ThirdParty(Report(Killed(15)), None, ThirdPartyFinal.PROGRESS, Honors.NONE),
    ),
    successors=(Successor.OWN_PROCESS,),
    displaced=(),
    before_run=(None,),
    launches=(LaunchShape.NONE,),
    stop_slots=(),
)
"""Third parties' stops after a claim's end (a release through the helper or
written directly, a note, a non-claimant's reap); a worker that saves after its
own exit and writes a teardown value. Run with ``claims=2, steps=1,
stops=0``."""

RELEASED_LIVE = Space(
    origins=(Origins.FRESH,),
    loading=(False,),
    clocks=(Clock.LOADED,),
    cadences=(Cadence.COMMITS,),
    strides=(1,),
    shapes=(
        SAVES,
        Shape(
            False, Tick.AFTER_BODY, Watermark.CONTROL, Saving.IN_BODY, trailing=False
        ),
    ),
    mid_claim=(None, _RELEASE, _NOTE),
    cuts=(Cut.MID_BODY, Cut.BEFORE_TICK, Cut.DONE),
    endings=(KILL, COMPLETES, PREEMPTED),
    third_parties=(None,),
    successors=(Successor.OWN_PROCESS,),
    displaced=(),
    before_run=(None,),
    launches=(LaunchShape.NONE,),
    stop_slots=(),
)
"""One claim that a third party's stop names while its worker lives, inside its
last iteration: a release (mistaken, which fences it) or a note; the worker
then finishes that iteration and ends. ``BEATEN_PAST``'s and
``LATER_COMPLETED``'s shapes. Run with ``claims=1, steps=2, stops=0``."""

DISPLACEMENT = Space(
    origins=(Origins.FRESH, Origins.SAVED, Origins.OUTSIDE),
    loading=(False,),
    clocks=(Clock.LOADED,),
    cadences=(Cadence.COMMITS,),
    strides=(1,),
    shapes=(SAVES,),
    mid_claim=(None,),
    cuts=(Cut.MID_BODY, Cut.BEFORE_TICK),
    endings=(COMPLETES,),
    third_parties=(None,),
    successors=(Successor.OWN_PROCESS,),
    displaced=tuple(
        Displaced(by, tail)
        for by in (Takeover(), Release(Outcome.LOST, Via.HELPER), Report(None))
        for tail in (
            Tail(value=True, completes=False, steps=(), end=KILL),
            Tail(value=False, completes=True, steps=(), end=KILL),
            Tail(value=False, completes=True, steps=(SAVES,), end=PREEMPTED),
        )
    ),
    before_run=(None,),
    launches=(LaunchShape.NONE,),
    stop_slots=(),
)
"""A worker displaced while it lives, by a takeover, a release, or a note and a
takeover, that then writes a value, or finishes its iteration and beats, or
runs on and exits, after its successor claimed; the successor resumes fresh,
from a save made before its claim, or from outside at step 0 or at a saved
node's step. (A resume from a save the displaced worker made after the
takeover, which no node names, is in the named goldens, not here.) Run with
``claims=2, steps=2, stops=0``."""

STOPS_AND_REAPS = Space(
    origins=(Origins.FRESH, Origins.SAVED),
    loading=(False,),
    clocks=(Clock.LOADED,),
    cadences=(Cadence.COMMITS,),
    strides=(1,),
    shapes=(SAVES,),
    mid_claim=(None,),
    cuts=(Cut.MID_BODY,),
    endings=(KILL, PREEMPTED),
    third_parties=(
        None,
        ThirdParty(Report(None), None, ThirdPartyFinal.NULL, Honors.UNDISCHARGED),
    ),
    successors=(Successor.OWN_PROCESS,),
    displaced=(),
    before_run=(None,),
    launches=(LaunchShape.NONE, LaunchShape.REAPED, LaunchShape.WRAPPED),
    stop_slots=(StopSlot.AFTER_CLAIM,),
)
"""Stop requests, the stops that clear them, and launches reaped or not, around
two claims. Run with ``claims=2, steps=1, stops=1``."""

WITHDRAWALS = Space(
    origins=(Origins.FRESH,),
    loading=(False,),
    clocks=(Clock.LOADED,),
    cadences=(Cadence.COMMITS, Cadence.TICKS),
    strides=(1,),
    shapes=(
        SAVES,
        Shape(
            True, Tick.BEFORE_BODY, Watermark.CONTROL, Saving.IN_BODY, trailing=False
        ),
    ),
    mid_claim=(
        None,
        ThirdParty(Report(None), None, ThirdPartyFinal.NULL, Honors.UNDISCHARGED),
    ),
    cuts=(Cut.DONE,),
    endings=(COMPLETES, PREEMPTED),
    third_parties=(None,),
    successors=(Successor.OWN_PROCESS,),
    displaced=(),
    before_run=(None,),
    launches=(LaunchShape.NONE,),
    stop_slots=(StopSlot.MID_CLAIM,),
)
"""One claim, a stop before its last iteration, and a third party that clears
it inside that iteration, after a tick-first loop's tick: whether the stop is
honoured turns on the native counterpart's drain cadence, a heartbeat at the
tick or the commit alone. Run with ``claims=1, steps=2, stops=1``."""
