"""The candidate engine of the 0.3.0 -> 0.4.0 step (commits-and-lineage.md §7; the
plan's Task 20).

Part of the step, under its import rule: from the package it takes only ``Row`` and
``MigrationError``, and its own facts module. It carries its own reading of format
0.3.0's records and of §2-§4's lineage, written from the prose, never from the simulator
in ``tests/migration_sim``, which is the second encoding the oracle checks it against.

**What it computes.** For one run's 0.3.0 rows, the facts a consumer states per writer
version, its classifier, and the earlier step's readings, the set of conforming 0.4.0
lineages that record a 0.3.0 history writing those rows and meeting every statement and
every classifier answer (§7, "The target"; "The writer facts"). An empty set refuses the
run. A set of one is the answer. Several are forks, reported per claim in three
projections (End, Span, Origin) and decided by choosers, from the last claim back.

**How: a forward-backward pass over the log, one layer per record.** A state is the
world between two records:
- **the checkpoint's content** (``slot``): the set of saves that may be the newest, each
  with its step and the node it names, and whether there may be none. It is the step's
  interval of the approved architecture: a save a writer may make or skip is never a
  branch, so histories that differ only in which of their passes saved last share one
  state, and a later load or rewind chooses among them;
- **the older saves promised** to a later ``older`` load or rewind (``older``): the
  promise;
- **the workers still alive** (``workers``): the latest claim's, and earlier ones that
  exited and write on, or that a release or a takeover displaced, which natively write
  nothing more (§4, "A released worker fences itself");
- what the gate, the verdict and E2 read: the claims ended, released or exited, and the
  stops spent.

A worker's silent events (a pass beginning or closing, its end) write no record. They
are made lazily, within a bounded search (``_silent``): when the worker writes its next
record, when a claim begins (the gate and the load read them), when a release or a
takeover fences it, when a third party's ``stopped`` withdraws a stop a commit could
have read first (E2), and at the log's end. An edge carries the pieces of the lineage
its record and silent events decide (``Piece``); a lineage is one complete path's
pieces. The coupling between claims flows only through the state: the pass is linear in
records times states, never a search across claims.

**What it does not read** (§7, "Encodings"): a worker's ``fold``, ``honored`` and
``watermark``, and a third party's ``final-step`` and ``honored``, which decide no
output under E2.
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

# ----- the records, as format 0.3.0 pins them -----------------------------------------


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
    """One 0.3.0 record, read once. ``t`` is its writer's clock, else its ``created_at``
    (§7, "Records")."""

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


_TOPICS = {t.value: t for t in Topic}


def read(rows: Sequence[Row]) -> tuple[Rec, ...]:
    """The rows as records. The step's parse has accepted them (M9, and every body as
    0.3.0 pins it), so a body's keys are there to read."""
    out: list[Rec] = []
    for r in rows:
        body: dict[str, Any] = json.loads(r.body)
        topic = _TOPICS.get(r.topic, Topic.OTHER)
        t = body.get("t")
        when = (
            float(t)
            if isinstance(t, (int, float)) and not isinstance(t, bool)
            else r.created_at
        )
        stepped = topic in (Topic.BEAT, Topic.VALUE)
        named = topic in (Topic.BEAT, Topic.STOPPED)
        stopped = topic is Topic.STOPPED
        malformed = stopped and "completed" not in body
        whole = stopped and not malformed
        how: Exited | Killed | None = None
        if topic is Topic.TERMINATED:
            how = (
                Killed(body["signal"])
                if body["reason"] == "killed"
                else Exited(body["exit_code"])
            )
        out.append(
            Rec(
                r.seq,
                topic,
                when,
                body["step"] if stepped else None,
                body["claim_seq"] if named else None,
                r.request_id,
                body["handle"] if topic in (Topic.STARTED, Topic.LAUNCHED) else None,
                body["completed"] if whole else False,
                body["error"] if whole else None,
                body["final_step"] if whole else None,
                tuple(body["honored"]) if stopped else (),
                malformed,
                how,
            )
        )
    return tuple(out)


# ----- the lineage's names ------------------------------------------------------------


class CommitId(NamedTuple):
    """A claim's ``nth`` commit (from 0), the claim named by its ``started``'s 0.3.0
    seq."""

    claim: int
    nth: int


class ExitId(NamedTuple):
    """A claim's exit node: its worker's own ``stopped``."""

    claim: int


Node: TypeAlias = CommitId | ExitId


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


# ----- the pieces an edge decides -----------------------------------------------------


class Undated(NamedTuple):
    """Where a commit stands, for its end time when no record dates it (§7, "The
    end-time fork"): before the record at ``before``, just before it (``exact``: a cut
    there) or anywhere after its claim's newest own record. Its lower bound is that
    record's ``t``; an upper bound is the ``t`` of the record after it. Both read the
    path's pieces, which say whose records are whose (``bounds``)."""

    before: int
    exact: bool


class PStart(NamedTuple):
    """Origin: what the claim loaded, and its ``resume_from``."""

    claim: int
    origin: Origin
    step: int | None  # the loaded state's step
    resume: Node | None


class PCommit(NamedTuple):
    """Span: one finished iteration. Its values are the holds of its claim's ``epoch``.
    Its old step is no part of it: 0.4.0's commit records none, and a value records its
    own, so iterations that differ only in their labels write one piece."""

    claim: int
    nth: int
    epoch: int
    parent: Node | None
    ended: Undated


class PDate(NamedTuple):
    """A commit dated by its own tick, which follows its close at once (§7, "The
    end-time fork")."""

    claim: int
    nth: int
    t: float


class PHold(NamedTuple):
    """Span: a value its claim's worker wrote as a metric, pending for the node of its
    ``epoch``."""

    seq: int
    claim: int
    epoch: int


class PValue(NamedTuple):
    """Span's value holds: who wrote the value, a claim's worker or none, in which
    role."""

    seq: int
    writer: int | None
    role: Role


class PBeat(NamedTuple):
    """A heartbeat, and the worker that wrote it."""

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
    # None: a third party's report of a process's end, under a launch the step mints
    launch: str | None
    how: Exited | Killed


class PWithdrawal(NamedTuple):
    """E2: a ``control.withdraw`` just after its answer, the ``stopped`` at ``by``."""

    request_id: str
    by: int


class Author(Enum):
    """End: who wrote a ``stopped``, and what it was. ``FENCED`` is derived, never a
    reading (the critique's P8)."""

    EXIT = "exit"  # its claim's worker's own: the exit node
    # its writer's own, after a release or a takeover fenced it: no node (§4)
    FENCED = "fenced"
    THIRD = "third"  # a third party's


class PStop(NamedTuple):
    """End: one ``stopped``'s author, and the claim it speaks for (its writer's, or the
    one a third party names)."""

    seq: int
    claim: int | None
    author: Author
    act: Release | Note | Reap | None


class PFence(NamedTuple):
    """End: the fence point, where a release or a takeover reached a live worker."""

    claim: int
    at: int


class PRunEnd(NamedTuple):
    """Span: the claim's commits when its worker ended, its run-on included, and its
    clock then."""

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

# ----- the state ----------------------------------------------------------------------


class Stage(Enum):
    LOADING = "loading"  # claimed, no pass begun
    ITERATING = "iterating"  # a pass open
    BETWEEN = "between"  # its last pass closed
    EXITED = "exited"  # its own stopped written; it writes on


class Last(Enum):
    """The worker's last act, as its next one reads it: a stepped value of its open pass
    closes the pass when the close follows at once, and the value's role says whether it
    may (``closing``)."""

    OTHER = "other"
    EITHER = "either"  # a stepped value that may close the pass or not
    CLOSES = "closes"  # a closing value: only the close may follow
    STAYS = "stays"  # a value that is no closing one: the close may not follow


class Held(NamedTuple):
    """A save that may be the newest: the step of the state it holds and the node it
    names (§6), and, to keep what a stated save rules out, the claim whose worker made
    it and its passes begun then."""

    step: int
    node: Node | None
    by: int
    gen: int


NOTHING = Held(-1, None, 0, -1)
"""No save at all: the checkpoint may hold nothing."""

SOME = Held(-2, None, 0, -2)
"""A save whose identity nothing after reads: no later claim or rewind takes the newest
save (C's BLOCKED)."""


class Promise(NamedTuple):
    """A save promised to a later ``older`` load or rewind; ``used`` once one has."""

    step: int
    node: Node | None
    used: bool


class Wk(NamedTuple):
    """One live worker: its claim's seq, its version's statement (an index into the
    context), and its program's state."""

    claim: int
    facts: int
    handle: str
    stage: Stage
    clock: int
    floor: int
    # its clock before its first pass; None: its version's loading statement is empty
    idle: int | None
    held: int  # the step of the state it holds between passes
    state: Node | None  # the node a save of that state names
    position: Node | None
    commits: int
    # the position changes so far: its pending values go to this epoch's node
    epoch: int
    passes: int  # the passes it has begun
    tick_due: bool
    valued: bool
    ticked_in: bool
    acted: bool  # an act of the open pass since it began
    # it began a save of the open pass's completed state: its only silent act there
    saving: bool
    last: Last
    beaten: bool  # the last pass ticked
    # a save of the last closed pass's completed state, where its version may make one
    closed: Held | None
    begun: int | None
    finished: int | None
    ticked: int | None
    released: bool  # fenced: natively it writes nothing more
    kind: Kind | None  # its exit node's
    taken: frozenset[str]  # the stops its native drains took in
    # the commit its pass just closed with, which a tick at once after dates
    dating: int | None
    # the claims whose workers were alive beside it when it claimed
    peers: frozenset[int]


class World(NamedTuple):
    workers: tuple[Wk, ...]  # in claim order
    latest: int  # the latest claim's seq; 0 before any
    released: frozenset[int]  # claims a release named
    ended: frozenset[int]  # claims an exit node or a death report ended (§2, ``ended``)
    nodes: frozenset[int]  # claims with an exit node
    exited: frozenset[int]  # claims whose worker wrote its own stopped
    slot: frozenset[Held]  # the saves that may be the newest
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
    frozenset({NOTHING}),
    frozenset(),
    frozenset(),
    frozenset(),
)

Step: TypeAlias = tuple[World, tuple[Piece, ...]]

# ----- the context: a log, its configuration, and what the pass looks ahead to --------


class Refused(MigrationError):
    """The engine cannot answer the run: ``names`` say why, a fact, a classifier answer,
    or the record it stopped at."""

    def __init__(self, message: str, names: tuple[str, ...]) -> None:
        super().__init__(message)
        self.names = names


class Unknown(MigrationError):
    """The classifier named a version the configuration has no facts for."""


class Ahead(NamedTuple):
    """What the records from one on could be of a worker's: values a role allows, its
    heartbeats and stopped."""

    teardown: frozenset[int | None]  # labels (None: stepless) a teardown role allows
    worker: int  # the highest label of a value a worker's role allows, -1 for none
    stepless: bool  # a stepless value a worker's role allows
    beat: int  # the highest step of a heartbeat, -1 for none
    beacon: bool  # a stepless heartbeat
    stop: bool  # a stopped a worker may have written
    named: frozenset[int]  # claims a heartbeat or a worker's stopped names


@dataclass(frozen=True)
class Context:
    recs: tuple[Rec, ...]
    born: Born
    workers: tuple[WorkerFacts, ...]  # distinct statements
    # claim seq -> the statements its versions make
    versions: Mapping[int, tuple[int, ...]]
    stops: Mapping[int, frozenset[StopAnswer]]
    roles: Mapping[int, frozenset[ValueRole]]
    third: Mapping[Hashable, ThirdPartyFacts]
    posted: tuple[tuple[int, str], ...]  # (seq, id) of each control.stop
    launched: Mapping[str, str]  # launch id -> its process's handle
    claim_launch: Mapping[int, str | None]
    handles: Mapping[int, str]
    # per record index: handles a claim at or after it runs in
    reused: Mapping[int, frozenset[str]]
    # per record index: a claim from it on, or a rewind, may take the newest save
    newest_ahead: tuple[bool, ...]
    # per record index: the loads from it on, and rewinders, that may take an older one
    older_loads: tuple[int, ...]
    # per record index: what a worker might still write from it on
    ahead: tuple[Ahead, ...]
    # per record index: no record from it on can read a clock or a save's step
    blind: tuple[bool, ...]
    # per record index: the claims a record from it on may ask about: a stopped's, a
    # reap's
    asked: tuple[frozenset[int], ...]
    # the highest label a run-on is followed to: past every step the log names, by two a
    # claim. A run-on no record pins is unbounded; its fork is reported as reaching it.
    horizon: int
    by_seq: Mapping[int, int]
    # the states the pass may hold at once, in a layer or in one worker's silent run:
    # past it the run is refused, naming the record
    cap: int


def context(
    rows: Sequence[Row], configuration: Configuration, *, born: Born, cap: int
) -> Context:
    """One log, read once, with its classifier's answers and what the pass looks ahead
    to."""
    recs = read(rows)
    reader: LogReader = configuration.classifier.read(rows)
    statements: list[WorkerFacts] = []
    index: dict[WorkerFacts, int] = {}
    versions: dict[int, tuple[int, ...]] = {}
    stops: dict[int, frozenset[StopAnswer]] = {}
    roles: dict[int, frozenset[ValueRole]] = {}
    for r in recs:
        if r.topic is Topic.STARTED:
            found: set[int] = set()
            for v in reader.started(r.seq):
                if v not in configuration.workers:
                    raise Unknown(
                        f"the classifier names worker version {v!r} for the claim at "
                        f"seq {r.seq}, which the configuration states no facts for"
                    )
                f = configuration.workers[v]
                if f not in index:
                    index[f] = len(statements)
                    statements.append(f)
                found.add(index[f])
            versions[r.seq] = tuple(sorted(found))
        elif r.topic is Topic.STOPPED:
            answers = reader.stopped(r.seq)
            for a in answers:
                if (
                    isinstance(a, ThirdStop)
                    and a.writer not in configuration.third_parties
                ):
                    raise Unknown(
                        f"the classifier names third-party writer {a.writer!r} for the "
                        f"stopped at seq {r.seq}, which the configuration states no "
                        "facts for"
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
    n = len(recs)
    # Who may take a save later: a newest or older load, or a rewind, which may take a
    # save of its own claim's at any time. A save's identity is kept only where one may
    # come; older promises never outnumber the loads ahead.
    rewinds = any(f.rewinds for f in statements)
    newest = [rewinds] * (n + 1)
    loads = [int(rewinds)] * (n + 1)
    reused: dict[int, frozenset[str]] = {n: frozenset()}
    for i in range(n - 1, -1, -1):
        r = recs[i]
        newest[i], loads[i], reused[i] = newest[i + 1], loads[i + 1], reused[i + 1]
        if r.topic is Topic.STARTED:
            fs = [statements[k] for k in versions[r.seq]]
            newest[i] = newest[i] or any(Load.NEWEST in f.load for f in fs)
            loads[i] += any(Load.OLDER in f.load for f in fs)
            reused[i] = reused[i] | {r.handle or ""}
    aheads = [Ahead(frozenset(), -1, False, -1, False, False, frozenset())] * (n + 1)
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
            assert r.claim is not None  # a heartbeat names its claim (lifecycle-v0.5)
            ah = ah._replace(named=ah.named | {r.claim})
        elif r.topic is Topic.STOPPED and any(
            isinstance(x, OwnStop) for x in stops[r.seq]
        ):
            # a stopped names its claim, or none (a claimless backstop)
            claimed = frozenset() if r.claim is None else frozenset({r.claim})
            ah = ah._replace(stop=True, named=ah.named | claimed)
        aheads[i] = ah
    labels = [r.step for r in recs if r.step is not None]
    labels += [r.final_step for r in recs if r.final_step is not None]
    # Where no record from one on can read a clock, no step is observable: a loading
    # value under a zero clock reads 0 whatever was loaded, and nothing else with a step
    # or a final_step follows.
    asked: list[frozenset[int]] = [frozenset()] * (n + 1)
    for i in range(n - 1, -1, -1):
        r = recs[i]
        asked[i] = asked[i + 1]
        if r.topic is Topic.STOPPED and r.claim is not None:
            asked[i] = asked[i] | {r.claim}
        elif r.topic is Topic.TERMINATED:
            asked[i] = asked[i] | {
                c.seq for c in claims if c.request_id == r.request_id
            }
    zero = all(f.loading <= {Loading.ZERO} for f in statements)
    blind = [True] * (n + 1)
    for i in range(n - 1, -1, -1):
        r = recs[i]
        loading_only = r.topic is Topic.VALUE and all(
            x.role in (Role.LOADING, Role.STRAY) for x in roles[r.seq]
        )
        reads = (
            r.step is not None and not (zero and loading_only and r.step == 0)
        ) or r.final_step is not None
        blind[i] = blind[i + 1] and not reads
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
        {r.seq: r.handle or "" for r in claims},
        reused,
        tuple(newest),
        tuple(loads),
        tuple(aheads),
        tuple(blind),
        tuple(asked),
        max(labels, default=0) + 2 * len(claims) + 3,
        {r.seq: i for i, r in enumerate(recs)},
        cap,
    )


def _wf(ctx: Context, w: Wk) -> WorkerFacts:
    return ctx.workers[w.facts]


# ----- saves: what a worker may save, and what a stated save rules out ----------------


def _offers(ctx: Context, w: Wk) -> frozenset[Held]:
    """The saves the worker may make in its stage, at any moment of it: the state it
    loaded, before its first pass; its partial state, in a pass (and, once fenced, its
    completed state, which names no node); the state it holds, between passes; and after
    its own stopped. A save of an open pass's completed state is decided where the pass
    ends.
    """
    at = _wf(ctx, w).save_at
    node = None if w.released else w.state
    out: set[Held] = set()
    if w.stage is Stage.LOADING and SaveAt.BEFORE_LOOP in at:
        out.add(Held(w.held, node, w.claim, w.passes))
    elif w.stage is Stage.ITERATING:
        if SaveAt.PARTIAL in at or (w.released and SaveAt.IN_PASS in at):
            out.add(Held(w.clock, None, w.claim, w.passes))
    elif w.stage is Stage.BETWEEN and SaveAt.BETWEEN in at:
        out.add(Held(w.held, node, w.claim, w.passes))
    elif w.stage is Stage.EXITED and SaveAt.AFTER_EXIT in at:
        kept = w.kind is Kind.EXTENDABLE and not w.released
        out.add(Held(w.held, ExitId(w.claim) if kept else None, w.claim, w.passes))
    return frozenset(out)


def _completed(ctx: Context, w: Wk, node: Node | None) -> Held | None:
    """A save of the pass closing now's completed state, inside it, as its last act, or
    between passes, where its version may make one (§7, ``save-at``)."""
    at = _wf(ctx, w).save_at
    if {SaveAt.IN_PASS, SaveAt.AT_CLOSE, SaveAt.BETWEEN} & at:
        return Held(w.clock, None if w.released else node, w.claim, w.passes)
    return None


def _made(slot: frozenset[Held], w: Wk, h: Held) -> frozenset[Held]:
    """The worker certainly made the save ``h``: no save it made before is the newest,
    and there was one. A save of a worker alive beside it when it claimed, or of a later
    claim's, may still be newer, and stays; one of a worker that had ended before it
    claimed was made before it, and goes."""
    return frozenset(
        x
        for x in slot
        if x is not NOTHING
        and (
            x is SOME
            or x.by in w.peers
            or x.by > w.claim
            or (x.by == w.claim and x.gen >= h.gen)
        )
    ) | {h}


def _offer(
    ctx: Context,
    i: int,
    slot: frozenset[Held],
    older: frozenset[Promise],
    hs: Iterable[Held],
) -> Iterator[tuple[frozenset[Held], frozenset[Promise]]]:
    """Saves a worker may make join the checkpoint's candidates; each may also be
    promised to a later ``older`` load, which says it was made."""
    new = [h for h in hs if h not in slot]
    joined = slot | set(new)
    for promised in _promised(ctx, i, older, new):
        yield joined, promised


def _promised(
    ctx: Context, i: int, older: frozenset[Promise], hs: Iterable[Held]
) -> Iterator[frozenset[Promise]]:
    """The promises after saves ``hs`` were made: none of them promised, or one, while
    the ``older`` loads still to come outnumber the promises unused."""
    yield older
    if not ctx.older_loads[i]:
        return
    for h in hs:
        p = Promise(h.step, h.node, False)
        if p not in older and sum(1 for x in older if not x.used) < ctx.older_loads[i]:
            yield older | {p}


class Run(NamedTuple):
    """A silent run's end: the worker (None once it has ended), the checkpoint and older
    promises after it, and the pieces its commits and rewinds decide."""

    w: Wk | None
    slot: frozenset[Held]
    older: frozenset[Promise]
    pieces: tuple[Piece, ...]


class Cut(NamedTuple):
    """How an open pass ended other than by its close: the worker after, the checkpoint,
    the cut commit if any, and whether its save of the completed state landed without
    finishing it (an exit that keeps the pass names it).
    """

    w: Wk
    slot: frozenset[Held]
    pieces: tuple[Piece, ...]
    landed: Held | None  # its save of the completed state, where one landed

    @property
    def kept(self) -> bool:
        """A save of the completed state landed without finishing the pass."""
        return self.landed is not None and not self.pieces


def _cut(ctx: Context, w: Wk, slot: frozenset[Held], ended: Undated) -> Iterator[Cut]:
    """The open pass ends other than by its close (a death, a fence, its own stopped):
    did its iteration finish? ``cut-save`` says, where it began a save of its completed
    state (§7). A commit it makes stands at the cut.
    """
    if not w.saving:  # it began no save of its completed state, or none is open
        yield Cut(w, slot, (), None)
    if (
        w.stage is not Stage.ITERATING
        or w.released
        or SaveAt.IN_PASS not in _wf(ctx, w).save_at
    ):
        return
    stated = _wf(ctx, w).cut_save
    node = CommitId(w.claim, w.commits)
    for landed in (True, False):
        finishes = {CutSave.SAVE_START} | ({CutSave.LANDED} if landed else set())
        if finishes & stated:
            piece = PCommit(w.claim, w.commits, w.epoch, w.position, ended)
            y = w._replace(
                commits=w.commits + 1,
                epoch=w.epoch + 1,
                position=node,
                state=node,
                held=w.clock,
                dating=None,
            )
            made = Held(w.clock, node, w.claim, w.passes) if landed else None
            yield Cut(y, _made(slot, w, made) if made else slot, (piece,), made)
        if landed and (set(CutSave) - finishes) & stated:
            made = Held(w.clock, None, w.claim, w.passes)
            yield Cut(w, _made(slot, w, made), (), made)


def _landed(c: Cut) -> list[Held]:
    """The save a cut landed, which a later ``older`` load may be promised."""
    return [c.landed] if c.landed is not None else []


def _covered(
    stated: frozenset[Saved], slot: frozenset[Held], x: Wk
) -> frozenset[Held] | None:
    """``save-cov`` of the pass a loop continues past, or ``leave-save`` of the one it
    leaves after: what the checkpoint may hold after it, or None where the statement
    leaves nothing. A pass whose completed state its version cannot save is unsaved."""
    if x.closed is None:
        return slot if Saved.UNSAVED in stated else None
    if stated >= {Saved.SAVED, Saved.UNSAVED}:
        return slot
    if Saved.SAVED in stated:
        return _made(slot, x, x.closed)
    if Saved.UNSAVED in stated:
        return slot - {x.closed} or None
    return None


# ----- a worker's silent events -------------------------------------------------------


def _silent(
    ctx: Context, i: int, world: World, w: Wk, *, bound: int, may_end: bool, before: int
) -> list[Run]:
    """Every state the worker reaches by silent events alone before the record at
    ``before`` (index ``i``), with labels at most ``bound``: passes beginning and
    closing, a rewind, and (``may_end``) its end. The saves it may make on the way join
    the checkpoint's candidates."""
    out: dict[Run, None] = {}
    f = _wf(ctx, w)
    seen: set[
        tuple[Wk, frozenset[Held], frozenset[Promise], tuple[Piece, ...], bool]
    ] = set()
    undated = Undated(before, False)

    def visit(
        x: Wk,
        slot: frozenset[Held],
        older: frozenset[Promise],
        pieces: tuple[Piece, ...],
        rewound: bool,
    ) -> None:
        key = (x, slot, older, pieces, rewound)
        if key in seen:
            return
        seen.add(key)
        if len(seen) > ctx.cap:
            raise _too_many(ctx, before, "in one worker's silent run before")
        out[Run(x, slot, older, pieces)] = None
        if x.stage is Stage.ITERATING:
            if _closes(f, x):
                close(x, slot, older, pieces, rewound)
            acted = _save_act(f, x)
            if acted is not None:
                visit(acted, slot, older, pieces, rewound)
        elif x.stage in (Stage.LOADING, Stage.BETWEEN):
            for label in _labels(f, x, bound):
                begin(x, slot, older, pieces, label, rewound)
            if x.stage is Stage.BETWEEN and not rewound:
                for step, node, s2, o2 in _rewinds(f, slot, older):
                    rewind(x, s2, o2, pieces, step, node)
        if may_end and (not _owing(x) or x != w or pieces or _may_save(ctx, i, x)):
            end(x, slot, older, pieces)

    def close(
        x: Wk,
        slot: frozenset[Held],
        older: frozenset[Promise],
        pieces: tuple[Piece, ...],
        rewound: bool,
    ) -> None:
        """Its pass closes: a commit, unless a fence has reached it."""
        y = x._replace(
            stage=Stage.BETWEEN,
            floor=x.clock + 1,
            finished=x.clock,
            held=x.clock,
            last=Last.OTHER,
            saving=False,
        )
        new: tuple[Piece, ...] = ()
        node: Node | None = None
        if not x.released:
            node = CommitId(x.claim, x.commits)
            new = (PCommit(x.claim, x.commits, x.epoch, x.position, undated),)
            y = y._replace(
                position=node,
                commits=x.commits + 1,
                epoch=x.epoch + 1,
                dating=x.commits,
            )
        done = _completed(ctx, x, node)
        y = y._replace(state=node, closed=done)
        for s2, o2 in _offer(
            ctx, i, slot, older, ([done] if done else []) + list(_offers(ctx, y))
        ):
            visit(y, s2, o2, pieces + new, rewound)

    def begin(
        x: Wk,
        slot: frozenset[Held],
        older: frozenset[Promise],
        pieces: tuple[Piece, ...],
        label: int,
        rewound: bool,
    ) -> None:
        """A pass begins at ``label``: where its loop continued past the last one,
        whether that one ticked and saved."""
        if x.stage is Stage.BETWEEN:
            kept = _continued(f, slot, x)
            if kept is None:
                return
            slot = kept
        y = x._replace(
            stage=Stage.ITERATING,
            clock=label,
            tick_due=True,
            begun=label,
            valued=False,
            ticked_in=False,
            acted=False,
            last=Last.OTHER,
            beaten=False,
            dating=None,
            passes=x.passes + 1,
            closed=None,
            saving=False,
        )
        for s2, o2 in _offer(ctx, i, slot, older, _offers(ctx, y)):
            visit(y, s2, o2, pieces, rewound)

    def rewind(
        x: Wk,
        slot: frozenset[Held],
        older: frozenset[Promise],
        pieces: tuple[Piece, ...],
        step: int | None,
        node: Node | None,
    ) -> None:
        """Between passes it reloads a state and drops what it wrote since its last
        position (§4, ``rewind``)."""
        at = 0 if step is None else step
        y = x._replace(
            clock=at,
            floor=0 if step is None else at + 1,
            held=at,
            state=node,
            tick_due=False,
            dating=None,
        )
        new: tuple[Piece, ...] = ()
        if not x.released:
            new = (PRewind(x.claim, x.commits, x.epoch, node),)
            y = y._replace(position=node, epoch=x.epoch + 1)
        for s2, o2 in _offer(ctx, i, slot, older, _offers(ctx, y)):
            visit(y, s2, o2, pieces + new, True)

    def end(
        x: Wk,
        slot: frozenset[Held],
        older: frozenset[Promise],
        pieces: tuple[Piece, ...],
    ) -> None:
        """It ends: its open pass is cut there."""
        if x.last is Last.CLOSES:
            return  # a closing value is followed by its close
        for c in _cut(ctx, x, slot, undated):
            final: tuple[Piece, ...] = (
                () if x.released else (PRunEnd(c.w.claim, c.w.commits, c.w.clock),)
            )
            for promised in _promised(ctx, i, older, _landed(c)):
                out[Run(None, c.slot, promised, pieces + c.pieces + final)] = None

    visit(w, world.slot, world.older, (), False)
    return list(out)


def _closes(f: WorkerFacts, x: Wk) -> bool:
    """Whether the open pass may close now: at once after a value of it, or otherwise
    (``closing``); and, where it closes neither so nor after a tick of its own, with a
    value of it or none (``marker``). A value that is no closing one is not followed at
    once by its close."""
    if x.last is Last.STAYS:
        return False
    at_value = x.last in (Last.EITHER, Last.CLOSES)
    if (Closing.AT_VALUE if at_value else Closing.OTHER) not in f.closing:
        return False
    return (
        at_value
        or x.ticked_in
        or (Marker.RECORDED if x.valued else Marker.SILENT) in f.marker
    )


def _save_act(f: WorkerFacts, x: Wk) -> Wk | None:
    """A save inside the open pass: an act that writes no record. After it the pass's
    last value closes nothing, and a tick is inside the pass. Which state it saved is
    the checkpoint's candidates' concern; where it can only be the completed state's, a
    cut reads that it began. None where it would change nothing, or no save is there.
    """
    if x.acted and x.last not in (Last.EITHER, Last.STAYS):
        return None
    if not ({SaveAt.IN_PASS, SaveAt.PARTIAL} & f.save_at):
        return None
    only = SaveAt.PARTIAL not in f.save_at
    return x._replace(acted=True, last=Last.OTHER, dating=None, saving=x.saving or only)


def _labels(f: WorkerFacts, x: Wk, bound: int) -> list[int]:
    """The labels a pass may begin at (``stride``): the step after the state the worker
    continues from, or above."""
    return [
        label
        for label in range(x.floor, bound + 1)
        if (Stride.NEXT if label == x.floor else Stride.SKIP) in f.stride
    ]


def _continued(f: WorkerFacts, slot: frozenset[Held], x: Wk) -> frozenset[Held] | None:
    """A loop continues past its last pass (``beat-cov``, ``save-cov``): the checkpoint
    after, or None where the statements leave nothing."""
    if (Covered.BEATEN if x.beaten else Covered.UNBEATEN) not in f.beat_cov:
        return None
    return _covered(f.save_cov, slot, x)


def _rewinds(
    f: WorkerFacts, slot: frozenset[Held], older: frozenset[Promise]
) -> Iterator[tuple[int | None, Node | None, frozenset[Held], frozenset[Promise]]]:
    """A rewind's targets (§7, ``rewinds``): the root, the newest save, or an older one
    promised to it."""
    if Rewinds.ROOT in f.rewinds:
        yield None, None, slot, older
    if Rewinds.NEWEST in f.rewinds:
        for h in _real(slot):
            yield h.step, h.node, frozenset({h}), older
    if Rewinds.OLDER in f.rewinds:
        for p in older:
            rest = _not_newest(slot, p)
            if rest is not None:
                yield p.step, p.node, rest, (older - {p}) | {p._replace(used=True)}


def _real(slot: frozenset[Held]) -> list[Held]:
    """The saves a load may read as the newest, one per identity."""
    seen: dict[tuple[int, Node | None], Held] = {}
    for h in sorted(slot, key=repr):
        if h is not NOTHING and h is not SOME:
            seen.setdefault((h.step, h.node), h)
    return list(seen.values())


def _not_newest(slot: frozenset[Held], p: Promise) -> frozenset[Held] | None:
    """The checkpoint where the promised save was made and is not the newest, or None
    where it must be."""
    rest = frozenset(
        h for h in slot if h is not NOTHING and (h.step, h.node) != (p.step, p.node)
    )
    return rest or None


# ----- the records --------------------------------------------------------------------


def _owing(w: Wk) -> bool:
    """A worker whose claim its own stopped or a fence ended. Nothing the lineage
    records, nor any fact, tells one that lives on without acting from one that has
    halted, so it halts right after its last act: it lives on only owing another, and
    ends at none but such a moment."""
    return w.stage is Stage.EXITED or w.released


def _may_save(ctx: Context, i: int, w: Wk) -> bool:
    """Whether a save the worker may still make could be read: a later load or rewind
    may take it. Once its own stopped is written, only a save after it is left."""
    at = _wf(ctx, w).save_at
    can = SaveAt.AFTER_EXIT in at if w.stage is Stage.EXITED else bool(at)
    return can and bool(ctx.newest_ahead[i] or ctx.older_loads[i])


def _may_act(ctx: Context, i: int, w: Wk) -> bool:
    """Whether an owing worker could still act from record ``i`` on: write a record, or
    save what a later load may take. A superset: it reads roles and labels, never who
    else may have written the record.
    """
    if _may_save(ctx, i, w):
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
    """The world's workers with claim ``claim``'s replaced by ``w``, or removed
    (None)."""
    out = [x for x in world.workers if x.claim != claim]
    if w is not None:
        out.append(w)
        out.sort(key=lambda x: x.claim)
    return tuple(out)


def _acted(ctx: Context, i: int, world: World, w: Wk) -> Iterator[World]:
    """After an act of ``w`` at record ``i``: it lives on, and an owing worker halts at
    once, or lives on only where a later record could be its or a later load could take
    a save it makes (``_may_act``).
    """
    if not _owing(w) or _may_act(ctx, i + 1, w):
        yield world._replace(workers=_put(world, w, w.claim))
    if _owing(w):
        yield world._replace(workers=_put(world, None, w.claim))


def _drain(ctx: Context, world: World, w: Wk, seq: int) -> Wk:
    """A native drain before ``seq``: it takes in every stop posted before it that
    nothing has spent (§4; E2)."""
    if w.released:
        return w
    new = frozenset(x for s, x in ctx.posted if s < seq) - world.spent - w.taken
    return w._replace(taken=w.taken | new) if new else w


def _writers(ctx: Context, world: World, rec: Rec) -> Iterable[Wk]:
    """The live workers that may have written a heartbeat or a worker's stopped: the
    claim it names, in a log born in 0.3.0; any, in one born in 0.2.0, whose names are
    the earlier step's positional inference (§7).
    """
    if isinstance(ctx.born, Born030):
        return [w for w in world.workers if w.claim == rec.claim]
    return world.workers


def _before(
    ctx: Context, i: int, world: World, w: Wk, rec: Rec, *, bound: int, may_end: bool
) -> list[Run]:
    """Worker ``w``'s silent events before record ``rec``."""
    return _silent(ctx, i, world, w, bound=bound, may_end=may_end, before=rec.seq)


def _value(ctx: Context, i: int, world: World, rec: Rec) -> Iterator[Step]:
    """A value: a live worker's, in a role the classifier allows and its phase gives, or
    a stray's (§7, ``strays``)."""
    roles = ctx.roles[rec.seq]
    if _stray(ctx, world, rec, roles):
        yield world, (PValue(rec.seq, None, Role.STRAY),)
    bound = rec.step if rec.step is not None else ctx.horizon
    for w in world.workers:
        for run in _before(ctx, i, world, w, rec, bound=bound, may_end=False):
            assert run.w is not None
            after = world._replace(slot=run.slot, older=run.older)
            for y, pieces in _emit(ctx, run.w, rec, roles):
                for nw in _acted(ctx, i, after, y):
                    yield nw, run.pieces + pieces


def _stray(ctx: Context, world: World, rec: Rec, roles: frozenset[ValueRole]) -> bool:
    """Whether a writer holding no claim may have written the value: the classifier
    allows it, and the latest claim's version's ``strays`` its step or none (no element
    before any claim)."""
    if not any(r.role is Role.STRAY for r in roles):
        return False
    cls = Stray.STEPLESS if rec.step is None else Stray.STEPPED
    return world.latest == 0 or any(
        cls in ctx.workers[k].strays for k in _latest(ctx, world)
    )


def _latest(ctx: Context, world: World) -> tuple[int, ...]:
    """The statements the latest claim's version may make: the version of its worker
    alive, or, once it has ended, any its classifier names."""
    w = next((x for x in world.workers if x.claim == world.latest), None)
    return (w.facts,) if w is not None else ctx.versions.get(world.latest, ())


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
    """Worker ``w`` writes the value ``rec`` now: at its clock (its loading clock before
    its first pass), in its phase, pending for its next node where the classifier says
    no register and nothing has ended its claim for it.
    """
    f = _wf(ctx, w)
    if w.last is Last.CLOSES:
        return
    label = w.idle if w.stage is Stage.LOADING else w.clock
    stepped = rec.step is not None
    if stepped and (
        label is None or rec.step != label or _STAGE_PHASE[w.stage] not in f.values
    ):
        return
    allowed = {
        r for r in roles if r.role is not Role.STRAY and _ROLE_STAGE[r.role] is w.stage
    }
    for register in sorted({r.register for r in allowed}):
        if not stepped and not register:
            continue  # a stepless value is a register (§7, M3)
        rs = {r.role for r in allowed if r.register == register}
        last = Last.OTHER
        role = min(rs, key=lambda r: r.value)
        iterating = w.stage is Stage.ITERATING
        if iterating and stepped:
            closing, passing = Role.CLOSING in rs, Role.PASS in rs
            last = (
                Last.EITHER
                if closing and passing
                else (Last.CLOSES if closing else Last.STAYS)
            )
            role = Role.PASS if passing else Role.CLOSING
        y = w._replace(
            last=last,
            acted=w.acted or iterating,
            valued=w.valued or (stepped and iterating),
            dating=None,
        )
        pieces: tuple[Piece, ...] = (PValue(rec.seq, w.claim, role),)
        if not register and not _owing(w):
            pieces += (PHold(rec.seq, w.claim, w.epoch),)
        yield y, pieces


def _beat(ctx: Context, i: int, world: World, rec: Rec) -> Iterator[Step]:
    """A heartbeat: with a step, a tick of the worker's pass; without, a beacon. Each is
    a native worker's drain."""
    bound = rec.step if rec.step is not None else ctx.horizon
    for w in _writers(ctx, world, rec):
        for run in _before(ctx, i, world, w, rec, bound=bound, may_end=False):
            x = run.w
            assert x is not None
            if x.last is Last.CLOSES or x.stage is Stage.EXITED:
                continue
            pieces = run.pieces
            iterating = x.stage is Stage.ITERATING
            if rec.step is None:
                y = x._replace(last=Last.OTHER, acted=x.acted or iterating, dating=None)
            else:
                if not x.tick_due or rec.step != x.clock or x.stage is Stage.LOADING:
                    continue
                pos = (
                    BeatPos.AFTER_CLOSE
                    if not iterating
                    else (BeatPos.INSIDE if x.acted else BeatPos.OPENING)
                )
                if pos not in _wf(ctx, x).beat_pos:
                    continue
                dates = _dated(x)
                if dates is not None:
                    pieces += (PDate(x.claim, dates, rec.t),)
                y = x._replace(
                    tick_due=False,
                    ticked=x.clock,
                    beaten=True,
                    ticked_in=x.ticked_in or iterating,
                    acted=x.acted or iterating,
                    last=Last.OTHER,
                    dating=None,
                )
            y = _drain(ctx, world, y, rec.seq)
            after = world._replace(slot=run.slot, older=run.older)
            for nw in _acted(ctx, i, after, y):
                yield nw, pieces + (PBeat(rec.seq, w.claim),)


def _dated(x: Wk) -> int | None:
    """The commit the tick a worker writes now dates, if any: the one its pass just
    closed with, which the tick follows at once (§7, "The end-time fork")."""
    return x.dating


def _final(w: Wk, convention: FinalStep) -> int | None:
    """What an own stopped's ``final_step`` reports under one convention (§7,
    ``final-step``). A pass a cut finished is not one its loop closed."""
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
    """A worker's own stopped: its exit node, or, once a fence has reached it, no node
    and its withdrawals (E2)."""
    for w in _writers(ctx, world, rec):
        for run in _before(ctx, i, world, w, rec, bound=ctx.horizon, may_end=False):
            x = run.w
            assert x is not None
            if x.stage is not Stage.EXITED and x.last is not Last.CLOSES:
                yield from _exit(ctx, i, world, run, x, rec)


def _exit(
    ctx: Context, i: int, world: World, run: Run, x: Wk, rec: Rec
) -> Iterator[Step]:
    """The worker's dying breath (§4, "The exit"): its open pass cut, its elements
    checked, and its node."""
    f = _wf(ctx, x)
    clean = rec.error is None
    inside = x.stage is Stage.ITERATING
    if not any(
        _final(x, c) == rec.final_step
        for c in (f.final_step if clean else f.error_final_step)
    ):
        return
    slot = run.slot
    if clean:
        if (LoopEnd.IN_PASS if inside else LoopEnd.AFTER_CLOSE) not in f.loop_end:
            return
        if x.stage is Stage.BETWEEN:
            left = _covered(f.leave_save, slot, x)
            if left is None:
                return
            slot = left
    for c in _cut(ctx, x, slot, Undated(rec.seq, True)):
        committed = bool(c.pieces)
        for discards in _discards(f, clean and inside and not committed):
            z = c.w._replace(stage=Stage.EXITED, last=Last.OTHER, dating=None)
            nworld = world._replace(exited=world.exited | {x.claim}, older=run.older)
            pieces = run.pieces + c.pieces
            if x.released:
                fenced, wd = _withdraw(nworld._replace(slot=c.slot), rec)
                for nw in _acted(ctx, i, fenced, z):
                    yield nw, pieces + (
                        PStop(rec.seq, x.claim, Author.FENCED, None),
                    ) + wd
                continue
            kind = (
                Kind.CLOSED
                if rec.completed
                else (Kind.TORN if not clean or discards else Kind.EXTENDABLE)
            )
            keeps = inside and kind is Kind.EXTENDABLE and not committed
            s = c.slot
            made = _landed(c)
            if c.kept and keeps:
                # the landed save of the open pass's completed state, which an exit that
                # keeps the pass holds (§6)
                held = Held(x.clock, ExitId(x.claim), x.claim, x.passes)
                s = (s - set(made)) | {held}
                made = [held]
            z = _drain(ctx, world, z, rec.seq)._replace(
                kind=kind, held=x.clock if keeps else c.w.held
            )
            honored = tuple(sorted(z.taken))
            out = pieces + (
                PStop(rec.seq, x.claim, Author.EXIT, None),
                PExit(
                    x.claim,
                    rec.seq,
                    z.epoch,
                    z.position,
                    kind,
                    not clean or discards,
                    honored,
                ),
            )
            nworld = nworld._replace(
                spent=world.spent | z.taken,
                nodes=world.nodes | {x.claim},
                ended=world.ended | {x.claim},
            )
            for promised in _promised(ctx, i, nworld.older, made):
                for s2, o2 in _offer(ctx, i, s, promised, _offers(ctx, z)):
                    for nw in _acted(ctx, i, nworld._replace(slot=s2, older=o2), z):
                        yield nw, out


def _discards(f: WorkerFacts, open_pass: bool) -> list[bool]:
    """Whether a clean exit inside an open pass keeps it or discards it (``discards``);
    any other keeps nothing."""
    if not open_pass:
        return [False]
    return [
        d
        for d in (False, True)
        if (Discards.DISCARDS if d else Discards.KEEPS) in f.discards
    ]


def _withdraw(world: World, rec: Rec) -> tuple[World, tuple[Piece, ...]]:
    """E2: a withdrawal for each id a stopped that is no exit node honours, unless
    already withdrawn."""
    ids = [x for x in dict.fromkeys(rec.honored) if x not in world.withdrawn]
    if not ids:
        return world, ()
    pieces = tuple(PWithdrawal(x, rec.seq) for x in ids)
    return (
        world._replace(
            withdrawn=world.withdrawn | set(ids), spent=world.spent | set(ids)
        ),
        pieces,
    )


def _state(world: World, n: int | None) -> ClaimState:
    """The state of the claim a third party's stopped names when it writes it (§7,
    ``acts``)."""
    if n is None:
        return ClaimState.UNNAMED
    if n in world.nodes:
        return ClaimState.EXITED
    if n in world.exited or n in world.released or n < world.latest:
        return ClaimState.ENDED
    if any(x.claim == n for x in world.workers):
        return ClaimState.LIVE
    return ClaimState.DEAD


_ACTS: dict[type, Act] = {Release: Act.RELEASE, Note: Act.NOTE, Reap: Act.REAP}


def _third(
    ctx: Context, i: int, world: World, rec: Rec, answer: ThirdStop
) -> Iterator[Step]:
    """A third party's stopped (§7, "The third-party facts"): its act on the claim it
    names, and its withdrawals."""
    tp = ctx.third[answer.writer]
    act = answer.act
    for prior, before in _ahead_of(ctx, i, world, rec):
        state = _state(prior, rec.claim)
        if Acts(state, _ACTS[type(act)]) not in tp.acts:
            continue
        pieces = before + (PStop(rec.seq, rec.claim, Author.THIRD, act),)
        if isinstance(act, Release):
            if rec.claim is None or act.outcome not in tp.outcome:
                continue
            for via in (
                tp.via if state is ClaimState.EXITED else frozenset({Via.ALWAYS})
            ):
                yield from _released(
                    ctx, i, prior, rec, rec.claim, act.outcome, via, pieces
                )
            continue
        if isinstance(act, Reap):
            pieces += (PTerminated(None, act.how),)
        nw, wd = _withdraw(prior, rec)
        yield nw, pieces + wd


def _ahead_of(ctx: Context, i: int, world: World, rec: Rec) -> Iterator[Step]:
    """Before a third party's stopped: the named claim's worker may end silently, so
    that the stop finds it dead; and a worker whose commit the stop's withdrawals would
    come too late for may close its pass first (E2).
    """
    yield world, ()
    for w in world.workers:
        named = w.claim == rec.claim and not _owing(w)
        drains = not w.released and any(
            x not in world.spent and x not in w.taken for x in rec.honored
        )
        if not (named or drains):
            continue
        for run in _before(ctx, i, world, w, rec, bound=ctx.horizon, may_end=named):
            if run.w == w and not run.pieces:
                continue
            x = run.w
            if x is not None and any(isinstance(p, PCommit) for p in run.pieces):
                x = _drain(ctx, world, x, rec.seq)
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
    """A release of claim ``n``: it fences a live worker with no exit node, there (§4),
    and is a death report unless §3 holds it back: through the helper on a claim with a
    readable exit, and without a cause on one that has ended.
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
    if w is None or w.kind is not None or w.released:
        fw, wd = _withdraw(nworld, rec)
        yield fw, out + wd
        return
    for c in _cut(ctx, w, world.slot, Undated(rec.seq, True)):
        z = c.w._replace(released=True, dating=None)
        fw, wd = _withdraw(nworld._replace(slot=c.slot), rec)
        for promised in _promised(ctx, i, fw.older, _landed(c)):
            for s2, o2 in _offer(ctx, i, fw.slot, promised, _offers(ctx, z)):
                for nw in _acted(ctx, i, fw._replace(slot=s2, older=o2), z):
                    yield nw, out + c.pieces + (PFence(n, rec.seq),) + wd


def _stopped(ctx: Context, i: int, world: World, rec: Rec) -> Iterator[Step]:
    for answer in ctx.stops[rec.seq]:
        if isinstance(answer, OwnStop):
            if not rec.malformed:  # no worker writes a body 0.3.0 does not define
                yield from _own_stop(ctx, i, world, rec)
        elif isinstance(answer, ThirdStop):
            yield from _third(ctx, i, world, rec, answer)
        else:
            assert_never(answer)


def _settle(
    ctx: Context, i: int, world: World, rec: Rec, must_end: Callable[[Wk], bool]
) -> Iterator[Step]:
    """Every live worker's silent events before ``rec``, where a claim begins (the gate
    and the load read them) or a process is reaped: each may run on and save, end, or
    neither; ``must_end`` says which must have ended.
    """
    states: list[Step] = [(world, ())]
    for w in world.workers:
        nxt: list[Step] = []
        for wd, pieces in states:
            cur = next(x for x in wd.workers if x.claim == w.claim)
            ends = must_end(cur)
            if not ends:
                nxt.append((wd, pieces))
            for run in _before(ctx, i, wd, cur, rec, bound=ctx.horizon, may_end=True):
                if run.w is not None and (
                    ends or (run.w == cur and not run.pieces and run.slot == wd.slot)
                ):
                    continue
                moved = wd._replace(
                    workers=_put(wd, run.w, w.claim), slot=run.slot, older=run.older
                )
                nxt.append((moved, pieces + run.pieces))
        states = list(dict.fromkeys(nxt))
    yield from states


def _claim(ctx: Context, i: int, world: World, rec: Rec) -> Iterator[Step]:
    """A claim: the gate on the latest one (§4, "The claim"), its death report where
    nothing ended it, and the new worker's load."""
    for wd, pieces in _settle(ctx, i, world, rec, lambda w: False):
        yield from _gate(ctx, i, wd, rec, pieces)


def _gate(
    ctx: Context, i: int, world: World, rec: Rec, pieces: tuple[Piece, ...]
) -> Iterator[Step]:
    """The claim gate (design-v0.2.md §4, §8): the latest claim had ended, or its
    process had, or the gate judged its worker dead though its process lived (a
    takeover, ``takeover``): then its worker had halted, or lives and the claim path's
    report fences it (§4)."""
    p = world.latest
    if not p:
        yield from _load(ctx, i, world, rec, pieces)
        return
    w = next((x for x in world.workers if x.claim == p), None)
    free = p in world.exited or p in world.released
    if w is None:
        # its worker has ended; its process with it, unless a claim from here on runs in
        # it
        if not free and ctx.handles[p] in ctx.reused[i]:
            if not any(
                Takeover.DEAD in ctx.workers[k].takeover for k in ctx.versions[p]
            ):
                return
        yield from _reported(ctx, i, world, rec, pieces, p)
        return
    if free:
        yield from _reported(ctx, i, world, rec, pieces, p)
        return
    if Takeover.LIVE not in _wf(ctx, w).takeover:
        return
    for c in _cut(ctx, w, world.slot, Undated(rec.seq, True)):
        z = c.w._replace(released=True, dating=None)
        for promised in _promised(ctx, i, world.older, _landed(c)):
            for s2, o2 in _offer(ctx, i, c.slot, promised, _offers(ctx, z)):
                for nw in _acted(ctx, i, world._replace(slot=s2, older=o2), z):
                    yield from _reported(
                        ctx, i, nw, rec, pieces + c.pieces + (PFence(p, rec.seq),), p
                    )


def _reported(
    ctx: Context, i: int, world: World, rec: Rec, pieces: tuple[Piece, ...], p: int
) -> Iterator[Step]:
    """The claim path's death report, where nothing ended the latest claim: ``lost``, by
    the gate (§4; E1)."""
    if p not in world.ended:
        pieces += (PDied(p, Outcome.LOST, Source.GATE),)
        world = world._replace(ended=world.ended | {p})
    yield from _load(ctx, i, world, rec, pieces)


class _Loaded(NamedTuple):
    origin: Origin
    step: int | None
    node: Node | None
    slot: frozenset[Held]
    older: frozenset[Promise]


def _loads(ctx: Context, f: WorkerFacts, world: World) -> Iterator[_Loaded]:
    """What a claim of version ``f`` may load (§7, ``load``): nothing, the newest save,
    an older one, or a checkpoint from outside, at any step a later record may read."""
    slot = world.slot
    if Load.FRESH in f.load and NOTHING in slot:
        yield _Loaded(Origin.FRESH, None, None, frozenset({NOTHING}), world.older)
    saved = slot - {NOTHING}
    if Load.RESTART in f.load and saved:
        yield _Loaded(Origin.RESTART, None, None, saved, world.older)
    if Load.NEWEST in f.load:
        for h in _real(slot):
            yield _Loaded(Origin.NODE, h.step, h.node, frozenset({h}), world.older)
    if Load.OLDER in f.load:
        for p in world.older:
            rest = _not_newest(slot, p)
            if rest is not None:
                yield _Loaded(
                    Origin.NODE,
                    p.step,
                    p.node,
                    rest,
                    (world.older - {p}) | {p._replace(used=True)},
                )
    if Load.OUTSIDE in f.load:
        for at in range(ctx.horizon + 1):
            yield _Loaded(Origin.OUTSIDE, at, None, slot, world.older)


def _idles(f: WorkerFacts, loaded: int) -> list[int | None]:
    """What the worker's clock reads before its first pass (``loading``): the loaded
    state's step, or 0. Both read 0 over a state at 0. None where the statement is
    empty: its clock is never read."""
    if loaded == 0:
        return [0 if f.loading else None]
    found = [
        x
        for x, cls in ((loaded, Loading.LOADED), (0, Loading.ZERO))
        if cls in f.loading
    ]
    return list(found) or [None]


def _load(
    ctx: Context, i: int, world: World, rec: Rec, pieces: tuple[Piece, ...]
) -> Iterator[Step]:
    """The new claim's worker: its version, what it loaded, and its clock before its
    first pass (§7, ``loading``)."""
    c = rec.seq
    for k in ctx.versions[c]:
        f = ctx.workers[k]
        for ld in _loads(ctx, f, world):
            loaded = 0 if ld.step is None else ld.step
            for idle in _idles(f, loaded):
                w = Wk(
                    claim=c,
                    facts=k,
                    handle=rec.handle or "",
                    stage=Stage.LOADING,
                    clock=loaded if idle is None else idle,
                    floor=0 if ld.step is None else loaded + 1,
                    idle=idle,
                    held=loaded,
                    state=ld.node,
                    position=ld.node,
                    commits=0,
                    epoch=0,
                    passes=0,
                    tick_due=False,
                    valued=False,
                    ticked_in=False,
                    acted=False,
                    saving=False,
                    last=Last.OTHER,
                    beaten=False,
                    closed=None,
                    begun=None,
                    finished=None,
                    ticked=None,
                    released=False,
                    kind=None,
                    taken=frozenset(),
                    dating=None,
                    peers=frozenset(x.claim for x in world.workers),
                )
                nworld = world._replace(
                    workers=_put(world, w, c), latest=c, slot=ld.slot, older=ld.older
                )
                # every live worker may save after the claim: its offers join the
                # checkpoint again
                offered = [h for x in nworld.workers for h in _offers(ctx, x)]
                for s2, o2 in _offer(ctx, i, nworld.slot, nworld.older, offered):
                    yield nworld._replace(slot=s2, older=o2), pieces + (
                        PStart(c, ld.origin, ld.step, ld.node),
                    )


def _reap(ctx: Context, i: int, world: World, rec: Rec) -> Iterator[Step]:
    """A launcher's reap: every worker in its process has ended, so it ends here if it
    has not (design-v0.2.md §8). A death report for the newest claim naming the launch,
    when that claim runs in its process and nothing else ended it (§3; E1)."""
    launch = rec.request_id
    handle = ctx.launched.get(launch or "")
    how = rec.how
    assert how is not None
    if handle is not None and handle in ctx.reused[i + 1]:
        return  # a later claim runs in a process that has ended
    for wd, pieces in _settle(ctx, i, world, rec, lambda w: w.handle == handle):
        out = pieces + (PTerminated(launch, how),)
        named = [
            c
            for c, lid in ctx.claim_launch.items()
            if lid is not None and lid == launch and c <= wd.latest
        ]
        newest = max(named, default=None)
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
                wd = wd._replace(ended=wd.ended | {newest})
        yield wd, out


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


def _end(ctx: Context, world: World) -> Iterator[tuple[Piece, ...]]:
    """The log's end: every worker ends, and every older promise has been kept."""
    n = len(ctx.recs)
    after = (ctx.recs[-1].seq if ctx.recs else 0) + 1
    states: list[Step] = [(world, ())]
    for w in world.workers:
        nxt: list[Step] = []
        for wd, pieces in states:
            cur = next(x for x in wd.workers if x.claim == w.claim)
            for run in _silent(
                ctx, n, wd, cur, bound=ctx.horizon, may_end=True, before=after
            ):
                if run.w is None:
                    nxt.append(
                        (
                            wd._replace(
                                workers=_put(wd, None, w.claim), older=run.older
                            ),
                            pieces + run.pieces,
                        )
                    )
        states = list(dict.fromkeys(nxt))
    for wd, pieces in states:
        if all(p.used for p in wd.older):
            yield pieces


# ----- the graph ----------------------------------------------------------------------


class Edge(NamedTuple):
    src: int
    dst: int
    pieces: tuple[Piece, ...]


@dataclass
class Graph:
    """The pass's layered graph, kept as its live edges: ``live[i]`` lead from the
    states before record ``i`` to those after it, and the last entry to the end (one
    state, 0), each on some complete path.
    """

    ctx: Context
    widths: list[int]  # the states in each layer
    live: list[list[Edge]]
    # the record index no state could take, len(recs) for the end; None when it admits
    stopped_at: int | None
    widest: int  # the most states in one layer


def _settled(ctx: Context, i: int, world: World) -> World:
    """The state as what follows record ``i`` can still read it: a save's identity that
    no later load or rewind can take is only a save that exists, and an older promise
    kept leaves nothing to keep.
    """
    if not ctx.newest_ahead[i] and any(
        h is not NOTHING and h is not SOME for h in world.slot
    ):
        world = world._replace(slot=frozenset({SOME} | ({NOTHING} & world.slot)))
    if world.older and not ctx.older_loads[i]:
        world = world._replace(older=frozenset(p for p in world.older if not p.used))
    keep = ctx.asked[i] | {world.latest}
    if any(
        len(x - keep) for x in (world.released, world.ended, world.nodes, world.exited)
    ):
        # what no later record asks about a claim is no part of the state
        world = world._replace(
            released=world.released & keep,
            ended=world.ended & keep,
            nodes=world.nodes & keep,
            exited=world.exited & keep,
        )
    if ctx.blind[i]:
        # no later record reads a step: every clock and every save's step is one
        world = world._replace(
            workers=tuple(
                w._replace(
                    clock=0, floor=0, held=0, begun=None, finished=None, ticked=None
                )
                for w in world.workers
            ),
            slot=frozenset(
                h._replace(step=0) if h.step >= 0 else h for h in world.slot
            ),
        )
    return world


def _merged(steps: Iterable[Step]) -> list[Step]:
    """One state's successors, where two differ only in the saves their checkpoint may
    hold, merged into one that may hold either. They share every piece and the rest of
    the state, so a continuation of one is a continuation of the other with that save
    read. Successors of different states are never merged: their pasts may rule a save
    out (a cut commit under ``cut-save=landed`` says its save landed)."""
    groups: dict[tuple[World, tuple[Piece, ...]], set[Held]] = {}
    for world, pieces in steps:
        groups.setdefault((world._replace(slot=frozenset()), pieces), set()).update(
            world.slot
        )
    return [
        (world._replace(slot=frozenset(slot)), pieces)
        for (world, pieces), slot in groups.items()
    ]


def _too_many(ctx: Context, seq: int, where: str) -> Refused:
    return Refused(
        f"more than {ctx.cap} states {where} the record at seq {seq}: the stated facts "
        "leave too many histories to follow at once",
        (f"seq {seq}",),
    )


def build(ctx: Context) -> Graph:
    """The forward pass, then the backward one."""
    layer: dict[World, int] = {START: 0}
    edges: list[list[Edge]] = []
    widths = [1]
    for i in range(len(ctx.recs)):
        nxt: dict[World, int] = {}
        es: dict[Edge, None] = {}
        for world, sid in layer.items():
            for w2, pieces in _merged(_record(ctx, i, world)):
                did = nxt.setdefault(_settled(ctx, i + 1, w2), len(nxt))
                es[Edge(sid, did, pieces)] = None
            # the layer only grows: past the cap it is refused at once
            if len(nxt) > ctx.cap:
                raise _too_many(ctx, ctx.recs[i].seq, "after")
        widths.append(len(nxt))
        edges.append(list(es))
        layer = nxt
        if not nxt:
            return Graph(ctx, widths, [[] for _ in edges], i, max(widths))
    final = list(
        dict.fromkeys(
            Edge(sid, 0, pieces)
            for world, sid in layer.items()
            for pieces in _end(ctx, world)
        )
    )
    edges.append(final)
    if not final:
        return Graph(ctx, widths, [[] for _ in edges], len(ctx.recs), max(widths))
    return Graph(ctx, widths, _alive(edges), None, max(widths))


def admits(g: Graph) -> bool:
    return g.stopped_at is None


def candidates(
    rows: Sequence[Row], configuration: Configuration, *, born: Born, cap: int
) -> Graph:
    """The candidate set of one run's rows: the pass's graph, whose complete paths are
    the candidates."""
    return build(context(rows, configuration, born=born, cap=cap))


# ----- the lineage a path writes ------------------------------------------------------


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
    """One candidate: the records the step writes, by name. Commits' ``t`` is chosen by
    ``when``."""

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
    """Each claim's own records on a path, by seq: its started, and the values,
    heartbeats and stopped its worker wrote."""
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
    """An undated commit's lower bound, and its upper bounds in placement order
    (earliest first)."""
    last = max(s for s in own if s < ended.before)
    lower = ctx.recs[ctx.by_seq[last]].t
    if ended.exact:
        return lower, (ctx.recs[ctx.by_seq[ended.before]].t,)
    return lower, tuple(r.t for r in ctx.recs if last < r.seq <= ended.before)


def times(ctx: Context, pieces: Sequence[Piece]) -> dict[CommitId, frozenset[float]]:
    """Every end time each commit of a path may have: its tick's, or the end-time fork's
    bounds."""
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
    deaths = sorted(
        (Died(p.claim, p.outcome, p.source) for p in ps if isinstance(p, PDied)),
        key=lambda d: (d.claim, d.outcome.value, d.source.value),
    )
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
        tuple(deaths),
        tuple(p for p in ps if isinstance(p, PTerminated)),
        tuple(p for p in ps if isinstance(p, PWithdrawal)),
    )


# ----- complete paths -----------------------------------------------------------------


class TooMany(MigrationError):
    """More distinct paths than a cap asked for."""


def chains(g: Graph, *, cap: int) -> list[tuple[Piece, ...]]:
    """Every complete path's pieces, one per distinct sequence: a forward pass over the
    live edges that keeps, per state, the distinct piece sequences reaching it. Raises
    ``TooMany`` past ``cap`` sequences at a state.
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
        if any(len(cs) > cap for cs in nxt.values()):
            raise TooMany(f"more than {cap} distinct paths reach one state")
        at = nxt
    out: list[tuple[Piece, ...]] = []
    for c in at.get(0, ()):
        parts: list[tuple[Piece, ...]] = []
        while c > 0:
            ps, c = back[c]
            parts.append(ps)
        out.append(tuple(p for part in reversed(parts) for p in part))
    return out


# ----- the projections, and the decode ------------------------------------------------

RUN = 0
"""The run itself, as a subject: what no claim's projection holds (a reap's
``terminated``, a claimless stop's withdrawals)."""


class EndValue(NamedTuple):
    """End: a claim's death reports, and the withdrawals the stops speaking for it make
    (E2)."""

    deaths: tuple[tuple[Outcome, Source], ...]
    withdrawals: tuple[tuple[str, int], ...]
    terminations: tuple[PTerminated, ...]  # the run's only


class SpanValue(NamedTuple):
    """Span: a claim's commits (each its parent and values), its rewinds, and its exit
    node's content."""

    commits: tuple[tuple[int, Node | None, tuple[int, ...]], ...]
    rewinds: tuple[tuple[int, Node | None], ...]
    exit: tuple[Kind, Node | None, tuple[int, ...] | None, tuple[str, ...]] | None


class Reading(NamedTuple):
    """One subject's three projections on one path (§7; the approved architecture):
    Origin, its ``resume_from``; End; and Span. End times are apart: the end-time fork
    never couples anything."""

    origin: Node | None
    end: EndValue
    span: SpanValue


def _subject(ctx: Context, p: Piece) -> int:
    """The claim whose projections a piece belongs to, or the run."""
    if isinstance(
        p, (PStart, PCommit, PDate, PHold, PRewind, PExit, PRunEnd, PDied, PFence)
    ):
        return p.claim
    if isinstance(p, PValue):
        return RUN if p.writer is None else p.writer
    if isinstance(p, PBeat):
        return p.writer
    if isinstance(p, PStop):
        return RUN if p.claim is None else p.claim
    if isinstance(p, PWithdrawal):
        named = ctx.recs[ctx.by_seq[p.by]].claim
        return RUN if named is None else named
    if isinstance(p, PTerminated):
        return RUN
    assert_never(p)


def reading(ctx: Context, subject: int, pieces: Sequence[Piece]) -> Reading:
    """A subject's reading from its pieces on one path."""
    holds: dict[int, list[int]] = defaultdict(list)
    for p in pieces:
        if isinstance(p, PHold):
            holds[p.epoch].append(p.seq)
    origin: Node | None = None
    deaths: list[tuple[Outcome, Source]] = []
    withdrawals: list[tuple[str, int]] = []
    terminations: list[PTerminated] = []
    commits: list[tuple[int, Node | None, tuple[int, ...]]] = []
    rewinds: list[tuple[int, Node | None]] = []
    ended: tuple[Kind, Node | None, tuple[int, ...] | None, tuple[str, ...]] | None = (
        None
    )
    for p in pieces:
        if isinstance(p, PStart):
            origin = p.resume
        elif isinstance(p, PDied):
            deaths.append((p.outcome, p.source))
        elif isinstance(p, PWithdrawal):
            withdrawals.append((p.request_id, p.by))
        elif isinstance(p, PTerminated):
            terminations.append(p)
        elif isinstance(p, PCommit):
            commits.append((p.nth, p.parent, tuple(sorted(holds[p.epoch]))))
        elif isinstance(p, PRewind):
            rewinds.append((p.commits, p.target))
        elif isinstance(p, PExit):
            values = None if p.discarded else tuple(sorted(holds[p.epoch]))
            ended = (p.kind, p.parent, values, p.honored)
    return Reading(
        origin,
        EndValue(
            tuple(sorted(deaths, key=repr)),
            tuple(sorted(withdrawals)),
            tuple(sorted(terminations, key=repr)),
        ),
        SpanValue(
            tuple(sorted(commits, key=repr)), tuple(sorted(rewinds, key=repr)), ended
        ),
    )


class Window(NamedTuple):
    """One subject's partial paths over the layers its pieces stand in: per layer, the
    partial sequences reaching each state, interned."""

    first: int
    last: int
    # partial id -> (its last pieces, the partial before)
    back: list[tuple[tuple[Piece, ...], int]]
    at: list[dict[int, set[int]]]  # per layer first..last + 1: state -> partial ids


def _window(g: Graph, subject: int, *, cap: int) -> Window | None:
    """The subject's partial paths, or None where no live edge carries its pieces."""
    marks = [
        k
        for k, es in enumerate(g.live)
        if any(_subject(g.ctx, p) == subject for e in es for p in e.pieces)
    ]
    if not marks:
        return None
    first, last = marks[0], marks[-1]
    cells: dict[tuple[tuple[Piece, ...], int], int] = {}
    back: list[tuple[tuple[Piece, ...], int]] = [((), -1)]
    at: list[dict[int, set[int]]] = [{e.src: {0} for e in g.live[first]}]
    for k in range(first, last + 1):
        nxt: dict[int, set[int]] = defaultdict(set)
        for e in g.live[k]:
            mine = tuple(p for p in e.pieces if _subject(g.ctx, p) == subject)
            for c in at[-1].get(e.src, ()):
                if mine:
                    key = (mine, c)
                    if key not in cells:
                        cells[key] = len(back)
                        back.append(key)
                    nxt[e.dst].add(cells[key])
                else:
                    nxt[e.dst].add(c)
        if any(len(cs) > cap for cs in nxt.values()):
            raise TooMany(
                f"more than {cap} partial paths of claim {subject} reach one state"
            )
        at.append(dict(nxt))
    return Window(first, last, back, at)


def _pieces(w: Window, c: int) -> tuple[Piece, ...]:
    parts: list[tuple[Piece, ...]] = []
    while c > 0:
        ps, c = w.back[c]
        parts.append(ps)
    return tuple(p for part in reversed(parts) for p in part)


def subjects(g: Graph) -> list[int]:
    """The claims, last first, then the run: the decode's order."""
    claims = sorted(
        (r.seq for r in g.ctx.recs if r.topic is Topic.STARTED), reverse=True
    )
    return claims + [RUN]


def readings(g: Graph, subject: int, *, cap: int) -> dict[Reading, tuple[Piece, ...]]:
    """Every reading the subject has on some complete path, each with its pieces on one
    such path (its witness's part)."""
    w = _window(g, subject, cap=cap)
    if w is None:
        return {reading(g.ctx, subject, ()): ()}
    out: dict[Reading, tuple[Piece, ...]] = {}
    for cs in w.at[-1].values():
        for c in cs:
            ps = _pieces(w, c)
            out.setdefault(reading(g.ctx, subject, ps), ps)
    return out


def _alive(live: list[list[Edge]]) -> list[list[Edge]]:
    """The edges on some complete path, again: forward from the start, backward from the
    end."""
    reach: set[int] = {0}
    fwd: list[list[Edge]] = []
    for es in live:
        kept = [e for e in es if e.src in reach]
        fwd.append(kept)
        reach = {e.dst for e in kept}
    out: list[list[Edge]] = [[] for _ in fwd]
    alive: set[int] = {0}
    for k in range(len(fwd) - 1, -1, -1):
        out[k] = [e for e in fwd[k] if e.dst in alive]
        alive = {e.src for e in out[k]}
    return out


def restrict(g: Graph, subject: int, chosen: Reading, *, cap: int) -> Graph:
    """The graph of the complete paths on which ``subject`` reads ``chosen``. Inside the
    subject's window a state is split by the partial path that reached it, so that no
    path mixes two partials; it is merged again after.
    """
    w = _window(g, subject, cap=cap)
    if w is None:
        return (
            g
            if reading(g.ctx, subject, ()) == chosen
            else replace(g, live=[[] for _ in g.live])
        )
    cells = {key: i for i, key in enumerate(w.back)}
    Split = tuple[tuple[int, int], tuple[int, int], tuple[Piece, ...]]
    split: list[list[Split]] = []
    for k in range(w.first, w.last + 1):
        es: list[Split] = []
        for e in g.live[k]:
            mine = tuple(p for p in e.pieces if _subject(g.ctx, p) == subject)
            for c in w.at[k - w.first].get(e.src, ()):
                es.append(
                    ((e.src, c), (e.dst, cells[(mine, c)] if mine else c), e.pieces)
                )
        split.append(es)
    # backward from the ends whose partial reads ``chosen``, through the split states
    good = {
        (s, c)
        for s, cs in w.at[-1].items()
        for c in cs
        if reading(g.ctx, subject, _pieces(w, c)) == chosen
    }
    kept: list[list[Split]] = [[] for _ in split]
    for k in range(len(split) - 1, -1, -1):
        kept[k] = [x for x in split[k] if x[1] in good]
        good = {x[0] for x in kept[k]}
    ids: list[dict[tuple[int, int], int]] = [{} for _ in range(len(kept) + 1)]
    live = [list(es) for es in g.live]
    for k, es in enumerate(kept):
        new: list[Edge] = []
        for src, dst, ps in es:
            a = src[0] if k == 0 else _split_id(ids[k], src)
            b = dst[0] if k == len(kept) - 1 else _split_id(ids[k + 1], dst)
            new.append(Edge(a, b, ps))
        live[w.first + k] = list(dict.fromkeys(new))
    return replace(g, live=_alive(live))


def _split_id(ids: dict[tuple[int, int], int], key: tuple[int, int]) -> int:
    """A split state's id in its layer, apart from every unsplit one's."""
    if key not in ids:
        ids[key] = -1 - len(ids)
    return ids[key]


Chooser: TypeAlias = Callable[[int, Sequence[Reading]], Reading | None]
"""A chooser picks one of a subject's readings, or None to refuse (§7; Task 21 ships
them)."""


class Undecided(MigrationError):
    """A chooser refused a fork: ``subject`` is the claim (or the run, 0) it was at."""

    def __init__(self, message: str, subject: int) -> None:
        super().__init__(message)
        self.subject = subject


def decode(g: Graph, choose: Chooser, *, cap: int) -> Graph:
    """The decode's stated rule: from the last claim back to the first, then the run,
    each subject's fork is chosen among the readings it has on the paths every later
    choice left, and the graph is pruned to the choice before the next subject is read.
    A subject with one reading needs no choice."""
    for subject in subjects(g):
        found = sorted(readings(g, subject, cap=cap), key=repr)
        if len(found) == 1:
            continue
        pick = choose(subject, found)
        if pick is None:
            raise Undecided(
                f"the chooser refused the fork at subject {subject}", subject
            )
        if pick not in found:
            raise MigrationError(
                f"the chooser picked a reading claim {subject} does not have"
            )
        g = restrict(g, subject, pick, cap=cap)
    return g


# ----- a refusal's names --------------------------------------------------------------


_ROLES = frozenset(
    ValueRole(r, reg)
    for r in Role
    for reg in (False, True)
    if r is not Role.STRAY or reg
)


class _Relaxed:
    """A classifier whose answers for one kind of record are the whole domain: every
    version, every author and act a record allows by its shape, every role."""

    def __init__(
        self, inner: LogReader, method: str, configuration: Configuration
    ) -> None:
        self._inner = inner
        self._method = method
        self._cfg = configuration

    def started(self, seq: int) -> frozenset[Hashable]:
        if self._method == "started":
            return frozenset(self._cfg.workers)
        return self._inner.started(seq)

    def stopped(self, seq: int) -> frozenset[StopAnswer]:
        if self._method != "stopped":
            return self._inner.stopped(seq)
        acts: list[Release | Note | Reap] = [Release(o) for o in Outcome]
        acts.append(Note())
        acts += [
            a.act
            for a in self._inner.stopped(seq)
            if isinstance(a, ThirdStop) and isinstance(a.act, Reap)
        ]
        out: set[StopAnswer] = {OwnStop()}
        out |= {ThirdStop(w, a) for w in self._cfg.third_parties for a in acts}
        return frozenset(out)

    def value(self, seq: int) -> frozenset[ValueRole]:
        return _ROLES if self._method == "value" else self._inner.value(seq)


class _Relaxing:
    def __init__(self, inner: Configuration, method: str) -> None:
        self._inner = inner
        self._method = method

    def read(self, log: Sequence[Row]) -> LogReader:
        return _Relaxed(self._inner.classifier.read(log), self._method, self._inner)


_WORKER_DOMAINS: dict[str, frozenset[Any]] = {
    "stride": frozenset(Stride),
    "loading": frozenset(Loading),
    "values": frozenset(Phase),
    "beat_pos": frozenset(BeatPos),
    "beat_cov": frozenset(Covered),
    "marker": frozenset(Marker),
    "closing": frozenset(Closing),
    "loop_end": frozenset(LoopEnd),
    "discards": frozenset(Discards),
    "final_step": frozenset(FinalStep),
    "error_final_step": frozenset(FinalStep),
    "cut_save": frozenset(CutSave),
    "save_at": frozenset(SaveAt),
    "save_cov": frozenset(Saved),
    "leave_save": frozenset(Saved),
    "load": frozenset(Load),
    "rewinds": frozenset(Rewinds),
    "takeover": frozenset(Takeover),
    "strays": frozenset(Stray),
}
_THIRD_DOMAINS: dict[str, frozenset[Any]] = {
    "acts": frozenset(
        Acts(s, a)
        for s in ClaimState
        for a in Act
        if not (s is ClaimState.UNNAMED and a is Act.RELEASE)
    ),
    "via": frozenset(Via),
    "outcome": frozenset(Outcome),
}


def _relaxed(configuration: Configuration, names: Sequence[str]) -> Configuration:
    workers = {
        k: replace(f, **{n: _WORKER_DOMAINS[n] for n in names if n in _WORKER_DOMAINS})
        for k, f in configuration.workers.items()
    }
    third = {
        k: replace(f, **{n: _THIRD_DOMAINS[n] for n in names if n in _THIRD_DOMAINS})
        for k, f in configuration.third_parties.items()
    }
    out = Configuration(workers, third, configuration.classifier)
    for n in names:
        if n.startswith("classifier."):
            out = out._replace(classifier=_Relaxing(out, n.removeprefix("classifier.")))
    return out


FACTS = tuple(_WORKER_DOMAINS) + tuple(f"third.{n}" for n in _THIRD_DOMAINS)
"""The facts a refusal may name: the worker facts, then the third-party facts. The
encodings decide no output, so no refusal names them."""

ANSWERS = ("classifier.started", "classifier.stopped", "classifier.value")


def blame(
    rows: Sequence[Row], configuration: Configuration, *, born: Born, cap: int
) -> tuple[str, ...]:
    """What a refusal names (§7, "What refuses"): each stated fact and classifier answer
    whose relaxation alone, to its whole domain, admits a history; where none alone
    does, the smallest pairs whose joint relaxation does. Empty where no relaxation of
    one or two admits one."""

    def admitted(names: Sequence[str]) -> bool:
        plain = [n.removeprefix("third.") for n in names]
        try:
            return admits(
                candidates(rows, _relaxed(configuration, plain), born=born, cap=cap)
            )
        except Refused:
            return True  # too many to follow is not none

    stated = [
        n
        for n in FACTS
        if any(
            getattr(f, n.removeprefix("third.")) != _all(n)
            for f in _facts_of(configuration, n)
        )
    ] + list(ANSWERS)
    singles = tuple(n for n in stated if admitted([n]))
    if singles:
        return singles
    return tuple(
        f"{a} + {b}"
        for k, a in enumerate(stated)
        for b in stated[k + 1 :]
        if admitted([a, b])
    )


def _all(name: str) -> frozenset[Any]:
    return (
        _THIRD_DOMAINS[name.removeprefix("third.")]
        if name.startswith("third.")
        else _WORKER_DOMAINS[name]
    )


def _facts_of(configuration: Configuration, name: str) -> list[Any]:
    return list(
        configuration.third_parties.values()
        if name.startswith("third.")
        else configuration.workers.values()
    )
