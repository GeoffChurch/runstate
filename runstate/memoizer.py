"""The memoizer: reuse-by-run_id with a schedule-shaped read (docs/specs/memoizer.md).

Two free functions over the topic log: `history` replays the subscription
condition-algebra over the LOGGED `value` points (passive; channel-only;
worker-invisible), and `ensure` adds read-first/produce-on-miss on top (active;
needs a producer). The layers join *through the log*, never by piping values.
"""

from __future__ import annotations

from .vocabulary.schedule import Subscription


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
