"""The subscription convention's condition-algebra (docs/design-v0.2.md §6).

A *Condition* is a threshold over the worker's coordinates ``(step, time, count)``
or an ``any``/``all`` of Conditions:

    Threshold := {"step": N} | {"time_seconds": S} | {"count": C}
    Condition := Threshold | {"any": [Condition, ...]} | {"all": [Condition, ...]}

``any`` fires when *any* child crosses (whichever first / OR); ``all`` when *all*
have crossed (whichever last / AND). Thresholds are ``>=`` comparisons, so every
condition is monotone (once true, stays true as coordinates advance). A
``step`` threshold is never satisfied for a stepless worker (``step is None``).
"""

from __future__ import annotations

from typing import NamedTuple


def satisfied(cond: dict, *, step=None, time_seconds: float = 0.0, count: int = 0) -> bool:
    """Is ``cond`` satisfied at coordinates (step, time_seconds, count)?"""
    if "any" in cond:
        return any(
            satisfied(c, step=step, time_seconds=time_seconds, count=count)
            for c in cond["any"]
        )
    if "all" in cond:
        return all(
            satisfied(c, step=step, time_seconds=time_seconds, count=count)
            for c in cond["all"]
        )
    if "step" in cond:
        return step is not None and step >= cond["step"]
    if "time_seconds" in cond:
        return time_seconds >= cond["time_seconds"]
    if "count" in cond:
        return count >= cond["count"]
    raise ValueError(f"unknown condition: {cond!r}")


class Decision(NamedTuple):
    fire: bool
    expired: bool


class Subscription:
    """A subscription's schedule + firing history.

    ``tick(step, now)`` is called at each of the worker's safe points and returns
    ``Decision(fire, expired)``. ``from``/``until`` evaluate over absolute
    coordinates (step, seconds-since-registration, fire-count); ``every``
    evaluates over deltas-since-the-last-fire. Absent ``every`` => one-shot.
    """

    def __init__(self, schedule: dict, *, registered_at: float):
        self.from_ = schedule.get("from")
        self.every = schedule.get("every")
        self.until = schedule.get("until")
        self.registered_at = registered_at
        self.count = 0
        self._last_step = None
        self._last_elapsed = None

    def tick(self, *, step, now: float) -> Decision:
        elapsed = now - self.registered_at
        # Pre-fire expiry gate (catches step/time `until`, and count `until`
        # once the budget is already spent).
        if self.until is not None and satisfied(
            self.until, step=step, time_seconds=elapsed, count=self.count
        ):
            return Decision(False, True)
        fire = self._triggers(step, elapsed)
        if fire:
            self.count += 1
            self._last_step = step
            self._last_elapsed = elapsed
        return Decision(fire, self._expired(step, elapsed))

    def _triggers(self, step, elapsed: float) -> bool:
        from_open = self.from_ is None or satisfied(
            self.from_, step=step, time_seconds=elapsed, count=self.count
        )
        if not from_open:
            return False
        if self.count == 0:
            return True  # first fire, at `from`
        if self.every is None:
            return False  # one-shot, already fired
        since_step = (
            step - self._last_step
            if (step is not None and self._last_step is not None)
            else None
        )
        since_elapsed = elapsed - self._last_elapsed
        return satisfied(self.every, step=since_step, time_seconds=since_elapsed, count=0)

    def _expired(self, step, elapsed: float) -> bool:
        # Post-fire: count-based `until` (the fire that reached the budget), and
        # one-shot subscriptions that have now fired.
        if self.until is not None and satisfied(
            self.until, step=step, time_seconds=elapsed, count=self.count
        ):
            return True
        return self.every is None and self.count >= 1


def is_unsatisfiable(schedule: dict, *, step) -> bool:
    """Can this schedule produce *zero* fires, determinable at registration?

    Two clean static cases (docs/design-v0.2.md §6): ``until`` already satisfied
    (the window is closed before any fire), or a step-keyed ``from`` on a
    *stepless* worker (it can never open). A merely-future or already-crossed
    step threshold is NOT unsatisfiable — by the clean ``>=`` semantics it fires
    at the next safe point where the threshold holds.
    """
    until = schedule.get("until")
    if until is not None and satisfied(until, step=step, time_seconds=0.0, count=0):
        return True
    if step is None:
        from_ = schedule.get("from")
        if from_ is not None and not _satisfiable_stepless(from_):
            return True
    return False


def _satisfiable_stepless(cond: dict) -> bool:
    """Could ``cond`` ever be satisfied when the worker has no step?"""
    if "any" in cond:
        return any(_satisfiable_stepless(c) for c in cond["any"])
    if "all" in cond:
        return all(_satisfiable_stepless(c) for c in cond["all"])
    if "step" in cond:
        return False
    return True  # time_seconds / count become satisfiable as they grow
