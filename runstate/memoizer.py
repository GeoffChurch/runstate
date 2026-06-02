"""The memoizer: reuse-by-run_id with a schedule-shaped read (docs/specs/memoizer.md).

Two free functions over the topic log: `history` replays the subscription
condition-algebra over the LOGGED `value` points (passive; channel-only;
worker-invisible), and `ensure` adds read-first/produce-on-miss on top (active;
needs a producer). The layers join *through the log*, never by piping values.
"""

from __future__ import annotations

from .vocabulary.schedule import Subscription
from .launcher import relaunch_if_needed


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

    def extend(self, up_to) -> None:
        launch_kwargs = dict(self._variant.launch_kwargs)
        worker_kwargs = dict(launch_kwargs.get("kwargs") or {})
        worker_kwargs[self._target_key] = up_to
        launch_kwargs["kwargs"] = worker_kwargs
        relaunch_if_needed(
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
