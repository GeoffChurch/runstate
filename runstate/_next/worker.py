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
reports naming the claim, then ``control.*`` above the watermark. A stop registers
or is refused; a withdrawal spends what it names. Every record a drain writes is
compare-and-appended at the tail it last read, so a death report that lands first
fences the worker before it refuses anything. Requests are answered by name, for
the whole log (§2, "Requests"): the claim folds every answer on the log below it,
and a spent id is dead on arrival, on every control topic. A record with a missing
or empty id is nameless: nothing can spend it, so a claim refuses one only above the
previous claim's ``started``.

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
from dataclasses import asdict
from enum import Enum
from types import MappingProxyType
from typing import NamedTuple, Self

from ..channel import Channel, Envelope
from ..vocabulary.handle import local_handle
from ..vocabulary.launch import current_launch_id
from .checkpoint import SavePolicy
from .claims import endings, ends
from .launcher import report_if_dead
from .observables import LatestClaimHead
from .vocabulary.coordinate import Commits, NodeRef, Target
from .vocabulary.payloads import (
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
    Term,
    parse_stop_trigger,
    satisfied,
    unsatisfiable,
)
from .walk import readable
from .walk import NodeKind, coordinates, kind, node_at, walk

# What a drain reads: every control request, a read-glob the substrate expands, not
# a wire topic.
_CONTROL = "control.>"

# The coordinates of the empty lineage: no element, no commit (§2: empty is 0).
_ROOT = Coordinates(streams=MappingProxyType({}), commits=0)


class _Phase(Enum):
    """Where the worker is in its claim's life. Only a ``LIVE`` worker commits; a
    ``DISCARDED`` one still writes its exit; the rest write nothing.

    ``LIVE`` -> ``DISCARDED`` (``discard()``) -> ``STOPPED`` (its exit landed) or
    ``RELEASED`` (a drain read a death report naming its claim), from either of
    the first two; ``LOST`` from the start."""

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


class _Stop(NamedTuple):
    """A drained, pending ``control.stop``: its ``from``, parsed at the drain
    (``None`` holds once drained), and the claim's clock when it registered, which
    a time atom counts from."""

    from_: Term | None
    registered_at: float


class _Pending(NamedTuple):
    """A value the next node commits: its seq, and its name, an eager stream's."""

    seq: int
    name: str


class _Lineage:
    """The lineage at the position, root first (§4, "At every position change"):
    each node's seq, the coordinates at and including it, and the value seqs it
    commits. It is the Worker's count map, so a rewind to a node on it is a
    truncation, and a target elsewhere costs the walk from it."""

    def __init__(
        self, nodes: list[int], coords: list[Coordinates], values: list[list[int]]
    ) -> None:
        self._nodes = nodes
        self._coords = coords
        self._values = values
        self._index = {n: i for i, n in enumerate(nodes)}
        self._committed = {s for vs in values for s in vs}

    @classmethod
    def at(cls, ch: Channel, node: int | None) -> _Lineage:
        """The lineage ending at ``node`` (``None``: the empty lineage), walked and
        checked (§5, "The pure walk"), its coordinates resolved."""
        walked = walk(ch, node)
        return cls(
            [at.env.seq for at in walked.nodes],
            coordinates(walked),
            [list(at.node.committed) for at in walked.nodes],
        )

    @property
    def node(self) -> int | None:
        """The position: the newest node, or ``None`` for the empty lineage."""
        return self._nodes[-1] if self._nodes else None

    @property
    def tip(self) -> Coordinates:
        """The coordinates at the position."""
        return self._coords[-1] if self._coords else _ROOT

    def commits(self, seq: int) -> bool:
        """Does a node of the lineage commit the value ``seq``?"""
        return seq in self._committed

    def truncated(self, node: int | None) -> _Lineage | None:
        """The prefix of this lineage that ends at ``node``, or ``None`` when
        ``node`` is not on it."""
        if node is None:
            return _Lineage([], [], [])
        i = self._index.get(node)
        if i is None:
            return None
        return _Lineage(
            self._nodes[: i + 1], self._coords[: i + 1], self._values[: i + 1]
        )

    def extend(self, node: int, at: Coordinates, values: list[int]) -> None:
        """Append the node the claim just wrote."""
        self._index[node] = len(self._nodes)
        self._nodes.append(node)
        self._coords.append(at)
        self._values.append(values)
        self._committed.update(values)


def _record_at(ch: Channel, seq: int, w: int) -> Envelope | None:
    """The record at ``seq``, one point read, or ``None`` when there is none at or
    below ``w``."""
    if not 1 <= seq <= w:
        return None
    got = ch.read(after=seq - 1, limit=1)
    return got[0] if got and got[0].seq == seq else None


def _extendable(ch: Channel, seq: int, w: int) -> None:
    """Check, with one point read, that ``seq`` names an extendable node of this run
    at or below ``w`` (§4: what ``resume_from`` and ``rewind`` may name); raise
    ``ValueError`` naming what it is instead. A node whose body is unreadable raises
    ``MalformedRecordError``."""
    e = _record_at(ch, seq, w)
    if e is None:
        raise ValueError(f"no record at seq {seq} at or below {w}")
    at = node_at(e)
    if at is None:
        raise ValueError(f"seq {seq} is a {e.topic}, not a node")
    k = kind(at.node)
    if k is not NodeKind.EXTENDABLE:
        raise ValueError(f"seq {seq} is a {k} node: only an extendable one extends")


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
    exception, preempted otherwise, unless the worker already stopped."""

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
        self._claim = 0  # the claim's seq, once won
        self._previous = 0  # the previous claim's seq, 0 when there is none
        self._resume_node: int | None = None
        self._lineage = _Lineage([], [], [])
        self._pending: list[_Pending] = []
        self._discarded: set[int] = set()  # values this claim discarded at a rewind
        self._registers: dict[str, object] = {}
        self._cursor = 0  # the control-drain watermark, ``consumed_seq``
        self._reports_read = 0  # the death reports are read up to here
        self._spent: set[str] = set()  # request ids an answer names (§2, "Requests")
        self._stops: dict[str, _Stop] = {}  # pending: drained, not yet held
        self._held: list[str] = []  # the stops whose ``from`` held, in that order
        self._claim_run(resume_from)

    # ----- the claim (§4, "The claim") -------------------------------------------

    def _claim_run(self, resume_from: NodeRef | LatestClaimHead | None) -> None:
        """Claim the run, in order (§4, "The claim"): read W, validate
        ``resume_from`` there and walk the lineage that ends at its node, so a
        malformed one raises with nothing written; if the latest claim is held,
        call ``report_if_dead`` (§3), and start again if it reports, else lose the
        claim; CAS ``started`` at W; after winning, fold the answers by name up to
        that same W, and take the walked lineage as the position. A lost CAS starts
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
        while True:
            node, w = self._resume_point(resume_from)
            if lineage is None or lineage.node != node:
                lineage = _Lineage.at(self._ch, node)  # raises before the CAS
            latest = self._ch.latest(Topic.LIFECYCLE_STARTED)
            if latest is not None and latest.seq > w:
                continue  # a claim landed since W: the CAS at W would lose
            if latest is not None and not endings(self._ch, latest, w):
                if report_if_dead(self._ch) is not None:
                    continue  # its death is on the log now: claim again
                return  # held by a live claimant: lost
            body = asdict(
                Started(
                    handle=self._started.handle,
                    t=self._started.t,
                    resume_from=node,
                    sources=self._started.sources,
                )
            )
            seq = self._ch.send(
                body,
                topic=Started.TOPIC,
                # The claim names the launch it answers (ambient; None when no
                # launcher spawned it), so a launcher's death report names the
                # claim (specs/launcher-record-identity.md).
                request_id=current_launch_id(),
                expected_seq=w,
            )
            if seq is not None:
                break
        self._phase = _Phase.LIVE
        self._claim = seq
        self._previous = 0 if latest is None else latest.seq
        self._reports_read = seq
        self._resume_node = node
        self._fold_answers(w)
        self._lineage = lineage

    def _fold_answers(self, w: int) -> None:
        """The answers on the log at or below ``w``, by name (§4, "The claim", step
        4): a request is spent once a ``control.withdraw`` or a ``lifecycle.nak``
        names it, and a stop once an exit's ``honored`` does, wherever that sits, a
        displaced worker's exit included, so a resumed claim never re-honors a stop.
        An unreadable exit's ``honored`` is not read, as no fold reads more of an
        unreadable record (§5); an answer with a missing or empty id names
        nothing."""
        topics: list[str] = [
            Topic.LIFECYCLE_STOPPED,
            Topic.CONTROL_WITHDRAW,
            Topic.LIFECYCLE_NAK,
        ]
        for e in self._ch.read(topics=topics, limit=w) if w else []:
            if e.seq > w:
                break
            if e.topic == Topic.LIFECYCLE_STOPPED:
                exit_ = readable(Stopped, e)
                self._spent.update(() if exit_ is None else exit_.honored)
            elif e.request_id:
                self._spent.add(e.request_id)

    def _resume_point(
        self, resume_from: NodeRef | LatestClaimHead | None
    ) -> tuple[int | None, int]:
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
                return None, head.w
            e = _record_at(self._ch, head.node, head.w)
            at = None if e is None else node_at(e)
            assert at is not None  # a resolved head node is a node at or below w
            if kind(at.node) is NodeKind.TORN:
                return at.node.parent, head.w
            return head.node, head.w
        w = self._ch.last_seq()
        if resume_from is None:
            return None, w
        _extendable(self._ch, resume_from.node, w)
        return resume_from.node, w

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
        return self._phase is not _Phase.LIVE or bool(self._held)

    @property
    def resume_node(self) -> int | None:
        """The node the claim resumed from, which a rewind moves to its target, or
        ``None`` when it names no node: what the checkpoint recipe's load assertion
        compares (§6)."""
        return self._resume_node

    def _acts(self) -> bool:
        """Whether ``emit``, ``set``, ``include`` and ``rewind`` act: only for a
        live worker. A lost, released or stopped worker's do nothing, since a
        released one cannot know when its report landed, and a stopped one is
        fenced the same way (§4, "A worker after its own exit is fenced the same
        way"); after ``discard()``, until the exit, they raise ``RuntimeError``:
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
        try:
            seq = self._ch.send(
                asdict(Value(value=value, t=self._now())), topic=Value.TOPIC, name=name
            )
        except (TypeError, ValueError) as exc:
            raise TypeError(
                f"value for {name!r} ({type(value).__name__}) is not "
                "JSON-serializable; pass json_default to "
                "current_channel()/create_channel() to coerce it"
            ) from exc
        assert seq is not None  # an unconditional send always lands
        self._pending.append(_Pending(seq, name))

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
        self._registers[name] = value

    def include(self, seq: int) -> None:
        """Add to the next node the value at ``seq``, sent through another channel
        handle (§4). One point read; ``ValueError`` unless it is a ``value`` with a
        name and no ``request_id``, above the claim, neither pending, nor committed
        on the current lineage, nor discarded by this claim at a rewind, and its name
        is not a declared source. Authorship stays unprovable: an honor system."""
        if not self._acts():
            return
        e = _record_at(self._ch, seq, self._ch.last_seq())
        if e is None or e.topic != Topic.VALUE:
            raise ValueError(f"seq {seq} is not a value record")
        if e.name is None or e.request_id is not None:
            raise ValueError(
                f"the value at seq {seq} must name a stream and no request "
                f"(name {e.name!r}, request_id {e.request_id!r})"
            )
        if seq <= self._claim:
            raise ValueError(f"seq {seq} is at or below the claim {self._claim}")
        if any(p.seq == seq for p in self._pending):
            raise ValueError(f"seq {seq} is already pending")
        if self._lineage.commits(seq):
            raise ValueError(f"seq {seq} is committed on the current lineage")
        if seq in self._discarded:
            raise ValueError(f"seq {seq} was discarded at a rewind")
        if e.name in self._sources:
            raise ValueError(f"{e.name!r} is a declared source, never an element")
        self._pending.append(_Pending(seq, e.name))

    # ----- the node (§4, "commit()") -----------------------------------------------

    def _written(self, *, iterations: int) -> Coordinates:
        """The coordinates at the node being written (§3, "Evaluation"): the
        position's, with the pending values counted and ``iterations`` added, 1 for a
        commit and 0 for the exit."""
        tip = self._lineage.tip
        streams = dict(tip.streams)
        for p in self._pending:
            streams[p.name] = streams.get(p.name, 0) + 1
        return Coordinates(MappingProxyType(streams), tip.commits + iterations)

    def commit(self) -> bool:
        """End an iteration, in order (§4, "commit()"): drain; evaluate the stops
        at the node being written; write one ``lifecycle.commit`` extending the
        position, committing the pending values in the order they were added; run
        the save policy once it has landed (§6); return the stop decision.

        A save that raises propagates, and the node stands (§6): the claim ends,
        and its exit, errored and torn, names that node as its ``parent``."""
        if self._phase is not _Phase.LIVE or self._drain() is None:
            return True  # writes nothing, or released by a report the drain read
        at = self._written(iterations=1)
        decision = self._decide(at)
        values = [p.seq for p in self._pending]
        body = Commit(
            claim_seq=self._claim,
            consumed_seq=self._cursor,
            parent=self._lineage.node,
            values=values,
            fired=[],
            expired=[],
            t=self._now(),
        )
        seq = self._ch.send(asdict(body), topic=Commit.TOPIC)
        assert seq is not None  # an unconditional send always lands
        self._lineage.extend(seq, at, values)
        self._pending = []
        self._save.after(seq, commits=at.commits, exit=False)
        return decision

    def heartbeat(self) -> bool:
        """Liveness inside a slow iteration (§4): drain, decide the stops at the
        position and the heartbeat's clock, and write a ``lifecycle.heartbeat``.
        It commits nothing and evaluates no subscription. It returns the stop
        decision, so a stop drained mid-iteration whose ``from`` holds, or one whose
        time ``from`` comes due inside the iteration, lands here."""
        if self._phase is not _Phase.LIVE or self._drain() is None:
            return True
        decision = self._decide(self._lineage.tip)
        body = Heartbeat(
            claim_seq=self._claim, consumed_seq=self._cursor, t=self._now()
        )
        self._ch.send(asdict(body), topic=Heartbeat.TOPIC)
        return decision

    # ----- the drain (§4, "A drain") -------------------------------------------------

    def _drain(self) -> int | None:
        """Read W; read the death reports naming the claim at or below W, and latch
        ``released`` on one, returning ``None``; else handle each ``control.*``
        record above the watermark, and return the tail the drain last read, its
        own writes included, which the exit compare-and-appends at.

        Each refusal is a ``nak`` compare-and-appended at that tail, naming the
        request it refuses, or null for a nameless one. Once it lands it spends the
        id, and drops a stop registered under it (a refused re-send). A lost CAS
        means a record landed unread, perhaps a death report, so the drain runs
        again from the request it was refusing: the records before it were
        handled, and the watermark is past them."""
        while True:
            w = self._ch.last_seq()
            if self._reported(w):
                self._phase = _Phase.RELEASED
                return None
            tail = self._drain_control(w)
            if tail is not None:
                return tail

    def _reported(self, w: int) -> bool:
        """Does a ``supervisor.died`` at or below ``w`` name the claim? Presence
        alone, readable or not (§3): a report's other fields are no reason to keep
        writing for a claim it ended."""
        lo = self._reports_read
        if w <= lo:
            return False
        got = self._ch.read(after=lo, topics=[Topic.SUPERVISOR_DIED], limit=w - lo)
        self._reports_read = w
        return any(e.seq <= w and ends(e) == self._claim for e in got)

    def _drain_control(self, w: int) -> int | None:
        """Handle the control records above the watermark and at or below ``w``;
        the tail, or ``None`` when a refusal's CAS lost."""
        tail = w
        if w <= self._cursor:
            return tail
        for e in self._ch.read(
            after=self._cursor, topics=[_CONTROL], limit=w - self._cursor
        ):
            if e.seq > w:
                break
            refusal = self._take(e)
            if refusal is not None:
                r = e.request_id or None  # an empty id is no name
                seq = self._ch.send(
                    asdict(refusal),
                    topic=Nak.TOPIC,
                    request_id=r,
                    expected_seq=tail,
                )
                if seq is None:
                    return None  # run the drain again, from this request
                tail = seq
                if r is not None:
                    self._spent.add(r)  # a nak names what it spends
                    self._stops.pop(r, None)  # a refused re-send drops its stop
            self._cursor = e.seq
        return tail

    def _take(self, e: Envelope) -> Nak | None:
        """Handle one control record, or return the refusal to write for it,
        having changed nothing: the refusal takes effect only once it lands.

        By its id first, on every control topic (§4, "A drain"):

        - **A missing or empty id is nameless,** and refused ``malformed``. Nothing
          can spend it, so it is refused once: only above the previous claim's
          ``started``, since an earlier claim had its chance.
        - **A spent id is skipped,** silently: "already answered" is not a
          refusal, and the id is dead on arrival.

        Then by its topic:

        - **A stop** (subscription-v0.4). Its trigger is parsed once here
          (``parse_stop_trigger``): ``malformed`` when it does not parse,
          ``unsatisfiable`` when a progress atom names a declared source (§3,
          "Refusals"). An accepted re-send of a pending id replaces its
          registration, its time ``from`` counted from the re-send; a refused one
          drops it once the refusal lands (``_drain_control``); a re-send of one
          that held is ignored, since the decision is a level.
        - **A withdrawal** spends what it names (§2): a pending stop that has not
          held is dropped, and one that held is too late, since the claim stops
          and ``honored`` names it (§4, "The exit").
        - **A subscription** is passed over (plan Task 12).
        - **Any other ``control.*`` topic** is refused ``unsupported``."""
        r = e.request_id
        if not r:
            if e.seq <= self._previous:
                return None  # an earlier claim had its chance to refuse it
            return Nak(
                reason=NakReason.MALFORMED,
                message=f"a {e.topic} is named by its request_id, and this one "
                "has none",
            )
        if r in self._spent:
            return None
        if e.topic == Topic.CONTROL_STOP:
            if r in self._held:
                return None
            try:
                trigger = parse_stop_trigger(e.body)
            except MalformedSchedule as exc:
                return Nak(reason=NakReason.MALFORMED, message=str(exc))
            if trigger is not None:
                why = unsatisfiable(
                    Schedule(from_=trigger, every=None, until=None),
                    sources=self._sources,
                )
                if why is not None:
                    return Nak(reason=NakReason.UNSATISFIABLE, message=why)
            self._stops[r] = _Stop(trigger, self._now())
            return None
        if e.topic == Topic.CONTROL_WITHDRAW:
            self._spent.add(r)
            self._stops.pop(r, None)
            return None
        if e.topic == Topic.CONTROL_SUBSCRIBE:
            return None
        return Nak(
            reason=NakReason.UNSUPPORTED, message=f"unknown control topic {e.topic!r}"
        )

    def _decide(self, at: Coordinates) -> bool:
        """The stop decision at the coordinates ``at`` and the clock now (§4): each
        pending stop whose ``from`` holds there is held for the rest of the claim,
        a monotone level; a stop with no ``from`` holds once drained. Every atom is
        a ``>=`` threshold, so a stop that held would hold again."""
        now = self._now()
        for r, stop in list(self._stops.items()):
            if stop.from_ is None or satisfied(
                stop.from_, at=at, time_seconds=now - stop.registered_at, count=0
            ):
                del self._stops[r]
                self._held.append(r)
        return bool(self._held)

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
            until is None or self._lineage.tip.of(until.x) < until.n
        ):
            yield self._lineage.tip.commits
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
        lineage = self._lineage.truncated(node) or _Lineage.at(self._ch, node)
        body = Rewind(claim_seq=self._claim, resume_from=node, t=self._now())
        self._ch.send(asdict(body), topic=Rewind.TOPIC)
        self._discarded.update(p.seq for p in self._pending)
        self._pending = []
        self._lineage = lineage
        self._registers = {}
        self._resume_node = node

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
        self._discarded.update(p.seq for p in self._pending)
        self._pending = []

    def stopped(self, *, completed: bool, error: str | None) -> None:
        """Write the exit node, the claim's last, in order (§4, "The exit"):

        1. read W and drain up to it: the death reports naming the claim, stops and
           withdrawals. No subscription is registered: one that arrives after the
           last commit stays live for the next claim;
        2. (plan Task 12: evaluate the subscriptions once, and write their samples);
        3. read W again, drain as in step 1, and compare-and-append ``stopped`` at
           the drain's tail, with ``parent`` the position, ``values`` the pending
           values, or null when the exit discards its iteration (an error, or after
           ``discard()``), and ``honored`` every stop that held during the claim and
           every stop pending now, a set whose order means nothing (§3). A lost
           CAS repeats this step only, so a stop first drained here is honored;
        4. if the exit is extendable, run the save policy (§6).

        ``completed`` is the worker's claim of intrinsic completion, which closes
        the lineage; a completed exit carries no ``error`` (``ValueError``). A
        second call does nothing, and so does a lost or released worker's.

        A save that raises propagates (§6, "A save that raises"). The exit has
        landed and stands with its own verdict, and the worker is stopped, so
        nothing more is written: the failure is the process's, on the external
        plane. The save is never run before the CAS, which would name a seq the exit
        may not get."""
        if self._phase not in (_Phase.LIVE, _Phase.DISCARDED):
            return
        if completed and error is not None:
            raise ValueError("a completed exit carries no error")
        discards = error is not None or self._phase is _Phase.DISCARDED
        if self._drain() is None:  # step 1
            return
        while True:  # step 3, until its CAS lands
            tail = self._drain()
            if tail is None:
                return
            values = None if discards else [p.seq for p in self._pending]
            body = Stopped(
                completed=completed,
                error=error,
                claim_seq=self._claim,
                honored=[*self._held, *self._stops],
                parent=self._lineage.node,
                values=values,
                fired=[],
                expired=[],
                t=self._now(),
            )
            seq = self._ch.send(asdict(body), topic=Stopped.TOPIC, expected_seq=tail)
            if seq is None:
                continue  # a record landed unread: drain it, and try again
            self._phase = _Phase.STOPPED
            if body.extendable:
                commits = self._lineage.tip.commits  # an exit adds no iteration
                self._save.after(seq, commits=commits, exit=True)
            return

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        """Write the exit unless the worker already stopped (§4): errored, and so
        torn, on an exception, whose message is its ``error``; otherwise preempted,
        ``stopped(completed=False, error=None)``. A lost or released worker writes
        nothing. The exception, if any, propagates."""
        if exc_type is not None:
            self.stopped(completed=False, error=str(exc))
        else:
            self.stopped(completed=False, error=None)
