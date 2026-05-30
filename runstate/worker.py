"""The reference worker loop (docs/design-v0.2.md §6).

A worker-side runtime over a Channel. The worker reports *current values* (set
via ``set``); each ``tick(step)`` drains ``control.*``, registers/cancels
subscriptions, and services the due ones by emitting ``value`` envelopes
correlated by ``request_id``.

The clock is injectable (``now``) for deterministic tests.
"""

from __future__ import annotations

import time

from .schedule import Subscription


class Worker:
    def __init__(self, channel, *, now=time.time):
        self._ch = channel
        self._now = now
        self._values: dict = {}
        self._subs: dict = {}  # request_id -> (name, Subscription)
        self._stop = None  # a pending commanded-stop Subscription, or None
        self._cursor = 0

    def set(self, name: str, value) -> None:
        """Update the worker's current value for ``name``."""
        self._values[name] = value

    def tick(self, step):
        """Drain control, service due subscriptions, evaluate the stop decision.

        Returns ``"commanded"`` if a ``control.stop`` fired this tick (the worker
        should stop at this safe point), else ``None``. The worker's *own* stop
        reasons (intrinsic completion, data-dependent) are separate — it simply
        leaves its loop and calls a stopped-emitting helper.
        """
        self._drain_control()
        self._service(step)
        # Tick-driven liveness beacon: step (progress) + consumed_seq (the
        # registration watermark, published only after draining/registering).
        self._ch.send(
            {"step": step, "consumed_seq": self._cursor}, topic="lifecycle.heartbeat"
        )
        if self._stop is not None and self._stop.tick(step=step, now=self._now()).fire:
            return "commanded"
        return None

    def stopped(self, reason: str = "completed", *, error=None, final_step=None) -> None:
        """Emit the cooperative dying breath (``lifecycle.stopped``).

        Its *existence* on the log = the run cleanly finished (§7). Broadcast
        (``request_id=None``) so every observer sees it.
        """
        body = {"reason": reason}
        if error is not None:
            body["error"] = error
        if final_step is not None:
            body["final_step"] = final_step
        self._ch.send(body, topic="lifecycle.stopped")

    # ----- internals -----

    def _drain_control(self) -> None:
        for e in self._ch.read(after=self._cursor, topics=["control.>"]):
            self._cursor = e.seq
            if e.topic == "control.subscribe":
                self._subs[e.request_id] = (
                    e.name,
                    Subscription(e.body, registered_at=self._now()),
                )
            elif e.topic == "control.unsubscribe":
                self._subs.pop(e.request_id, None)
            elif e.topic == "control.stop":
                self._stop = Subscription(e.body, registered_at=self._now())

    def _service(self, step) -> None:
        now = self._now()
        for request_id, (name, sub) in list(self._subs.items()):
            decision = sub.tick(step=step, now=now)
            if decision.fire:
                self._ch.send(
                    {"value": self._values.get(name), "step": step},
                    topic="value",
                    name=name,
                    request_id=request_id,
                )
            if decision.expired:
                del self._subs[request_id]
