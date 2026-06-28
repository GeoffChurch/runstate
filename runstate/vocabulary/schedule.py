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


def satisfied(cond: dict, *, step: int | None = None, time_seconds: float = 0.0, count: int = 0) -> bool:
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
        return bool(time_seconds >= cond["time_seconds"])
    if "count" in cond:
        return bool(count >= cond["count"])
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
        self._last_step: int | None = None
        self._last_elapsed: float | None = None

    def tick(self, *, step: int | None, now: float) -> Decision:
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

    def _triggers(self, step: int | None, elapsed: float) -> bool:
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
        assert self._last_elapsed is not None  # set with count on the first fire (count>0 here)
        since_elapsed = elapsed - self._last_elapsed
        return satisfied(self.every, step=since_step, time_seconds=since_elapsed, count=0)

    def _expired(self, step: int | None, elapsed: float) -> bool:
        # Post-fire: count-based `until` (the fire that reached the budget), and
        # one-shot subscriptions that have now fired.
        if self.until is not None and satisfied(
            self.until, step=step, time_seconds=elapsed, count=self.count
        ):
            return True
        if self.every is None and self.count >= 1:
            return True
        # The enforced invariant (specs/service-worker.md): registered iff a
        # future fire is possible. After a fire, an `every` that can never be
        # satisfied again on this worker's coordinates -- a step-only `every`
        # on a stepless worker -- expires instead of pinning forever.
        return (
            self.every is not None
            and self.count >= 1
            and step is None
            and not _satisfiable_stepless(self.every)
        )


def is_unsatisfiable(schedule: dict, *, step: int | None) -> bool:
    """Can this schedule produce *zero* fires, determinable at registration?

    Static cases (docs/design-v0.2.md §6):
    - ``until`` already satisfied at the current coordinates (window closed);
    - a step-keyed ``from`` on a *stepless* worker (it can never open);
    - an empty window — ``from`` ⟹ ``until`` (the gate opens no earlier than it
      closes). Detected for a *conjunctive* ``from`` (its single minimal corner)
      by checking whether ``until`` already holds there; a ``from`` containing an
      ``any`` has many corners (a potential exponential), so we punt on it and it
      degrades to a dynamic never-fire rather than reach for a normal form.

    A merely-future or already-crossed step threshold is NOT unsatisfiable — by
    the clean ``>=`` semantics it fires at the next safe point where it holds.
    """
    until = schedule.get("until")
    if until is not None and satisfied(until, step=step, time_seconds=0.0, count=0):
        return True
    from_ = schedule.get("from")
    if step is None and from_ is not None and not _satisfiable_stepless(from_):
        return True
    if until is not None and from_ is not None:
        corner = _conjunctive_corner(from_)
        if corner is not None:
            # On a stepless worker, `from` provably has no step atom to reach
            # here (else the stepless check above returned), so the step corner
            # is meaningless -- pass step=None so a `step` atom in `until`
            # evaluates as it does everywhere else (never satisfied), instead of
            # spuriously matching at corner step 0.
            corner_step = corner[0] if step is not None else None
            if satisfied(until, step=corner_step, time_seconds=corner[1], count=0):
                return True
    return False


def _conjunctive_corner(cond: dict) -> tuple[int, float] | None:
    """The single minimal ``(step, time)`` corner of a conjunctive condition, or
    None if it contains an ``any`` (many corners) or isn't corner-representable.
    Sound for the from ⟹ until check: ``until`` holding at this corner means it
    holds on all of ``from``'s up-set (every condition is monotone)."""
    if "any" in cond:
        return None
    if "all" in cond:
        step, time = 0, 0.0
        for c in cond["all"]:
            sub = _conjunctive_corner(c)
            if sub is None:
                return None
            step, time = max(step, sub[0]), max(time, sub[1])
        return (step, time)
    if "step" in cond:
        return (cond["step"], 0.0)
    if "time_seconds" in cond:
        return (0, cond["time_seconds"])
    return None  # count (not a `from` key) or unknown -> punt


def references_time(schedule: dict) -> bool:
    """Does the schedule contain a ``time_seconds`` atom anywhere in
    ``from``/``every``/``until``? The episode-scoping predicate
    (specs/time-lease-boundary.md): a time atom's meaning — seconds since
    registration — is episode-local, so any schedule containing one is a
    *lease*, scoped to a single episode (blunt-but-crisp: no per-atom
    carve-outs). Tolerant: an unparseable schedule is NOT time-referencing
    (the worker naks it, which answers it)."""
    def has_time(cond: object) -> bool:
        if not isinstance(cond, dict):
            return False
        if "any" in cond and isinstance(cond["any"], list):
            return any(has_time(c) for c in cond["any"])
        if "all" in cond and isinstance(cond["all"], list):
            return any(has_time(c) for c in cond["all"])
        return "time_seconds" in cond
    if not isinstance(schedule, dict):
        return False
    return any(
        has_time(schedule.get(k))
        for k in ("from", "every", "until")
        if schedule.get(k) is not None
    )


def contains_count(cond: dict) -> bool:
    """Does ``cond`` contain a ``count`` atom anywhere? The schema already
    forbids count outside ``until`` (it is grammatical only in ``UntilTerm``);
    the worker enforces the same rule as defense-in-depth, because a count
    atom inside ``from``/``every`` is a circular gate (count advances only on
    fires) -- the accidental pure pin (specs/service-worker.md)."""
    if "any" in cond:
        return any(contains_count(c) for c in cond["any"])
    if "all" in cond:
        return any(contains_count(c) for c in cond["all"])
    return "count" in cond


def _satisfiable_stepless(cond: dict) -> bool:
    """Could ``cond`` ever be satisfied when the worker has no step?"""
    if "any" in cond:
        return any(_satisfiable_stepless(c) for c in cond["any"])
    if "all" in cond:
        return all(_satisfiable_stepless(c) for c in cond["all"])
    if "step" in cond:
        return False
    return True  # time_seconds / count become satisfiable as they grow
