"""The memoizer: reuse-by-run_id with a schedule-shaped read (docs/specs/memoizer.md).

Two free functions over the topic log: `history` replays the subscription
condition-algebra over the LOGGED `value` points (passive; channel-only;
worker-invisible), and `ensure` adds read-first/produce-on-miss on top (active;
needs a producer). The layers join *through the log*, never by piping values.
"""

from __future__ import annotations

import time

from .vocabulary.schedule import Subscription
from .launcher import relaunch_if_needed
from .liveness import peek_terminal


class _LaunchProducer:
    """The default producer: wraps a launcher + a Variant so the memoizer can
    treat one run as an extendable, worker-shaped thing. ``extend(N)`` injects
    the target into the worker kwargs and relaunches iff not already live. Only
    the common in-process callable-worker case (target via a kwarg); a
    subprocess / ray / service producer is the user's own object with the same
    ``.channel`` / ``.run_id`` / ``.extend`` shape (the seam)."""

    def __init__(self, launcher, variant, target_key="up_to"):
        self._launcher = launcher
        self._variant = variant
        self._target_key = target_key
        self.run_id = variant.run_id

    @property
    def channel(self):
        # cheap: both backends share the backing store, so a fresh read view per
        # access is fine (and is what `ensure` wants as the log grows).
        return self._launcher.open_channel(self._variant.run_id)

    def extend(self, up_to):
        """Trigger production toward step ``up_to``: relaunch iff not already
        live. Returns the new ``LaunchHandle`` if it launched an episode, or
        ``None`` if it no-op'd (an episode was already live). ``ensure`` reads
        this to know whether it actually *drove* new work (the seam contract:
        a producer's ``extend`` returns truthy iff it triggered production)."""
        launch_kwargs = dict(self._variant.launch_kwargs)
        worker_kwargs = dict(launch_kwargs.get("kwargs") or {})
        worker_kwargs[self._target_key] = up_to
        launch_kwargs["kwargs"] = worker_kwargs
        return relaunch_if_needed(
            self._launcher, self._variant.run_id, self._variant.target, **launch_kwargs
        )


def launch_producer(launcher, variant, *, target_key="up_to"):
    """A producer backed by ``launcher`` relaunching ``variant``, injecting the
    target into the worker kwargs under ``target_key`` (the loop bound).

    For a **callable-worker** launcher (e.g. ``ThreadLauncher``) whose worker
    receives its config -- and the injected target -- as ``kwargs``. A
    subprocess (``LocalLauncher``), ray, or service worker plumbs the target
    differently (env / CLI), so it gets its **own** producer implementing
    ``.channel`` / ``.run_id`` / ``.extend`` (the seam), not this factory."""
    return _LaunchProducer(launcher, variant, target_key)


def history(channel, name, schedule: dict) -> list[dict]:
    """Replay ``schedule`` (the Subscription algebra) over the logged ``value``
    points for ``name``; return the bodies it fires on, in step order.

    Collapses by step (a resumed episode re-emits the checkpoint overlap), but
    RAISES on a same-step / differing-value collision -- the reuse-soundness
    alarm (a non-reproducible / non-target-independent trajectory). Time-based
    conditions are evaluated run-relative: ``now`` is the point's absolute
    ``value.t`` and ``registered_at`` is the run epoch (earliest
    ``lifecycle.started``), so ``t - epoch`` is seconds since the run began.

    Assumes stepped emission: a ``value.step`` of ``None`` raises (this is a
    stepped-trajectory reader). For a point with ``t is None`` the run-relative
    clock cannot advance, so *time*-keyed conditions are inert for it (``step``
    conditions are unaffected)."""
    by_step: dict = {}
    for e in channel.read(topics=["value"], name=name):
        b = e.body
        s = b["step"]
        if s in by_step and by_step[s]["value"] != b["value"]:
            raise ValueError(
                f"divergent re-emission at step {s}: "
                f"{by_step[s]['value']!r} != {b['value']!r} "
                f"(trajectory not reproducible -- reuse would be unsound)"
            )
        by_step[s] = b
    if any(s is None for s in by_step):
        raise ValueError("history() requires stepped emission; a value point has step=None")
    points = [by_step[s] for s in sorted(by_step)]

    started = channel.read(topics=["lifecycle.started"], limit=1)
    epoch = (
        started[0].body["attached_at"]
        if started and started[0].body.get("attached_at") is not None
        else 0.0
    )
    sub = Subscription(schedule, registered_at=epoch)
    out: list[dict] = []
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
# which never returns it -- it's the Watcher's inference tier. Kept for parity
# with the closed RunResult.outcome vocabulary / sweep's _FAILURES.)
_FAILURES = frozenset({"errored", "killed", "presumed_dead"})


def _progress(channel) -> int:
    """The max step the trajectory has reached, from the DENSE axis (the
    heartbeat beats every tick regardless of emission): the latest
    heartbeat.step and the latest stopped.final_step. -1 if none yet."""
    steps = []
    hb = channel.latest("lifecycle.heartbeat")
    if hb is not None and hb.body.get("step") is not None:
        steps.append(hb.body["step"])
    stopped = channel.latest("lifecycle.stopped")
    if stopped is not None and stopped.body.get("final_step") is not None:
        steps.append(stopped.body["final_step"])
    return max(steps) if steps else -1


def ensure(producer, name, *, up_to, poll_interval=0.01, sleep=time.sleep) -> list[dict]:
    """Return ``name``'s series through step ``up_to`` (steps ``0..up_to-1``),
    producing the missing suffix on a miss. Hit (progress >= up_to-1) -> a pure
    log read. Miss -> ``producer.extend(up_to)``, wait until the trajectory
    reaches the target or the episode we're tracking ends; re-drive a short
    clean stop (resume converges); raise on a failure outcome or no progress.
    Reads progress from the dense axis and content from the value series, so it
    works for sparse emitters too (you get the sparse series, not N points).

    No hang timeout: ``ensure`` trusts the episode to terminate, so a
    wedged-but-live worker (or a foreign live episode that never reaches the
    target) blocks. Wrap with a ``Watcher``/``heartbeat_timeout`` out of band if
    you need death-by-staleness."""
    channel = producer.channel
    dense = {"every": {"step": 1}, "until": {"step": up_to}}
    result = peek_terminal(channel)
    if _progress(channel) >= up_to - 1 or (result is not None and result.outcome == "completed"):
        return history(channel, name, dense)

    while _progress(channel) < up_to - 1:
        before = _progress(channel)
        handle = producer.extend(up_to)   # LaunchHandle if it launched, else None (no-op)
        # Wait until the target is reached, or the episode we're tracking ends.
        # When we launched, ``handle.is_alive()`` is the exact, race-free signal
        # that *our* episode finished -- unlike log-seq heuristics, which trip
        # over a prior episode's trailing `stopped`/`terminated` records. When
        # extend no-op'd (a foreign episode was already live), wait for that
        # episode to go terminal on the log instead.
        while _progress(channel) < up_to - 1:
            if handle is not None:
                if not handle.is_alive():
                    handle.wait()         # reap (writes launcher.terminated); returns at once
                    break
            elif peek_terminal(channel) is not None:
                break
            sleep(poll_interval)
        else:
            return history(channel, name, dense)        # loop exited because target reached
        result = peek_terminal(channel)
        if result is not None and result.outcome in _FAILURES:
            raise RuntimeError(
                f"run {producer.run_id!r} failed: {result.outcome}/{result.reason}"
            )
        if result is not None and result.outcome == "completed":
            return history(channel, name, dense)   # producer declared done before up_to
        # No-progress is *our* failure only when we drove an episode that then
        # didn't advance. A no-op extend (onto a foreign episode that stopped
        # short) drove nothing -> loop and re-drive (the next extend spawns,
        # since that episode is now terminal), which converges.
        if handle is not None and _progress(channel) <= before:
            raise RuntimeError(
                f"run {producer.run_id!r} made no progress toward step {up_to} "
                f"(stuck at {_progress(channel)}); cannot extend"
            )
    return history(channel, name, dense)
