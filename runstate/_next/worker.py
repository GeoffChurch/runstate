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
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import asdict
from enum import Enum
from types import MappingProxyType
from typing import NamedTuple

from ..channel import Channel, Envelope
from ..vocabulary.handle import local_handle
from ..vocabulary.launch import current_launch_id
from .checkpoint import SavePolicy
from .claims import endings
from .launcher import report_if_dead
from .observables import LatestClaimHead
from .vocabulary.coordinate import NodeRef
from .vocabulary.payloads import Commit, Rewind, Started, Topic, Value
from .vocabulary.schedule import Coordinates
from .walk import NodeKind, coordinates, kind, node_at, walk

# The coordinates of the empty lineage: no element, no commit (§2: empty is 0).
_ROOT = Coordinates(streams=MappingProxyType({}), commits=0)


class _Phase(Enum):
    """Where the worker is in its claim's life. Only a ``LIVE`` worker writes."""

    LIVE = "live"
    """It won the claim, and computes."""
    LOST = "lost"
    """It lost the claim race: it never claimed, and writes nothing (§4)."""


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
    nothing, ever, and its ``commit()`` returns True (``claimed`` says why)."""

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
        self._resume_node: int | None = None
        self._lineage = _Lineage([], [], [])
        self._pending: list[_Pending] = []
        self._discarded: set[int] = set()  # values this claim discarded at a rewind
        self._registers: dict[str, object] = {}
        self._cursor = 0  # the control-drain watermark, ``consumed_seq``
        self._claim_run(resume_from)

    # ----- the claim (§4, "The claim") -------------------------------------------

    def _claim_run(self, resume_from: NodeRef | LatestClaimHead | None) -> None:
        """Claim the run, in order: if the latest claim is held, call
        ``report_if_dead`` (§3), and start again if it reports, else lose the claim;
        CAS ``started`` at W; after winning, rebuild at the position. A lost CAS
        starts again from a new W.

        "Held" is the presence test (``claims.endings``): no exit and no death
        report names the latest claim, readable or not. It never raises, so a
        contradiction in an old claim's window cannot keep the run from being
        claimed, and it agrees with ``report_if_dead``'s own test (§3)."""
        while True:
            node, w = self._resume_point(resume_from)
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
        self._resume_node = node
        self._lineage = _Lineage.at(self._ch, node)

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
    def resume_node(self) -> int | None:
        """The node the claim resumed from, which a rewind moves to its target, or
        ``None`` when it names no node: what the checkpoint recipe's load assertion
        compares (§6)."""
        return self._resume_node

    # ----- values (§4, "Values") ---------------------------------------------------

    def emit(self, name: str, value: object) -> None:
        """Send a ``value`` of the eager stream ``name`` at once, and hold its seq
        for the next node to commit. ``ValueError`` on a declared source, which is
        sampled on demand, never emitted. A value that does not serialize raises
        ``TypeError`` naming it: a broken reporting path stops the run."""
        if self._phase is not _Phase.LIVE:
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
        if self._phase is not _Phase.LIVE:
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
        if self._phase is not _Phase.LIVE:
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
        """End an iteration: write one ``lifecycle.commit`` extending the position,
        committing the pending values in the order they were added; run the save
        policy once it has landed (§6); and return the stop decision.

        A save that raises propagates, and the node stands (§6): the claim ends,
        and its exit, errored and torn, names that node as its ``parent``."""
        if self._phase is not _Phase.LIVE:
            return True
        at = self._written(iterations=1)
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
        return False

    # ----- the position (§4, "rewind()", "At every position change") ---------------

    def rewind(self, target: NodeRef | None) -> None:
        """Restate the position (§2): write ``lifecycle.rewind`` naming ``target``,
        an extendable node of the run validated as ``resume_from``'s is, or the root
        for ``None``. Pending values are discarded: they stay on the log outside
        every lineage, so commit first to keep them. ``resume_node`` moves to the
        target.

        The new lineage is walked before the record is written, so a target whose
        lineage is malformed raises with nothing written."""
        if self._phase is not _Phase.LIVE:
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
