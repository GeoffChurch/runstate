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

from .vocabulary.payloads import Stopped, Terminated


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

    @property
    def done(self) -> bool:
        """A RunResult is the terminal arm of RunStatus (see watcher.Running)."""
        return True


def _terminal_unless_followed(channel, terminal_topic, opener_topic):
    """The latest terminal record, unless a newer episode opened after it."""
    term = channel.latest(terminal_topic)
    if term is None:
        return None
    opener = channel.latest(opener_topic)
    if opener is not None and opener.seq > term.seq:
        return None  # a started/launched follows this terminal -> an episode is live
    return term


def peek_terminal(channel) -> Optional[RunResult]:
    """Return a terminal RunResult if the run has left a terminal *record*, else
    None. This is the record-based verdict (a clean ``lifecycle.stopped``, or a
    reaped ``launcher.terminated``); the inference-based tier (heartbeat
    staleness ⟹ ``presumed_dead``) is the stateful Watcher's job.

    A clean ``lifecycle.stopped`` takes precedence (the worker's own report);
    otherwise a reaped ``launcher.terminated`` gives the manner of death.

    Episode-aware: a ``lifecycle.stopped`` is only terminal if no
    ``lifecycle.started`` follows it in the log (i.e. it is the latest
    episode's stop, not an earlier episode's). Same guard applies to
    ``launcher.terminated`` vs ``launcher.launched``.
    """
    stopped = _terminal_unless_followed(channel, "lifecycle.stopped", "lifecycle.started")
    if stopped is not None:
        s = Stopped(**stopped.body)
        if s.reason == "completed":
            outcome = "completed"
        elif s.reason == "errored":
            outcome = "errored"
        else:
            outcome = "stopped"  # a clean stop that isn't self-completion
        return RunResult(
            outcome=outcome, reason=s.reason, error=s.error, final_step=s.final_step
        )
    term = _terminal_unless_followed(channel, "launcher.terminated", "launcher.launched")
    if term is not None:
        t = Terminated(**term.body)
        if t.reason == "killed":
            outcome = "killed"
        elif t.exit_code == 0:
            outcome = "completed"
        else:
            outcome = "errored"
        return RunResult(outcome=outcome, reason=t.reason)
    return None
