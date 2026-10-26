"""The candidate engine of the 0.3.0 -> 0.4.0 step (commits-and-lineage.md §7; the plan's Task 20).

Part of the step, under its import rule: from the package it takes only ``Row`` and ``MigrationError``, and its own
facts module. It carries its own reading of format 0.3.0's records and of §2-§4's lineage, written from the prose,
never from the simulator in ``tests/migration_sim``, which is the second encoding the oracle checks it against.

**What it computes.** For a run's 0.3.0 rows, the facts a consumer states per writer version, its classifier, and the
earlier step's readings, the set of conforming 0.4.0 lineages that record a 0.3.0 history writing those rows and
satisfying every statement and every classifier answer (§7, "The target"; "The writer facts"). An empty set refuses
the run, naming the facts whose relaxation admits a history. A set of one is the answer. Several are forks, reported
per claim in three projections and decided by choosers, last claim first.

**How.** A forward-backward pass over the log, one layer per record. A state is the world between two records, made
of the declared cross-claim state and each live worker's own:
- **the checkpoint's content** (``Slot``): none, a save no later claim loads as the newest, or the newest save, its
  step and the node it names, with whether a later claim has loaded it yet: **the promise**. A save is promised to a
  later claim when it is made, and a path that overwrites a promise before a claim keeps it dies;
- **older saves promised** to a later ``older`` load or rewind (``Promise``);
- **the workers still alive** (``Wk``): the latest claim's, and earlier ones that exited and write on, or that a
  release or a takeover displaced (fenced: §4, "A released worker fences itself"), each with its clock.

A worker's silent events (a pass beginning or closing, a save landing, its end) write no record. They are made
lazily, when the worker writes its next record, when a claim begins (the gate and the load read them), when a
release fences it, when a third party's ``stopped`` withdraws a stop its commit could have read, and at the log's
end, each within a bounded silent search (``_silent``). An edge carries the pieces of the lineage its record and
silent events decide (``Piece``); a lineage is the pieces of one complete path. The coupling between claims flows
only through the state, so the pass is linear in records times states, never a search across claims.

**What it does not read** (§7, "Encodings"): a worker's ``fold``, ``honored`` and ``watermark``, and a third
party's ``final-step`` and ``honored``, decide no output under E2; the engine neither uses nor checks them.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable, Hashable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, NamedTuple, TypeAlias, assert_never

from . import MigrationError, Row
from .v0_3_0_to_v0_4_0_facts import (
    Act,
    Acts,
    BeatPos,
    Born,
    Born020,
    Born030,
    ClaimState,
    Closing,
    Configuration,
    Covered,
    CutSave,
    Discards,
    Exited,
    FinalStep,
    Killed,
    Load,
    Loading,
    LogReader,
    LoopEnd,
    Marker,
    Note,
    Outcome,
    OwnStop,
    Phase,
    Reap,
    Release,
    Rewinds,
    Role,
    SaveAt,
    Saved,
    StopAnswer,
    Stray,
    Stride,
    Takeover,
    ThirdPartyFacts,
    ThirdStop,
    ValueRole,
    Via,
    WorkerFacts,
)

# ----- the records, as format 0.3.0 pins them ------------------------------------------------------------------------


class Topic(Enum):
    """The records the engine reads, by what they can be."""

    STARTED = "lifecycle.started"
    BEAT = "lifecycle.heartbeat"
    STOPPED = "lifecycle.stopped"
    VALUE = "value"
    STOP = "control.stop"
    LAUNCHED = "launcher.launched"
    TERMINATED = "launcher.terminated"
    OTHER = "other"  # a user's topic, a nak: no writer the lineage records


class Rec(NamedTuple):
    """One 0.3.0 record, read once. ``t`` is its writer's clock, else its ``created_at`` (§7, "Records")."""

    seq: int
    topic: Topic
    t: float
    step: int | None  # a heartbeat's or a value's
    claim: int | None  # a heartbeat's or a stopped's claim_seq
    request_id: str | None
    handle: str | None  # a started's or a launched's
    completed: bool
    error: str | None
    final_step: int | None
    honored: tuple[str, ...]
    malformed: bool  # a stopped in M10's {reason, honored, claim_seq} shape
    how: Exited | Killed | None  # a terminated's


def read(rows: Sequence[Row]) -> tuple[Rec, ...]:
    """The rows as records. The step's parse has accepted them (M9, and every body as 0.3.0 pins it), so a body's keys
    are there to read."""
    out: list[Rec] = []
    for r in rows:
        body: dict[str, Any] = json.loads(r.body)
        topic = next((t for t in Topic if t.value == r.topic), Topic.OTHER)
        t = body.get("t")
        when = (
            float(t)
            if isinstance(t, (int, float)) and not isinstance(t, bool)
            else r.created_at
        )
        step = body.get("step") if topic in (Topic.BEAT, Topic.VALUE) else None
        how: Exited | Killed | None = None
        if topic is Topic.TERMINATED:
            how = (
                Killed(body["signal"])
                if body["reason"] == "killed"
                else Exited(body["exit_code"])
            )
        malformed = topic is Topic.STOPPED and "completed" not in body
        out.append(
            Rec(
                r.seq,
                topic,
                when,
                step,
                body.get("claim_seq") if topic in (Topic.BEAT, Topic.STOPPED) else None,
                r.request_id,
                (
                    body.get("handle")
                    if topic in (Topic.STARTED, Topic.LAUNCHED)
                    else None
                ),
                bool(body.get("completed", False)),
                body.get("error") if topic is Topic.STOPPED else None,
                body.get("final_step") if topic is Topic.STOPPED else None,
                tuple(body.get("honored", ())) if topic is Topic.STOPPED else (),
                malformed,
                how,
            )
        )
    return tuple(out)


# ----- the lineage's names --------------------------------------------------------------------------------------------


class CommitId(NamedTuple):
    """A claim's ``nth`` commit (from 0), the claim named by its ``started``'s 0.3.0 seq."""

    claim: int
    nth: int


class ExitId(NamedTuple):
    """A claim's exit node: its worker's own ``stopped``."""

    claim: int


class OpenPass(NamedTuple):
    """What a save of an open pass's completed state names until the pass ends: its commit, the exit that keeps it, or
    nothing (§6, "Save"; ``cut-save``)."""

    claim: int


Node: TypeAlias = CommitId | ExitId
SaveNode: TypeAlias = Node | OpenPass | None


class Kind(Enum):
    """An exit node's kind (§2)."""

    CLOSED = "closed"
    TORN = "torn"
    EXTENDABLE = "extendable"


class Source(Enum):
    """E1: the 0.3.0 record a migrated death report stands for."""

    REAP = "reap"
    GATE = "gate"
    STOPPED = "stopped"


class Origin(Enum):
    """Where a claim's state came from: the Origin projection."""

    FRESH = "fresh"  # nothing, and nothing saved before
    RESTART = "restart"  # nothing, though a save existed
    NODE = "node"  # a save of the run's claims, which names a node or none
    OUTSIDE = "outside"  # a checkpoint no claim of the run saved


# ----- the pieces an edge decides -------------------------------------------------------------------------------------


class Undated(NamedTuple):
    """A commit no record dates: its end time is the end-time fork (§7). It stands before the record at ``before``:
    just before it (``exact``: a cut at that record), or anywhere after its claim's newest own record. Its lower bound
    is that record's ``t``; an upper bound is the ``t`` of the record after it, as its placement allows. Both read the
    path's pieces, which say whose records are whose (``lineage``)."""

    before: int
    exact: bool


class PStart(NamedTuple):
    """Origin: what the claim loaded, and its ``resume_from``."""

    claim: int
    origin: Origin
    step: int | None  # the loaded state's step
    resume: Node | None


class PCommit(NamedTuple):
    """Span: one finished iteration. Its values are the holds of its claim's ``epoch``."""

    claim: int
    nth: int
    epoch: int
    label: int
    parent: Node | None
    ended: Undated


class PDate(NamedTuple):
    """A commit dated by its own tick, which follows its close at once (§7, "The end-time fork")."""

    claim: int
    nth: int
    t: float


class PHold(NamedTuple):
    """Span: a value its claim's worker wrote as a metric, pending for the node of its ``epoch``."""

    seq: int
    claim: int
    epoch: int


class PValue(NamedTuple):
    """Span's value holds: who wrote the value, a claim's worker or none, in which role."""

    seq: int
    writer: int | None
    role: Role


class PBeat(NamedTuple):
    """A heartbeat, and the worker that wrote it: whose records are whose, for an undated commit's bounds."""

    seq: int
    writer: int


class PRewind(NamedTuple):
    claim: int
    commits: int
    epoch: int
    target: Node | None


class PExit(NamedTuple):
    """Span: the exit node, and the stops it honours (E2)."""

    claim: int
    seq: int
    epoch: int
    parent: Node | None
    kind: Kind
    discarded: bool
    honored: tuple[str, ...]


class PDied(NamedTuple):
    """End: a death report."""

    claim: int
    outcome: Outcome
    source: Source


class PTerminated(NamedTuple):
    launch: (
        str | None
    )  # None: a third party's report of a process's end, under a launch the step mints
    how: Exited | Killed


class PWithdrawal(NamedTuple):
    """E2: a ``control.withdraw`` just after its answer, the ``stopped`` at ``by``."""

    request_id: str
    by: int


class Author(Enum):
    """End: who wrote a ``stopped``, and what it was."""

    EXIT = "exit"  # its claim's worker's own: the exit node
    FENCED = (
        "fenced"  # its claim's worker's own, after a release fenced it: no node (§4)
    )
    THIRD = "third"  # a third party's


class PStop(NamedTuple):
    """End: one ``stopped``'s author, the claim it names or speaks for, and a third party's act."""

    seq: int
    claim: int | None
    author: Author
    act: Release | Note | Reap | None


class PFence(NamedTuple):
    """End: the fence point, where a release or a takeover reached a live worker."""

    claim: int
    at: int


class PRunEnd(NamedTuple):
    """Span: the claim's commits when its worker ended, its run-on included."""

    claim: int
    commits: int
    clock: int


Piece: TypeAlias = (
    PStart
    | PCommit
    | PDate
    | PHold
    | PValue
    | PBeat
    | PRewind
    | PExit
    | PDied
    | PTerminated
    | PWithdrawal
    | PStop
    | PFence
    | PRunEnd
)

# ----- the state ---------------------------------------------------------------------------------------------------------


class Stage(Enum):
    LOADING = "loading"  # claimed, no pass begun
    ITERATING = "iterating"  # a pass open
    BETWEEN = "between"  # its last pass closed
    EXITED = "exited"  # its own stopped written; it writes on


class Last(Enum):
    """The worker's last act, as its next one reads it: a stepped value of its open pass closes the pass when the
    close follows at once, and its role says whether it may (``closing``)."""

    OTHER = "other"
    EITHER = "either"  # a stepped value that may close the pass or not
    CLOSES = "closes"  # a closing value: only the close may follow
    STAYS = "stays"  # a value that is no closing one: the close may not follow


class Wk(NamedTuple):
    """One live worker: its claim's seq, its version's facts (an index into the context), and its program's state."""

    claim: int
    facts: int
    handle: str
    stage: Stage
    clock: int
    floor: int
    idle: (
        int | None
    )  # its clock before its first pass; None: its version's loading statement is empty
    held: int  # the step of the state it holds between passes
    state: SaveNode  # the node a save of that state names
    position: Node | None
    commits: int
    epoch: (
        int  # the position changes so far: its pending values go to this epoch's node
    )
    tick_due: bool
    landed: (
        bool | None
    )  # the open pass's save of its completed state: none, landed, or begun and never landed
    valued: bool
    ticked_in: bool
    acted: bool  # an act of the open pass since it began
    last: Last
    beaten: bool  # the last pass ticked
    saved: bool  # the last pass's completed state was saved
    begun: int | None
    finished: int | None
    ticked: int | None
    released: bool  # fenced: natively it writes nothing more
    kind: Kind | None  # its exit node's
    taken: frozenset[str]  # the stops its native drains took in
    dating: (
        int | None
    )  # the commit its pass just closed with, which a tick at once after dates


class Bare(Enum):
    """The checkpoint holds nothing, or a save no later claim loads as the newest (C's BLOCKED)."""

    EMPTY = "empty"
    UNLOADED = "unloaded"


class Newest(NamedTuple):
    """The newest save, promised to a later claim's ``newest`` load or rewind; ``loaded`` once one has."""

    step: int
    node: SaveNode
    loaded: bool


Slot: TypeAlias = Bare | Newest


class Promise(NamedTuple):
    """A save promised to a later ``older`` load or rewind; ``used`` once one has."""

    step: int
    node: SaveNode
    used: bool


class World(NamedTuple):
    workers: tuple[Wk, ...]  # in claim order
    latest: int  # the latest claim's seq; 0 before any
    released: frozenset[int]  # claims a release named
    ended: frozenset[int]  # claims an exit node or a death report ended (§2, ``ended``)
    nodes: frozenset[int]  # claims with an exit node
    exited: frozenset[int]  # claims whose worker wrote its own stopped
    slot: Slot
    older: frozenset[Promise]
    spent: frozenset[str]  # stops a withdrawal or an exit spent
    withdrawn: frozenset[str]


START = World(
    (),
    0,
    frozenset(),
    frozenset(),
    frozenset(),
    frozenset(),
    Bare.EMPTY,
    frozenset(),
    frozenset(),
    frozenset(),
)

Step: TypeAlias = tuple[World, tuple[Piece, ...]]


class Done(Enum):
    """The state after the log's end: every worker has ended."""

    DONE = "done"


# ----- the context: a log, its configuration, and what the pass looks ahead to ---------------------------------------


class Refused(MigrationError):
    """No history writes the log and meets the statements: ``names`` are the facts or classifier answers whose
    relaxation alone admits one, or the record the pass stopped at."""

    def __init__(self, message: str, names: tuple[str, ...]) -> None:
        super().__init__(message)
        self.names = names


class Unknown(MigrationError):
    """The classifier named a version the configuration has no facts for."""


class Ahead(NamedTuple):
    """What the records from one on could be of a worker's: values a role allows, its heartbeats and stopped."""

    teardown: frozenset[
        int | None
    ]  # labels (None: stepless) of values a teardown role allows
    worker: int  # the highest label of a value any worker role allows, -1 for none
    stepless: bool  # a stepless value a worker role allows
    beat: int  # the highest step of a heartbeat, -1 for none
    beacon: bool  # a stepless heartbeat
    stop: bool  # a stopped a worker may have written
    named: frozenset[int]  # claims a heartbeat or a worker's stopped names


@dataclass(frozen=True)
class Context:
    recs: tuple[Rec, ...]
    born: Born
    workers: tuple[WorkerFacts, ...]  # distinct statements
    versions: Mapping[int, tuple[int, ...]]  # claim seq -> statements its versions make
    stops: Mapping[int, frozenset[StopAnswer]]
    roles: Mapping[int, frozenset[ValueRole]]
    third: Mapping[Hashable, ThirdPartyFacts]
    posted: tuple[tuple[int, str], ...]  # (seq, id) of each control.stop
    launched: Mapping[str, str]  # launch id -> its process's handle
    claim_launch: Mapping[int, str | None]
    handles: Mapping[int, str]
    reused: Mapping[
        int, frozenset[str]
    ]  # per record index: handles a claim at or after it uses
    reaped: Mapping[
        int, frozenset[str]
    ]  # per record index: handles a terminated at or after it reaps
    newest_ahead: tuple[
        bool, ...
    ]  # per record index: a claim from it on, or a rewind, may take the newest save
    older_loads: tuple[
        int, ...
    ]  # per record index: the claims from it on, and rewinders, that may take an older one
    horizon: int  # labels above every step the log names: a run-on beyond it is reported as reaching it
    ahead: tuple[
        Ahead, ...
    ]  # per record index: what a worker might still write from it on
    by_seq: Mapping[int, int]


def context(
    rows: Sequence[Row], configuration: Configuration, *, born: Born
) -> Context:
    recs = read(rows)
    reader: LogReader = configuration.classifier.read(rows)
    statements: list[WorkerFacts] = []
    index: dict[WorkerFacts, int] = {}
    versions: dict[int, tuple[int, ...]] = {}
    stops: dict[int, frozenset[StopAnswer]] = {}
    roles: dict[int, frozenset[ValueRole]] = {}
    for r in recs:
        if r.topic is Topic.STARTED:
            found: list[int] = []
            for v in sorted(reader.started(r.seq), key=repr):
                if v not in configuration.workers:
                    raise Unknown(
                        f"the classifier names worker version {v!r} for the claim at seq {r.seq}, which "
                        "the configuration states no facts for"
                    )
                f = configuration.workers[v]
                found.append(index.setdefault(f, len(index)))
                if len(statements) < len(index):
                    statements.append(f)
            versions[r.seq] = tuple(sorted(set(found)))
        elif r.topic is Topic.STOPPED:
            answers = reader.stopped(r.seq)
            for a in answers:
                if (
                    isinstance(a, ThirdStop)
                    and a.writer not in configuration.third_parties
                ):
                    raise Unknown(
                        f"the classifier names third-party writer {a.writer!r} for the stopped at seq "
                        f"{r.seq}, which the configuration states no facts for"
                    )
            stops[r.seq] = answers
        elif r.topic is Topic.VALUE:
            roles[r.seq] = reader.value(r.seq)
    launched: dict[str, str] = {}
    for r in recs:
        if (
            r.topic is Topic.LAUNCHED
            and r.request_id is not None
            and r.handle is not None
        ):
            launched.setdefault(r.request_id, r.handle)
    claims = [r for r in recs if r.topic is Topic.STARTED]
    handles = {r.seq: r.handle or "" for r in claims}
    n = len(recs)
    reused: dict[int, frozenset[str]] = {}
    reaped: dict[int, frozenset[str]] = {}
    acc: set[str] = set()
    racc: set[str] = set()
    for i in range(n - 1, -1, -1):
        r = recs[i]
        if r.topic is Topic.STARTED:
            acc.add(r.handle or "")
        if r.topic is Topic.TERMINATED and r.request_id in launched:
            racc.add(launched[r.request_id])
        reused[i] = frozenset(acc)
        reaped[i] = frozenset(racc)
    # Who may take a save later: a claim's newest or older load, or a rewind. A save is promised to one only where one
    # may come, and the older promises outstanding never outnumber the loads that may keep them.
    rewinds = any(f.rewinds for f in statements)
    newest: list[bool] = [False] * (n + 1)
    loads: list[int] = [0] * (n + 1)
    for i in range(n - 1, -1, -1):
        r = recs[i]
        newest[i], loads[i] = newest[i + 1], loads[i + 1]
        if r.topic is Topic.STARTED:
            fs = [statements[k] for k in versions[r.seq]]
            newest[i] = newest[i] or any(Load.NEWEST in f.load for f in fs)
            loads[i] += any(Load.OLDER in f.load for f in fs)
    if rewinds:  # a rewind may take a save of its own claim's, any time
        newest = [True] * (n + 1)
        loads = [x + 1 for x in loads]
    labels = [r.step for r in recs if r.step is not None] + [
        r.final_step for r in recs if r.final_step is not None
    ]
    aheads: list[Ahead] = [
        Ahead(frozenset(), -1, False, -1, False, False, frozenset())
    ] * (n + 1)
    for i in range(n - 1, -1, -1):
        r, ah = recs[i], aheads[i + 1]
        if r.topic is Topic.VALUE:
            rs = roles[r.seq]
            if any(x.role is Role.TEARDOWN for x in rs):
                ah = ah._replace(teardown=ah.teardown | {r.step})
            if any(x.role is not Role.STRAY for x in rs):
                if r.step is None:
                    ah = ah._replace(stepless=True)
                else:
                    ah = ah._replace(worker=max(ah.worker, r.step))
        elif r.topic is Topic.BEAT:
            if r.step is None:
                ah = ah._replace(beacon=True)
            else:
                ah = ah._replace(beat=max(ah.beat, r.step))
            ah = ah._replace(named=ah.named | {r.claim or 0})
        elif r.topic is Topic.STOPPED and any(
            isinstance(x, OwnStop) for x in stops[r.seq]
        ):
            ah = ah._replace(stop=True, named=ah.named | {r.claim or 0})
        aheads[i] = ah
    return Context(
        recs,
        born,
        tuple(statements),
        versions,
        stops,
        roles,
        dict(configuration.third_parties),
        tuple(
            (r.seq, r.request_id)
            for r in recs
            if r.topic is Topic.STOP and r.request_id is not None
        ),
        launched,
        {r.seq: r.request_id for r in claims},
        handles,
        reused,
        reaped,
        tuple(newest),
        tuple(loads),
        max(labels, default=0) + 3,
        tuple(aheads),
        {r.seq: i for i, r in enumerate(recs)},
    )


# ----- the facts' checks -------------------------------------------------------------------------------------------------


def _wf(ctx: Context, w: Wk) -> WorkerFacts:
    return ctx.workers[w.facts]


def _posted_before(ctx: Context, seq: int) -> frozenset[str]:
    return frozenset(x for s, x in ctx.posted if s < seq)


# ----- a worker's silent events ------------------------------------------------------------------------------------------


class Run(NamedTuple):
    """A silent run's end: the worker (None once it has ended), the world's checkpoint after its saves, and the pieces
    its commits and rewinds decide."""

    w: Wk | None
    slot: Slot
    older: frozenset[Promise]
    pieces: tuple[Piece, ...]


def _resolve(
    slot: Slot, older: frozenset[Promise], claim: int, node: Node | None
) -> tuple[Slot, frozenset[Promise]]:
    """An open pass ends: a save of its completed state now names ``node``."""
    pending = OpenPass(claim)
    if isinstance(slot, Newest) and slot.node == pending:
        slot = slot._replace(node=node)
    if any(p.node == pending for p in older):
        older = frozenset(
            p._replace(node=node) if p.node == pending else p for p in older
        )
    return slot, older


def _saves(
    ctx: Context,
    i: int,
    slot: Slot,
    older: frozenset[Promise],
    step: int,
    node: SaveNode,
) -> Iterator[tuple[Slot, frozenset[Promise]]]:
    """A save lands: it is the newest. It is promised to a later claim's ``newest`` load, or to none; and it may be
    promised to a later ``older`` load. A promise not yet kept cannot be overwritten, unless by the same state.
    """
    if (
        isinstance(slot, Newest)
        and not slot.loaded
        and (slot.step, slot.node) != (step, node)
    ):
        return
    keep = isinstance(slot, Newest) and (slot.step, slot.node) == (step, node)
    tops: list[Slot] = [Bare.UNLOADED]
    if keep:
        tops = [slot]
    elif ctx.newest_ahead[i]:
        tops.append(Newest(step, node, False))
    olds = [older]
    unused = sum(1 for p in older if not p.used)
    if unused < ctx.older_loads[i] and Promise(step, node, False) not in older:
        olds.append(older | {Promise(step, node, False)})
    for top in tops:
        for o in olds:
            yield top, o


def _cut(
    ctx: Context,
    w: Wk,
    slot: Slot,
    older: frozenset[Promise],
    before: int,
    exact: bool,
    *,
    own_exit: bool,
) -> Iterator[tuple[Wk, Slot, frozenset[Promise], tuple[Piece, ...]]]:
    """The open pass ends other than by its close (a death, a fence, its own stopped): did its iteration finish?
    ``cut-save`` says, where it saved its completed state (§7). A commit it makes stands at the cut, dated there.
    """
    if w.stage is not Stage.ITERATING or w.released or w.landed is None:
        yield w, slot, older, ()
        return
    finishes = {CutSave.SAVE_START} | ({CutSave.LANDED} if w.landed else set())
    stated = _wf(ctx, w).cut_save
    if finishes & stated:
        node = CommitId(w.claim, w.commits)
        piece = PCommit(
            w.claim, w.commits, w.epoch, w.clock, w.position, Undated(before, exact)
        )
        s2, o2 = _resolve(slot, older, w.claim, node)
        w2 = w._replace(
            commits=w.commits + 1,
            epoch=w.epoch + 1,
            position=node,
            state=node,
            held=w.clock,
            dating=None,
            landed=None,
            finished=w.clock,
        )
        yield w2, s2, o2, (piece,)
    if (set(CutSave) - finishes) & stated:
        if own_exit:
            # the exit decides: one that keeps the open pass names it (resolved there)
            yield w._replace(landed=None if not w.landed else w.landed), slot, older, ()
        else:
            s2, o2 = _resolve(slot, older, w.claim, None)
            yield w._replace(landed=None), s2, o2, ()


def _silent(
    ctx: Context, i: int, world: World, w: Wk, *, bound: int, may_end: bool, before: int
) -> list[Run]:
    """Every state the worker reaches by silent events alone, with labels at most ``bound``: passes beginning and
    closing, saves, rewinds, and (``may_end``) its end, all before the record at ``before``, which bounds where its
    commits stand, for their end times. Each run's saves land in order; only the checkpoint they leave is kept.
    """
    out: dict[Run, None] = {}
    f = _wf(ctx, w)
    seen: set[tuple[Wk, Slot, frozenset[Promise], tuple[Piece, ...], int, int]] = set()

    def visit(
        x: Wk,
        slot: Slot,
        older: frozenset[Promise],
        pieces: tuple[Piece, ...],
        saves: int,
        rewound: int,
    ) -> None:
        key = (x, slot, older, pieces, saves, rewound)
        if key in seen:
            return
        seen.add(key)
        out[Run(x, slot, older, pieces)] = None
        acted = x._replace(dating=None, acted=True)
        if x.stage is Stage.ITERATING:
            # the close
            if x.last is not Last.STAYS:
                at_value = x.last in (Last.EITHER, Last.CLOSES)
                if (Closing.AT_VALUE if at_value else Closing.OTHER) in f.closing and (
                    at_value
                    or x.ticked_in
                    or (Marker.RECORDED if x.valued else Marker.SILENT) in f.marker
                ):
                    for saved in (False, True):
                        if saved and SaveAt.AT_CLOSE not in f.save_at:
                            continue
                        closed(x, slot, older, pieces, saved)
            if x.last is not Last.CLOSES:
                # a save of its completed state, inside the pass
                if x.landed is None and SaveAt.IN_PASS in f.save_at:
                    for landed in (True, False):
                        y = acted._replace(
                            landed=landed, last=Last.OTHER, saved=x.saved or landed
                        )
                        if landed:
                            node: SaveNode = None if x.released else OpenPass(x.claim)
                            for s2, o2 in _saves(ctx, i, slot, older, x.clock, node):
                                visit(y, s2, o2, pieces, saves + 1, rewound)
                        else:
                            visit(y, slot, older, pieces, saves, rewound)
                # a save of its partial state
                if SaveAt.PARTIAL in f.save_at and saves < 2:
                    for s2, o2 in _saves(ctx, i, slot, older, x.clock, None):
                        visit(
                            acted._replace(last=Last.OTHER),
                            s2,
                            o2,
                            pieces,
                            saves + 1,
                            rewound,
                        )
        elif x.stage in (Stage.LOADING, Stage.BETWEEN):
            for label in range(x.floor, bound + 1):
                stride = Stride.NEXT if label == x.floor else Stride.SKIP
                if stride not in f.stride:
                    continue
                if x.stage is Stage.BETWEEN and (
                    (Covered.BEATEN if x.beaten else Covered.UNBEATEN) not in f.beat_cov
                    or (Saved.SAVED if x.saved else Saved.UNSAVED) not in f.save_cov
                ):
                    break
                y = x._replace(
                    stage=Stage.ITERATING,
                    clock=label,
                    tick_due=True,
                    begun=label,
                    landed=None,
                    valued=False,
                    ticked_in=False,
                    acted=False,
                    last=Last.OTHER,
                    beaten=False,
                    saved=False,
                    dating=None,
                )
                visit(y, slot, older, pieces, 0, rewound)
            # a save of the state it holds
            where = SaveAt.BEFORE_LOOP if x.stage is Stage.LOADING else SaveAt.BETWEEN
            if where in f.save_at and saves < 2:
                for s2, o2 in _saves(ctx, i, slot, older, x.held, x.state):
                    visit(
                        acted._replace(
                            saved=x.saved or x.stage is Stage.BETWEEN, acted=x.acted
                        ),
                        s2,
                        o2,
                        pieces,
                        saves + 1,
                        rewound,
                    )
            # a rewind, between passes
            if x.stage is Stage.BETWEEN and rewound == 0 and f.rewinds:
                for target, step, node, s2, o2 in _rewinds(f, slot, older):
                    at = 0 if step is None else step
                    y = acted._replace(
                        clock=at,
                        floor=at + 1 if step is not None else 0,
                        held=at,
                        state=node,
                        tick_due=False,
                        acted=x.acted,
                    )
                    tgt: Node | None = (
                        node if isinstance(node, (CommitId, ExitId)) else None
                    )
                    new: tuple[Piece, ...] = ()
                    if step is None:
                        y = y._replace(clock=0, floor=0, held=0)
                    if not x.released:
                        new = (PRewind(x.claim, x.commits, x.epoch, tgt),)
                        y = y._replace(position=tgt, epoch=x.epoch + 1)
                    visit(y, s2, o2, pieces + new, saves, rewound + 1)
        elif x.stage is Stage.EXITED:
            if SaveAt.AFTER_EXIT in f.save_at and saves < 2:
                node2: SaveNode = (
                    ExitId(x.claim)
                    if x.kind is Kind.EXTENDABLE and not x.released
                    else None
                )
                for s2, o2 in _saves(ctx, i, slot, older, x.held, node2):
                    visit(x, s2, o2, pieces, saves + 1, rewound)
        # a worker its own stopped or a fence ended halts only right after an act (``_owing``)
        if may_end and (not _owing(x) or x != w or pieces or saves or rewound):
            ended(x, slot, older, pieces)

    def closed(
        x: Wk,
        slot: Slot,
        older: frozenset[Promise],
        pieces: tuple[Piece, ...],
        saved: bool,
    ) -> None:
        y = x._replace(
            stage=Stage.BETWEEN,
            floor=x.clock + 1,
            finished=x.clock,
            held=x.clock,
            last=Last.OTHER,
            acted=True,
            saved=x.saved or saved or bool(x.landed),
            landed=None,
        )
        if x.released:
            y = y._replace(state=None)
            s2, o2 = _resolve(slot, older, x.claim, None)
            if saved:
                for s3, o3 in _saves(ctx, i, s2, o2, x.clock, None):
                    visit(y, s3, o3, pieces, 2, 0)
            else:
                visit(y, s2, o2, pieces, 0, 0)
            return
        node = CommitId(x.claim, x.commits)
        piece = PCommit(
            x.claim, x.commits, x.epoch, x.clock, x.position, Undated(before, False)
        )
        y = y._replace(
            position=node,
            state=node,
            commits=x.commits + 1,
            epoch=x.epoch + 1,
            dating=x.commits,
        )
        s2, o2 = _resolve(slot, older, x.claim, node)
        if saved:
            for s3, o3 in _saves(ctx, i, s2, o2, x.clock, node):
                visit(y, s3, o3, pieces + (piece,), 1, 0)
        else:
            visit(y, s2, o2, pieces + (piece,), 0, 0)

    def ended(
        x: Wk, slot: Slot, older: frozenset[Promise], pieces: tuple[Piece, ...]
    ) -> None:
        if x.last is Last.CLOSES:
            return  # a closing value is followed by its close
        for y, s2, o2, cut in _cut(ctx, x, slot, older, before, False, own_exit=False):
            final = (PRunEnd(y.claim, y.commits, y.clock),) if not x.released else ()
            out[Run(None, s2, o2, pieces + cut + final)] = None

    visit(w, world.slot, world.older, (), 0, 0)
    return list(out)


def _rewinds(
    f: WorkerFacts, slot: Slot, older: frozenset[Promise]
) -> Iterator[tuple[Rewinds, int | None, SaveNode, Slot, frozenset[Promise]]]:
    """A rewind's targets: the root, the newest save (its promise kept), or an older one promised to it."""
    if Rewinds.ROOT in f.rewinds:
        yield Rewinds.ROOT, None, None, slot, older
    if Rewinds.NEWEST in f.rewinds and isinstance(slot, Newest):
        yield Rewinds.NEWEST, slot.step, slot.node, slot._replace(loaded=True), older
    if Rewinds.OLDER in f.rewinds:
        for p in older:
            if isinstance(slot, Newest) and (slot.step, slot.node) == (p.step, p.node):
                continue
            yield Rewinds.OLDER, p.step, p.node, slot, (older - {p}) | {
                p._replace(used=True)
            }


# ----- the records ----------------------------------------------------------------------------------------------------


def _owing(w: Wk) -> bool:
    """A worker whose claim its own stopped or a fence ended. Nothing the lineage records, nor any fact, tells one
    that lives on without acting from one that has halted, so it halts right after its last act: it lives on only by
    owing another act, and ends at none but such a moment."""
    return w.stage is Stage.EXITED or w.released


def _acted(ctx: Context, i: int, world: World, w: Wk) -> Iterator[tuple[Wk, ...]]:
    """After an act of ``w`` at record ``i``: it lives on, and an owing worker halts at once, or lives on only where
    a later record could be its or a later load could take a save it makes (``_may_act``).
    """
    if not _owing(w) or _may_act(ctx, i + 1, w):
        yield _put(world, w, w.claim)
    if _owing(w):
        yield _put(world, None, w.claim)


def _may_act(ctx: Context, i: int, w: Wk) -> bool:
    """Whether an owing worker could still act from record ``i`` on: write a record, or save what a later load may
    take. A superset: it reads roles and labels, never who else may have written the record.
    """
    f = _wf(ctx, w)
    if (ctx.newest_ahead[i] or ctx.older_loads[i]) and (
        (w.stage is Stage.EXITED and SaveAt.AFTER_EXIT in f.save_at)
        or (w.stage is not Stage.EXITED and f.save_at)
    ):
        return True
    a = ctx.ahead[i]
    if w.stage is Stage.EXITED:
        return None in a.teardown or w.clock in a.teardown
    if a.stepless or a.worker >= min(
        w.clock, w.idle if w.idle is not None else w.clock
    ):
        return True
    if isinstance(ctx.born, Born030):
        return w.claim in a.named
    return a.beacon or a.beat >= w.clock or a.stop


def _put(world: World, w: Wk | None, claim: int) -> tuple[Wk, ...]:
    """The world's workers with claim ``claim``'s replaced by ``w``, or removed (None)."""
    out = [x for x in world.workers if x.claim != claim]
    if w is not None:
        out.append(w)
        out.sort(key=lambda x: x.claim)
    return tuple(out)


def _with(world: World, run: Run, claim: int) -> World:
    return world._replace(
        workers=_put(world, run.w, claim), slot=run.slot, older=run.older
    )


def _drain(ctx: Context, world: World, w: Wk, seq: int) -> Wk:
    """A native drain at a record or a commit before ``seq``: it takes in every stop posted before it that nothing has
    spent (§4; E2)."""
    if w.released:
        return w
    new = _posted_before(ctx, seq) - world.spent - w.taken
    return w._replace(taken=w.taken | new) if new else w


def _writers(ctx: Context, world: World, rec: Rec) -> Iterable[Wk]:
    """The live workers that may have written a heartbeat or a worker's stopped: the claim it names, in a log born in
    0.3.0; any, in one born in 0.2.0, whose names are the earlier step's positional inference (§7).
    """
    if isinstance(ctx.born, Born030):
        return [w for w in world.workers if w.claim == rec.claim]
    return world.workers


def _materialized(
    ctx: Context, i: int, world: World, w: Wk, rec: Rec, *, bound: int, may_end: bool
) -> list[Run]:
    """Worker ``w``'s silent events before record ``rec``: from its newest own record, and placed before ``rec``."""
    return _silent(ctx, i, world, w, bound=bound, may_end=may_end, before=rec.seq)


def _value(ctx: Context, i: int, world: World, rec: Rec) -> Iterator[Step]:
    """A value: a live worker's, in the role the classifier allows and its phase gives, or a stray's."""
    roles = ctx.roles[rec.seq]
    if any(r.role is Role.STRAY for r in roles):
        if world.latest == 0:
            yield world, (PValue(rec.seq, None, Role.STRAY),)
        else:
            latest = next((w for w in world.workers if w.claim == world.latest), None)
            kinds = _latest_facts(ctx, world)
            cls = Stray.STEPLESS if rec.step is None else Stray.STEPPED
            if any(cls in ctx.workers[k].strays for k in kinds):
                del latest
                yield world, (PValue(rec.seq, None, Role.STRAY),)
    for w in world.workers:
        bound = rec.step if rec.step is not None else ctx.horizon
        for run in _materialized(ctx, i, world, w, rec, bound=bound, may_end=False):
            x = run.w
            assert x is not None
            if x.dating is not None:
                x = x._replace(dating=None)
            for step in _emit(ctx, x, rec, roles):
                y, pieces = step
                after = world._replace(slot=run.slot, older=run.older)
                for workers in _acted(ctx, i, after, y):
                    yield after._replace(workers=workers), run.pieces + pieces


def _latest_facts(ctx: Context, world: World) -> tuple[int, ...]:
    """The statements the latest claim's version may make. Its worker may have ended, so the context keeps them."""
    return ctx.versions.get(world.latest, ())


_ROLE_STAGE = {
    Role.LOADING: Stage.LOADING,
    Role.PASS: Stage.ITERATING,
    Role.CLOSING: Stage.ITERATING,
    Role.BETWEEN: Stage.BETWEEN,
    Role.TEARDOWN: Stage.EXITED,
}
_STAGE_PHASE = {
    Stage.LOADING: Phase.LOADING,
    Stage.ITERATING: Phase.PASS,
    Stage.BETWEEN: Phase.BETWEEN,
    Stage.EXITED: Phase.TEARDOWN,
}


def _emit(
    ctx: Context, w: Wk, rec: Rec, roles: frozenset[ValueRole]
) -> Iterator[tuple[Wk, tuple[Piece, ...]]]:
    """Worker ``w`` writes the value ``rec`` now."""
    f = _wf(ctx, w)
    if w.last is Last.CLOSES:
        return
    label = w.idle if w.stage is Stage.LOADING else w.clock
    stepped = rec.step is not None
    if stepped and (label is None or rec.step != label):
        return
    if stepped and _STAGE_PHASE[w.stage] not in f.values:
        return
    allowed = {
        r for r in roles if r.role is not Role.STRAY and _ROLE_STAGE[r.role] is w.stage
    }
    if not stepped:
        allowed = {r for r in allowed if r.register}
    for register in sorted({r.register for r in allowed}):
        if not stepped and not register:
            continue
        rs = {r.role for r in allowed if r.register == register}
        last = Last.OTHER
        role = next(iter(rs))
        if w.stage is Stage.ITERATING and stepped:
            closing, passing = Role.CLOSING in rs, Role.PASS in rs
            last = (
                Last.EITHER
                if closing and passing
                else (Last.CLOSES if closing else Last.STAYS)
            )
            role = Role.CLOSING if closing and not passing else Role.PASS
        y = w._replace(
            last=last,
            acted=w.acted or w.stage is Stage.ITERATING,
            valued=w.valued or (stepped and w.stage is Stage.ITERATING),
            dating=None,
        )
        pieces: tuple[Piece, ...] = (PValue(rec.seq, w.claim, role),)
        if not register and not w.released and w.stage is not Stage.EXITED:
            pieces += (PHold(rec.seq, w.claim, w.epoch),)
        yield y, pieces


def _beat(ctx: Context, i: int, world: World, rec: Rec) -> Iterator[Step]:
    """A heartbeat: with a step, a tick of its pass; without, a beacon. Either is a drain of a native worker."""
    for w in _writers(ctx, world, rec):
        bound = rec.step if rec.step is not None else ctx.horizon
        for run in _materialized(ctx, i, world, w, rec, bound=bound, may_end=False):
            x = run.w
            assert x is not None
            if x.last is Last.CLOSES:
                continue
            f = _wf(ctx, x)
            pieces = run.pieces
            if rec.step is None:
                if x.stage is Stage.EXITED:
                    continue
                y = x._replace(
                    last=Last.OTHER,
                    acted=x.acted or x.stage is Stage.ITERATING,
                    dating=None,
                )
            else:
                if (
                    x.stage not in (Stage.ITERATING, Stage.BETWEEN)
                    or not x.tick_due
                    or rec.step != x.clock
                ):
                    continue
                pos = (
                    BeatPos.AFTER_CLOSE
                    if x.stage is Stage.BETWEEN
                    else (BeatPos.INSIDE if x.acted else BeatPos.OPENING)
                )
                if pos not in f.beat_pos:
                    continue
                if x.dating is not None and not x.released:
                    pieces += (PDate(x.claim, x.dating, rec.t),)
                y = x._replace(
                    tick_due=False,
                    ticked=x.clock,
                    beaten=True,
                    ticked_in=x.ticked_in or x.stage is Stage.ITERATING,
                    acted=x.acted or x.stage is Stage.ITERATING,
                    last=Last.OTHER,
                    dating=None,
                )
            y = _drain(ctx, world, y, rec.seq)
            after = world._replace(slot=run.slot, older=run.older)
            for workers in _acted(ctx, i, after, y):
                yield after._replace(workers=workers), pieces + (
                    PBeat(rec.seq, w.claim),
                )


def _final(w: Wk, convention: FinalStep) -> int | None:
    if convention is FinalStep.YIELDED:
        return w.begun
    if convention is FinalStep.FINISHED:
        return w.finished
    if convention is FinalStep.TICKED:
        return w.ticked
    if convention is FinalStep.COUNTER:
        return w.idle if w.finished is None else w.finished
    assert_never(convention)


def _own_stop(ctx: Context, i: int, world: World, rec: Rec) -> Iterator[Step]:
    """A worker's own stopped: its exit node, or, once a release fenced it, no node and its withdrawals (E2)."""
    for w in _writers(ctx, world, rec):
        for run in _materialized(
            ctx, i, world, w, rec, bound=ctx.horizon, may_end=False
        ):
            x = run.w
            assert x is not None
            if x.stage is Stage.EXITED or x.last is Last.CLOSES:
                continue
            yield from _exit(ctx, i, world, run, x, rec)


def _exit(
    ctx: Context, i: int, world: World, run: Run, x: Wk, rec: Rec
) -> Iterator[Step]:
    f = _wf(ctx, x)
    clean = rec.error is None
    inside = x.stage is Stage.ITERATING
    stated = f.final_step if clean else f.error_final_step
    if not any(_final(x, c) == rec.final_step for c in stated):
        return
    for y, slot, older, cut in _cut(
        ctx, x, run.slot, run.older, rec.seq, True, own_exit=True
    ):
        committed = bool(cut)
        # a clean exit's elements: where it left its loop, whether it kept an open pass, whether it saved
        options: list[bool] = [False]
        if clean:
            if (LoopEnd.IN_PASS if inside else LoopEnd.AFTER_CLOSE) not in f.loop_end:
                continue
            if (
                x.stage is Stage.BETWEEN
                and (Saved.SAVED if x.saved else Saved.UNSAVED) not in f.leave_save
            ):
                continue
            if inside and not committed:
                options = [
                    d
                    for d in (False, True)
                    if (Discards.DISCARDS if d else Discards.KEEPS) in f.discards
                ]
        if not any(_final(y, c) == rec.final_step for c in stated) and not committed:
            continue
        for discards in options:
            z = y._replace(stage=Stage.EXITED, last=Last.OTHER, dating=None)
            pieces = run.pieces + cut
            nworld = world._replace(exited=world.exited | {x.claim})
            if x.released:
                z = z._replace(landed=None)
                pieces += (PStop(rec.seq, x.claim, Author.FENCED, None),)
                nworld, wd = _withdraw(nworld, rec)
                pieces += wd
                after = nworld._replace(slot=slot, older=older)
                for workers in _acted(ctx, i, after, z):
                    yield after._replace(workers=workers), pieces
                continue
            kind = (
                Kind.CLOSED
                if rec.completed
                else (Kind.TORN if not clean or discards else Kind.EXTENDABLE)
            )
            discarded = not clean or discards
            z = _drain(ctx, world, z, rec.seq)
            keeps = inside and clean and not discards and not committed
            node: Node | None = ExitId(x.claim) if kind is Kind.EXTENDABLE else None
            if inside and not committed and y.landed is not None:
                slot, older = _resolve(slot, older, x.claim, node)
            z = z._replace(kind=kind, landed=None, held=x.clock if keeps else z.held)
            honored = tuple(sorted(z.taken))
            pieces += (
                PStop(rec.seq, x.claim, Author.EXIT, None),
                PExit(x.claim, rec.seq, z.epoch, z.position, kind, discarded, honored),
            )
            nworld = nworld._replace(
                spent=world.spent | z.taken,
                nodes=world.nodes | {x.claim},
                ended=world.ended | {x.claim},
                exited=world.exited | {x.claim},
            )
            after = nworld._replace(slot=slot, older=older)
            for workers in _acted(ctx, i, after, z):
                yield after._replace(workers=workers), pieces


def _withdraw(world: World, rec: Rec) -> tuple[World, tuple[Piece, ...]]:
    """E2: a withdrawal for each id a stopped that is no exit node honours, unless already withdrawn."""
    ids = [x for x in rec.honored if x not in world.withdrawn]
    if not ids:
        return world, ()
    pieces = tuple(PWithdrawal(x, rec.seq) for x in dict.fromkeys(ids))
    return (
        world._replace(
            withdrawn=world.withdrawn | set(ids), spent=world.spent | set(ids)
        ),
        pieces,
    )


def _state(
    ctx: Context, world: World, n: int | None
) -> Iterator[tuple[ClaimState, World, tuple[Piece, ...]]]:
    """The state of the claim a third party's stopped names: its worker alive or dead, or its claim ended. A live
    worker may end silently before the stop, which makes it dead."""
    if n is None:
        yield ClaimState.UNNAMED, world, ()
        return
    if n in world.nodes:
        yield ClaimState.EXITED, world, ()
        return
    if n in world.exited or n in world.released or n < world.latest:
        yield ClaimState.ENDED, world, ()
        return
    w = next((x for x in world.workers if x.claim == n), None)
    if w is None:
        yield ClaimState.DEAD, world, ()
        return
    yield ClaimState.LIVE, world, ()


def _third(
    ctx: Context, i: int, world: World, rec: Rec, answer: ThirdStop
) -> Iterator[Step]:
    """A third party's stopped (§7, "The third-party facts"): its act on the claim it names, and its withdrawals."""
    tp = ctx.third[answer.writer]
    act = answer.act
    for prior, before in _ended_before(ctx, i, world, rec):
        for state, w0, extra in _state(ctx, prior, rec.claim):
            kind = (
                Act.RELEASE
                if isinstance(act, Release)
                else (Act.NOTE if isinstance(act, Note) else Act.REAP)
            )
            if Acts(state, kind) not in tp.acts:
                continue
            pieces = before + extra + (PStop(rec.seq, rec.claim, Author.THIRD, act),)
            nw = w0
            if isinstance(act, Release):
                if rec.claim is None or act.outcome not in tp.outcome:
                    continue
                n = rec.claim
                vias = list(tp.via) if state is ClaimState.EXITED else [Via.ALWAYS]
                for via in vias:
                    yield from _released(ctx, i, nw, rec, n, act.outcome, via, pieces)
                continue
            if isinstance(act, Reap):
                pieces += (PTerminated(None, act.how),)
            nw, wd = _withdraw(nw, rec)
            yield nw, pieces + wd


def _ended_before(
    ctx: Context, i: int, world: World, rec: Rec
) -> Iterator[tuple[World, tuple[Piece, ...]]]:
    """Before a third party's stopped: the named claim's worker may end silently (so the stop finds it dead), and a
    worker whose commit the stop's withdrawals would come too late for may close its pass first (E2).
    """
    yield world, ()
    for w in world.workers:
        named = w.claim == rec.claim and w.kind is None and not w.released
        drains = not w.released and any(
            x not in world.spent and x not in w.taken for x in rec.honored
        )
        if not (named or drains):
            continue
        for run in _materialized(
            ctx, i, world, w, rec, bound=ctx.horizon, may_end=named
        ):
            if run.w == w and not run.pieces:
                continue
            x = run.w
            if x is not None:
                x = (
                    _drain(ctx, world, x, rec.seq)
                    if any(isinstance(p, PCommit) for p in run.pieces)
                    else x
                )
            yield world._replace(
                workers=_put(world, x, w.claim), slot=run.slot, older=run.older
            ), run.pieces


def _released(
    ctx: Context,
    i: int,
    world: World,
    rec: Rec,
    n: int,
    outcome: Outcome,
    via: Via,
    pieces: tuple[Piece, ...],
) -> Iterator[Step]:
    """A release of claim ``n``: it fences a live worker with no exit node, there (§4), and is a death report unless
    §3 holds it back: through the helper on a claim with a readable exit, and without a cause on one that has ended.
    """
    w = next((x for x in world.workers if x.claim == n), None)
    report = not (via is Via.IF_HELD and n in world.nodes) and not (
        outcome is Outcome.LOST and n in world.ended
    )
    nworld = world._replace(released=world.released | {n})
    out = pieces
    if report:
        out += (PDied(n, outcome, Source.STOPPED),)
        nworld = nworld._replace(ended=nworld.ended | {n})
    if w is not None and w.kind is None and not w.released:
        for y, slot, older, cut in _cut(
            ctx, w, world.slot, world.older, rec.seq, True, own_exit=False
        ):
            z = y._replace(released=True, dating=None)
            fw2, wd = _withdraw(nworld._replace(slot=slot, older=older), rec)
            for workers in _acted(ctx, i, fw2, z):
                yield fw2._replace(workers=workers), out + cut + (
                    PFence(n, rec.seq),
                ) + wd
        return
    fw2, wd = _withdraw(nworld, rec)
    yield fw2, out + wd


def _stopped(ctx: Context, i: int, world: World, rec: Rec) -> Iterator[Step]:
    for answer in ctx.stops[rec.seq]:
        if isinstance(answer, OwnStop):
            if rec.malformed:
                continue  # no worker writes a body 0.3.0 does not define
            yield from _own_stop(ctx, i, world, rec)
        elif isinstance(answer, ThirdStop):
            yield from _third(ctx, i, world, rec, answer)
        else:
            assert_never(answer)


def _ends(
    ctx: Context, i: int, world: World, rec: Rec, keep: Callable[[Wk], bool]
) -> Iterator[tuple[World, tuple[Piece, ...]]]:
    """Every live worker's silent events up to ``rec``, where the claim begins or a process is reaped: each may save,
    end, or neither (``keep`` says which must end). The worker that saves last leaves the checkpoint.
    """
    states: list[tuple[World, tuple[Piece, ...]]] = [(world, ())]
    for w in world.workers:
        nxt: list[tuple[World, tuple[Piece, ...]]] = []
        for wd, pieces in states:
            cur = next(x for x in wd.workers if x.claim == w.claim)
            must_end = not keep(cur)
            if not must_end:
                nxt.append((wd, pieces))
            for run in _materialized(
                ctx, i, wd, cur, rec, bound=ctx.horizon, may_end=True
            ):
                if run.w is not None and (
                    must_end
                    or (run.w == cur and not run.pieces and run.slot == wd.slot)
                ):
                    continue
                nxt.append((_with(wd, run, w.claim), pieces + run.pieces))
        states = list(dict.fromkeys(nxt))
    yield from states


def _claim(ctx: Context, i: int, world: World, rec: Rec) -> Iterator[Step]:
    """A claim: the gate on the latest one, its death report where nothing ended it (§4, "The claim"), and the new
    worker's load."""
    later = ctx.reused[i]  # handles a claim from here on uses, this one's included

    def must_end(w: Wk) -> bool:
        return False

    for wd, pieces in _ends(ctx, i, world, rec, lambda w: True):
        del must_end
        yield from _gate(ctx, i, wd, rec, pieces, later)
        must_end = lambda w: False  # noqa: E731


def _gate(
    ctx: Context,
    i: int,
    world: World,
    rec: Rec,
    pieces: tuple[Piece, ...],
    later: frozenset[str],
) -> Iterator[Step]:
    p = world.latest
    out = pieces
    nworld = world
    if p:
        w = next((x for x in world.workers if x.claim == p), None)
        free = p in world.exited or p in world.released
        kinds = ctx.versions[p]
        if w is None:
            # ended silently: its process ended unless a claim from here on runs in it
            if not free and ctx.handles[p] in later:
                if not any(
                    Takeover.DEAD in ctx.workers[k].takeover
                    for k in _facts_of(world, p, kinds)
                ):
                    return
        elif not free:
            # held, its worker alive: a takeover, whose report fences it (§4)
            if Takeover.LIVE not in ctx.workers[w.facts].takeover:
                return
            done = False
            for y, slot, older, cut in _cut(
                ctx, w, world.slot, world.older, rec.seq, True, own_exit=False
            ):
                done = True
                z = y._replace(released=True, dating=None)
                fw = world._replace(slot=slot, older=older)
                for workers in _acted(ctx, i, fw, z):
                    yield from _gate_report(
                        ctx,
                        i,
                        fw._replace(workers=workers),
                        rec,
                        out + cut + (PFence(p, rec.seq),),
                        p,
                    )
            if not done:
                return
            return
        yield from _gate_report(ctx, i, nworld, rec, out, p)
        return
    yield from _load(ctx, i, nworld, rec, out)


def _facts_of(world: World, claim: int, kinds: tuple[int, ...]) -> tuple[int, ...]:
    return kinds


def _gate_report(
    ctx: Context, i: int, world: World, rec: Rec, pieces: tuple[Piece, ...], p: int
) -> Iterator[Step]:
    if p not in world.ended:
        pieces += (PDied(p, Outcome.LOST, Source.GATE),)
        world = world._replace(ended=world.ended | {p})
    yield from _load(ctx, i, world, rec, pieces)


def _load(
    ctx: Context, i: int, world: World, rec: Rec, pieces: tuple[Piece, ...]
) -> Iterator[Step]:
    """The new claim's load, by its version's ``load`` (§7): nothing, the newest save, an older one, or a checkpoint
    from outside."""
    c = rec.seq
    for k in ctx.versions[c]:
        f = ctx.workers[k]
        loads: list[tuple[Origin, int | None, SaveNode, Slot, frozenset[Promise]]] = []
        saved = world.slot is not Bare.EMPTY
        if Load.FRESH in f.load and not saved:
            loads.append((Origin.FRESH, None, None, world.slot, world.older))
        if Load.RESTART in f.load and saved:
            loads.append((Origin.RESTART, None, None, world.slot, world.older))
        if Load.NEWEST in f.load and isinstance(world.slot, Newest):
            loads.append(
                (
                    Origin.NODE,
                    world.slot.step,
                    world.slot.node,
                    world.slot._replace(loaded=True),
                    world.older,
                )
            )
        if Load.OLDER in f.load:
            for pr in world.older:
                if isinstance(world.slot, Newest) and (
                    world.slot.step,
                    world.slot.node,
                ) == (pr.step, pr.node):
                    continue
                loads.append(
                    (
                        Origin.NODE,
                        pr.step,
                        pr.node,
                        world.slot,
                        (world.older - {pr}) | {pr._replace(used=True)},
                    )
                )
        if Load.OUTSIDE in f.load:
            for at in range(0, ctx.horizon + 1):
                loads.append((Origin.OUTSIDE, at, None, world.slot, world.older))
        for origin, step, node, slot, older in loads:
            if isinstance(node, OpenPass):
                continue
            loaded = 0 if step is None else step
            idles: list[int | None] = []
            if loaded == 0:
                idles = [0] if f.loading else [None]
            else:
                if Loading.LOADED in f.loading:
                    idles.append(loaded)
                if Loading.ZERO in f.loading:
                    idles.append(0)
                if not idles:
                    idles = [None]
            for idle in idles:
                w = Wk(
                    c,
                    k,
                    rec.handle or "",
                    Stage.LOADING,
                    idle if idle is not None else loaded,
                    0 if step is None else step + 1,
                    idle,
                    loaded,
                    node,
                    node if not isinstance(node, OpenPass) else None,
                    0,
                    0,
                    False,
                    None,
                    False,
                    False,
                    False,
                    Last.OTHER,
                    False,
                    False,
                    None,
                    None,
                    None,
                    False,
                    None,
                    frozenset(),
                    None,
                )
                nworld = world._replace(
                    workers=_put(world, w, c), latest=c, slot=slot, older=older
                )
                resume = node if isinstance(node, (CommitId, ExitId)) else None
                yield nworld, pieces + (PStart(c, origin, step, resume),)


def _reap(ctx: Context, i: int, world: World, rec: Rec) -> Iterator[Step]:
    """A launcher's reap: every worker in its process has ended, so it ends here if it has not (design-v0.2.md §8).
    A death report for the newest claim naming the launch, when that claim runs in its process and nothing else ended
    it (§3; E1)."""
    launch = rec.request_id
    handle = ctx.launched.get(launch or "")
    how = rec.how
    assert how is not None
    later_claims = ctx.reused[i + 1] if i + 1 < len(ctx.recs) else frozenset()
    if handle is not None and handle in later_claims:
        return  # a claim after it runs in a process that has ended
    for wd, pieces in _ends(ctx, i, world, rec, lambda w: w.handle != handle):
        out = pieces + (PTerminated(launch, how),)
        newest = _newest_naming(ctx, wd, launch)
        nw = wd
        if newest is not None and handle is not None and ctx.handles[newest] == handle:
            outcome = (
                Outcome.KILLED
                if isinstance(how, Killed)
                else (Outcome.ERRORED if how.code else Outcome.LOST)
            )
            if newest not in wd.nodes and not (
                outcome is Outcome.LOST and newest in wd.ended
            ):
                out += (PDied(newest, outcome, Source.REAP),)
                nw = wd._replace(ended=wd.ended | {newest})
        yield nw, out


def _newest_naming(ctx: Context, world: World, launch: str | None) -> int | None:
    found = [
        c
        for c, lid in ctx.claim_launch.items()
        if lid == launch and c <= world.latest and lid is not None
    ]
    return max(found) if found else None


def _record(ctx: Context, i: int, world: World) -> Iterator[Step]:
    rec = ctx.recs[i]
    if rec.topic is Topic.STARTED:
        yield from _claim(ctx, i, world, rec)
    elif rec.topic is Topic.VALUE:
        yield from _value(ctx, i, world, rec)
    elif rec.topic is Topic.BEAT:
        yield from _beat(ctx, i, world, rec)
    elif rec.topic is Topic.STOPPED:
        yield from _stopped(ctx, i, world, rec)
    elif rec.topic is Topic.TERMINATED:
        yield from _reap(ctx, i, world, rec)
    elif rec.topic in (Topic.STOP, Topic.LAUNCHED, Topic.OTHER):
        yield world, ()
    else:
        assert_never(rec.topic)


def _end(ctx: Context, world: World) -> Iterator[tuple[tuple[Piece, ...]]]:
    """The log's end: every worker ends, and every promise has been kept."""
    n = len(ctx.recs)
    end = Rec(
        ctx.recs[-1].seq + 1 if ctx.recs else 1,
        Topic.OTHER,
        0.0,
        None,
        None,
        None,
        None,
        False,
        None,
        None,
        (),
        False,
        None,
    )
    states: list[tuple[World, tuple[Piece, ...]]] = [(world, ())]
    for w in world.workers:
        nxt: list[tuple[World, tuple[Piece, ...]]] = []
        for wd, pieces in states:
            cur = next(x for x in wd.workers if x.claim == w.claim)
            for run in _silent(
                ctx, n, wd, cur, bound=ctx.horizon, may_end=True, before=end.seq
            ):
                if run.w is not None:
                    continue
                nxt.append((_with(wd, run, w.claim), pieces + run.pieces))
        states = list(dict.fromkeys(nxt))
    for wd, pieces in states:
        if isinstance(wd.slot, Newest) and not wd.slot.loaded:
            continue
        if any(not p.used for p in wd.older):
            continue
        yield (pieces,)
    del end


# ----- the graph ---------------------------------------------------------------------------------------------------------


class Edge(NamedTuple):
    src: int
    dst: int
    pieces: tuple[Piece, ...]


@dataclass
class Graph:
    """The pass's layered graph: ``layers[i]`` are the states before record ``i``; ``edges[i]`` lead from layer ``i``
    to layer ``i + 1``, and the last ``edges`` entry to the end (one state, 0). ``live[i]`` are the edges on some
    complete path."""

    ctx: Context
    layers: list[dict[World, int]]
    edges: list[list[Edge]]
    live: list[list[Edge]]
    stopped_at: int | None  # the record index no state could take, if any
    widest: int  # the most states in one layer


def build(ctx: Context, *, cap: int) -> Graph:
    """The forward pass, then the backward one. ``cap`` bounds the states in a layer; past it the run is refused as
    too many, naming the record."""
    layers: list[dict[World, int]] = [{START: 0}]
    edges: list[list[Edge]] = []
    widest = 1
    for i in range(len(ctx.recs)):
        nxt: dict[World, int] = {}
        es: dict[Edge, None] = {}
        for world, sid in layers[-1].items():
            for w2, pieces in _record(ctx, i, world):
                did = nxt.setdefault(w2, len(nxt))
                es[Edge(sid, did, pieces)] = None
        if len(nxt) > cap:
            raise Refused(
                f"more than {cap} states at the record at seq {ctx.recs[i].seq}: the stated facts leave too many "
                "histories to follow",
                (f"seq {ctx.recs[i].seq}",),
            )
        widest = max(widest, len(nxt))
        layers.append(nxt)
        edges.append(list(es))
        if not nxt:
            return Graph(ctx, layers, edges, [[] for _ in edges], i, widest)
    final: dict[Edge, None] = {}
    for world, sid in layers[-1].items():
        for (pieces,) in _end(ctx, world):
            final[Edge(sid, 0, pieces)] = None
    edges.append(list(final))
    layers.append({START: 0} if final else {})
    live: list[list[Edge]] = [[] for _ in edges]
    alive: set[int] = {0} if final else set()
    for i in range(len(edges) - 1, -1, -1):
        live[i] = [e for e in edges[i] if e.dst in alive]
        alive = {e.src for e in live[i]}
    stopped = None if final else len(ctx.recs)
    return Graph(ctx, layers, edges, live, stopped, widest)


def admits(g: Graph) -> bool:
    return bool(g.live) and bool(g.live[-1])


# ----- the lineage a path writes -----------------------------------------------------------------------------------------


class Commit(NamedTuple):
    node: CommitId
    parent: Node | None
    values: tuple[int, ...]
    t: float


class ExitNode(NamedTuple):
    node: ExitId
    parent: Node | None
    values: tuple[int, ...] | None
    kind: Kind
    honored: tuple[str, ...]


class Start(NamedTuple):
    claim: int
    resume_from: Node | None


class RewindTo(NamedTuple):
    claim: int
    commits: int
    target: Node | None


class Died(NamedTuple):
    claim: int
    outcome: Outcome
    source: Source


class Lineage(NamedTuple):
    """One candidate: the records the step writes, by name. Commits' ``t`` is chosen by ``when``."""

    starts: tuple[Start, ...]
    commits: tuple[Commit, ...]
    rewinds: tuple[RewindTo, ...]
    exits: tuple[ExitNode, ...]
    deaths: tuple[Died, ...]
    terminations: tuple[PTerminated, ...]
    withdrawals: tuple[PWithdrawal, ...]


class EndTime(Enum):
    """The end-time fork's generic choosers (§7)."""

    LOWER = "lower"  # the claim's newest own record's t at or before the commit
    UPPER = "upper"  # the t of the next record after it


def _own(ps: Iterable[Piece]) -> dict[int, list[int]]:
    """Each claim's own records on a path, by seq: its started, and the values, heartbeats and stopped its worker
    wrote."""
    own: dict[int, list[int]] = defaultdict(list)
    for p in ps:
        if isinstance(p, PStart):
            own[p.claim].append(p.claim)
        elif isinstance(p, PValue) and p.writer is not None:
            own[p.writer].append(p.seq)
        elif isinstance(p, PBeat):
            own[p.writer].append(p.seq)
        elif (
            isinstance(p, PStop)
            and p.author is not Author.THIRD
            and p.claim is not None
        ):
            own[p.claim].append(p.seq)
    for v in own.values():
        v.sort()
    return own


def bounds(
    ctx: Context, own: Sequence[int], ended: Undated
) -> tuple[float, tuple[float, ...]]:
    """An undated commit's lower bound, and its upper bounds in placement order."""
    last = max(s for s in own if s < ended.before)
    lower = ctx.recs[ctx.by_seq[last]].t
    if ended.exact:
        return lower, (ctx.recs[ctx.by_seq[ended.before]].t,)
    return lower, tuple(r.t for r in ctx.recs if last < r.seq <= ended.before)


def times(ctx: Context, pieces: Sequence[Piece]) -> dict[CommitId, frozenset[float]]:
    """Every end time each commit of a path may have: its tick's, or the end-time fork's bounds."""
    own = _own(pieces)
    dated = {(p.claim, p.nth): p.t for p in pieces if isinstance(p, PDate)}
    out: dict[CommitId, frozenset[float]] = {}
    for p in pieces:
        if isinstance(p, PCommit):
            t = dated.get((p.claim, p.nth))
            if t is not None:
                out[CommitId(p.claim, p.nth)] = frozenset({t})
            else:
                lower, uppers = bounds(ctx, own[p.claim], p.ended)
                out[CommitId(p.claim, p.nth)] = frozenset({lower, *uppers})
    return out


def lineage(ctx: Context, pieces: Iterable[Piece], *, when: EndTime) -> Lineage:
    """The lineage of one path's pieces."""
    ps = list(pieces)
    own = _own(ps)
    holds: dict[tuple[int, int], list[int]] = defaultdict(list)
    dated: dict[tuple[int, int], float] = {}
    for p in ps:
        if isinstance(p, PHold):
            holds[(p.claim, p.epoch)].append(p.seq)
        elif isinstance(p, PDate):
            dated[(p.claim, p.nth)] = p.t
    commits: list[Commit] = []
    exits: list[ExitNode] = []
    for p in ps:
        if isinstance(p, PCommit):
            t = dated.get((p.claim, p.nth))
            if t is None:
                lower, uppers = bounds(ctx, own[p.claim], p.ended)
                t = lower if when is EndTime.LOWER or not uppers else uppers[0]
            commits.append(
                Commit(
                    CommitId(p.claim, p.nth),
                    p.parent,
                    tuple(sorted(holds[(p.claim, p.epoch)])),
                    t,
                )
            )
        elif isinstance(p, PExit):
            values = None if p.discarded else tuple(sorted(holds[(p.claim, p.epoch)]))
            exits.append(ExitNode(ExitId(p.claim), p.parent, values, p.kind, p.honored))
    return Lineage(
        tuple(sorted(Start(p.claim, p.resume) for p in ps if isinstance(p, PStart))),
        tuple(sorted(commits)),
        tuple(
            sorted(
                RewindTo(p.claim, p.commits, p.target)
                for p in ps
                if isinstance(p, PRewind)
            )
        ),
        tuple(sorted(exits)),
        tuple(
            sorted(
                (
                    Died(p.claim, p.outcome, p.source)
                    for p in ps
                    if isinstance(p, PDied)
                ),
                key=lambda d: (d.claim, d.outcome.value, d.source.value),
            )
        ),
        tuple(p for p in ps if isinstance(p, PTerminated)),
        tuple(p for p in ps if isinstance(p, PWithdrawal)),
    )


# ----- paths -------------------------------------------------------------------------------------------------------------


def paths(g: Graph, *, limit: int) -> Iterator[tuple[Piece, ...]]:
    """The complete paths' pieces, up to ``limit`` of them: for small logs and the oracle."""
    by_src: list[dict[int, list[Edge]]] = []
    for es in g.live:
        m: dict[int, list[Edge]] = defaultdict(list)
        for e in es:
            m[e.src].append(e)
        by_src.append(m)
    count = 0
    stack: list[tuple[int, int, tuple[Piece, ...]]] = [(0, 0, ())]
    while stack:
        i, sid, acc = stack.pop()
        if i == len(by_src):
            yield acc
            count += 1
            if count >= limit:
                return
            continue
        for e in by_src[i].get(sid, []):
            stack.append((i + 1, e.dst, acc + e.pieces))


def candidates(
    rows: Sequence[Row], configuration: Configuration, *, born: Born, cap: int
) -> Graph:
    """The candidate set of one run's rows: the pass's graph, whose complete paths are the candidates."""
    return build(context(rows, configuration, born=born), cap=cap)


class TooMany(MigrationError):
    """More distinct candidates than the cap asked for."""


def chains(g: Graph, *, cap: int) -> list[tuple[Piece, ...]]:
    """Every complete path's pieces, one per distinct sequence: a forward pass over the live edges that keeps, per
    state, the distinct piece sequences reaching it. Raises ``TooMany`` past ``cap`` sequences at a state.
    """
    cells: dict[tuple[tuple[Piece, ...], int], int] = {}
    back: list[tuple[tuple[Piece, ...], int]] = [((), -1)]
    at: dict[int, set[int]] = {0: {0}}
    for es in g.live:
        nxt: dict[int, set[int]] = defaultdict(set)
        for e in es:
            for c in at.get(e.src, ()):
                if e.pieces:
                    key = (e.pieces, c)
                    if key not in cells:
                        cells[key] = len(back)
                        back.append(key)
                    nxt[e.dst].add(cells[key])
                else:
                    nxt[e.dst].add(c)
        for sid, cs in nxt.items():
            if len(cs) > cap:
                raise TooMany(f"more than {cap} distinct paths reach one state")
        at = nxt
    out: list[tuple[Piece, ...]] = []
    for c in at.get(0, ()):
        parts: list[tuple[Piece, ...]] = []
        while c > 0:
            ps, c = back[c]
            parts.append(ps)
        out.append(tuple(p for ps in reversed(parts) for p in ps))
    return out
