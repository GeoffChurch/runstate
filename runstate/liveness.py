"""Observer-side liveness assessment (docs/design-v0.2.md §8, §9).

The layered failure detector, observer side. ``peek_terminal`` covers the two
*terminal* tiers that are a pure read of the log — a clean ``lifecycle.stopped``
(the worker's own report) and a reaped ``launcher.terminated`` (the manner of
death). The non-terminal tiers (resolve-the-handle probe, heartbeat staleness)
are evaluated by the stateful Watcher, which polls and tracks arrival times.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class RunResult:
    # ``outcome`` is the CLOSED, normalized verdict consumers branch/aggregate on
    # (it unifies the worker-stop, reaped-death, and inferred-death tiers into one
    # vocabulary). ``reason`` is the verbatim per-tier label — the raw "why",
    # finer than the bucket (e.g. outcome "stopped", reason "commanded"). There is
    # deliberately no ``success`` bool: it is a pure projection of ``outcome`` that
    # would bake one contested policy ("is a clean non-completion a success?") into
    # the producer; consumers apply their own (e.g. sweep fails on the bottom three).
    outcome: str  # "completed" | "stopped" | "errored" | "killed" | "presumed_dead"
    reason: str
    # run_id is stamped by the Watcher (which knows the run); peek_terminal works
    # from a bare channel and leaves it None.
    run_id: Optional[str] = None
    error: Optional[str] = None
    final_step: Optional[int] = None
    elapsed: Optional[float] = None


def peek_terminal(channel) -> Optional[RunResult]:
    """Return a terminal RunResult if the run has left a terminal *record*, else
    None. This is the record-based verdict (a clean ``lifecycle.stopped``, or a
    reaped ``launcher.terminated``); the inference-based tier (heartbeat
    staleness ⟹ ``presumed_dead``) is the stateful Watcher's job.

    A clean ``lifecycle.stopped`` takes precedence (the worker's own report);
    otherwise a reaped ``launcher.terminated`` gives the manner of death.
    """
    stopped = channel.latest("lifecycle.stopped")
    if stopped is not None:
        b = stopped.body
        reason = b.get("reason", "completed")
        if reason == "completed":
            outcome = "completed"
        elif reason == "errored":
            outcome = "errored"
        else:
            outcome = "stopped"  # a clean stop that isn't self-completion
        return RunResult(
            outcome=outcome,
            reason=reason,
            error=b.get("error"),
            final_step=b.get("final_step"),
        )
    term = channel.latest("launcher.terminated")
    if term is not None:
        b = term.body
        reason = b.get("reason", "exited")
        if reason == "killed":
            outcome = "killed"
        elif b.get("exit_code", 0) == 0:
            outcome = "completed"
        else:
            outcome = "errored"
        return RunResult(outcome=outcome, reason=reason)
    return None
