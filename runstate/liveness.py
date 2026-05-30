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
    outcome: str  # "completed" | "errored" | "killed" | "presumed_dead"
    success: bool
    reason: str
    error: Optional[str] = None
    final_step: Optional[int] = None
    elapsed: Optional[float] = None


def peek_terminal(channel) -> Optional[RunResult]:
    """Return a terminal RunResult if the run has finished, else None.

    A clean ``lifecycle.stopped`` takes precedence (the worker's own report);
    otherwise a reaped ``launcher.terminated`` gives the manner of death.
    """
    stopped = channel.latest("lifecycle.stopped")
    if stopped is not None:
        b = stopped.body
        reason = b.get("reason", "completed")
        return RunResult(
            outcome=reason,
            success=(reason == "completed"),
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
        return RunResult(outcome=outcome, success=(outcome == "completed"), reason=reason)
    return None
