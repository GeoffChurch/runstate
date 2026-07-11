"""The stateless observer plane (docs/specs/observables.md; design §8, §9).

Observables: pure, body-aware folds ``log -> derived view`` — the questions you
can ask of a run without disturbing it (the read side of design §7's
read-vs-subscribe line; reads never pin a worker, subscriptions do). Observe
*statelessly* here; watch *statefully* with the ``Watcher`` (which adds the one
non-log-derivable input, arrival time). Not Rx-style observables — pull-side
pure functions; the push side is the subscription convention.

Membership test: stateless, observer-side, derived-never-stored. Needs a
cursor or a clock? It's the Watcher's. Parses a handle string? It's
``vocabulary/``'s. Tolerance splits by plane (the substrate admits foreign
bodies on any topic): measurement folds (``progress``, ``value_series``,
``live_demand``) skip what isn't a measurement — one lost point is marginal;
verdict folds (``peek_terminal``, ``live_episode``) decide categorical
answers from single records and refuse to guess — an uninterpretable record
raises ``MalformedRecordError``, typed and catchable.

The liveness tiers live here too: ``peek_terminal`` covers the two *terminal*
tiers that are a pure read of the log — a clean ``lifecycle.stopped`` (the
worker's own report) and a reaped ``launcher.terminated`` (the manner of
death); the non-terminal tiers (handle probe, heartbeat staleness) are the
stateful Watcher's.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Optional, TypeVar

from .channel import Channel, Envelope
from .vocabulary.payloads import Stopped, Terminated, Topic
from .vocabulary.handle import resolve
from .vocabulary.schedule import references_time

_T = TypeVar("_T")


class MalformedRecordError(Exception):
    """A record on a verdict topic cannot be interpreted — the writer violated
    the convention. Raised by the verdict folds (``peek_terminal``,
    ``live_episode``, ``await_consumed``'s nak parse), which decide categorical
    answers from single records and refuse to guess; the measurement folds
    (``progress``, ``value_series``, ``live_demand``) skip junk instead.
    Callers wanting degradation catch this."""

    def __init__(self, seq: int, topic: str, detail: str) -> None:
        super().__init__(f"uninterpretable record at seq {seq} on topic {topic!r}: {detail}")
        self.seq = seq
        self.topic = topic
        self.detail = detail


def verdict_parse(cls: type[_T], e: Envelope) -> _T:
    """Parse a verdict-plane record via ``cls(**body)``, wrapping the writer's
    convention violation (bad keys -> TypeError, a payload-constraint violation
    -> ValueError) in the typed MalformedRecordError."""
    try:
        return cls(**e.body)
    except (TypeError, ValueError) as exc:
        raise MalformedRecordError(e.seq, e.topic, str(exc)) from exc


class Outcome(StrEnum):
    """The CLOSED, normalized terminal verdict — the codomain of ``RunResult.outcome``.
    StrEnum: each member IS its wire string (``Outcome.COMPLETED == "completed"``), so it
    serializes byte-identically and compares equal to the bare strings on existing logs —
    zero channel migration. The single authoritative home for the vocabulary: peek_terminal,
    the Watcher, sweep, and the memoizer reference these members instead of re-spelling the
    literals (which had drifted into four uncoordinated copies)."""
    COMPLETED = "completed"
    PREEMPTED = "preempted"
    ERRORED = "errored"
    KILLED = "killed"
    PRESUMED_DEAD = "presumed_dead"

    @classmethod
    def failures(cls) -> frozenset["Outcome"]:
        """The death outcomes (the worker died, not a clean finish/preempt) — the subset
        sweep and the memoizer stop-and-surface on, spelled once here."""
        return frozenset({cls.ERRORED, cls.KILLED, cls.PRESUMED_DEAD})


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
    outcome: Outcome  # the closed verdict vocabulary (see Outcome above)
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


def latest_episode(channel: Channel) -> Envelope | None:
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
    return channel.latest(Topic.LIFECYCLE_STARTED)


def live_episode(channel: Channel) -> Optional[str]:
    """Handle of the currently-live episode, or None: the latest episode
    (``latest_episode``) with no following ``stopped`` whose worker resolves
    alive (a started-then-crashed episode resolves dead -> not live)."""
    started = latest_episode(channel)
    if started is None:
        return None
    stopped = channel.latest(Topic.LIFECYCLE_STOPPED)
    if stopped is not None and stopped.seq > started.seq:
        return None
    try:
        handle = started.body["handle"]
    except KeyError as exc:
        raise MalformedRecordError(started.seq, started.topic, "missing 'handle'") from exc
    if not isinstance(handle, str):
        raise MalformedRecordError(
            started.seq, started.topic, f"handle must be a string, got {handle!r}"
        )
    if resolve(handle) is False:
        return None
    return handle


def _terminal_unless_followed(channel: Channel, terminal_topic: str, opener_topic: str) -> Envelope | None:
    """The latest terminal record, unless a newer episode opened after it."""
    term = channel.latest(terminal_topic)
    if term is None:
        return None
    opener = channel.latest(opener_topic)
    if opener is not None and opener.seq > term.seq:
        return None  # a started/launched follows this terminal -> an episode is live
    return term


def peek_terminal(channel: Channel) -> Optional[RunResult]:
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
    stopped = _terminal_unless_followed(channel, Topic.LIFECYCLE_STOPPED, Topic.LIFECYCLE_STARTED)
    if stopped is not None:
        s = verdict_parse(Stopped, stopped)
        if s.error is not None:          # NB: `is not None`, not truthiness — "" still errors
            outcome = Outcome.ERRORED
        elif s.completed:
            outcome = Outcome.COMPLETED
        else:
            outcome = Outcome.PREEMPTED
        return RunResult(outcome=outcome, reason=outcome, error=s.error, final_step=s.final_step)
    term = _terminal_unless_followed(channel, Topic.LAUNCHER_TERMINATED, Topic.LAUNCHER_LAUNCHED)
    if term is not None:
        t = verdict_parse(Terminated, term)
        if t.reason == "killed":
            outcome = Outcome.KILLED
        elif t.exit_code == 0:
            outcome = Outcome.COMPLETED
        else:
            outcome = Outcome.ERRORED
        return RunResult(outcome=outcome, reason=t.reason)
    return None


def boundary_voided(sub_seq: int, started_seqs: list[int], drainer_started_seq: int) -> bool:
    """The episode-boundary discharge (specs/time-lease-boundary.md): a
    time-referencing subscribe is voided iff a ``lifecycle.started`` other
    than the draining episode's own follows it — equivalently, a ``started``
    strictly between the subscribe and the drainer's own. ONE predicate,
    shared by the worker (drain form: drainer = its own claim) and
    ``live_demand`` (observer form: drainer = the latest ``started``)."""
    return any(sub_seq < b < drainer_started_seq for b in started_seqs)


def live_demand(channel: Channel) -> list[Envelope]:
    """The live leased demand: every ``control.subscribe`` envelope with no
    **answer** following it by seq (specs/service-worker.md: the positional
    answer fold — an answer is a ``control.unsubscribe`` or ``lifecycle.nak``
    bearing its ``request_id``; null-id naks answer nothing, and an answer
    never reaches a *later* same-id subscribe, so resubscribe-after-answer is
    live), and — for time-referencing schedules — no episode boundary between
    it and the latest ``lifecycle.started`` (specs/time-lease-boundary.md:
    a time-lease is live only while the latest episode is still its first
    possible drainer; the boundary ``started`` is its counter-record). The
    one public home of the rule the worker's refold and the relaunch decider
    both consume. Value-blind: it reads schedule *shape* for the time-atom
    check, never payloads."""
    pending: dict[str, Envelope] = {}   # request_id -> the latest unanswered subscribe
    starteds: list[int] = []
    for e in channel.read():
        if e.topic == Topic.LIFECYCLE_STARTED:
            starteds.append(e.seq)
            continue
        if e.request_id is None:
            continue
        if e.topic == Topic.CONTROL_SUBSCRIBE:
            pending[e.request_id] = e
        elif e.topic in (Topic.CONTROL_UNSUBSCRIBE, Topic.LIFECYCLE_NAK):
            pending.pop(e.request_id, None)
    latest = starteds[-1] if starteds else 0
    return sorted(
        (e for e in pending.values()
         if not (references_time(e.body)
                 and boundary_voided(e.seq, starteds, latest))),
        key=lambda e: e.seq,
    )


def undischarged_stops(channel: Channel) -> list[Envelope]:
    """The ``control.stop`` envelopes not yet discharged — pending from append
    until the next ``lifecycle.stopped`` FOLLOWS by seq, when one ``stopped``
    discharges every pending stop at once (specs/stop-discharge.md). The
    positional stop rule's public observer home, mirroring ``live_demand``
    (the subscribe fold's): "is there an unhonored stop?" for a status
    surface or a dispatch gate. The worker's drain applies the same rule
    (its ``_discharge_floor`` skip).

    Two edges an observer cannot avoid: **pending ≠ due** — a stop with a
    ``from`` condition is pending the moment it lands but fires only when the
    condition crosses (due-evaluation needs the worker's coordinates), so a
    gate refusing work on "pending" may be gating on a not-yet-due stop. And
    **naked stops over-report**: a malformed stop was refused by the worker
    (never in its pending set), but no nak discharges a stop — it stays
    listed until the next ``stopped`` discharges everything (conservative:
    never under-reports)."""
    stopped = channel.latest(Topic.LIFECYCLE_STOPPED)
    return channel.read(after=stopped.seq if stopped is not None else 0,
                        topics=[Topic.CONTROL_STOP])


def progress(channel: Channel) -> Optional[int]:
    """Max step the trajectory reached, from the DENSE axis (the heartbeat
    beats every tick regardless of emission): the latest
    ``lifecycle.heartbeat.step`` and the latest ``lifecycle.stopped.final_step``,
    whichever is greater; None if neither axis has a value yet. The frontier
    of two registers — under an episode rewind the latest heartbeat already
    reflects the resumed branch.

    THE WINDOW FENCEPOST (the one home for the rule a second implementation and
    a viewer both need): a target ``until={"step": N}`` is the **half-open**
    window ``[0, N)`` — steps ``0 … N-1`` — so the target is reached iff
    ``progress + 1 >= N`` (equivalently ``progress >= N - 1``); ``progress is
    None`` (no stepped record) is window-step 0, so ``N == 0`` is trivially
    reached. This is what ``ensure``/``history`` gate on internally
    (``memoizer._window_step``); a consumer asking "did this run reach its
    target?" uses this arithmetic, not a bespoke off-by-one."""
    steps = []
    hb = channel.latest(Topic.LIFECYCLE_HEARTBEAT)
    if hb is not None and is_step(hb.body.get("step")):
        steps.append(hb.body["step"])
    stopped = channel.latest(Topic.LIFECYCLE_STOPPED)
    if stopped is not None and is_step(stopped.body.get("final_step")):
        steps.append(stopped.body["final_step"])
    return max(steps) if steps else None


def is_step(v: object) -> bool:
    """A conforming step measurement: an int (bool excluded — JSON ``true`` is
    not an integer). Wrong-typed junk isn't a measurement (the tolerance split,
    module docstring): skip it, never compare against it."""
    return isinstance(v, int) and not isinstance(v, bool)


def _value_points(channel: Channel) -> Iterator[tuple[str, Any, Any]]:
    """Decode ``value`` envelopes to ``(name, step, value)`` samples, lazily.
    Applies the domain rules: skip records with no envelope ``name``, a null
    (or wrong-typed) ``step``, or no ``"value"`` key — a stepless emission is
    outside the step-indexed observable's domain, and the substrate admits
    foreign bodies on any topic. Private: the designated escape hatch if a
    custom-fold consumer ever appears; until then the bring-your-own-fold seam
    is the substrate itself (``read`` + a loop)."""
    for e in channel.read(topics=[Topic.VALUE]):
        if e.name is None or "value" not in e.body or not is_step(e.body.get("step")):
            continue
        yield e.name, e.body["step"], e.body["value"]


def value_series(channel: Channel) -> dict[str, dict[int, Any]]:
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
    out: dict[str, dict[int, Any]] = {}
    for name, step, value in _value_points(channel):
        out.setdefault(name, {})[step] = value
    return {name: dict(sorted(series.items())) for name, series in out.items()}
