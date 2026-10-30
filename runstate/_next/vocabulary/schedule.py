"""The condition algebra over counts (docs/specs/commits-and-lineage.md §3,
subscription-v0.4).

A *Condition* is an atom over the coordinates at a node, or an ``any``/``all`` of
Conditions:

    Atom      := {"stream": X, "n": N} | {"commits": N} | {"time_seconds": S}
               | {"count": C}
    Condition := Atom | {"any": [Condition, ...]} | {"all": [Condition, ...]}

``{stream, n}`` and ``{commits: n}`` are the **lineage atoms**: ``count(X, node)``,
the number of X's elements on the lineage up to and including the node, for an
eager stream X or ``Commits()`` (§2, "The coordinate"). ``time_seconds`` is the
wall clock since the claim registered the request, and ``count`` the claim's
firings of it, valid only in ``until``. Every atom is a ``>=`` threshold, so every
condition is monotone along a lineage. ``any`` holds when any child does, ``all``
when all do. The step atom of 0.3.0 is removed.

A schedule is ``{from?, every?, until?}``, each a Condition, kept as the wire dict;
a stop trigger is ``{from?}``. A schedule with any time or count atom is a
**lease**, which lives per claim; every other one is **durable**, and lives per
lineage (§2, "Requests").
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from collections.abc import Set as AbstractSet
from typing import Any, NamedTuple

from .coordinate import Commits

Condition = dict[str, Any]
"""The wire dict of a condition or a schedule (subscription-v0.4): an atom, an
``any``/``all`` of conditions, or ``{from?, every?, until?}``. Kept as the wire dict
by design: ``malformed_schedule`` checks it once, at the drain."""

# subscription-v0.4's ``Count``: the lineage atoms' n is from 0 to 2^53 - 1.
_MAX_COUNT = 2**53 - 1


class Coordinates(NamedTuple):
    """The counts at one node (§3, "Evaluation"), its pending values included when
    it is the node being written: evaluated at the previous node instead, ``from
    {commits: 1}`` would fire one commit late."""

    streams: Mapping[str, int]
    """``count(X, node)`` per eager stream X. A stream with no element on the
    lineage counts 0, so it may be absent: empty is 0, with no sentinel (§2)."""
    commits: int
    """``count(Commits(), node)``: the iterations committed."""

    def of(self, x: str | Commits) -> int:
        """``count(x, node)``, for an eager stream name or ``Commits()``."""
        if isinstance(x, Commits):
            return self.commits
        return self.streams.get(x, 0)


def _holds(cond: Condition, atom: Callable[[Condition], bool]) -> bool:
    """``cond`` under ``atom``, the judgment of one atom: ``any`` and ``all`` are
    the same combinators for an absolute threshold and for a delta."""
    if "any" in cond:
        return any(_holds(c, atom) for c in cond["any"])
    if "all" in cond:
        return all(_holds(c, atom) for c in cond["all"])
    return atom(cond)


def satisfied(
    cond: Condition, *, at: Coordinates, time_seconds: float, count: int
) -> bool:
    """Does ``cond`` hold at the node whose counts are ``at``, ``time_seconds``
    after registration, with ``count`` firings? (§3: ``from`` and ``until`` hold
    once ``count(X, node) >= n``.)"""

    def atom(a: Condition) -> bool:
        if "stream" in a:
            return bool(at.of(a["stream"]) >= a["n"])
        if "commits" in a:
            return bool(at.commits >= a["commits"])
        if "time_seconds" in a:
            return bool(time_seconds >= a["time_seconds"])
        if "count" in a:
            return bool(count >= a["count"])
        raise ValueError(f"unknown condition: {a!r}")

    return _holds(cond, atom)


class Decision(NamedTuple):
    """A subscription's evaluation at one node: whether it fires there, and whether
    its schedule ends there (the node's ``fired`` and ``expired``)."""

    fire: bool
    expired: bool


class Subscription:
    """A registered schedule (§3, "Evaluation"). The claim holds ``registered_at``,
    the fire count and the time of the last firing; the lineage supplies the count
    baselines, which the caller passes to each ``evaluate``.

    ``target`` is the source it samples, its envelope ``name``, or ``None`` for a
    pure demand, which has a lifetime but no schedule and never fires. It is fixed
    at registration (§3: a re-send that changes it is refused), so it is the
    constructor's, and the constructor refuses exactly what the Worker refuses as
    ``malformed``: ``malformed_schedule(schedule, pure_demand=target is None)``.
    Which sources hold a value varies per node, so that is ``evaluate``'s.

    A re-send that the worker accepts registers afresh (§3, subscription-v0.4): a
    new ``Subscription``, so a lease's time atoms count from the re-send, its fire
    count returns to 0, and its time baseline is dropped, while its lineage
    baselines, read on the lineage, stay. ``fires`` is assignable because a durable
    subscription's is the lineage's, which the Worker rebuilds at every position
    change (§4)."""

    def __init__(
        self, schedule: Condition, *, registered_at: float, target: str | None
    ) -> None:
        problem = malformed_schedule(schedule, pure_demand=target is None)
        if problem is not None:
            raise ValueError(f"malformed schedule: {problem}")
        self.from_: Condition | None = schedule.get("from")
        self.every: Condition | None = schedule.get("every")
        self.until: Condition | None = schedule.get("until")
        self.registered_at = registered_at
        self.lease = is_lease(schedule)
        self.fires = 0
        self._fired_at: float | None = None
        self._target = target

    @property
    def target(self) -> str | None:
        """The source it samples, or ``None`` for a pure demand; fixed."""
        return self._target

    def evaluate(
        self,
        *,
        at: Coordinates,
        baseline: Coordinates | None,
        now: float,
        valued: AbstractSet[str],
    ) -> Decision:
        """Evaluate the schedule once, at the node being written.

        - ``at``: the counts at the node, its pending values included.
        - ``baseline``: the counts at the newest node on the lineage whose ``fired``
          names this subscription, or ``None`` when no node there does. It is the
          baseline of every lineage atom in ``every``, for a lease as for a durable
          subscription (review §12, item 14), so after a rewind below the last
          firing, ``every`` counts from the newest firing left on the lineage.
        - ``now``: the writer's clock. ``time_seconds`` in ``from`` and ``until``
          reads ``now - registered_at``; in ``every`` it reads ``now`` minus the
          time of this claim's last firing, which a rewind does not move.
        - ``valued``: the sources whose registers hold a value at this node. A
          subscription can fire only when its target is one of them: a source with
          no value yet does not fire, and waits for the first ``set`` (§3). A pure
          demand never consults it, as a schedule with no time atom never consults
          ``now``.

        ``until`` first: where it holds, the subscription expires and fires
        nothing. Otherwise it fires once ``from`` holds and ``every``'s delta rule
        does: ``count(now) - count(at the last firing) >= n``, not rounded to a
        multiple, with no interpolation inside a jump. An atom with no baseline
        holds, so the first firing is immediate. One that cannot fire here (a pure
        demand, or a target with no value) evaluates only its ``until``: it may
        expire, never fires, and moves neither ``fires`` nor the time baseline, so
        its first firing once it can sample is still immediate. A firing advances ``fires`` and
        the time baseline. It expires at the node where it fires if it is a
        one-shot (no ``every``), or if its ``until`` holds once this firing is
        counted: ``{count: n}`` counts the firings up to and including the node,
        as ``count(X, node)`` counts elements, so the nth firing ends the
        schedule, as a one-shot's one firing does."""
        elapsed = now - self.registered_at
        if self.until is not None and satisfied(
            self.until, at=at, time_seconds=elapsed, count=self.fires
        ):
            return Decision(fire=False, expired=True)
        if self._target is None or self._target not in valued:
            return Decision(fire=False, expired=False)
        if self.from_ is not None and not satisfied(
            self.from_, at=at, time_seconds=elapsed, count=self.fires
        ):
            return Decision(fire=False, expired=False)
        if self.every is not None and not self._due(self.every, at, baseline, now):
            return Decision(fire=False, expired=False)
        self.fires += 1
        self._fired_at = now
        expired = self.every is None or (
            self.until is not None
            and satisfied(self.until, at=at, time_seconds=elapsed, count=self.fires)
        )
        return Decision(fire=True, expired=expired)

    def _due(
        self,
        every: Condition,
        at: Coordinates,
        baseline: Coordinates | None,
        now: float,
    ) -> bool:
        """``every``'s delta rule, atom by atom against each atom's own baseline;
        ``count`` is no ``every`` atom (the grammar, checked at construction)."""
        fired_at = self._fired_at

        def atom(a: Condition) -> bool:
            if "stream" in a:
                x: str | Commits = a["stream"]
                n = a["n"]
            elif "commits" in a:
                x, n = Commits(), a["commits"]
            else:
                return fired_at is None or now - fired_at >= a["time_seconds"]
            return baseline is None or at.of(x) - baseline.of(x) >= n

        return _holds(every, atom)


# ----- the class of a schedule ------------------------------------------------------


def _references_atom(schedule: Condition, keys: frozenset[str]) -> bool:
    """Does the schedule name an atom in ``keys`` anywhere in ``from``, ``every``
    or ``until``, through ``any`` and ``all``?

    Total over any body: a reader classifies the subscriptions on a log before a
    worker has refused a malformed one, and a junk record of a dead past must not
    poison a fold for good, since an append-only log cannot retract it. A body it
    cannot parse references nothing; a ``nak`` answers it."""

    def hit(cond: object) -> bool:
        if not isinstance(cond, dict):
            return False
        for combinator in ("any", "all"):
            children = cond.get(combinator)
            if isinstance(children, list):
                return any(hit(c) for c in children)
        return bool(keys & cond.keys())

    return any(hit(schedule.get(slot)) for slot in ("from", "every", "until"))


def is_lease(schedule: Condition) -> bool:
    """Is the schedule a **lease** (§2, §3)? True if and only if it has a
    ``time_seconds`` or a ``count`` atom anywhere: both are relative to the claim
    that registered it (seconds since, firings since), so it lives per claim, is
    bound to it by ``lifecycle.bound``, and is void once that claim has ended.
    Every other schedule's atoms are lineage coordinates, and it is durable."""
    return _references_atom(schedule, frozenset({"time_seconds", "count"}))


def references_time(schedule: Condition) -> bool:
    """Does the schedule need an epoch: a ``time_seconds`` atom anywhere? Not the
    lease question, which a count-only schedule answers yes and this one no."""
    return _references_atom(schedule, frozenset({"time_seconds"}))


# ----- the grammar ------------------------------------------------------------------


def malformed_schedule(body: Condition, *, pure_demand: bool) -> str | None:
    """Why ``body`` fails subscription-v0.4's ``Schedule``, or ``None`` if it
    conforms: the structural check, spelled in Python so the worker needs no
    jsonschema. Keys within ``from``/``every``/``until``, each a Condition, with
    ``{count: n}`` only under ``until``.

    With ``pure_demand`` (a subscription whose envelope ``name`` is null), also a
    ``from``, an ``every`` or a ``{count: n}`` atom anywhere: a pure demand has a
    lifetime, not a schedule, and those schedule samples it cannot take (§3; review
    §12, item 14). A non-``None`` reason is the worker's ``malformed`` nak message,
    decided before any refusal as ``unsatisfiable``; a ``None`` guarantees the
    schedule evaluates cleanly at every node."""
    for key, value in body.items():
        if key not in ("from", "every", "until"):
            return f"unknown schedule key {key!r} (a schedule takes from/every/until)"
        if pure_demand and key != "until":
            return f"a pure demand takes only an `until`, not `{key}`"
        if key != "until":
            no_count: str | None = "count thresholds are valid only in `until`"
        elif pure_demand:
            no_count = "a pure demand takes no count threshold: it never fires"
        else:
            no_count = None
        problem = _malformed_condition(value, no_count=no_count, path=key)
        if problem is not None:
            return problem
    return None


def malformed_stop_trigger(body: Condition) -> str | None:
    """Why ``body`` fails subscription-v0.4's ``StopTrigger``, or ``None`` if it
    conforms: at most a ``from`` with no ``{count: n}``. A stop is one-shot, so an
    ``every`` is inert and an ``until`` could gate it from ever firing."""
    for key, value in body.items():
        if key != "from":
            return "control.stop takes only `from`"
        problem = _malformed_condition(
            value, no_count="count thresholds are valid only in `until`", path=key
        )
        if problem is not None:
            return problem
    return None


def _malformed_condition(
    cond: object, *, no_count: str | None, path: str
) -> str | None:
    """subscription-v0.4's ``Condition`` (``no_count`` is the refusal of a count
    atom) or ``UntilCondition`` (``no_count`` is None): an atom, or a non-empty
    ``any``/``all`` of conditions. ``path`` locates the offense in the reason.

    A whole-number float is no integer here, though JSON Schema admits it, and
    ``bool`` is no number, as in ``payloads``."""
    if cond is None:
        return f"`{path}` is null (omit the key instead)"
    if not isinstance(cond, dict):
        return f"`{path}` must be a condition object, got {type(cond).__name__}"
    if "stream" in cond or "n" in cond:
        if set(cond) != {"stream", "n"}:
            return f"`{path}` must be {{stream, n}} with exactly those keys, got {list(cond)}"
        stream, n = cond["stream"], cond["n"]
        if not (isinstance(stream, str) and stream):
            return f"`{path}.stream` must be a non-empty string, got {stream!r}"
        return _malformed_count(f"{path}.n", n, maximum=_MAX_COUNT)
    if len(cond) != 1:
        keys = "stream/commits/time_seconds/any/all" + ("" if no_count else "/count")
        return f"`{path}` must carry exactly one condition ({keys}), got {list(cond)}"
    ((key, value),) = cond.items()
    if key in ("any", "all"):
        if not isinstance(value, list) or not value:
            return f"`{path}.{key}` must be a non-empty list of conditions"
        for i, child in enumerate(value):
            problem = _malformed_condition(
                child, no_count=no_count, path=f"{path}.{key}[{i}]"
            )
            if problem is not None:
                return problem
        return None
    if key == "commits":
        return _malformed_count(f"{path}.commits", value, maximum=_MAX_COUNT)
    if key == "time_seconds":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            return f"`{path}.time_seconds` must be a number >= 0, got {value!r}"
        return None
    if key == "count":
        if no_count is not None:
            return no_count
        return _malformed_count(f"{path}.count", value, maximum=None)
    return f"`{path}` has unknown condition key {key!r}"


def _malformed_count(path: str, n: object, *, maximum: int | None) -> str | None:
    if isinstance(n, bool) or not isinstance(n, int) or n < 0:
        return f"`{path}` must be an integer >= 0, got {n!r}"
    if maximum is not None and n > maximum:
        return f"`{path}` must be at most 2^53 - 1, got {n!r}"
    return None


# ----- the refusals -----------------------------------------------------------------


class _Corner(NamedTuple):
    """The least coordinates at which a conjunctive condition holds."""

    streams: dict[str, int]
    commits: int
    time: float


def unsatisfiable(schedule: Condition, *, sources: frozenset[str]) -> str | None:
    """Why the claim whose declared sources are ``sources`` refuses a well-formed
    ``schedule`` as ``unsatisfiable``, or ``None`` (§3, "Refusals"):

    - a progress atom naming a declared source, anywhere: a source's samples
      exist only where someone asked, so its count depends on who asked;
    - an empty window: a ``from`` that implies the ``until``, so the subscription
      can never fire. 0.3.0's corner check, restated in counts: ``until`` holding
      at the least coordinates where a conjunctive ``from`` holds holds wherever
      ``from`` does, since every condition is monotone. On one X, that is
      ``until``'s n at most ``from``'s; across two it is speed-contingent and not
      refused. A ``from`` with an ``any`` has many corners, and is not checked
      rather than normalized. It does not depend on the position. An absent
      ``from`` reads as ``from`` 0 on each X, its least coordinates the root's,
      since a 0 in ``from`` re-spells an absent one: ``{until: {commits: 0}}`` is
      refused as ``{from: {commits: 0}, until: {commits: 0}}`` is, so one schedule
      gets one answer (amended 2026-10-30).

    An ``until`` that holds at registration but not at the root is not refused
    (review §12, item 14): whether it holds depends on the position, and it
    expires at the next node.
    The third refusal, a subscription targeting a name the claim does not declare a
    source, is the Worker's, since it reads the envelope's ``name``."""
    named = sorted(_streams_named(schedule) & sources)
    if named:
        return (
            f"a progress atom names the declared source(s) {named}: a source's"
            " samples exist only where asked, so they are never counted"
        )
    from_, until = schedule.get("from"), schedule.get("until")
    if until is not None:
        corner = _Corner({}, 0, 0.0) if from_ is None else _corner(from_)
        if corner is not None and satisfied(
            until,
            at=Coordinates(streams=corner.streams, commits=corner.commits),
            time_seconds=corner.time,
            count=0,
        ):
            return "an empty window: `from` implies `until`, so it can never fire"
    return None


def _streams_named(schedule: Condition) -> set[str]:
    """The streams the well-formed ``schedule``'s ``{stream, n}`` atoms name."""
    named: set[str] = set()

    def walk(cond: Condition) -> None:
        for combinator in ("any", "all"):
            if combinator in cond:
                for c in cond[combinator]:
                    walk(c)
                return
        if "stream" in cond:
            named.add(cond["stream"])

    for slot in ("from", "every", "until"):
        if slot in schedule:
            walk(schedule[slot])
    return named


def _corner(cond: Condition) -> _Corner | None:
    """The least coordinates where the well-formed, count-free ``cond`` holds, or
    ``None`` for a condition with an ``any``, which has many. ``all`` takes the
    per-coordinate maximum of its children's corners."""
    if "any" in cond:
        return None
    if "all" in cond:
        streams: dict[str, int] = {}
        commits, time = 0, 0.0
        for c in cond["all"]:
            sub = _corner(c)
            if sub is None:
                return None
            for x, n in sub.streams.items():
                streams[x] = max(streams.get(x, 0), n)
            commits, time = max(commits, sub.commits), max(time, sub.time)
        return _Corner(streams, commits, time)
    if "stream" in cond:
        return _Corner({cond["stream"]: cond["n"]}, 0, 0.0)
    if "commits" in cond:
        return _Corner({}, cond["commits"], 0.0)
    return _Corner({}, 0, cond["time_seconds"])
