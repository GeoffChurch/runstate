"""The condition algebra over counts (docs/specs/commits-and-lineage.md §3,
subscription-v0.4).

THE WIRE. A condition is an atom over the coordinates at a node, or an
``any``/``all`` of conditions:

    Atom      := {"stream": X, "n": N} | {"commits": N} | {"time_seconds": S}
               | {"count": C}
    Condition := Atom | {"any": [Condition, ...]} | {"all": [Condition, ...]}

A schedule is ``{from?, every?, until?}``, each a condition, with ``count`` only in
``until``; a stop trigger is ``{from?}``. The step atom of 0.3.0 is removed.

THE TERM. The wire dict is checked and converted once, at the boundary, by
``parse_schedule`` and ``parse_stop_trigger``, into a typed ``Term`` and a
``Schedule``; everything past the parse matches on types, never on string keys.

- ``Target`` (``coordinate.py``) is the **lineage atom** ``{stream, n}`` or
  ``{commits: n}``: ``count(X, node)``, the number of X's elements on the lineage up
  to and including the node, for an eager stream X or ``Commits()`` (§2, "The
  coordinate"), at least ``n``.
- ``Elapsed`` is ``time_seconds``: the wall clock since the claim registered the
  request. ``Fired`` is ``count``: the claim's firings of it.
- ``AnyOf`` holds when any child does, ``AllOf`` when all do.

Every atom is a ``>=`` threshold, so every term is monotone along a lineage. A
schedule with any ``Elapsed`` or ``Fired`` atom is a **lease**, which lives per
claim; every other one is **durable**, and lives per lineage (§2, "Requests").

The parse refuses what subscription-v0.4 refuses, with two exceptions, as in
``payloads``: a whole-number float such as ``3.0`` is no integer here, and NaN is
no number, though JSON Schema admits both. No writer of a 0.4.0 log writes either.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, NamedTuple, assert_never

from .coordinate import _MAX_COUNT, Commits, Target

Condition = dict[str, Any]
"""The wire dict of a schedule or a stop trigger (subscription-v0.4), as a
``control.subscribe`` or ``control.stop`` body carries it. Only the boundary takes
it: ``parse_schedule`` and ``parse_stop_trigger``, and the functions that send or
replay a wire schedule, which parse it at their own boundary."""


class MalformedSchedule(ValueError):
    """A body subscription-v0.4 refuses, or a pure demand with a ``from``, an
    ``every`` or a ``count`` atom. Its message names the offending key by its path
    in the body (``from.any[1].time_seconds``), and is the worker's ``malformed``
    nak message."""


# ----- the term ---------------------------------------------------------------------


@dataclass(frozen=True)
class Elapsed:
    """``{time_seconds: s}``: holds once ``s`` seconds have passed since the claim
    registered the request (in ``every``: since its last firing)."""

    seconds: float

    def __post_init__(self) -> None:
        s = self.seconds
        if isinstance(s, bool) or not isinstance(s, (int, float)) or not s >= 0:
            raise ValueError(f"seconds must be a number >= 0, got {s!r}")


@dataclass(frozen=True)
class Fired:
    """``{count: n}``: holds once the claim has fired the subscription ``n`` times,
    this node's firing included. Only in ``until``: elsewhere it is circular, since
    firings advance only by firing."""

    n: int

    def __post_init__(self) -> None:
        n = self.n
        if isinstance(n, bool) or not isinstance(n, int) or n < 0:
            raise ValueError(f"n must be an integer >= 0, got {n!r}")


@dataclass(frozen=True)
class AnyOf:
    """Holds when any of its terms does: whichever crosses first."""

    terms: tuple[Term, ...]

    def __post_init__(self) -> None:
        if not self.terms:
            raise ValueError("any takes at least one term")


@dataclass(frozen=True)
class AllOf:
    """Holds when all of its terms do: whichever crosses last."""

    terms: tuple[Term, ...]

    def __post_init__(self) -> None:
        if not self.terms:
            raise ValueError("all takes at least one term")


Atom = Target | Elapsed | Fired
Term = Target | Elapsed | Fired | AnyOf | AllOf


@dataclass(frozen=True)
class Schedule:
    """A subscription's schedule (§3): ``from_`` opens it, ``every`` repeats it
    (absent: a one-shot), ``until`` ends it. A ``Fired`` atom only in ``until``."""

    from_: Term | None
    every: Term | None
    until: Term | None

    def __post_init__(self) -> None:
        for slot, term in (("from", self.from_), ("every", self.every)):
            if term is not None and any(isinstance(a, Fired) for a in _atoms(term)):
                raise MalformedSchedule(f"`{slot}`: count is valid only in `until`")

    def atoms(self) -> Iterator[Atom]:
        """Every atom of every slot."""
        for term in (self.from_, self.every, self.until):
            if term is not None:
                yield from _atoms(term)


def _atoms(term: Term) -> Iterator[Atom]:
    """The leaves of ``term``."""
    match term:
        case AnyOf(terms=terms) | AllOf(terms=terms):
            for t in terms:
                yield from _atoms(t)
        case Target() | Elapsed() | Fired():
            yield term
        case _:
            assert_never(term)


# ----- evaluation -------------------------------------------------------------------


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


def _holds(term: Term, atom: Callable[[Atom], bool]) -> bool:
    """``term`` under ``atom``, the judgment of one leaf: ``any`` and ``all`` are the
    same combinators for an absolute threshold and for a delta."""
    match term:
        case AnyOf(terms=terms):
            return any(_holds(t, atom) for t in terms)
        case AllOf(terms=terms):
            return all(_holds(t, atom) for t in terms)
        case Target() | Elapsed() | Fired():
            return atom(term)
        case _:
            assert_never(term)


def satisfied(term: Term, *, at: Coordinates, time_seconds: float, count: int) -> bool:
    """Does ``term`` hold at the node whose counts are ``at``, ``time_seconds``
    after registration, with ``count`` firings? (§3: ``from`` and ``until`` hold
    once ``count(X, node) >= n``.)"""

    def atom(a: Atom) -> bool:
        match a:
            case Target(x=x, n=n):
                return at.of(x) >= n
            case Elapsed(seconds=s):
                return time_seconds >= s
            case Fired(n=n):
                return count >= n
            case _:
                assert_never(a)

    return _holds(term, atom)


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
    constructor's; which sources hold a value varies per node, so that is
    ``evaluate``'s. The schedule comes parsed: ``parse_schedule(body,
    pure_demand=target is None)`` refuses at the drain what the Worker naks as
    ``malformed``.

    A re-send that the worker accepts registers afresh (§3, subscription-v0.4): a
    new ``Subscription``, so a lease's time atoms count from the re-send, its fire
    count returns to 0, and its time baseline is dropped, while its lineage
    baselines, read on the lineage, stay.

    ``fires`` and the time baseline are the claim's, and only a lease reads them:
    an ``Elapsed`` or a ``Fired`` atom is what makes a schedule a lease, so a durable
    subscription, whose state is all the lineage's, reads neither, and nothing
    rebuilds them."""

    def __init__(
        self, schedule: Schedule, *, registered_at: float, target: str | None
    ) -> None:
        self._schedule = schedule
        self._registered_at = registered_at
        self._target = target
        self._lease = is_lease(schedule)
        self._fires = 0
        self._fired_at: float | None = None

    @property
    def schedule(self) -> Schedule:
        return self._schedule

    @property
    def target(self) -> str | None:
        """The source it samples, or ``None`` for a pure demand; fixed."""
        return self._target

    @property
    def registered_at(self) -> float:
        """The claim's clock at registration, which ``from`` and ``until``'s
        ``Elapsed`` atoms count from."""
        return self._registered_at

    @property
    def lease(self) -> bool:
        """``is_lease(schedule)``: whether it lives per claim."""
        return self._lease

    @property
    def fires(self) -> int:
        """The claim's firings, which a ``Fired`` atom reads."""
        return self._fires

    def evaluate(
        self,
        *,
        at: Coordinates,
        baseline: Coordinates | None,
        now: float,
        valued: AbstractSet[str],
    ) -> Decision:
        """Evaluate the schedule at the node being written. **The call is the
        firing:** a ``Decision`` that fires has already advanced ``fires`` and the
        time baseline, so two calls at one node count two firings, and a caller
        that retries a write reuses the ``Decision`` rather than calling again.

        - ``at``: the counts at the node, its pending values included.
        - ``baseline``: the counts at the newest node on the lineage whose ``fired``
          names this subscription, or ``None`` when no node there does. It is the
          baseline of every lineage atom in ``every``, for a lease as for a durable
          subscription (review §12, item 14), so after a rewind below the last
          firing, ``every`` counts from the newest firing left on the lineage.
        - ``now``: the writer's clock. ``Elapsed`` in ``from`` and ``until`` reads
          ``now - registered_at``; in ``every`` it reads ``now`` minus the time of
          this claim's last firing, which a rewind does not move.
        - ``valued``: the sources whose registers hold a value at this node. A
          subscription can fire only when its target is one of them: a source with
          no value yet does not fire, and waits for the first ``set`` (§3). A pure
          demand never consults it, as a schedule with no time atom never consults
          ``now``.

        ``until`` first: where it holds, the subscription expires and fires
        nothing. One that cannot fire here (a pure demand, or a target with no
        value) evaluates only its ``until``: it may expire, never fires, and moves
        neither ``fires`` nor the time baseline, so its first firing once it can
        sample is still immediate. Otherwise it fires once ``from`` holds and
        ``every``'s delta rule does: ``count(now) - count(at the last firing) >=
        n``, not rounded to a multiple, with no interpolation inside a jump. An
        atom with no baseline holds, so the first firing is immediate. It expires
        at the node where it fires if it is a one-shot (no ``every``), or if its
        ``until`` holds once this firing is counted: a ``Fired`` atom counts the
        firings up to and including the node (§3, "Expiry"), so the nth firing
        ends the schedule, as a one-shot's one firing does."""
        s = self._schedule
        elapsed = now - self._registered_at
        if s.until is not None and satisfied(
            s.until, at=at, time_seconds=elapsed, count=self._fires
        ):
            return Decision(fire=False, expired=True)
        if self._target is None or self._target not in valued:
            return Decision(fire=False, expired=False)
        if s.from_ is not None and not satisfied(
            s.from_, at=at, time_seconds=elapsed, count=self._fires
        ):
            return Decision(fire=False, expired=False)
        if s.every is not None and not self._due(s.every, at, baseline, now):
            return Decision(fire=False, expired=False)
        self._fires += 1
        self._fired_at = now
        expired = s.every is None or (
            s.until is not None
            and satisfied(s.until, at=at, time_seconds=elapsed, count=self._fires)
        )
        return Decision(fire=True, expired=expired)

    def _due(
        self,
        every: Term,
        at: Coordinates,
        baseline: Coordinates | None,
        now: float,
    ) -> bool:
        """``every``'s delta rule, atom by atom against each atom's own baseline."""
        fired_at = self._fired_at

        def atom(a: Atom) -> bool:
            match a:
                case Target(x=x, n=n):
                    return baseline is None or at.of(x) - baseline.of(x) >= n
                case Elapsed(seconds=s):
                    return fired_at is None or now - fired_at >= s
                case Fired():
                    raise AssertionError("Schedule refuses a count atom in `every`")
                case _:
                    assert_never(a)

        return _holds(every, atom)


# ----- the class of a schedule ------------------------------------------------------


def is_lease(schedule: Schedule) -> bool:
    """Is the schedule a **lease** (§2, §3)? True if and only if it has an
    ``Elapsed`` or a ``Fired`` atom anywhere: both are relative to the claim that
    registered it (seconds since, firings since), so it lives per claim, is bound
    to it by ``lifecycle.bound``, and is void once that claim has ended. Every
    other schedule's atoms are lineage coordinates, and it is durable.

    A fold that meets a logged ``control.subscribe`` parses its body first. A body
    that does not parse is malformed: it is never a lease, nor durable demand, and
    the worker's ``nak`` spends its id. So a junk record cannot poison a fold, which
    an append-only log could never retract."""
    return any(isinstance(a, (Elapsed, Fired)) for a in schedule.atoms())


def references_time(schedule: Schedule) -> bool:
    """Does the schedule need an epoch: an ``Elapsed`` atom anywhere? Not the lease
    question, which a count-only schedule answers yes and this one no."""
    return any(isinstance(a, Elapsed) for a in schedule.atoms())


# ----- the parse --------------------------------------------------------------------

_COUNT_OUTSIDE_UNTIL = "count is valid only in `until`"


def parse_schedule(body: Condition, *, pure_demand: bool) -> Schedule:
    """Parse a ``control.subscribe`` body into a ``Schedule``, or raise
    ``MalformedSchedule`` naming the offending key: subscription-v0.4's
    ``Schedule``, spelled in Python so the worker needs no jsonschema.

    With ``pure_demand`` (a subscription whose envelope ``name`` is null), it also
    refuses a ``from``, an ``every`` or a ``count`` atom anywhere: a pure demand has
    a lifetime, not a schedule, and those schedule samples it cannot take (§3;
    review §12, item 14). The worker's drain parses first, and its refusal is a
    ``nak`` of reason ``malformed``, decided before any refusal as
    ``unsatisfiable``. A fold that meets a logged body parses it too, and reads a
    body that does not parse as malformed, spent by that ``nak`` (``is_lease``)."""
    unknown = sorted(body.keys() - {"from", "every", "until"})
    if unknown:
        raise MalformedSchedule(
            f"`{unknown[0]}`: unknown schedule key (a schedule takes from/every/until)"
        )

    def slot(key: str) -> Term | None:
        if key not in body:
            return None
        if pure_demand and key != "until":
            raise MalformedSchedule(f"`{key}`: a pure demand takes only an `until`")
        if key != "until":
            no_count: str | None = _COUNT_OUTSIDE_UNTIL
        elif pure_demand:
            no_count = "a pure demand takes no count: it never fires"
        else:
            no_count = None
        return _parse_term(body[key], path=key, no_count=no_count)

    return Schedule(from_=slot("from"), every=slot("every"), until=slot("until"))


def parse_stop_trigger(body: Condition) -> Term | None:
    """Parse a ``control.stop`` body (subscription-v0.4's ``StopTrigger``) into its
    ``from``, or ``None`` when it has none, which holds once drained (§4, "The stop
    decision"); raise ``MalformedSchedule`` naming the offending key. A stop is
    one-shot, so an ``every`` is inert and an ``until`` could gate it from ever
    firing: it takes only a ``from``, with no ``count``."""
    unknown = sorted(body.keys() - {"from"})
    if unknown:
        raise MalformedSchedule(f"`{unknown[0]}`: control.stop takes only `from`")
    if "from" not in body:
        return None
    return _parse_term(body["from"], path="from", no_count=_COUNT_OUTSIDE_UNTIL)


def _parse_term(cond: object, *, path: str, no_count: str | None) -> Term:
    """subscription-v0.4's ``Condition`` (``no_count`` is the refusal of a count
    atom) or ``UntilCondition`` (``no_count`` is None): an atom, or a non-empty
    ``any``/``all`` of conditions. ``path`` names the offending key."""
    if cond is None:
        raise MalformedSchedule(f"`{path}`: is null (omit the key instead)")
    if not isinstance(cond, dict):
        raise MalformedSchedule(
            f"`{path}`: must be a condition object, got {type(cond).__name__}"
        )
    if "stream" in cond or "n" in cond:
        if set(cond) != {"stream", "n"}:
            raise MalformedSchedule(
                f"`{path}`: {{stream, n}} takes exactly those keys, got {list(cond)}"
            )
        stream = cond["stream"]
        if not (isinstance(stream, str) and stream):
            raise MalformedSchedule(
                f"`{path}.stream`: must be a non-empty string, got {stream!r}"
            )
        n = cond["n"]
        return _built(f"{path}.n", lambda: Target(stream, n))
    if len(cond) != 1:
        raise MalformedSchedule(
            f"`{path}`: must carry exactly one condition key, got {list(cond)}"
        )
    ((key, value),) = cond.items()
    here = f"{path}.{key}"
    if key in ("any", "all"):
        if not isinstance(value, list) or not value:
            raise MalformedSchedule(f"`{here}`: must be a non-empty list of conditions")
        terms = tuple(
            _parse_term(c, path=f"{here}[{i}]", no_count=no_count)
            for i, c in enumerate(value)
        )
        return AnyOf(terms) if key == "any" else AllOf(terms)
    if key == "commits":
        return _built(here, lambda: Target(Commits(), value))
    if key == "time_seconds":
        return _built(here, lambda: Elapsed(value))
    if key == "count":
        if no_count is not None:
            raise MalformedSchedule(f"`{here}`: {no_count}")
        return _built(here, lambda: Fired(value))
    raise MalformedSchedule(f"`{here}`: unknown condition key")


def _built(path: str, build: Callable[[], Atom]) -> Atom:
    """An atom whose own constructor checks its number, its refusal named by
    ``path``."""
    try:
        return build()
    except ValueError as e:
        raise MalformedSchedule(f"`{path}`: {e}") from None


# ----- the refusals -----------------------------------------------------------------


class _Corner(NamedTuple):
    """The least coordinates at which a conjunctive term holds."""

    at: Coordinates
    time: float


# The root: no element, no commit, no time. An absent `from` holds here.
_ROOT = _Corner(Coordinates(streams=MappingProxyType({}), commits=0), 0.0)


def unsatisfiable(schedule: Schedule, *, sources: frozenset[str]) -> str | None:
    """Why the claim whose declared sources are ``sources`` refuses ``schedule``
    as ``unsatisfiable``, or ``None`` (§3, "Refusals"):

    - a progress atom naming a declared source, anywhere: a source's samples
      exist only where someone asked, so its count depends on who asked;
    - an empty window: a ``from`` that implies the ``until``, so the subscription
      can never fire. 0.3.0's corner check, restated in counts: ``until`` holding
      at the least coordinates where a conjunctive ``from`` holds holds wherever
      ``from`` does, since every term is monotone. On one X, that is ``until``'s
      n at most ``from``'s; across two it is speed-contingent and not refused. A
      ``from`` with an ``any`` has many corners, and is not checked rather than
      normalized. It does not depend on the position. An absent ``from`` reads as
      ``from`` 0 on each X, its corner the root, since a 0 in ``from`` re-spells
      an absent one: ``{until: {commits: 0}}`` is refused as ``{from: {commits:
      0}, until: {commits: 0}}`` is, so one schedule gets one answer (amended
      2026-10-30).

    An ``until`` that holds at registration but not at the root is not refused
    (review §12, item 14): whether it holds depends on the position, and it
    expires at the next node. The third refusal, a subscription targeting a name
    the claim does not declare a source, is the Worker's, since it reads the
    envelope's ``name``."""
    streams = {a.x for a in schedule.atoms() if isinstance(a, Target)}
    named = sorted(x for x in streams & sources if isinstance(x, str))
    if named:
        return (
            f"a progress atom names the declared source(s) {named}: a source's"
            " samples exist only where asked, so they are never counted"
        )
    if schedule.until is not None:
        corner = _ROOT if schedule.from_ is None else _corner(schedule.from_)
        if corner is not None and satisfied(
            schedule.until, at=corner.at, time_seconds=corner.time, count=0
        ):
            return "an empty window: `from` implies `until`, so it can never fire"
    return None


def _corner(term: Term) -> _Corner | None:
    """The least coordinates where the count-free ``term`` holds, or ``None`` for a
    term with an ``any``, which has many. ``all`` takes the per-coordinate maximum
    of its children's corners."""
    match term:
        case Target(x=x, n=n):
            if isinstance(x, Commits):
                return _Corner(_ROOT.at._replace(commits=n), 0.0)
            return _Corner(_ROOT.at._replace(streams={x: n}), 0.0)
        case Elapsed(seconds=s):
            return _Corner(_ROOT.at, s)
        case AnyOf():
            return None
        case AllOf(terms=terms):
            streams: dict[str, int] = {}
            commits, time = 0, 0.0
            for t in terms:
                sub = _corner(t)
                if sub is None:
                    return None
                for x, n in sub.at.streams.items():
                    streams[x] = max(streams.get(x, 0), n)
                commits = max(commits, sub.at.commits)
                time = max(time, sub.time)
            return _Corner(Coordinates(streams=streams, commits=commits), time)
        case Fired():
            raise AssertionError("Schedule refuses a count atom in `from`")
        case _:
            assert_never(term)
