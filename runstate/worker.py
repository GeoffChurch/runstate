"""The reference worker loop (docs/design-v0.2.md §6).

A worker-side runtime over a Channel. The worker reports *current values* (set
via ``set``); each ``tick(step)`` drains ``control.*``, registers/cancels
subscriptions, and services the due ones by emitting ``value`` envelopes
correlated by ``request_id``.

The clock is injectable (``now``) for deterministic tests.
"""

from __future__ import annotations

import time

from .handle import local_handle
from .schedule import Subscription, is_unsatisfiable, satisfied


class Worker:
    def __init__(self, channel, *, now=time.time):
        self._ch = channel
        self._now = now
        self._values: dict = {}
        self._subs: dict = {}  # request_id -> (name, Subscription)
        self._stop = None  # a pending commanded-stop Subscription, or None
        self._cursor = 0
        self._stopped = False
        self._stop_reason = None
        self._last_step = None
        # Attaching announces the worker and self-reports its liveness handle.
        self._ch.send(
            {"handle": local_handle(), "hostname": None, "attached_at": self._now()},
            topic="lifecycle.started",
        )

    def __enter__(self) -> "Worker":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is not None:
            self.stopped(reason="errored", error=str(exc), final_step=self._last_step)
        else:
            self.stopped(reason=self._stop_reason or "completed", final_step=self._last_step)
        return False  # never suppress exceptions

    def steps(self, total=None):
        """Drive the worker over a loop. Yields each step; after the body it
        ``tick``s (servicing the values set this iteration) and stops on a
        commanded stop. Pair with ``with Worker(...) as w`` so the dying breath
        (completed / commanded / errored) is emitted on exit.
        """
        step = 0
        while total is None or step < total:
            self._last_step = step
            yield step
            reason = self.tick(step)
            if reason is not None:
                self._stop_reason = reason
                return
            step += 1

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
        self._drain_control(step)
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
        (``request_id=None``) so every observer sees it. Idempotent — first
        writer wins: a second call (e.g. an explicit one plus the context-manager
        exit) is a no-op. So an explicit ``stopped()`` *commits* the terminal
        reason; if the block then raises, the exception still propagates to the
        caller but won't overwrite the logged reason (the log shows the committed
        one, not ``errored``).
        """
        if self._stopped:
            return
        self._stopped = True
        # present-nullable: always send error + final_step (null when N/A) so
        # consumers get a uniform key set.
        body = {"reason": reason, "error": error, "final_step": final_step}
        self._ch.send(body, topic="lifecycle.stopped")

    # ----- internals -----

    def _drain_control(self, step) -> None:
        for e in self._ch.read(after=self._cursor, topics=["control.>"]):
            self._cursor = e.seq
            # One bad control request must never be fatal: refuse it with a nak
            # and carry on. The reason distinguishes a structural problem
            # (malformed -- couldn't interpret the body) from a clean semantic
            # refusal (unsatisfiable) and an unknown verb (unsupported).
            try:
                self._handle_control(e, step)
            except Exception as exc:
                self._nak(e.request_id, "malformed", str(exc))

    def _handle_control(self, e, step) -> None:
        if e.topic == "control.subscribe":
            if e.request_id is None:
                self._nak(None, "malformed", "subscribe requires a request_id")
            elif is_unsatisfiable(e.body, step=step):
                self._nak(e.request_id, "unsatisfiable", "schedule can produce no fires")
            else:
                self._subs[e.request_id] = (
                    e.name,
                    Subscription(e.body, registered_at=self._now()),
                )
        elif e.topic == "control.unsubscribe":
            if e.request_id is None:
                self._nak(None, "malformed", "unsubscribe requires a request_id")
            else:
                self._subs.pop(e.request_id, None)
        elif e.topic == "control.stop":
            # a stop is one-shot: at most a `from` (when to stop). `every` is
            # inert and `until` could gate the stop from ever firing, so reject
            # them rather than silently honor a self-defeating request.
            if "every" in e.body or "until" in e.body:
                self._nak(e.request_id, "malformed", "control.stop takes only `from`")
            else:
                # Validate the `from` here (inside the drain guard) so a malformed
                # condition naks like a bad subscribe, instead of poisoning
                # self._stop and crashing at the unguarded tick-time eval site.
                from_ = e.body.get("from")
                if from_ is not None:
                    satisfied(from_, step=step, time_seconds=0.0, count=0)  # raises -> nak
                if is_unsatisfiable(e.body, step=step):
                    # e.g. a step-keyed stop on a stepless worker: it can never
                    # fire, so nak (parity with subscribe) rather than silently
                    # never auto-stopping.
                    self._nak(e.request_id, "unsatisfiable", "stop trigger can never fire")
                else:
                    self._stop = Subscription(e.body, registered_at=self._now())
        else:
            self._nak(e.request_id, "unsupported", f"unknown control topic {e.topic!r}")

    def _nak(self, request_id, reason: str, message: str) -> None:
        self._ch.send(
            {"reason": reason, "message": message},
            topic="lifecycle.nak",
            request_id=request_id,
        )

    def _service(self, step) -> None:
        now = self._now()
        for request_id, (name, sub) in list(self._subs.items()):
            # A schedule that's well-formed enough to register but blows up when
            # evaluated (e.g. an unknown condition deeper in the tree) is naked
            # and dropped here, not allowed to crash the loop.
            try:
                decision = sub.tick(step=step, now=now)
            except Exception as exc:
                self._nak(request_id, "malformed", str(exc))
                del self._subs[request_id]
                continue
            if decision.fire:
                value = self._values.get(name)
                try:
                    self._ch.send(
                        {"value": value, "step": step},
                        topic="value",
                        name=name,
                        request_id=request_id,
                    )
                except (TypeError, ValueError) as exc:
                    # A user value that won't serialize is the user's own bug,
                    # surfaced clearly at the point we try to report it (not the
                    # opaque json error) -- and fatal: a broken reporting path
                    # should stop the run, not silently drop the metric. The
                    # escape hatch is json_default on attach()/open_channel().
                    raise TypeError(
                        f"value for {name!r} ({type(value).__name__}) is not "
                        f"JSON-serializable; pass json_default to "
                        f"attach()/open_channel() to coerce it"
                    ) from exc
            if decision.expired:
                del self._subs[request_id]
