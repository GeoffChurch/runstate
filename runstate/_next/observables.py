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

THE PURE WALK. Each read takes ``w`` first and reads nothing above it. It reads
backwards from its head node in windows that grow by 4x (§5, "The pure walk":
bulk reads, never a point read per node), as far as the lineage, its values and
its claims reach, walks the lineage back to the root, and checks every node it
walks (§5, "Well-formedness"). These are one-shot reads, never polled. Nothing is
cached between calls.

THE CLAIM PLANE (§2, §5). A claim has ended once its exit, or a death report
naming it, is on the log: ``claim_verdict`` is not ``None``, the one predicate the
gate, lease voiding, the verdict and the Watcher share. The verdict is the claim's
readable exit, else the death reports naming it, whose outcomes are kept as a set.
It reads only the claim's window, the records after it, since a record cannot
speak for a claim it never saw. A run nothing claimed takes its launch's
``supervisor.terminated``. No read probes a handle and none writes.

THE ANSWER FOLD (§2 "Requests", §5) is by name, for the whole log: a request is
spent by what names it (a node's ``expired``, an exit's ``honored``, a withdrawal,
a refusal), wherever that sits, and a lease is void once its bound claim ended.

THE INCREMENTAL FOLDS (§5, "Bounded reads") are the polled forms, public and
standalone, since ``ensure``'s loop has no Watcher and the TUI may use public API
only: ``HeadFold``, ``PrefixLen``, ``LastElement``, ``Answers``, ``LiveDemand``
and ``PendingStops``. Each holds its state and reads only the records above its
cursor, so a poll costs O(new records), and equals its pure read after every
record.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Generic, NamedTuple, Protocol, TypeVar

from ..channel import Channel, Envelope
from .vocabulary.coordinate import Commits
from .vocabulary.payloads import (
    Bound,
    Commit,
    Died,
    Node,
    Outcome,
    Reap,
    Rewind,
    Started,
    Stopped,
    Terminated,
    Topic,
    Value,
)
from .vocabulary.schedule import (
    Coordinates,
    MalformedSchedule,
    Schedule,
    is_lease,
    parse_schedule,
)

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
        return _latest_claim_head(ch)


@dataclass(frozen=True)
class AtNode:
    """That node (§5): forensics, or reading an abandoned branch. Always settled,
    since the lineage ending at a node is fixed. Also a ``NodeRef`` (§4).

    Resolving raises ``ValueError`` on a seq that is not a node or is above w, and
    ``MalformedRecordError`` on a node whose body is unreadable."""

    node: int

    def resolve(self, ch: Channel) -> Head:
        w = ch.last_seq()
        e = _point(ch, w, self.node)
        if e is None:
            raise ValueError(f"AtNode({self.node}): no record at or below w={w}")
        if _node_at(e) is None:
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
    return _count(_walk(ch, head.resolve(ch)), x)


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
    walked = _walk(ch, h)
    found = tuple(_element(walked, at, s) for at, s in _members(walked, stream)[:n])
    if len(found) == n:
        return Complete(found, h.node)
    if h.settled:
        return SettledShort(found, h.node)
    return Pending(found, h.node)


def series(ch: Channel, stream: str, *, head: HeadStrategy) -> list[Element]:
    """One eager stream's elements in lineage order (§5). It reads every
    element's body, so a malformed one raises."""
    walked = _walk(ch, head.resolve(ch))
    return [_element(walked, at, s) for at, s in _members(walked, stream)]


def last_element(ch: Channel, stream: str, *, head: HeadStrategy) -> Element | None:
    """The last element in lineage order, or ``None`` (§5). That is not always
    the newest by seq: ``include`` can commit an older value after a newer one.
    It reads the last element's body only."""
    walked = _walk(ch, head.resolve(ch))
    members = _members(walked, stream)
    if not members:
        return None
    at, s = members[-1]
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
    walked = _walk(ch, head.resolve(ch))
    wanted = frozenset(names)
    rows: list[AlignedRow] = []
    for at, x in zip(walked.nodes, _axis(walked, axis), strict=True):
        cells: dict[str, list[Element]] = {n: [] for n in names}
        for s in at.node.committed:
            name = _value_record(walked, at, s).name
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
    walked = _walk(ch, h)
    target = _subscription_target(ch, request_id, h.w)
    out: list[Element] = []
    for at in walked.nodes:
        if request_id not in at.node.fired:
            continue
        if target is None:
            raise MalformedRecordError(
                at.env.seq,
                at.env.topic,
                f"fired names {request_id!r}, which no source subscription names",
            )
        samples = [
            s for s in at.node.committed if _value_record(walked, at, s).name == target
        ]
        if len(samples) != 1:
            raise MalformedRecordError(
                at.env.seq,
                at.env.topic,
                f"fired names {request_id!r}, and commits {len(samples)} samples "
                f"of its target {target!r}, not one",
            )
        out.append(_element(walked, at, samples[0]))
    return out


def streams(ch: Channel, *, head: HeadStrategy) -> Streams:
    """The names with a committed value on the lineage (§5), two sets, each by the
    committing claim's declaration (§2, "Membership"): ``sources`` the names some
    committing claim declared, ``eager`` the names some committing claim did not.
    A name that changed kind is in both. It resolves every value on the lineage."""
    walked = _walk(ch, head.resolve(ch))
    eager: set[str] = set()
    sources: set[str] = set()
    declared: dict[int, frozenset[str]] = {}
    for at in walked.nodes:
        for s in at.node.committed:
            name = _value_record(walked, at, s).name
            if name is None:
                continue
            claim = at.node.claim_seq
            if claim not in declared:
                declared[claim] = _declared(walked, at)
            (sources if name in declared[claim] else eager).add(name)
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

    - ``outcomes``: never empty. One under an exit, a launch's ``terminated`` or
      an inference; under death reports, the set of their outcomes, a ``lost``
      for each unreadable record that ended the claim, less ``lost`` beside
      another. A consumer's policy tests the set, as ``ensure`` does with "any of
      its outcomes in ``Outcome.failures()``". There is no ``success``;
    - ``reports``: the readable death reports naming the claim, in seq order,
      under ``DIED`` only;
    - ``node``: the exit node's seq under ``EXIT``, else ``None``;
    - ``claim_seq``: the claim the verdict is about: required under ``EXIT`` and
      ``DIED``, ``None`` for a run nothing claimed (``TERMINATED``);
    - ``malformed``: the seqs of the unreadable records the verdict skipped, so
      they are reported, not raised;
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
        if (self.node is not None) != (self.tier == Tier.EXIT):
            raise ValueError("a verdict names its node under an exit, and only there")
        if self.claim_seq is None and self.tier in (Tier.EXIT, Tier.DIED):
            raise ValueError("a verdict under an exit or a death names its claim")
        if self.claim_seq is not None and self.tier == Tier.TERMINATED:
            raise ValueError("a launch's verdict is an unclaimed run's: no claim")
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
    has **ended** if and only if this is not ``None``: the one predicate the
    folds, the Worker and the launchers use (§2, "How a claim ended").

    The claim's readable exit, its self-report, is the verdict (``Tier.EXIT``).
    Else the death reports naming it are (``Tier.DIED``): their outcomes as a set,
    less ``lost`` beside another, so the verdict does not depend on their order
    and only grows as reports arrive. An unreadable ``stopped`` or ``died`` whose
    ``claim_seq`` names the claim still ends it, and counts as a ``lost``: it says
    only that the claim ended. No more of its body is read. Its seq is in
    ``malformed``, and so is an unreadable one in the claim's window naming no
    claim, which may have been the claim's own; one naming another claim is that
    claim's. While the claim is held, though, nameless junk is reported nowhere.

    It reads only the claim's window (``_window``): a record cannot speak for a
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
    for e in _window(ch, claim, w, [Topic.LIFECYCLE_STOPPED, Topic.SUPERVISOR_DIED]):
        named = _named_claim(e)
        if named is not None and named != claim.seq:
            continue  # another claim's
        body: Stopped | Died | None = (
            _readable(Stopped, e)
            if e.topic == Topic.LIFECYCLE_STOPPED
            else _readable(Died, e)
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
    unreadable one, or one naming no launch, raises ``MalformedRecordError``."""
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
    if _named_claim(top) == claim.seq:
        return top
    hi, width = top.seq, _FIRST_WINDOW
    while hi > claim.seq:
        lo = max(claim.seq, hi - width)
        window = ch.read(
            after=lo, topics=[Topic.LIFECYCLE_COMMIT, Topic.LIFECYCLE_HEARTBEAT]
        )
        for e in reversed(window):
            if e.seq <= hi and _named_claim(e) == claim.seq:
                return e
        hi, width = lo, width * 4
    return None


# ----- the answer fold (§2 "Requests", §5) ------------------------------------------


def live_demand(ch: Channel) -> list[Envelope]:
    """The live subscriptions, by name, for the whole log, at a W taken first
    (§2, §5, "The answer fold"): each ``control.subscribe`` id's newest send,
    ascending by seq, unless

    - it is **spent**: a node's ``expired`` names it, on any lineage, or a
      ``control.withdraw`` or a ``lifecycle.nak`` does, wherever it sits; a spent
      id stays spent, so a later send reusing it is dead on arrival;
    - it is **malformed**: its body does not parse as a schedule
      (``parse_schedule``, a pure demand's as one), which the worker's ``nak``
      spends; never a lease, never live demand;
    - it is a **void** lease: a ``lifecycle.bound`` binds it to a claim that has
      ended (``claim_verdict``). A lease nothing bound yet is not void: no claim
      took it, so the next to drain it will;
    - or **the head is closed**: nothing is demanded of a completed run, so the
      whole read is empty while the newest claim's readable exit completed (§2: a
      claim's head is its readable exit). A subscription posted to one is never
      answered and never refused.

    So a claim that dies before its first commit revives no answered demand: an
    answer is by name, not per lineage. One-shot: expiry sits inside node bodies,
    so the read is the whole log, never polled (``LiveDemand`` is its fold)."""
    w = ch.last_seq()
    if w == 0:
        return []
    subs: dict[str, Envelope] = {}
    schedules: dict[str, Schedule | None] = {}
    spent: set[str] = set()
    bound: dict[str, set[int]] = {}
    for e in ch.read(topics=list(_DEMAND_TOPICS), limit=w):
        if e.seq > w:
            break
        if e.topic in (Topic.LIFECYCLE_COMMIT, Topic.LIFECYCLE_STOPPED):
            spent.update(_expired(e))
        elif e.request_id is None:
            continue
        elif e.topic == Topic.CONTROL_SUBSCRIBE:
            subs[e.request_id] = e
            schedules[e.request_id] = _schedule(e)
        elif e.topic == Topic.LIFECYCLE_BOUND:
            b = _readable(Bound, e)
            if b is not None:
                bound.setdefault(e.request_id, set()).add(b.claim_seq)
        else:
            spent.add(e.request_id)
    claim = _newest(ch, Topic.LIFECYCLE_STARTED, w)
    if claim is not None and worker_completed(claim_verdict(ch, claim, w=w)):
        return []
    ended: dict[int, bool] = {}

    def has_ended(c: int) -> bool:
        if c not in ended:
            e = _point(ch, w, c)
            ended[c] = (
                e is not None
                and e.topic == Topic.LIFECYCLE_STARTED
                and claim_verdict(ch, e, w=w) is not None
            )
        return ended[c]

    live: list[Envelope] = []
    for r, e in subs.items():
        sched = schedules[r]
        if r in spent or sched is None:
            continue
        if is_lease(sched) and any(has_ended(c) for c in bound.get(r, ())):
            continue
        live.append(e)
    return sorted(live, key=lambda e: e.seq)


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
    stop or an answer with no ``request_id`` names nothing. Pending is not due: a
    stop with a ``from`` is pending the moment it lands. One-shot: the question
    is the whole log (``PendingStops`` is its fold)."""
    w = ch.last_seq()
    if w == 0:
        return []
    stops: dict[str, Envelope] = {}
    spent: set[str] = set()
    for e in ch.read(topics=list(_STOP_TOPICS), limit=w):
        if e.seq > w:
            break
        if e.topic == Topic.LIFECYCLE_STOPPED:
            exit_ = _readable(Stopped, e)
            if exit_ is not None:
                spent.update(exit_.honored)
        elif e.request_id is None:
            continue
        elif e.topic == Topic.CONTROL_STOP:
            stops[e.request_id] = e
        else:
            spent.add(e.request_id)
    return sorted((e for r, e in stops.items() if r not in spent), key=lambda e: e.seq)


# What the answer fold reads: the requests, and the records that spend or bind them.
_DEMAND_TOPICS = (
    Topic.CONTROL_SUBSCRIBE,
    Topic.CONTROL_WITHDRAW,
    Topic.LIFECYCLE_NAK,
    Topic.LIFECYCLE_COMMIT,
    Topic.LIFECYCLE_STOPPED,
    Topic.LIFECYCLE_BOUND,
)
_STOP_TOPICS = (
    Topic.CONTROL_STOP,
    Topic.LIFECYCLE_STOPPED,
    Topic.CONTROL_WITHDRAW,
    Topic.LIFECYCLE_NAK,
)


def _expired(e: Envelope) -> list[str]:
    """The subscriptions a node's ``expired`` names: none for an unreadable node,
    whose body no fold reads more of (§5)."""
    node: Commit | Stopped | None = (
        _readable(Commit, e)
        if e.topic == Topic.LIFECYCLE_COMMIT
        else _readable(Stopped, e)
    )
    return [] if node is None else node.expired


def _schedule(e: Envelope) -> Schedule | None:
    """A ``control.subscribe``'s schedule, parsed once at the boundary
    (``parse_schedule``, a pure demand's as one), or ``None`` when its body is
    malformed: the worker naks it, which spends the id."""
    try:
        return parse_schedule(e.body, pure_demand=e.name is None)
    except MalformedSchedule:
        return None


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
# A fold checks what it holds: each node's ``values`` and ``parent`` below its own
# seq, its ``parent`` an extendable node, and membership's claim (§5,
# "Well-formedness"). The pure walk's other checks cost O(depth) per poll and stay
# the pure reads': a seq committed twice along one lineage, and a ``values`` seq
# naming a record that is no ``value`` (a fold holds only the values it counts).


def _tail(ch: Channel, cursor: int, w: int, topics: Sequence[str]) -> list[Envelope]:
    """The records on ``topics`` above ``cursor`` and at or below ``w``. There are
    at most ``w - cursor`` of them, so that is the limit, and the filter is the
    guard against one that landed above ``w``."""
    if w <= cursor:
        return []
    got = ch.read(after=cursor, topics=list(topics), limit=w - cursor)
    return [e for e in got if e.seq <= w]


class HeadFold:
    """``LatestClaimHead``, held (§5): the newest claim, its position and its
    readable exit. A poll applies the records above its cursor in order, as the
    pure head fold applies the claim's window, and a new claim re-resolves from
    that claim, reading below it only the node its ``resume_from`` names. The
    checks are the head fold's (``_latest_claim_head``): one that fails holds for
    the claim, and every poll raises it, until a newer claim."""

    def __init__(self) -> None:
        self._cursor = 0
        self._claim: int | None = None
        self._position: int | None = None
        self._exit: _At | None = None
        self._error: MalformedRecordError | None = None

    def poll(self, ch: Channel) -> Head:
        w = ch.last_seq()
        for e in _tail(ch, self._cursor, w, _HEAD_TOPICS):
            self._apply(ch, w, e)
        self._cursor = max(self._cursor, w)
        if self._error is not None:
            raise self._error
        if self._claim is None:
            return Head(None, False, w)
        if self._exit is not None:
            return Head(self._exit.env.seq, self._exit.node.closed, w)
        return Head(self._position, False, w)

    def _apply(self, ch: Channel, w: int, e: Envelope) -> None:
        def lookup(seq: int) -> Envelope | None:
            return _point(ch, w, seq)

        if e.topic == Topic.LIFECYCLE_STARTED:
            self._claim, self._exit, self._error = e.seq, None, None
            try:
                started = verdict_parse(Started, e)
                self._position = _resume_point(lookup, e, started.resume_from)
            except MalformedRecordError as exc:
                self._error = exc
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
                self._position = _resume_point(lookup, e, body.resume_from)
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
                self._exit = _At(e, body)
        except MalformedRecordError as exc:
            self._error = exc


_HEAD_TOPICS = (
    Topic.LIFECYCLE_STARTED,
    Topic.LIFECYCLE_COMMIT,
    Topic.LIFECYCLE_REWIND,
    Topic.LIFECYCLE_STOPPED,
)
_STREAM_TOPICS = (
    Topic.LIFECYCLE_STARTED,
    Topic.LIFECYCLE_COMMIT,
    Topic.LIFECYCLE_STOPPED,
    Topic.VALUE,
)

_S = TypeVar("_S")


class _NodeFold(ABC, Generic[_S]):
    """What the stream folds share: a cursor, and for every node seen its derived
    state, ``_S``, from its parent's and the elements of the stream it commits, or
    the ``MalformedRecordError`` a read of a lineage through it raises. Membership
    is decided per committing claim, from the claims' declared sources, and the
    stream's value records are held as they pass (§5, "The stream-prefix fold")."""

    def __init__(self, stream: str | None) -> None:
        self._stream = stream  # None: no stream, the Commits() count
        self._cursor = 0
        self._state: dict[int, _S | MalformedRecordError] = {}
        self._extendable: dict[int, bool] = {}
        self._sources: dict[int, frozenset[str] | MalformedRecordError] = {}
        self._values: dict[int, Envelope] = {}  # the stream's, by seq

    def _held(self, ch: Channel, head: Head) -> _S | None:
        """The state at ``head.node`` after reading up to ``head.w``; ``None`` for
        the empty lineage."""
        for e in _tail(ch, self._cursor, head.w, _STREAM_TOPICS):
            self._apply(e)
        self._cursor = max(self._cursor, head.w)
        if head.node is None:
            return None
        if head.node not in self._state:
            raise ValueError(f"head node {head.node} is not a node")
        state = self._state[head.node]
        if isinstance(state, MalformedRecordError):
            raise state
        return state

    def _apply(self, e: Envelope) -> None:
        if e.topic == Topic.VALUE:
            if self._stream is not None and e.name == self._stream:
                self._values[e.seq] = e
            return
        if e.topic == Topic.LIFECYCLE_STARTED:
            try:
                self._sources[e.seq] = frozenset(verdict_parse(Started, e).sources)
            except MalformedRecordError as exc:
                self._sources[e.seq] = exc
            return
        try:
            at = _node_at(e)
            assert at is not None  # a commit or an exit, by _STREAM_TOPICS
            self._state[e.seq] = self._node(at)
            self._extendable[e.seq] = at.node.extendable
        except MalformedRecordError as exc:
            self._state[e.seq] = exc
            self._extendable[e.seq] = False

    def _node(self, at: _At) -> _S | MalformedRecordError:
        """The state at a parsed node, checked as the pure walk checks it."""
        seq, node = at.env.seq, at.node
        later = [s for s in node.committed if s >= seq]
        if later:
            raise MalformedRecordError(
                seq, at.env.topic, f"values names {later}, at or above its own seq"
            )
        base: _S | None = None
        if node.parent is not None:
            if node.parent >= seq:
                raise MalformedRecordError(
                    seq,
                    at.env.topic,
                    f"parent {node.parent} is at or above its own seq",
                )
            if node.parent not in self._state:
                raise MalformedRecordError(
                    seq, at.env.topic, f"parent {node.parent} names no node"
                )
            parent = self._state[node.parent]
            if isinstance(parent, MalformedRecordError):
                return parent  # a read through this node raises the parent's
            if not self._extendable[node.parent]:
                raise MalformedRecordError(
                    seq,
                    at.env.topic,
                    f"parent {node.parent} names no extendable node",
                )
            base = parent
        members = [s for s in node.committed if s in self._values]
        if members and self._declares(at):
            members = []
        return self._extend(base, at, members)

    def _declares(self, at: _At) -> bool:
        """Did the claim that committed ``at`` declare the stream a source?"""
        claim = at.node.claim_seq
        sources = self._sources.get(claim) if claim < at.env.seq else None
        if sources is None:
            raise MalformedRecordError(
                at.env.seq,
                at.env.topic,
                f"claim_seq {claim} names no lifecycle.started below it",
            )
        if isinstance(sources, MalformedRecordError):
            raise sources
        return self._stream in sources

    @abstractmethod
    def _extend(self, base: _S | None, at: _At, members: list[int]) -> _S:
        """The state at ``at`` from its parent's (``None`` at a root) and the value
        seqs of the stream it commits, in its order."""


class PrefixLen(_NodeFold[int]):
    """``prefix_len`` held (§5): ``count[node] = count[parent] + |values ∩ S|``,
    with membership decided per committing claim, for an eager stream, or
    ``count[parent] + 1`` at a commit for ``Commits()``."""

    def __init__(self, x: str | Commits) -> None:
        super().__init__(None if isinstance(x, Commits) else x)

    def poll(self, ch: Channel, head: Head) -> int:
        count = self._held(ch, head)
        return 0 if count is None else count

    def _extend(self, base: int | None, at: _At, members: list[int]) -> int:
        add = at.node.iterations if self._stream is None else len(members)
        return (base or 0) + add


class _Last(NamedTuple):
    node: int
    seq: int


class LastElement(_NodeFold[_Last | None]):
    """``last_element`` held (§5): ``last[node]`` is the node's last element of the
    stream, else its parent's. A poll reads that element's body, which the fold
    held as it passed, so a malformed one raises, as the pure read's does."""

    def __init__(self, stream: str) -> None:
        super().__init__(stream)

    def poll(self, ch: Channel, head: Head) -> Element | None:
        last = self._held(ch, head)
        if last is None:
            return None
        v = verdict_parse(Value, self._values[last.seq])
        return Element(node=last.node, seq=last.seq, value=v.value, t=v.t)

    def _extend(self, base: _Last | None, at: _At, members: list[int]) -> _Last | None:
        return _Last(at.env.seq, members[-1]) if members else base


class _AnswerNode(NamedTuple):
    """A node as ``Answers`` holds it: its parent, and its answer: the sample's
    seq where it fires the request, ``None`` where it does not, or the error a
    firing without exactly one sample of the target is."""

    parent: int | None
    answer: int | MalformedRecordError | None


class Answers:
    """``answers`` held (§5): the answers along the head it last read. A poll at a
    head that descends from that one extends the list by the new nodes' answers;
    at one that does not, it re-derives the list along the new head's lineage, and
    returns it whole. A caller that streams answers diffs the lists, since a
    callback cannot retract an answer."""

    def __init__(self, request_id: str) -> None:
        self._request_id = request_id
        self._cursor = 0
        self._target: str | None = None  # the subscribe's name, once one is seen
        self._subscribed = False
        self._nodes: dict[int, _AnswerNode | MalformedRecordError] = {}
        self._extendable: dict[int, bool] = {}
        self._samples: dict[int, Envelope] = {}  # the target's values, by seq
        self._at: int | None = None  # the head its list is along
        self._list: list[Element] = []

    def poll(self, ch: Channel, head: Head) -> list[Element]:
        for e in _tail(ch, self._cursor, head.w, _ANSWER_TOPICS):
            self._apply(e)
        self._cursor = max(self._cursor, head.w)
        # parents decrease, so the walk back from the head meets the held head,
        # if it descends from it, before it passes below it
        path: list[int] = []
        node = head.node
        while node is not None and (self._at is None or node > self._at):
            path.append(node)
            node = self._entry(node).parent
        descends = node == self._at
        while not descends and node is not None:  # re-derive, to the root
            path.append(node)
            node = self._entry(node).parent
        answers = list(self._list) if descends else []
        for n in reversed(path):
            answer = self._entry(n).answer
            if isinstance(answer, MalformedRecordError):
                raise answer
            if answer is not None:
                v = verdict_parse(Value, self._samples[answer])
                answers.append(Element(node=n, seq=answer, value=v.value, t=v.t))
        self._at, self._list = head.node, answers
        return list(answers)

    def _entry(self, node: int) -> _AnswerNode:
        if node not in self._nodes:
            raise ValueError(f"head node {node} is not a node")
        entry = self._nodes[node]
        if isinstance(entry, MalformedRecordError):
            raise entry
        return entry

    def _apply(self, e: Envelope) -> None:
        if e.topic == Topic.CONTROL_SUBSCRIBE:
            if e.request_id == self._request_id and not self._subscribed:
                self._subscribed, self._target = True, e.name
            return
        if e.topic == Topic.VALUE:
            if self._target is not None and e.name == self._target:
                self._samples[e.seq] = e
            return
        try:
            at = _node_at(e)
            assert at is not None  # a commit or an exit, by _ANSWER_TOPICS
            self._nodes[e.seq] = self._node(at)
            self._extendable[e.seq] = at.node.extendable
        except MalformedRecordError as exc:
            self._nodes[e.seq] = exc
            self._extendable[e.seq] = False

    def _node(self, at: _At) -> _AnswerNode | MalformedRecordError:
        """The node as held, checked as the pure walk checks it; a read through a
        node whose parent is unreadable raises the parent's error."""
        seq, node = at.env.seq, at.node
        later = [s for s in node.committed if s >= seq]
        if later:
            raise MalformedRecordError(
                seq, at.env.topic, f"values names {later}, at or above its own seq"
            )
        if node.parent is not None:
            if node.parent >= seq:
                raise MalformedRecordError(
                    seq,
                    at.env.topic,
                    f"parent {node.parent} is at or above its own seq",
                )
            parent = self._nodes.get(node.parent)
            if isinstance(parent, MalformedRecordError):
                return parent
            if parent is None or not self._extendable[node.parent]:
                raise MalformedRecordError(
                    seq, at.env.topic, f"parent {node.parent} names no extendable node"
                )
        if self._request_id not in node.fired:
            return _AnswerNode(node.parent, None)
        if self._target is None:
            error = MalformedRecordError(
                seq,
                at.env.topic,
                f"fired names {self._request_id!r}, which no source subscription names",
            )
            return _AnswerNode(node.parent, error)
        samples = [s for s in node.committed if s in self._samples]
        if len(samples) != 1:
            error = MalformedRecordError(
                seq,
                at.env.topic,
                f"fired names {self._request_id!r}, and commits {len(samples)} "
                f"samples of its target {self._target!r}, not one",
            )
            return _AnswerNode(node.parent, error)
        return _AnswerNode(node.parent, samples[0])


_ANSWER_TOPICS = (
    Topic.CONTROL_SUBSCRIBE,
    Topic.LIFECYCLE_COMMIT,
    Topic.LIFECYCLE_STOPPED,
    Topic.VALUE,
)


class LiveDemand:
    """``live_demand`` held (§5): the subscriptions, each id's newest send and its
    schedule parsed once; the spent ids; the bindings; the claims, and those a
    record has ended; and whether the newest claim's readable exit completed. By
    name, for the whole log, so it takes no head (plan, open question 4): it reads
    the newest head's closedness itself. ``ensure_served``'s standing form holds
    one per run, and polls it again after its ``report_if_dead`` reports, since a
    report may void the only lease.

    ``ended`` is ``claim_verdict``'s predicate, held: a ``stopped`` or a ``died``
    above a claim whose ``claim_seq`` names it, readable or not."""

    def __init__(self) -> None:
        self._cursor = 0
        self._subs: dict[str, Envelope] = {}
        self._schedules: dict[str, Schedule | None] = {}
        self._spent: set[str] = set()
        self._bound: dict[str, set[int]] = {}
        self._claims: set[int] = set()
        self._ended: set[int] = set()
        self._newest: int | None = None
        self._closed = False

    def poll(self, ch: Channel) -> list[Envelope]:
        w = ch.last_seq()
        for e in _tail(ch, self._cursor, w, _LIVE_DEMAND_TOPICS):
            self._apply(e)
        self._cursor = max(self._cursor, w)
        if self._closed:
            return []
        live: list[Envelope] = []
        for r, e in self._subs.items():
            sched = self._schedules[r]
            if r in self._spent or sched is None:
                continue
            if is_lease(sched) and any(
                c in self._claims and c in self._ended for c in self._bound.get(r, ())
            ):
                continue
            live.append(e)
        return sorted(live, key=lambda e: e.seq)

    def _apply(self, e: Envelope) -> None:
        if e.topic == Topic.LIFECYCLE_STARTED:
            self._claims.add(e.seq)
            self._newest, self._closed = e.seq, False
            return
        if e.topic in (Topic.LIFECYCLE_STOPPED, Topic.SUPERVISOR_DIED):
            named = _named_claim(e)
            if named is not None and named < e.seq:
                self._ended.add(named)
        if e.topic == Topic.LIFECYCLE_STOPPED:
            exit_ = _readable(Stopped, e)
            if exit_ is not None and exit_.claim_seq == self._newest:
                self._closed = self._closed or exit_.completed
        if e.topic in (Topic.LIFECYCLE_COMMIT, Topic.LIFECYCLE_STOPPED):
            self._spent.update(_expired(e))
        elif e.topic == Topic.SUPERVISOR_DIED or e.request_id is None:
            return
        elif e.topic == Topic.CONTROL_SUBSCRIBE:
            self._subs[e.request_id] = e
            self._schedules[e.request_id] = _schedule(e)
        elif e.topic == Topic.LIFECYCLE_BOUND:
            b = _readable(Bound, e)
            if b is not None:
                self._bound.setdefault(e.request_id, set()).add(b.claim_seq)
        else:
            self._spent.add(e.request_id)


_LIVE_DEMAND_TOPICS = (
    *_DEMAND_TOPICS,
    Topic.LIFECYCLE_STARTED,
    Topic.SUPERVISOR_DIED,
)


class PendingStops:
    """``undischarged_stops`` held (§5): each stop id's newest send and the spent
    ids. By name, for the whole log, polled ``fold.poll(ch)``; the Watcher's
    ``pending_stops`` is one held per run."""

    def __init__(self) -> None:
        self._cursor = 0
        self._stops: dict[str, Envelope] = {}
        self._spent: set[str] = set()

    def poll(self, ch: Channel) -> list[Envelope]:
        w = ch.last_seq()
        for e in _tail(ch, self._cursor, w, _STOP_TOPICS):
            if e.topic == Topic.LIFECYCLE_STOPPED:
                exit_ = _readable(Stopped, e)
                if exit_ is not None:
                    self._spent.update(exit_.honored)
            elif e.request_id is None:
                continue
            elif e.topic == Topic.CONTROL_STOP:
                self._stops[e.request_id] = e
            else:
                self._spent.add(e.request_id)
        self._cursor = max(self._cursor, w)
        return sorted(
            (e for r, e in self._stops.items() if r not in self._spent),
            key=lambda e: e.seq,
        )


# ----- reading at W -----------------------------------------------------------------

_Lookup = Callable[[int], Envelope | None]


def _point(ch: Channel, w: int, seq: int) -> Envelope | None:
    """The record at ``seq``, or ``None`` when there is none at or below ``w``."""
    if not 1 <= seq <= w:
        return None
    got = ch.read(after=seq - 1, limit=1)
    return got[0] if got and got[0].seq == seq else None


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


def _window(ch: Channel, claim: Envelope, w: int, topics: list[str]) -> list[Envelope]:
    """The records on ``topics`` after ``claim`` and at or below ``w``: the
    claim's window, since a record cannot speak for a claim it never saw. The
    read is capped at ``w``'s distance from the claim, and the filter is the
    guard; it is never ``limit=0``."""
    if w <= claim.seq:
        return []
    got = ch.read(after=claim.seq, topics=topics, limit=w - claim.seq)
    return [e for e in got if e.seq <= w]


def _named_claim(e: Envelope) -> int | None:
    """The claim a record's ``claim_seq`` names, read on its own so that a record
    whose other fields are unreadable still names its claim; ``None`` when it
    names none."""
    if "claim_seq" not in e.body:
        return None
    c = e.body["claim_seq"]
    return c if isinstance(c, int) and not isinstance(c, bool) and c >= 1 else None


# ----- the head fold ----------------------------------------------------------------


def _latest_claim_head(ch: Channel) -> Head:
    """The newest claim's head, at a W taken first.

    It folds the claim and the records after it, and below the claim reads only
    the nodes its ``resume_from``s name: the claim is a Markov boundary for its
    own position, so a malformed record from a dead past cannot poison it. In
    the claim's own order, which is seq order within one writer, its position
    starts at its ``resume_from``; a commit leaves itself and a rewind its
    ``resume_from``. Each position-bearing record is checked where it is held
    (§5): a ``resume_from`` below its record and naming an extendable node, and
    a node whose ``parent`` is the position before it.

    Records naming another claim or none, a displaced worker's, are skipped:
    anything in the window that the fold cannot attribute to this claim is not
    its. The claim's readable exit is its head; an unreadable one is absent for
    the head (§5, "The verdict"). A nameless record that was in fact one of this
    claim's commits is still caught, unless it is the claim's last: its next
    node's ``parent`` names it, not the position. The fold refuses to guess
    where §2 says "at most one": a record of the claim after its exit, the exit
    being its last node, raises, a second exit included.

    It reads nothing above ``w`` (``_window``)."""
    w = ch.last_seq()
    claim = _newest(ch, Topic.LIFECYCLE_STARTED, w)
    if claim is None:
        return Head(None, False, w)

    def lookup(seq: int) -> Envelope | None:
        return _point(ch, w, seq)

    started = verdict_parse(Started, claim)
    position = _resume_point(lookup, claim, started.resume_from)
    exit_: _At | None = None
    for e in _window(
        ch,
        claim,
        w,
        [Topic.LIFECYCLE_COMMIT, Topic.LIFECYCLE_REWIND, Topic.LIFECYCLE_STOPPED],
    ):
        body = _claims(e, claim.seq)
        if body is None:
            continue
        if exit_ is not None:
            raise MalformedRecordError(
                e.seq,
                e.topic,
                f"claim {claim.seq} exited at seq {exit_.env.seq}: "
                "the exit is its claim's last node",
            )
        if isinstance(body, Rewind):
            position = _resume_point(lookup, e, body.resume_from)
            continue
        if body.parent != position:
            raise MalformedRecordError(
                e.seq,
                e.topic,
                f"parent {body.parent} is not its claim's position {position}",
            )
        if isinstance(body, Commit):
            position = e.seq
        else:
            exit_ = _At(e, body)
    if exit_ is not None:
        return Head(exit_.env.seq, exit_.node.closed, w)
    return Head(position, False, w)


def _claims(e: Envelope, claim: int) -> Commit | Rewind | Stopped | None:
    """``e``'s body when it is one of ``claim``'s position-bearing records, else
    ``None``: another claim's, one that names no claim, or an unreadable exit,
    which is no self-report and so absent for the head (§5, "The verdict"). A
    commit or a rewind that names ``claim`` must be readable, or the position is
    unknown."""
    if e.topic == Topic.LIFECYCLE_STOPPED:
        stop = _readable(Stopped, e)
        return stop if stop is not None and stop.claim_seq == claim else None
    if _named_claim(e) != claim:
        return None
    if e.topic == Topic.LIFECYCLE_REWIND:
        return verdict_parse(Rewind, e)
    return verdict_parse(Commit, e)


def _resume_point(
    lookup: _Lookup, record: Envelope, resume_from: int | None
) -> int | None:
    """A ``started``'s or a ``rewind``'s ``resume_from``, checked: below its own
    record, and naming an extendable node."""
    if resume_from is not None:
        if resume_from >= record.seq:
            raise MalformedRecordError(
                record.seq,
                record.topic,
                f"resume_from {resume_from} is at or above its own seq",
            )
        _extendable(lookup, record, "resume_from", resume_from)
    return resume_from


# ----- the walk ---------------------------------------------------------------------


class _At(NamedTuple):
    """A node: its record, and its parsed body."""

    env: Envelope
    node: Node


class _Walked(NamedTuple):
    """A lineage walked: the records the walk read, by seq (every record from the
    lowest it needed up to its head node: the lineage's nodes, their values and
    their claims among them), and its nodes, root first."""

    records: dict[int, Envelope]
    nodes: list[_At]


def _node_at(e: Envelope) -> _At | None:
    """The node at ``e``, or ``None`` when its topic is not a node's. A node's
    unreadable body raises ``MalformedRecordError``."""
    if e.topic == Topic.LIFECYCLE_COMMIT:
        return _At(e, verdict_parse(Commit, e))
    if e.topic == Topic.LIFECYCLE_STOPPED:
        return _At(e, verdict_parse(Stopped, e))
    return None


def _extendable(lookup: _Lookup, referrer: Envelope, field: str, target: int) -> _At:
    """The node ``referrer``'s ``field`` names, which must be extendable (§5)."""
    e = lookup(target)
    at = None if e is None else _node_at(e)
    if at is None:
        what = "no record" if e is None else f"a {e.topic}"
        raise MalformedRecordError(
            referrer.seq, referrer.topic, f"{field} {target} names {what}, not a node"
        )
    if not at.node.extendable:
        kind = "closed" if at.node.closed else "torn"
        raise MalformedRecordError(
            referrer.seq,
            referrer.topic,
            f"{field} {target} names a {kind} node, not an extendable one",
        )
    return at


# The first backward window, in rows; each later one is 4x the last. Exempt: it sets
# how many rows each bulk read asks for, never which records a read returns.
_FIRST_WINDOW = 64


class _Backward:
    """The records at or below ``hi``, read backwards in bulk windows that grow by
    4x (§5, "The pure walk"): a record below what is loaded loads windows until it
    is, so a walk reads O(the distance back to the lowest record it needs) rows in
    O(log) reads, whatever the log's length below that."""

    def __init__(self, ch: Channel, hi: int) -> None:
        self._ch = ch
        self._hi = hi
        self._lo = hi  # the records in (lo, hi] are loaded
        self._width = _FIRST_WINDOW
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


def _walk(ch: Channel, head: Head) -> _Walked:
    """The lineage ending at ``head.node``, checked (§5): on every node walked, a
    ``parent`` or a ``values`` seq at or above the node's own seq, and a
    ``parent`` naming no extendable node; along the lineage, a seq committed
    twice. A head node that is not a node (a hand-built ``Head``) raises
    ``ValueError``.

    It reads backwards from the head node (``_Backward``), and loads what a read
    may resolve: each node's values, and its claim's ``started`` when it lies
    below the node. Loading resolves nothing: membership raises only when a read
    resolves (``_members``)."""
    if head.node is None:
        return _Walked({}, [])
    back = _Backward(ch, head.node)
    first = back.get(head.node)
    at = None if first is None else _node_at(first)
    if at is None:
        raise ValueError(f"head node {head.node} is not a node")
    chain = [at]
    while True:
        seq, node = at.env.seq, at.node
        later = [s for s in node.committed if s >= seq]
        if later:
            raise MalformedRecordError(
                seq, at.env.topic, f"values names {later}, at or above its own seq"
            )
        if node.parent is None:
            break
        if node.parent >= seq:
            raise MalformedRecordError(
                seq, at.env.topic, f"parent {node.parent} is at or above its own seq"
            )
        at = _extendable(back.get, at.env, "parent", node.parent)
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
    return _Walked(back.records, chain)


def _count(walked: _Walked, x: str | Commits) -> int:
    if isinstance(x, Commits):
        return sum(at.node.iterations for at in walked.nodes)
    return len(_members(walked, x))


def _axis(walked: _Walked, axis: str | Commits) -> list[int]:
    """``count(axis, node)`` at each node of the lineage, root first."""
    if isinstance(axis, Commits):
        per = [at.node.iterations for at in walked.nodes]
    else:
        at_node: dict[int, int] = {}
        for at, _ in _members(walked, axis):
            at_node[at.env.seq] = at_node.get(at.env.seq, 0) + 1
        per = [at_node.get(at.env.seq, 0) for at in walked.nodes]
    out: list[int] = []
    running = 0
    for n in per:
        running += n
        out.append(running)
    return out


def _coordinates(walked: _Walked) -> list[Coordinates]:
    """The coordinates at and including each node of the lineage, root first
    (§3, "Evaluation"): ``count(X, node)`` for every eager stream X with an
    element there, by the committing claim's declaration, and ``count(Commits(),
    node)``. It resolves every value on the lineage."""
    out: list[Coordinates] = []
    counts: dict[str, int] = {}
    commits = 0
    declared: dict[int, frozenset[str]] = {}
    for at in walked.nodes:
        for s in at.node.committed:
            name = _value_record(walked, at, s).name
            if name is None:
                continue
            claim = at.node.claim_seq
            if claim not in declared:
                declared[claim] = _declared(walked, at)
            if name not in declared[claim]:
                counts[name] = counts.get(name, 0) + 1
        commits += at.node.iterations
        out.append(Coordinates(MappingProxyType(dict(counts)), commits))
    return out


def _value_record(walked: _Walked, at: _At, seq: int) -> Envelope:
    """The ``value`` record at ``seq``, which ``at`` commits: a ``values`` seq
    naming a record that is not a ``value`` raises (§5, "Membership, checked only
    for the records a read resolves")."""
    e = walked.records.get(seq)
    if e is None or e.topic != Topic.VALUE:
        what = "no record" if e is None else f"a {e.topic}"
        raise MalformedRecordError(
            at.env.seq, at.env.topic, f"values names {seq}, {what}, not a value"
        )
    return e


def _members(walked: _Walked, stream: str) -> list[tuple[_At, int]]:
    """(node, value seq) of each element of ``stream``, in lineage order.

    Membership is decided by the envelope, never the body (§5): a committed
    ``value`` named ``stream`` is an element if and only if the claim that
    committed it, the node's ``claim_seq``, did not declare ``stream`` a source
    (§2, "Membership"). Deciding it resolves every value seq on the lineage, so
    one naming a record that is not a ``value`` raises here; and the claim's
    ``started``, which must lie below its node."""
    sources: dict[int, frozenset[str]] = {}
    out: list[tuple[_At, int]] = []
    for at in walked.nodes:
        for s in at.node.committed:
            if _value_record(walked, at, s).name != stream:
                continue
            claim = at.node.claim_seq
            if claim not in sources:
                sources[claim] = _declared(walked, at)
            if stream not in sources[claim]:
                out.append((at, s))
    return out


def _declared(walked: _Walked, at: _At) -> frozenset[str]:
    """The sources declared by the claim that wrote ``at``."""
    claim = at.node.claim_seq
    e = walked.records.get(claim) if claim < at.env.seq else None
    if e is None or e.topic != Topic.LIFECYCLE_STARTED:
        raise MalformedRecordError(
            at.env.seq,
            at.env.topic,
            f"claim_seq {claim} names no lifecycle.started below it",
        )
    return frozenset(verdict_parse(Started, e).sources)


def _element(walked: _Walked, at: _At, seq: int) -> Element:
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


def _readable(cls: type[_T], e: Envelope) -> _T | None:
    """``e``'s body parsed as ``cls``, or ``None`` when it is unreadable: a fold
    that skips an unreadable record reads no more of it (§5)."""
    try:
        return verdict_parse(cls, e)
    except MalformedRecordError:
        return None


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
