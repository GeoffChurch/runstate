"""Bounded, streaming enumeration of histories.

``histories`` yields every conforming history its bounds and its ``Space``
allow, one at a time. A history is built claim by claim, in claim order:

1. **the claim**: its launch shape; a stray value its process writes before it
   claims (``space.strays``); what it loaded (nothing, every save the claims
   before it made, the newest of them, as ``load=newest`` has it, or a
   checkpoint from outside at step 0 or at any saved step); whether it first
   writes a value at its clock, and what its clock reads then; and its writer's
   facts ``fold`` and ``cut_save`` (``space.folds``, read only where stops are
   placed; ``space.cut_saves``, read only by a claim whose pass saves inside
   it);
2. **its iterations**: 0 to ``steps`` of them, each one of ``space.shapes``,
   labelled from the origin's floor upward by one of ``space.strides`` each,
   with one of ``space.mid_claim`` (a third party's ``stopped`` naming the live
   claim) in its last one, at one of ``space.mid_positions``;
3. **its end**: its last iteration stopped at one of ``space.cuts``, then one of
   ``space.endings``, then one of ``space.third_parties`` (a third party's
   ``stopped`` naming it); and, for every claim but the last, one of
   ``space.successors`` (the next claim in its own process, or in this one's
   after this worker exits);
4. **or its displacement**, for every claim but the last: one of
   ``space.displaced``. Something lets the next claim in while this worker
   lives (a release, a note with a takeover, or a takeover alone), and its
   tail, which may finish its iteration, run more and save, and end, follows
   that claim's ``started``.

Before the run, one of ``space.before_run`` (a third party's ``stopped`` naming
no claim). Then 0 to ``stops`` stop requests go into the slots
``space.stop_slots`` names, in every combination.

Histories the walk refuses (``history.NotConforming``) are pruned, a prefix as
soon as it is refused, so the generator yields only conforming ones, each
once. ``Space``
holds no defaults: a caller names every choice it ranges over, as the presets
at the end do. ``slot`` is a family of its own: three claims whose loads a
constraint across claims can rule on (the critique's SLOT).
"""

from __future__ import annotations

import itertools
from collections.abc import Iterator
from dataclasses import dataclass, replace
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
    Claim,
    Clock,
    CutSave,
    Drain,
    Emit,
    Undated,
    Event,
    Exited,
    FinalStep,
    Finish,
    Fold,
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
    NotConforming,
    Origin,
    Outcome,
    Process,
    Reaped,
    Release,
    Report,
    SaveRef,
    StopRequest,
    Stray,
    ThirdPartyFinal,
    ThirdPartyStop,
    Via,
    Watermark,
    conforms,
    loadable,
    newest_save,
)


class Origins(Enum):
    """Which origins a claim tries."""

    FRESH = "fresh"
    SAVED = "saved"  # each save the claims before it made
    NEWEST = "newest"  # the newest save at its claim, or fresh where none is:
    # mycooc's load=newest, one checkpoint slot every save overwrites
    OUTSIDE = "outside"  # from outside, at step 0 or at any saved step so far


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


class MidPosition(Enum):
    """Where a third party's ``stopped`` inside a live claim goes, in its last
    iteration. The two differ only where a tick-first loop ticks between them."""

    BEFORE_ITERATION = "before-iteration"  # before the iteration begins
    BEFORE_BODY = "before-body"  # just before its body, after a tick-first tick


class StopSlot(Enum):
    """Where a stop request may go."""

    BEFORE_CLAIM = "before-claim"  # just before a claim's started
    IN_PASS = "in-pass"  # inside a claim's first pass of several, before its close
    MID_CLAIM = "mid-claim"  # just before a claim's last iteration begins
    AFTER_CLAIM = "after-claim"  # after a claim's end and its third party


@dataclass(frozen=True)
class Space:
    """The options enumeration ranges over; each field lists the ones tried."""

    origins: tuple[Origins, ...]
    loading: tuple[bool, ...]
    clocks: tuple[Clock, ...]  # read only by a loading value of a resumed claim
    folds: tuple[Fold, ...]  # read only where stops are placed
    cut_saves: tuple[CutSave, ...]  # read only by a claim whose pass saves inside it
    strays: tuple[bool, ...]  # a value a process writes before it claims
    strides: tuple[int, ...]
    shapes: tuple[Shape, ...]
    mid_claim: tuple[ThirdParty | None, ...]
    mid_positions: tuple[MidPosition, ...]
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
    """A claim's iterations, the third party in its last and where it goes,
    and where its last one stops."""

    steps: list[Step]
    mid: ThirdParty | None
    position: MidPosition
    cut: Cut


def histories(
    *, claims: int, steps: int, stops: int, space: Space
) -> Iterator[History]:
    """Every conforming history of exactly ``claims`` claims, each running at
    most ``steps`` iterations, with at most ``stops`` stop requests, once each:
    two choices can build one history (shapes that agree up to a cut, a cut
    before a tick the shape never takes and the cut after it, the newest save
    and that save named), and it is yielded the first time."""
    seen: set[History] = set()
    for before in space.before_run:
        start: list[_Item] = []
        if before is not None:
            start.append(_third_party(None, before))
        entry = _Entry(own_process(0), Gate.FREE, [])
        for items in _claims(0, claims, steps, space, start, entry):
            for events in _with_stops(items, stops, space.stop_slots):
                history = History(tuple(events))
                if history not in seen and conforms(history):
                    seen.add(history)
                    yield history


def _claims(
    c: int, claims: int, steps: int, space: Space, items: list[_Item], entry: _Entry
) -> Iterator[list[_Item]]:
    """Extend ``items`` with claims ``c`` to ``claims - 1``."""
    if c == claims:
        yield items
        return
    prefix = tuple(e for e in items if not isinstance(e, _Slot))
    try:
        saved = loadable(prefix)
        newest = newest_save(prefix)
    except NotConforming:
        return  # a prefix no conforming writer produces: prune it
    last = c == claims - 1
    folds = space.folds if space.stop_slots else space.folds[:1]
    for launch, origin, loading, fold in itertools.product(
        space.launches, _origins(saved, newest, space.origins), space.loading, folds
    ):
        launch_id = None if launch is LaunchShape.NONE else f"L{c}"
        for clock in _clocks(origin, loading, space.clocks):
            for its in _iterations(origin, saved, steps, space):
                for cut_save, stray in itertools.product(
                    _cut_saves(its.steps, space.cut_saves), space.strays
                ):
                    facts = _Facts(clock, fold, cut_save)
                    stray_step = max(_floor(origin, saved) - 1, 0)
                    claimed = _claim_items(
                        c,
                        entry,
                        launch,
                        launch_id,
                        origin,
                        facts,
                        loading,
                        stray,
                        stray_step,
                    )
                    yield from _ends_of(
                        c,
                        claims,
                        steps,
                        space,
                        items,
                        entry,
                        claimed,
                        its,
                        _Origin(origin, saved),
                        _Launch(launch, launch_id),
                        last,
                    )


class _Facts(NamedTuple):
    clock: Clock
    fold: Fold
    cut_save: CutSave


class _Origin(NamedTuple):
    origin: Origin
    saved: dict[SaveRef, int]


class _Launch(NamedTuple):
    shape: LaunchShape
    id: str | None


def _ends_of(
    c: int,
    claims: int,
    steps: int,
    space: Space,
    items: list[_Item],
    entry: _Entry,
    claimed: list[_Item],
    its: _Run,
    origin: _Origin,
    launch: _Launch,
    last: bool,
) -> Iterator[list[_Item]]:
    """Claim ``c``'s end, by each ending and third party or displacement, and
    the claims after it."""
    body = claimed + _body(c, its)
    for end in space.endings:
        if not _ends(its, end):
            continue
        done = body + ending(c, end)
        for third in space.third_parties:
            after = done + _reap(launch.shape, launch.id, end)
            if third is not None:
                after.append(_third_party(c, third))
            after.append(_Slot(StopSlot.AFTER_CLAIM))
            for nxt in [None] if last else space.successors:
                if nxt is Successor.SAME_PROCESS:
                    if not isinstance(end, Stops) or launch.id is not None:
                        continue
                    shared = done[:-1] + [Halt(c, None)] + after[len(done) :]
                    into = _Entry(entry.process, Gate.FREE, [])
                    yield from _claims(
                        c + 1, claims, steps, space, items + shared, into
                    )
                else:
                    into = _Entry(own_process(c + 1), Gate.FREE, [])
                    yield from _claims(c + 1, claims, steps, space, items + after, into)
    if last:
        return
    for d in space.displaced:
        gate = Gate.FREE if isinstance(d.by, Release) else Gate.TAKEOVER
        displaced = body.copy()
        if isinstance(d.by, (Release, Report)):
            displaced.append(
                ThirdPartyStop(c, d.by, None, ThirdPartyFinal.PROGRESS, Honors.NONE)
            )
        tail = _tail(c, its, origin.origin, origin.saved, d.tail)
        tail += _reap(launch.shape, launch.id, d.tail.end)
        into = _Entry(own_process(c + 1), gate, tail)
        yield from _claims(c + 1, claims, steps, space, items + displaced, into)


def _claim_items(
    c: int,
    entry: _Entry,
    launch: LaunchShape,
    launch_id: str | None,
    origin: Origin,
    facts: _Facts,
    loading: bool,
    stray: bool,
    stray_step: int,
) -> list[_Item]:
    """Claim ``c``'s start: its launch, a stray value its process writes before
    it claims, its claim, the previous claim's displaced tail, and its loading
    value."""
    items: list[_Item] = [_Slot(StopSlot.BEFORE_CLAIM)]
    if launch_id is not None:
        launched = own_process(c) if launch is not LaunchShape.WRAPPED else _wrapper(c)
        items.append(Launched(launch_id, launched))
    if stray:
        items.append(Stray(VALUE, stray_step))
    items.append(
        Claim(
            c,
            entry.process,
            entry.gate,
            origin,
            launch_id,
            facts.clock,
            facts.fold,
            facts.cut_save,
        )
    )
    items += entry.tail
    if loading:
        items.append(Emit(c, VALUE, stepless=False))
    return items


def _body(c: int, its: _Run) -> list[_Item]:
    """Claim ``c``'s iterations, the last stopped at its cut, with the stop slot
    before the last and the third party at its position in the last."""
    items: list[_Item] = []
    for k, it in enumerate(its.steps):
        if k < len(its.steps) - 1:
            events = iteration(c, it, cut=Cut.DONE)
            close = next(j for j, ev in enumerate(events) if isinstance(ev, Finish))
            if k == 0:
                items += [*events[:close], _Slot(StopSlot.IN_PASS), *events[close:]]
            else:
                items += events
            continue
        items.append(_Slot(StopSlot.MID_CLAIM))
        mid: list[Event] = [] if its.mid is None else [_third_party(c, its.mid)]
        head = opening(c, it)
        if its.position is MidPosition.BEFORE_ITERATION:
            items += mid + head
        elif its.position is MidPosition.BEFORE_BODY:
            items += head + mid
        else:
            assert_never(its.position)
        items += iteration(c, it, cut=its.cut)[len(head) :]
    return items


def _tail(
    c: int, its: _Run, origin: Origin, saved: dict[SaveRef, int], tail: Tail
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
            and its.cut in (Cut.MID_BODY, Cut.MID_SAVE, Cut.AFTER_SAVE)
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


def _origins(
    saved: dict[SaveRef, int], newest: SaveRef | None, kinds: tuple[Origins, ...]
) -> list[Origin]:
    origins: list[Origin] = []
    for kind in kinds:
        if kind is Origins.FRESH:
            origins.append(Fresh())
        elif kind is Origins.SAVED:
            origins += [FromSave(save) for save in saved]
        elif kind is Origins.NEWEST:
            origins.append(Fresh() if newest is None else FromSave(newest))
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
    origin: Origin, saved: dict[SaveRef, int], steps: int, space: Space
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
                    for position in _positions(its, mid, space.mid_positions):
                        for cut in _cuts(its, space.cuts):
                            yield _Run(its, mid, position, cut)


def _positions(
    its: list[Step], mid: ThirdParty | None, positions: tuple[MidPosition, ...]
) -> tuple[MidPosition, ...]:
    """The positions that differ: only a tick-first last iteration ticks
    between them."""
    if mid is not None and its[-1].shape.tick is Tick.BEFORE_BODY:
        return positions
    return positions[:1]


def _floor(origin: Origin, saved: dict[SaveRef, int]) -> int:
    if isinstance(origin, Fresh):
        return 0
    if isinstance(origin, FromOutside):
        return origin.step + 1
    if isinstance(origin, FromSave):
        return saved[origin.save] + 1
    assert_never(origin)


def _cut_saves(its: list[Step], cut_saves: tuple[CutSave, ...]) -> tuple[CutSave, ...]:
    """The facts that differ: only a pass that saves inside it reads one."""
    if any(it.shape.save is Saving.IN_PASS for it in its):
        return cut_saves
    return cut_saves[:1]


def _cuts(its: list[Step], cuts: tuple[Cut, ...]) -> list[Cut]:
    """The cuts that differ for these iterations: none without one, and a
    mid-save cut only where the last iteration records its save."""
    if not its:
        return [Cut.DONE]
    save = its[-1].shape.save
    return [
        cut
        for cut in cuts
        if (cut is not Cut.MID_SAVE or save in (Saving.RECORDED, Saving.IN_PASS))
        and (cut is not Cut.AFTER_SAVE or save is Saving.IN_PASS)
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


def _shape(
    valued: bool, tick: Tick, save: Saving, *, trailing: bool, ended: Undated
) -> Shape:
    return Shape(valued, tick, Watermark.CONTROL, save, trailing, ended)


STEPS = _shape(
    True, Tick.AFTER_BODY, Saving.NONE, trailing=False, ended=Undated.OWN_LAST
)
"""The reference ``steps()`` worker that emits each iteration and saves nothing."""

SAVES = _shape(
    True, Tick.AFTER_BODY, Saving.IN_BODY, trailing=False, ended=Undated.OWN_LAST
)
"""The ``steps()`` worker that emits each iteration and saves in its body."""

UNSAVED = STEPS

UNLOGGED = _shape(
    False, Tick.NONE, Saving.IN_BODY, trailing=False, ended=Undated.OWN_LAST
)
"""An iteration that writes nothing: no value, no tick, and a save."""

MYCOOC = _shape(
    True, Tick.AFTER_SAVE, Saving.IN_PASS, trailing=False, ended=Undated.OWN_LAST
)
"""mycooc's pass: its ``status``, a save of its completed state, its tick, its
metrics and ``stage``, then its close (d78b72f1's records)."""

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

_RELEASE = ThirdParty(
    Release(Outcome.LOST, Via.HELPER), None, ThirdPartyFinal.NULL, Honors.NONE
)
_NOTE = ThirdParty(Report(None), "note", ThirdPartyFinal.PROGRESS, Honors.NONE)


def _space(
    *,
    origins: tuple[Origins, ...],
    loading: tuple[bool, ...],
    shapes: tuple[Shape, ...],
    cuts: tuple[Cut, ...],
    endings: tuple[Ending, ...],
) -> Space:
    """A space with one value of every other option: the reference Worker's
    facts, no stray, third party, displacement, launch or stop."""
    return Space(
        origins=origins,
        loading=loading,
        clocks=(Clock.LOADED,),
        folds=(Fold.AT_CLAIM,),
        cut_saves=(CutSave.LANDED,),
        strays=(False,),
        strides=(1,),
        shapes=shapes,
        mid_claim=(None,),
        mid_positions=(MidPosition.BEFORE_BODY,),
        cuts=cuts,
        endings=endings,
        third_parties=(None,),
        successors=(Successor.OWN_PROCESS,),
        displaced=(),
        before_run=(None,),
        launches=(LaunchShape.NONE,),
        stop_slots=(),
    )


WORKER_SHAPES = _space(
    origins=(Origins.FRESH,),
    loading=(False, True),
    shapes=(
        STEPS,
        _shape(
            True, Tick.BEFORE_BODY, Saving.NONE, trailing=False, ended=Undated.OWN_LAST
        ),
        UNLOGGED,
        _shape(
            False, Tick.AFTER_BODY, Saving.NONE, trailing=True, ended=Undated.OWN_LAST
        ),
        _shape(
            True,
            Tick.AFTER_BODY,
            Saving.RECORDED,
            trailing=False,
            ended=Undated.OWN_LAST,
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
pass, or none; a value in the pass or after the tick; a save recorded or not),
where the worker stops in its last one (mid-pass, mid-save, before its tick,
or after it), and how it ends (killed, or its own stopped: completed, preempted
or errored, with each final_step convention; or a clean exit whose loop
discards). Run with ``claims=1, steps=2, stops=0``."""

END_TIMES = _space(
    origins=(Origins.FRESH,),
    loading=(False,),
    shapes=(
        UNLOGGED,
        _shape(False, Tick.NONE, Saving.IN_BODY, trailing=False, ended=Undated.NEXT),
        _shape(
            True,
            Tick.BEFORE_BODY,
            Saving.IN_BODY,
            trailing=False,
            ended=Undated.OWN_LAST,
        ),
        _shape(
            True, Tick.BEFORE_BODY, Saving.IN_BODY, trailing=False, ended=Undated.NEXT
        ),
    ),
    cuts=(Cut.BEFORE_TICK, Cut.DONE),
    endings=(PREEMPTED,),
)
"""One claim whose iterations no tick of their own dates: each ended with its
claim's newest record, or just before the log's next one, the end-time fork
(§7). Run with ``claims=1, steps=2, stops=0``."""

MID_SAVES = Space(
    origins=(Origins.NEWEST,),
    loading=(False,),
    clocks=(Clock.LOADED,),
    folds=(Fold.AT_CLAIM,),
    cut_saves=(CutSave.LANDED, CutSave.CLOSED, CutSave.SAVE_START),
    strays=(False,),
    strides=(1,),
    shapes=(MYCOOC,),
    mid_claim=(None,),
    mid_positions=(MidPosition.BEFORE_BODY,),
    cuts=(Cut.MID_BODY, Cut.MID_SAVE, Cut.AFTER_SAVE, Cut.DONE),
    endings=(KILL, PREEMPTED, DISCARDS),
    third_parties=(None,),
    successors=(Successor.OWN_PROCESS,),
    displaced=tuple(
        Displaced(by, Tail(value=False, completes=True, steps=(), end=KILL))
        for by in (Takeover(), Release(Outcome.LOST, Via.HELPER))
    ),
    before_run=(None,),
    launches=(LaunchShape.NONE,),
    stop_slots=(),
)
"""Two claims in mycooc's pass order, each loading the newest save, whose pass
is cut before its save, during it, after it landed (the fact ``cut_save``
decides whether that iteration finished), or after its close: by a kill, by
the worker's own exit (one that keeps the open iteration, or discards it), or
by a fence, a takeover's or a release's, after which the worker finishes the
pass and is killed. Run with ``claims=2, steps=1, stops=0``."""

RESUMES = Space(
    origins=(Origins.FRESH, Origins.SAVED, Origins.OUTSIDE),
    loading=(False, True),
    clocks=(Clock.LOADED,),
    folds=(Fold.AT_CLAIM,),
    cut_saves=(CutSave.LANDED,),
    strays=(False,),
    strides=(1,),
    shapes=(SAVES, UNLOGGED),
    mid_claim=(None,),
    mid_positions=(MidPosition.BEFORE_BODY,),
    cuts=(Cut.BEFORE_TICK,),
    endings=(KILL,),
    third_parties=(None,),
    successors=(Successor.OWN_PROCESS,),
    displaced=(),
    before_run=(None,),
    launches=(LaunchShape.NONE,),
    stop_slots=(),
)
"""Two claims, each killed after closing and saving its last pass, before its
tick: what the second loaded (nothing, each save, or a checkpoint from
outside), with loading values and unlogged iterations (a resume gap, a
run-on). Run with ``claims=2, steps=2, stops=0``."""

LOADING = replace(
    RESUMES,
    loading=(True,),
    clocks=(Clock.LOADED, Clock.ZERO),
    cuts=(Cut.MID_BODY, Cut.BEFORE_TICK),
    endings=(KILL, PREEMPTED),
)
"""Two claims that each write a value before their first iteration, its clock
reading the loaded step or 0 (mycooc's counter), then stop mid-pass or before
a tick. Run with ``claims=2, steps=1, stops=0``."""

THIRD_PARTIES = Space(
    origins=(Origins.FRESH, Origins.SAVED),
    loading=(False,),
    clocks=(Clock.LOADED,),
    folds=(Fold.AT_CLAIM,),
    cut_saves=(CutSave.LANDED,),
    strays=(False,),
    strides=(1,),
    shapes=(SAVES,),
    mid_claim=(None,),
    mid_positions=(MidPosition.BEFORE_BODY,),
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
    folds=(Fold.AT_CLAIM,),
    cut_saves=(CutSave.LANDED,),
    strays=(False,),
    strides=(1,),
    shapes=(
        SAVES,
        _shape(
            False,
            Tick.AFTER_BODY,
            Saving.IN_BODY,
            trailing=False,
            ended=Undated.OWN_LAST,
        ),
    ),
    mid_claim=(None, _RELEASE, _NOTE),
    mid_positions=(MidPosition.BEFORE_BODY, MidPosition.BEFORE_ITERATION),
    cuts=(Cut.MID_BODY, Cut.BEFORE_TICK, Cut.DONE),
    endings=(KILL, COMPLETES, PREEMPTED),
    third_parties=(None,),
    successors=(Successor.OWN_PROCESS,),
    displaced=(),
    before_run=(None,),
    launches=(LaunchShape.NONE,),
    stop_slots=(),
)
"""One claim that a third party's stop names while its worker lives, in its last
iteration, before it begins or before its pass: a release (mistaken, which
fences it) or a note; the worker then finishes that iteration and ends.
``BEATEN_PAST``'s and ``LATER_COMPLETED``'s shapes. Run with ``claims=1,
steps=2, stops=0``."""

DISPLACEMENT = Space(
    origins=(Origins.FRESH, Origins.SAVED, Origins.OUTSIDE),
    loading=(False,),
    clocks=(Clock.LOADED,),
    folds=(Fold.AT_CLAIM,),
    cut_saves=(CutSave.LANDED,),
    strays=(False,),
    strides=(1,),
    shapes=(SAVES,),
    mid_claim=(None,),
    mid_positions=(MidPosition.BEFORE_BODY,),
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
runs on and exits, after its successor claimed; the successor loads nothing,
a save made before its claim, or a checkpoint from outside. Run with
``claims=2, steps=2, stops=0``."""

DISPLACED_SAVES = Space(
    origins=(Origins.NEWEST, Origins.SAVED),
    loading=(False,),
    clocks=(Clock.LOADED,),
    folds=(Fold.AT_CLAIM,),
    cut_saves=(CutSave.LANDED,),
    strays=(False, True),
    strides=(1,),
    shapes=(SAVES,),
    mid_claim=(None,),
    mid_positions=(MidPosition.BEFORE_BODY,),
    cuts=(Cut.DONE,),
    endings=(KILL,),
    third_parties=(None,),
    successors=(Successor.OWN_PROCESS,),
    displaced=(
        Displaced(
            Takeover(), Tail(value=True, completes=False, steps=(SAVES,), end=KILL)
        ),
    ),
    before_run=(None,),
    launches=(LaunchShape.NONE,),
    stop_slots=(),
)
"""Three claims that each load the newest save, or any save, where a displaced
worker keeps writing and saving after its successor claimed: a save that names
no node (§4's fence), which a later claim may load; and stray values a process
writes before it claims. Run with ``claims=3, steps=1, stops=0``."""

STOPS_AND_REAPS = Space(
    origins=(Origins.FRESH, Origins.SAVED),
    loading=(False,),
    clocks=(Clock.LOADED,),
    folds=(Fold.AT_CLAIM,),
    cut_saves=(CutSave.LANDED,),
    strays=(False,),
    strides=(1,),
    shapes=(SAVES,),
    mid_claim=(None,),
    mid_positions=(MidPosition.BEFORE_BODY,),
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

TICKS_ONLY = Stops(
    False, None, FinalStep.YIELDED, Drain.TICKS, False, saves=False, teardown=False
)
"""A preempted exit whose ``honored`` lists only the stops its ticks drained."""

UNANSWERED_STOPS = replace(
    STOPS_AND_REAPS,
    folds=(Fold.AT_CLAIM, Fold.AT_DRAIN),
    endings=(KILL, PREEMPTED, TICKS_ONLY),
    launches=(LaunchShape.NONE,),
    stop_slots=(StopSlot.BEFORE_CLAIM,),
)
"""A stop before either of two claims, neither of which ticks in its last pass,
by writers that fold at their claim or at each drain, the stops a third party
clears, and an exit that lists only what its ticks drained: it omits a stop its
native exit drains and honours (E2's first stated cost), which the next claim,
whose 0.3.0 exit honours it, then no longer honours. Run with ``claims=2,
steps=1, stops=1``."""

WITHDRAWALS = Space(
    origins=(Origins.FRESH,),
    loading=(False,),
    clocks=(Clock.LOADED,),
    folds=(Fold.AT_CLAIM, Fold.AT_DRAIN),
    cut_saves=(CutSave.LANDED,),
    strays=(False,),
    strides=(1,),
    shapes=(
        SAVES,
        _shape(
            True,
            Tick.BEFORE_BODY,
            Saving.IN_BODY,
            trailing=False,
            ended=Undated.OWN_LAST,
        ),
    ),
    mid_claim=(
        None,
        ThirdParty(Report(None), None, ThirdPartyFinal.NULL, Honors.UNDISCHARGED),
    ),
    mid_positions=(MidPosition.BEFORE_BODY, MidPosition.BEFORE_ITERATION),
    cuts=(Cut.DONE,),
    endings=(COMPLETES, PREEMPTED),
    third_parties=(None,),
    successors=(Successor.OWN_PROCESS,),
    displaced=(),
    before_run=(None,),
    launches=(LaunchShape.NONE,),
    stop_slots=(StopSlot.IN_PASS, StopSlot.MID_CLAIM),
)
"""One claim, a stop inside its first pass or before its last iteration, and a
third party that clears it in that iteration, before it begins or after a
tick-first loop's tick, by a writer that applies the clearing at its claim or
at its next drain. The withdrawal stands at its answer (E2), and the native
exit honours what the native drains read: a stop the commit's drain read
before the clearing, which the 0.3.0 exit of an at-drain writer omits (E2's
second stated cost, CLEARED_AFTER_THE_END), or none that an at-claim writer
took in after the answer (the third). Where the stop lands against an
iteration end no record dates, the commit's drain reads it or not: the
end-place fork. Run with ``claims=1, steps=2, stops=1``."""


# ----- SLOT: what each claim loaded, under a constraint across claims ----------------------


def slot(*, beats: int, run_on: int) -> Iterator[History]:
    """SLOT's shape (the critique's P1): three claims, where what a claim may
    have loaded depends on what the claims between it and the saver saved.

    - C1 beats 0 to ``beats - 1``, saving any subset of those steps, and is
      killed.
    - C2 loads any save that exists at its claim, beats its next step, saving it
      or not, then runs on unlogged for 0 to ``run_on`` steps, saving the last,
      and is killed.
    - C3 loads any save that exists at its claim, beats two steps, and
      completes.

    ``history.loads_newest`` says which satisfy ``load=newest``: one checkpoint
    slot that every save overwrites. The critique's SLOT history is the one with
    ``beats=4``: C1 saved 0 and 1, C2 resumed from 1, beat 2 and ran on to 3, and
    C3 resumed from C2's 3."""
    for kept in itertools.product((False, True), repeat=beats):
        first: list[Event] = [_slot_claim(0, Fresh())]
        for s, keep in enumerate(kept):
            first += iteration(0, Step(s, SAVES if keep else STEPS), cut=Cut.DONE)
        first.append(Halt(0, Killed(9)))
        for r2, c2 in _resumes(first):
            for keep2, n in itertools.product((False, True), range(run_on + 1)):
                second: list[Event] = [_slot_claim(1, c2)]
                shape2 = SAVES if keep2 else STEPS
                second += iteration(1, Step(r2 + 1, shape2), cut=Cut.DONE)
                for k in range(n):
                    shape = UNLOGGED if k == n - 1 else _QUIET
                    second += iteration(1, Step(r2 + 2 + k, shape), cut=Cut.DONE)
                second.append(Halt(1, Killed(9)))
                for r3, c3 in _resumes(first + second):
                    third: list[Event] = [_slot_claim(2, c3)]
                    third += iteration(2, Step(r3 + 1, STEPS), cut=Cut.DONE)
                    third += iteration(2, Step(r3 + 2, STEPS), cut=Cut.DONE)
                    third += ending(2, COMPLETES)
                    history = History(tuple(first + second + third))
                    if conforms(history):
                        yield history


_QUIET = _shape(False, Tick.NONE, Saving.NONE, trailing=False, ended=Undated.OWN_LAST)
"""An iteration that writes nothing and saves nothing: a run-on's inner step."""


def _slot_claim(c: int, origin: Origin) -> Claim:
    return Claim(
        c,
        own_process(c),
        Gate.FREE,
        origin,
        None,
        Clock.LOADED,
        Fold.AT_CLAIM,
        CutSave.LANDED,
    )


def _resumes(events: list[Event]) -> list[tuple[int, Origin]]:
    """Each save that exists after ``events``, with its saved state's step."""
    return [(step, FromSave(save)) for save, step in loadable(tuple(events)).items()]
