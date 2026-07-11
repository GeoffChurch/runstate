"""The memoizer: reuse-by-run_id with a schedule-shaped read (docs/specs/memoizer.md).

Two free functions over the topic log: `history` replays the subscription
condition-algebra over the LOGGED `value` points (passive; channel-only;
worker-invisible), and `ensure` adds read-first/produce-on-miss on top (active;
needs a producer). The layers join *through the log*, never by piping values.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Protocol

from .channel import Body, Channel
from .vocabulary.payloads import Topic
from .vocabulary.schedule import Condition, Subscription, references_time, satisfied
from .launcher import Launcher, relaunch_if_needed
from .observables import (Outcome, RunResult, is_step, live_episode,
                          peek_terminal, progress)

if TYPE_CHECKING:
    from .sweep import Variant


class RunFailedError(Exception):
    """``ensure``'s producer run died with a failure verdict (``errored`` /
    ``killed`` / ``presumed_dead``). ``result`` is the ``RunResult`` observed
    AT RAISE TIME — the retry decision's input, handed over rather than
    racily re-read (the log may have moved by the time a catcher looks)."""

    def __init__(self, run_id: str, result: RunResult) -> None:
        super().__init__(f"run {run_id!r} failed: {result.outcome}/{result.reason}")
        self.run_id = run_id
        self.result = result


class NoProgressError(Exception):
    """``ensure``'s OWN spawn died without advancing the step frontier, and no
    live episode owns the run — relaunching would spin, so refuse. ``progress``
    is the frontier at raise (None = no stepped record yet); ``until`` the
    target. A foreign episode's no-progress death re-drives instead (the
    guard is own-spawn-scoped), and a live foreign claim skips the raise
    (claim-aware)."""

    def __init__(self, run_id: str, *, progress: int | None, until: Condition) -> None:
        super().__init__(
            f"run {run_id!r} made no progress toward {until} "
            f"(progress={progress}); cannot extend"
        )
        self.run_id = run_id
        self.progress = progress
        self.until = until


class LiveHandle(Protocol):
    """The minimal liveness handle `ensure` drives: is it still running, and
    block until it isn't. Satisfied by a launcher's `LaunchHandle` and by the
    `_ForeignEpisode` gate (specs/store.md Recipe 2)."""

    def is_alive(self) -> bool: ...
    def wait(self) -> object: ...


class Producer(Protocol):
    """The producer seam (specs/memoizer.md): a run `ensure` can read and extend.
    `channel` / `run_id` locate the log; `extend(until)` drives toward `until`
    and returns a liveness handle (never None -- the seam contract)."""

    run_id: str

    @property
    def channel(self) -> Channel: ...

    def extend(self, until: Condition) -> LiveHandle: ...


class _ForeignEpisode:
    """The gate's foreign half (specs/store.md Recipe 2): a liveness handle
    for an episode this producer did NOT spawn. ``is_alive()`` re-reads
    ``live_episode`` on every poll (verify-at-use: a recordless winner death
    is noticed, never waited on forever); ``wait()`` is a no-op (not our
    child; nothing to reap). ``ensure`` recognizes these handles and exempts
    them from the no-progress guard: a foreign episode that stops without
    progress is re-driven (the lazy-launch re-wake posture), never raised on
    -- the guard remains for OWN spawns that burn without progress."""

    def __init__(self, channel: Channel):
        self._channel = channel

    def is_alive(self) -> bool:
        return live_episode(self._channel) is not None

    def wait(self) -> None:
        return None


def foreign_episode(channel: Channel) -> _ForeignEpisode:
    """The one public copy of the producer gate's foreign-episode handle (the
    F7 doctrine: one boundary rule, one implementation). Compose a producer's
    ``extend`` as ``relaunch_if_needed(...) or foreign_episode(channel)``."""
    return _ForeignEpisode(channel)


class _LaunchProducer:
    """The default producer: wraps a launcher + a Variant so the memoizer can
    treat one run as an extendable, worker-shaped thing. ``extend(until)``
    extracts the scalar step target from ``until`` and injects it into the
    worker kwargs, then relaunches iff not already live. Only the common
    in-process callable-worker case (target via a kwarg); a subprocess / ray /
    service producer is the user's own object with the same ``.channel`` /
    ``.run_id`` / ``.extend`` shape (the seam)."""

    def __init__(self, launcher: Any, variant: Variant, target_key: str = "up_to") -> None:
        self._launcher: Launcher = launcher
        self._variant = variant
        self._target_key = target_key
        self.run_id = variant.run_id

    @property
    def channel(self) -> Channel:
        # cheap: the backends share the backing store, so a fresh read view per
        # access is fine (and is what `ensure` wants as the log grows).
        return self._launcher.open_channel(self._variant.run_id)

    def extend(self, until: Condition) -> LiveHandle:
        """Trigger production toward `until`: relaunch iff not already live,
        else hand back the live episode's foreign handle (the Recipe-2 gate --
        never ``None``, whose record-only wait strands the latecomer on a
        recordless winner death). The default producer translates ONLY a step
        condition -- it injects the scalar `until["step"]` under `target_key`.
        Any other shape (time_seconds, count, any/all) needs a launcher whose
        worker accepts that bound, i.e. the user's own producer
        (.channel/.run_id/.extend(until)); reject it loudly rather than inject
        a dict the worker can't consume."""
        if list(until.keys()) != ["step"]:
            raise ValueError(
                f"the default launch-producer translates only {{'step': N}}; got "
                f"{until!r}. Bring your own producer (.channel/.run_id/.extend(until)) "
                f"for time/compound milestones."
            )
        target = until["step"]
        launch_kwargs = dict(self._variant.launch_kwargs)
        worker_kwargs = dict(launch_kwargs.get("kwargs") or {})
        worker_kwargs[self._target_key] = target
        launch_kwargs["kwargs"] = worker_kwargs
        return relaunch_if_needed(
            self._launcher, self._variant.run_id, self._variant.target, **launch_kwargs
        ) or foreign_episode(self.channel)


def launch_producer(launcher: Any, variant: Variant, *, target_key: str = "up_to") -> _LaunchProducer:
    """A producer backed by ``launcher`` relaunching ``variant``, injecting the
    target into the worker kwargs under ``target_key`` (the loop bound).

    For a **callable-worker** launcher (e.g. ``ThreadLauncher``) whose worker
    receives its config -- and the injected target -- as ``kwargs``. A
    subprocess (``LocalLauncher``), ray, or service worker plumbs the target
    differently (env / CLI), so it gets its **own** producer implementing
    ``.channel`` / ``.run_id`` / ``.extend`` (the seam), not this factory."""
    return _LaunchProducer(launcher, variant, target_key)


def _conforming_point(b: Body) -> bool:
    """A conforming stepped-value body: ``value``/``step``/``t`` present,
    ``step`` int-or-None and ``t`` number-or-None (bool excluded -- the
    ``is_step`` rule). Junk fails here and is skipped (the observables'
    tolerance split: the substrate admits foreign bodies on any topic); a
    conforming *stepless* point passes and is ``history``'s own domain error."""
    if any(k not in b for k in ("value", "step", "t")):
        return False
    if b["step"] is not None and not is_step(b["step"]):
        return False
    t = b["t"]
    return t is None or (isinstance(t, (int, float)) and not isinstance(t, bool))


def _epoch(channel: Channel) -> float | None:
    """The run epoch -- the earliest ``lifecycle.started.attached_at`` -- or
    None when the run has none (never started, or a null or junk-typed
    ``attached_at``: junk earns no epoch, the measurement rule).
    The ONE epoch reader ``history`` and ``_elapsed`` share; their null-epoch
    responses differ (raise vs. inert) but the epoch itself cannot."""
    started = channel.read(topics=[Topic.LIFECYCLE_STARTED], limit=1)
    at = started[0].body.get("attached_at") if started else None
    if not isinstance(at, (int, float)) or isinstance(at, bool):
        return None
    return float(at)


def history(channel: Channel, name: str, schedule: Condition) -> list[Body]:
    """Replay ``schedule`` (the Subscription algebra) over the logged ``value``
    points for ``name``; return the bodies it fires on, in step order.

    Collapses by step (a resumed episode re-emits the checkpoint overlap),
    taking the latest record by ``seq`` -- the as-resumed / continuing branch
    (see docs/backlog/value-plane-divergence-resolution.md). Time-based
    conditions are evaluated run-relative: ``now`` is the point's absolute
    ``value.t`` and ``registered_at`` is the run epoch (earliest
    ``lifecycle.started``), so ``t - epoch`` is seconds since the run began.
    A time-referencing schedule therefore requires an epoch: with none on the
    log it raises (anchoring at 0.0 would evaluate absolute ``value.t`` as
    elapsed). Step-only schedules never touch the epoch.

    Domain rules: a NONCONFORMING record -- missing any of ``value``/``step``/
    ``t``, or wrong-typed ``step``/``t`` -- is foreign junk, skipped as the
    observables' measurement folds skip it; a CONFORMING point with ``step``
    null raises (this is a stepped-trajectory reader). For a point with ``t is
    None`` the run-relative clock cannot advance, so *time*-keyed conditions
    are inert for it (``step`` conditions are unaffected)."""
    by_step: dict[Any, Body] = {}
    for e in channel.read(topics=[Topic.VALUE], name=name):
        b = e.body
        if not _conforming_point(b):
            continue
        by_step[b["step"]] = b   # take-the-latest: a resumed episode's re-emission (higher seq) supersedes
    if any(s is None for s in by_step):
        raise ValueError("history() requires stepped emission; a value point has step=None")
    points = [by_step[s] for s in sorted(by_step)]

    epoch = _epoch(channel)
    if epoch is None:
        if references_time(schedule):
            raise ValueError(
                "time-referencing replay requires a run epoch (started.attached_at)")
        epoch = 0.0
    sub = Subscription(schedule, registered_at=epoch)
    out: list[Body] = []
    for b in points:
        now = b["t"] if b["t"] is not None else epoch
        decision = sub.tick(step=b["step"], now=now)
        if decision.fire:
            out.append(b)
        if decision.expired:
            break
    return out


# outcomes that mean the worker died, not finished -- stop re-driving and surface.
# (presumed_dead is inert here: ensure's only verdict source is peek_terminal,
# which never returns it -- it's the Watcher's inference tier. The death subset is
# the closed Outcome vocabulary's, spelled once on the enum, shared with sweep.)
_FAILURES = Outcome.failures()


def _progress(channel: Channel) -> int:
    """The public ``observables.progress`` with None mapped to -1 — a local
    arithmetic convenience so `_window_step = _progress + 1` starts at 0 on an
    empty log (the in-band sentinel stays private; the public observable
    returns None for absence)."""
    p = progress(channel)
    return -1 if p is None else p


def _elapsed(channel: Channel, clock: Callable[[], float]) -> float:
    """Run-relative seconds on the consumer's OWN poll-clock (dense, monotone,
    gap-inclusive; no wire dependency -- see the spec's clock rationale).
    Returns 0.0 with no epoch yet -- honestly correct for its transient case
    (the run hasn't started during ensure's wait), so time conditions are
    inert until the run begins."""
    epoch = _epoch(channel)
    return 0.0 if epoch is None else float(clock() - epoch)


def _window_step(channel: Channel) -> int:
    """The step coordinate for window-close satisfaction: `_progress + 1`.

    `ensure(until={step:N})` drives the half-open window `[0, N)` -- the
    worker's exclusive target (steps `0..N-1`, reaching `progress = N-1`).
    `_progress + 1 >= N` <=> `_progress >= N-1` is exactly the old `up_to-1`
    hit, and agrees with the read-side `Subscription` expiry gate (which
    excludes the boundary point `N`). The `+1` is applied HERE, in the
    coordinate -- never by rewriting the condition (`{step:N}`->`{step:N-1}`
    would break `any`/`all`, which evaluates every atom against the same
    passed coordinates). `[0,N)` <-> `range(N)`/`up_to` <-> read-side expiry
    is the triple that makes this exact."""
    return _progress(channel) + 1


def _reject_count(cond: Condition) -> None:
    """ensure does not drive the count axis (no use case; an un-driven count atom
    would never satisfy -> livelock). Reject at entry, walking any/all. (count
    stays legal in a *subscription* until -- only the ensure drive-target rejects it.)"""
    if "count" in cond:
        raise ValueError(
            "ensure(until=...) does not support a 'count' condition (no driven "
            "count axis); use step / time_seconds")
    for key in ("any", "all"):
        for c in cond.get(key, ()):
            _reject_count(c)


def _satisfied(channel: Channel, until: Condition, *, clock: Callable[[], float]) -> bool:
    """Has the run closed the `until` window? Coordinates read live: step from
    the dense axis (`_window_step`), time from the consumer's poll-clock
    (`_elapsed`). `count=0` -- the count drive-axis is rejected at entry."""
    return satisfied(until, step=_window_step(channel),
                     time_seconds=_elapsed(channel, clock), count=0)


# `until` is the run *bound*; the emission *filter* (`from`/`every`, the
# ensure(I) strided case) is deferred -- docs/backlog/memoizer-index-algebra.md.
def ensure(producer: Producer, name: str, *, until: Condition, poll_interval: float = 0.01,
           sleep: Callable[[float], None] = time.sleep,
           clock: Callable[[], float] = time.time) -> list[Body]:
    """Return ``name``'s series for the window ``until`` (a Condition from the
    subscription algebra: ``{"step":N} | {"time_seconds":S} | any/all``),
    producing the missing suffix on a miss. Window-closed (or worker-declared
    ``completed``) -> a pure log read; else ``producer.extend(until)`` and wait,
    re-driving ``preempted`` and raising on a failure outcome or no progress.

    `up_to=N` is `until={"step":N}` (the half-open window `[0, N)`). Time is the
    consumer's poll-clock; the generalization to the emission filter
    (`from`/`every`) is deferred -- see docs/backlog/memoizer-index-algebra.md.
    No hang timeout (unchanged)."""
    _reject_count(until)
    channel = producer.channel
    dense: Condition = {"every": {"step": 1}, "until": until}
    result = peek_terminal(channel)
    if _satisfied(channel, until, clock=clock) or (
        result is not None and result.outcome == Outcome.COMPLETED):
        return history(channel, name, dense)

    while not _satisfied(channel, until, clock=clock):
        before = _progress(channel)
        handle = producer.extend(until)
        if handle is None:
            raise TypeError(
                "producer.extend returned None -- the seam contract requires a "
                "liveness handle: your own spawn's, or foreign_episode(channel) "
                "when an episode is already live (specs/store.md Recipe 2)"
            )
        while not _satisfied(channel, until, clock=clock):
            if not handle.is_alive():
                handle.wait()
                break
            sleep(poll_interval)
        else:
            return history(channel, name, dense)
        result = peek_terminal(channel)
        if result is not None and result.outcome in _FAILURES:
            raise RunFailedError(producer.run_id, result)
        if result is not None and result.outcome == Outcome.COMPLETED:
            return history(channel, name, dense)
        # The no-progress guard is OWN-SPAWN-scoped: a foreign episode ending
        # without progress is no evidence a relaunch would spin (we never
        # launched) -- re-drive it, the lazy-launch re-wake posture. And it is
        # CLAIM-AWARE: at this site the own spawn is dead, so a LIVE episode
        # means someone else holds the claim -- the claim-window collision
        # (two dispatchers spawned; ours lost the birth-CAS recordless while
        # the winner is still mid-load) -- not "stuck": skip the raise, and
        # the next extend waits on the winner (the foreign_episode posture).
        # A genuinely stuck own spawn has no live episode and still raises; a
        # false-live handle degrades to the conservative wait.
        if (not isinstance(handle, _ForeignEpisode)
                and _progress(channel) <= before
                and not satisfied(until, step=_progress(channel) + 1,
                                  time_seconds=float("inf"), count=0)
                and live_episode(channel) is None):
            raise NoProgressError(producer.run_id, progress=progress(channel),
                                  until=until)
    return history(channel, name, dense)
