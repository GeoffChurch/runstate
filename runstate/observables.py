"""The stateless observer plane (docs/specs/observables.md; design §8, §9).

Observables: pure, body-aware folds ``log -> derived view`` — the questions you
can ask of a run without disturbing it (the read side of design §7's
read-vs-subscribe line; reads never pin a worker, subscriptions do). Observe
*statelessly* here; watch *statefully* with the ``Watcher`` (which adds the one
non-log-derivable input, arrival time). Not Rx-style observables — pull-side
pure functions; the push side is the subscription convention.

Membership test: stateless, observer-side, derived-never-stored. Needs a
cursor or a clock? It's the Watcher's. Parses a handle string? It's
``vocabulary/``'s. Every fold is a tolerant reader: the substrate admits
foreign bodies on any topic, so records missing a fold's required keys are
skipped, never raised on.

The liveness tiers live here too: ``peek_terminal`` covers the two *terminal*
tiers that are a pure read of the log — a clean ``lifecycle.stopped`` (the
worker's own report) and a reaped ``launcher.terminated`` (the manner of
death); the non-terminal tiers (handle probe, heartbeat staleness) are the
stateful Watcher's.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .vocabulary.payloads import Stopped, Terminated
from .vocabulary.handle import resolve


@dataclass(frozen=True)
class RunResult:
    # ``outcome`` is the CLOSED, normalized verdict consumers branch/aggregate on
    # (it unifies the worker-stop, reaped-death, and inferred-death tiers into one
    # vocabulary). ``reason`` is the verbatim per-tier label — the raw "why". For the
    # lifecycle tier, reason == outcome (the verbatim worker reason is gone; B′ removes
    # Stopped.reason, and commandedness is recoverable from the control.stop on the
    # log). The launcher tier keeps its finer labels ("exited" / "killed"). There is
    # deliberately no ``success`` bool: it is a pure projection of ``outcome`` that
    # would bake one contested policy ("is a clean non-completion a success?") into
    # the producer; consumers apply their own (e.g. sweep fails on the bottom three).
    outcome: str  # "completed" | "preempted" | "errored" | "killed" | "presumed_dead"
    reason: str
    # run_id is stamped by the Watcher (which knows the run); peek_terminal works
    # from a bare channel and leaves it None.
    run_id: Optional[str] = None
    error: Optional[str] = None
    final_step: Optional[int] = None

    @property
    def done(self) -> bool:
        """A RunResult is the terminal arm of RunStatus (see watcher.Running)."""
        return True


def latest_episode(channel):
    """The latest ``lifecycle.started`` envelope, or None if no worker ever
    attached. *Latest* means latest — live, cleanly ended, or crashed alike
    (liveness is ``live_episode``'s composition; None = the run was never
    started, which is information, not a degenerate case). The envelope's
    ``seq`` is the episode-window watermark (``channel.read(after=e.seq, …)``
    reads this episode's events); its body parses via ``Started(**e.body)``.

    The fold is one ``latest`` call; what this function owns is the
    episode-boundary *rule* (specs/run-episodes.md Decision 1: an episode is a
    read-side derivation, not a record) — named in the one place that changes
    if explicit episode markers ever land, instead of being re-derived (and
    misapplied — audit F7) by every consumer."""
    return channel.latest("lifecycle.started")


def live_episode(channel) -> Optional[str]:
    """Handle of the currently-live episode, or None: the latest episode
    (``latest_episode``) with no following ``stopped`` whose worker resolves
    alive (a started-then-crashed episode resolves dead -> not live)."""
    started = latest_episode(channel)
    if started is None:
        return None
    stopped = channel.latest("lifecycle.stopped")
    if stopped is not None and stopped.seq > started.seq:
        return None
    if resolve(started.body["handle"]) is False:
        return None
    return started.body["handle"]


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
        if s.error is not None:          # NB: `is not None`, not truthiness — "" still errors
            outcome = "errored"
        elif s.completed:
            outcome = "completed"
        else:
            outcome = "preempted"
        return RunResult(outcome=outcome, reason=outcome, error=s.error, final_step=s.final_step)
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


def live_demand(channel) -> list:
    """The live leased demand: every ``control.subscribe`` envelope with no
    **answer** — a ``control.unsubscribe`` or a ``lifecycle.nak`` bearing its
    ``request_id`` — *following it by seq* (specs/service-worker.md: the
    positional answer fold, the discharge floor's third instance; naks with a
    null ``request_id`` answer nothing, and an answer never reaches a *later*
    same-id subscribe, so resubscribe-after-answer is live). The one public
    home of the rule the worker's refold and the relaunch decider both
    consume; an envelope-level fold — ``topic``/``request_id``/``seq`` only,
    body untouched."""
    pending: dict = {}      # request_id -> the latest unanswered subscribe
    for e in channel.read():
        if e.request_id is None:
            continue
        if e.topic == "control.subscribe":
            pending[e.request_id] = e
        elif e.topic in ("control.unsubscribe", "lifecycle.nak"):
            pending.pop(e.request_id, None)
    return sorted(pending.values(), key=lambda e: e.seq)


def progress(channel) -> Optional[int]:
    """Max step the trajectory reached, from the DENSE axis (the heartbeat
    beats every tick regardless of emission): the latest
    ``lifecycle.heartbeat.step`` and the latest ``lifecycle.stopped.final_step``,
    whichever is greater; None if neither axis has a value yet. The frontier
    of two registers — under an episode rewind the latest heartbeat already
    reflects the resumed branch."""
    steps = []
    hb = channel.latest("lifecycle.heartbeat")
    if hb is not None and hb.body.get("step") is not None:
        steps.append(hb.body["step"])
    stopped = channel.latest("lifecycle.stopped")
    if stopped is not None and stopped.body.get("final_step") is not None:
        steps.append(stopped.body["final_step"])
    return max(steps) if steps else None


def _value_points(channel):
    """Decode ``value`` envelopes to ``(name, step, value)`` samples, lazily.
    Applies the domain rules: skip records with no envelope ``name``, a null
    ``step``, or no ``"value"`` key — a stepless emission is outside the
    step-indexed observable's domain, and the substrate admits foreign bodies
    on any topic. Private: the designated escape hatch if a custom-fold
    consumer ever appears; until then the bring-your-own-fold seam is the
    substrate itself (``read`` + a loop)."""
    for e in channel.read(topics=["value"]):
        if e.name is None or "value" not in e.body or e.body.get("step") is None:
            continue
        yield e.name, e.body["step"], e.body["value"]


def value_series(channel) -> dict:
    """``{name: {step: value}}`` — the run's reported values as functions of
    step, in one log pass (per-name access = indexing; name enumeration =
    ``.keys()``).

    A ``value`` event is a *sample* of the worker's current-value function
    (``set(name, value)`` + ``tick(step)``: one value per (name, step);
    concurrent subscriptions duplicate samples differing only in
    ``request_id``), so the fold is the substrate's register projection
    (design §4 ``latest``) lifted pointwise: last-write-wins by ``seq`` per
    (name, step) cell. Under an episode rewind the rewritten steps last-win
    and the orphaned branch drops out — the as-resumed trajectory, with the
    raw events still on the log for forensics. Inner dicts are step-sorted.

    Pure and cache-free: the fold inherits the scope of the read view it is
    given (visibility/enforcement compose upstream — design §6); ``request_id``
    is a dedup concern only and is ignored."""
    out: dict = {}
    for name, step, value in _value_points(channel):
        out.setdefault(name, {})[step] = value
    return {name: dict(sorted(series.items())) for name, series in out.items()}
