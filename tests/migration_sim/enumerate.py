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
    Abandons,
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
    TICKED,
    Beat,
    Begin,
    Born,
    Claim,
    Clock,
    CutSave,
    Drain,
    Emit,
    Ended,
    Undated,
    Event,
    Exit,
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
    Rewind,
    Save,
    SaveRef,
    Stamp,
    StopRequest,
    Stray,
    ThirdPartyFinal,
    ThirdPartyStop,
    ThirdPartyValue,
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


class Strays(Enum):
    """A value a writer that holds no claim writes, just before a claim's
    ``started``: a process before it claims, a claim-race loser, or another
    program (mycooc's orchestrator writes ``completion_reason``)."""

    NONE = "none"
    STEPPED = "stepped"  # at a step just below the claim's floor
    STEPLESS = "stepless"  # with a null step


class Successor(Enum):
    """Where the next claim runs, after this one's worker exits."""

    OWN_PROCESS = "own-process"
    SAME_PROCESS = "same-process"  # this one's: two Workers run in turn by one process


class Naming(Enum):
    """Whether a third party's ``stopped`` names the claim the space places it at."""

    CLAIM = "claim"
    NONE = "none"  # a claimless note or reap (mycooc's W4: 72 in the store copy)


@dataclass(frozen=True)
class ThirdParty:
    """A third party's ``stopped``, as the space places it."""

    intent: Intent
    error: str | None
    final_step: ThirdPartyFinal
    honors: Honors
    naming: Naming


@dataclass(frozen=True)
class Interjects:
    """A third party writes a register on the live claim's log (``ThirdPartyValue``):
    at the label of the claim's last iteration (``stepped``), where it reads as
    the worker's own value, or with a null step."""

    stepped: bool


MidClaim: TypeAlias = ThirdParty | Interjects


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
    strays: tuple[Strays, ...]
    pre_saves: tuple[bool, ...]  # a save of the loaded state before the first
    # iteration (save-at=before-loop)
    strides: tuple[int, ...]
    shapes: tuple[Shape, ...]
    mid_claim: tuple[MidClaim | None, ...]
    mid_positions: tuple[MidPosition, ...]
    cuts: tuple[Cut, ...]
    endings: tuple[Ending, ...]
    third_parties: tuple[ThirdParty | None, ...]
    successors: tuple[Successor, ...]
    displaced: tuple[Displaced, ...]
    before_run: tuple[ThirdParty | None, ...]
    launches: tuple[LaunchShape, ...]
    stop_slots: tuple[StopSlot, ...]
    born: tuple[Born, ...]  # the format each log was born in


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
    mid: MidClaim | None
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
                for born in space.born:
                    history = History(tuple(events), born)
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
                for cut_save, stray, pre_save in itertools.product(
                    _cut_saves(its.steps, space.cut_saves),
                    space.strays,
                    space.pre_saves,
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
                        pre_save,
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
                    # an abandoned worker's claim is held and its process lives:
                    # only a takeover's gate passes it (takeover=dead)
                    gate = Gate.TAKEOVER if isinstance(end, Abandons) else Gate.FREE
                    into = _Entry(own_process(c + 1), gate, [])
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
    stray: Strays,
    stray_step: int,
    pre_save: bool,
) -> list[_Item]:
    """Claim ``c``'s start: its launch, a stray value its process writes before
    it claims, its claim, the previous claim's displaced tail, its loading value,
    and a save of the state it loaded."""
    items: list[_Item] = [_Slot(StopSlot.BEFORE_CLAIM)]
    if launch_id is not None:
        launched = own_process(c) if launch is not LaunchShape.WRAPPED else _wrapper(c)
        items.append(Launched(launch_id, launched))
    if stray is Strays.STEPPED:
        items.append(Stray(VALUE, stray_step))
    elif stray is Strays.STEPLESS:
        items.append(Stray(VALUE, None))
    elif stray is not Strays.NONE:
        assert_never(stray)
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
        items.append(Emit(c, VALUE, Stamp.METRIC))
    if pre_save:
        items.append(Save(c))
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
        mid = _mid_claim(c, it.step, its.mid)
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
    events: list[Event] = [Emit(c, VALUE, Stamp.METRIC)] if tail.value else []
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
    how = halt_of(end)
    return [] if how is None else [Reaped(launch_id, how)]


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
    its: list[Step], mid: MidClaim | None, positions: tuple[MidPosition, ...]
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


def _mid_claim(c: int, label: int, mid: MidClaim | None) -> list[Event]:
    """What a third party writes inside claim ``c``'s last iteration, labelled
    ``label``."""
    if mid is None:
        return []
    if isinstance(mid, ThirdParty):
        return [_third_party(c, mid)]
    if isinstance(mid, Interjects):
        return [ThirdPartyValue(VALUE, label if mid.stepped else None)]
    assert_never(mid)


def _third_party(at: int | None, third: ThirdParty) -> ThirdPartyStop:
    """``third``, placed at claim ``at`` (none before the run)."""
    names = at if third.naming is Naming.CLAIM else None
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
    Release(Outcome.LOST, Via.HELPER),
    None,
    ThirdPartyFinal.NULL,
    Honors.NONE,
    Naming.CLAIM,
)
_NOTE = ThirdParty(
    Report(None), "note", ThirdPartyFinal.PROGRESS, Honors.NONE, Naming.CLAIM
)


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
        strays=(Strays.NONE,),
        pre_saves=(False,),
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
        born=(Born.V0_3_0,),
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
    strays=(Strays.NONE,),
    pre_saves=(False,),
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
    born=(Born.V0_3_0,),
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
    strays=(Strays.NONE,),
    pre_saves=(False,),
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
    born=(Born.V0_3_0,),
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
    strays=(Strays.NONE,),
    pre_saves=(False,),
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
            Naming.CLAIM,
        ),
        _NOTE,
        ThirdParty(
            Report(Killed(15)),
            None,
            ThirdPartyFinal.PROGRESS,
            Honors.NONE,
            Naming.CLAIM,
        ),
    ),
    successors=(Successor.OWN_PROCESS,),
    displaced=(),
    before_run=(None,),
    launches=(LaunchShape.NONE,),
    stop_slots=(),
    born=(Born.V0_3_0,),
)
"""Third parties' stops after a claim's end (a release through the helper or
written directly, a note, a non-claimant's reap); a worker that saves after its
own exit and writes a teardown value, a synthetic writer (no mycooc version
saves its resume state after its exit: task-19-report.md, P-a). Run with
``claims=2, steps=1, stops=0``."""

RELEASED_LIVE = Space(
    origins=(Origins.FRESH,),
    loading=(False,),
    clocks=(Clock.LOADED,),
    folds=(Fold.AT_CLAIM,),
    cut_saves=(CutSave.LANDED,),
    strays=(Strays.NONE,),
    pre_saves=(False,),
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
    mid_claim=(
        None,
        _RELEASE,
        _NOTE,
        Interjects(stepped=True),
        Interjects(stepped=False),
    ),
    mid_positions=(MidPosition.BEFORE_BODY, MidPosition.BEFORE_ITERATION),
    cuts=(Cut.MID_BODY, Cut.BEFORE_TICK, Cut.DONE),
    endings=(KILL, COMPLETES, PREEMPTED),
    third_parties=(None,),
    successors=(Successor.OWN_PROCESS,),
    displaced=(),
    before_run=(None,),
    launches=(LaunchShape.NONE,),
    stop_slots=(),
    born=(Born.V0_3_0,),
)
"""One claim that a third party acts on while its worker lives, in its last
iteration, before it begins or before its pass: a stop naming it, a release
(mistaken, which fences it) or a note; or a register it writes on the claim's
log, at the iteration's label or stepless, which no node holds and which a
worker's own value at that label writes alike. The worker then finishes that
iteration and ends. ``BEATEN_PAST``'s and ``LATER_COMPLETED``'s shapes. Run with
``claims=1, steps=2, stops=0``."""

DISPLACEMENT = Space(
    origins=(Origins.FRESH, Origins.SAVED, Origins.OUTSIDE),
    loading=(False,),
    clocks=(Clock.LOADED,),
    folds=(Fold.AT_CLAIM,),
    cut_saves=(CutSave.LANDED,),
    strays=(Strays.NONE,),
    pre_saves=(False,),
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
    born=(Born.V0_3_0,),
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
    strays=(Strays.NONE, Strays.STEPPED, Strays.STEPLESS),
    pre_saves=(False,),
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
    born=(Born.V0_3_0,),
)
"""Three claims that each load the newest save, or any save, where a displaced
worker keeps writing and saving after its successor claimed: a save that names
no node (§4's fence), which a later claim may load; and stray values, with a
step or none, a process
writes before it claims. Run with ``claims=3, steps=1, stops=0``."""

STOPS_AND_REAPS = Space(
    origins=(Origins.FRESH, Origins.SAVED),
    loading=(False,),
    clocks=(Clock.LOADED,),
    folds=(Fold.AT_CLAIM,),
    cut_saves=(CutSave.LANDED,),
    strays=(Strays.NONE,),
    pre_saves=(False,),
    strides=(1,),
    shapes=(SAVES,),
    mid_claim=(None,),
    mid_positions=(MidPosition.BEFORE_BODY,),
    cuts=(Cut.MID_BODY,),
    endings=(KILL, PREEMPTED),
    third_parties=(
        None,
        ThirdParty(
            Report(None),
            None,
            ThirdPartyFinal.NULL,
            Honors.UNDISCHARGED,
            Naming.CLAIM,
        ),
    ),
    successors=(Successor.OWN_PROCESS,),
    displaced=(),
    before_run=(None,),
    launches=(LaunchShape.NONE, LaunchShape.REAPED, LaunchShape.WRAPPED),
    stop_slots=(StopSlot.AFTER_CLAIM,),
    born=(Born.V0_3_0,),
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
native exit drains and honours (E2's case 1), which the next claim, whose
0.3.0 exit honours it, then no longer honours (case 4). Run with ``claims=2,
steps=1, stops=1``."""

WITHDRAWALS = Space(
    origins=(Origins.FRESH,),
    loading=(False,),
    clocks=(Clock.LOADED,),
    folds=(Fold.AT_CLAIM, Fold.AT_DRAIN),
    cut_saves=(CutSave.LANDED,),
    strays=(Strays.NONE,),
    pre_saves=(False,),
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
        ThirdParty(
            Report(None),
            None,
            ThirdPartyFinal.NULL,
            Honors.UNDISCHARGED,
            Naming.CLAIM,
        ),
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
    born=(Born.V0_3_0,),
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


COUNTED = Stops(
    False, None, FinalStep.COUNTER, Drain.EXIT, False, saves=False, teardown=False
)
"""A preempted exit whose ``final_step`` is its own counter (mycooc's W1)."""

SAVE_POINTS = Space(
    origins=(Origins.NEWEST,),
    loading=(True,),
    clocks=(Clock.LOADED, Clock.ZERO),
    folds=(Fold.AT_CLAIM,),
    cut_saves=(CutSave.LANDED,),
    strays=(Strays.NONE,),
    pre_saves=(False, True),
    strides=(1, 2),
    shapes=(
        _shape(
            True,
            Tick.AFTER_BODY,
            Saving.AFTER_TICK,
            trailing=False,
            ended=Undated.OWN_LAST,
        ),
        _shape(
            True,
            Tick.AFTER_BODY,
            Saving.PARTIAL,
            trailing=False,
            ended=Undated.OWN_LAST,
        ),
    ),
    mid_claim=(None,),
    mid_positions=(MidPosition.BEFORE_BODY,),
    cuts=(Cut.MID_BODY, Cut.DONE),
    endings=(KILL, COUNTED),
    third_parties=(None,),
    successors=(Successor.OWN_PROCESS,),
    displaced=(),
    before_run=(None,),
    launches=(LaunchShape.NONE,),
    stop_slots=(),
    born=(Born.V0_3_0,),
)
"""Two claims, each loading the newest save, with a loading value at a clock
that reads the loaded step or 0, that save: before their first iteration (the
state they loaded, ``save-at=before-loop``), after an iteration's tick
(``between``), or of an iteration's partial state (``partial``, which names no
node); and that end killed, or by an exit whose ``final_step`` is its counter
(``final-step=counter``); their labels step by one or skip (``stride=skip``).
Run with ``claims=2, steps=1, stops=0``."""

_UNNAMED_NOTE = ThirdParty(
    Report(None), None, ThirdPartyFinal.NULL, Honors.UNDISCHARGED, Naming.NONE
)
_UNNAMED_REAP = ThirdParty(
    Report(Killed(15)), None, ThirdPartyFinal.PROGRESS, Honors.NONE, Naming.NONE
)

THIRD_PARTY_ACTS = Space(
    origins=(Origins.NEWEST,),
    loading=(False,),
    clocks=(Clock.LOADED,),
    folds=(Fold.AT_CLAIM,),
    cut_saves=(CutSave.LANDED,),
    strays=(Strays.NONE,),
    pre_saves=(False,),
    strides=(1,),
    shapes=(
        MYCOOC,
        _shape(
            True, Tick.AFTER_SAVE, Saving.IN_PASS, trailing=False, ended=Undated.NEXT
        ),
    ),
    mid_claim=(
        None,
        ThirdParty(
            Report(Killed(15)),
            None,
            ThirdPartyFinal.PROGRESS,
            Honors.NONE,
            Naming.CLAIM,
        ),
    ),
    mid_positions=(MidPosition.BEFORE_BODY,),
    cuts=(Cut.DONE,),
    endings=(KILL, PREEMPTED, Abandons()),
    third_parties=(None, _UNNAMED_REAP),
    successors=(Successor.OWN_PROCESS,),
    displaced=(),
    before_run=(None, _UNNAMED_NOTE),
    launches=(LaunchShape.NONE,),
    stop_slots=(),
    born=(Born.V0_3_0,),
)
"""Two claims in mycooc's pass order, its ``status`` a stepped register, each
pass ending with its claim's newest record or just before the log's next (the
end-time fork), and third parties' acts: a reap
reported on the live claim (``acts`` (live, reap)), a claimless note before the
run and a claimless reap after a claim's end (``unnamed``); and a worker
abandoned in its live process, which the next claim's gate takes over
(``takeover=dead``). Run with ``claims=2, steps=1, stops=0``."""

POSITIONAL = Space(
    origins=(Origins.FRESH, Origins.SAVED),
    loading=(False,),
    clocks=(Clock.LOADED,),
    folds=(Fold.AT_CLAIM,),
    cut_saves=(CutSave.LANDED,),
    strays=(Strays.NONE,),
    pre_saves=(False,),
    strides=(1,),
    shapes=(
        _shape(
            True,
            Tick.BEFORE_BODY,
            Saving.IN_BODY,
            trailing=False,
            ended=Undated.OWN_LAST,
        ),
    ),
    mid_claim=(None, _NOTE),
    mid_positions=(MidPosition.BEFORE_BODY,),
    cuts=(Cut.BEFORE_TICK,),
    endings=(KILL, PREEMPTED),
    third_parties=(None, _RELEASE),
    successors=(Successor.OWN_PROCESS,),
    displaced=tuple(
        Displaced(by, Tail(value=False, completes=True, steps=(), end=PREEMPTED))
        for by in (Takeover(), Release(Outcome.LOST, Via.HELPER))
    ),
    before_run=(None,),
    launches=(LaunchShape.NONE,),
    stop_slots=(StopSlot.MID_CLAIM, StopSlot.AFTER_CLAIM),
    born=(Born.V0_2_0,),
)
"""Logs born in format 0.2.0, whose ``honored`` lists and heartbeat and
``stopped`` ``claim_seq`` the 0.2.0 -> 0.3.0 step filled positionally: two
claims of a tick-first loop, a stop, a note after a claim's tick, a release
after a claim's end, and a worker displaced by a takeover or a release that
finishes its iteration and exits after its successor's claim, so its records
name that claim. A note after a tick that took a stop in lists it
positionally, so the exit's list omits what its native exit honours: E2's
fifth case (``INFERRED_INPUT_LIST``). Run with ``claims=2, steps=1,
stops=1``."""


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
                    history = History(tuple(first + second + third), Born.V0_3_0)
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


# ----- REWINDS: a worker that reloads a save between its iterations ------------------------


def rewinds(*, passes: int) -> Iterator[History]:
    """A worker that reloads state between iterations (``Rewind``), which writes no
    record:

    - C1 runs ``passes`` iterations, saving any subset of them, then rewinds to
      the root or to any save it made, or does not, runs the next iteration above
      that state, saving it, and is killed or exits. A rewind to its newest save
      at its last iteration writes the log that no rewind writes: the rewind is
      an event no record shows. The next iteration is the one after the state,
      or, after a rewind below its last, the one after its last (a skip): every
      heartbeat stays above the ones before, so M9 refuses none, and the rewind
      shows only in what the iteration's commit continues. After a rewind to a
      save it may save the reloaded state before that iteration, which then
      saves or not: the reloaded state is then the newest save;
    - C2 loads any save that exists at its claim, runs one iteration, and
      completes.

    mycooc's code can revert its state inside a pass (training.py:1099-1110,
    :1377-1381@bb6f9e8c), which the model does not express; the store copy never
    shows it."""
    for kept in itertools.product((False, True), repeat=passes):
        first: list[Event] = [_slot_claim(0, Fresh())]
        for s, keep in enumerate(kept):
            first += iteration(0, Step(s, SAVES if keep else STEPS), cut=Cut.DONE)
        # the step rewound to, the events, and whether the next iteration may not save
        targets: list[tuple[int, list[Event], bool]] = [(passes - 1, [], False)]
        targets.append((-1, [Rewind(0, None)], False))
        for save, step in loadable(tuple(first)).items():
            targets.append((step, [Rewind(0, save)], False))
            targets.append((step, [Rewind(0, save), Save(0)], True))
        ends: tuple[Ending, ...] = (KILL, PREEMPTED)
        for (step, rewind, resaved), end in itertools.product(targets, ends):
            for label, saves in itertools.product(
                sorted({step + 1, passes}), (SAVES, STEPS) if resaved else (SAVES,)
            ):
                rewound = first + rewind
                rewound += iteration(0, Step(label, saves), cut=Cut.DONE)
                rewound += ending(0, end)
                for r2, c2 in _resumes(rewound):
                    second: list[Event] = [_slot_claim(1, c2)]
                    second += iteration(1, Step(r2 + 1, STEPS), cut=Cut.DONE)
                    second += ending(1, COMPLETES)
                    history = History(tuple(rewound + second), Born.V0_3_0)
                    if conforms(history):
                        yield history


# ----- ENDED: a third party's stopped on a claim something else ended first ---------


def ended_stops() -> Iterator[History]:
    """A third party's ``stopped`` naming a claim that something other than its
    worker's own ``stopped`` ended first (``acts``' ``ended`` state): a release
    before it, or a later claim. mycooc's backstop since 88425fa8 reports a reap
    on the previous claim after a later one began, and its repair script writes a
    note after a malformed release of the same claim (task-19-report.md):

    - C0 runs one pass, saving it, with a stop request before its end or none,
      and is killed, or exits preempted;
    - optionally a third party releases C0 after its death (``lost``, through the
      helper);
    - C1 loads the newest save, runs one pass, and is killed or completes;
    - a third party's ``stopped`` names C0: after the release (before C1), after
      C1's ``started``, or after C1's end; it is a note, a reap, or a release
      (with or without a cause, through the helper or directly), and honours no
      stop or every undischarged one.
    """
    acts: tuple[Intent, ...] = (
        Report(None),
        Report(Killed(15)),
        Release(Outcome.LOST, Via.HELPER),
        Release(Outcome.ERRORED, Via.DIRECT),
    )
    firsts: tuple[Ending, ...] = (KILL, PREEMPTED)
    lasts: tuple[Ending, ...] = (KILL, COMPLETES)
    for stop, first_end, released, place, act, honors, last_end in itertools.product(
        (False, True),
        firsts,
        (False, True),
        ("released", "claimed", "ended"),
        acts,
        (Honors.NONE, Honors.UNDISCHARGED),
        lasts,
    ):
        if place == "released" and not released:
            continue
        if released and isinstance(first_end, Stops):
            continue  # the release of an exited claim is third-party-stop's own family
        events: list[Event] = [_slot_claim(0, Fresh())]
        events += iteration(0, Step(0, SAVES), cut=Cut.DONE)
        if stop:
            events.append(StopRequest("x0"))
        events += ending(0, first_end)
        later = ThirdPartyStop(
            0,
            act,
            None if isinstance(act, Report) else "boom",
            ThirdPartyFinal.NULL,
            honors,
        )
        if released:
            events.append(
                ThirdPartyStop(
                    0,
                    Release(Outcome.LOST, Via.HELPER),
                    None,
                    ThirdPartyFinal.NULL,
                    Honors.NONE,
                )
            )
        if place == "released":
            events.append(later)
        for origin in _resumes(events) or [(-1, Fresh())]:
            tail: list[Event] = [_slot_claim(1, origin[1])]
            if place == "claimed":
                tail.append(later)
            tail += iteration(1, Step(origin[0] + 1, SAVES), cut=Cut.DONE)
            tail += ending(1, last_end)
            if place == "ended":
                tail.append(later)
            history = History(tuple(events + tail), Born.V0_3_0)
            if conforms(history):
                yield history


# ----- PLACED DRAINS: a silent close among stops a stopped then withdraws ------------


def placed_drains() -> Iterator[History]:
    """A pass that closes silently among stops a ``stopped`` then withdraws (§7, E2; the
    Task 20 review's Critical 1): a commit's native drain reads where it stands, so
    the exit honours what was posted before it and not yet withdrawn.

    - One worker: a pass with a value, two stops and a third party's note honouring
      every undischarged stop or none, with its silent close before, between or after
      the stops, or after the note; and its exit, which drains or not, under either
      fold.
    - Two workers: claim 0 is released while it lives; claim 1 runs a pass, a stop
      comes, and claim 1's pass closes silently before or after claim 0's own
      ``stopped``, which withdraws the stop; claim 1 then exits, draining or not.
    """
    for at, honors, drain, fold, ended in itertools.product(
        (0, 1, 2, 3),
        (Honors.UNDISCHARGED, Honors.NONE),
        (Drain.EXIT, Drain.TICKS),
        (Fold.AT_CLAIM, Fold.AT_DRAIN),
        (Undated.OWN_LAST, Undated.NEXT),
    ):
        among: list[Event] = [
            StopRequest("a"),
            StopRequest("b"),
            ThirdPartyStop(0, Report(None), None, ThirdPartyFinal.NULL, honors),
        ]
        among.insert(at, Finish(0, False, ended))
        events: list[Event] = [
            _placed_claim(0, fold),
            Begin(0, 0),
            Emit(0, VALUE, Stamp.METRIC),
            *among,
            Exit(0, False, None, FinalStep.FINISHED, drain, False),
            Halt(0, Exited(0)),
        ]
        history = History(tuple(events), Born.V0_3_0)
        if conforms(history):
            yield history
    for first, drain, fold in itertools.product(
        (True, False), (Drain.EXIT, Drain.TICKS), (Fold.AT_CLAIM, Fold.AT_DRAIN)
    ):
        close: list[Event] = [Finish(1, False, Undated.OWN_LAST)]
        fenced: list[Event] = [
            Exit(0, False, None, FinalStep.YIELDED, Drain.EXIT, False),
            Halt(0, Exited(0)),
        ]
        events = [
            _placed_claim(0, fold),
            Begin(0, 0),
            Emit(0, VALUE, Stamp.METRIC),
            ThirdPartyStop(
                0,
                Release(Outcome.LOST, Via.HELPER),
                None,
                ThirdPartyFinal.NULL,
                Honors.NONE,
            ),
            _placed_claim(1, fold),
            Begin(1, 0),
            Emit(1, VALUE, Stamp.METRIC),
            StopRequest("a"),
            *((close + fenced) if first else (fenced + close)),
            Exit(1, False, None, FinalStep.FINISHED, drain, False),
            Halt(1, Exited(0)),
        ]
        history = History(tuple(events), Born.V0_3_0)
        if conforms(history):
            yield history


def _placed_claim(c: int, fold: Fold) -> Claim:
    return Claim(
        c,
        own_process(c),
        Gate.FREE,
        Fresh(),
        None,
        Clock.LOADED,
        fold,
        CutSave.CLOSED,
    )


# ----- DATED CLOSES: what comes between a close and its worker's tick ---------------


def dated_closes() -> Iterator[History]:
    """A pass's close and its worker's tick, with nothing, a save of the worker's, a
    stop request, or both between them (§7, "The end-time fork"; the Task 20 review's
    Critical 2): the tick dates the close only where no act of the worker's came
    between; another writer's record may. The close is dated by the tick, by the
    claim's newest record, or by the next record, as each history allows; one pass or
    two, the second ticked."""
    betweens: tuple[tuple[Event, ...], ...] = (
        (),
        (Save(0),),
        (StopRequest("x"),),
        (Save(0), StopRequest("x")),
        (StopRequest("x"), Save(0)),
    )
    ends: tuple[Ended, ...] = (TICKED, Undated.OWN_LAST, Undated.NEXT)
    for passes, ended, between in itertools.product((1, 2), ends, betweens):
        events: list[Event] = [_slot_claim(0, Fresh())]
        events += [Begin(0, 0), Emit(0, VALUE, Stamp.METRIC), Finish(0, False, ended)]
        events += [*between, Beat(0, Watermark.CONTROL)]
        if passes == 2:
            events += iteration(0, Step(1, STEPS), cut=Cut.DONE)
        events += ending(0, COMPLETES)
        history = History(tuple(events), Born.V0_3_0)
        if conforms(history):
            yield history


# ----- DEAD TAKEOVERS: a later claim in the process its halted worker ran in ---------


def dead_takeovers() -> Iterator[History]:
    """A claim whose worker halted while its process lived, and a later claim, through a
    takeover's gate, in that same process (``takeover=dead``): the gate judged the
    held claim dead, and the process running on is the later claim's. Or the worker
    still lived at the takeover and halted after it (``takeover=live``).

    - C0 runs one or two passes, saving any, and halts without a ``stopped`` of its
      own, before the takeover or after it;
    - C1 claims through the takeover in C0's process, loading nothing or any save C0
      made, runs one pass, and completes or is killed.
    """
    lasts: tuple[Ending, ...] = (KILL, COMPLETES)
    for kept, halted, last_end in itertools.product(
        itertools.chain.from_iterable(
            itertools.product((False, True), repeat=n) for n in (1, 2)
        ),
        (True, False),
        lasts,
    ):
        first: list[Event] = [_slot_claim(0, Fresh())]
        for s, keep in enumerate(kept):
            first += iteration(0, Step(s, SAVES if keep else STEPS), cut=Cut.DONE)
        if halted:
            first.append(Halt(0, None))
        for step, origin in [(-1, Fresh()), *_resumes(first)]:
            second: list[Event] = [
                Claim(
                    1,
                    own_process(0),
                    Gate.TAKEOVER,
                    origin,
                    None,
                    Clock.LOADED,
                    Fold.AT_CLAIM,
                    CutSave.LANDED,
                )
            ]
            second += iteration(1, Step(step + 1, STEPS), cut=Cut.DONE)
            if not halted:
                second.append(Halt(0, None))
            second += ending(1, last_end)
            history = History(tuple(first + second), Born.V0_3_0)
            if conforms(history):
                yield history
