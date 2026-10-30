"""The observer plane of log format 0.4.0, staged (docs/specs/commits-and-lineage.md
§2, §5): the heads, the pure lineage reads, the claim plane, the answer fold and the
incremental folds.

STAGED. Task 19 of docs/plans/2026-10-28-layer2-live.md moves this module over
``runstate/observables.py``. It replaces the provisional ``runstate/lineage.py``,
whose reads the migration round's corpus runner measured migrated logs with: they
live here now, beside the reads §5 adds (``prefix``, ``aligned``, ``answers``,
``streams``), so a fix lands once. They read a log through the Channel surface
alone (``last_seq``, ``read``, ``latest``), and parse 0.4.0 bodies with the staged
vocabulary.

THE MODEL (§2). A node is a ``lifecycle.commit`` or a worker's exit
(``lifecycle.stopped``), named by its seq. It names the node it extends
(``parent``) and the values it commits (``values``), all below its own seq. A
lineage is the chain of nodes from a head, by ``parent``. A stream's elements are
its values committed along a lineage, in lineage order, and within a node in the
order of its ``values``. A value no node on the lineage names is not an element.

THE HEAD. Every read that depends on a branch takes a required, keyword-only
``head: HeadStrategy``, chosen once at the edge, and resolves it to a ``Head(node,
settled, w)``. ``LatestClaimHead`` takes the newest claim, by the claim CAS, then
that writer's own order, which the log's seq order is within one writer: the
claim's readable exit if it has one, else its position. ``AtNode(x)`` takes ``x``.

THE PURE WALK (``walk``, the internal module the reads, the Worker and ``ensure``
share). Each read takes ``w`` first and reads nothing above it, and walks the
lineage from its head node back to the root in bulk windows, checking every node
it walks (§5, "Well-formedness"). These are one-shot reads, never polled. Nothing
is cached between calls.

THE CLAIM PLANE (§2, §5). A claim has ended once its exit, or a death report
naming it, is on the log, readable or not: the one predicate the gate, lease
voiding, the verdict and the Watcher share. ``claim_verdict`` is not ``None``
exactly where it holds. The verdict is the claim's readable exit, else the death
reports naming it, whose outcomes are kept as a set. It reads only the claim's
window, the records after it, since a record cannot speak for a claim it never
saw. A run nothing claimed takes its launch's ``supervisor.terminated``. No read
probes a handle and none writes.

THE ANSWER FOLD (§2 "Requests", §5) reads each request kind in its own scope: a
request is spent by what names it (an exit's ``honored``, a withdrawal, a
refusal), wherever that sits; a lease also by any node's ``expired``, its claim's,
and it is void once its bound claim ended; a durable subscription by an
``expired`` on the head's lineage only. There ``ended`` is the presence of a
record naming the claim (``ends``), which never raises, so a malformed record in
an old claim's window cannot poison the present: the verdict is where that
contradiction raises.

THE INCREMENTAL FOLDS (§5, "Bounded reads") are the polled forms, public and
standalone, since ``ensure``'s loop has no Watcher and the TUI may use public API
only: ``HeadFold``, ``PrefixLen``, ``LastElement``, ``Answers``, ``LiveDemand``
and ``PendingStops``. Each holds its state and reads only the records above its
cursor, so a poll costs O(new records), and equals its pure read after every
record. Each pure read and its fold share one accumulator (``_ClaimHead``,
``_Demand``, ``_Stops``) or one node check (``check_node``), so a rule is written
once.
"""

from __future__ import annotations

import sys
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Mapping, Sequence, Set as AbstractSet
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Generic, NamedTuple, Protocol, TypeVar

from ..channel import Channel, Envelope
from .vocabulary.coordinate import Commits
from .vocabulary.payloads import (
    Commit,
    Died,
    Outcome,
    Reap,
    Rewind,
    Started,
    Stopped,
    Terminated,
    Topic,
    Value,
)
from .claims import (
    Bindings,
    backward,
    claim_window,
    named_claim,
    request_name,
    spent_by,
)
from .vocabulary.schedule import MalformedSchedule, is_lease, parse_schedule
from .walk import (
    EVERY_NAME,
    FIRST_WINDOW,
    At,
    KindOf,
    MalformedRecordError as MalformedRecordError,
    NodeKind,
    Walked,
    check_node,
    check_reference,
    classified,
    kind,
    kind_at,
    members,
    no_claim,
    node_at,
    not_a_value,
    point,
    readable,
    value_record,
    verdict_parse,
    walk,
)

# ----- the heads (§5) ---------------------------------------------------------------


@dataclass(frozen=True)
class Head:
    """A resolved head (§5): ``node``, the head node's seq, or ``None`` for the
    empty lineage; ``settled``, whether the read's subject can no longer grow;
    and ``w``, the ``last_seq()`` the head was resolved at, above which no read
    that takes it reads.

    A ``Head`` is itself a ``HeadStrategy`` that resolves to itself, so a polled
    caller resolves once per poll and passes the value to every read in it."""

    node: int | None
    settled: bool
    w: int

    def __post_init__(self) -> None:
        if self.node is None:
            if self.settled:
                raise ValueError("the empty lineage is not settled")
        elif not 1 <= self.node <= self.w:
            raise ValueError(f"a head node is a seq from 1 to w={self.w}")

    def resolve(self, ch: Channel) -> Head:
        return self


class HeadStrategy(Protocol):
    """How a read chooses its head (§5), chosen once at the edge."""

    def resolve(self, ch: Channel) -> Head: ...


@dataclass(frozen=True)
class LatestClaimHead:
    """The newest claim's head (§2, §5): the order among claims, which the claim
    CAS fixes, then that writer's own order. No clocks, and never an older
    claim's head, though a commitless claim's head may be an older claim's node.
    The read is settled if and only if the head is closed."""

    def resolve(self, ch: Channel) -> Head:
        return _latest_claim_head(ch, ch.last_seq())


@dataclass(frozen=True)
class AtNode:
    """That node (§5): forensics, or reading an abandoned branch. Always settled,
    since the lineage ending at a node is fixed. Also a ``NodeRef`` (§4).

    Resolving raises ``ValueError`` on a seq that is not a node or is above w, and
    ``MalformedRecordError`` on a node whose body is unreadable."""

    node: int

    def resolve(self, ch: Channel) -> Head:
        w = ch.last_seq()
        e = point(ch, self.node, w=w)
        if e is None:
            raise ValueError(f"AtNode({self.node}): no record at or below w={w}")
        if node_at(e) is None:
            raise ValueError(f"AtNode({self.node}): a {e.topic} is not a node")
        return Head(self.node, True, w)


# ----- what the reads return --------------------------------------------------------


@dataclass(frozen=True)
class Element:
    """One element of an eager stream, or one sample of a source (§5): the node
    that committed it, the value's seq, and its body's ``value`` and ``t``."""

    node: int
    seq: int
    value: Any
    t: float | None


@dataclass(frozen=True)
class Complete:
    """``prefix``'s first ``n`` elements, at the head ``node`` it evaluated. Only
    this outcome has ``elements`` (§5): on the union, ``.elements`` is a type
    error, so a caller tells the outcomes apart before it reads."""

    elements: tuple[Element, ...]
    node: int | None


@dataclass(frozen=True)
class Pending:
    """Fewer than ``n`` elements, and the read is not settled: more may come."""

    partial: tuple[Element, ...]
    node: int | None


@dataclass(frozen=True)
class SettledShort:
    """Fewer than ``n`` elements, and the read is settled: no more will ever come."""

    partial: tuple[Element, ...]
    node: int | None


class AlignedRow(NamedTuple):
    """One node that commits any of ``aligned``'s names (§5): ``x``, the axis's
    count at the node; ``node``; and per name its elements there, in the node's
    order, ``()`` for a name it does not commit."""

    x: int
    node: int
    cells: Mapping[str, tuple[Element, ...]]


class Streams(NamedTuple):
    """The names with a committed value on a lineage (§5), each by the committing
    claim's declaration: a name that changed kind is in both."""

    eager: frozenset[str]
    sources: frozenset[str]


# ----- the reads (§5) ---------------------------------------------------------------


def prefix_len(ch: Channel, x: str | Commits, *, head: HeadStrategy) -> int:
    """``count(x, head)`` (§5): what ``progress`` was, as a count, for an eager
    stream or ``Commits()``. A name that was a source throughout counts 0."""
    return _count(walk(ch, head.resolve(ch).node), x)


def prefix(
    ch: Channel, stream: str, n: int, *, head: HeadStrategy
) -> Complete | Pending | SettledShort:
    """The threshold read (§5): the first ``n`` elements of ``stream`` at the head,
    ``Complete``; else the elements found, ``SettledShort`` when the read is
    settled and ``Pending`` when it is not. At ``n = 0`` it is ``Complete((),
    node)``, the empty lineage's included. It reads the bodies of the elements it
    returns, so a malformed one among them raises. ``n < 0`` raises
    ``ValueError``."""
    if n < 0:
        raise ValueError(f"n must be >= 0, got {n!r}")
    h = head.resolve(ch)
    if n == 0:
        return Complete((), h.node)
    walked = walk(ch, h.node)
    found = tuple(_element(walked, at, s) for at, s in members(walked, stream)[:n])
    if len(found) == n:
        return Complete(found, h.node)
    if h.settled:
        return SettledShort(found, h.node)
    return Pending(found, h.node)


def series(ch: Channel, stream: str, *, head: HeadStrategy) -> list[Element]:
    """One eager stream's elements in lineage order (§5). It reads every
    element's body, so a malformed one raises."""
    walked = walk(ch, head.resolve(ch).node)
    return [_element(walked, at, s) for at, s in members(walked, stream)]


def last_element(ch: Channel, stream: str, *, head: HeadStrategy) -> Element | None:
    """The last element in lineage order, or ``None`` (§5). That is not always
    the newest by seq: ``include`` can commit an older value after a newer one.
    It reads the last element's body only."""
    walked = walk(ch, head.resolve(ch).node)
    found = members(walked, stream)
    if not found:
        return None
    at, s = found[-1]
    return _element(walked, at, s)


def aligned(
    ch: Channel, names: Sequence[str], *, head: HeadStrategy, axis: str | Commits
) -> list[AlignedRow]:
    """Values aligned by node (§5): one row per node on the lineage that commits a
    value of any of ``names``, in lineage order. The row's ``x`` is ``count(axis,
    node)``, 0 before the axis's first element, so rows share an x where the axis
    is sparser than the metric. Each cell is a tuple, empty for an absent name and
    one element per value, so a value that is itself a JSON array is still one
    element. Sources' samples appear here, keyed by node: a cell is every value of
    its name the node commits, whatever its kind. It reads the bodies of the
    values in its cells."""
    walked = walk(ch, head.resolve(ch).node)
    wanted = frozenset(names)
    rows: list[AlignedRow] = []
    for at, x in zip(walked.nodes, _axis(walked, axis), strict=True):
        cells: dict[str, list[Element]] = {n: [] for n in names}
        for s in at.node.committed:
            name = value_record(walked, at, s).name
            if name is not None and name in wanted:
                cells[name].append(_element(walked, at, s))
        if any(cells.values()):
            frozen = {n: tuple(v) for n, v in cells.items()}
            rows.append(AlignedRow(x, at.env.seq, MappingProxyType(frozen)))
    return rows


def answers(ch: Channel, request_id: str, *, head: HeadStrategy) -> list[Element]:
    """The answers to a subscription on the head's lineage (§5): at each node
    whose ``fired`` names ``request_id``, the sample of its target that the node
    commits, one per firing, in lineage order. A pure demand has none, since it
    never fires.

    The target is the envelope ``name`` of the ``control.subscribe`` the id names,
    fixed at its first send (§3: a re-send that changes it is refused). A node
    that fires a request no subscribe names, or a pure demand, or that commits
    other than exactly one sample of the target (§3, "A source fires once per
    node"), is malformed: ``MalformedRecordError``. The read resolves the values
    of the nodes that fire, and reads the samples' bodies."""
    h = head.resolve(ch)
    walked = walk(ch, h.node)
    target = _subscription_target(ch, request_id, h.w)
    out: list[Element] = []
    for at in walked.nodes:
        if request_id not in at.node.fired:
            continue
        if target is None:
            raise _unsubscribed(at.env.seq, at.env.topic, request_id)
        samples = [
            s for s in at.node.committed if value_record(walked, at, s).name == target
        ]
        if len(samples) != 1:
            raise _not_one_sample(
                at.env.seq, at.env.topic, request_id, target, len(samples)
            )
        out.append(_element(walked, at, samples[0]))
    return out


def _unsubscribed(seq: int, topic: str, request_id: str) -> MalformedRecordError:
    """The error of the node at ``seq`` whose ``fired`` names a request no source
    subscription names: none, or a pure demand, which never fires."""
    return MalformedRecordError(
        seq,
        topic,
        f"fired names {request_id!r}, which no source subscription names",
    )


def _not_one_sample(
    seq: int, topic: str, request_id: str, target: str, n: int
) -> MalformedRecordError:
    """The error of the node at ``seq`` that fires a request and commits ``n``
    samples of its target, not one (§3, "A source fires once per node")."""
    return MalformedRecordError(
        seq,
        topic,
        f"fired names {request_id!r}, and commits {n} samples of its target "
        f"{target!r}, not one",
    )


def streams(ch: Channel, *, head: HeadStrategy) -> Streams:
    """The names with a committed value on the lineage (§5), two sets, each by the
    committing claim's declaration (§2, "Membership"): ``sources`` the names some
    committing claim declared, ``eager`` the names some committing claim did not.
    A name that changed kind is in both. It resolves every value on the lineage."""
    eager: set[str] = set()
    sources: set[str] = set()
    for c in classified(walk(ch, head.resolve(ch).node), among=EVERY_NAME):
        (sources if c.is_source else eager).add(c.name)
    return Streams(frozenset(eager), frozenset(sources))


# ----- the claim plane (§2, §5) -----------------------------------------------------


class Tier(StrEnum):
    """The viewpoint a verdict rests on (§5), a closed enum. A pure read's three,
    one per record kind: ``EXIT``, the claim's own exit; ``DIED``, the death
    reports and unreadable records that ended it; ``TERMINATED``, an unclaimed
    run's launch. The Watcher's inferences: ``PROBED_DEAD``, the claim's handle
    probed dead; ``HEARTBEAT_STALE``, its witnessed liveness went stale; and
    ``SESSION_ENDED``, the session that wrote the claim ended (named for its
    evidence, the session probe; plan, open question 16)."""

    EXIT = "exit"
    DIED = "died"
    TERMINATED = "terminated"
    PROBED_DEAD = "probed_dead"
    HEARTBEAT_STALE = "heartbeat_stale"
    SESSION_ENDED = "session_ended"


@dataclass(frozen=True)
class DeathReport:
    """A readable ``supervisor.died`` naming the claim (§5): its seq, outcome,
    evidence, which no fold parses, and its writer's ``t``."""

    seq: int
    outcome: Outcome
    evidence: str
    t: float


@dataclass(frozen=True)
class RunResult:
    """The verdict (§5), the terminal arm of ``RunStatus``:

    - ``outcomes``: never empty. Exactly one under an exit, a launch's
      ``terminated`` or an inference; under death reports, the set of their
      outcomes, a ``lost``
      for each unreadable record that ended the claim, less ``lost`` beside
      another. A consumer's policy tests the set, as ``ensure`` does with "any of
      its outcomes in ``Outcome.failures()``". There is no ``success``;
    - ``reports``: the readable death reports naming the claim, in seq order,
      under ``DIED`` only;
    - ``node``: the exit node's seq under ``EXIT``, else ``None``;
    - ``claim_seq``: the claim the verdict is about: required under every tier
      but ``TERMINATED``, a run nothing claimed, where it is ``None``;
    - ``malformed``: the seqs of the unreadable records the verdict skipped, so
      they are reported, not raised: under ``EXIT`` and ``DIED`` only, the tiers
      that read a claim's window;
    - ``tier``: the viewpoint;
    - ``error``: the exit's error under ``EXIT``, else ``None``: under ``DIED``
      the evidence is in ``reports``;
    - ``run_id``: the run, which the Watcher stamps; a pure read of a channel
      cannot know it, and passes ``None``."""

    outcomes: frozenset[Outcome]
    reports: tuple[DeathReport, ...]
    node: int | None
    claim_seq: int | None
    malformed: tuple[int, ...]
    tier: Tier
    error: str | None
    run_id: str | None

    def __post_init__(self) -> None:
        if not self.outcomes:
            raise ValueError("a verdict's outcomes are never empty")
        if len(self.outcomes) != 1 and self.tier != Tier.DIED:
            raise ValueError("a verdict has one outcome, but under death reports")
        if (self.node is not None) != (self.tier == Tier.EXIT):
            raise ValueError("a verdict names its node under an exit, and only there")
        if (self.claim_seq is None) != (self.tier == Tier.TERMINATED):
            raise ValueError(
                "a verdict names its claim, but a launch's: an unclaimed run's"
            )
        if self.malformed and self.tier not in (Tier.EXIT, Tier.DIED):
            raise ValueError(
                "a verdict reports malformed records under an exit or death "
                "reports only, the tiers that read a claim's window"
            )
        if self.reports and self.tier != Tier.DIED:
            raise ValueError("a verdict carries reports under death reports only")
        if self.error is not None and self.tier != Tier.EXIT:
            raise ValueError("a verdict carries an error under an exit only")

    @property
    def done(self) -> bool:
        """A ``RunResult`` is the terminal arm of ``RunStatus``."""
        return True


def claim_verdict(ch: Channel, claim: Envelope, *, w: int) -> RunResult | None:
    """How ``claim`` ended, at ``w``, or ``None`` while it is held (§2, §5). A claim
    has **ended** if and only if this is not ``None``, where it does not raise:
    the one predicate the folds, the Worker and the launchers use (§2, "How a
    claim ended"). The answer fold reads the same predicate as the presence of a
    record that ends the claim (``ends``), which never raises.

    The claim's readable exit, its self-report, is the verdict (``Tier.EXIT``).
    Else the death reports naming it are (``Tier.DIED``): their outcomes as a set,
    less ``lost`` beside another, so the verdict does not depend on their order
    and only grows as reports arrive. An unreadable ``stopped`` or ``died`` whose
    ``claim_seq`` names the claim still ends it, and counts as a ``lost``: it says
    only that the claim ended. No more of its body is read. Its seq is in
    ``malformed``, and so is an unreadable one in the claim's window naming no
    claim, which may have been the claim's own; one naming another claim is that
    claim's. While the claim is held, though, nameless junk is reported nowhere.

    It reads only the claim's window (``claim_window``): a record cannot speak for a
    claim it never saw, and a malformed record from a dead past cannot poison it.
    A second readable exit of the claim raises ``MalformedRecordError``, as the
    head fold does: §2 allows one per claim. A ``supervisor.terminated`` ends no
    claim: no fold matches one to a claim (§2).

    The claim's ``started`` body, its commits and rewinds, and the exit's
    ``parent`` are not read here: they are the head fold's checks (§5,
    "Well-formedness"), so ``Tier.EXIT`` at a node does not mean the head fold
    accepts the run."""
    exit_: _Exit | None = None
    reports: list[DeathReport] = []
    ended_unread = False  # an unreadable record naming the claim ended it
    malformed: list[int] = []
    for e in claim_window(
        ch, claim, w, [Topic.LIFECYCLE_STOPPED, Topic.SUPERVISOR_DIED]
    ):
        named = named_claim(e)
        if named is not None and named != claim.seq:
            continue  # another claim's
        body: Stopped | Died | None = (
            readable(Stopped, e)
            if e.topic == Topic.LIFECYCLE_STOPPED
            else readable(Died, e)
        )
        if body is None:
            malformed.append(e.seq)
            ended_unread = ended_unread or named is not None
        elif isinstance(body, Died):
            reports.append(DeathReport(e.seq, body.outcome, body.evidence, body.t))
        elif exit_ is not None:
            raise MalformedRecordError(
                e.seq,
                e.topic,
                f"claim {claim.seq} exited at seq {exit_.env.seq}: "
                "a claim has at most one exit",
            )
        else:
            exit_ = _Exit(e, body)
    if exit_ is not None:
        return RunResult(
            outcomes=frozenset({_self_report(exit_.stop)}),
            reports=(),
            node=exit_.env.seq,
            claim_seq=claim.seq,
            malformed=tuple(malformed),
            tier=Tier.EXIT,
            error=exit_.stop.error,
            run_id=None,
        )
    outcomes = {r.outcome for r in reports}
    if ended_unread:
        outcomes.add(Outcome.LOST)
    if not outcomes:
        return None  # held
    if len(outcomes) > 1:
        outcomes.discard(Outcome.LOST)  # it says only what every report says
    return RunResult(
        outcomes=frozenset(outcomes),
        reports=tuple(reports),
        node=None,
        claim_seq=claim.seq,
        malformed=tuple(malformed),
        tier=Tier.DIED,
        error=None,
        run_id=None,
    )


def peek_terminal(ch: Channel) -> RunResult | None:
    """How the run ended (§5), at a W taken first, or ``None`` while the newest
    claim is held, or nothing has ended: ``claim_verdict`` of the newest claim.

    A run nothing claimed at or below W takes its launch's ``terminated``
    (``Tier.TERMINATED``), as 0.3.0's null-worker startup crash does: the newest
    at or below W, whichever launch it names, a backstop's that no ``launched``
    names included. A signal reads ``killed``, an exit 0 ``completed`` and any
    other exit ``errored``. The verdict rests on that one record, so an
    unreadable one, or one naming no launch, raises ``MalformedRecordError``.
    §5 defines readability for ``stopped`` and ``died`` only, and this raise is
    carried over from 0.3.0: that ``terminated`` is the run's present verdict,
    not a record from a dead past, so the read refuses to guess rather than skip
    it."""
    w = ch.last_seq()
    claim = _newest(ch, Topic.LIFECYCLE_STARTED, w)
    if claim is None:
        return _launch_end(ch, w)
    return claim_verdict(ch, claim, w=w)


def latest_episode(ch: Channel) -> Envelope | None:
    """The newest ``lifecycle.started``, or ``None`` if nothing ever claimed:
    unchanged (§5). *Latest* means latest, live or ended alike; its seq is the
    claim's name, which every record speaking for it carries as ``claim_seq``,
    and its rank, since the claim CAS orders claims."""
    return ch.latest(Topic.LIFECYCLE_STARTED)


def live_episode(ch: Channel) -> str | None:
    """The newest claim's handle unless that claim has ended (§2, §5): the claim
    gate. ``None`` when nothing claimed, or the claim's exit or a death report
    naming it is on the log, an unreadable one whose ``claim_seq`` parses
    included (``claim_verdict``).

    It never probes a handle: a dead process with no report reads live. Its probe
    moved to ``report_if_dead``, which the actors call before they act (§3), so a
    read stays pure and a death is a record every reader agrees on. An unreadable
    claim raises ``MalformedRecordError``: the gate refuses to guess."""
    w = ch.last_seq()
    claim = _newest(ch, Topic.LIFECYCLE_STARTED, w)
    if claim is None or claim_verdict(ch, claim, w=w) is not None:
        return None
    return verdict_parse(Started, claim).handle


def worker_completed(result: RunResult | None) -> bool:
    """Did the worker close its lineage (§5: "the head is closed")? True if and
    only if the verdict is the claim's own exit, and it completed. A death report
    is never a completion, and a launch's ``terminated`` exit 0 is a fact about a
    process, which no longer enters a claimed run's verdict: an unclaimed run's
    reads ``completed`` under ``TERMINATED``, and is not this (plan, open
    question 14)."""
    return (
        result is not None
        and result.tier == Tier.EXIT
        and result.outcomes == {Outcome.COMPLETED}
    )


# The records whose ``t`` dates a run's activity (§5): the worker's own, and its
# launch. Never a death report or a ``terminated``: each is dated when its writer
# saw an end, maybe hours after the run last did anything (claim-eviction.md §3).
_DATED_TOPICS = (
    Topic.LIFECYCLE_STARTED,
    Topic.LIFECYCLE_COMMIT,
    Topic.LIFECYCLE_HEARTBEAT,
    Topic.LIFECYCLE_REWIND,
    Topic.LIFECYCLE_STOPPED,
    Topic.SUPERVISOR_LAUNCHED,
)


def last_activity(ch: Channel) -> float | None:
    """When the run last did anything, from the log alone (observer-clock.md): the
    largest ``t`` among the newest record of each dated topic, or ``None`` when it
    has none. The largest of a few finalists, not the ``t`` of the newest record
    by seq, which skew can put behind, nor the largest over the whole log, which
    one fast clock would pin forever. A measurement fold: a junk ``t`` is skipped,
    never raised. A ``value``'s ``t`` is the data plane's and is not read."""
    ts: list[float] = []
    for topic in _DATED_TOPICS:
        e = ch.latest(topic)
        if e is not None:
            t = e.body.get("t")
            if isinstance(t, (int, float)) and not isinstance(t, bool) and t >= 0:
                ts.append(float(t))
    return max(ts) if ts else None


def current_heartbeat(ch: Channel, claim: Envelope | None) -> Envelope | None:
    """The claim's newest commit or heartbeat, the records that carry its
    ``consumed_seq`` (§5), or ``None``. Latest-then-verify: the newest of the two
    on the log usually names the claim, so one ``latest`` per topic; on a miss (a
    displaced worker writing over its successor) the tail is searched newest
    first in windows that grow by 4x back toward the claim, so a miss costs the
    distance to the claim's newest, not its age. Tolerant: a record naming no
    claim names nothing."""
    if claim is None:
        return None
    newest = [
        e
        for e in (
            ch.latest(Topic.LIFECYCLE_COMMIT),
            ch.latest(Topic.LIFECYCLE_HEARTBEAT),
        )
        if e is not None and e.seq > claim.seq
    ]
    if not newest:
        return None
    top = max(newest, key=lambda e: e.seq)
    if named_claim(top) == claim.seq:
        return top
    hi, width = top.seq, FIRST_WINDOW
    while hi > claim.seq:
        lo = max(claim.seq, hi - width)
        window = ch.read(
            after=lo, topics=[Topic.LIFECYCLE_COMMIT, Topic.LIFECYCLE_HEARTBEAT]
        )
        for e in reversed(window):
            if e.seq <= hi and named_claim(e) == claim.seq:
                return e
        hi, width = lo, width * 4
    return None


# ----- the answer fold (§2 "Requests", §5) ------------------------------------------


def live_demand(ch: Channel) -> list[Envelope]:
    """The live subscriptions at a W taken first (§2, §5, "The answer fold"): each
    ``control.subscribe`` id's newest send, ascending by seq, unless

    - it is **spent by name**: a ``control.withdraw``, a ``lifecycle.nak`` or an
      exit's ``honored`` names it, wherever it sits (``claims.spent_by``). Request
      ids are one namespace, so a
      subscribe reusing a stop's honored id is dead on arrival, as the Worker
      skips it; a spent id stays spent, so a later send reusing it is dead on
      arrival. A subscribe with a missing or empty id is nameless, and never
      live;
    - it is **expired** in its class's scope: a lease once any node's
      ``expired`` names it, since its expiry is its claim's; a durable
      subscription once a node on the head's lineage (``LatestClaimHead``'s node
      and its ancestors) does, so one another lineage expired is live demand
      again while the head stands on a lineage that has not (§2, "Per lineage",
      amended 2026-10-30: the decision to launch a worker and the decision to keep
      serving read demand by the same rules);
    - it is a **void** lease: a ``lifecycle.bound`` binds it to a claim that has
      ended (``ends``: the presence of an exit or a death report naming the
      claim, which never raises, so a malformed record in an old claim's window
      cannot poison the present). A lease nothing bound yet is not void: no claim
      took it, so the next to drain it will;
    - or **the head is closed**: nothing is demanded of a completed run, so the
      whole read is empty while ``LatestClaimHead`` is closed. A subscription
      posted to one is never answered and never refused. The head is the
      present's, so a contradiction in the newest claim's records raises
      ``MalformedRecordError``, as the head does.

    A subscription's class, lease or durable, is its first send's (§3: a re-send
    that changes it is refused), so a void lease does not come back by being sent
    again as durable. A body that does not parse as a schedule (``parse_schedule``,
    a pure demand's as one) is live until a ``nak`` names it, as a malformed stop
    is pending until its ``nak``: no fold judges a body the worker has not
    refused, and a waker must launch a worker to refuse it. No claim binds one, so
    it is never void.

    Which durable expiries lie on the head's lineage is read by one walk of it
    (``_lineage``), and only when a live durable subscription has an expiry to
    place: the question is the head's lineage, so the walk is inside its window
    (CLAUDE.md, "Bound a fold to the window its question is about"), and a
    lineage it finds malformed raises ``MalformedRecordError``, as the head does.
    One-shot: expiry sits inside node bodies, so the read is the whole log, never
    polled (``LiveDemand`` is its fold)."""
    w = ch.last_seq()
    if w == 0:
        return []
    demand = _Demand()
    nodes: list[Envelope] = []
    for e in ch.read(topics=list(_DEMAND_TOPICS), limit=w):
        if e.seq > w:
            break
        demand.apply(e)
        if e.topic in _NODE_TOPICS:
            nodes.append(e)
    head = _latest_claim_head(ch, w)

    def lineage() -> AbstractSet[int]:
        return _lineage(head.node, reversed(nodes)).seqs

    return demand.live(closed=head.settled, lineage=lineage)


def undischarged_stops(ch: Channel) -> list[Envelope]:
    """The pending ``control.stop`` records, each id's newest send, ascending by
    seq, at a W taken first (§2, §5, "The answer fold").

    By name, for the whole log: a stop is spent once an exit's ``honored``, a
    ``control.withdraw`` or a ``lifecycle.nak`` names its ``request_id``,
    wherever that sits, a displaced worker's exit included, since a stop is a
    request about the process: one honoured on one branch is not re-armed on
    another. A spent id stays spent, and a re-sent live one is one stop, at its
    newest send. A death report spends nothing. An unreadable exit's ``honored``
    is not read (§5: no fold reads more of an unreadable record's body), and a
    stop or an answer with a missing or empty ``request_id`` names nothing
    (``claims.request_name``), so an empty-id stop is never pending. Pending is
    not due: a
    stop with a ``from`` is pending the moment it lands. One-shot: the question
    is the whole log (``PendingStops`` is its fold)."""
    w = ch.last_seq()
    if w == 0:
        return []
    stops = _Stops()
    for e in ch.read(topics=list(_STOP_TOPICS), limit=w):
        if e.seq > w:
            break
        stops.apply(e)
    return stops.pending()


# What the answer fold reads: the requests, the records that spend or bind them,
# and the claims and the records that end them.
_DEMAND_TOPICS = (
    Topic.CONTROL_SUBSCRIBE,
    Topic.CONTROL_WITHDRAW,
    Topic.LIFECYCLE_NAK,
    Topic.LIFECYCLE_COMMIT,
    Topic.LIFECYCLE_STOPPED,
    Topic.LIFECYCLE_BOUND,
    Topic.LIFECYCLE_STARTED,
    Topic.SUPERVISOR_DIED,
)
_STOP_TOPICS = (
    Topic.CONTROL_STOP,
    Topic.LIFECYCLE_STOPPED,
    Topic.CONTROL_WITHDRAW,
    Topic.LIFECYCLE_NAK,
)


class _Demand:
    """The answer fold's subscriptions (§5, "The answer fold"), applied record by
    record in seq order: ``live_demand`` applies the whole log in one pass, and
    ``LiveDemand`` holds one and applies each poll's new records.

    It holds each live id's newest send, in send order, so the live list is read
    off in seq order; each such id's class, by its first send; the bindings
    (``claims.Bindings``, which the Worker's claim shares); and, for each id not
    known to be a lease, the nodes whose ``expired`` name it, which spend a
    durable subscription only on a lineage through one of them (§2, "Per
    lineage"). A spent id, a void lease or an expired one is **dead**: never live
    again, since a spent id stays spent, void never reverts (a void lease's class
    and its ended claim are fixed), and a lease's expiry is its claim's. So its
    send, its bindings and its expiries are dropped, only its id is kept, and a
    send reusing it is skipped: a poll costs O(new records), and the live list
    O(live) plus, while a live durable subscription has an expiry, the head's
    lineage (``live``)."""

    def __init__(self) -> None:
        self._live: dict[str, Envelope] = {}
        self._lease: dict[str, bool] = {}
        self._dead: set[str] = set()
        self._expiries: dict[str, set[int]] = {}  # the nodes expiring each id
        self._bindings = Bindings()

    def apply(self, e: Envelope) -> None:
        """Apply ``e``, one of ``_DEMAND_TOPICS``."""
        if e.topic == Topic.LIFECYCLE_BOUND and request_name(e) in self._dead:
            return  # a dead id's binding: nothing to void
        for r in list(self._bindings.apply(e)):
            if self._lease.get(r, False):
                self._kill(r)
        if e.topic in _NODE_TOPICS:
            for r in _expired(e):
                self._expire(r, e.seq)
        for r in spent_by(e):  # one namespace: a stop's honored id is spent too
            self._kill(r)
        name = request_name(e)
        if e.topic == Topic.CONTROL_SUBSCRIBE and name is not None:
            if name not in self._dead:  # else dead on arrival
                self._send(name, e)

    def live(
        self, *, closed: bool, lineage: Callable[[], AbstractSet[int]]
    ) -> list[Envelope]:
        """The live subscriptions, ascending by seq; none when the head is
        ``closed``. ``lineage`` gives the node seqs on the head's lineage, and is
        called only when a live durable subscription has an expiry to place, at
        most once."""
        if closed:
            return []
        expiring = [r for r in self._live if r in self._expiries]
        if not expiring:
            return list(self._live.values())
        on = lineage()
        spent = {r for r in expiring if not self._expiries[r].isdisjoint(on)}
        return [e for r, e in self._live.items() if r not in spent]

    def _expire(self, r: str, node: int) -> None:
        """A node's ``expired`` names ``r``: a lease is spent, since its expiry is
        its claim's; a durable subscription, or an id with no send yet, is held
        expired at ``node``, which spends it on a lineage through it only."""
        if r in self._dead:
            return
        if r in self._lease and self._lease[r]:
            self._kill(r)
        else:
            self._expiries.setdefault(r, set()).add(node)

    def _send(self, r: str, e: Envelope) -> None:
        if r not in self._lease:  # the first send fixes the class
            self._lease[r] = _is_lease(e)
            if self._lease[r] and (self._bindings.void(r) or r in self._expiries):
                self._kill(r)  # bound to an ended claim, or expired, before it
                return
        self._live.pop(r, None)  # the newest send, last
        self._live[r] = e

    def _kill(self, r: str) -> None:
        self._dead.add(r)
        self._live.pop(r, None)
        self._lease.pop(r, None)
        self._expiries.pop(r, None)
        self._bindings.forget(r)


class _Stops:
    """The stop fold (§5, "The answer fold"), applied record by record in seq
    order: ``undischarged_stops`` applies the whole log in one pass, and
    ``PendingStops`` holds one and applies each poll's new records.

    It holds each pending id's newest send, in send order, and the spent ids. A
    spent id stays spent, so its send is dropped, only its id is kept, and a send
    reusing it is skipped: a poll costs O(new records) and the pending list
    O(pending)."""

    def __init__(self) -> None:
        self._pending: dict[str, Envelope] = {}
        self._spent: set[str] = set()

    def apply(self, e: Envelope) -> None:
        """Apply ``e``, one of ``_STOP_TOPICS``."""
        for r in spent_by(e):
            self._spend(r)
        name = request_name(e)
        if e.topic == Topic.CONTROL_STOP and name is not None:
            if name not in self._spent:
                self._pending.pop(name, None)  # the newest send, last
                self._pending[name] = e

    def pending(self) -> list[Envelope]:
        """The pending stops, ascending by seq."""
        return list(self._pending.values())

    def _spend(self, r: str) -> None:
        self._spent.add(r)
        self._pending.pop(r, None)


class _Lineage(NamedTuple):
    """The lineage ending at a node, as the answer fold reads it: its node seqs,
    and the kind of its newest node, ``None`` for the empty lineage, which says
    whether a node may extend it."""

    seqs: frozenset[int]
    tip: NodeKind | None


_EMPTY = _Lineage(frozenset(), None)


def _lineage(node: int | None, below: Iterable[Envelope]) -> _Lineage:
    """The lineage ending at ``node`` (§2), from ``below``, the node records at or
    below it, newest first: ``node``, then each ``parent`` in turn, so it reads
    back only as far as the lineage's root. ``live_demand`` and ``LiveDemand``
    share it, so they read one lineage and raise alike.

    It checks what it follows (§5, "Well-formedness"): each node on it readable,
    and each ``parent`` below its node and naming an extendable node
    (``check_reference``); a failure raises ``MalformedRecordError``. A ``node``
    that is not a node raises ``ValueError``, as ``walk`` does: a resolved head's
    is one."""
    if node is None:
        return _EMPTY
    records = iter(below)
    seqs: set[int] = set()
    child: Envelope | None = None
    tip: NodeKind | None = None
    want: int | None = node
    while want is not None:
        found = next((e for e in records if e.seq <= want), None)
        at = node_at(found) if found is not None and found.seq == want else None
        if child is None:
            if at is None:
                raise ValueError(f"head node {node} is not a node")
            tip = kind(at.node)
        else:
            found_kind = None if at is None else kind(at.node)
            check_reference(child, "parent", want, lambda _: found_kind)
        assert at is not None  # check_reference found a node there
        seqs.add(want)
        child, want = at.env, at.node.parent
    return _Lineage(frozenset(seqs), tip)


def _expired(e: Envelope) -> list[str]:
    """The subscriptions a node's ``expired`` names: none for an unreadable node,
    whose body no fold reads more of (§5)."""
    node: Commit | Stopped | None = (
        readable(Commit, e)
        if e.topic == Topic.LIFECYCLE_COMMIT
        else readable(Stopped, e)
    )
    return [] if node is None else node.expired


def _is_lease(e: Envelope) -> bool:
    """Does a ``control.subscribe``'s send make its subscription a lease? Its
    schedule parsed at the boundary (``parse_schedule``, a pure demand's as one).
    A body that does not parse is no lease: no claim binds it, so nothing voids
    it, and it is live until the worker's ``nak`` spends its id."""
    try:
        return is_lease(parse_schedule(e.body, pure_demand=e.name is None))
    except MalformedSchedule:
        return False


# ----- the incremental folds (§5, "Bounded reads") ---------------------------------
#
# Each is built with its question and polled. A poll takes W first and reads only
# the records above its own cursor and at or below W, so its cost is O(new records)
# given the state it holds, whatever the lineage's length and the log's age; and its
# answer equals its pure read's at W. The folds by a head (``PrefixLen``,
# ``LastElement``, ``Answers``) are polled ``fold.poll(ch, head)`` with a resolved
# ``Head`` (``HeadFold``'s, or ``AtNode``'s), and read up to its ``w``; the folds by
# name, for the whole log (§2), are polled ``fold.poll(ch)`` (plan, open question 4).
#
# The cursor moves past each record as it is applied, and applying a record either
# completes or, when a read inside it raises (the head's point read of a
# ``resume_from``), changes nothing. So a poll a read interrupts leaves the fold as if
# it had applied the records before that one, and the next poll applies the rest:
# none is applied twice.
#
# A fold raises wherever its pure read raises (§5, "Bounded reads"): each node's
# ``check_node``, membership's claim, and, where the pure read resolves them, its
# ``values`` seqs naming ``value`` records. A fold reads every ``value`` record, so
# it holds each one's name, never the record, and checks each node's against them
# as the node lands, at O(new records). A node's check that fails is held as the
# node's state and raised by any read of a lineage through it. The one check left to
# the pure walk is a seq committed twice along one lineage, which in a fold would
# cost O(depth) per value (§5, "Well-formedness": "Checked by the pure walk only").


def _tail(ch: Channel, cursor: int, w: int, topics: Sequence[str]) -> list[Envelope]:
    """The records on ``topics`` above ``cursor`` and at or below ``w``. There are
    at most ``w - cursor`` of them, so that is the limit, and the filter is the
    guard against one that landed above ``w``."""
    if w <= cursor:
        return []
    got = ch.read(after=cursor, topics=list(topics), limit=w - cursor)
    return [e for e in got if e.seq <= w]


_HEAD_TOPICS = (
    Topic.LIFECYCLE_STARTED,
    Topic.LIFECYCLE_COMMIT,
    Topic.LIFECYCLE_REWIND,
    Topic.LIFECYCLE_STOPPED,
)


class HeadFold:
    """``LatestClaimHead``, held (§5): the newest claim, its position and its
    readable exit (``_ClaimHead``). Its first poll starts at the newest claim, and
    reads below it only the node its ``resume_from`` names (CLAUDE.md, "Bound a
    fold to the window its question is about"); a poll applies the records above
    its cursor in order, and a newer claim re-resolves from that claim. A check
    that fails holds for the claim, and every poll raises it, until a newer
    claim."""

    def __init__(self) -> None:
        self._cursor = 0
        self._head = _ClaimHead()

    def poll(self, ch: Channel) -> Head:
        w = ch.last_seq()

        def read_at(seq: int) -> Envelope | None:
            return point(ch, seq, w=w)

        if self._head.claim is None and w > self._cursor:
            claim = _newest(ch, Topic.LIFECYCLE_STARTED, w)
            if claim is None:
                self._cursor = w  # nothing claimed: no record below w is the head's
            else:
                self._head.apply(claim, read_at)
                self._cursor = claim.seq
        for e in _tail(ch, self._cursor, w, _HEAD_TOPICS):
            self._head.apply(e, read_at)
            self._cursor = e.seq
        self._cursor = max(self._cursor, w)
        return self._head.head(w)


_NODE_TOPICS = (Topic.LIFECYCLE_COMMIT, Topic.LIFECYCLE_STOPPED)
_STREAM_TOPICS = (Topic.LIFECYCLE_STARTED, *_NODE_TOPICS, Topic.VALUE)

_S = TypeVar("_S")


class _NodeFold(ABC, Generic[_S]):
    """What the folds by a head share: a cursor, and for every node seen its kind
    and its derived state, ``_S``, from its parent's, or the
    ``MalformedRecordError`` a read of a lineage through it raises, after
    ``check_node``. Of the records it reads (``topics``), it holds each claim's
    declared sources and each value's name, never a record: membership is decided
    per committing claim (§5, "The stream-prefix fold"), and a node naming a
    non-value raises as the pure read that resolves it does."""

    def __init__(self, topics: tuple[Topic, ...]) -> None:
        self._topics = topics
        self._cursor = 0
        self._state: dict[int, _S | MalformedRecordError] = {}
        self._kinds: dict[int, NodeKind | MalformedRecordError] = {}
        self._sources: dict[int, frozenset[str] | MalformedRecordError] = {}
        self._name_of: dict[int, str | None] = {}  # every value's

    def _advance(self, ch: Channel, w: int) -> None:
        """Apply the records above the cursor and at or below ``w``."""
        for e in _tail(ch, self._cursor, w, self._topics):
            self._apply(e)
            self._cursor = e.seq
        self._cursor = max(self._cursor, w)

    def _held(self, ch: Channel, head: Head) -> _S | None:
        """The state at ``head.node`` after reading up to ``head.w``; ``None`` for
        the empty lineage."""
        self._advance(ch, head.w)
        return None if head.node is None else self._at(head.node)

    def _at(self, node: int) -> _S:
        """The state at ``node``: the error a read through it raises, raised."""
        if node not in self._state:
            raise ValueError(f"head node {node} is not a node")
        state = self._state[node]
        if isinstance(state, MalformedRecordError):
            raise state
        return state

    def _apply(self, e: Envelope) -> None:
        if e.topic == Topic.VALUE:
            self._name_of[e.seq] = None if e.name is None else sys.intern(e.name)
            return
        if e.topic == Topic.LIFECYCLE_STARTED:
            try:
                self._sources[e.seq] = frozenset(verdict_parse(Started, e).sources)
            except MalformedRecordError as exc:
                self._sources[e.seq] = exc
            return
        try:
            at = node_at(e)
        except MalformedRecordError as exc:
            self._kinds[e.seq] = self._state[e.seq] = exc
            return
        assert at is not None  # a commit or an exit, by the topics
        self._kinds[e.seq] = kind(at.node)
        try:
            self._state[e.seq] = self._node(at)
        except MalformedRecordError as exc:
            self._state[e.seq] = exc

    def _kind_of(self, seq: int) -> NodeKind | None:
        """``check_node``'s lookup: a node's kind, ``None`` where no node is, and an
        unreadable node's error raised."""
        k = self._kinds.get(seq)
        if isinstance(k, MalformedRecordError):
            raise k
        return k

    def _node(self, at: At) -> _S | MalformedRecordError:
        """The state at a parsed node, checked as the pure walk checks it: its own
        structure, then its parent's state, then what it adds."""
        check_node(at, self._kind_of)
        base: _S | None = None
        if at.node.parent is not None:
            parent = self._state[at.node.parent]  # a node, by check_node
            if isinstance(parent, MalformedRecordError):
                return parent  # a read through this node raises the parent's
            base = parent
        return self._extend(base, at)

    def _members(self, at: At, stream: str) -> list[int]:
        """The value seqs of ``stream`` that ``at`` commits, in its order: none when
        its claim declared ``stream`` a source. It resolves every value seq the
        node commits, as the pure read does (``members``)."""
        for s in at.node.committed:
            if s not in self._name_of:
                raise not_a_value(at, s)
        found = [s for s in at.node.committed if self._name_of[s] == stream]
        if not found:
            return found
        claim = at.node.claim_seq
        sources = self._sources.get(claim) if claim < at.env.seq else None
        if sources is None:
            raise no_claim(at)
        if isinstance(sources, MalformedRecordError):
            raise sources
        return [] if stream in sources else found

    @abstractmethod
    def _extend(self, base: _S | None, at: At) -> _S:
        """The state at ``at`` from its parent's, ``None`` at a root."""


def _iterations(at: At) -> int:
    return at.node.iterations


class PrefixLen(_NodeFold[int]):
    """``prefix_len`` held (§5): ``count[node] = count[parent] + |values ∩ S|``,
    with membership decided per committing claim, for an eager stream, or
    ``count[parent] + 1`` at a commit for ``Commits()``, which reads the nodes
    alone."""

    def __init__(self, x: str | Commits) -> None:
        self._adds: Callable[[At], int]
        if isinstance(x, Commits):
            super().__init__(_NODE_TOPICS)
            self._adds = _iterations
        else:
            super().__init__(_STREAM_TOPICS)
            stream = x
            self._adds = lambda at: len(self._members(at, stream))

    def poll(self, ch: Channel, head: Head) -> int:
        count = self._held(ch, head)
        return 0 if count is None else count

    def _extend(self, base: int | None, at: At) -> int:
        return (base or 0) + self._adds(at)


class _Last(NamedTuple):
    node: int
    seq: int


class LastElement(_NodeFold[_Last | None]):
    """``last_element`` held (§5): ``last[node]`` is the node's last element of the
    stream, else its parent's. A poll reads that element's record, one row, so a
    malformed body raises, as the pure read's does."""

    def __init__(self, stream: str) -> None:
        super().__init__(_STREAM_TOPICS)
        self._stream = stream

    def poll(self, ch: Channel, head: Head) -> Element | None:
        last = self._held(ch, head)
        if last is None:
            return None
        e = point(ch, last.seq, w=head.w)
        assert e is not None  # a value the fold read, at or below w
        v = verdict_parse(Value, e)
        return Element(node=last.node, seq=last.seq, value=v.value, t=v.t)

    def _extend(self, base: _Last | None, at: At) -> _Last | None:
        found = self._members(at, self._stream)
        return _Last(at.env.seq, found[-1]) if found else base


class _Firing(NamedTuple):
    """A node that fires the request: its topic, and the value seqs it commits,
    each a value, among which its sample is found by name once the target is
    known."""

    topic: str
    committed: tuple[int, ...]


class _AnswerNode(NamedTuple):
    """A node as ``Answers`` holds it: its parent, and its firing: ``None`` where
    it does not fire the request, or the error of one that commits a non-value."""

    parent: int | None
    firing: _Firing | MalformedRecordError | None


_ANSWER_TOPICS = (Topic.CONTROL_SUBSCRIBE, *_NODE_TOPICS, Topic.VALUE)


class Answers(_NodeFold[_AnswerNode]):
    """``answers`` held (§5): the answers along the head it last read. A poll at a
    head that descends from that one extends the list by the new nodes' answers;
    at one that does not, it re-derives the list along the new head's lineage, and
    returns it whole. A caller that streams answers diffs the lists, since a
    callback cannot retract an answer.

    It holds every value's name, so a sample written before the subscribe is
    found when its node is read, and reads a sample's record, one row, the first
    time it answers. Its reads are O(new records); returning the list is
    O(answers)."""

    def __init__(self, request_id: str) -> None:
        super().__init__(_ANSWER_TOPICS)
        self._request_id = request_id
        self._subscribed = False
        self._target: str | None = None  # the first subscribe's name
        self._answers: dict[int, Element] = {}  # each answer read, by its node
        self._along: int | None = None  # the head its list is along
        self._list: list[Element] = []

    def poll(self, ch: Channel, head: Head) -> list[Element]:
        self._advance(ch, head.w)
        # parents decrease, so the walk back from the head meets the held head,
        # if it descends from it, before it passes below it. That the extend path
        # visits only the new nodes is pinned by review only: it reads no channel.
        path: list[int] = []
        node = head.node
        while node is not None and (self._along is None or node > self._along):
            path.append(node)
            node = self._at(node).parent
        descends = node == self._along
        while not descends and node is not None:  # re-derive, to the root
            path.append(node)
            node = self._at(node).parent
        answers = list(self._list) if descends else []
        for n in reversed(path):
            firing = self._at(n).firing
            if firing is not None:
                answers.append(self._answer(ch, head.w, n, firing))
        self._along, self._list = head.node, answers
        return list(answers)

    def _answer(
        self, ch: Channel, w: int, node: int, firing: _Firing | MalformedRecordError
    ) -> Element:
        """The answer at ``node``, which fires the request, checked as ``answers``
        checks it; its sample's record is read the first time."""
        if isinstance(firing, MalformedRecordError):
            raise firing
        if node in self._answers:
            return self._answers[node]
        if self._target is None:
            raise _unsubscribed(node, firing.topic, self._request_id)
        target = self._target
        samples = [s for s in firing.committed if self._name_of[s] == target]
        if len(samples) != 1:
            raise _not_one_sample(
                node, firing.topic, self._request_id, target, len(samples)
            )
        e = point(ch, samples[0], w=w)
        assert e is not None  # a value the fold read, at or below w
        v = verdict_parse(Value, e)
        answer = Element(node=node, seq=samples[0], value=v.value, t=v.t)
        self._answers[node] = answer
        return answer

    def _apply(self, e: Envelope) -> None:
        if e.topic == Topic.CONTROL_SUBSCRIBE:
            if e.request_id == self._request_id and not self._subscribed:
                self._subscribed, self._target = True, e.name
            return
        super()._apply(e)

    def _extend(self, base: _AnswerNode | None, at: At) -> _AnswerNode:
        parent = at.node.parent
        if self._request_id not in at.node.fired:
            return _AnswerNode(parent, None)
        for s in at.node.committed:
            if s not in self._name_of:
                return _AnswerNode(parent, not_a_value(at, s))
        return _AnswerNode(parent, _Firing(at.env.topic, tuple(at.node.committed)))


class LiveDemand:
    """``live_demand`` held (§5): the answer fold's subscriptions (``_Demand``),
    the newest claim's head (``_ClaimHead``) and the head's lineage
    (``_HeldLineage``), applied from one cursor at one W. It takes no head from its
    caller (plan, open question 4): it reads the newest head itself, its
    closedness and its lineage, and raises where that head does. Of each poll's
    records, the head takes those from the newest claim among them.
    ``ensure_served``'s standing form holds one per run, and polls it again after
    its ``report_if_dead`` reports, since a report may void the only lease.

    ``ended`` is the pure read's, the presence of a record naming the claim
    (``ends``), which never raises."""

    def __init__(self) -> None:
        self._cursor = 0
        self._demand = _Demand()
        self._head = _ClaimHead()
        self._lineage = _HeldLineage()

    def poll(self, ch: Channel) -> list[Envelope]:
        w = ch.last_seq()

        def read_at(seq: int) -> Envelope | None:
            return point(ch, seq, w=w)

        batch = _tail(ch, self._cursor, w, _LIVE_DEMAND_TOPICS)
        claims = [e.seq for e in batch if e.topic == Topic.LIFECYCLE_STARTED]
        newest = claims[-1] if claims else 0
        self._lineage.new_poll()
        for e in batch:
            if e.topic in _HEAD_TOPICS and e.seq >= newest:
                self._head.apply(e, read_at)
            if e.topic in _DEMAND_TOPICS:
                self._demand.apply(e)
            if e.topic in _NODE_TOPICS:
                self._lineage.saw(e)
            self._cursor = e.seq
        self._cursor = max(self._cursor, w)
        head = self._head.head(w)

        def lineage() -> AbstractSet[int]:
            return self._lineage.at(ch, head.node)

        return self._demand.live(closed=head.settled, lineage=lineage)


class _Seen(NamedTuple):
    """A node a poll applied: its ``parent`` and its kind."""

    parent: int | None
    kind: NodeKind


class _HeldLineage:
    """The head's lineage, held for ``LiveDemand`` (§5, "The answer fold"): the
    lineage ending at ``tip``, and the nodes the current poll applied. A head that
    descends from ``tip`` through those nodes extends it, at O(new records); one
    that does not (a rewind, a resume from an older node, a new claim's position
    elsewhere, or a head ``LiveDemand`` last asked about polls ago) re-derives it
    by one walk back from the head (``_lineage``), at O(L), as the Worker's
    rebuild does. A lineage is fixed by its newest node, so the held one stays
    true of ``tip`` for as long as it is held.

    An extension checks each node it adds as ``_lineage`` would, and walks
    instead when one fails, so the walk raises what the pure read raises; a walk
    that raises leaves the held lineage as it was."""

    def __init__(self) -> None:
        self._tip: int | None = None  # the held lineage's newest node
        self._tip_kind: NodeKind | None = None  # its kind; None for the empty one
        self._seqs: set[int] = set()  # its node seqs, extended in place
        self._seen: dict[int, _Seen | None] = {}  # None: an unreadable node

    def new_poll(self) -> None:
        """Forget the nodes the last poll applied."""
        self._seen = {}

    def saw(self, e: Envelope) -> None:
        """A node record the current poll applied."""
        try:
            at = node_at(e)
        except MalformedRecordError:
            self._seen[e.seq] = None
            return
        assert at is not None  # a node's topic
        self._seen[e.seq] = _Seen(at.node.parent, kind(at.node))

    def at(self, ch: Channel, node: int | None) -> AbstractSet[int]:
        """The node seqs on the lineage ending at ``node``."""
        if node == self._tip:
            return self._seqs
        if node is None:
            self._tip, self._tip_kind, self._seqs = None, None, set()
            return self._seqs
        descent = self._descent(node)
        if descent is None:
            topics: list[str] = list(_NODE_TOPICS)
            walked = _lineage(node, backward(ch, after=0, hi=node, topics=topics))
            self._seqs, self._tip_kind = set(walked.seqs), walked.tip
        else:
            added, self._tip_kind = descent
            self._seqs.update(added)
        self._tip = node
        return self._seqs

    def _descent(self, node: int) -> tuple[list[int], NodeKind] | None:
        """The nodes from ``node`` back to ``tip``, exclusive, and ``node``'s kind,
        when ``node`` descends from ``tip`` through nodes this poll applied, each
        checked as ``_lineage`` checks it; else ``None``."""
        added: list[int] = []
        want: int | None = node
        while want != self._tip:
            if want is None:
                return None  # the root, and tip is not the empty lineage
            seen = self._seen.get(want)
            if seen is None:
                return None  # not one of the poll's nodes, or an unreadable one
            if seen.parent is not None:
                if seen.parent >= want:
                    return None
                above = self._seen.get(seen.parent)
                if above is not None:
                    parent_kind: NodeKind | None = above.kind
                elif seen.parent == self._tip:
                    parent_kind = self._tip_kind
                else:
                    return None
                if parent_kind is not NodeKind.EXTENDABLE:
                    return None
            added.append(want)
            want = seen.parent
        top = self._seen[node]
        assert top is not None  # node is not tip, so the loop added it
        return added, top.kind


_LIVE_DEMAND_TOPICS = tuple(dict.fromkeys((*_DEMAND_TOPICS, *_HEAD_TOPICS)))


class PendingStops:
    """``undischarged_stops`` held (§5): the stop fold (``_Stops``). By name, for
    the whole log, polled ``fold.poll(ch)``; the Watcher's ``pending_stops`` is
    one held per run."""

    def __init__(self) -> None:
        self._cursor = 0
        self._stops = _Stops()

    def poll(self, ch: Channel) -> list[Envelope]:
        w = ch.last_seq()
        for e in _tail(ch, self._cursor, w, _STOP_TOPICS):
            self._stops.apply(e)
            self._cursor = e.seq
        self._cursor = max(self._cursor, w)
        return self._stops.pending()


# ----- reading at W -----------------------------------------------------------------


def _newest(ch: Channel, topic: Topic, w: int) -> Envelope | None:
    """The newest record on ``topic`` at or below ``w``: the newest on the log,
    unless one landed above ``w`` since it was read. ``latest`` takes no bound;
    the fallback scan reads at most ``w`` records, which holds every one at or
    below ``w``, and the filter is the guard."""
    if w == 0:
        return None
    e = ch.latest(topic)
    if e is None or e.seq <= w:
        return e
    below = [c for c in ch.read(topics=[topic], limit=w) if c.seq <= w]
    return below[-1] if below else None


# ----- the head fold ----------------------------------------------------------------


def _latest_claim_head(ch: Channel, w: int) -> Head:
    """The newest claim's head at ``w``, the caller's W, taken first: the claim and
    the records after it applied to a ``_ClaimHead``. It reads nothing above ``w``
    (``claim_window``), and below the claim only the nodes its ``resume_from``s name:
    the claim is a Markov boundary for its own position, so a malformed record
    from a dead past cannot poison it."""
    head = _ClaimHead()
    claim = _newest(ch, Topic.LIFECYCLE_STARTED, w)
    if claim is not None:

        def read_at(seq: int) -> Envelope | None:
            return point(ch, seq, w=w)

        head.apply(claim, read_at)
        topics: list[str] = [
            Topic.LIFECYCLE_COMMIT,
            Topic.LIFECYCLE_REWIND,
            Topic.LIFECYCLE_STOPPED,
        ]
        for e in claim_window(ch, claim, w, topics):
            head.apply(e, read_at)
    return head.head(w)


class _ClaimHead:
    """The head fold (§5), applied record by record in seq order: the newest claim
    applied, its position and its readable exit, or the error that holds for it.
    ``_latest_claim_head`` applies a claim and its window; ``HeadFold`` and
    ``LiveDemand`` hold one and apply each poll's records. A ``started``
    re-resolves from that claim.

    In the claim's own order, which is seq order within one writer, its position
    starts at its ``resume_from``; a commit leaves itself and a rewind its
    ``resume_from``. Each position-bearing record is checked where it is held
    (§5): a ``resume_from`` by ``check_reference``, and a node whose ``parent`` is
    the position before it.

    Records naming another claim or none, a displaced worker's, are skipped:
    anything that the fold cannot attribute to this claim is not its. The claim's
    readable exit is its head; an unreadable one is absent for the head (§5, "The
    verdict"). A nameless record that was in fact one of this claim's commits is
    still caught, unless it is the claim's last: its next node's ``parent`` names
    it, not the position. The fold refuses to guess where §2 says "at most one": a
    record of the claim after its exit, the exit being its last node, raises, a
    second exit included.

    ``apply`` reads only the node a ``resume_from`` names, through ``read_at``, and
    either applies its record whole or, when that read raises, changes nothing."""

    def __init__(self) -> None:
        self._claim: int | None = None
        self._position: int | None = None
        self._exit: At | None = None
        self._error: MalformedRecordError | None = None

    @property
    def claim(self) -> int | None:
        """The claim applied, or ``None`` before any."""
        return self._claim

    def apply(self, e: Envelope, read_at: Callable[[int], Envelope | None]) -> None:
        """Apply ``e``, one of ``_HEAD_TOPICS``, whose ``resume_from`` is read
        through ``read_at``."""

        def kind_of(seq: int) -> NodeKind | None:
            return kind_at(read_at(seq))

        if e.topic == Topic.LIFECYCLE_STARTED:
            position: int | None = None
            error: MalformedRecordError | None = None
            try:
                started = verdict_parse(Started, e)
                position = _resume_point(e, started.resume_from, kind_of)
            except MalformedRecordError as exc:
                error = exc
            self._claim, self._position, self._exit, self._error = (
                e.seq,
                position,
                None,
                error,
            )
            return
        if self._claim is None or self._error is not None:
            return
        try:
            body = _claims(e, self._claim)
            if body is None:
                return
            if self._exit is not None:
                raise MalformedRecordError(
                    e.seq,
                    e.topic,
                    f"claim {self._claim} exited at seq {self._exit.env.seq}: "
                    "the exit is its claim's last node",
                )
            if isinstance(body, Rewind):
                self._position = _resume_point(e, body.resume_from, kind_of)
                return
            if body.parent != self._position:
                raise MalformedRecordError(
                    e.seq,
                    e.topic,
                    f"parent {body.parent} is not its claim's position "
                    f"{self._position}",
                )
            if isinstance(body, Commit):
                self._position = e.seq
            else:
                self._exit = At(e, body)
        except MalformedRecordError as exc:
            self._error = exc

    def head(self, w: int) -> Head:
        """The head, at ``w``: the error that holds for the claim raised."""
        if self._error is not None:
            raise self._error
        if self._claim is None:
            return Head(None, False, w)
        if self._exit is not None:
            return Head(self._exit.env.seq, self._exit.node.closed, w)
        return Head(self._position, False, w)


def _claims(e: Envelope, claim: int) -> Commit | Rewind | Stopped | None:
    """``e``'s body when it is one of ``claim``'s position-bearing records, else
    ``None``: another claim's, one that names no claim, or an unreadable exit,
    which is no self-report and so absent for the head (§5, "The verdict"). A
    commit or a rewind that names ``claim`` must be readable, or the position is
    unknown."""
    if e.topic == Topic.LIFECYCLE_STOPPED:
        stop = readable(Stopped, e)
        return stop if stop is not None and stop.claim_seq == claim else None
    if named_claim(e) != claim:
        return None
    if e.topic == Topic.LIFECYCLE_REWIND:
        return verdict_parse(Rewind, e)
    return verdict_parse(Commit, e)


def _resume_point(
    record: Envelope, resume_from: int | None, kind_of: KindOf
) -> int | None:
    """A ``started``'s or a ``rewind``'s ``resume_from``, checked: below its own
    record, and naming an extendable node (``check_reference``)."""
    if resume_from is not None:
        check_reference(record, "resume_from", resume_from, kind_of)
    return resume_from


# ----- the reads' helpers over a walked lineage ----------------------------------


def _count(walked: Walked, x: str | Commits) -> int:
    if isinstance(x, Commits):
        return sum(at.node.iterations for at in walked.nodes)
    return len(members(walked, x))


def _axis(walked: Walked, axis: str | Commits) -> list[int]:
    """``count(axis, node)`` at each node of the lineage, root first."""
    if isinstance(axis, Commits):
        per = [at.node.iterations for at in walked.nodes]
    else:
        at_node: dict[int, int] = {}
        for at, _ in members(walked, axis):
            at_node[at.env.seq] = at_node.get(at.env.seq, 0) + 1
        per = [at_node.get(at.env.seq, 0) for at in walked.nodes]
    out: list[int] = []
    running = 0
    for n in per:
        running += n
        out.append(running)
    return out


def _element(walked: Walked, at: At, seq: int) -> Element:
    """The element at value ``seq``, its body read: a malformed one raises."""
    v = verdict_parse(Value, walked.records[seq])
    return Element(node=at.env.seq, seq=seq, value=v.value, t=v.t)


def _subscription_target(ch: Channel, request_id: str, w: int) -> str | None:
    """The source the ``control.subscribe`` that first names ``request_id`` at or
    below ``w`` targets, its envelope ``name``: ``None`` for a pure demand, and
    when no subscribe names it. Either way nothing is sampled for it."""
    if w == 0:
        return None
    for e in ch.read(topics=[Topic.CONTROL_SUBSCRIBE], limit=w):
        if e.seq > w:
            break
        if e.request_id == request_id:
            return e.name
    return None


# ----- the verdict's folds ----------------------------------------------------------


class _Exit(NamedTuple):
    """A claim's readable exit: its record, and its parsed body."""

    env: Envelope
    stop: Stopped


def _self_report(stop: Stopped) -> Outcome:
    """An exit's outcome: ``errored`` when it carries an error, else
    ``completed`` or ``preempted``. A completed exit carries none."""
    if stop.error is not None:
        return Outcome.ERRORED
    return Outcome.COMPLETED if stop.completed else Outcome.PREEMPTED


def _launch_end(ch: Channel, w: int) -> RunResult | None:
    """An unclaimed run's verdict: its newest ``terminated`` at or below ``w``
    (``peek_terminal``)."""
    e = _newest(ch, Topic.SUPERVISOR_TERMINATED, w)
    if e is None:
        return None
    end = verdict_parse(Terminated, e)
    if not e.request_id:
        raise MalformedRecordError(
            e.seq, e.topic, "names no launch: supervisor-v0.5 requires its request_id"
        )
    if end.reason == Reap.KILLED:
        outcome = Outcome.KILLED
    elif end.exit_code == 0:
        outcome = Outcome.COMPLETED
    else:
        outcome = Outcome.ERRORED
    return RunResult(
        outcomes=frozenset({outcome}),
        reports=(),
        node=None,
        claim_seq=None,
        malformed=(),
        tier=Tier.TERMINATED,
        error=None,
        run_id=None,
    )
