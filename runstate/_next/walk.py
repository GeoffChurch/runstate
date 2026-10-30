"""The lineage walk of log format 0.4.0, staged (docs/specs/commits-and-lineage.md §2,
§5): a node, its kind and its checks, the pure walk back from a head node, and
membership by the committing claim's declaration.

INTERNAL. It is what the reads (``observables``), the Worker's rebuild and
``ensure`` share, and it is not part of ``runstate.__all__``: the public lineage
reads are ``observables``'. Its names carry no leading underscore because other
modules of the package import them.

STAGED. Task 19 of docs/plans/2026-10-28-layer2-live.md moves this module to
``runstate/walk.py``.

THE WALK (§5, "The pure walk"). ``walk(ch, node)`` reads backwards from ``node`` in
bulk windows that grow by 4x (``Backward``), never a point read per node, as far as
the lineage, its values and its claims reach, walks the lineage back to the root,
and checks every node it walks (§5, "Well-formedness"). Loading resolves nothing:
membership raises only where a read resolves it (``value_record``, ``declared``,
``classified``).

THE CHECKS. ``check_node`` is a node's structural check, one message per failure,
which the pure walk and the incremental folds of ``observables`` share. A fold
holds each node's ``NodeKind`` where the walk reads the parent's record, so a
``kind_of`` lookup is all the check asks of its caller.
"""

from __future__ import annotations

from collections.abc import Callable, Container, Iterator
from enum import StrEnum
from types import MappingProxyType
from typing import NamedTuple, TypeVar

from ..channel import Channel, Envelope
from .vocabulary.payloads import Commit, Node, Started, Stopped, Topic
from .vocabulary.schedule import Coordinates

_T = TypeVar("_T")


# ----- malformed records ------------------------------------------------------------


class MalformedRecordError(Exception):
    """A record a read must interpret cannot be (§5, "Well-formedness"): the writer
    violated the convention. Typed and catchable: ``seq`` and ``topic`` name the
    record, ``detail`` says what is wrong with it. A read raises rather than
    guesses, since completeness is well-formedness: a read never waits."""

    def __init__(self, seq: int, topic: str, detail: str) -> None:
        super().__init__(
            f"uninterpretable record at seq {seq} on topic {topic!r}: {detail}"
        )
        self.seq = seq
        self.topic = topic
        self.detail = detail


def verdict_parse(cls: type[_T], e: Envelope) -> _T:
    """``e``'s body parsed as ``cls(**body)``, the writer's convention violation (a
    bad or missing key -> ``TypeError``, a bad value -> ``ValueError``) wrapped in
    the typed ``MalformedRecordError``."""
    try:
        return cls(**e.body)
    except (TypeError, ValueError) as exc:
        raise MalformedRecordError(e.seq, e.topic, str(exc)) from exc


# ----- a readable record ------------------------------------------------------------


def readable(cls: type[_T], e: Envelope) -> _T | None:
    """``e``'s body parsed as ``cls``, or ``None`` when it is unreadable: a fold
    that skips an unreadable record reads no more of it (§5). The claim plane's
    presence predicates (``claims``) read with it."""
    try:
        return verdict_parse(cls, e)
    except MalformedRecordError:
        return None


def point(ch: Channel, seq: int, *, w: int) -> Envelope | None:
    """The record at ``seq``, one point read, or ``None`` when there is none at or
    below ``w``."""
    if not 1 <= seq <= w:
        return None
    got = ch.read(after=seq - 1, limit=1)
    return got[0] if got and got[0].seq == seq else None


# ----- a node, its kind and its checks ----------------------------------------------


class At(NamedTuple):
    """A node: its record, and its parsed body."""

    env: Envelope
    node: Node


def node_at(e: Envelope) -> At | None:
    """The node at ``e``, or ``None`` when its topic is not a node's. A node's
    unreadable body raises ``MalformedRecordError``."""
    if e.topic == Topic.LIFECYCLE_COMMIT:
        return At(e, verdict_parse(Commit, e))
    if e.topic == Topic.LIFECYCLE_STOPPED:
        return At(e, verdict_parse(Stopped, e))
    return None


class NodeKind(StrEnum):
    """A node's kind, from its body alone (§2, "Nodes"): only an extendable node
    may be extended, by a ``parent`` or a ``resume_from``."""

    EXTENDABLE = "extendable"
    CLOSED = "closed"
    TORN = "torn"


def kind(node: Node) -> NodeKind:
    if node.extendable:
        return NodeKind.EXTENDABLE
    return NodeKind.CLOSED if node.closed else NodeKind.TORN


def kind_at(e: Envelope | None) -> NodeKind | None:
    """The kind of the node at ``e``, or ``None`` when ``e`` is no node's record (or
    no record). A node's unreadable body raises ``MalformedRecordError``."""
    at = None if e is None else node_at(e)
    return None if at is None else kind(at.node)


# What a check asks of its caller: the kind of the node at a seq, ``None`` when no
# node is there, raising the node's own ``MalformedRecordError`` when its body is
# unreadable.
KindOf = Callable[[int], NodeKind | None]


def check_reference(
    referrer: Envelope, field: str, target: int, kind_of: KindOf
) -> None:
    """``referrer``'s ``field`` (a ``parent`` or a ``resume_from``) names
    ``target``, which must lie below it and be an extendable node (§5,
    "Well-formedness")."""
    if target >= referrer.seq:
        raise MalformedRecordError(
            referrer.seq, referrer.topic, f"{field} {target} is at or above its own seq"
        )
    k = kind_of(target)
    if k is None:
        raise MalformedRecordError(
            referrer.seq, referrer.topic, f"{field} {target} names no node"
        )
    if k is not NodeKind.EXTENDABLE:
        raise MalformedRecordError(
            referrer.seq,
            referrer.topic,
            f"{field} {target} names a {k} node, not an extendable one",
        )


def check_node(at: At, kind_of: KindOf) -> None:
    """A node's structural check (§5, "Well-formedness"), in one order: its
    ``values`` seqs below its own seq, then its ``parent`` (``check_reference``)."""
    seq, node = at.env.seq, at.node
    later = [s for s in node.committed if s >= seq]
    if later:
        raise MalformedRecordError(
            seq, at.env.topic, f"values names {later}, at or above its own seq"
        )
    if node.parent is not None:
        check_reference(at.env, "parent", node.parent, kind_of)


def not_a_value(at: At, seq: int) -> MalformedRecordError:
    """The error of a node whose ``values`` names a seq that is no ``value`` record
    (§5, "Membership")."""
    return MalformedRecordError(
        at.env.seq, at.env.topic, f"values names {seq}, which is not a value"
    )


def no_claim(at: At) -> MalformedRecordError:
    """The error of a node whose ``claim_seq`` names no ``lifecycle.started`` below
    it, which membership resolves (§2, "Membership")."""
    return MalformedRecordError(
        at.env.seq,
        at.env.topic,
        f"claim_seq {at.node.claim_seq} names no lifecycle.started below it",
    )


# ----- the pure walk ----------------------------------------------------------------


class Walked(NamedTuple):
    """A lineage walked: the records the walk read, by seq (every record from the
    lowest it needed up to its head node: the lineage's nodes, their values and
    their claims among them), and its nodes, root first."""

    records: dict[int, Envelope]
    nodes: list[At]


# The first backward window, in rows; each later one is 4x the last. Exempt: it sets
# how many rows each bulk read asks for, never which records a read returns.
FIRST_WINDOW = 64


class Backward:
    """The records at or below ``hi``, read backwards in bulk windows that grow by
    4x (§5, "The pure walk"): a record below what is loaded loads windows until it
    is, so a walk reads O(the distance back to the lowest record it needs) rows in
    O(log) reads, whatever the log's length below that."""

    def __init__(self, ch: Channel, hi: int) -> None:
        self._ch = ch
        self._hi = hi
        self._lo = hi  # the records in (lo, hi] are loaded
        self._width = FIRST_WINDOW
        self.records: dict[int, Envelope] = {}

    def get(self, seq: int) -> Envelope | None:
        """The record at ``seq``, or ``None`` when there is none at or below
        ``hi``."""
        if not 1 <= seq <= self._hi:
            return None
        while seq <= self._lo:
            lo = max(0, self._lo - self._width)
            for e in self._ch.read(after=lo, limit=self._lo - lo):
                if e.seq <= self._lo:
                    self.records[e.seq] = e
            self._lo, self._width = lo, self._width * 4
        return self.records.get(seq)


def walk(ch: Channel, node: int | None) -> Walked:
    """The lineage ending at ``node`` (``None``, the empty lineage), checked (§5):
    every node walked by ``check_node``, and along the lineage, a seq committed
    twice. A ``node`` that is not a node raises ``ValueError``.

    It reads backwards from ``node`` (``Backward``), and loads what a read may
    resolve: each node's values, and its claim's ``started`` when it lies below
    the node. Loading resolves nothing: membership raises only when a read
    resolves it."""
    if node is None:
        return Walked({}, [])
    back = Backward(ch, node)
    first = back.get(node)
    at = None if first is None else node_at(first)
    if at is None:
        raise ValueError(f"head node {node} is not a node")
    chain = [at]
    while True:
        check_node(at, lambda s: kind_at(back.get(s)))
        if at.node.parent is None:
            break
        e = back.get(at.node.parent)
        parent = None if e is None else node_at(e)
        assert parent is not None  # check_node found a node there
        at = parent
        chain.append(at)
    chain.reverse()
    seen: set[int] = set()
    for at in chain:
        for s in at.node.committed:
            if s in seen:
                raise MalformedRecordError(
                    at.env.seq,
                    at.env.topic,
                    f"value seq {s} is committed twice along one lineage",
                )
            seen.add(s)
            back.get(s)
        if at.node.claim_seq < at.env.seq:
            back.get(at.node.claim_seq)
    return Walked(back.records, chain)


# ----- membership (§2, "Membership") -------------------------------------------------


def value_record(walked: Walked, at: At, seq: int) -> Envelope:
    """The ``value`` record at ``seq``, which ``at`` commits: a ``values`` seq
    naming a record that is not a ``value`` raises (§5, "Membership, checked only
    for the records a read resolves")."""
    e = walked.records.get(seq)
    if e is None or e.topic != Topic.VALUE:
        raise not_a_value(at, seq)
    return e


def declared(walked: Walked, at: At) -> frozenset[str]:
    """The sources declared by the claim that wrote ``at``."""
    claim = at.node.claim_seq
    e = walked.records.get(claim) if claim < at.env.seq else None
    if e is None or e.topic != Topic.LIFECYCLE_STARTED:
        raise no_claim(at)
    return frozenset(verdict_parse(Started, e).sources)


class _Every:
    """Every name: the ``among`` of a read that classifies all of them."""

    def __contains__(self, name: object) -> bool:
        return True


EVERY_NAME: Container[str] = _Every()


class Classified(NamedTuple):
    """A committed, named value on a lineage: its node, its seq, its name, and
    whether the claim that committed it declared the name a source."""

    at: At
    seq: int
    name: str
    is_source: bool


def classified(walked: Walked, *, among: Container[str]) -> Iterator[Classified]:
    """Each committed value on the lineage whose name is ``among`` (``EVERY_NAME``,
    or the names a read asks about), in lineage order, classified by the
    committing claim's declaration (§2, "Membership").

    It resolves every value seq on the lineage, so one naming a record that is
    not a ``value`` raises; and, for each value it yields, the committing claim's
    ``started``, which must lie below its node. A nameless value is in no
    stream."""
    sources: dict[int, frozenset[str]] = {}
    for at in walked.nodes:
        for s in at.node.committed:
            name = value_record(walked, at, s).name
            if name is None or name not in among:
                continue
            claim = at.node.claim_seq
            if claim not in sources:
                sources[claim] = declared(walked, at)
            yield Classified(at, s, name, name in sources[claim])


def members(walked: Walked, stream: str) -> list[tuple[At, int]]:
    """(node, value seq) of each element of ``stream``, in lineage order.

    Membership is decided by the envelope, never the body (§5): a committed
    ``value`` named ``stream`` is an element if and only if the claim that
    committed it, the node's ``claim_seq``, did not declare ``stream`` a source
    (§2, "Membership")."""
    return [
        (c.at, c.seq) for c in classified(walked, among=(stream,)) if not c.is_source
    ]


def coordinates(walked: Walked) -> list[Coordinates]:
    """The coordinates at and including each node of the lineage, root first
    (§3, "Evaluation"): ``count(X, node)`` for every eager stream X with an
    element there, by the committing claim's declaration, and ``count(Commits(),
    node)``. It resolves every value on the lineage."""
    eager: dict[int, list[str]] = {}
    for c in classified(walked, among=EVERY_NAME):
        if not c.is_source:
            eager.setdefault(c.at.env.seq, []).append(c.name)
    out: list[Coordinates] = []
    counts: dict[str, int] = {}
    commits = 0
    for at in walked.nodes:
        for name in eager.get(at.env.seq, ()):
            counts[name] = counts.get(name, 0) + 1
        commits += at.node.iterations
        out.append(Coordinates(MappingProxyType(dict(counts)), commits))
    return out
