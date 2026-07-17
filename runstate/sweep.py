"""sweep: run a set of variants sequentially, one per run (docs/design-v0.2.md §9).

There is **no Experiment class** — a sweep is just sequential orchestration over
a Launcher and a Watcher. Each variant is launched into its own run and watched
to a terminal ``RunResult``; ``resume`` skips runs that already finished (by
their terminal record on the log), and ``stop_on_failure`` halts on the first
failed outcome. Concurrent / cross-run coordination is the Watcher's barrier
(``broadcast``), not this helper.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from typing import Any

from .channel import Envelope
from .observables import Outcome, RunResult, peek_terminal
from .watcher import Watcher

# Outcomes sweep treats as failure for stop_on_failure. A clean "preempted"
# (commanded / worker-chosen) and "completed" are NOT failures — this is exactly
# the consumer-side success policy RunResult deliberately leaves to the caller
# (instead of a baked-in `success` bool). The set is the closed Outcome vocabulary's
# death subset — spelled once on the enum, not re-listed here.
_FAILURES = Outcome.failures()


@dataclass(frozen=True)
class Variant:
    """One run's specification: a ``run_id``, the launcher's ``target`` (a
    callable for ThreadLauncher, a command for LocalLauncher), and any
    launcher-specific kwargs forwarded to ``launch`` (e.g. args/kwargs, env)."""

    run_id: str
    target: object
    launch_kwargs: dict[str, Any] = field(default_factory=dict)


def sweep(
    variants: Iterable[Variant],
    launcher: Any,  #  heterogeneous `launch` signatures -> not a typed Protocol (see launcher.py)
    *,
    on_event: Callable[[str, Envelope], object] | None = None,
    resume: bool = True,
    stop_on_failure: bool = False,
    watcher: Watcher | None = None,
) -> list[RunResult]:
    """Launch each variant into its own run and watch it to a terminal result,
    sequentially. Returns one RunResult per run actually reached (so a
    stop_on_failure halt yields a shorter list)."""
    watcher = watcher if watcher is not None else Watcher()
    results: list[RunResult] = []
    for v in variants:
        if resume:
            with launcher.open_channel(v.run_id) as ch:  #  close the probe channel
                existing = peek_terminal(ch)
            if existing is not None:
                result = replace(existing, run_id=v.run_id)
                results.append(result)
                if stop_on_failure and result.outcome in _FAILURES:
                    break
                continue
        handle = launcher.launch(v.run_id, v.target, **v.launch_kwargs)
        watcher.add(handle)
        result = watcher.wait(v.run_id, on_event=on_event)
        results.append(result)
        if stop_on_failure and result.outcome in _FAILURES:
            break
    return results
