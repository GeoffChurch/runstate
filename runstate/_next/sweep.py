"""The sequential sweep of log format 0.4.0, staged (docs/specs/commits-and-lineage.md
§5; plan, open questions 13 and 18): ``Variant``, the run a launcher's target is
launched into, and ``sweep``, which launches each into its own run and watches it to
its verdict.

STAGED. Task 19 of docs/plans/2026-10-28-layer2-live.md moves this module over
``runstate/sweep.py``.

There is no Experiment class: a sweep is sequential orchestration over a
``Launcher[T]`` and a ``Watcher``; cross-run coordination is the Watcher's
``broadcast``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from typing import Generic, TypeVar

from ..channel import Envelope, RunNotFound
from .launcher import Launcher
from .observables import RunResult, peek_terminal
from .vocabulary.payloads import Outcome
from .watcher import Watcher

T = TypeVar("T")


@dataclass(frozen=True)
class Variant(Generic[T]):
    """One run of a sweep, or the run a producer drives (``launch_producer``):
    ``run_id``, the run's address, and ``target``, the record its launcher takes
    (``Launcher[T]``). The record carries everything that changes the worker's
    result, its arguments or its environment, so there are no launch keywords
    beside it (plan, open question 18)."""

    run_id: str
    target: T


def sweep(
    variants: Iterable[Variant[T]],
    launcher: Launcher[T],
    *,
    watcher: Watcher,
    resume: bool,
    stop_on_failure: bool,
    on_event: Callable[[str, Envelope], object] | None = None,
) -> list[RunResult]:
    """Launch each variant into its own run and watch it to its verdict, one at a
    time; one ``RunResult`` per run reached, so a halt yields a shorter list. There
    is no Experiment class: cross-run coordination is the Watcher's ``broadcast``.

    - ``launcher`` takes each variant's target (``Launcher[T]``), so a mismatched
      pair is a type error (plan, open question 18).
    - ``watcher``, ``resume`` and ``stop_on_failure`` are required (plan, open
      question 13): the Watcher's ``heartbeat_timeout`` decides verdicts, and the
      two flags decide which runs are launched.
    - ``resume`` skips a run whose log already holds a verdict (``peek_terminal``),
      read through ``attach_channel``, which never creates: a variant never
      launched has no run (``RunNotFound``), and is launched.
    - ``stop_on_failure`` halts once a verdict has any outcome in
      ``Outcome.failures()``: a death report's set is tested whole (§5).
    - ``on_event`` is an exempt default (plan, "Exempt defaults"): it observes, and
      changes no result."""
    results: list[RunResult] = []
    for v in variants:
        result: RunResult | None = None
        if resume:
            try:
                with launcher.attach_channel(v.run_id) as ch:
                    result = peek_terminal(ch)
            except RunNotFound:
                result = None  # never launched: nothing to resume
        if result is not None:
            result = replace(result, run_id=v.run_id)
        else:
            watcher.add(launcher.launch(v.run_id, v.target))
            result = watcher.wait(v.run_id, on_event=on_event)
        results.append(result)
        if stop_on_failure and result.outcomes & Outcome.failures():
            break
    return results
