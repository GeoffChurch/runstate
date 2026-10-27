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
run. A set of one is the answer. Several are forks, reported per claim in four
projections (End, Span, Origin, and Writer, who wrote a heartbeat) and decided by
choosers, from the last claim back.

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

**Why the small cases speak for every size.** Everything a record can depend on from
the records before it is in the world between them: the checkpoint's candidates and the
older saves promised, which a load or a rewind reads; the workers alive, each with its
program's state, which its next record and its silent events read; and the claims
ended, released or exited and the stops spent, which the gate, the verdict and E2 read.
The context's lookaheads (``ahead``, ``blind``, ``asked``, ``newest_ahead``,
``older_loads``) read only the records from an index on. So the world screens off the
past: two prefixes that reach one world have the same continuations, and the pass is
right on a log if each record's rule is right from each world that reaches it. A longer
log adds records under the same few rules, from worlds made of the same parts, more of
them: more claims ended, more saves in the slot, more workers alive. The oracle's
families enumerate every combination of the writer model's options up to small bounds,
so they exercise each rule from each kind of world those bounds produce. That is a
bounded model check, with the simulator as the model: evidence for every size, not a
proof. Its limit is its bounds. A fault that needs more parts at once than a family
holds (three workers alive, two silent rewinds, one version over several claims) is out
of its reach, and the mutation test measures only the rules its mutants narrow. No
formal-verification tool is used: the simulator and the families are already that
check, and a third encoding would need its own referee.

**Narrowing.** A stated fact, and a classifier answer, only remove candidates. Each rule
reads a statement as the classes it allows and offers an edge for each, as it does each
answer the classifier gives; an empty ``loading`` allows no clock read before the first
pass (``_idles``). The lookaheads read them too, and each keeps more of a world where
more may follow (``newest_ahead``, ``older_loads``, ``blind``). So wider facts admit a
superset of lineages: two histories' truths, each a candidate under its own facts, are
both candidates under the facts true of both, which the oracle's first property rests
on. One statement is read otherwise: an empty ``fold`` lets either fold stand
(``_folds``), since its element occurs only where the two folds write different logs,
and an encoding decides no output. A fact's whole domain contains every statement of
it, so a relaxation narrower than the whole that admits a history shows the whole one
does (``blame``).

**The encodings** (§7, "Encodings") decide no output under E2, but a log can contradict
them, so the engine checks them: a worker's ``watermark`` at each heartbeat, its
``honored`` and ``fold`` at its own stopped, where that list is its word (a log born in
0.3.0), and a third party's ``final-step`` and ``honored``.
"""

from __future__ import annotations

import bisect
import itertools
import json
import math
from collections import defaultdict
from collections.abc import Callable, Hashable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from functools import partial
from enum import Enum
from typing import (
    Any,
    NamedTuple,
    Protocol,
    TypeAlias,
    TypeVar,
    assert_never,
    get_args,
    get_type_hints,
)

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
    Fold,
    Honored,
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
    ThirdFinal,
    ThirdHonored,
    ThirdPartyFacts,
    ThirdStop,
    ValueRole,
    Via,
    Watermark,
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
    consumed: int | None  # a heartbeat's consumed_seq


_TOPICS = {t.value: t for t in Topic}


def read(rows: Sequence[Row]) -> tuple[Rec, ...]:
    """The rows as records. The step's parse has accepted them (every body as 0.3.0
    pins it, and nothing 0.4.0 cannot hold), so a body's keys are there to read."""
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
                seq=r.seq,
                topic=topic,
                t=when,
                step=body["step"] if stepped else None,
                claim=body["claim_seq"] if named else None,
                request_id=r.request_id,
                handle=(
                    body["handle"] if topic in (Topic.STARTED, Topic.LAUNCHED) else None
                ),
                completed=body["completed"] if whole else False,
                error=body["error"] if whole else None,
                final_step=body["final_step"] if whole else None,
                honored=tuple(body["honored"]) if stopped else (),
                malformed=malformed,
                how=how,
                consumed=body["consumed_seq"] if topic is Topic.BEAT else None,
            )
        )
    return tuple(out)


# ----- the lineage's names ------------------------------------------------------------


class CommitId(NamedTuple):
    """A claim's ``nth`` commit (from 0), the claim named by its ``started``'s 0.3.0
    seq."""

    claim: int
    nth: int


class RewindId(NamedTuple):
    """A claim's rewind, by the epoch it closed (``PRewind.epoch``): one commit or one
    rewind closes each epoch of a claim, so it names one rewind on every path."""

    claim: int
    epoch: int


Silent: TypeAlias = CommitId | RewindId
"""A record the step writes where no record of the log stands: a commit or a rewind,
each placed by its gaps (``Gaps``)."""


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


class Gaps(NamedTuple):
    """Where a commit stands: in the gap after one of the records from the one at seq
    ``first`` to the one at seq ``last``, all past the same boundary, the record at seq
    ``after`` (a claim's ``started``, a ``control.stop`` or a ``stopped``).

    ``after`` is Span's placement (critique P4): the interval between two boundaries,
    the records a commit's place can matter to. Its native drain reads there (E2), so
    the exit's ``honored`` follows from it; where in the interval the step writes the
    record is the chooser's. ``first`` and ``last`` bound its end time where no record
    dates it (§7, "The end-time fork", ``bounds``)."""

    after: int
    first: int
    last: int


UNPLACED = Gaps(-1, -1, -1)
"""A commit a worker's silent run made, not yet placed among the gaps it may stand in
(``_placed``)."""


class Spent(NamedTuple):
    """A stop a withdrawal or an exit spent, at the record at seq ``at`` (E2)."""

    stop: str
    at: int


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
    gaps: Gaps


class PDate(NamedTuple):
    """A commit dated by its own tick, the heartbeat at ``seq``, which follows its close
    at once (§7, "The end-time fork"): the step writes the commit in its place."""

    claim: int
    nth: int
    t: float
    seq: int


class PHold(NamedTuple):
    """Span: a value its claim's worker wrote as a metric, pending for the node of its
    ``epoch``."""

    seq: int
    claim: int
    epoch: int


class PValue(NamedTuple):
    """Span's value holds: who wrote the value, a claim's worker or none, in which
    role, and whether as a register, which no node holds (the classifier's answer)."""

    seq: int
    writer: int | None
    role: Role
    register: bool


class PBeat(NamedTuple):
    """A heartbeat, and the worker that wrote it."""

    seq: int
    writer: int


class PRewind(NamedTuple):
    """Span: a reload between passes, placed as a commit is (``Gaps``)."""

    claim: int
    commits: int
    epoch: int
    target: Node | None
    gaps: Gaps


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


class PHorizon(NamedTuple):
    """A pass closed at the horizon that its loop may continue past: the commits a
    longer run makes, which no record pins, were cut there (``Context.horizon``).

    It marks only a cut a reading could read. Span reads a claim's commits, and no
    piece carries a label (``PCommit``), so what the horizon can change is how many
    commits a run makes. A commit past the horizon needs a pass begun past it, and a
    pass begins at its worker's floor or above: a floor past the horizon comes from a
    close at the horizon, which this piece marks where the loop may continue, or from a
    load or a rewind of a state at the horizon, which admits no pass and whose step
    nothing reads (below). A pass begun at the horizon itself and cut before its close
    makes at most one commit, at the horizon, where ``cut-save`` says its save finished
    it, a commit the bound admits; the passes it forbids all follow a marked close. A
    load reads its step into the worker's clock, which Origin does not read
    (``Resumes``); a later record that reads the clock (a step, a ``final_step``, a save
    a later claim loads) bounds it by that record's own label, below the horizon, so a
    load at the horizon is on no complete path where its step is read."""

    claim: int


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
    | PHorizon
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


NOTHING = Held(step=-1, node=None, by=0, gen=-1)
"""No save at all: the checkpoint may hold nothing."""

SOME = Held(step=-2, node=None, by=0, gen=-2)
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
    # where its state was last fixed: the boundary at or before that record, by index.
    # Its silent events since stand in the gaps after it; the gaps before its own last
    # record there are ruled out by that record, where an end time is read (``bounds``)
    fixed: int
    # the 0.3.0 writer's own drains, which its stopped's ``honored`` lists (``honored``,
    # ``fold``): its fold, None where no element can occur; the stops a drain has
    # considered; those it took in
    fold: Fold | None
    considered: frozenset[str]
    took: frozenset[str]


class World(NamedTuple):
    workers: tuple[Wk, ...]  # in claim order
    latest: int  # the latest claim's seq; 0 before any
    # the statement the latest claim's version makes, the version its worker loaded as:
    # a later gate and a stray read it after its worker has ended; None before any
    version: int | None
    released: frozenset[int]  # claims a release named
    ended: frozenset[int]  # claims an exit node or a death report ended (§2, ``ended``)
    nodes: frozenset[int]  # claims with an exit node
    exited: frozenset[int]  # claims whose worker wrote its own stopped
    slot: frozenset[Held]  # the saves that may be the newest
    older: frozenset[Promise]
    spent: frozenset[Spent]
    withdrawn: frozenset[str]


START = World(
    workers=(),
    latest=0,
    version=None,
    released=frozenset(),
    ended=frozenset(),
    nodes=frozenset(),
    exited=frozenset(),
    slot=frozenset({NOTHING}),
    older=frozenset(),
    spent=frozenset(),
    withdrawn=frozenset(),
)

Step: TypeAlias = tuple[World, tuple[Piece, ...]]

# ----- the context: a log, its configuration, and what the pass looks ahead to --------


class Names(Protocol):
    """What a log's names are worth, by its birth: resolved once, in ``context``."""

    def writers(self, world: World, rec: Rec) -> Sequence[Wk]:
        """The live workers that may have written a heartbeat or a worker's
        stopped."""
        ...

    def may_name(self, a: Ahead, w: Wk) -> bool:
        """Whether a heartbeat or a stopped ahead could be worker ``w``'s."""
        ...

    def honored(self) -> bool:
        """Whether a stopped's ``honored`` is its writer's word, which the encodings
        ``honored`` and ``fold`` can be checked against."""
        ...

    def progress(self, recs: Sequence[Rec]) -> tuple[int | None, ...]:
        """Per record index, what runstate's ``progress()`` returned on the records
        before it, which a third party's ``final_step`` under ``progress`` reads (§7):
        the greater of the current episode's heartbeat step and its stopped's
        ``final_step``, each where an integer. Which records are the episode's is the
        reading the runstate that wrote the log made."""
        ...


class OwnNames:
    """A log born in format 0.3.0: a heartbeat's or a stopped's ``claim_seq`` names
    its writer's claim, and its ``honored`` is its writer's (§7)."""

    def writers(self, world: World, rec: Rec) -> Sequence[Wk]:
        return [w for w in world.workers if w.claim == rec.claim]

    def may_name(self, a: Ahead, w: Wk) -> bool:
        return w.claim in a.named

    def honored(self) -> bool:
        return True

    def progress(self, recs: Sequence[Rec]) -> tuple[int | None, ...]:
        """By name: the newest heartbeat naming the newest claim, and the newest
        stopped after it naming it, however formed; before any claim, the newest
        claimless stopped."""
        claim: int | None = None
        beat: int | None = None
        stop: int | None = None
        out: list[int | None] = [None]
        for r in recs:
            if r.topic is Topic.STARTED:
                claim, beat, stop = r.seq, None, None
            elif r.topic is Topic.BEAT and r.claim == claim:
                beat = r.step
            elif r.topic is Topic.STOPPED and r.claim == claim:
                stop = r.final_step
            out.append(_greater(beat, stop))
        return tuple(out)


class InferredNames:
    """A log born in format 0.2.0: the earlier step filled every ``claim_seq`` and
    ``honored`` by its readings (``Born020``). An inference is no writer's word, so any
    live worker may have written such a record, and no ``honored`` is checked; which
    reading the earlier step used therefore changes no output here."""

    def writers(self, world: World, rec: Rec) -> Sequence[Wk]:
        return world.workers

    def may_name(self, a: Ahead, w: Wk) -> bool:
        return a.beacon or a.beat >= w.clock or a.stop

    def honored(self) -> bool:
        return False

    def progress(self, recs: Sequence[Rec]) -> tuple[int | None, ...]:
        """Positional (runstate 72d9c3f): the newest heartbeat of any claim, and the
        newest stopped unless a started follows it."""
        beat: int | None = None
        stop: int | None = None
        out: list[int | None] = [None]
        for r in recs:
            if r.topic is Topic.STARTED:
                stop = None
            elif r.topic is Topic.BEAT:
                beat = r.step
            elif r.topic is Topic.STOPPED:
                stop = r.final_step
            out.append(_greater(beat, stop))
        return tuple(out)


def _greater(beat: int | None, stop: int | None) -> int | None:
    """``progress()``'s value from its two registers: the greater integer, else
    None."""
    steps = [x for x in (beat, stop) if x is not None]
    return max(steps, default=None)


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
    # each stopped's answers and each value's roles, in a fixed order (``context``)
    stops: Mapping[int, tuple[StopAnswer, ...]]
    roles: Mapping[int, tuple[ValueRole, ...]]
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
    # the indices of the boundaries, in order: each claim's started, control.stop and
    # stopped, between which a commit's placement is an interval (``Gaps``)
    boundaries: tuple[int, ...]
    # what the log's names are worth, by its birth
    names: Names
    # per record index: the seq of the newest control.stop before it, 0 for none
    control_before: tuple[int, ...]
    # per record index: the stops a stopped before it named
    named_before: tuple[frozenset[str], ...]
    # per record index: what runstate's ``progress()`` returned on the records before
    # it (``Names.progress``)
    progress_before: tuple[int | None, ...]
    # the highest label a run-on is followed to: past every step the log names, by
    # ``run_on`` a claim and once more. A pass closed there that its loop may continue
    # past carries ``PHorizon``: the commits of a longer run were cut there, so the
    # step refuses the run (``reaching``)
    horizon: int
    by_seq: Mapping[int, int]
    # the states the pass may hold at once, in a layer or in one worker's silent run:
    # past it the run is refused, naming the record
    cap: int
    # the states the graph may hold summed over every layer, which its memory follows
    # (about 420 bytes a state, its edges included): past it the run is refused,
    # naming the record
    budget: int


def context(
    rows: Sequence[Row],
    configuration: Configuration,
    *,
    born: Born,
    cap: int,
    budget: int,
    run_on: int,
) -> Context:
    """One log, read once, with its classifier's answers and what the pass looks ahead
    to."""
    recs = read(rows)
    reader: LogReader = configuration.classifier.read(rows)
    # The classifier's answers are sets of the consumer's keys, whose hash and repr are
    # the consumer's: each is put in the configuration's declared order here, once, so
    # that no later loop, state number or witness depends on either.
    declared = {v: k for k, v in enumerate(configuration.workers)}
    writers = {w: k for k, w in enumerate(configuration.third_parties)}
    named: dict[int, list[Hashable]] = {}
    stops: dict[int, tuple[StopAnswer, ...]] = {}
    roles: dict[int, tuple[ValueRole, ...]] = {}
    for r in recs:
        if r.topic is Topic.STARTED:
            vs = list(reader.started(r.seq))
            for v in vs:
                if v not in declared:
                    raise Unknown(
                        f"the classifier names worker version {v!r} for the claim at "
                        f"seq {r.seq}, which the configuration states no facts for"
                    )
            named[r.seq] = sorted(vs, key=declared.__getitem__)
        elif r.topic is Topic.STOPPED:
            answers = list(reader.stopped(r.seq))
            for a in answers:
                if isinstance(a, ThirdStop) and a.writer not in writers:
                    raise Unknown(
                        f"the classifier names third-party writer {a.writer!r} for the "
                        f"stopped at seq {r.seq}, which the configuration states no "
                        "facts for"
                    )
            stops[r.seq] = tuple(
                sorted(
                    answers,
                    key=lambda a: (
                        (-1, "")
                        if isinstance(a, OwnStop)
                        else (writers[a.writer], repr(a.act))
                    ),
                )
            )
        elif r.topic is Topic.VALUE:
            roles[r.seq] = tuple(
                sorted(
                    reader.value(r.seq),
                    key=lambda x: (_ROLE_ORDER.index(x.role), x.register),
                )
            )
    # the statements the log's claims make, numbered in the declared order of the first
    # version making each
    statements: list[WorkerFacts] = []
    index: dict[WorkerFacts, int] = {}
    used = {v for vs in named.values() for v in vs}
    for v in configuration.workers:
        f = configuration.workers[v]
        if v in used and f not in index:
            index[f] = len(statements)
            statements.append(f)
    versions = {
        c: tuple(sorted({index[configuration.workers[v]] for v in vs}))
        for c, vs in named.items()
    }
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
            reused[i] = reused[i] | {_handle(r)}
    nothing_ahead = Ahead(
        teardown=frozenset(),
        worker=-1,
        stepless=False,
        beat=-1,
        beacon=False,
        stop=False,
        named=frozenset(),
    )
    aheads = [nothing_ahead] * (n + 1)
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
        # a null final_step reads no step: whether it is null is kept where no step
        # is read (``_settled``)
        reads = (
            r.step is not None and not (zero and loading_only and r.step == 0)
        ) or r.final_step is not None
        blind[i] = blind[i + 1] and not reads
    control_before: list[int] = [0]
    named_before: list[frozenset[str]] = [frozenset()]
    for r in recs:
        control_before.append(r.seq if r.topic is Topic.STOP else control_before[-1])
        named_before.append(named_before[-1] | set(r.honored))
    names: Names = OwnNames() if isinstance(born, Born030) else InferredNames()
    return Context(
        recs=recs,
        born=born,
        workers=tuple(statements),
        versions=versions,
        stops=stops,
        roles=roles,
        third=dict(configuration.third_parties),
        posted=tuple(
            (r.seq, r.request_id)
            for r in recs
            if r.topic is Topic.STOP and r.request_id is not None
        ),
        launched=launched,
        claim_launch={r.seq: r.request_id for r in claims},
        handles={r.seq: _handle(r) for r in claims},
        reused=reused,
        newest_ahead=tuple(newest),
        older_loads=tuple(loads),
        ahead=tuple(aheads),
        blind=tuple(blind),
        asked=tuple(asked),
        boundaries=tuple(
            k
            for k, r in enumerate(recs)
            if r.topic in (Topic.STARTED, Topic.STOP, Topic.STOPPED)
        ),
        names=names,
        control_before=tuple(control_before),
        named_before=tuple(named_before),
        progress_before=names.progress(recs),
        horizon=max(labels, default=0) + run_on * (len(claims) + 1) + 1,
        by_seq={r.seq: i for i, r in enumerate(recs)},
        cap=cap,
        budget=budget,
    )


def _handle(r: Rec) -> str:
    """A started's handle, which every started carries (lifecycle-v0.5)."""
    assert r.handle is not None
    return r.handle


def _wf(ctx: Context, w: Wk) -> WorkerFacts:
    return ctx.workers[w.facts]


# ----- saves: what a worker may save, and what a stated save rules out ----------------


def _held(w: Wk, step: int, node: Node | None) -> Held:
    """A save the worker makes now: of a state at ``step``, naming ``node``."""
    return Held(step=step, node=node, by=w.claim, gen=w.passes)


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
        out.add(_held(w, w.held, node))
    elif w.stage is Stage.ITERATING:
        if SaveAt.PARTIAL in at or (w.released and SaveAt.IN_PASS in at):
            out.add(_held(w, w.clock, None))
    elif w.stage is Stage.BETWEEN and SaveAt.BETWEEN in at:
        out.add(_held(w, w.held, node))
    elif w.stage is Stage.EXITED and SaveAt.AFTER_EXIT in at:
        kept = w.kind is Kind.EXTENDABLE and not w.released
        out.add(_held(w, w.held, ExitId(w.claim) if kept else None))
    return frozenset(out)


def _completed(ctx: Context, w: Wk, node: Node | None) -> Held | None:
    """A save of the pass closing now's completed state, inside it, as its last act, or
    between passes, where its version may make one (§7, ``save-at``)."""
    at = _wf(ctx, w).save_at
    if {SaveAt.IN_PASS, SaveAt.AT_CLOSE, SaveAt.BETWEEN} & at:
        return _held(w, w.clock, None if w.released else node)
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
    new = [h for h in _each(hs) if h not in slot]
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


def _cut(ctx: Context, w: Wk, slot: frozenset[Held], gaps: Gaps) -> Iterator[Cut]:
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
            piece = PCommit(
                claim=w.claim,
                nth=w.commits,
                epoch=w.epoch,
                parent=w.position,
                gaps=gaps,
            )
            y = w._replace(
                commits=w.commits + 1,
                epoch=w.epoch + 1,
                position=node,
                state=node,
                held=w.clock,
                dating=None,
            )
            made = _held(w, w.clock, node) if landed else None
            yield Cut(y, _made(slot, w, made) if made else slot, (piece,), made)
        if landed and (set(CutSave) - finishes) & stated:
            made = _held(w, w.clock, None)
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
            saved = _between_save(f, x)
            if saved is not None:
                visit(saved, slot, older, pieces, rewound)
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
            new = (
                PCommit(
                    claim=x.claim,
                    nth=x.commits,
                    epoch=x.epoch,
                    parent=x.position,
                    gaps=UNPLACED,
                ),
            )
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
            # a pass closed at the horizon that its loop may continue past: the next
            # pass, and so the commits that follow, only the horizon forbade
            cut: tuple[Piece, ...] = (
                (PHorizon(x.claim),)
                if x.clock == bound == ctx.horizon and _continued(f, s2, y) is not None
                else ()
            )
            visit(y, s2, o2, pieces + new + cut, rewound)

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
            new = (
                PRewind(
                    claim=x.claim,
                    commits=x.commits,
                    epoch=x.epoch,
                    target=node,
                    gaps=UNPLACED,
                ),
            )
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
        for c in _cut(ctx, x, slot, UNPLACED):
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
        for p in _each(older):
            rest = _not_newest(slot, p)
            if rest is not None:
                yield p.step, p.node, rest, (older - {p}) | {p._replace(used=True)}


T = TypeVar("T")


def _each(xs: Iterable[T]) -> list[T]:
    """``xs`` in a fixed order, their ``repr``'s: every set of the engine's own values
    the pass's successors come from is iterated so, so the states' numbering, the paths
    ``written`` takes and the witnesses a chooser reads depend on no hash order. Only
    the engine's own values: their ``repr`` is a function of their fields. A consumer's
    keys are put in the configuration's declared order instead, once (``context``)."""
    return sorted(xs, key=repr)


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
    return ctx.names.may_name(a, w)


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


def _drain(ctx: Context, world: World, w: Wk, upto: int) -> Wk:
    """A native drain that reads the log up to the record at seq ``upto``: it takes in
    every stop posted by then that nothing spent by then (§4; E2)."""
    if w.released:
        return w
    spent = {x.stop for x in world.spent if x.at <= upto}
    new = frozenset(x for s, x in ctx.posted if s <= upto) - spent - w.taken
    return w._replace(taken=w.taken | new) if new else w


def _folds(ctx: Context, k: int) -> list[Fold | None]:
    """The folds a claim of statement ``k`` may make (``fold``): none where no element
    can occur, as in a log whose ``honored`` lists are no writer's word, or with no
    stop. An empty statement says no element occurs; the engine then lets either fold
    stand, and does not check that they agree."""
    if not ctx.names.honored() or not ctx.posted:
        return [None]
    folds: list[Fold | None] = list(_each(ctx.workers[k].fold or Fold))
    return folds


def _drain03(ctx: Context, w: Wk, i: int) -> Wk:
    """The 0.3.0 writer's own drain before record ``i``, what its ``honored`` lists (a
    beat, a beacon, its exit under ``exit``): each stop posted before it is considered
    once, at the first drain after it, and taken in unless an answer had spent it, one
    named before its claim (``at-claim``) or before this drain (``at-drain``)."""
    if w.fold is None:
        return w
    claimed = ctx.by_seq[w.claim]
    spent = ctx.named_before[claimed if w.fold is Fold.AT_CLAIM else i]
    seq = ctx.recs[i].seq
    new = frozenset(x for s, x in ctx.posted if s < seq) - w.considered
    if not new:
        return w
    return w._replace(considered=w.considered | new, took=w.took | (new - spent))


def _watermarks(ctx: Context, i: int, w: Wk) -> set[int]:
    """What a heartbeat at record ``i`` may hold as ``consumed_seq`` (``watermark``)."""
    control = ctx.control_before[i]
    tail = ctx.recs[i - 1].seq if i else 0
    by = {
        Watermark.CONTROL: control,
        Watermark.CLAIMED: control if control > w.claim else 0,
        Watermark.TAIL: tail,
    }
    return {by[x] for x in _wf(ctx, w).watermark}


def _lists(ctx: Context, i: int, w: Wk) -> set[frozenset[str]]:
    """What the worker's own stopped at record ``i`` may list as ``honored``
    (``honored``): what its drains took in, its exit's own drain included (``exit``)
    or not (``ticks``)."""
    by = {
        Honored.EXIT: _drain03(ctx, w, i).took,
        Honored.TICKS: w.took,
    }
    return {frozenset(by[x]) for x in _wf(ctx, w).honored}


def _third_answers(ctx: Context, i: int, rec: Rec, tp: ThirdPartyFacts) -> bool:
    """Whether a third party's stopped at record ``i`` fills ``final_step`` and, where
    it is its word, ``honored`` as the writer's statements say (``final-step``,
    ``honored``)."""
    finals = {ThirdFinal.NULL: None, ThirdFinal.PROGRESS: ctx.progress_before[i]}
    if rec.final_step not in {finals[x] for x in tp.final_step}:
        return False
    if not ctx.names.honored():
        return True
    undischarged = (
        frozenset(x for s, x in ctx.posted if s < rec.seq) - ctx.named_before[i]
    )
    lists = {
        ThirdHonored.NONE: frozenset[str](),
        ThirdHonored.UNDISCHARGED: undischarged,
    }
    return frozenset(rec.honored) in {lists[x] for x in tp.honored}


def _writers(ctx: Context, world: World, rec: Rec) -> Sequence[Wk]:
    """The live workers that may have written a heartbeat or a worker's stopped."""
    return ctx.names.writers(world, rec)


def _before(
    ctx: Context,
    i: int,
    world: World,
    w: Wk,
    rec: Rec,
    *,
    bound: int,
    may_end: bool,
    tick: bool,
) -> list[Run]:
    """Worker ``w``'s silent events before record ``rec`` (index ``i``), each commit
    placed (``_placed``). ``tick`` says the record is the worker's own tick, which dates
    its last close where that stands just before it."""
    runs = _silent(ctx, i, world, w, bound=bound, may_end=may_end, before=rec.seq)
    return list(
        dict.fromkeys(
            placed
            for run in runs
            for placed in _placed(ctx, i, world, w, run, tick=tick)
        )
    )


class _Span(NamedTuple):
    """The gaps between two boundaries: each gap is named by the index of the record
    it follows."""

    boundary: int  # the index of the boundary record before them
    first: int
    last: int


def _interval(ctx: Context, k: int) -> int:
    """The index of the boundary at or before record ``k``."""
    return ctx.boundaries[bisect.bisect_right(ctx.boundaries, k) - 1]


def _spans(ctx: Context, fixed: int, i: int) -> list[_Span]:
    """The boundary intervals that the gaps from the one after record ``fixed`` to the
    one before record ``i`` fall in, each with its first and last gap there."""
    bs = ctx.boundaries
    lo = bisect.bisect_right(bs, fixed) - 1
    hi = bisect.bisect_right(bs, i - 1) - 1
    return [
        _Span(bs[k], max(bs[k], fixed), bs[k + 1] - 1 if k < hi else i - 1)
        for k in range(lo, hi + 1)
    ]


def _gaps(ctx: Context, span: _Span, first: int, last: int) -> Gaps:
    return Gaps(ctx.recs[span.boundary].seq, ctx.recs[first].seq, ctx.recs[last].seq)


def _just_before(ctx: Context, i: int) -> Gaps:
    """The gap just before record ``i``: where a cut there stands."""
    span = _spans(ctx, i - 1, i)[-1]
    return _gaps(ctx, span, i - 1, i - 1)


def _placed(
    ctx: Context, i: int, world: World, w: Wk, run: Run, *, tick: bool
) -> Iterator[Run]:
    """The run's new commits placed, in order, among the boundary intervals the gaps
    from the worker's fixed record to record ``i`` fall in, and each one's native drain
    read there (E2): what a stop or a spend before it gives it. The last close stays
    datable only by the worker's own tick at ``i``: no act of its own came between,
    though other writers' records may have. The worker is fixed at ``i`` after. More
    placements than the cap are refused before any is made."""
    x = run.w
    new = [
        k
        for k, p in enumerate(run.pieces)
        if isinstance(p, (PCommit, PRewind)) and p.gaps == UNPLACED
    ]
    spans = _spans(ctx, w.fixed, i) if new else []
    closes = [k for k in new if isinstance(run.pieces[k], PCommit)]
    last = run.pieces[closes[-1]] if closes else None
    datable = (
        tick
        and x is not None
        and isinstance(last, PCommit)
        and x.dating == last.nth
        and x.claim == last.claim
    )
    if new and math.comb(len(spans) + len(new) - 1, len(new)) > ctx.cap:
        seq = ctx.recs[i].seq if i < len(ctx.recs) else ctx.recs[-1].seq + 1
        raise _too_many(ctx, seq, "in placing one worker's silent run before")
    for choice in itertools.combinations_with_replacement(range(len(spans)), len(new)):
        pieces = list(run.pieces)
        y = x
        for k, j in zip(new, choice):
            g = _gaps(ctx, spans[j], spans[j].first, spans[j].last)
            p = pieces[k]
            assert isinstance(p, (PCommit, PRewind))
            pieces[k] = p._replace(gaps=g)
            if y is not None and isinstance(p, PCommit):
                y = _commit_drain(ctx, world, y, g)
        if y is not None:
            y = y._replace(
                fixed=_interval(ctx, i), dating=y.dating if datable else None
            )
        yield Run(y, run.slot, run.older, tuple(pieces))


def _commit_drain(ctx: Context, world: World, w: Wk, gaps: Gaps) -> Wk:
    """A commit's native drain, where it stands: it reads the log up to the boundary
    before it, since nothing a drain reads changes between two boundaries (E2)."""
    return _drain(ctx, world, w, gaps.after)


def _between_save(f: WorkerFacts, x: Wk) -> Wk | None:
    """A save between passes before anything else the worker does after its close: the
    tick that follows no longer follows the close at once, so it dates nothing (§7,
    "The end-time fork"). The save is among the checkpoint's candidates already
    (``close`` offers it). None where no tick could date the close, or its version
    saves nothing between passes."""
    if x.dating is None or SaveAt.BETWEEN not in f.save_at:
        return None
    return x._replace(dating=None)


def _value(ctx: Context, i: int, world: World, rec: Rec) -> Iterator[Step]:
    """A value: a live worker's, in a role the classifier allows and its phase gives, or
    a stray's (§7, ``strays``)."""
    roles = ctx.roles[rec.seq]
    if _stray(ctx, world, rec, roles):
        yield world, (PValue(rec.seq, None, Role.STRAY, True),)
    bound = rec.step if rec.step is not None else ctx.horizon
    for w in world.workers:
        for run in _before(
            ctx, i, world, w, rec, bound=bound, may_end=False, tick=False
        ):
            assert run.w is not None
            after = world._replace(slot=run.slot, older=run.older)
            for y, pieces in _emit(ctx, run.w, rec, roles):
                for nw in _acted(ctx, i, after, y):
                    yield nw, run.pieces + pieces


def _stray(ctx: Context, world: World, rec: Rec, roles: Sequence[ValueRole]) -> bool:
    """Whether a writer holding no claim may have written the value: the classifier
    allows it, and the latest claim's version's ``strays`` its step or none (no element
    before any claim)."""
    if not any(r.role is Role.STRAY for r in roles):
        return False
    cls = Stray.STEPLESS if rec.step is None else Stray.STEPPED
    return world.version is None or cls in ctx.workers[world.version].strays


_ROLE_ORDER = list(Role)

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
    ctx: Context, w: Wk, rec: Rec, roles: Sequence[ValueRole]
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
        pieces: tuple[Piece, ...] = (PValue(rec.seq, w.claim, role, register),)
        if not register and not _owing(w):
            pieces += (PHold(rec.seq, w.claim, w.epoch),)
        yield y, pieces


def _beat(ctx: Context, i: int, world: World, rec: Rec) -> Iterator[Step]:
    """A heartbeat: with a step, a tick of the worker's pass; without, a beacon. Each is
    a native worker's drain."""
    bound = rec.step if rec.step is not None else ctx.horizon
    for w in _writers(ctx, world, rec):
        for run in _before(
            ctx,
            i,
            world,
            w,
            rec,
            bound=bound,
            may_end=False,
            tick=rec.step is not None,
        ):
            x = run.w
            assert x is not None
            if x.last is Last.CLOSES or x.stage is Stage.EXITED:
                continue
            if rec.consumed not in _watermarks(ctx, i, x):
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
                    pieces += (PDate(x.claim, dates, rec.t, rec.seq),)
                y = x._replace(
                    tick_due=False,
                    ticked=x.clock,
                    beaten=True,
                    ticked_in=x.ticked_in or iterating,
                    acted=x.acted or iterating,
                    last=Last.OTHER,
                    dating=None,
                )
            y = _drain03(ctx, _drain(ctx, world, y, rec.seq - 1), i)
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
        for run in _before(
            ctx, i, world, w, rec, bound=ctx.horizon, may_end=False, tick=False
        ):
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
    if ctx.names.honored() and frozenset(rec.honored) not in _lists(ctx, i, x):
        return
    if not any(
        _final(x, c) == rec.final_step
        for c in _each(f.final_step if clean else f.error_final_step)
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
    for c in _cut(ctx, x, slot, _just_before(ctx, i)):
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
                held = _held(x, x.clock, ExitId(x.claim))
                s = (s - set(made)) | {held}
                made = [held]
            z = _drain(ctx, world, z, rec.seq - 1)._replace(
                kind=kind, held=x.clock if keeps else c.w.held
            )
            honored = tuple(sorted(z.taken))
            out = pieces + (
                PStop(rec.seq, x.claim, Author.EXIT, None),
                PExit(
                    claim=x.claim,
                    seq=rec.seq,
                    epoch=z.epoch,
                    parent=z.position,
                    kind=kind,
                    discarded=not clean or discards,
                    honored=honored,
                ),
            )
            nworld = nworld._replace(
                spent=world.spent | {Spent(x, rec.seq) for x in z.taken},
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
            withdrawn=world.withdrawn | set(ids),
            spent=world.spent | {Spent(x, rec.seq) for x in ids},
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
    if not _third_answers(ctx, i, rec, tp):
        return
    for prior, before in _ahead_of(ctx, i, world, rec):
        state = _state(prior, rec.claim)
        if Acts(state, _ACTS[type(act)]) not in tp.acts:
            continue
        pieces = before + (PStop(rec.seq, rec.claim, Author.THIRD, act),)
        if isinstance(act, Release):
            if rec.claim is None or act.outcome not in tp.outcome:
                continue
            for via in _each(
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
    that the stop finds it dead. A commit the stop's withdrawals would come too late for
    needs no run here: it is placed where it stands when its worker is next read, and
    its drain reads there (``_placed``)."""
    named = next(
        (w for w in world.workers if w.claim == rec.claim and not _owing(w)), None
    )
    if named is None:
        yield world, ()
        return
    for run in _before(
        ctx, i, world, named, rec, bound=ctx.horizon, may_end=True, tick=False
    ):
        yield world._replace(
            workers=_put(world, run.w, named.claim), slot=run.slot, older=run.older
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
    for c in _cut(ctx, w, world.slot, _just_before(ctx, i)):
        z = c.w._replace(released=True, dating=None, fixed=_interval(ctx, i))
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
            for run in _before(
                ctx, i, wd, cur, rec, bound=ctx.horizon, may_end=True, tick=False
            ):
                if run.w is not None and ends:
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
            assert world.version is not None  # the latest claim's
            if Takeover.DEAD not in ctx.workers[world.version].takeover:
                return
        yield from _reported(ctx, i, world, rec, pieces, p)
        return
    if free:
        yield from _reported(ctx, i, world, rec, pieces, p)
        return
    if Takeover.LIVE not in _wf(ctx, w).takeover:
        return
    for c in _cut(ctx, w, world.slot, _just_before(ctx, i)):
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
        for p in _each(world.older):
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
    for k, fold in ((k, x) for k in ctx.versions[c] for x in _folds(ctx, k)):
        f = ctx.workers[k]
        for ld in _loads(ctx, f, world):
            loaded = 0 if ld.step is None else ld.step
            for idle in _idles(f, loaded):
                w = Wk(
                    claim=c,
                    facts=k,
                    handle=_handle(rec),
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
                    fixed=i,
                    fold=fold,
                    considered=frozenset(),
                    took=frozenset(),
                )
                nworld = world._replace(
                    workers=_put(world, w, c),
                    latest=c,
                    version=k,
                    slot=ld.slot,
                    older=ld.older,
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
            raw = _silent(
                ctx, n, wd, cur, bound=ctx.horizon, may_end=True, before=after
            )
            for run in (
                p for r in raw for p in _placed(ctx, n, wd, cur, r, tick=False)
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


@dataclass(frozen=True)
class Graph:
    """The pass's layered graph, kept as its live edges: ``live[i]`` lead from the
    states before record ``i`` to those after it, and the last entry to the end (one
    state, 0), each on some complete path.
    """

    ctx: Context
    widths: tuple[int, ...]  # the states in each layer
    live: tuple[tuple[Edge, ...], ...]
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
        # no later record reads a step: every clock and every save's step is one. A
        # null final_step still reads whether a pass began, finished or ticked, so
        # each of those stays null or not (the review's Important 2)
        world = world._replace(
            workers=tuple(
                w._replace(
                    clock=0,
                    floor=0,
                    held=0,
                    begun=_zero(w.begun),
                    finished=_zero(w.finished),
                    ticked=_zero(w.ticked),
                )
                for w in world.workers
            ),
            slot=frozenset(
                h._replace(step=0) if h.step >= 0 else h for h in world.slot
            ),
        )
    return world


def _zero(step: int | None) -> int | None:
    return None if step is None else 0


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
    edges: list[Sequence[Edge]] = []
    widths = [1]
    held = 1  # the states summed over the layers so far
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
        held += len(nxt)
        if held > ctx.budget:
            raise Refused(
                f"more than {ctx.budget} states summed over the layers by the record "
                f"at seq {ctx.recs[i].seq}: the stated facts leave too many histories "
                "to keep",
                (f"seq {ctx.recs[i].seq}",),
            )
        edges.append(list(es))
        layer = nxt
        if not nxt:
            return Graph(ctx, tuple(widths), tuple(() for _ in edges), i, max(widths))
    final = list(
        dict.fromkeys(
            Edge(sid, 0, pieces)
            for world, sid in layer.items()
            for pieces in _end(ctx, world)
        )
    )
    edges.append(final)
    if not final:
        return Graph(
            ctx, tuple(widths), tuple(() for _ in edges), len(ctx.recs), max(widths)
        )
    return Graph(ctx, tuple(widths), _alive(edges), None, max(widths))


def admits(g: Graph) -> bool:
    return g.stopped_at is None


def candidates(
    rows: Sequence[Row],
    configuration: Configuration,
    *,
    born: Born,
    cap: int,
    budget: int,
    run_on: int,
) -> Graph:
    """The candidate set of one run's rows: the pass's graph, whose complete paths are
    the candidates. ``cap`` bounds the states held at once; ``budget`` the states held
    summed over every layer; ``run_on`` how far past the log's steps a run of passes no
    record pins is followed, a claim."""
    return build(
        context(rows, configuration, born=born, cap=cap, budget=budget, run_on=run_on)
    )


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
    """A rewind after ``commits`` commits of its claim, closing its ``epoch``: two
    rewinds of one claim may follow the same count, and the epoch orders them."""

    claim: int
    commits: int
    epoch: int
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


EndChooser: TypeAlias = Callable[[CommitId, float, tuple[float, ...]], float]
"""An undated commit's end time, from its lower bound and its upper bounds where it is
written, earliest first: the end-time reading's ``EndedAt`` (§7)."""


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
    ctx: Context, own: Sequence[int], gaps: Gaps
) -> tuple[float, tuple[float, ...]]:
    """An undated commit's lower bound, the newest ``t`` among its claim's own records
    at or before it; and its upper bounds, the ``t`` of the record after each gap it may
    stand in, earliest first (none after the log's last record). It stands after its
    claim's own last record before it, whatever its gaps say."""
    last = max(s for s in own if s <= gaps.last)
    low = ctx.recs[ctx.by_seq[last]].t
    g0, g1 = ctx.by_seq[max(gaps.first, last)], ctx.by_seq[gaps.last]
    n = len(ctx.recs)
    return low, tuple(ctx.recs[g + 1].t for g in range(g0, g1 + 1) if g + 1 < n)


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
                low, highs = bounds(ctx, own[p.claim], p.gaps)
                out[CommitId(p.claim, p.nth)] = frozenset({low, *highs})
    return out


def lineage(ctx: Context, pieces: Iterable[Piece], *, when: EndChooser) -> Lineage:
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
                low, highs = bounds(ctx, own[p.claim], p.gaps)
                t = when(CommitId(p.claim, p.nth), low, highs)
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
    # each sorted by what names it on a path, never by a node it may lack: a rewind's
    # target or a start's resume point may be None beside a node
    return Lineage(
        tuple(
            sorted(
                (Start(p.claim, p.resume) for p in ps if isinstance(p, PStart)),
                key=lambda s: s.claim,
            )
        ),
        tuple(sorted(commits, key=lambda c: c.node)),
        tuple(
            sorted(
                (
                    RewindTo(p.claim, p.commits, p.epoch, p.target)
                    for p in ps
                    if isinstance(p, PRewind)
                ),
                key=lambda x: (x.claim, x.commits, x.epoch),
            )
        ),
        tuple(sorted(exits, key=lambda x: x.node)),
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
    """Span: a claim's commits (each its parent, its values and its placement, the seq
    of the boundary it stands after), its rewinds, and its exit node's content."""

    commits: tuple[tuple[int, Node | None, tuple[int, ...], int], ...]
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
        p,
        (
            PStart,
            PCommit,
            PDate,
            PHold,
            PRewind,
            PExit,
            PRunEnd,
            PDied,
            PFence,
            PHorizon,
        ),
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
    commits: list[tuple[int, Node | None, tuple[int, ...], int]] = []
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
            commits.append(
                (p.nth, p.parent, tuple(sorted(holds[p.epoch])), p.gaps.after)
            )
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


def subjects(g: Graph) -> list[int]:
    """The claims, last first, then the run: the decode's order."""
    claims = sorted(
        (r.seq for r in g.ctx.recs if r.topic is Topic.STARTED), reverse=True
    )
    return claims + [RUN]


def _alive(live: list[Sequence[Edge]]) -> tuple[tuple[Edge, ...], ...]:
    """The edges on some complete path: forward from the start, backward from the
    end. It filters ``live`` in place, layer by layer, so that it holds one copy of the
    edges beside the caller's, never two."""
    reach: set[int] = {0}
    for k, es in enumerate(live):
        kept = tuple(e for e in es if e.src in reach)
        live[k] = kept
        reach = {e.dst for e in kept}
    alive: set[int] = {0}
    for k in range(len(live) - 1, -1, -1):
        kept = tuple(e for e in live[k] if e.dst in alive)
        live[k] = kept
        alive = {e.src for e in kept}
    return tuple(tuple(es) for es in live)


class Projection(Enum):
    """The projections the decode reads, in its order for each subject: the approved
    architecture's three, then who wrote a heartbeat (``Writer``), which only a log
    born in 0.2.0 leaves open and which no other reading depends on."""

    END = "end"
    SPAN = "span"
    ORIGIN = "origin"
    WRITER = "writer"


class Resumes(NamedTuple):
    """What Origin reads of a claim's start: its ``resume_from`` alone."""

    claim: int
    resume: Node | None


Read: TypeAlias = (
    Resumes
    | PDied
    | PWithdrawal
    | PTerminated
    | PCommit
    | PHold
    | PRewind
    | PExit
    | PBeat
)


class Key(NamedTuple):
    """Where a fork is asked: a subject, a projection, and a position. A record's own
    pieces stand at its index; a commit or a rewind a silent run made, at its
    placement's boundary, wherever the pass read it in: so one commit is one question,
    whichever later record its worker was read at."""

    subject: int
    projection: Projection
    position: int


def _read(ctx: Context, p: Piece, layer: int) -> tuple[Key, Read] | None:
    """What a ``Reading`` reads of one piece at layer ``layer``: where it is asked, and
    the piece cut to what is read. None for a piece no reading reads: who wrote a value,
    a stopped's author, a fence, a run's end, a commit's date (end times stand apart).

    Who wrote a heartbeat is the Writer projection's: 0.4.0 writes it as its
    ``claim_seq``, which the folds read (``await_consumed`` takes a claim's watermarks
    from its heartbeats, the Watcher seeds a claim's staleness from them). A log born
    in 0.3.0 names it; in one born in 0.2.0 any live worker may have written it
    (``InferredNames``)."""
    if isinstance(p, PStart):
        return Key(p.claim, Projection.ORIGIN, layer), Resumes(p.claim, p.resume)
    if isinstance(p, (PDied, PWithdrawal, PTerminated)):
        return Key(_subject(ctx, p), Projection.END, layer), p
    if isinstance(p, (PCommit, PRewind)):
        # where in its interval it stands, and its end time, are no part of Span
        at = Gaps(p.gaps.after, p.gaps.after, p.gaps.after)
        key = Key(p.claim, Projection.SPAN, ctx.by_seq[p.gaps.after])
        return key, p._replace(gaps=at)
    if isinstance(p, (PHold, PExit)):
        return Key(p.claim, Projection.SPAN, layer), p
    if isinstance(p, PBeat):
        return Key(p.writer, Projection.WRITER, layer), p
    if isinstance(p, (PValue, PStop, PFence, PRunEnd, PDate, PHorizon)):
        return None
    assert_never(p)


def _mine(ctx: Context, e: Edge, layer: int, key: Key) -> tuple[Read, ...]:
    """What one edge decides at ``key``."""
    out: list[Read] = []
    for p in e.pieces:
        r = _read(ctx, p, layer)
        if r is not None and r[0] == key:
            out.append(r[1])
    return tuple(out)


@dataclass(frozen=True)
class Answer:
    """One way the paths left read a subject's projection at one position, and one
    complete path's pieces that read it so (its witness), found when asked for. Two
    answers are equal when their values are."""

    value: tuple[Read, ...]
    find: Callable[[], tuple[Piece, ...]] = field(compare=False, repr=False)

    def witness(self) -> tuple[Piece, ...]:
        return self.find()


class Question(NamedTuple):
    """A fork: the paths read a subject's projection at one position more than one
    way."""

    key: Key
    answers: tuple[Answer, ...]


def _witness(g: Graph, k: int, e: Edge) -> tuple[Piece, ...]:
    """One complete path's pieces through edge ``e`` of layer ``k``: back to the start
    and on to the end, along any live edge."""
    back: list[tuple[Piece, ...]] = []
    state = e.src
    for j in range(k - 1, -1, -1):
        prev = next(x for x in g.live[j] if x.dst == state)
        back.append(prev.pieces)
        state = prev.src
    ahead: list[tuple[Piece, ...]] = []
    state = e.dst
    for j in range(k + 1, len(g.live)):
        nxt = next(x for x in g.live[j] if x.src == state)
        ahead.append(nxt.pieces)
        state = nxt.dst
    parts = list(reversed(back)) + [e.pieces] + ahead
    return tuple(p for part in parts for p in part)


def witness(g: Graph) -> tuple[Piece, ...]:
    """One complete path's pieces: the answer itself, where ``forked`` finds no
    fork."""
    assert admits(g)
    return _witness(g, 0, g.live[0][0])


def written(g: Graph) -> tuple[tuple[Piece, ...], ...]:
    """The path the step writes once every fork is decided, the end-time fork's
    included: one complete path, as its edge's pieces at each layer, the last the log's
    end. The paths that remain write the same records: they differ only where no byte
    is written (which writer wrote a value, whose record names none; who wrote a
    ``stopped`` that becomes nothing, a fenced worker or a third party noting; how far
    a commit's gaps run past where it stands), and the step reads a divergence off
    every path left, never off this one alone. Each layer takes the edge whose pieces
    come first by ``repr``, then the first state in the pass's own numbering, which
    the pass keeps free of hash order (``_each``, ``context``)."""
    assert admits(g)
    out: list[tuple[Piece, ...]] = []
    state = 0
    for es in g.live:
        e = min(
            (x for x in es if x.src == state), key=lambda x: (repr(x.pieces), x.dst)
        )
        out.append(e.pieces)
        state = e.dst
    return tuple(out)


def reaching(g: Graph) -> frozenset[int]:
    """The claims some candidate closes a pass of at the horizon, its loop free to
    continue: the commits of a longer run, which no record pins, were cut there."""
    return frozenset(
        p.claim
        for es in g.live
        for e in es
        for p in e.pieces
        if isinstance(p, PHorizon)
    )


# ----- the end-time fork: a commit's dating, then an undated one's time ----------------


class Tick(NamedTuple):
    """A tick that may date a commit: its heartbeat's seq and ``t``."""

    seq: int
    t: float


class Dating(NamedTuple):
    """How a commit's end may be dated on the paths left: by each tick in ``ticks``, or
    by none (``undated``), when its end time is a chooser's."""

    ticks: tuple[Tick, ...]
    undated: bool


def _forward(g: Graph) -> list[dict[int, int]]:
    """Per layer, each state's count of paths to it from the start."""
    fwd: list[dict[int, int]] = [{0: 1}]
    for es in g.live:
        nxt: dict[int, int] = defaultdict(int)
        for e in es:
            nxt[e.dst] += fwd[-1].get(e.src, 0)
        fwd.append(nxt)
    return fwd


def datings(g: Graph) -> dict[CommitId, Dating]:
    """Each commit's datings on the paths left, by counting paths: a path holds a commit
    once and dates it at most once, so it is undated on some path exactly when fewer
    paths date it than hold it."""
    fwd = _forward(g)
    bwd: list[dict[int, int]] = [{} for _ in g.live] + [{0: 1}]
    for k in range(len(g.live) - 1, -1, -1):
        for e in g.live[k]:
            bwd[k][e.src] = bwd[k].get(e.src, 0) + bwd[k + 1].get(e.dst, 0)
    held: dict[CommitId, int] = defaultdict(int)
    dated: dict[CommitId, dict[Tick, int]] = defaultdict(lambda: defaultdict(int))
    for k, es in enumerate(g.live):
        for e in es:
            n = fwd[k].get(e.src, 0) * bwd[k + 1].get(e.dst, 0)
            if not n:
                continue
            for p in e.pieces:
                if isinstance(p, PCommit):
                    held[CommitId(p.claim, p.nth)] += n
                elif isinstance(p, PDate):
                    dated[CommitId(p.claim, p.nth)][Tick(p.seq, p.t)] += n
    return {
        c: Dating(tuple(sorted(dated[c])), sum(dated[c].values()) < n)
        for c, n in held.items()
    }


def _keep(g: Graph, keep: Callable[[int, Edge], bool]) -> Graph:
    live: list[Sequence[Edge]] = [
        tuple(e for e in es if keep(k, e)) for k, es in enumerate(g.live)
    ]
    return replace(g, live=_alive(live))


def dated_by(g: Graph, commit: CommitId, tick: Tick) -> Graph:
    """The paths on which ``tick`` dates ``commit``."""
    layer = g.ctx.by_seq[tick.seq]

    def keep(k: int, e: Edge) -> bool:
        return k != layer or any(
            isinstance(p, PDate) and (p.claim, p.nth, p.seq) == (*commit, tick.seq)
            for p in e.pieces
        )

    return _keep(g, keep)


def _dates(e: Edge, silent: Silent) -> bool:
    """Whether the edge's tick dates the commit ``silent``: such a path leaves it no
    place of its own (a rewind no tick dates)."""
    return isinstance(silent, CommitId) and any(
        isinstance(p, PDate) and (p.claim, p.nth) == tuple(silent) for p in e.pieces
    )


class Place(NamedTuple):
    """Where an undated commit stands on a path: just after the record at seq
    ``after``, the latest of its interval's first record and its claim's own last
    record before it; and its end's lower bound there, the ``t`` of that own record
    (``low``)."""

    after: int
    low: float


class _Track(NamedTuple):
    """What a path's prefix shows of where an undated commit stands: its gaps once its
    piece has passed (``UNPLACED`` before), and, per ``last`` its gaps may have
    (``checkpoints``), the seq of its claim's own latest record at or before that one
    so far (-1 for none)."""

    gaps: Gaps
    marks: tuple[int, ...]


def _names(p: Piece, x: Silent) -> bool:
    """Whether ``p`` is the piece of the commit or the rewind ``x``."""
    if isinstance(x, CommitId):
        return isinstance(p, PCommit) and p.claim == x.claim and p.nth == x.nth
    return isinstance(p, PRewind) and p.claim == x.claim and p.epoch == x.epoch


def reloads(g: Graph) -> frozenset[RewindId]:
    """Every rewind some path left holds."""
    return frozenset(
        RewindId(p.claim, p.epoch)
        for es in g.live
        for e in es
        for p in e.pieces
        if isinstance(p, PRewind)
    )


def _own_seq(p: Piece, claim: int) -> int | None:
    """The seq of the record piece ``p`` makes ``claim``'s own (``_own``), if it does."""
    if isinstance(p, PStart):
        return p.claim if p.claim == claim else None
    if isinstance(p, (PValue, PBeat)):
        return p.seq if p.writer == claim else None
    if isinstance(p, PStop):
        return p.seq if p.claim == claim and p.author is not Author.THIRD else None
    return None


class _Placing:
    """One commit's or rewind's place, followed along the paths: a record's own pieces
    stand at its layer, so once a path is past the piece and past the layer of the
    latest ``last`` any of its gaps have, its place is settled."""

    def __init__(self, g: Graph, silent: Silent) -> None:
        self.ctx = g.ctx
        self.silent = silent
        self.checkpoints = tuple(
            sorted(
                {
                    p.gaps.last
                    for es in g.live
                    for e in es
                    if not _dates(e, silent)
                    for p in e.pieces
                    if isinstance(p, (PCommit, PRewind)) and _names(p, silent)
                }
            )
        )
        self.settles = g.ctx.by_seq[self.checkpoints[-1]]
        self.start = _Track(UNPLACED, (-1,) * len(self.checkpoints))

    def advance(self, track: _Track, k: int, e: Edge) -> _Track | Place:
        """The track after edge ``e`` of layer ``k``, or the place once settled."""
        gaps, marks = track.gaps, list(track.marks)
        for p in e.pieces:
            s = _own_seq(p, self.silent.claim)
            if s is not None:
                assert s == self.ctx.recs[k].seq  # a record's own pieces, at its layer
                for j, last in enumerate(self.checkpoints):
                    if marks[j] < s <= last:
                        marks[j] = s
            elif isinstance(p, (PCommit, PRewind)) and _names(p, self.silent):
                gaps = p.gaps
        if gaps == UNPLACED or k < self.settles:
            return _Track(gaps, tuple(marks))
        m = marks[self.checkpoints.index(gaps.last)]
        return Place(max(gaps.first, m), self.ctx.recs[self.ctx.by_seq[m]].t)


def places(g: Graph, silent: Silent) -> frozenset[Place]:
    """Every place the commit or rewind ``silent`` stands undated on some path left,
    each with its lower bound: a forward pass carrying, per state, the tracks reaching
    it, which a path whose tick dates the commit leaves (``_dates``). A track settled at
    a state is a place some complete path has, since every live edge is on one."""
    placing = _Placing(g, silent)
    found: set[Place] = set()
    at: dict[int, set[_Track]] = {0: {placing.start}}
    for k, es in enumerate(g.live):
        nxt: dict[int, set[_Track]] = defaultdict(set)
        for e in es:
            if _dates(e, silent):
                continue
            for track in at.get(e.src, ()):
                y = placing.advance(track, k, e)
                if isinstance(y, Place):
                    found.add(y)
                else:
                    nxt[e.dst].add(y)
        at = nxt
        if not at:
            break
    return frozenset(found)


def ended_at(g: Graph, silent: Silent, after: int, t: float) -> Graph:
    """The paths on which no tick dates the commit or rewind ``silent`` (``_dates``),
    and it stands just after the record at seq ``after``, with its lower bound at or
    before ``t``. Until a path's place is settled its state is split by its track, a
    function of the path, so each path is kept once; it is whole again after. It builds
    one list of edges beside ``g``'s and narrows that in place, never a third."""
    placing = _Placing(g, silent)
    split: dict[tuple[int, int, _Track], int] = {}
    # below every id the graph has, which an earlier split may have made negative
    floor = min((min(e.src, e.dst) for es in g.live for e in es), default=0)

    def sid(k: int, state: int, track: _Track) -> int:
        """A split state's id in layer ``k``, apart from every whole one's."""
        return split.setdefault((k, state, track), min(floor, 0) - 1 - len(split))

    live: list[Sequence[Edge]] = []
    at: dict[int, set[_Track]] = {0: {placing.start}}
    whole: set[int] = set()
    for k, es in enumerate(g.live):
        kept: list[Edge] = []
        nxt: dict[int, set[_Track]] = defaultdict(set)
        then: set[int] = set()
        for e in es:
            if _dates(e, silent):
                continue
            if e.src in whole:
                kept.append(e)
                then.add(e.dst)
            for track in sorted(at.get(e.src, ())):
                y = placing.advance(track, k, e)
                src = e.src if k == 0 else sid(k, e.src, track)
                if isinstance(y, Place):
                    if y.after == after and y.low <= t:
                        kept.append(Edge(src, e.dst, e.pieces))
                        then.add(e.dst)
                elif k + 1 < len(g.live):
                    kept.append(Edge(src, sid(k + 1, e.dst, y), e.pieces))
                    nxt[e.dst].add(y)
        live.append(tuple(dict.fromkeys(kept)))
        at, whole = nxt, then
    return replace(g, live=_alive(live))


def _windows(g: Graph) -> dict[Key, tuple[int, int]]:
    """Each key's window: the first and last layer whose edges read it."""
    out: dict[Key, tuple[int, int]] = {}
    for k, es in enumerate(g.live):
        for e in es:
            for p in e.pieces:
                r = _read(g.ctx, p, k)
                if r is not None:
                    lo, _ = out.get(r[0], (k, k))
                    out[r[0]] = (lo, k)
    return out


def _values(
    g: Graph, key: Key, window: tuple[int, int], *, cap: int
) -> set[tuple[Read, ...]]:
    """What the paths read at ``key``: each path's reads there, in order, gathered over
    the key's window, per state. Raises ``TooMany`` past ``cap`` at a state."""
    lo, hi = window
    at: dict[int, set[tuple[Read, ...]]] = {e.src: {()} for e in g.live[lo]}
    for k in range(lo, hi + 1):
        nxt: dict[int, set[tuple[Read, ...]]] = defaultdict(set)
        for e in g.live[k]:
            mine = _mine(g.ctx, e, k, key)
            for v in at.get(e.src, ()):
                nxt[e.dst].add(v + mine)
        if any(len(vs) > cap for vs in nxt.values()):
            raise TooMany(f"more than {cap} answers at {key} reach one state")
        at = nxt
    return {v for vs in at.values() for v in vs}


def _restrict(
    g: Graph, key: Key, window: tuple[int, int], value: tuple[Read, ...]
) -> Graph:
    """The graph of the complete paths that read ``value`` at ``key``. Inside the key's
    window a state is split by what the path read there so far, a prefix of ``value``;
    it is whole again after."""
    lo, hi = window
    split: dict[tuple[int, int, tuple[Read, ...]], int] = {}

    def sid(k: int, state: int, prefix: tuple[Read, ...]) -> int:
        """A split state's id in layer ``k``, apart from every whole one's."""
        return split.setdefault((k, state, prefix), -1 - len(split))

    live: list[Sequence[Edge]] = list(g.live)
    at: dict[int, set[tuple[Read, ...]]] = {e.src: {()} for e in g.live[lo]}
    for k in range(lo, hi + 1):
        kept: list[Edge] = []
        nxt: dict[int, set[tuple[Read, ...]]] = defaultdict(set)
        for e in g.live[k]:
            mine = _mine(g.ctx, e, k, key)
            for prefix in _each(at.get(e.src, ())):
                reached = prefix + mine
                if reached != value[: len(reached)]:
                    continue
                if k == hi and reached != value:
                    continue
                src = e.src if k == lo else sid(k, e.src, prefix)
                dst = e.dst if k == hi else sid(k + 1, e.dst, reached)
                kept.append(Edge(src, dst, e.pieces))
                nxt[e.dst].add(reached)
        live[k] = tuple(dict.fromkeys(kept))
        at = nxt
    return replace(g, live=_alive(live))


def _witnessed(
    g: Graph, key: Key, window: tuple[int, int], value: tuple[Read, ...]
) -> tuple[Piece, ...]:
    """A complete path that reads ``value`` at ``key``."""
    return witness(_restrict(g, key, window, value))


def _question(
    g: Graph, key: Key, window: tuple[int, int], *, cap: int
) -> Question | None:
    """The fork at ``key``, if the paths read it more than one way, each answer with a
    witness."""
    lo, hi = window
    if lo == hi:  # read off one layer: each answer's witness runs through its edge
        by: dict[tuple[Read, ...], Edge] = {}
        for e in g.live[lo]:
            by.setdefault(_mine(g.ctx, e, lo, key), e)
        if len(by) < 2:
            return None
        return Question(
            key,
            tuple(
                Answer(v, partial(_witness, g, lo, e))
                for v, e in sorted(by.items(), key=lambda kv: repr(kv[0]))
            ),
        )
    values = sorted(_values(g, key, window, cap=cap), key=repr)
    if len(values) < 2:
        return None
    answers = tuple(Answer(v, partial(_witnessed, g, key, window, v)) for v in values)
    return Question(key, answers)


def _order(g: Graph, windows: Mapping[Key, tuple[int, int]]) -> list[Key]:
    """The keys in the decode's order: subjects last claim first, then the run; for
    each, End, Span, Origin, Writer; positions in log order."""
    rank = {s: k for k, s in enumerate(subjects(g))}
    projections = list(Projection)
    return sorted(
        windows,
        key=lambda x: (rank[x.subject], projections.index(x.projection), x.position),
    )


def questions(g: Graph, *, cap: int) -> list[Question]:
    """Every fork of the candidate set, per subject, projection and position, each with
    its answers and their witnesses, in the decode's order: what is reported of an
    ambiguous run. Each is read off the key's own window of the graph: the forks are
    counted apart, never multiplied into whole readings."""
    windows = _windows(g)
    out: list[Question] = []
    for key in _order(g, windows):
        q = _question(g, key, windows[key], cap=cap)
        if q is not None:
            out.append(q)
    return out


def forked(g: Graph, *, cap: int) -> frozenset[Key]:
    """The keys the candidates fork at: where ``questions`` finds a fork, without its
    witnesses."""
    windows = _windows(g)
    return frozenset(
        key for key, w in windows.items() if len(_values(g, key, w, cap=cap)) > 1
    )


Chooser: TypeAlias = Callable[[Question], Answer | None]
"""A chooser picks one answer to a fork, or None to refuse (§7; Task 21 ships them)."""


class Undecided(MigrationError):
    """A chooser refused a fork."""

    def __init__(self, message: str, question: Question) -> None:
        super().__init__(message)
        self.question = question


def decode(g: Graph, choose: Chooser, *, cap: int) -> Graph:
    """The decode's stated rule: from the last claim back to the first, then the run;
    for each, its End, its Span, its Origin, then its Writer; within a projection,
    position by position in log order. Where the paths left read a subject's projection
    at a position more than one way, the chooser picks one answer, and only the paths
    through it are kept before the next fork is read. It follows the graph choice by
    choice: a fork's answers are gathered over its own window, never whole paths."""
    windows = _windows(g)
    for key in _order(g, windows):
        q = _question(g, key, windows[key], cap=cap)
        if q is None:
            continue
        pick = choose(q)
        if pick is None:
            raise Undecided(
                f"the chooser refused the fork of claim {key.subject}'s "
                f"{key.projection.value} at record {key.position}",
                q,
            )
        if pick.value not in {a.value for a in q.answers}:
            raise MigrationError("the chooser picked no answer of the fork")
        g = _restrict(g, key, windows[key], pick.value)
    return g


def paths(g: Graph, *, cap: int) -> list[tuple[tuple[Piece, ...], ...]]:
    """Every complete path, as the pieces of its edge at each layer, once per distinct
    sequence. Raises ``TooMany`` past ``cap`` sequences at a state. For a log small
    enough to list: the decode itself never lists paths."""
    at: dict[int, set[tuple[tuple[Piece, ...], ...]]] = {0: {()}}
    for es in g.live:
        nxt: dict[int, set[tuple[tuple[Piece, ...], ...]]] = defaultdict(set)
        for e in es:
            for seq in at.get(e.src, ()):
                nxt[e.dst].add(seq + (e.pieces,))
        if any(len(xs) > cap for xs in nxt.values()):
            raise TooMany(f"more than {cap} distinct paths reach one state")
        at = nxt
    return sorted(at.get(0, set()), key=repr)


# ----- a refusal's names --------------------------------------------------------------


class WorkerFact(Enum):
    """A worker fact a refusal may name: a field of ``WorkerFacts``, by name."""

    STRIDE = "stride"
    LOADING = "loading"
    VALUES = "values"
    BEAT_POS = "beat_pos"
    BEAT_COV = "beat_cov"
    MARKER = "marker"
    CLOSING = "closing"
    LOOP_END = "loop_end"
    DISCARDS = "discards"
    FINAL_STEP = "final_step"
    ERROR_FINAL_STEP = "error_final_step"
    CUT_SAVE = "cut_save"
    SAVE_AT = "save_at"
    SAVE_COV = "save_cov"
    LEAVE_SAVE = "leave_save"
    LOAD = "load"
    REWINDS = "rewinds"
    TAKEOVER = "takeover"
    STRAYS = "strays"
    FOLD = "fold"
    HONORED = "honored"
    WATERMARK = "watermark"


class ThirdFact(Enum):
    """A third-party fact a refusal may name: a field of ``ThirdPartyFacts``."""

    ACTS = "acts"
    VIA = "via"
    OUTCOME = "outcome"
    FINAL_STEP = "final_step"
    HONORED = "honored"


class Classified(Enum):
    """A classifier answer a refusal may name: one method of its reader."""

    STARTED = "started"
    STOPPED = "stopped"
    VALUE = "value"


# The cross-claim state (§7's "feeds"): what a later claim reads of an earlier one.
_CROSS: frozenset[WorkerFact | ThirdFact] = frozenset(
    {
        WorkerFact.STRIDE,
        WorkerFact.CUT_SAVE,
        WorkerFact.SAVE_AT,
        WorkerFact.SAVE_COV,
        WorkerFact.LEAVE_SAVE,
        WorkerFact.LOAD,
        WorkerFact.REWINDS,
        WorkerFact.TAKEOVER,
        ThirdFact.ACTS,
    }
)
# What Span's pieces depend on: which workers live at each record and what each one's
# clock reads, from the cross-claim state (a takeover's survivor, a load) and the
# worker's own program (its passes, its values, its exit).
_SPAN_FEEDS: frozenset[WorkerFact | ThirdFact] = _CROSS | {
    WorkerFact.LOADING,
    WorkerFact.VALUES,
    WorkerFact.BEAT_POS,
    WorkerFact.BEAT_COV,
    WorkerFact.MARKER,
    WorkerFact.CLOSING,
    WorkerFact.LOOP_END,
    WorkerFact.DISCARDS,
    WorkerFact.FINAL_STEP,
    WorkerFact.ERROR_FINAL_STEP,
    WorkerFact.STRAYS,
}
FEEDS: Mapping[Projection, frozenset[WorkerFact | ThirdFact]] = {
    Projection.END: _CROSS
    | {
        WorkerFact.VALUES,
        WorkerFact.FINAL_STEP,
        WorkerFact.ERROR_FINAL_STEP,
        ThirdFact.VIA,
        ThirdFact.OUTCOME,
    },
    Projection.SPAN: _SPAN_FEEDS,
    Projection.ORIGIN: _CROSS | {WorkerFact.LOADING},
    # who wrote a heartbeat is a live worker whose clock the tick's step matches (a
    # beacon's any live worker) and whose watermark convention admits its consumed_seq
    # (``_beat``): the liveness and the clock are what Span's pieces depend on, and the
    # watermark is the one encoding that filters writers directly
    Projection.WRITER: _SPAN_FEEDS | {WorkerFact.WATERMARK},
}
"""The facts whose value can decide each projection: §7's "feeds" column, every fact
feeding the cross-claim state included, since a later claim reads it. An
over-approximation of the facts that settle a fork there."""


DATES: frozenset[WorkerFact | ThirdFact] = frozenset(
    {WorkerFact.BEAT_POS, WorkerFact.CLOSING, WorkerFact.MARKER, WorkerFact.CUT_SAVE}
)
"""The facts whose value can decide whether a tick dates a commit: where the worker
ticks, whether its pass closes at once after a value, and where a cut pass ends."""


def settled_by(
    configuration: Configuration, feeding: frozenset[WorkerFact | ThirdFact]
) -> tuple[str, ...]:
    """The facts whose statement could settle a fork the ``feeding`` facts decide
    (``FEEDS[projection]``, or ``DATES``): each some version states with more than one
    class. An over-approximation: stating one may leave the fork open."""
    out: set[str] = set()
    for n in feeding:
        versions: Iterable[WorkerFacts | ThirdPartyFacts] = (
            configuration.workers.values()
            if isinstance(n, WorkerFact)
            else configuration.third_parties.values()
        )
        if any(len(getattr(f, n.value)) > 1 for f in versions):
            out.add(n.value.replace("_", "-"))
    return tuple(sorted(out))


Named: TypeAlias = WorkerFact | ThirdFact | Classified


def domain(owner: type, field: str) -> frozenset[Any]:
    """A fact's whole domain, from its field's declared type: every member of its
    element's enum; for ``acts``, every state and act but an unnamed release."""
    (element,) = get_args(get_type_hints(owner)[field])
    if element is Acts:
        return frozenset(
            Acts(s, a)
            for s in ClaimState
            for a in Act
            if not (s is ClaimState.UNNAMED and a is Act.RELEASE)
        )
    return frozenset(element)


class _Passing:
    """A reader that passes every answer on: each relaxed reader below opens one."""

    def __init__(self, inner: LogReader, configuration: Configuration) -> None:
        self._inner = inner
        self._cfg = configuration

    def started(self, seq: int) -> frozenset[Hashable]:
        return self._inner.started(seq)

    def stopped(self, seq: int) -> frozenset[StopAnswer]:
        return self._inner.stopped(seq)

    def value(self, seq: int) -> frozenset[ValueRole]:
        return self._inner.value(seq)


class _AllVersions(_Passing):
    """Every claim may be any version."""

    name = Classified.STARTED

    def started(self, seq: int) -> frozenset[Hashable]:
        return frozenset(self._cfg.workers)


class _AllAuthors(_Passing):
    """Every stopped may be any author's, with any act its shape allows."""

    name = Classified.STOPPED

    def stopped(self, seq: int) -> frozenset[StopAnswer]:
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


_ROLES = frozenset(
    ValueRole(r, reg)
    for r in Role
    for reg in (False, True)
    if r is not Role.STRAY or reg
)


class _AllRoles(_Passing):
    """Every value may have any role."""

    name = Classified.VALUE

    def value(self, seq: int) -> frozenset[ValueRole]:
        return _ROLES


class _Relaxing:
    """A classifier whose readers open one kind of answer."""

    def __init__(self, inner: Configuration, reader: type[_Passing]) -> None:
        self._inner = inner
        self._reader = reader

    def read(self, log: Sequence[Row]) -> LogReader:
        return self._reader(self._inner.classifier.read(log), self._inner)


_READERS: tuple[type[_AllVersions | _AllAuthors | _AllRoles], ...] = (
    _AllVersions,
    _AllAuthors,
    _AllRoles,
)


def _relaxed(configuration: Configuration, names: Sequence[Named]) -> Configuration:
    """The configuration with each named fact relaxed to its whole domain, in every
    version, and each named answer opened to every answer."""
    workers = {
        k: replace(
            f,
            **{
                n.value: domain(WorkerFacts, n.value)
                for n in names
                if isinstance(n, WorkerFact)
            },
        )
        for k, f in configuration.workers.items()
    }
    third = {
        k: replace(
            f,
            **{
                n.value: domain(ThirdPartyFacts, n.value)
                for n in names
                if isinstance(n, ThirdFact)
            },
        )
        for k, f in configuration.third_parties.items()
    }
    out = Configuration(workers, third, configuration.classifier)
    for reader in _READERS:
        if reader.name in names:
            out = out._replace(classifier=_Relaxing(out, reader))
    return out


def _owner(n: WorkerFact | ThirdFact) -> type[WorkerFacts | ThirdPartyFacts]:
    return WorkerFacts if isinstance(n, WorkerFact) else ThirdPartyFacts


def _statements(
    configuration: Configuration, n: WorkerFact | ThirdFact
) -> list[frozenset[Any]]:
    """What each version states of fact ``n``."""
    versions: Iterable[WorkerFacts | ThirdPartyFacts] = (
        configuration.workers.values()
        if isinstance(n, WorkerFact)
        else configuration.third_parties.values()
    )
    return [getattr(f, n.value) for f in versions]


def _unstated(configuration: Configuration, n: WorkerFact | ThirdFact) -> list[Any]:
    """The classes of fact ``n`` some version does not state, in a fixed order."""
    statements = _statements(configuration, n)
    return sorted(
        (c for c in domain(_owner(n), n.value) if any(c not in s for s in statements)),
        key=repr,
    )


def _added(
    configuration: Configuration, n: WorkerFact | ThirdFact, c: Any
) -> Configuration:
    """The configuration with class ``c`` added to fact ``n`` in every version."""
    if isinstance(n, WorkerFact):
        return configuration._replace(
            workers={
                k: replace(f, **{n.value: getattr(f, n.value) | {c}})
                for k, f in configuration.workers.items()
            }
        )
    return configuration._replace(
        third_parties={
            k: replace(f, **{n.value: getattr(f, n.value) | {c}})
            for k, f in configuration.third_parties.items()
        }
    )


def _stated(configuration: Configuration) -> list[Named]:
    """The facts some version states short of their whole domain, then every
    classifier answer."""
    workers: list[Named] = [
        n
        for n in WorkerFact
        if any(
            getattr(f, n.value) != domain(WorkerFacts, n.value)
            for f in configuration.workers.values()
        )
    ]
    third: list[Named] = [
        n
        for n in ThirdFact
        if any(
            getattr(f, n.value) != domain(ThirdPartyFacts, n.value)
            for f in configuration.third_parties.values()
        )
    ]
    return workers + third + list(Classified)


class Blame(NamedTuple):
    """What a refusal names (§7, "What refuses")."""

    # each relaxation proven to admit a history: the single facts and answers, or,
    # where none alone does, the smallest pairs
    names: tuple[tuple[Named, ...], ...]
    # the relaxations too wide to follow at ``cap``: whether they admit one is unknown
    undecided: tuple[tuple[Named, ...], ...]
    cap: int


def blame(
    rows: Sequence[Row],
    configuration: Configuration,
    *,
    born: Born,
    cap: int,
    budget: int,
    run_on: int,
) -> Blame:
    """What a refusal names: each stated fact and classifier answer whose relaxation
    alone, to its whole domain, admits a history; where none alone does, the smallest
    pairs whose joint relaxation does. A relaxation with more than ``cap`` states to
    follow at once is neither: it is reported as undecided, with the cap, and while a
    single one is undecided no pair is tried.

    A fact's whole domain can be too wide to follow where one class added is not (a
    load from outside, at every step): there each class is added alone, in every
    version, and one that admits a history proves the whole relaxation does, since a
    stated fact only removes candidates (``Narrowing``)."""

    def admitted(relaxed: Configuration) -> bool | None:
        try:
            g = candidates(
                rows, relaxed, born=born, cap=cap, budget=budget, run_on=run_on
            )
        except Refused:
            return None
        return admits(g)

    def single(n: Named) -> bool | None:
        whole = admitted(_relaxed(configuration, (n,)))
        if whole is not None or isinstance(n, Classified):
            return whole
        for c in _unstated(configuration, n):
            if admitted(_added(configuration, n, c)):
                return True
        return None

    stated = _stated(configuration)
    found: dict[tuple[Named, ...], bool | None] = {(n,): single(n) for n in stated}
    # pairs only where every single is known to admit nothing: an undecided single
    # might admit one alone
    if all(ok is False for ok in found.values()):
        pairs = [(a, b) for k, a in enumerate(stated) for b in stated[k + 1 :]]
        found = {names: admitted(_relaxed(configuration, names)) for names in pairs}
    return Blame(
        tuple(n for n, ok in found.items() if ok),
        tuple(n for n, ok in found.items() if ok is None),
        cap,
    )
