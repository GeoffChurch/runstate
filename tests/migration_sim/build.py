"""Builders: a claim's events from its writer's structured choices.

A history is a flat sequence of events. These build the common shapes, so that a
history reads as its writers' choices: ``enumerate.py`` ranges over them, and the
tests write named histories with them. Nothing here adds a choice the events
cannot express.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import NamedTuple, TypeAlias, assert_never

from .history import (
    Beat,
    Begin,
    Claim,
    Emit,
    Event,
    Exit,
    Exited,
    FinalStep,
    Finish,
    Halt,
    Origin,
    ProcessEnd,
    Save,
)

VALUE = "v"
"""The one name a builder gives every labelled value. No 0.3.0 rule reads a
value's name, so a model that let names differ would only hide shared logs."""

RECORD = "status"
"""The stepless register a save's record writes (mycooc's ``status: saving``)."""


class Tick(Enum):
    """Where an iteration beats."""

    AFTER_BODY = "after-body"  # the steps() driver: the body, then the tick
    BEFORE_BODY = "before-body"  # a tick-first loop: ``w.tick(s); body(s)``
    NONE = "none"  # the iteration skips its tick


class Saving(Enum):
    """Whether and where an iteration saves its state."""

    NONE = "none"
    IN_BODY = "in-body"  # the body's last act, durable as the iteration finishes
    RECORDED = "recorded"  # in the body, after a stepless record of the save
    AFTER_TICK = "after-tick"  # after the iteration's tick


@dataclass(frozen=True)
class Shape:
    """One iteration's writer choices."""

    valued: bool  # its body emits a value
    tick: Tick
    save: Saving
    trailing: bool  # it emits a value after its tick, still at its step


class Step(NamedTuple):
    step: int  # the iteration's label
    shape: Shape


class Cut(Enum):
    """Where the worker's program stops inside its last iteration."""

    MID_BODY = "mid-body"  # its body's values written; its save not begun
    MID_SAVE = "mid-save"  # its save's record written; the save not durable
    BEFORE_TICK = "before-tick"  # finished, and saved in its body; not yet ticked
    DONE = "done"  # the whole iteration


@dataclass(frozen=True)
class Dies:
    """The worker's process ends with no ``stopped`` of its own."""

    how: ProcessEnd


@dataclass(frozen=True)
class Stops:
    """The worker writes its own ``stopped``; it may then save (its exit node,
    when extendable) and write a teardown value, and its process exits."""

    completed: bool
    error: str | None
    final_step: FinalStep
    saves: bool
    teardown: bool


Ending: TypeAlias = Dies | Stops


def iteration(claim: int, it: Step, *, cut: Cut) -> list[Event]:
    """One iteration's events, up to ``cut``."""
    shape = it.shape
    events: list[Event] = [Begin(claim, it.step)]
    if shape.tick is Tick.BEFORE_BODY:
        events.append(Beat(claim))
    if shape.valued:
        events.append(Emit(claim, VALUE, stepless=False))
    if cut is Cut.MID_BODY:
        return events
    if shape.save is Saving.RECORDED:
        events.append(Emit(claim, RECORD, stepless=True))
    if cut is Cut.MID_SAVE:
        return events
    events.append(Finish(claim, saved=shape.save in (Saving.IN_BODY, Saving.RECORDED)))
    if cut is Cut.BEFORE_TICK:
        return events
    if shape.tick is Tick.AFTER_BODY:
        events.append(Beat(claim))
    if shape.save is Saving.AFTER_TICK:
        events.append(Save(claim))
    if shape.trailing:
        events.append(Emit(claim, VALUE, stepless=False))
    return events


def halt_of(end: Ending) -> ProcessEnd:
    """How the worker's process ends: a worker that wrote its own ``stopped``
    exits 0, or 1 after an error."""
    if isinstance(end, Dies):
        return end.how
    if isinstance(end, Stops):
        return Exited(0 if end.error is None else 1)
    assert_never(end)


def ending(claim: int, end: Ending) -> list[Event]:
    """How the worker's program ends: its own ``stopped`` or none, then its
    process's end."""
    events: list[Event] = []
    if isinstance(end, Stops):
        events.append(Exit(claim, end.completed, end.error, end.final_step))
        if end.saves:
            events.append(Save(claim))
        if end.teardown:
            events.append(Emit(claim, VALUE, stepless=False))
    return events + [Halt(claim, halt_of(end))]


def program(
    claim: int,
    *,
    origin: Origin,
    launch: str | None,
    loading: bool,
    steps: list[Step],
    cut: Cut,
    end: Ending,
) -> list[Event]:
    """A claim's whole program: its claim, a value at its loaded step if
    ``loading``, its iterations (the last stopped at ``cut``), and its end."""
    events: list[Event] = [Claim(claim, origin, launch)]
    if loading:
        events.append(Emit(claim, VALUE, stepless=False))
    for k, it in enumerate(steps):
        events += iteration(claim, it, cut=cut if k == len(steps) - 1 else Cut.DONE)
    return events + ending(claim, end)


def run(claim: int, steps: range | list[int], shape: Shape) -> list[Event]:
    """Whole iterations of one shape, at the given labels."""
    events: list[Event] = []
    for s in steps:
        events += iteration(claim, Step(s, shape), cut=Cut.DONE)
    return events
