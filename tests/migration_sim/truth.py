"""A history's true lineage: what a native 0.4.0 writer records for the same
history, in the terms of commits-and-lineage.md §2.

The native counterpart of a history runs the same workers through the same
events, writing 0.4.0's records instead of 0.3.0's. Each field below follows
from the history so:

- **``starts``**: each claim's ``resume_from`` is the node its origin's save
  captured, or null for a fresh start and for a checkpoint copied in from
  outside the log, which no node of the run names (a parentless resume).
- **``commits``**: one per finished iteration (``Finish``), in its claim's order:
  - **``parent``** is the claim's position before it: its previous commit, else
    its resume point, else the last rewind's target;
  - **``values``** are the seqs of the claim's labelled values written since its
    last position change (its pending values), in writing order. A value is
    never pending once its worker has written its own ``stopped``, and stepless
    values are registers, never pending (§7, M3).
- **``rewinds``**: one per ``Rewind``. It moves the position to its target and
  drops the pending values, which stay outside every lineage.
- **``exits``**: a worker's own ``stopped`` is its exit node, with ``parent`` the
  position at the exit:
  - **closed** if completed, holding the pending values;
  - **torn** (``values`` null) if it carries an error: every error exit discards
    its open iteration (§2);
  - **extendable** otherwise, holding the pending values. The native Worker
    discards only on ``w.discard()``, which no 0.3.0 history calls;
  - **``honored``** is what the worker's ``stopped`` honoured, less a stop a third
    party withdrew before the worker first drained it. The native worker drains
    at each commit, heartbeat and exit, and once drained, a stop with no
    ``from`` holds and stays honoured (§4).
- **Fencing.** A third party's release of a claim with no exit node, while its
  worker lives, fences that worker: a native worker reads the death report at
  its next drain and writes nothing more (§4). So it makes no commit, rewind or
  exit node after the release, and its pending values stay outside every
  lineage.
- **``deaths``**: the death reports a native run writes, each with its outcome:
  - a third party's release, unless the claim has an exit node (a native
    ``report_death`` returns None there), or a ``lost`` one on a claim that has
    already ended (§3);
  - a reap of a launch whose process is its claimant, when the claim has no exit
    node: ``killed`` for a signal, ``errored`` for a nonzero exit code, and
    ``lost`` for 0, which adds nothing once the claim has ended (§3, "The
    writers");
  - the successor's claim path: a claim that a later claim follows, when nothing
    ended it, is reported ``lost`` just before the later claim (§4, "The claim":
    ``report_if_dead``).
- **``terminations``**: every reap, as ``supervisor.terminated``; and a third
  party's report of a process's end (``Report(reap)``) as one under a launch the
  migration mints (``launch`` None).
- **``withdrawals``**: every stop a third party's ``stopped`` honoured, as
  ``control.withdraw`` (§7, "third-party-stop": a native third party clears a
  stop by withdrawing it).

Nodes are named by their claim's 0.3.0 ``started`` seq and their index in it
(``CommitId``, from 0), or by their claim alone (``ExitId``); values by their 0.3.0 seqs.
Two histories that share a log therefore have comparable truths: equal
``Lineage``s need the same output.

``places`` says where the native writer puts each record, relative to the 0.3.0
log: ``At(seq)`` for a record that is that 0.3.0 record renamed, and ``After(seq,
rank)`` for one it adds just after ``seq`` (``rank`` orders the additions). It is
for the structural checks and for diagnosis, and is not part of the lineage.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import NamedTuple, TypeAlias, assert_never

from .history import (
    Beacon,
    Beat,
    Claim,
    CommitOf,
    Emit,
    Exit,
    Exited,
    ExitOf,
    Finish,
    FromOutside,
    FromSave,
    Fresh,
    History,
    Kind,
    Killed,
    Launched,
    NodeRef,
    Outcome,
    ProcessEnd,
    Reaped,
    Release,
    Report,
    Rewind,
    StopRequest,
    ThirdPartyStop,
)
from .render import render


class CommitId(NamedTuple):
    claim_seq: int
    nth: int


class ExitId(NamedTuple):
    claim_seq: int


NodeId: TypeAlias = CommitId | ExitId


class Start(NamedTuple):
    claim_seq: int
    resume_from: NodeId | None


class Commit(NamedTuple):
    node: CommitId
    parent: NodeId | None
    values: tuple[int, ...]


class RewindTo(NamedTuple):
    claim_seq: int
    commits: int  # how many commits its claim had made before it
    target: NodeId | None


class ExitNode(NamedTuple):
    node: ExitId
    parent: NodeId | None
    values: tuple[int, ...] | None
    kind: Kind
    honored: tuple[str, ...]


class Died(NamedTuple):
    claim_seq: int
    outcome: Outcome


class Terminated(NamedTuple):
    launch: str | None  # None: a third party's report, under a minted launch
    how: ProcessEnd


class Withdrawal(NamedTuple):
    request_id: str
    by: int  # the seq of the third party's stopped that cleared it


class Lineage(NamedTuple):
    starts: tuple[Start, ...]
    commits: tuple[Commit, ...]
    rewinds: tuple[RewindTo, ...]
    exits: tuple[ExitNode, ...]
    deaths: tuple[Died, ...]  # sorted: a verdict reads their set (§5)
    terminations: tuple[Terminated, ...]
    withdrawals: tuple[Withdrawal, ...]


class At(NamedTuple):
    seq: int


class After(NamedTuple):
    seq: int
    rank: int


Place: TypeAlias = At | After
Record: TypeAlias = (
    Start | Commit | RewindTo | ExitNode | Died | Terminated | Withdrawal
)


class Placed(NamedTuple):
    record: Record
    place: Place


class Truth(NamedTuple):
    lineage: Lineage
    places: tuple[Placed, ...]


def place_key(place: Place) -> tuple[int, int]:
    """The native log's order: ``At(s)`` follows every addition after ``s - 1``."""
    if isinstance(place, At):
        return (place.seq, -1)
    return (place.seq, place.rank)


def _reap_outcome(how: ProcessEnd) -> Outcome:
    if isinstance(how, Killed):
        return Outcome.KILLED
    if isinstance(how, Exited):
        return Outcome.ERRORED if how.code else Outcome.LOST
    assert_never(how)


def truth(history: History) -> Truth:
    rendered = render(history)
    w = rendered.walk
    claim_seqs: dict[int, int] = {}
    position: dict[int, NodeId | None] = {}
    pending: dict[int, list[int]] = defaultdict(list)
    exited: set[int] = set()  # its own stopped is written (whether or not a node)
    exit_nodes: set[int] = set()
    ended: set[int] = set()
    made: dict[int, int] = defaultdict(int)  # claim -> the commits it has made
    drains: dict[int, list[int]] = defaultdict(list)  # the newest seq each drain saw
    stop_seqs: dict[str, int] = {}
    withdrawn: dict[str, list[int]] = defaultdict(list)
    claimants: dict[str, int] = {}
    wrappers: dict[str, bool] = {}
    placed: list[Placed] = []
    last = 0
    rank = 0

    def node_id(ref: NodeRef) -> NodeId:
        if isinstance(ref, CommitOf):
            return CommitId(claim_seqs[ref.claim], ref.nth)
        if isinstance(ref, ExitOf):
            return ExitId(claim_seqs[ref.claim])
        assert_never(ref)

    def after() -> After:
        nonlocal rank
        rank += 1
        return After(last, rank)

    def report(claim: int, outcome: Outcome, place: Place) -> None:
        """A death report a native writer makes, by §3's rules."""
        if claim in exit_nodes or (outcome is Outcome.LOST and claim in ended):
            return
        placed.append(Placed(Died(claim_seqs[claim], outcome), place))
        ended.add(claim)

    for i, ev in enumerate(history.events):
        seq = rendered.seqs[i]
        fenced = w.fenced[i]
        if isinstance(ev, Claim):
            assert seq is not None
            prev = ev.claim - 1
            if prev >= 0 and prev not in ended:
                # the claim path's report_if_dead (§4): the gate found it dead
                placed.append(Placed(Died(claim_seqs[prev], Outcome.LOST), after()))
                ended.add(prev)
            claim_seqs[ev.claim] = seq
            origin = ev.origin
            if isinstance(origin, FromSave):
                position[ev.claim] = node_id(origin.node)
            elif isinstance(origin, (Fresh, FromOutside)):
                position[ev.claim] = None
            else:
                assert_never(origin)
            placed.append(Placed(Start(seq, position[ev.claim]), At(seq)))
        elif isinstance(ev, Emit):
            assert seq is not None
            if not (fenced or ev.stepless or ev.claim in exited):
                pending[ev.claim].append(seq)
        elif isinstance(ev, (Beat, Beacon)):
            assert seq is not None
            drains[ev.claim].append(seq - 1)
        elif isinstance(ev, Finish):
            drains[ev.claim].append(last)
            node = w.nodes[i]
            if isinstance(node, CommitOf):
                cid = CommitId(claim_seqs[ev.claim], node.nth)
                commit = Commit(cid, position[ev.claim], tuple(pending[ev.claim]))
                placed.append(Placed(commit, after()))
                position[ev.claim] = cid
                pending[ev.claim] = []
                made[ev.claim] += 1
        elif isinstance(ev, Rewind):
            if not fenced:
                target = None if ev.target is None else node_id(ev.target)
                rewind = RewindTo(claim_seqs[ev.claim], made[ev.claim], target)
                placed.append(Placed(rewind, after()))
                position[ev.claim] = target
                pending[ev.claim] = []
        elif isinstance(ev, Exit):
            assert seq is not None
            exited.add(ev.claim)
            if isinstance(w.nodes[i], ExitOf):
                body = json.loads(rendered.rows[seq - 1].body)
                honored = _native_honored(
                    body["honored"], seq - 1, drains[ev.claim], stop_seqs, withdrawn
                )
                kind = w.exit_kinds[ev.claim]
                values = None if kind is Kind.TORN else tuple(pending[ev.claim])
                exit_node = ExitNode(
                    ExitId(claim_seqs[ev.claim]),
                    position[ev.claim],
                    values,
                    kind,
                    honored,
                )
                placed.append(Placed(exit_node, At(seq)))
                exit_nodes.add(ev.claim)
                ended.add(ev.claim)
        elif isinstance(ev, StopRequest):
            assert seq is not None
            stop_seqs[ev.request_id] = seq
        elif isinstance(ev, ThirdPartyStop):
            assert seq is not None
            body = json.loads(rendered.rows[seq - 1].body)
            if isinstance(ev.intent, Release):
                assert ev.names is not None
                report(ev.names, ev.intent.outcome, At(seq))
            elif isinstance(ev.intent, Report):
                if ev.intent.reap is not None:
                    placed.append(Placed(Terminated(None, ev.intent.reap), At(seq)))
            else:
                assert_never(ev.intent)
            last = seq
            for request_id in body["honored"]:
                placed.append(Placed(Withdrawal(request_id, seq), after()))
                withdrawn[request_id].append(seq)
        elif isinstance(ev, Launched):
            wrappers[ev.launch] = ev.wrapper
        elif isinstance(ev, Reaped):
            assert seq is not None
            placed.append(Placed(Terminated(ev.launch, ev.how), At(seq)))
            last = seq
            c = claimants.get(ev.launch)
            if c is not None and not wrappers[ev.launch]:
                report(c, _reap_outcome(ev.how), after())
        if isinstance(ev, Claim) and ev.launch is not None:
            claimants[ev.launch] = ev.claim
        if seq is not None:
            last = seq
    return Truth(_lineage(placed), tuple(placed))


def steps(history: History) -> dict[NodeId, int]:
    """Each native node's 0.3.0 step: the label of the iteration a commit
    finished, or the worker's clock at its exit. For diagnosis and for tests:
    0.4.0 records no step."""
    rendered = render(history)
    claim_seqs = {
        ev.claim: seq
        for ev, seq in zip(history.events, rendered.seqs)
        if isinstance(ev, Claim) and seq is not None
    }
    found: dict[NodeId, int] = {}
    for ref, step in rendered.walk.steps.items():
        if isinstance(ref, CommitOf):
            found[CommitId(claim_seqs[ref.claim], ref.nth)] = step
        elif isinstance(ref, ExitOf):
            found[ExitId(claim_seqs[ref.claim])] = step
        else:
            assert_never(ref)
    return found


def _native_honored(
    honored: list[str],
    exit_watermark: int,
    drains: list[int],
    stop_seqs: dict[str, int],
    withdrawn: dict[str, list[int]],
) -> tuple[str, ...]:
    """The stops the native exit honours: those its 0.3.0 ``stopped`` honoured,
    less any a withdrawal spent before the worker first drained it."""
    kept = []
    for request_id in honored:
        posted = stop_seqs[request_id]
        drained = min(d for d in [*drains, exit_watermark] if d >= posted)
        if not any(posted < s <= drained for s in withdrawn[request_id]):
            kept.append(request_id)
    return tuple(kept)


def _lineage(placed: list[Placed]) -> Lineage:
    records = [p.record for p in placed]
    return Lineage(
        starts=tuple(r for r in records if isinstance(r, Start)),
        commits=tuple(r for r in records if isinstance(r, Commit)),
        rewinds=tuple(r for r in records if isinstance(r, RewindTo)),
        exits=tuple(r for r in records if isinstance(r, ExitNode)),
        deaths=tuple(
            sorted(
                (r for r in records if isinstance(r, Died)),
                key=lambda d: (d.claim_seq, d.outcome.value),
            )
        ),
        terminations=tuple(r for r in records if isinstance(r, Terminated)),
        withdrawals=tuple(r for r in records if isinstance(r, Withdrawal)),
    )


# ----- §2's structural rules -------------------------------------------------------


def violations(t: Truth) -> tuple[str, ...]:
    """Every way ``t`` breaks §2's structural rules: a node follows what it
    names; a ``parent``, a ``resume_from`` and a rewind's target name an
    extendable node of the run, or are null; no seq is named twice along one
    lineage; at most one exit per claim. Empty when it is well formed."""
    found: list[str] = []
    where: dict[NodeId, tuple[int, int]] = {}
    extendable: set[NodeId] = set()
    parent: dict[NodeId, NodeId | None] = {}
    values: dict[NodeId, tuple[int, ...]] = {}
    exits_per_claim: dict[int, int] = defaultdict(int)
    for p in t.places:
        r = p.record
        if isinstance(r, Commit):
            where[r.node] = place_key(p.place)
            extendable.add(r.node)
            parent[r.node], values[r.node] = r.parent, r.values
        elif isinstance(r, ExitNode):
            where[r.node] = place_key(p.place)
            if r.kind is Kind.EXTENDABLE:
                extendable.add(r.node)
            parent[r.node], values[r.node] = r.parent, r.values or ()
            exits_per_claim[r.node.claim_seq] += 1

    def names_extendable(what: str, node: NodeId | None, key: tuple[int, int]) -> None:
        if node is None:
            return
        if node not in extendable:
            found.append(f"{what} names {node}, not an extendable node")
        elif where[node] >= key:
            found.append(f"{what} names {node}, which does not precede it")

    for p in t.places:
        r, key = p.record, place_key(p.place)
        if isinstance(r, (Commit, ExitNode)):
            for v in r.values or ():
                if v > key[0] or (v == key[0] and key[1] < 0):
                    found.append(f"{r.node} commits seq {v}, which follows it")
            names_extendable(f"{r.node}'s parent", r.parent, key)
        elif isinstance(r, Start):
            names_extendable(f"claim {r.claim_seq}'s resume_from", r.resume_from, key)
        elif isinstance(r, RewindTo):
            names_extendable(f"claim {r.claim_seq}'s rewind", r.target, key)
    for claim_seq, n in exits_per_claim.items():
        if n > 1:
            found.append(f"claim {claim_seq} has {n} exits")
    for node in values:
        seen: set[int] = set()
        walked: set[NodeId] = set()
        cursor: NodeId | None = node
        while cursor is not None and cursor in values:
            if cursor in walked:
                found.append(f"{node}'s lineage returns to {cursor}")
                break
            walked.add(cursor)
            for v in values[cursor]:
                if v in seen:
                    found.append(f"seq {v} is named twice along {node}'s lineage")
                seen.add(v)
            cursor = parent[cursor]
    return tuple(found)
