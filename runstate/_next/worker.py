"""The reference Worker of log format 0.4.0, staged (docs/specs/commits-and-lineage.md
§2, §4, §6).

STAGED. Task 19 of docs/plans/2026-10-28-layer2-live.md moves this module over
``runstate/worker.py``.

THE MODEL (§2). A worker computes a lineage of nodes. Its claim
(``lifecycle.started``) states *who* computes, won by a compare-and-append against
every other claimant, and *where* it starts (``resume_from``). Each ``commit()``
writes one node, a ``lifecycle.commit`` naming the node it extends (``parent``) and
the values it commits (``values``); a ``rewind`` restates where. The claim's
**position** is left by its newest position-bearing record: a commit leaves itself,
a ``started`` or a ``rewind`` its ``resume_from``.

WHAT IT HOLDS. Everything that depends on the position is rebuilt from the lineage
at every position change, a claim or a rewind (§4, "At every position change"):
the coordinates at each node (``count(X, node)`` per eager stream, and
``Commits()``), and the value seqs the lineage commits. The source registers are
reset, so a sample never carries a value computed on an abandoned branch. What the
claim holds survives a rewind: its identity, and the values it discarded.

VALUES (§4, "Values"). ``emit`` sends a value at once and holds its seq pending;
the next node commits every pending seq, in the writer's order, so two emits of one
name in one iteration are two elements. ``set`` updates a source's register, which
is sampled only on demand. ``include`` adds a value another handle sent. A value no
node names is outside every lineage.

CONTROL (§4, "A drain"). ``commit()`` and ``heartbeat()`` first drain: the death
reports naming the claim, then ``control.*`` above the last complete drain, so a
drain reads O(the records since). A stop registers
or is refused; a withdrawal spends what it names. Every record a drain writes is
compare-and-appended at the tail it last read, so a death report that lands first
fences the worker before it refuses anything. Requests are answered by name, for
the whole log (§2, "Requests"): the claim folds every answer on the log below it,
and a spent id is dead on arrival, on every control topic. A record with a missing
or empty id is nameless: nothing can spend it, so a claim refuses one only above the
previous claim's watermark, below which that claim drained and refused.

SUBSCRIPTIONS (§3, subscription-v0.4; §4). A drain parses each
``control.subscribe`` once, and refuses what the claim cannot serve; a lease (a
time or count atom) registers only once its ``lifecycle.bound`` has landed. Each
node evaluates the live subscriptions once, at the node being written, writes one
sample per source that fired, and names what fired and expired (``fired``,
``expired``). A lease's clock, fire count and expiry are the claim's; a durable
subscription's baselines and expiry are the lineage's, rebuilt at every position
change, so it is live again on a lineage that did not expire it. ``serve()``
commits while one is live, and ``retire()`` exits when none is.

THE STOP DECISION (§4). A pending stop's ``from`` is evaluated at each node the
claim writes, with the counts at the node being written, and at each heartbeat,
with the counts at the position. Once one holds, the decision is True for the rest
of the claim: a monotone level, which a rewind does not lower.

THE EXIT (§4, "The exit"). ``stopped()`` writes the claim's last node by a
compare-and-append at a tail its drain has fully read, so no stop lands between the
last drain and the exit unseen: ``honored`` names every stop pending at the exit
and every stop that held during the claim. A completed exit is closed; an exit
that discarded its iteration (an error, or after ``discard()``) is torn; any other
is extendable, and the next claim may resume from it.

THE FENCE (§4, "A released worker fences itself"). Once a drain reads a death
report naming its claim, the worker writes nothing more: a worker released by
mistake costs at most the iteration in flight. A worker after its own exit is
fenced the same way (§4, "A worker after its own exit is fenced the same way"): a
record of its claim after the exit would be malformed (§5), and a value then is
outside every lineage. Only ``discard()`` makes misuse raise, until the exit.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import asdict, dataclass, replace
from enum import Enum
from types import MappingProxyType
from typing import Literal, NamedTuple, Self, assert_never

from ..channel import Channel, Envelope
from ..vocabulary.handle import local_handle
from ..vocabulary.launch import current_launch_id
from .checkpoint import SavePolicy
from .claims import (
    CAS_ATTEMPTS,
    Bindings,
    claim_before,
    endings,
    ends,
    request_name,
    spent_by,
    watermark,
)
from .launcher import report_if_dead
from .observables import LatestClaimHead
from .vocabulary.coordinate import Commits, NodeRef, Target
from .vocabulary.payloads import (
    Bound,
    Commit,
    Heartbeat,
    Nak,
    NakReason,
    Rewind,
    Started,
    Stopped,
    Topic,
    Value,
)
from .vocabulary.schedule import (
    Coordinates,
    MalformedSchedule,
    Schedule,
    Subscription,
    Term,
    is_lease,
    parse_schedule,
    parse_stop_trigger,
    satisfied,
    unsatisfiable,
)
from .walk import (
    NodeKind,
    coordinates,
    kind,
    kind_at,
    node_at,
    point,
    verdict_parse,
    walk,
)

# What a drain reads: every control request, a read-glob the substrate expands, not
# a wire topic.
_CONTROL = "control.>"

# The coordinates of the empty lineage: no element, no commit (§2: empty is 0).
_ROOT = Coordinates(streams=MappingProxyType({}), commits=0)


class WorkerStarvedError(RuntimeError):
    """One of the Worker's compare-and-append loops lost its CAS ``attempts`` times
    in a row (``claims.CAS_ATTEMPTS``): the log grew faster than the worker could
    read it, and that loop appended nothing. ``step`` names the loop: the
    ``claim`` (``claim_seq`` is then ``None``: nothing was claimed), a ``drain``'s
    refusal, or the ``exit``, after which the claim is still held, for a death
    report to end."""

    def __init__(
        self,
        step: Literal["claim", "drain", "exit"],
        claim_seq: int | None,
        attempts: int,
    ) -> None:
        whose = "no claim" if claim_seq is None else f"claim {claim_seq}"
        super().__init__(
            f"the worker's {step} ({whose}) lost its CAS {attempts} times in a "
            "row: the log grows faster than the worker reads it, and nothing was "
            "appended"
        )
        self.step = step
        self.claim_seq = claim_seq
        self.attempts = attempts


class _Phase(Enum):
    """Where the worker is in its claim's life. Only a ``LIVE`` worker commits; a
    ``DISCARDED`` one still writes its exit, and a ``STARVED`` one retries it; the
    rest write nothing.

    ``LIVE`` -> ``DISCARDED`` (``discard()``) -> ``STOPPED`` (its exit landed),
    ``RELEASED`` (a drain read a death report naming its claim) or ``STARVED`` (its
    exit's CAS starved), from either of the first two, and ``STARVED`` ->
    ``STOPPED`` or ``RELEASED`` on a retried ``stopped()``; ``LOST`` from the
    start."""

    LIVE = "live"
    """It won the claim, and computes."""
    DISCARDED = "discarded"
    """It declared its open iteration torn: it commits nothing more, and its exit
    is torn (§4, "discard()")."""
    STOPPED = "stopped"
    """Its exit landed: the claim's last node is on the log."""
    LOST = "lost"
    """It lost the claim race: it never claimed, and writes nothing (§4)."""
    RELEASED = "released"
    """It read a death report naming its claim, and writes nothing more (§4)."""
    STARVED = "starved"
    """Its exit's CAS lost past the bound (§4, "An exit that starves"): the claim is
    held, and every call but a retried ``stopped()`` is fenced, writing nothing,
    since the exit's drains passed over requests no watermark of the claim may
    claim."""


# ----- the lineage at the position --------------------------------------------------


class _Node(NamedTuple):
    """A node of the lineage at the position: its seq, the coordinates at and
    including it, the value seqs it commits, and the subscriptions that fired and
    expired there (§2: firing and expiry are facts at a node)."""

    seq: int
    coords: Coordinates
    values: tuple[int, ...]
    fired: tuple[str, ...]
    expired: tuple[str, ...]


class _Lineage:
    """The lineage at the position, root first (§4, "At every position change"),
    one ``_Node`` each. It is the Worker's count map, so a rewind to a node on it is
    a truncation, and a target elsewhere costs the walk from it.

    It folds what the subscriptions read of the lineage (§3, "Evaluation"): each
    one's baseline, the coordinates at the newest node whose ``fired`` names it,
    for a lease's lineage atoms as for a durable subscription's; and the
    subscriptions some node expired, which a durable subscription is not live
    beside. A lease reads only the baseline: its expiry is the claim's."""

    def __init__(self, nodes: list[_Node]) -> None:
        self._nodes = nodes
        self._index = {n.seq: i for i, n in enumerate(nodes)}
        self._committed: set[int] = set()
        self._baselines: dict[str, Coordinates] = {}
        self._expired: set[str] = set()
        for n in nodes:
            self._fold(n)

    def _fold(self, node: _Node) -> None:
        self._committed.update(node.values)
        for r in node.fired:
            self._baselines[r] = node.coords
        self._expired.update(node.expired)

    @classmethod
    def at(cls, ch: Channel, node: int | None) -> _Lineage:
        """The lineage ending at ``node`` (``None``: the empty lineage), walked and
        checked (§5, "The pure walk"), its coordinates resolved."""
        walked = walk(ch, node)
        return cls(
            [
                _Node(
                    at.env.seq,
                    c,
                    tuple(at.node.committed),
                    tuple(at.node.fired),
                    tuple(at.node.expired),
                )
                for at, c in zip(walked.nodes, coordinates(walked), strict=True)
            ]
        )

    @property
    def node(self) -> int | None:
        """The position: the newest node, or ``None`` for the empty lineage."""
        return self._nodes[-1].seq if self._nodes else None

    @property
    def tip(self) -> Coordinates:
        """The coordinates at the position."""
        return self._nodes[-1].coords if self._nodes else _ROOT

    def commits(self, seq: int) -> bool:
        """Does a node of the lineage commit the value ``seq``?"""
        return seq in self._committed

    def baseline(self, r: str) -> Coordinates | None:
        """The coordinates at the newest node whose ``fired`` names ``r``, or
        ``None`` when none does: ``every``'s lineage baseline (§3)."""
        return self._baselines.get(r)

    def expires(self, r: str) -> bool:
        """Does a node of the lineage name ``r`` in its ``expired``?"""
        return r in self._expired

    def to(self, ch: Channel, node: int | None) -> _Lineage:
        """The lineage ending at ``node``: this one's prefix when ``node`` is on it
        (or is the root), else walked from ``node``."""
        if node is None:
            return _Lineage([])
        i = self._index.get(node)
        return _Lineage.at(ch, node) if i is None else _Lineage(self._nodes[: i + 1])

    def extend(self, node: _Node) -> None:
        """Append the node the claim just wrote."""
        self._index[node.seq] = len(self._nodes)
        self._nodes.append(node)
        self._fold(node)


@dataclass(frozen=True)
class _Position:
    """What depends on the position (§4, "At every position change"): the node the
    position was last restated at (``resume_node``), the lineage that ends at the
    position, and the source registers. A claim and a rewind each rebuild it whole,
    by ``at``, so a position change is one assignment.

    The subscriptions' per-lineage state is the lineage's fold (``_Lineage``):
    each one's ``every`` baseline, and which a node expired, so a durable
    subscription another lineage expired is live again here. A durable
    subscription reads no fire count and no clock, so none is rebuilt; a lease's
    clock, fire count and expiry are the claim's (``Subscription``,
    ``_Requests``), and survive the change."""

    resume_node: int | None
    lineage: _Lineage
    registers: dict[str, object]

    @classmethod
    def at(cls, node: int | None, lineage: _Lineage) -> _Position:
        """The position restated at ``node``, whose lineage is ``lineage``: the
        registers are reset, so a sample never carries a value computed on an
        abandoned branch."""
        return cls(resume_node=node, lineage=lineage, registers={})


class _Landed(NamedTuple):
    """An exit that landed: its seq, and whether it is extendable, so the save
    policy runs after it."""

    seq: int
    extendable: bool


class _Unwritten(Enum):
    """Why an exit's compare-and-append loop wrote no exit."""

    RELEASED = "released"
    """A drain read a death report naming the claim: it writes nothing more."""
    SERVING = "serving"
    """A retiring drain registered a live subscription: the worker serves it."""


class _ResumePoint(NamedTuple):
    """The node a claim resumes from, validated, and the W it was read at."""

    node: int | None
    w: int


# ----- the claim, the drain and the open iteration ---------------------------------


class _Claim(NamedTuple):
    """The claim's facts: its seq (0 until won), and the previous claim's watermark
    (``claims.watermark``), above which it refuses a nameless record (§4, "A
    drain")."""

    seq: int
    previous_watermark: int


@dataclass
class _Drain:
    """Where the drains have read to. ``drained_to`` is the W of the last complete
    control drain, which the next one reads above, so a drain reads O(the records
    since); ``watermark`` is the newest control record handled, a commit's or a
    heartbeat's ``consumed_seq``; ``reports_read`` is the W the death reports naming
    the claim have been read to."""

    drained_to: int
    watermark: int
    reports_read: int


@dataclass
class _Iteration:
    """The open iteration's values: ``pending``, which the next node commits, and
    ``discarded``, the values this claim discarded at a rewind or by
    ``discard()``, which ``include`` refuses."""

    pending: list[_Pending]
    discarded: set[int]

    def discard(self) -> None:
        """Discard the pending values: they stay on the log outside every
        lineage."""
        self.discarded.update(p.seq for p in self.pending)
        self.pending = []


class _Pending(NamedTuple):
    """A value the next node commits: its seq, and its name, an eager stream's."""

    seq: int
    name: str


# ----- the requests (§2, "Requests"; §4, "A drain") --------------------------------


class _Mode(Enum):
    """What a drain is for, which decides what it registers (§4): an iteration's
    (``commit()``, ``heartbeat()``) registers every verb, and so do ``retire()``'s
    two, since it exits only when no subscription arrived; the exit's registers no
    subscription, so one that arrives after the last commit stays live for the
    next claim, where a lease bound here would be void at once."""

    ITERATION = "iteration"
    EXIT = "exit"


class _Refusal(NamedTuple):
    """A ``lifecycle.nak`` a drain writes for a control record it refuses: once it
    lands, it spends ``request_id`` (``None`` for a nameless record)."""

    body: Nak
    request_id: str | None


class _Binding(NamedTuple):
    """A ``lifecycle.bound`` a drain writes for a lease it registers (§4,
    "Leases"): once it lands, ``subscription`` registers under ``request_id``,
    bound to this claim, and not before, so the log binds every lease the claim
    serves."""

    body: Bound
    request_id: str
    subscription: Subscription


_Write = _Refusal | _Binding
"""A record a drain compare-and-appends for a control record, which takes effect
only once it lands (``_Requests.landed``)."""


class _Evaluation(NamedTuple):
    """The subscriptions' evaluation at one node (§3, "Evaluation"): the ids that
    fired and expired there, and the sources to sample, one each, in the order of
    their first firing."""

    fired: list[str]
    expired: list[str]
    sources: list[str]


_NOTHING = _Evaluation(fired=[], expired=[], sources=[])
""""An exit that discarded its iteration evaluates nothing" (§3)."""


class _ExitNode(NamedTuple):
    """What step 4 of the exit writes, decided by steps 1 to 3 (§4, "The exit"),
    held so that a lost CAS, or the retry of an exit that starved, writes it again
    without evaluating again: its verdict, its ``values`` (null when it discards
    its iteration), and its evaluation."""

    completed: bool
    error: str | None
    values: list[int] | None
    evaluation: _Evaluation


class _Stop(NamedTuple):
    """A drained, pending ``control.stop``: its ``from``, parsed at the drain
    (``None`` holds once drained), and the claim's clock when it registered, which
    a time atom counts from."""

    from_: Term | None
    registered_at: float


class _Requests:
    """The claim's requests, by name (§2, "Requests"): the ids spent, the stops
    pending, the stops held, in the order they held, and the subscriptions
    registered, with the leases bound to this claim.

    A subscription is dropped only when a withdrawal, a refusal or its own claim's
    expiry of a lease spends it, or it is a void lease (§4, "The claim"). A durable
    subscription's expiry is the lineage's (``_Lineage.expires``), so it stays
    registered, live again on a lineage that does not expire it."""

    def __init__(self, sources: frozenset[str], *, claim: int) -> None:
        self._sources = sources
        self._claim = claim
        self._spent: set[str] = set()
        self._stops: dict[str, _Stop] = {}
        self._held: list[str] = []
        self._subs: dict[str, Subscription] = {}
        self._bound: set[str] = set()  # the leases bound to this claim
        self._bindings = Bindings()  # the log's, below the claim

    def answered(self, e: Envelope) -> None:
        """Fold a record on the log below the claim: the ids ``e`` spends
        (``claims.spent_by``), a displaced worker's exit included; and the
        bindings (``claims.Bindings``), so a lease bound to a claim that has ended
        is void."""
        self._spent.update(spent_by(e))
        self._bindings.apply(e)

    def take(
        self, e: Envelope, *, mode: _Mode, now: float, nameless_above: int
    ) -> _Write | None:
        """Handle one control record, or return the record to write for it, having
        changed nothing: that record takes effect only once it lands (``landed``).

        By its id first, on every control topic (§4, "A drain"):

        - **A missing or empty id is nameless** (``claims.request_name``), and
          refused ``malformed``. Nothing can spend it, so it is refused once: only
          above ``nameless_above``, the previous claim's watermark, since that
          claim drained and refused what lies below.
        - **A spent id is skipped,** silently: "already answered" is not a
          refusal, and the id is dead on arrival. Request ids are one namespace.

        Then by its topic:

        - **A stop** (subscription-v0.4). Its trigger is parsed once here
          (``parse_stop_trigger``): ``malformed`` when it does not parse,
          ``unsatisfiable`` when a progress atom names a declared source (§3,
          "Refusals"). An accepted re-send of a pending id replaces its
          registration, its time ``from`` counted from ``now``; a refused one
          drops it once the refusal lands; a re-send of one that held is ignored,
          since the decision is a level.
        - **A withdrawal** spends what it names (§2): a pending stop that has not
          held is dropped, and one that held is too late, since the claim stops
          and ``honored`` names it (§4, "The exit").
        - **A subscription** (``_subscribe``) registers, in ``ITERATION`` mode; the
          exit passes over every named one, its body unparsed, so a malformed one
          too, for the next claim to register or refuse (§4, "The exit", step 1).
        - **Any other ``control.*`` topic** is refused ``unsupported``."""
        r = request_name(e)
        if r is None:
            if e.seq <= nameless_above:
                return None  # the previous claim drained it, and refused it
            return _Refusal(
                Nak(
                    reason=NakReason.MALFORMED,
                    message=f"a {e.topic} is named by its request_id, and this "
                    "one has none",
                ),
                None,
            )
        if r in self._spent:
            return None
        if e.topic == Topic.CONTROL_STOP:
            if r in self._held:
                return None
            try:
                trigger = parse_stop_trigger(e.body)
            except MalformedSchedule as exc:
                return _Refusal(Nak(reason=NakReason.MALFORMED, message=str(exc)), r)
            if trigger is not None:
                why = unsatisfiable(
                    Schedule(from_=trigger, every=None, until=None),
                    sources=self._sources,
                )
                if why is not None:
                    return _Refusal(Nak(reason=NakReason.UNSATISFIABLE, message=why), r)
            self._stops[r] = _Stop(trigger, now)
            return None
        if e.topic == Topic.CONTROL_WITHDRAW:
            self._spent.update(spent_by(e))
            self._stops.pop(r, None)
            self._subs.pop(r, None)
            return None
        if e.topic == Topic.CONTROL_SUBSCRIBE:
            return None if mode is _Mode.EXIT else self._subscribe(e, r, now)
        return _Refusal(
            Nak(
                reason=NakReason.UNSUPPORTED,
                message=f"unknown control topic {e.topic!r}",
            ),
            r,
        )

    def _subscribe(self, e: Envelope, r: str, now: float) -> _Write | None:
        """Register a ``control.subscribe`` named ``r``, or return the record to
        write for it (§3, subscription-v0.4; §4, "A drain"), in order:

        - its body is parsed (``parse_schedule``, a pure demand's as one, its
          ``name`` null), and refused ``malformed`` when it does not parse;
        - at its first send, a lease bound to a claim that has ended is void: it is
          dropped, and its id is dead (its class is its first send's, and void
          never reverts);
        - a re-send that changes its target or its class (lease or durable) is
          refused ``malformed``: the first send fixes the subscription;
        - a target the claim does not declare a source, or a schedule
          ``unsatisfiable`` against the claim's sources, is refused
          ``unsatisfiable``;
        - otherwise it registers afresh, as a new ``Subscription``, so a lease's
          time atoms count from ``now``, the clock of this drain in this claim (§3,
          "An accepted re-send": a ``control.subscribe`` carries no clock of the
          asker's), and its fire count is 0, while what the lineage holds stays. A
          lease not yet bound to this claim registers only once its
          ``lifecycle.bound`` lands."""
        try:
            schedule = parse_schedule(e.body, pure_demand=e.name is None)
        except MalformedSchedule as exc:
            return _Refusal(Nak(reason=NakReason.MALFORMED, message=str(exc)), r)
        lease = is_lease(schedule)
        first = self._subs.get(r)
        if first is None:
            if lease and self._bindings.void(r):
                self._spent.add(r)  # void: bound to a claim that has ended
                return None
        elif (first.target, first.lease) != (e.name, lease):
            was = "a lease" if first.lease else "durable"
            return _Refusal(
                Nak(
                    reason=NakReason.MALFORMED,
                    message=f"a re-send of {r!r} must keep its first send's "
                    f"target {first.target!r} and class ({was})",
                ),
                r,
            )
        if e.name is not None and e.name not in self._sources:
            return _Refusal(
                Nak(
                    reason=NakReason.UNSATISFIABLE,
                    message=f"{e.name!r} is not a declared source of this claim: "
                    "an eager stream is read, not subscribed",
                ),
                r,
            )
        why = unsatisfiable(schedule, sources=self._sources)
        if why is not None:
            return _Refusal(Nak(reason=NakReason.UNSATISFIABLE, message=why), r)
        sub = Subscription(schedule, registered_at=now, target=e.name)
        if lease and r not in self._bound:
            return _Binding(Bound(claim_seq=self._claim), r, sub)
        self._subs[r] = sub
        return None

    def landed(self, write: _Write) -> None:
        """A write's effect, once it has landed: a refusal spends the id it names,
        as a ``nak`` does (``claims.spent_by``), and drops a stop or a
        subscription registered under it (a refused re-send); a binding registers
        its lease, bound to this claim."""
        match write:
            case _Refusal(request_id=r):
                if r is not None:
                    self._spent.add(r)
                    self._stops.pop(r, None)
                    self._subs.pop(r, None)
            case _Binding(request_id=r, subscription=sub):
                self._bound.add(r)
                self._subs[r] = sub
            case _:
                assert_never(write)

    def live(self, lineage: _Lineage) -> frozenset[str]:
        """The subscriptions live for this claim at ``lineage``: every lease
        registered (its expiry is the claim's, and spends it), and every durable
        one no node of the lineage expired."""
        return frozenset(
            r for r, sub in self._subs.items() if sub.lease or not lineage.expires(r)
        )

    def evaluate(
        self,
        *,
        lineage: _Lineage,
        at: Coordinates,
        now: float,
        valued: AbstractSet[str],
    ) -> _Evaluation:
        """Evaluate each live subscription once, at the node being written, whose
        coordinates are ``at`` (§3, "Evaluation"): ``until`` first, then ``from``
        and ``every`` against its lineage baseline (``_Lineage.baseline``); a
        source fires only once ``valued`` holds it. The call is the firing
        (``Subscription.evaluate``), so a caller that retries a write reuses the
        result."""
        live = self.live(lineage)
        fired: list[str] = []
        expired: list[str] = []
        sources: list[str] = []
        for r, sub in self._subs.items():
            if r not in live:
                continue
            d = sub.evaluate(
                at=at, baseline=lineage.baseline(r), now=now, valued=valued
            )
            if d.fire:
                fired.append(r)
                assert sub.target is not None  # a pure demand never fires
                if sub.target not in sources:
                    sources.append(sub.target)
            if d.expired:
                expired.append(r)
        return _Evaluation(fired=fired, expired=expired, sources=sources)

    def node_landed(self, evaluation: _Evaluation) -> None:
        """A commit's effect on the claim, once it has landed: a lease it expired
        is spent, since a lease's expiry is the claim's, which no rewind re-arms. A
        durable subscription's expiry is the node's, which the lineage folds."""
        for r in evaluation.expired:
            if self._subs[r].lease:
                del self._subs[r]
                self._spent.add(r)

    def decide(self, at: Coordinates, now: float) -> bool:
        """The stop decision at the coordinates ``at`` and the clock ``now`` (§4):
        each pending stop whose ``from`` holds there is held for the rest of the
        claim, a monotone level; a stop with no ``from`` holds once drained. Every
        atom is a ``>=`` threshold, so a stop that held would hold again."""
        for r, stop in list(self._stops.items()):
            if stop.from_ is None or satisfied(
                stop.from_, at=at, time_seconds=now - stop.registered_at, count=0
            ):
                del self._stops[r]
                self._held.append(r)
        return self.stopping

    @property
    def stopping(self) -> bool:
        """The stop decision's level: has a stop held during the claim?"""
        return bool(self._held)

    def honored(self) -> list[str]:
        """What the exit honors (§4, "The exit"): every stop that held during the
        claim and every stop pending now, a set whose order means nothing (§3)."""
        return [*self._held, *self._stops]


# ----- reading at W -----------------------------------------------------------------


def _extendable(ch: Channel, seq: int, w: int) -> None:
    """Check, with one point read, that ``seq`` names an extendable node of this run
    at or below ``w`` (§4: what ``resume_from`` and ``rewind`` may name); raise
    ``ValueError`` naming what it is instead. A node whose body is unreadable raises
    ``MalformedRecordError``."""
    e = point(ch, seq, w=w)
    if e is None:
        raise ValueError(f"no record at seq {seq} at or below {w}")
    at = node_at(e)
    if at is None:
        raise ValueError(f"seq {seq} is a {e.topic}, not a node")
    k = kind(at.node)
    if k is not NodeKind.EXTENDABLE:
        raise ValueError(f"seq {seq} is a {k} node: only an extendable one extends")


def _described(exc: BaseException) -> str:
    """An exception as an exit's ``error``: its message, or its type's name when
    its message is empty (``KeyboardInterrupt()``, a bare ``raise RuntimeError``),
    so an errored exit never carries an empty ``error``."""
    return str(exc) or type(exc).__name__


class Worker:
    """The reference worker (§4): ``Worker(channel, *, resume_from, sources,
    save)``, each required and keyword-only.

    - ``resume_from`` is where the claim starts: a ``NodeRef`` names a fixed node (a
      checkpoint object, or ``AtNode(x)``); ``LatestClaimHead()`` continues the
      nearest extendable node of the head it resolves at the claim's own W, for a
      worker that holds no state; ``None`` is no node of this run, a fresh start.
    - ``sources`` are the names the worker exposes to be sampled on demand
      (``set``); every other name is an eager stream (``emit``). ``[]`` declares
      none.
    - ``save`` is the checkpoint save policy (§6), run after each node the
      recipe saves at.
    - ``now`` is the clock every record's ``t`` reads. Exempt from the rule that a
      result-changing parameter has no default (plan, "Exempt defaults"): it is
      the clock source, which tests freeze.

    Constructing it claims the run: a worker that loses the claim race writes
    nothing, ever, and its ``commit()`` returns True (``claimed`` says why). Use it
    as a context manager, so that leaving the block writes the exit: errored on an
    exception, preempted otherwise, unless the worker already stopped.

    Each compare-and-append loop (the claim, a drain's refusal, the exit) gives up
    after ``claims.CAS_ATTEMPTS`` lost CASes in a row, raising
    ``WorkerStarvedError``."""

    def __init__(
        self,
        channel: Channel,
        *,
        resume_from: NodeRef | LatestClaimHead | None,
        sources: Sequence[str],
        save: SavePolicy,
        now: Callable[[], float] = time.time,
    ) -> None:
        if isinstance(sources, str):
            raise TypeError(
                f"sources is a sequence of names, not a string: {sources!r}"
            )
        self._ch = channel
        self._now = now
        self._save = save
        # Built once, here, so a bad declaration raises before anything is written.
        self._started = Started(
            handle=local_handle(), t=now(), resume_from=None, sources=list(sources)
        )
        self._sources = frozenset(self._started.sources)
        self._phase = _Phase.LOST
        self._claim = _Claim(seq=0, previous_watermark=0)
        self._position = _Position.at(None, _Lineage([]))
        self._iteration = _Iteration(pending=[], discarded=set())
        self._drained = _Drain(drained_to=0, watermark=0, reports_read=0)
        self._requests = _Requests(self._sources, claim=0)
        self._starved: _ExitNode | None = None  # the exit a STARVED worker retries
        self._claim_run(resume_from)

    # ----- the claim (§4, "The claim") -------------------------------------------

    def _claim_run(self, resume_from: NodeRef | LatestClaimHead | None) -> None:
        """Claim the run, in order (§4, "The claim"): read W, validate
        ``resume_from`` there and walk the lineage that ends at its node, so a
        malformed one raises with nothing written; if the latest claim is held,
        call ``report_if_dead`` (§3), and start again if it reports, else lose the
        claim; CAS ``started`` at W; after winning, take the walked lineage as the
        position, and fold the answers by name up to that same W. A lost CAS starts
        again from a new W, and walks again only if the node it resolves changed.

        The answers are folded once the claim has won rather than before each
        attempt: the CAS at W proves that the log at or below W is exactly what
        preceded the claim, so the fold reads the same records either way, and a
        loser, or a lost CAS, reads none of them. A walked lineage is settled at
        its node, whatever W, so the rebuild reuses it.

        "Held" is the presence test (``claims.endings``): no exit and no death
        report names the latest claim, readable or not. It never raises, so a
        contradiction in an old claim's window cannot keep the run from being
        claimed, and it agrees with ``report_if_dead``'s own test (§3)."""
        lineage: _Lineage | None = None
        for _ in range(CAS_ATTEMPTS):
            at = self._resume_point(resume_from)
            if lineage is None or lineage.node != at.node:
                lineage = _Lineage.at(self._ch, at.node)  # raises before the CAS
            latest = self._ch.latest(Topic.LIFECYCLE_STARTED)
            if latest is not None and latest.seq > at.w:
                continue  # a claim landed since W: the CAS at W would lose
            if latest is not None and not endings(self._ch, latest, at.w):
                if report_if_dead(self._ch) is not None:
                    continue  # its death is on the log now: claim again
                return  # held by a live claimant: lost
            seq = self._ch.send(
                asdict(replace(self._started, resume_from=at.node)),
                topic=Started.TOPIC,
                # The claim names the launch it answers (ambient; None when no
                # launcher spawned it), so a launcher's death report names the
                # claim (specs/launcher-record-identity.md).
                request_id=current_launch_id(),
                expected_seq=at.w,
            )
            if seq is not None:
                self._won(seq, at, lineage, latest)
                return
        raise WorkerStarvedError("claim", None, CAS_ATTEMPTS)

    def _won(
        self, seq: int, at: _ResumePoint, lineage: _Lineage, previous: Envelope | None
    ) -> None:
        """After the claim's CAS (§4, "The claim", step 4): take the position, then
        read what lies at or below W: the answers by name, and the previous claim's
        watermark.

        Every field is assigned before the first read, so a read that raises
        leaves a worker that can write its exit, which it then writes, errored,
        before the raise propagates: a won claim never stays held with no object to
        end it. Until the fold has read the log at or below W, the claim serves
        only what lands above it, reading no control at or below W: so if the fold
        raises, the errored exit honors and refuses only what landed above W, and a
        stop at or below W stays pending for the next claim."""
        self._phase = _Phase.LIVE
        self._claim = _Claim(seq=seq, previous_watermark=at.w)
        self._position = _Position.at(at.node, lineage)
        self._drained = _Drain(drained_to=at.w, watermark=0, reports_read=seq)
        try:
            requests = self._answers(seq, at.w)
            bound = self._previous_watermark(previous, at.w)
        except BaseException as exc:
            try:
                self.stopped(completed=False, error=_described(exc))
            except Exception as inner:
                exc.add_note(f"and the claim's exit was not written: {inner!r}")
            raise
        self._requests = requests
        self._claim = _Claim(seq=seq, previous_watermark=bound)
        self._drained.drained_to = 0  # the first drain reads every control record

    def _answers(self, claim: int, w: int) -> _Requests:
        """The answers on the log at or below ``w``, by name, and the lease
        bindings (§4, "The claim", step 4): a request is spent once a
        ``control.withdraw`` or a ``lifecycle.nak`` names it, and a stop once an
        exit's ``honored`` does, wherever that sits, a displaced worker's exit
        included, so a resumed claim never re-honors a stop
        (``claims.spent_by``); and a lease is void once a ``lifecycle.bound`` binds
        it to a claim that has ended (``claims.Bindings``), so the claim never
        re-anchors another claim's lease."""
        requests = _Requests(self._sources, claim=claim)
        topics: list[str] = [
            Topic.CONTROL_WITHDRAW,
            Topic.LIFECYCLE_NAK,
            *Bindings.TOPICS,
        ]
        for e in self._ch.read(topics=topics, limit=w) if w else []:
            if e.seq > w:
                break
            requests.answered(e)
        return requests

    def _previous_watermark(self, previous: Envelope | None, w: int) -> int:
        """The bound above which this claim refuses a nameless record (§4, "A
        drain"): the previous claim's watermark at or below ``w``
        (``claims.watermark``), or 0 when there is no previous claim. A claim that
        wrote no watermark drained nothing anyone can see, so the bound is the
        watermark of the newest claim before it that wrote one."""
        claim = previous
        while claim is not None:
            mark = watermark(self._ch, claim, w)
            if mark is not None:
                return mark
            claim = claim_before(self._ch, claim.seq)
        return 0

    def _resume_point(
        self, resume_from: NodeRef | LatestClaimHead | None
    ) -> _ResumePoint:
        """The node the claim resumes from, validated, and the W it was read at.

        ``LatestClaimHead`` resolves at its own W, which the claim then takes as its
        own: the head of the newest claim, or for a torn exit its ``parent``, or the
        empty lineage. A closed head raises ``ValueError``: the run is complete. A
        ``NodeRef``'s node must be an extendable node at or below W."""
        if isinstance(resume_from, LatestClaimHead):
            head = resume_from.resolve(self._ch)
            if head.settled:
                raise ValueError(
                    f"the latest claim's head, node {head.node}, is closed: the run "
                    "is complete, and nothing extends it"
                )
            if head.node is None:
                return _ResumePoint(None, head.w)
            e = point(self._ch, head.node, w=head.w)
            if kind_at(e) is NodeKind.TORN:
                assert e is not None  # a torn node is an exit, at or below w
                return _ResumePoint(verdict_parse(Stopped, e).parent, head.w)
            return _ResumePoint(head.node, head.w)
        w = self._ch.last_seq()
        if resume_from is None:
            return _ResumePoint(None, w)
        _extendable(self._ch, resume_from.node, w)
        return _ResumePoint(resume_from.node, w)

    @property
    def claimed(self) -> bool:
        """True if this worker won the claim; False if it lost the race."""
        return self._phase is not _Phase.LOST

    @property
    def released(self) -> bool:
        """True once a drain has read a death report naming this worker's claim:
        it writes nothing more (§4)."""
        return self._phase is _Phase.RELEASED

    @property
    def stop_pending(self) -> bool:
        """The stop decision, read without side effects (§4): the level
        ``commit()`` and ``heartbeat()`` last returned, for a callback guest whose
        host loop cannot act on a return value. True for a worker that commits
        nothing more, a claim-race loser, a released or a stopped worker, or one
        after ``discard()``, whose every safe point is a stop."""
        return self._phase is not _Phase.LIVE or self._requests.stopping

    @property
    def pinned(self) -> bool:
        """Is some subscription live for this claim (§4, "The drivers"): a lease of
        the claim, or a durable subscription live on its lineage? It reads what the
        drains have registered, so before the first drain it is False; ``serve()``
        and ``retire()`` drain before they read it. False for a worker that commits
        nothing more."""
        return bool(self._live())

    def _live(self) -> frozenset[str]:
        """The ids of the subscriptions live for this claim (``pinned``)."""
        if self._phase is not _Phase.LIVE:
            return frozenset()
        return self._requests.live(self._position.lineage)

    @property
    def resume_node(self) -> int | None:
        """The node the claim resumed from, which a rewind moves to its target, or
        ``None`` when it names no node: what the checkpoint recipe's load assertion
        compares (§6)."""
        return self._position.resume_node

    def _acts(self) -> bool:
        """Whether ``emit``, ``set``, ``include`` and ``rewind`` act: only for a
        live worker. A lost, released or stopped worker's do nothing, since a
        released one cannot know when its report landed, and a stopped one is
        fenced the same way (§4, "A worker after its own exit is fenced the same
        way"), and so is one whose exit starved (§4, "An exit that starves");
        after ``discard()``, until the exit, they raise ``RuntimeError``:
        the worker discarded the iteration itself, so a later write is its own
        bug, and could never be committed (§4)."""
        if self._phase is _Phase.DISCARDED:
            raise RuntimeError(
                "the worker discarded its open iteration: nothing it writes now "
                "can be committed (rewind to a checkpoint instead of discarding "
                "to redo an iteration)"
            )
        return self._phase is _Phase.LIVE

    # ----- values (§4, "Values") ---------------------------------------------------

    def emit(self, name: str, value: object) -> None:
        """Send a ``value`` of the eager stream ``name`` at once, and hold its seq
        for the next node to commit. ``ValueError`` on a declared source, which is
        sampled on demand, never emitted. A value that does not serialize raises
        ``TypeError`` naming it: a broken reporting path stops the run."""
        if not self._acts():
            return
        if name in self._sources:
            raise ValueError(
                f"{name!r} is a declared source: set() its register, which is "
                "sampled on demand"
            )
        seq = self._send_value(name, value, self._now())
        self._iteration.pending.append(_Pending(seq, name))

    def _send_value(self, name: str, value: object, now: float) -> int:
        """Send a ``value`` record of ``name``: an emitted element, or a source's
        sample. A value that does not serialize raises ``TypeError`` naming it."""
        try:
            seq = self._ch.send(
                asdict(Value(value=value, t=now)), topic=Value.TOPIC, name=name
            )
        except (TypeError, ValueError) as exc:
            raise TypeError(
                f"value for {name!r} ({type(value).__name__}) is not "
                "JSON-serializable; pass json_default to "
                "current_channel()/create_channel() to coerce it"
            ) from exc
        assert seq is not None  # an unconditional send always lands
        return seq

    def set(self, name: str, value: object) -> None:
        """Update the source ``name``'s register, which a subscription samples only
        when it fires. ``ValueError`` on an undeclared name: an eager stream is
        emitted."""
        if not self._acts():
            return
        if name not in self._sources:
            raise ValueError(
                f"{name!r} is not a declared source: emit() an eager stream"
            )
        self._position.registers[name] = value

    def include(self, seq: int) -> None:
        """Add to the next node the value at ``seq``, sent through another channel
        handle (§4). One point read; ``ValueError`` unless it is a ``value`` with a
        name and no ``request_id``, above the claim, neither pending, nor committed
        on the current lineage, nor discarded by this claim at a rewind, and its name
        is not a declared source. Authorship stays unprovable: an honor system."""
        if not self._acts():
            return
        e = point(self._ch, seq, w=self._ch.last_seq())
        if e is None or e.topic != Topic.VALUE:
            raise ValueError(f"seq {seq} is not a value record")
        if e.name is None or e.request_id is not None:
            raise ValueError(
                f"the value at seq {seq} must name a stream and no request "
                f"(name {e.name!r}, request_id {e.request_id!r})"
            )
        if seq <= self._claim.seq:
            raise ValueError(f"seq {seq} is at or below the claim {self._claim.seq}")
        if any(p.seq == seq for p in self._iteration.pending):
            raise ValueError(f"seq {seq} is already pending")
        if self._position.lineage.commits(seq):
            raise ValueError(f"seq {seq} is committed on the current lineage")
        if seq in self._iteration.discarded:
            raise ValueError(f"seq {seq} was discarded at a rewind")
        if e.name in self._sources:
            raise ValueError(f"{e.name!r} is a declared source, never an element")
        self._iteration.pending.append(_Pending(seq, e.name))

    # ----- the node (§4, "commit()") -----------------------------------------------

    def _written(self, *, iterations: int) -> Coordinates:
        """The coordinates at the node being written (§3, "Evaluation"): the
        position's, with the pending values counted and ``iterations`` added, 1 for a
        commit and 0 for the exit."""
        tip = self._position.lineage.tip
        streams = dict(tip.streams)
        for p in self._iteration.pending:
            streams[p.name] = streams.get(p.name, 0) + 1
        return Coordinates(MappingProxyType(streams), tip.commits + iterations)

    def commit(self) -> bool:
        """End an iteration, in order (§4, "commit()"): drain, so a subscription
        registered here may fire here; evaluate the stops and the subscriptions at
        the node being written; write one sample per source whose subscriptions
        fired; write one ``lifecycle.commit`` extending the position, committing
        the pending values in the order they were added, then the samples, and
        naming what fired and expired; run the save policy once it has landed
        (§6); return the stop decision. The clock is read once, for the drain, the
        evaluations, the samples and the node's ``t``.

        A sample that cannot be written raises, as ``emit`` does, and the claim
        then ends errored; the samples already written stay outside every lineage.
        A save that raises propagates, and the node stands (§6): the claim ends,
        and its exit, errored and torn, names that node as its ``parent``."""
        if self._phase is not _Phase.LIVE:
            return True  # writes nothing
        now = self._now()
        if self._drain(_Mode.ITERATION, now) is None:
            return True  # released by a report the drain read
        at = self._written(iterations=1)
        decision = self._requests.decide(at, now)
        evaluation = self._evaluate(at, now)
        values = [p.seq for p in self._iteration.pending]
        values += self._samples(evaluation, now)
        body = Commit(
            claim_seq=self._claim.seq,
            consumed_seq=self._drained.watermark,
            parent=self._position.lineage.node,
            values=values,
            fired=evaluation.fired,
            expired=evaluation.expired,
            t=now,
        )
        seq = self._ch.send(asdict(body), topic=Commit.TOPIC)
        assert seq is not None  # an unconditional send always lands
        node = _Node(
            seq, at, tuple(values), tuple(evaluation.fired), tuple(evaluation.expired)
        )
        self._position.lineage.extend(node)
        self._requests.node_landed(evaluation)
        self._iteration.pending = []
        self._save.after(seq, commits=at.commits, exit=False)
        return decision

    def _evaluate(self, at: Coordinates, now: float) -> _Evaluation:
        """The subscriptions' evaluation at the node being written, whose
        coordinates are ``at``, against the registers that hold a value."""
        return self._requests.evaluate(
            lineage=self._position.lineage,
            at=at,
            now=now,
            valued=self._position.registers.keys(),
        )

    def _samples(self, evaluation: _Evaluation, now: float) -> list[int]:
        """Write one sample of each source ``evaluation`` fired, its register's
        value (§3: a source fires once per node); their seqs, in order."""
        registers = self._position.registers
        return [self._send_value(n, registers[n], now) for n in evaluation.sources]

    def heartbeat(self) -> bool:
        """Liveness inside a slow iteration (§4): drain, decide the stops at the
        position and the heartbeat's clock, and write a ``lifecycle.heartbeat``.
        It commits nothing and evaluates no subscription. It returns the stop
        decision, so a stop drained mid-iteration whose ``from`` holds, or one whose
        time ``from`` comes due inside the iteration, lands here. The clock is read
        once."""
        if self._phase is not _Phase.LIVE:
            return True
        now = self._now()
        if self._drain(_Mode.ITERATION, now) is None:
            return True
        decision = self._requests.decide(self._position.lineage.tip, now)
        body = Heartbeat(
            claim_seq=self._claim.seq, consumed_seq=self._drained.watermark, t=now
        )
        self._ch.send(asdict(body), topic=Heartbeat.TOPIC)
        return decision

    # ----- the drain (§4, "A drain") -------------------------------------------------

    def _drain(self, mode: _Mode, now: float) -> int | None:
        """Read W; read the death reports naming the claim at or below W, and latch
        ``released`` on one, returning ``None``; else handle each ``control.*``
        record above the last complete drain's W, and return the tail the drain
        last read, its own writes included, which the exit compare-and-appends at.

        Each record a drain writes (``_Write``) is compare-and-appended at that
        tail, and takes effect once it lands (``_Requests.landed``). A lost CAS
        means a record landed unread, perhaps a death report, so the drain runs
        again from the request it was handling: the records before it were
        handled. After ``CAS_ATTEMPTS`` lost CASes in a row it raises
        ``WorkerStarvedError``, having written nothing for that request."""
        for _ in range(CAS_ATTEMPTS):
            w = self._ch.last_seq()
            if self._reported(w):
                self._phase = _Phase.RELEASED
                return None
            tail = self._drain_control(w, mode, now)
            if tail is not None:
                return tail
        raise WorkerStarvedError("drain", self._claim.seq, CAS_ATTEMPTS)

    def _reported(self, w: int) -> bool:
        """Does a ``supervisor.died`` at or below ``w`` name the claim? Presence
        alone, readable or not (§3): a report's other fields are no reason to keep
        writing for a claim it ended."""
        lo = self._drained.reports_read
        if w <= lo:
            return False
        got = self._ch.read(after=lo, topics=[Topic.SUPERVISOR_DIED], limit=w - lo)
        self._drained.reports_read = w
        return any(e.seq <= w and ends(e) == self._claim.seq for e in got)

    def _drain_control(self, w: int, mode: _Mode, now: float) -> int | None:
        """Handle the control records above ``drained_to`` and at or below ``w``;
        the tail, or ``None`` when a write's CAS lost. A complete drain sets
        ``drained_to`` to ``w``: there are at most ``w - drained_to`` records
        there, so the read returned every one."""
        d = self._drained
        tail = w
        if w <= d.drained_to:
            return tail
        for e in self._ch.read(
            after=d.drained_to, topics=[_CONTROL], limit=w - d.drained_to
        ):
            if e.seq > w:
                break
            write = self._requests.take(
                e, mode=mode, now=now, nameless_above=self._claim.previous_watermark
            )
            if write is not None:
                seq = self._ch.send(
                    asdict(write.body),
                    topic=write.body.TOPIC,
                    request_id=write.request_id,
                    expected_seq=tail,
                )
                if seq is None:
                    return None  # run the drain again, from this request
                tail = seq
                self._requests.landed(write)
            d.watermark = d.drained_to = e.seq
        d.drained_to = w
        return tail

    # ----- the driver (§4, "The drivers") -------------------------------------------

    def steps(self, until: Target | None) -> Iterator[int]:
        """Drive the iterations: while ``until`` is not reached at the position
        (``count(until.x, position) >= until.n``) and no stop holds, yield the
        iteration's index, ``count(Commits(), position)``, then ``commit()``. So a
        resumed driver continues from the log, run-absolute, and a rewind inside
        the body moves the next index. ``None`` is no bound: it ends on a stop.

        ``ValueError``, here at the call and before the first iteration, when
        ``until`` names a declared source: its count is 0 forever, so the drive
        would never end (§4; plan, open question 1)."""
        if until is not None and not isinstance(until.x, Commits):
            if until.x in self._sources:
                raise ValueError(
                    f"the target {until.x!r} is a declared source, which counts 0 "
                    "forever: drive to an eager stream or Commits()"
                )
        return self._iterations(until)

    def _iterations(self, until: Target | None) -> Iterator[int]:
        while not self.stop_pending and (
            until is None or self._position.lineage.tip.of(until.x) < until.n
        ):
            yield self._position.lineage.tip.commits
            self.commit()

    def serve(self) -> Iterator[int]:
        """The driver for demand (§4, "The drivers"): while some subscription is
        live for this claim (``pinned``) and no stop holds, yield the iteration's
        index, ``count(Commits(), position)``, as ``steps`` does, then
        ``commit()``. At zero it calls ``retire()``, which drains first: so a
        subscription already on the log when ``serve()`` starts is served from the
        first iteration, and one that arrives as the worker retires keeps it
        serving. It ends on a stop, leaving the exit to the ``with`` block, and
        does nothing for a worker that commits nothing more."""
        while not self.stop_pending:
            if not self.pinned and self.retire():
                return
            yield self._position.lineage.tip.commits
            self.commit()

    # ----- the position (§4, "rewind()", "At every position change") ---------------

    def rewind(self, target: NodeRef | None) -> None:
        """Restate the position (§2): write ``lifecycle.rewind`` naming ``target``,
        an extendable node of the run validated as ``resume_from``'s is, or the root
        for ``None``. Pending values are discarded: they stay on the log outside
        every lineage, so commit first to keep them. ``resume_node`` moves to the
        target.

        The new lineage is walked before the record is written, so a target whose
        lineage is malformed raises with nothing written."""
        if not self._acts():
            return
        node = None if target is None else target.node
        if node is not None:
            _extendable(self._ch, node, self._ch.last_seq())
        lineage = self._position.lineage.to(self._ch, node)
        body = Rewind(claim_seq=self._claim.seq, resume_from=node, t=self._now())
        self._ch.send(asdict(body), topic=Rewind.TOPIC)
        self._iteration.discard()
        self._position = _Position.at(node, lineage)

    # ----- the end of the claim (§4, "discard()", "The exit") ----------------------

    def discard(self) -> None:
        """Declare the open iteration torn (§4): its pending values stay on the log
        outside every lineage. Afterwards ``commit()`` and ``heartbeat()`` write
        nothing, not even a drain, and return True, so every driver ends; ``emit``,
        ``set``, ``include`` and ``rewind`` raise; and the exit writes ``values:
        null`` and evaluates nothing. A torn iteration to be redone is a ``rewind``
        to a checkpoint instead.

        Call it from the worker's own flow, never from inside a signal handler,
        which may interrupt a ``commit()`` between its steps: a handler sets a flag
        the loop reads at its next safe point. A released or stopped worker's, and a
        second, do nothing."""
        if self._phase is not _Phase.LIVE:
            return
        self._phase = _Phase.DISCARDED
        self._iteration.discard()

    def stopped(self, *, completed: bool, error: str | None) -> None:
        """Write the exit node, the claim's last, in order (§4, "The exit"):

        1. read W and drain up to it: the death reports naming the claim, stops and
           withdrawals. No subscription is registered, and every named
           ``control.subscribe`` is passed over, a malformed one included: one that
           arrives after the last commit stays live for the next claim, which
           registers or refuses it;
        2. unless the exit discards its iteration (an error, or after
           ``discard()``), evaluate the registered subscriptions once, at the exit
           node, which adds no iteration;
        3. write their samples once. A failure while writing them makes the exit an
           error exit, torn, with the failure as its ``error``; the samples already
           written stay outside every lineage, and the failure propagates once the
           exit has landed, as a save's does (§4, "The exit", step 3; §6);
        4. read W again, drain as in step 1, and compare-and-append ``stopped`` at
           the drain's tail, with ``parent`` the position, ``values`` the pending
           values then the samples, or null when the exit discards its iteration,
           ``fired`` and ``expired`` step 2's, and ``honored`` every stop that held
           during the claim and every stop pending now, a set whose order means
           nothing (§3). A lost CAS repeats this step only: it does not evaluate
           again, and a stop first drained here is honored;
        5. if the exit is extendable, run the save policy (§6).

        ``completed`` is the worker's claim of intrinsic completion, which closes
        the lineage; a completed exit carries no ``error`` (``ValueError``, checked
        first). A second call does nothing, and so does a lost or released
        worker's.

        After ``CAS_ATTEMPTS`` lost CASes in a row, step 4 raises
        ``WorkerStarvedError``, out of ``stopped()`` and so out of ``__exit__``: no
        exit was written, the claim is still held, and a death report is what ends
        it (§4, "An exit that starves"). Every call but ``stopped()`` is then
        fenced, writing nothing and returning as a stopped worker's does, since the
        exit's drains passed over requests, a subscribe among them, that no
        watermark of the claim may claim. A retried ``stopped()`` repeats step 4 of
        the exit that starved, as a lost CAS does: it does not evaluate again, its
        samples being on the log already, and it writes that exit's verdict and
        ``values``, whatever its own arguments say.

        A save that raises propagates (§6, "A save that raises"). The exit has
        landed and stands with its own verdict, and the worker is stopped, so
        nothing more is written: the failure is the process's, on the external
        plane. The save is never run before the CAS, which would name a seq the exit
        may not get."""
        if completed and error is not None:
            raise ValueError("a completed exit carries no error")
        self._exit(completed=completed, error=error, mode=_Mode.EXIT)

    def retire(self) -> bool:
        """``serve()``'s careful death (§4, "The exit", ``retire()``): the exit,
        preempted, with every control verb drained at both of its reads,
        subscriptions included. False, writing no exit, if any subscription is live
        for the claim (``pinned``) after either read, whenever it arrived: the
        worker serves it. Otherwise True, its exit landed: it fires nothing, since
        nothing is live when it is written, and it is extendable, so a woken
        successor that holds no state continues from it.

        True, writing nothing, for a worker that commits nothing more: a lost, a
        released or a stopped one, or one whose exit starved, which only
        ``stopped()`` retries (§4, "An exit that starves"); and after
        ``discard()``, True once its torn exit has landed, as ``stopped()`` would
        write it (§4, ``retire()``)."""
        if self._phase is _Phase.STARVED:
            return True
        if self._phase is not _Phase.LIVE:
            self.stopped(completed=False, error=None)
            return True
        return self._exit(completed=False, error=None, mode=_Mode.ITERATION)

    def _exit(self, *, completed: bool, error: str | None, mode: _Mode) -> bool:
        """``stopped()``'s steps, its drains in ``mode``: ``EXIT``'s, or for
        ``retire()`` ``ITERATION``'s, whose registrations make it return False,
        having written no exit, when a subscription is live after a drain. True
        once the exit has landed, or when there is nothing to write. A starved
        exit's retry repeats step 4 only, in ``EXIT`` mode, since only
        ``stopped()`` retries it."""
        if self._phase is _Phase.STARVED:
            assert self._starved is not None  # set where the phase is
            return self._after(self._exit_cas(self._starved, mode=_Mode.EXIT))
        if self._phase not in (_Phase.LIVE, _Phase.DISCARDED):
            return True
        retiring = mode is _Mode.ITERATION
        discards = error is not None or self._phase is _Phase.DISCARDED
        if self._drain(mode, self._now()) is None:  # step 1
            return True
        if retiring and self.pinned:
            return False
        evaluation, samples = _NOTHING, []
        failure: Exception | None = None
        if not discards:  # steps 2 and 3, once
            now = self._now()
            evaluation = self._evaluate(self._written(iterations=0), now)
            try:
                samples = self._samples(evaluation, now)
            except Exception as exc:
                failure = exc
                completed, error, discards = False, _described(exc), True
                evaluation, samples = _NOTHING, []
        node = _ExitNode(
            completed=completed,
            error=error,
            values=None if discards else [*self._pending(), *samples],
            evaluation=evaluation,
        )
        landed = self._exit_cas(node, mode=mode)
        if failure is not None:
            raise failure  # once the exit has landed (§4, "The exit", step 3)
        return self._after(landed)

    def _after(self, landed: _Landed | _Unwritten) -> bool:
        """Step 5, once step 4 has returned: the save policy after an extendable
        exit (§6); whether the worker is done, False only when it serves."""
        match landed:
            case _Unwritten.SERVING:
                return False
            case _Unwritten.RELEASED:
                return True
            case _Landed(seq=seq, extendable=extendable):
                if extendable:
                    commits = self._position.lineage.tip.commits  # an exit adds none
                    self._save.after(seq, commits=commits, exit=True)
                return True
            case _:
                assert_never(landed)

    def _pending(self) -> list[int]:
        return [p.seq for p in self._iteration.pending]

    def _exit_cas(self, node: _ExitNode, *, mode: _Mode) -> _Landed | _Unwritten:
        """Step 4: drain in ``mode``, then compare-and-append ``node`` at the
        drain's tail, until it lands; the exit that landed, or why none was
        written. After ``CAS_ATTEMPTS`` lost CASes it holds ``node`` and fences the
        worker (``_Phase.STARVED``) before it raises ``WorkerStarvedError``."""
        for _ in range(CAS_ATTEMPTS):
            now = self._now()
            tail = self._drain(mode, now)
            if tail is None:
                self._starved = None
                return _Unwritten.RELEASED
            if mode is _Mode.ITERATION and self.pinned:
                return _Unwritten.SERVING
            body = Stopped(
                completed=node.completed,
                error=node.error,
                claim_seq=self._claim.seq,
                honored=self._requests.honored(),
                parent=self._position.lineage.node,
                values=node.values,
                fired=node.evaluation.fired,
                expired=node.evaluation.expired,
                t=now,
            )
            seq = self._ch.send(asdict(body), topic=Stopped.TOPIC, expected_seq=tail)
            if seq is None:
                continue  # a record landed unread: drain it, and try again
            self._phase, self._starved = _Phase.STOPPED, None
            return _Landed(seq=seq, extendable=body.extendable)
        self._phase, self._starved = _Phase.STARVED, node
        raise WorkerStarvedError("exit", self._claim.seq, CAS_ATTEMPTS)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        """Write the exit unless the worker already stopped (§4): errored, and so
        torn, on an exception, whose message is its ``error`` (its type's name when
        the message is empty); otherwise preempted, ``stopped(completed=False,
        error=None)``. A lost or released worker writes nothing, and one whose exit
        starved retries that exit (``stopped()``). The exception, if any,
        propagates."""
        if isinstance(exc, BaseException):
            self.stopped(completed=False, error=_described(exc))
        else:
            self.stopped(completed=False, error=None)
