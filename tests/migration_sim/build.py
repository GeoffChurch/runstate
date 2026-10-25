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
    Drain,
    Emit,
    Ended,
    Event,
    Exit,
    Exited,
    FinalStep,
    Finish,
    Halt,
    PassSave,
    Process,
    ProcessEnd,
    Save,
    Watermark,
)

VALUE = "v"
"""The one name a builder gives every labelled value. No 0.3.0 rule reads a
value's name, so a model that let names differ would only hide shared logs."""

RECORD = "status"
"""The stepless register a save's record writes (mycooc's ``status: saving``)."""

HOST = "sim"


def own_process(claim: int) -> Process:
    """The process a builder gives a claim that runs in a process of its own."""
    return Process(HOST, 1000 + claim)


class Tick(Enum):
    """Where an iteration beats."""

    AFTER_BODY = "after-body"  # the steps() driver: the pass, then the tick
    BEFORE_BODY = "before-body"  # a tick-first loop: ``w.tick(s); body(s)``
    AFTER_SAVE = "after-save"  # mycooc's: just after its pass's save (with IN_PASS)
    NONE = "none"  # the iteration skips its tick


class Saving(Enum):
    """Whether and where an iteration saves its state."""

    NONE = "none"
    IN_BODY = "in-body"  # its pass's last act, durable as the pass closes
    RECORDED = "recorded"  # its pass's last act, after a stepless record of it
    IN_PASS = "in-pass"  # mycooc's order: a value, the save of its completed
    # state, its tick (with Tick.AFTER_SAVE), its values, then its close
    AFTER_TICK = "after-tick"  # after the iteration's tick


@dataclass(frozen=True)
class Shape:
    """One iteration's writer choices."""

    valued: bool  # its pass emits a value
    tick: Tick
    watermark: Watermark  # what its beat writes as consumed_seq
    save: Saving
    trailing: bool  # it emits a value after its tick, still at its step
    ended: Ended  # when it ended, where no tick of its own dates it at once


class Step(NamedTuple):
    step: int  # the iteration's label
    shape: Shape


class Cut(Enum):
    """Where the worker's program stops inside its last iteration."""

    MID_BODY = "mid-body"  # its pass's first values written; its save not begun
    MID_SAVE = "mid-save"  # its save begun (its record, or a PassSave that never
    # lands); the save not durable
    AFTER_SAVE = "after-save"  # its in-pass save landed, its values written; its
    # pass not closed
    BEFORE_TICK = "before-tick"  # its pass closed; not yet ticked
    DONE = "done"  # the whole iteration


@dataclass(frozen=True)
class Dies:
    """The worker ends with no ``stopped`` of its own, and its process ends so."""

    how: ProcessEnd


@dataclass(frozen=True)
class Stops:
    """The worker writes its own ``stopped``; it may then save (its exit node,
    when extendable) and write a teardown value, and its process exits."""

    completed: bool
    error: str | None
    final_step: FinalStep
    honors: Drain
    discards: bool  # its loop leaves no resumable state of the open iteration
    saves: bool
    teardown: bool


Ending: TypeAlias = Dies | Stops


def opening(claim: int, it: Step) -> list[Event]:
    """An iteration's events before its pass's body: its begin, and a
    tick-first loop's tick."""
    events: list[Event] = [Begin(claim, it.step)]
    if it.shape.tick is Tick.BEFORE_BODY:
        events.append(Beat(claim, it.shape.watermark))
    return events


def iteration(claim: int, it: Step, *, cut: Cut) -> list[Event]:
    """One iteration's events, up to ``cut``."""
    if it.shape.save is Saving.IN_PASS:
        return _in_pass(claim, it, cut)
    shape = it.shape
    events = opening(claim, it)
    if shape.valued:
        events.append(Emit(claim, VALUE, stepless=False))
    if cut is Cut.MID_BODY:
        return events
    if shape.save is Saving.RECORDED:
        events.append(Emit(claim, RECORD, stepless=True))
    if cut is Cut.MID_SAVE:
        return events
    ticked = shape.tick is Tick.AFTER_BODY and cut is Cut.DONE
    saved = shape.save in (Saving.IN_BODY, Saving.RECORDED)
    events.append(Finish(claim, saved, Ended.TICKED if ticked else shape.ended))
    if cut is Cut.BEFORE_TICK:
        return events
    return events + _after_close(claim, shape)


def _in_pass(claim: int, it: Step, cut: Cut) -> list[Event]:
    """mycooc's pass: a value (its ``status``), the save of its completed state,
    its tick, its values (metrics and ``stage``), then its close."""
    shape = it.shape
    events = opening(claim, it) + [Emit(claim, VALUE, stepless=False)]
    if cut is Cut.MID_BODY:
        return events
    if cut is Cut.MID_SAVE:
        return events + [PassSave(claim, landed=False, ended=shape.ended)]
    ticked = shape.tick is Tick.AFTER_SAVE
    events.append(
        PassSave(claim, landed=True, ended=Ended.TICKED if ticked else shape.ended)
    )
    if ticked:
        events.append(Beat(claim, shape.watermark))
    if shape.valued:
        events.append(Emit(claim, VALUE, stepless=False))
    if cut is Cut.AFTER_SAVE:
        return events
    closing = shape.tick is Tick.AFTER_BODY and cut is Cut.DONE
    events.append(Finish(claim, False, Ended.TICKED if closing else shape.ended))
    if cut is Cut.BEFORE_TICK:
        return events
    return events + _after_close(claim, shape)


def _after_close(claim: int, shape: Shape) -> list[Event]:
    events: list[Event] = []
    if shape.tick is Tick.AFTER_BODY:
        events.append(Beat(claim, shape.watermark))
    if shape.save is Saving.AFTER_TICK:
        events.append(Save(claim))
    if shape.trailing:
        events.append(Emit(claim, VALUE, stepless=False))
    return events


def rest(claim: int, it: Step, *, cut: Cut) -> list[Event]:
    """The events of an iteration after ``cut``: what finishing it writes."""
    return iteration(claim, it, cut=Cut.DONE)[len(iteration(claim, it, cut=cut)) :]


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
    end, its process's with it."""
    events: list[Event] = []
    if isinstance(end, Stops):
        events.append(
            Exit(
                claim,
                end.completed,
                end.error,
                end.final_step,
                end.honors,
                end.discards,
            )
        )
        if end.saves:
            events.append(Save(claim))
        if end.teardown:
            events.append(Emit(claim, VALUE, stepless=False))
    return events + [Halt(claim, halt_of(end))]


def run(claim: int, steps: range | list[int], shape: Shape) -> list[Event]:
    """Whole iterations of one shape, at the given labels."""
    events: list[Event] = []
    for s in steps:
        events += iteration(claim, Step(s, shape), cut=Cut.DONE)
    return events
