"""The pure 0.4.0 reads: the head, the lineage, counts and series, the verdict
and the stop fold (docs/specs/commits-and-lineage.md §2, §5).

PROVISIONAL. Log format 0.4.0 is not this release's format yet: ``LOG_FORMAT``
stays 0.3.0 until the last commit of layer 2 (log-formats.md §2, rule 3). So its
vocabulary is not in ``vocabulary/``, whose ``Topic`` 0.3.0's schema test pins.
This module parses 0.4.0 bodies (lifecycle-v0.6, supervisor-v0.5, value-v0.3)
with its own private frozen types, and defines §4's ``Commits`` and ``NodeRef``
and §5's ``RunResult`` (as ``RunResult04``, with its ``Outcome04`` and ``Tier``)
itself. At the format flip, layer 2 moves the types into ``vocabulary/`` and
``observables``, and these reads beside the observer plane's, replacing 0.3.0's
``peek_terminal`` and ``undischarged_stops``. Until then the migration round's
corpus runner reads migrated logs with them, as its measurement oracle. They
read a log through the Channel surface alone (``last_seq``, ``read``,
``latest``), and never import the migration step, which carries its own copy of
0.3.0's semantics.

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
the records at or below its head node in one bulk read, walks the lineage back
to the root, and checks every node it walks (§5, "Well-formedness"). These are
one-shot reads, never polled: the held, incremental folds come with layer 2.
Nothing is cached between calls.

THE VERDICT (§5). How the newest claim ended, from two viewpoints: its own
readable exit, else the death reports naming it, whose outcomes are kept as a
set. It reads only the claim's window, the records after it, since a record
cannot speak for a claim it never saw. A run nothing claimed takes its launch's
``supervisor.terminated``. THE STOP FOLD (§2, §5) is by name, for the whole log.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, NamedTuple, Protocol, TypeGuard, TypeVar

from .channel import Channel, Envelope
from .observables import MalformedRecordError, verdict_parse

_T = TypeVar("_T")

# ----- the 0.4.0 bodies, provisional ------------------------------------------------


class _Topic(StrEnum):
    """The 0.4.0 topics these reads route on. Provisional: ``Topic`` gains them at
    the format flip."""

    STARTED = "lifecycle.started"
    COMMIT = "lifecycle.commit"
    REWIND = "lifecycle.rewind"
    STOPPED = "lifecycle.stopped"
    NAK = "lifecycle.nak"
    DIED = "supervisor.died"
    TERMINATED = "supervisor.terminated"
    STOP = "control.stop"
    WITHDRAW = "control.withdraw"
    VALUE = "value"


# Each check mirrors one schema definition of lifecycle-v0.6 or value-v0.3, so a
# body parses here if and only if it validates there, with one exception: a
# whole-number float such as 3.0 is no integer here, though JSON Schema admits it,
# as in ``vocabulary.payloads`` and the migration step. ``bool`` is never a number
# either: JSON ``true`` is not a seq and not a clock.


def _is_int(v: object) -> TypeGuard[int]:
    return isinstance(v, int) and not isinstance(v, bool)


def _seq(field: str, v: object, *, nullable: bool) -> None:
    """``Seq``, or ``Parent`` when nullable: an integer >= 1."""
    if v is None and nullable:
        return
    if not (_is_int(v) and v >= 1):
        raise ValueError(f"{field} must be a seq (an integer >= 1), got {v!r}")


def _time(field: str, v: object, *, nullable: bool) -> None:
    """``Time``: a number >= 0."""
    if v is None and nullable:
        return
    if not (isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0):
        raise ValueError(f"{field} must be a number >= 0, got {v!r}")


def _seqs(field: str, v: object) -> None:
    """``Values``: unique seqs, order significant."""
    if not isinstance(v, list):
        raise ValueError(f"{field} must be a list of seqs, got {v!r}")
    for s in v:
        _seq(field, s, nullable=False)
    if len(set(v)) != len(v):
        raise ValueError(f"{field} must be unique, got {v!r}")


def _names(field: str, v: object) -> None:
    """``Requests``: unique, non-empty strings."""
    if not (isinstance(v, list) and all(isinstance(r, str) and r for r in v)):
        raise ValueError(f"{field} must be a list of non-empty strings, got {v!r}")
    if len(set(v)) != len(v):
        raise ValueError(f"{field} must be unique, got {v!r}")


@dataclass(frozen=True)
class _Started:
    """lifecycle-v0.6's ``Started``: the claim, which states who computes, where
    it starts (``resume_from``) and which names are its sources."""

    handle: str
    t: float
    resume_from: int | None
    sources: list[str]

    def __post_init__(self) -> None:
        if not isinstance(self.handle, str):
            raise ValueError(f"handle must be a string, got {self.handle!r}")
        _time("t", self.t, nullable=False)
        _seq("resume_from", self.resume_from, nullable=True)
        _names("sources", self.sources)


@dataclass(frozen=True)
class _Rewind:
    """lifecycle-v0.6's ``Rewind``: restates the claim's position, never who."""

    claim_seq: int
    resume_from: int | None
    t: float

    def __post_init__(self) -> None:
        _seq("claim_seq", self.claim_seq, nullable=False)
        _seq("resume_from", self.resume_from, nullable=True)
        _time("t", self.t, nullable=False)


class _Node(Protocol):
    """§3's ``Node`` Protocol, provisional: what a lineage walk reads of a commit
    or an exit."""

    @property
    def claim_seq(self) -> int: ...

    @property
    def parent(self) -> int | None: ...

    @property
    def committed(self) -> list[int]:
        """The value seqs it commits, in its own order: none for a torn exit."""

    @property
    def iterations(self) -> int:
        """What it adds to ``Commits()``: 1 for a commit, 0 for an exit."""

    @property
    def extendable(self) -> bool: ...

    @property
    def closed(self) -> bool: ...


@dataclass(frozen=True)
class _Commit:
    """lifecycle-v0.6's ``Commit``: one per iteration. Always extendable."""

    claim_seq: int
    consumed_seq: int
    parent: int | None
    values: list[int]
    fired: list[str]
    expired: list[str]
    t: float

    def __post_init__(self) -> None:
        _seq("claim_seq", self.claim_seq, nullable=False)
        if not (_is_int(self.consumed_seq) and self.consumed_seq >= 0):
            raise ValueError(
                f"consumed_seq must be an integer >= 0, got {self.consumed_seq!r}"
            )
        _seq("parent", self.parent, nullable=True)
        _seqs("values", self.values)
        _names("fired", self.fired)
        _names("expired", self.expired)
        _time("t", self.t, nullable=False)

    @property
    def committed(self) -> list[int]:
        return self.values

    @property
    def iterations(self) -> int:
        return 1

    @property
    def extendable(self) -> bool:
        return True

    @property
    def closed(self) -> bool:
        return False


@dataclass(frozen=True)
class _Stopped:
    """lifecycle-v0.6's ``Stopped``: the worker's exit, its claim's last node.
    Closed if completed; torn if not completed and it discarded its open
    iteration (``values: null``); extendable otherwise (§2, "Nodes")."""

    completed: bool
    error: str | None
    claim_seq: int
    honored: list[str]
    parent: int | None
    values: list[int] | None
    fired: list[str]
    expired: list[str]
    t: float

    def __post_init__(self) -> None:
        if not isinstance(self.completed, bool):
            raise ValueError(f"completed must be a bool, got {self.completed!r}")
        if not (self.error is None or isinstance(self.error, str)):
            raise ValueError(f"error must be a string or null, got {self.error!r}")
        _seq("claim_seq", self.claim_seq, nullable=False)
        _names("honored", self.honored)
        _seq("parent", self.parent, nullable=True)
        if self.values is not None:
            _seqs("values", self.values)
        _names("fired", self.fired)
        _names("expired", self.expired)
        _time("t", self.t, nullable=False)
        if self.completed and self.error is not None:
            raise ValueError("a completed exit carries no error")
        if self.error is not None and self.values is not None:
            raise ValueError("an error exit discards its iteration: values is null")
        if self.values is None and (self.fired or self.expired):
            raise ValueError("an exit that discarded its iteration evaluates nothing")

    @property
    def committed(self) -> list[int]:
        return [] if self.values is None else self.values

    @property
    def iterations(self) -> int:
        return 0

    @property
    def extendable(self) -> bool:
        return not self.completed and self.values is not None

    @property
    def closed(self) -> bool:
        return self.completed


@dataclass(frozen=True)
class _Value:
    """value-v0.3's body: ``{value, t}``. The name is the envelope's."""

    value: Any
    t: float | None

    def __post_init__(self) -> None:
        _time("t", self.t, nullable=True)


class Outcome04(StrEnum):
    """How a run ended (§5): the closed codomain of ``RunResult04.outcomes``.
    0.3.0's ``Outcome`` gains ``lost`` and, for these record-based reads, loses
    the Watcher's inferred ``presumed_dead``, which no record carries.

    PROVISIONAL, like ``RunResult04``: layer 2's flip makes this
    ``observables.Outcome``."""

    COMPLETED = "completed"
    PREEMPTED = "preempted"
    ERRORED = "errored"
    KILLED = "killed"
    LOST = "lost"


# A death report's outcomes (supervisor-v0.5): a death is never a completion.
_DEATHS = frozenset(
    {Outcome04.PREEMPTED, Outcome04.ERRORED, Outcome04.KILLED, Outcome04.LOST}
)


@dataclass(frozen=True)
class _Died:
    """supervisor-v0.5's ``Died``: a third party's report of how a claim ended,
    with its evidence, which no fold parses."""

    claim_seq: int
    outcome: Outcome04
    evidence: str
    t: float

    def __post_init__(self) -> None:
        _seq("claim_seq", self.claim_seq, nullable=False)
        if not (isinstance(self.outcome, str) and self.outcome in _DEATHS):
            raise ValueError(f"outcome must be a death's, got {self.outcome!r}")
        object.__setattr__(self, "outcome", Outcome04(self.outcome))
        if not (isinstance(self.evidence, str) and self.evidence):
            raise ValueError(
                f"evidence must be a non-empty string, got {self.evidence!r}"
            )
        _time("t", self.t, nullable=False)


class _Reap(StrEnum):
    """supervisor-v0.5's ``Terminated.reason``."""

    EXITED = "exited"
    KILLED = "killed"


@dataclass(frozen=True)
class _Terminated:
    """supervisor-v0.5's ``Terminated``: how a launch's process ended. An exit
    carries its code and no signal, a kill its signal and no code."""

    reason: _Reap
    exit_code: int | None
    signal: int | None
    t: float

    def __post_init__(self) -> None:
        if not (isinstance(self.reason, str) and self.reason in set(_Reap)):
            raise ValueError(f"reason must be exited or killed, got {self.reason!r}")
        object.__setattr__(self, "reason", _Reap(self.reason))
        code, signal = self.exit_code, self.signal
        if self.reason == _Reap.EXITED:
            if not (_is_int(code) and code >= 0 and signal is None):
                raise ValueError(
                    f"an exit carries a code >= 0 and no signal, got {code!r}, {signal!r}"
                )
        elif not (_is_int(signal) and signal >= 1 and code is None):
            raise ValueError(
                f"a kill carries a signal >= 1 and no code, got {signal!r}, {code!r}"
            )
        _time("t", self.t, nullable=False)


# ----- the public types -------------------------------------------------------------


@dataclass(frozen=True)
class Commits:
    """The lineage's own clock (§2, "The coordinate"; §4): ``count(Commits(),
    node)`` is the number of ``lifecycle.commit`` nodes on the lineage up to and
    including ``node``, which is the number of iterations. An exit adds none.
    A singleton marker type: every instance is equal, and no stream name, a
    ``str``, can collide with it. Provisional, like the bodies above."""


class NodeRef(Protocol):
    """A fixed node (§4): its one member is ``.node``, the node's seq. ``AtNode``
    is one, and so is a checkpoint object. A resolved ``Head`` is not, since its
    node may be ``None``. Provisional."""

    @property
    def node(self) -> int: ...


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


@dataclass(frozen=True)
class Element:
    """One element of an eager stream (§5): the node that committed it, the
    value's seq, and its body's ``value`` and ``t``."""

    node: int
    seq: int
    value: Any
    t: float | None


class Tier(StrEnum):
    """The viewpoint a verdict rests on (§5), one per record kind: ``EXIT``, the
    claim's own exit; ``DIED``, the death reports and unreadable records that
    ended it; ``TERMINATED``, an unclaimed run's launch. The Watcher's inference
    tiers (``probed_dead``, ``heartbeat_stale``, ``episode_lock_released``) are
    not a pure read's, and join at layer 2's flip, which makes this
    ``observables.Tier``. PROVISIONAL."""

    EXIT = "exit"
    DIED = "died"
    TERMINATED = "terminated"


@dataclass(frozen=True)
class DeathReport:
    """A readable ``supervisor.died`` naming the claim (§5): its seq, outcome,
    evidence, which no fold parses, and its writer's ``t``."""

    seq: int
    outcome: Outcome04
    evidence: str
    t: float


@dataclass(frozen=True)
class RunResult04:
    """The verdict (§5), as 0.4.0's ``RunResult`` will be:

    - ``outcomes``: never empty. One under an exit or a launch's ``terminated``;
      under death reports, the set of their outcomes, a ``lost`` for each
      unreadable record that ended the claim, less ``lost`` beside another;
    - ``reports``: the readable death reports naming the claim, in seq order,
      under ``DIED`` only;
    - ``node``: the exit node's seq under ``EXIT``, else ``None``;
    - ``claim_seq``: the claim the verdict is about, ``None`` for a run nothing
      claimed;
    - ``malformed``: the seqs of the unreadable records the verdict skipped, so
      they are reported, not raised;
    - ``tier``: the viewpoint;
    - ``error``: the exit's error under ``EXIT``, else ``None``: under ``DIED``
      the evidence is in ``reports``.

    PROVISIONAL: layer 2's flip makes it ``observables.RunResult``, where the
    Watcher stamps ``run_id``, which a pure read of a channel cannot know."""

    outcomes: frozenset[Outcome04]
    reports: tuple[DeathReport, ...]
    node: int | None
    claim_seq: int | None
    malformed: tuple[int, ...]
    tier: Tier
    error: str | None

    def __post_init__(self) -> None:
        if not self.outcomes:
            raise ValueError("a verdict's outcomes are never empty")
        if (self.node is not None) != (self.tier == Tier.EXIT):
            raise ValueError("a verdict names its node under an exit, and only there")
        if (self.claim_seq is None) != (self.tier == Tier.TERMINATED):
            raise ValueError("a verdict names its claim unless nothing claimed")
        if self.reports and self.tier != Tier.DIED:
            raise ValueError("a verdict carries reports under death reports only")
        if self.error is not None and self.tier != Tier.EXIT:
            raise ValueError("a verdict carries an error under an exit only")


# ----- the reads (§5) ---------------------------------------------------------------


def lineage(ch: Channel, *, head: HeadStrategy) -> list[int]:
    """The node seqs from the root to the head; ``[]`` for the empty lineage."""
    return [at.env.seq for at in _walk(ch, head.resolve(ch)).nodes]


def count(ch: Channel, x: str | Commits, *, node: int | None) -> int:
    """``count(X, node)`` (§2): the number of X's elements on the lineage up to and
    including ``node``, where X is an eager stream or ``Commits()``. ``node=None``
    is the empty lineage, which counts 0. A ``node`` that is not a node raises
    ``ValueError``, as ``AtNode`` does."""
    if node is None:
        return 0
    return _count(_walk(ch, AtNode(node).resolve(ch)), x)


def prefix_len(ch: Channel, x: str | Commits, *, head: HeadStrategy) -> int:
    """``count(x, head)`` (§5): what ``progress`` was, as a count. A name that was
    a source throughout counts 0."""
    return _count(_walk(ch, head.resolve(ch)), x)


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


def verdict(ch: Channel) -> RunResult04 | None:
    """How the run ended (§5), at a W taken first, or ``None`` while the newest
    claim is held or nothing has ended.

    The newest claim's readable exit, its self-report, is the verdict
    (``Tier.EXIT``). Else the death reports naming the claim are
    (``Tier.DIED``): their outcomes as a set, less ``lost`` beside another, so
    the verdict does not depend on their order and only grows as reports
    arrive. An unreadable ``stopped`` or ``died`` whose ``claim_seq`` names the
    claim still ends it, and counts as a ``lost``: it says only that the claim
    ended. No more of its body is read. Its seq is in ``malformed``, and so is
    an unreadable one in the claim's window naming no claim, which may have
    been the claim's own; one naming another claim is that claim's. While the
    claim is held, though, the verdict is ``None`` and that nameless junk is
    reported nowhere; layer 2's held status may carry it.

    The fold reads only the claim's window (``_window``): an older claim's
    records, a displaced worker's included, say nothing of the newest claim's
    end, and a malformed record from a dead past cannot poison it. A second
    readable exit of the claim raises ``MalformedRecordError``, as the head fold
    does: §2 allows one per claim. A ``supervisor.terminated`` ends nothing for
    a claimed run.

    The claim's ``started`` body, its commits and rewinds, and the exit's
    ``parent`` are not read here: they are the head fold's checks (§5,
    "Well-formedness"), so ``Tier.EXIT`` at a node does not mean the head fold
    accepts the run.

    A run nothing claimed at or below W takes its launch's ``terminated``
    (``Tier.TERMINATED``), as 0.3.0's null-worker startup crash does: the newest
    at or below W, whichever launch it names, a backstop's ``backstop@<seq>``
    that no ``launched`` names included. A signal reads ``killed``, an exit 0
    ``completed`` and any other exit ``errored``, as in 0.3.0. As in 0.3.0 too,
    the verdict rests on that one record, so an unreadable one, or one naming no
    launch, raises ``MalformedRecordError``."""
    w = ch.last_seq()
    claim = _newest(ch, _Topic.STARTED, w)
    if claim is None:
        return _launch_end(ch, w)
    return _claim_end(ch, claim, w)


def undischarged_stops(ch: Channel) -> list[int]:
    """The seqs of the pending ``control.stop`` records, ascending, at a W taken
    first (§2, §5, "The answer fold").

    By name, for the whole log: a stop is spent once an exit's ``honored``, a
    ``control.withdraw`` or a ``lifecycle.nak`` names its ``request_id``,
    wherever that sits, a displaced worker's exit included, since a stop is a
    request about the process. A spent id stays spent, and a re-sent live one is
    one stop, at its newest send. A death report spends nothing. An unreadable
    exit's ``honored`` is not read (§5: no fold reads more of an unreadable
    record's body), and a stop or an answer with no ``request_id`` names
    nothing. One-shot: the question is the whole log, so it is never polled."""
    w = ch.last_seq()
    if w == 0:
        return []
    stops: dict[str, int] = {}
    spent: set[str] = set()
    for e in ch.read(
        topics=[_Topic.STOP, _Topic.STOPPED, _Topic.WITHDRAW, _Topic.NAK], limit=w
    ):
        if e.seq > w:
            break
        if e.topic == _Topic.STOPPED:
            exit_ = _readable(_Stopped, e)
            if exit_ is not None:
                spent.update(exit_.honored)
        elif e.request_id is None:
            continue
        elif e.topic == _Topic.STOP:
            stops[e.request_id] = e.seq
        else:
            spent.add(e.request_id)
    return sorted(seq for r, seq in stops.items() if r not in spent)


# ----- the head fold ----------------------------------------------------------------

_Lookup = Callable[[int], Envelope | None]


def _point(ch: Channel, w: int, seq: int) -> Envelope | None:
    """The record at ``seq``, or ``None`` when there is none at or below ``w``."""
    if not 1 <= seq <= w:
        return None
    got = ch.read(after=seq - 1, limit=1)
    return got[0] if got and got[0].seq == seq else None


def _newest(ch: Channel, topic: _Topic, w: int) -> Envelope | None:
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
    guard; it is never ``limit=0``, on which the backends disagree (memory
    returns one record)."""
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
    return c if _is_int(c) and c >= 1 else None


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
    claim = _newest(ch, _Topic.STARTED, w)
    if claim is None:
        return Head(None, False, w)

    def lookup(seq: int) -> Envelope | None:
        return _point(ch, w, seq)

    started = verdict_parse(_Started, claim)
    position = _resume_point(lookup, claim, started.resume_from)
    exit_: _At | None = None
    for e in _window(ch, claim, w, [_Topic.COMMIT, _Topic.REWIND, _Topic.STOPPED]):
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
        if isinstance(body, _Rewind):
            position = _resume_point(lookup, e, body.resume_from)
            continue
        if body.parent != position:
            raise MalformedRecordError(
                e.seq,
                e.topic,
                f"parent {body.parent} is not its claim's position {position}",
            )
        if isinstance(body, _Commit):
            position = e.seq
        else:
            exit_ = _At(e, body)
    if exit_ is not None:
        return Head(exit_.env.seq, exit_.node.closed, w)
    return Head(position, False, w)


def _claims(e: Envelope, claim: int) -> _Commit | _Rewind | _Stopped | None:
    """``e``'s body when it is one of ``claim``'s position-bearing records, else
    ``None``: another claim's, one that names no claim, or an unreadable exit,
    which is no self-report and so absent for the head (§5, "The verdict"). A
    commit or a rewind that names ``claim`` must be readable, or the position is
    unknown."""
    if e.topic == _Topic.STOPPED:
        stop = _readable(_Stopped, e)
        return stop if stop is not None and stop.claim_seq == claim else None
    if _named_claim(e) != claim:
        return None
    if e.topic == _Topic.REWIND:
        return verdict_parse(_Rewind, e)
    return verdict_parse(_Commit, e)


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


@dataclass(frozen=True)
class _At:
    """A node: its record, and its parsed body."""

    env: Envelope
    node: _Node


def _node_at(e: Envelope) -> _At | None:
    """The node at ``e``, or ``None`` when its topic is not a node's. A node's
    unreadable body raises ``MalformedRecordError``."""
    if e.topic == _Topic.COMMIT:
        return _At(e, verdict_parse(_Commit, e))
    if e.topic == _Topic.STOPPED:
        return _At(e, verdict_parse(_Stopped, e))
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


@dataclass(frozen=True)
class _Walked:
    """A lineage walked: every record at or below its head node, by seq, and its
    nodes, root first."""

    records: dict[int, Envelope]
    nodes: list[_At]


def _walk(ch: Channel, head: Head) -> _Walked:
    """The lineage ending at ``head.node``, checked (§5): on every node walked, a
    ``parent`` or a ``values`` seq at or above the node's own seq, and a
    ``parent`` naming no extendable node; along the lineage, a seq committed
    twice. A head node that is not a node (a hand-built ``Head``) raises
    ``ValueError``."""
    if head.node is None:
        return _Walked({}, [])
    bound = head.node
    records = {e.seq: e for e in ch.read(limit=bound) if e.seq <= bound}
    first = records.get(bound)
    at = None if first is None else _node_at(first)
    if at is None:
        raise ValueError(f"head node {bound} is not a node")
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
        at = _extendable(records.get, at.env, "parent", node.parent)
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
    return _Walked(records, chain)


def _count(walked: _Walked, x: str | Commits) -> int:
    if isinstance(x, Commits):
        return sum(at.node.iterations for at in walked.nodes)
    return len(_members(walked, x))


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
            e = walked.records.get(s)
            if e is None or e.topic != _Topic.VALUE:
                what = "no record" if e is None else f"a {e.topic}"
                raise MalformedRecordError(
                    at.env.seq, at.env.topic, f"values names {s}, {what}, not a value"
                )
            if e.name != stream:
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
    if e is None or e.topic != _Topic.STARTED:
        raise MalformedRecordError(
            at.env.seq,
            at.env.topic,
            f"claim_seq {claim} names no lifecycle.started below it",
        )
    return frozenset(verdict_parse(_Started, e).sources)


def _element(walked: _Walked, at: _At, seq: int) -> Element:
    """The element at value ``seq``, its body read: a malformed one raises."""
    v = verdict_parse(_Value, walked.records[seq])
    return Element(node=at.env.seq, seq=seq, value=v.value, t=v.t)


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
    stop: _Stopped


def _claim_end(ch: Channel, claim: Envelope, w: int) -> RunResult04 | None:
    """How ``claim`` ended, from the records in its window (``verdict``)."""
    exit_: _Exit | None = None
    reports: list[DeathReport] = []
    ended_unread = False  # an unreadable record naming the claim ended it
    malformed: list[int] = []
    for e in _window(ch, claim, w, [_Topic.STOPPED, _Topic.DIED]):
        named = _named_claim(e)
        if named is not None and named != claim.seq:
            continue  # another claim's
        body: _Stopped | _Died | None = (
            _readable(_Stopped, e) if e.topic == _Topic.STOPPED else _readable(_Died, e)
        )
        if body is None:
            malformed.append(e.seq)
            ended_unread = ended_unread or named is not None
        elif isinstance(body, _Died):
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
        return RunResult04(
            outcomes=frozenset({_self_report(exit_.stop)}),
            reports=(),
            node=exit_.env.seq,
            claim_seq=claim.seq,
            malformed=tuple(malformed),
            tier=Tier.EXIT,
            error=exit_.stop.error,
        )
    outcomes = {r.outcome for r in reports}
    if ended_unread:
        outcomes.add(Outcome04.LOST)
    if not outcomes:
        return None  # held
    if len(outcomes) > 1:
        outcomes.discard(Outcome04.LOST)  # it says only what every report says
    return RunResult04(
        outcomes=frozenset(outcomes),
        reports=tuple(reports),
        node=None,
        claim_seq=claim.seq,
        malformed=tuple(malformed),
        tier=Tier.DIED,
        error=None,
    )


def _self_report(stop: _Stopped) -> Outcome04:
    """An exit's outcome: ``errored`` when it carries an error, else
    ``completed`` or ``preempted``. A completed exit carries none."""
    if stop.error is not None:
        return Outcome04.ERRORED
    return Outcome04.COMPLETED if stop.completed else Outcome04.PREEMPTED


def _launch_end(ch: Channel, w: int) -> RunResult04 | None:
    """An unclaimed run's verdict: its newest ``terminated`` at or below ``w``
    (``verdict``)."""
    e = _newest(ch, _Topic.TERMINATED, w)
    if e is None:
        return None
    end = verdict_parse(_Terminated, e)
    if not e.request_id:
        raise MalformedRecordError(
            e.seq, e.topic, "names no launch: supervisor-v0.5 requires its request_id"
        )
    if end.reason == _Reap.KILLED:
        outcome = Outcome04.KILLED
    elif end.exit_code == 0:
        outcome = Outcome04.COMPLETED
    else:
        outcome = Outcome04.ERRORED
    return RunResult04(
        outcomes=frozenset({outcome}),
        reports=(),
        node=None,
        claim_seq=None,
        malformed=(),
        tier=Tier.TERMINATED,
        error=None,
    )
