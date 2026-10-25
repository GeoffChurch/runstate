"""Bounded, streaming enumeration of histories.

``histories`` yields every conforming history its bounds and its ``Space``
allow, one at a time. A history is built claim by claim, in claim order:

1. **the claim**: its launch shape, its origin (a fresh start, every node the
   claims before it saved, or a checkpoint from outside at step 0 or at any of
   their nodes' steps), and whether it writes a value at its loaded step first;
2. **its iterations**: 0 to ``steps`` of them, each one of ``space.shapes``,
   labelled from the origin's floor upward by one of ``space.strides`` each;
3. **its end**: its last iteration stopped at one of ``space.cuts``, then one of
   ``space.endings``; or, for every claim but the last, one of
   ``space.displaced``: a release reaches its worker alive, the next claim
   starts, and the released worker's tail follows that claim's ``started``;
4. **after its end**: one of ``space.third_parties``, a third party's
   ``stopped`` naming it.

Before the run, one of ``space.before_run`` (a third party's ``stopped`` naming
no claim). Then 0 to ``stops`` stop requests go into the slots
``space.stop_slots`` names, in every combination.

Histories the walk refuses (``history.NotConforming``) are skipped, so the
generator yields only conforming ones. ``Space`` holds no defaults: a caller
names every choice it ranges over, as the presets at the end do.
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
)
from .history import (
    Claim,
    Emit,
    Event,
    Exited,
    FinalStep,
    FromOutside,
    FromSave,
    Fresh,
    History,
    Honors,
    Intent,
    Killed,
    Launched,
    NodeRef,
    Origin,
    Outcome,
    Reaped,
    Release,
    Report,
    StopRequest,
    ThirdPartyFinal,
    ThirdPartyStop,
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


@dataclass(frozen=True)
class ThirdParty:
    """A third party's ``stopped``, as the space places it."""

    intent: Intent
    error: str | None
    final_step: ThirdPartyFinal
    honors: Honors


@dataclass(frozen=True)
class Displaced:
    """A release that reaches a live worker: it writes a value (if ``value``)
    after its successor claims, then ends by ``end``."""

    outcome: Outcome
    value: bool
    end: Ending


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
    strides: tuple[int, ...]
    shapes: tuple[Shape, ...]
    cuts: tuple[Cut, ...]
    endings: tuple[Ending, ...]
    displaced: tuple[Displaced, ...]
    third_parties: tuple[ThirdParty | None, ...]
    before_run: tuple[ThirdParty | None, ...]
    launches: tuple[LaunchShape, ...]
    stop_slots: tuple[StopSlot, ...]


class _Run(NamedTuple):
    """A claim's iterations, and where its last one stops."""

    steps: list[Step]
    cut: Cut


@dataclass(frozen=True)
class _Slot:
    """A place a stop request may go; removed before the history is built."""

    kind: StopSlot


_Item: TypeAlias = Event | _Slot


def histories(
    *, claims: int, steps: int, stops: int, space: Space
) -> Iterator[History]:
    """Every conforming history of exactly ``claims`` claims, each running at
    most ``steps`` iterations, with at most ``stops`` stop requests."""
    for before in space.before_run:
        start: list[_Item] = []
        if before is not None:
            start.append(_third_party(None, before))
        for items in _claims(0, claims, steps, space, start, tail=[]):
            for events in _with_stops(items, stops, space.stop_slots):
                history = History(tuple(events))
                if conforms(history):
                    yield history


def _claims(
    c: int, claims: int, steps: int, space: Space, items: list[_Item], tail: list[Event]
) -> Iterator[list[_Item]]:
    """Extend ``items`` with claims ``c`` to ``claims - 1``. ``tail`` is a
    released worker's rest, to follow claim ``c``'s started."""
    if c == claims:
        yield items
        return
    saved = loadable(tuple(e for e in items if not isinstance(e, _Slot)))
    for launch, origin, loading in itertools.product(
        space.launches, _origins(saved, space.origins), space.loading
    ):
        for its in _iterations(origin, saved, steps, space):
            body = _claim_items(c, launch, origin, loading, its, tail)
            launch_id = None if launch is LaunchShape.NONE else f"L{c}"
            for end in space.endings:
                for third in space.third_parties:
                    done = body + ending(c, end) + _reap(launch, launch_id, end)
                    if third is not None:
                        done.append(_third_party(c, third))
                    done.append(_Slot(StopSlot.AFTER_CLAIM))
                    yield from _claims(c + 1, claims, steps, space, items + done, [])
            if c < claims - 1:
                for d in space.displaced:
                    release = ThirdPartyStop(
                        c, Release(d.outcome), None, ThirdPartyFinal.NULL, Honors.NONE
                    )
                    rest: list[Event] = (
                        [Emit(c, VALUE, stepless=False)] if d.value else []
                    )
                    rest += ending(c, d.end) + _reap(launch, launch_id, d.end)
                    yield from _claims(
                        c + 1, claims, steps, space, items + body + [release], rest
                    )


def _claim_items(
    c: int,
    launch: LaunchShape,
    origin: Origin,
    loading: bool,
    its: _Run,
    tail: list[Event],
) -> list[_Item]:
    """Claim ``c`` up to its end: its launch, its claim (then the previous
    claim's released tail), and its iterations, with their stop slots."""
    items: list[_Item] = [_Slot(StopSlot.BEFORE_CLAIM)]
    launch_id = None if launch is LaunchShape.NONE else f"L{c}"
    if launch_id is not None:
        items.append(Launched(launch_id, wrapper=launch is LaunchShape.WRAPPED))
    items.append(Claim(c, origin, launch_id))
    items += tail
    if loading:
        items.append(Emit(c, VALUE, stepless=False))
    for k, it in enumerate(its.steps):
        last = k == len(its.steps) - 1
        if last:
            items.append(_Slot(StopSlot.MID_CLAIM))
        items += iteration(c, it, cut=its.cut if last else Cut.DONE)
    return items


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


def _iterations(
    origin: Origin, saved: dict[NodeRef, int], steps: int, space: Space
) -> Iterator[_Run]:
    """Each run of 0 to ``steps`` iterations from the origin's floor, with each
    cut its last iteration can take."""
    floor = _floor(origin, saved)
    for n in range(steps + 1):
        for shapes in itertools.product(space.shapes, repeat=n):
            for strides in itertools.product(space.strides, repeat=n):
                labels = list(itertools.accumulate(strides, initial=floor - 1))[1:]
                its = [Step(s, shape) for s, shape in zip(labels, shapes)]
                for cut in _cuts(its, space.cuts):
                    yield _Run(its, cut)


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

STEPS = Shape(valued=True, tick=Tick.AFTER_BODY, save=Saving.NONE, trailing=False)
"""The reference ``steps()`` worker that emits each iteration and saves nothing."""

KILL = Dies(Killed(9))
COMPLETES = Stops(True, None, FinalStep.YIELDED, saves=False, teardown=False)
PREEMPTED = Stops(False, None, FinalStep.YIELDED, saves=False, teardown=False)
ERRORS = Stops(False, "boom", FinalStep.YIELDED, saves=False, teardown=False)

WORKER_SHAPES = Space(
    origins=(Origins.FRESH,),
    loading=(False, True),
    strides=(1,),
    shapes=(
        STEPS,
        Shape(valued=True, tick=Tick.BEFORE_BODY, save=Saving.NONE, trailing=False),
        Shape(valued=False, tick=Tick.NONE, save=Saving.IN_BODY, trailing=False),
        Shape(valued=False, tick=Tick.AFTER_BODY, save=Saving.NONE, trailing=True),
        Shape(valued=True, tick=Tick.AFTER_BODY, save=Saving.RECORDED, trailing=False),
    ),
    cuts=(Cut.MID_BODY, Cut.MID_SAVE, Cut.BEFORE_TICK, Cut.DONE),
    endings=(
        KILL,
        COMPLETES,
        PREEMPTED,
        ERRORS,
        Stops(False, None, FinalStep.FINISHED, saves=False, teardown=False),
        Stops(False, None, FinalStep.TICKED, saves=False, teardown=False),
    ),
    displaced=(),
    third_parties=(None,),
    before_run=(None,),
    launches=(LaunchShape.NONE,),
    stop_slots=(),
)
"""One claim, fresh: how an iteration is laid out (tick after or before the
body, or none; a value in the body or after the tick; a save recorded or not),
where the worker stops in its last one (mid-body, mid-save, before its tick, or
after it), and how it ends (killed, or its own stopped: completed, preempted or
errored, with each final_step convention). Run with ``claims=1, steps=2,
stops=0``."""

RESUMES = Space(
    origins=(Origins.FRESH, Origins.SAVED, Origins.OUTSIDE),
    loading=(False, True),
    strides=(1,),
    shapes=(
        Shape(valued=True, tick=Tick.AFTER_BODY, save=Saving.IN_BODY, trailing=False),
        Shape(valued=False, tick=Tick.NONE, save=Saving.IN_BODY, trailing=False),
    ),
    cuts=(Cut.BEFORE_TICK,),
    endings=(KILL,),
    displaced=(),
    third_parties=(None,),
    before_run=(None,),
    launches=(LaunchShape.NONE,),
    stop_slots=(),
)
"""Two claims, each killed after finishing and saving its last iteration, before
its tick: where the second resumed from (fresh, each save, outside), with
loading values and unlogged iterations (a resume gap, a run-on). Run with
``claims=2, steps=2, stops=0``."""

THIRD_PARTIES = Space(
    origins=(Origins.FRESH, Origins.SAVED),
    loading=(False,),
    strides=(1,),
    shapes=(
        Shape(valued=True, tick=Tick.AFTER_BODY, save=Saving.IN_BODY, trailing=False),
    ),
    cuts=(Cut.MID_BODY,),
    endings=(
        KILL,
        PREEMPTED,
        Stops(False, None, FinalStep.YIELDED, saves=True, teardown=True),
    ),
    displaced=(
        Displaced(Outcome.LOST, value=True, end=KILL),
        Displaced(Outcome.LOST, value=False, end=PREEMPTED),
    ),
    third_parties=(
        None,
        ThirdParty(Release(Outcome.LOST), None, ThirdPartyFinal.NULL, Honors.NONE),
        ThirdParty(Report(None), "note", ThirdPartyFinal.PROGRESS, Honors.NONE),
        ThirdParty(Report(Killed(15)), None, ThirdPartyFinal.PROGRESS, Honors.NONE),
    ),
    before_run=(None,),
    launches=(LaunchShape.NONE,),
    stop_slots=(),
)
"""Third parties' stops after each of two claims, and releases that reach a live
worker: a release, a note, and a report of a non-claimant's end; and a worker
that saves after its own exit and writes a teardown value. Run with ``claims=2,
steps=1, stops=0``."""

STOPS_AND_REAPS = Space(
    origins=(Origins.FRESH, Origins.SAVED),
    loading=(False,),
    strides=(1,),
    shapes=(
        Shape(valued=True, tick=Tick.AFTER_BODY, save=Saving.IN_BODY, trailing=False),
    ),
    cuts=(Cut.MID_BODY,),
    endings=(KILL, PREEMPTED),
    displaced=(),
    third_parties=(
        None,
        ThirdParty(Report(None), None, ThirdPartyFinal.NULL, Honors.UNDISCHARGED),
    ),
    before_run=(None,),
    launches=(LaunchShape.NONE, LaunchShape.REAPED, LaunchShape.WRAPPED),
    stop_slots=(StopSlot.AFTER_CLAIM,),
)
"""Stop requests, the stops that clear them, and launches reaped or not, around
two claims. Run with ``claims=2, steps=1, stops=1``."""
