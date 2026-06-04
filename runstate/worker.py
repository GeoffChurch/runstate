"""The reference worker loop (docs/design-v0.2.md §6).

A worker-side runtime over a Channel. The worker reports *current values* (set
via ``set``); each ``tick(step)`` drains ``control.*``, registers/cancels
subscriptions, and services the due ones by emitting ``value`` envelopes
correlated by ``request_id``.

The clock is injectable (``now``) for deterministic tests.
"""

from __future__ import annotations

import time
from dataclasses import asdict

from .vocabulary.payloads import Heartbeat, Nak, Started, Stopped, Value
from .vocabulary.handle import local_handle
from .vocabulary.schedule import Subscription, is_unsatisfiable, satisfied
from .liveness import live_episode


class Worker:
    def __init__(self, channel, *, now=time.time):
        self._ch = channel
        self._now = now
        self._values: dict = {}
        self._subs: dict = {}  # request_id -> (name, Subscription)
        self._stop = None  # a pending commanded-stop Subscription, or None
        self._cursor = 0
        self._stopped = False
        self._last_step = None
        # Attaching CAS-claims the episode: a worker that loses (a live episode
        # already exists) sets _lost and exits without acting on the channel.
        self._lost = False
        while True:
            envs = self._ch.read()
            last = envs[-1].seq if envs else 0
            if live_episode(self._ch) is not None:
                self._lost = True
                break
            if self._ch.send(
                asdict(Started(handle=local_handle(), hostname=None, attached_at=self._now())),
                topic="lifecycle.started",
                expected_seq=last,
            ) is not None:
                break  # won the claim

    @property
    def claimed(self) -> bool:
        """True if this worker won the episode claim; False if it lost."""
        return not self._lost

    def __enter__(self) -> "Worker":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if self._lost:
            return False
        if exc_type is not None:
            self.stopped(error=str(exc), final_step=self._last_step)
        else:
            self.stopped(final_step=self._last_step)   # default: no claim -> preempted
        return False

    def steps(self, total=None, *, start=0):
        """Drive the worker over a loop. Yields each step; after the body it
        ``tick``s (servicing the values set this iteration) and stops on a
        commanded stop. Pair with ``with Worker(...) as w`` so the dying breath
        (completed / commanded / errored) is emitted on exit.

        ``start`` is keyword-only (default 0). Pass ``start=k`` to resume a run
        from checkpoint step ``k`` — steps are then emitted as ``k, k+1, …``
        (run-absolute), and ``lifecycle.stopped`` records the correct
        ``final_step``.
        """
        if self._lost:
            return
        step = start
        while total is None or step < total:
            self._last_step = step
            yield step
            if self.tick(step):    # truthy -> a control.stop fired; stop at this safe point
                return
            step += 1

    def set(self, name: str, value) -> None:
        """Update the worker's current value for ``name``."""
        self._values[name] = value

    def tick(self, step) -> bool:
        """Drain control, service due subscriptions, beacon a heartbeat. Returns
        True iff a control.stop fired this tick (stop at this safe point), else
        False. The worker's own *completion* is a separate opt-in claim
        (``w.stopped(completed=True)``); a commanded stop carries no reason —
        commandedness is recoverable from the control.stop on the log."""
        self._drain_control(step)
        self._service(step)
        self._ch.send(asdict(Heartbeat(step=step, consumed_seq=self._cursor)),
                      topic="lifecycle.heartbeat")
        return self._stop is not None and self._stop.tick(step=step, now=self._now()).fire

    def stopped(self, *, completed: bool = False, error=None, final_step=None) -> None:
        """Emit the cooperative dying breath (lifecycle.stopped). Its existence = a
        clean, resumable halt. ``completed=True`` is the opt-in completion claim; the
        default (completed=False, no error) projects to ``preempted``; an ``error``
        projects to ``errored``. Idempotent — first writer wins."""
        if self._stopped:
            return
        self._stopped = True
        body = asdict(Stopped(completed=completed, error=error, final_step=final_step))
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
            asdict(Nak(reason=reason, message=message)),
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
                        asdict(Value(value=value, step=step, t=self._now())),
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
