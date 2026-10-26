"""The history type: what conforming 0.3.0 writers did, one event at a time.

A history is the run as it happened: one sequence of events, in the order the
world and the log share. A worker's events carry the index of its claim (0 for
the run's first ``lifecycle.started``). Events that write a record become the
0.3.0 log (``render.py``); the others (a save landing, a pass closing, a worker
or its process ending) write nothing, and are what a 0.3.0 log cannot show.
The lineage that records the history's own events follows from all of them
(``truth.py``): the target of commits-and-lineage.md §7.

Every choice is an event or a declared field of one. A **writer fact** is how a
writer behaves where no event of the log decides it (§7, "the consumer decides
it"); the consumer states it, per writer version, so it is a field of the
claim or of the record its writer wrote:

- **the claim**: its process (``Claim.process``, which several claims may share);
  the gate's verdict (``Claim.gate``: the latest claim had ended or its process
  had, or the gate judged a live worker dead: a takeover); what it loaded
  (``Claim.origin``: a save of this log's claims, by ``SaveRef``, a checkpoint
  from outside, or nothing); and three writer facts: what its clock reads
  before its first iteration (``Claim.clock``), when it applies an answer
  another party wrote after its claim (``Claim.fold``), and whether a pass cut
  after its save landed finished (``Claim.cut_save``);
- **per iteration**: its label (``Begin.step``: a stride skips labels), which
  values it emits and where (``Emit``), whether and where it beats (``Beat``,
  with its watermark), its saves (``PassSave`` of its completed state inside
  its pass, ``Finish.saved`` as its pass's last act, ``Save`` outside it), its
  close (``Finish``) and when that came (``Ended``: dated by its tick, or one of
  the end-time fork's bounds, ``Undated``), when the commit a cut of its pass
  makes came (``PassSave.cut_end``), and whether it rewinds;
- **how a claim ends**: the worker ends (``Halt``) anywhere in its program, its
  process with it or not, with or without its own ``stopped`` before (``Exit``);
  a third party's ``stopped`` names it (``ThirdPartyStop``); a later claim takes
  it over; its launch is reaped (``Reaped``).

What 0.3.0 pins (§7, "What 0.3.0 pins"), ``walk`` enforces, raising
``NotConforming``: names are the writers' own; a worker's ``stopped`` is its
halt (no beat, iteration or second ``stopped`` after it); ``completed`` is the
worker's alone and carries no error; the claim gate (a claim starts once the
latest has ended or its worker is judged dead, which a takeover's verdict may
judge wrongly); the step clock; and run-absolute steps (a resumed claim's labels
continue above the state it loaded). Where a native exit's ``honored`` would
differ from its 0.3.0 list outside §7's stated costs, the truth raises
``truth.Residue``.

A claim loads a save that exists when it claims. 0.3.0's own order is claim,
then load (run-episodes.md §3); §6's order chooses first; the two read alike
wherever the save already exists, and a history places the load at the claim.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import NamedTuple, TypeAlias, assert_never

# ----- references to nodes and saves ------------------------------------------------


@dataclass(frozen=True)
class CommitOf:
    """The ``nth`` commit (from 0) a claim's worker made, counting only those
    its native counterpart records (a fenced worker's later ones are not)."""

    claim: int
    nth: int


@dataclass(frozen=True)
class ExitOf:
    """A claim's exit node: its worker's own ``stopped``."""

    claim: int


NodeRef: TypeAlias = CommitOf | ExitOf


@dataclass(frozen=True)
class SaveOf:
    """The newest save that claim ``claim``'s worker made, before the event
    naming it, of its state at ``step``: an iteration's completed state, a
    pass's partial state, or the state it loaded."""

    claim: int
    step: int


@dataclass(frozen=True)
class ExitSave:
    """The newest save that claim ``claim``'s worker made after its own
    ``stopped``."""

    claim: int


SaveRef: TypeAlias = SaveOf | ExitSave

# ----- the claim -----------------------------------------------------------------------


class Process(NamedTuple):
    """An OS process, rendered ``local://<host>/<pid>``. Claims that share one
    share a handle: two Workers run in turn by one process, or a
    ThreadLauncher's."""

    host: str
    pid: int


class Gate(Enum):
    """The claim gate's verdict on the latest claim when this one claimed."""

    FREE = "free"  # it had ended (an exit, a release) or its process had; or none
    TAKEOVER = "takeover"  # it was held and its process lived, and the gate judged
    # its worker dead: a false-dead probe, a stale heartbeat, a stopped that
    # released nothing (a note), or a worker abandoned in its live process


class Clock(Enum):
    """A writer fact: what the worker's clock reads before its first iteration."""

    LOADED = "loaded"  # the loaded save's step (0 for a fresh start)
    ZERO = "zero"  # 0 whatever it loaded: a counter that starts at 0 (mycooc's)


class Fold(Enum):
    """A writer fact: when a worker applies an answer (a ``stopped``'s
    ``honored``) that another party wrote after its claim."""

    AT_CLAIM = "at-claim"  # once, at its claim: the reference Worker (worker.py:85-118)
    AT_DRAIN = "at-drain"  # at each drain: design-v0.2.md §6's drain skips a stop
    # an answer already names


class CutSave(Enum):
    """A writer fact: whether an iteration finished whose pass was cut (its worker
    killed or fenced, or its own ``stopped`` written) after a save of its
    completed state, and before the pass closed."""

    LANDED = "landed"  # it finished once its save landed
    CLOSED = "closed"  # only its pass's close finishes it
    SAVE_START = "save-start"  # it finished once its save started, landed or not


@dataclass(frozen=True)
class Fresh:
    """A fresh start: no state."""


@dataclass(frozen=True)
class FromSave:
    """The claim loaded ``save``, which exists when it claims. Its first
    iteration is labelled above the saved state's step."""

    save: SaveRef


@dataclass(frozen=True)
class FromOutside:
    """The claim loaded a checkpoint standing at ``step`` that no claim of the
    log saved: one copied in from outside."""

    step: int


Origin: TypeAlias = Fresh | FromSave | FromOutside

# ----- closed choices ----------------------------------------------------------------


class Undated(Enum):
    """When an iteration that no record dates ended: the end-time fork's two
    generic choosers (§7), which bound its commit's ``t``. Both are at or after
    every value the commit holds (§3, a commit's ``t``)."""

    OWN_LAST = "own-last"  # with its claim's newest record before the commit
    NEXT = "next"  # just before the log's next record after the commit


@dataclass(frozen=True)
class Ticked:
    """Dated: its own tick follows its close at once, and stands at its end."""


TICKED = Ticked()

Ended: TypeAlias = Ticked | Undated
"""When an iteration ended: the time its native commit carries (§3,
lifecycle-v0.6: a commit's ``t`` is its writer's clock at ``commit()``)."""


class FinalStep(Enum):
    """A writer fact: what a worker's own ``stopped`` writes as ``final_step``."""

    YIELDED = "yielded"  # the last iteration it began (the reference Worker's)
    FINISHED = "finished"  # the last iteration it finished
    TICKED = "ticked"  # the last step it beat (a manual-tick worker)
    COUNTER = "counter"  # its own counter: the last iteration it finished, else
    # what its clock read before its first iteration (mycooc's main.py:1131, :333)


class Drain(Enum):
    """A writer fact: which of the stops it took in a worker's own ``stopped``
    lists in ``honored``."""

    EXIT = "exit"  # every one, its exit draining too (the reference Worker's ``_die``)
    TICKS = "ticks"  # only those its beats drained


class Watermark(Enum):
    """A writer fact: what a heartbeat writes as its ``consumed_seq``."""

    CONTROL = "control"  # the newest control record before it (the reference
    # Worker's cursor, which reads from the log's start)
    CLAIMED = "claimed"  # the newest control record since its claim, else 0
    TAIL = "tail"  # the newest record of any topic before it


class ThirdPartyFinal(Enum):
    """What a third party's ``stopped`` writes as its ``final_step``."""

    NULL = "null"  # the reclaim tool's
    PROGRESS = "progress"  # the newest integer beat on the log (the backstop's)


class Honors(Enum):
    """Which stops a third party's ``stopped`` lists in ``honored``."""

    NONE = "none"
    UNDISCHARGED = "undischarged"  # every stop no ``stopped`` has named yet


class Outcome(Enum):
    """supervisor-v0.5's death-report outcomes."""

    PREEMPTED = "preempted"
    ERRORED = "errored"
    KILLED = "killed"
    LOST = "lost"


class Kind(Enum):
    """A node's kind (commits-and-lineage.md §2)."""

    CLOSED = "closed"  # a completed exit
    TORN = "torn"  # an exit that did not complete and discarded its open iteration
    EXTENDABLE = "extendable"  # a commit, or any other exit


@dataclass(frozen=True)
class Killed:
    """A process killed by a signal."""

    signal: int


@dataclass(frozen=True)
class Exited:
    """A process that exited with a code."""

    code: int


ProcessEnd: TypeAlias = Killed | Exited


class Via(Enum):
    """A writer fact: how a third party writes a death report (§3)."""

    HELPER = "helper"  # through ``report_death``, which writes nothing on a claim
    # with a readable exit (the reclaim tool's, the launchers')
    DIRECT = "direct"  # a ``supervisor.died`` it writes itself: "any party ...
    # may write one". A cause reports "whether or not the claim has ended", but
    # evidence without one (``lost``) "reports only while the claim is held",
    # which binds every party (§3, "Who writes a death report")


@dataclass(frozen=True)
class Release:
    """A third party's ``stopped`` that reports its claim dead: a death report
    with ``outcome``, written ``via`` the helper or directly. Its writer may be
    wrong, and a live worker it names is then released."""

    outcome: Outcome
    via: Via


@dataclass(frozen=True)
class Report:
    """A third party's ``stopped`` that ends no claim: a note, or the clearing of
    the stops it honours, or (``reap``) the end of a process that never held the
    claim, such as a claim-race loser's."""

    reap: ProcessEnd | None


Intent: TypeAlias = Release | Report

# ----- events ------------------------------------------------------------------------


@dataclass(frozen=True)
class Claim:
    """The claim's worker writes ``lifecycle.started`` from ``process``,
    re-emitting ``launch``, past the gate's ``gate`` verdict, having loaded
    ``origin``; ``clock``, ``fold`` and ``cut_save`` are its writer's facts."""

    claim: int
    process: Process
    gate: Gate
    origin: Origin
    launch: str | None
    clock: Clock
    fold: Fold
    cut_save: CutSave


@dataclass(frozen=True)
class Begin:
    """The worker begins an iteration's pass and sets its clock to ``step``."""

    claim: int
    step: int


class Stamp(Enum):
    """A writer fact: what a value is to its writer, which its record's step
    alone does not say."""

    METRIC = "metric"  # at its clock, and the node of the iteration it is in holds it
    REGISTER = "register"  # at its clock, and no node holds it (mycooc's ``status``:
    # "A *register*, not a metric series", runstate_emit.py:57-65@bb6f9e8c)
    STEPLESS = "stepless"  # with a null step: a register (§7, M3)


@dataclass(frozen=True)
class Emit:
    """The worker writes a ``value`` named ``name``, stamped ``stamp``."""

    claim: int
    name: str
    stamp: Stamp

    @property
    def stepless(self) -> bool:
        """Its record's step is null."""
        return self.stamp is Stamp.STEPLESS


@dataclass(frozen=True)
class Beat:
    """The worker ticks: it drains control and beats at its clock, at most once
    per iteration."""

    claim: int
    watermark: Watermark


@dataclass(frozen=True)
class Beacon:
    """The worker drains control and writes a stepless heartbeat: liveness only."""

    claim: int
    watermark: Watermark


@dataclass(frozen=True)
class PassSave:
    """Inside its pass, the worker saves the iteration's completed state: its
    update is done, and the pass writes on (mycooc's order: status, save, tick,
    metrics, stage). ``landed`` False: the save started and never landed, the
    worker cut or the save failed. ``cut_end`` dates the commit a cut of the pass
    makes, where ``cut_save`` says the save finished the iteration: that commit
    stands at the cut, after every value the pass wrote, so it is read there,
    never at the save (§3, a commit's ``t``)."""

    claim: int
    landed: bool
    cut_end: Undated


@dataclass(frozen=True)
class Finish:
    """The iteration's pass closes: the point the writer's loop treats as done.
    With ``saved``, the pass's last act was a save of the completed state. It is
    where the native counterpart calls ``commit()``, whose drain reads any death
    report naming the claim (§4); ``ended`` dates it."""

    claim: int
    saved: bool
    ended: Ended


@dataclass(frozen=True)
class Save:
    """A save of the worker's state outside a pass (between iterations, after
    its exit, or of the state it loaded), or of a pass's partial state."""

    claim: int


@dataclass(frozen=True)
class Rewind:
    """Between iterations, the worker reloads ``target`` (the root when None):
    its pending values are dropped and its clock reads the saved state's step."""

    claim: int
    target: SaveRef | None


@dataclass(frozen=True)
class Exit:
    """The worker writes its own ``stopped``, its halt. ``honors`` and
    ``final_step`` are its writer's encodings; ``discards`` is whether its loop
    leaves no resumable state after a clean exit inside an open iteration (§4,
    ``w.discard()``)."""

    claim: int
    completed: bool
    error: str | None
    final_step: FinalStep
    honors: Drain
    discards: bool


@dataclass(frozen=True)
class Halt:
    """The worker ends: nothing of it follows. ``how`` is how its process ended,
    or None when the process runs on."""

    claim: int
    how: ProcessEnd | None


@dataclass(frozen=True)
class StopRequest:
    """An orchestrator writes ``control.stop`` (an empty body: stop now)."""

    request_id: str


@dataclass(frozen=True)
class ThirdPartyStop:
    """A third party writes a ``stopped`` naming claim ``names`` (or none)."""

    names: int | None
    intent: Intent
    error: str | None
    final_step: ThirdPartyFinal
    honors: Honors


@dataclass(frozen=True)
class Launched:
    """A launcher writes ``launcher.launched`` for ``process``."""

    launch: str
    process: Process


@dataclass(frozen=True)
class Reaped:
    """The launcher reaps its process: ``launcher.terminated``."""

    launch: str
    how: ProcessEnd


@dataclass(frozen=True)
class Stray:
    """A ``value`` written by a writer that holds no claim: a process before it
    claims, a claim-race loser, a third party. ``step`` is its own clock."""

    name: str
    step: int | None


@dataclass(frozen=True)
class UserRecord:
    """A record on a topic no 0.3.0 convention reserves."""

    topic: str


Event: TypeAlias = (
    Claim
    | Begin
    | Emit
    | Beat
    | Beacon
    | PassSave
    | Finish
    | Save
    | Rewind
    | Exit
    | Halt
    | StopRequest
    | ThirdPartyStop
    | Launched
    | Reaped
    | Stray
    | UserRecord
)

WorkerEvent: TypeAlias = (
    Begin | Emit | Beat | Beacon | PassSave | Finish | Save | Rewind | Exit | Halt
)

_WORKER_EVENTS = (Begin, Emit, Beat, Beacon, PassSave, Finish, Save, Rewind, Exit, Halt)

RECORDS = (
    Claim,
    Emit,
    Beat,
    Beacon,
    Exit,
    StopRequest,
    ThirdPartyStop,
    Launched,
    Reaped,
    Stray,
    UserRecord,
)
"""The events that write a record."""


class Born(Enum):
    """The format the log was born in, which decides who filled the names a 0.3.0
    record carries."""

    V0_3_0 = "0.3.0"  # each record as its writer filled it
    V0_2_0 = "0.2.0"  # migrated by the 0.2.0 -> 0.3.0 step, which filled every
    # ``stopped``'s ``honored`` and every heartbeat's and ``stopped``'s ``claim_seq``
    # positionally (reference-by-name.md §6): the earlier step's recorded choice,
    # not a writer's


@dataclass(frozen=True)
class History:
    events: tuple[Event, ...]
    born: Born


class NotConforming(Exception):
    """The history is not one conforming 0.3.0 writers produce: the message says
    which rule it breaks, at which event."""


_RESERVED = ("lifecycle.", "control.", "launcher.")

# ----- the walk ----------------------------------------------------------------------


class Phase(Enum):
    LOADING = "loading"  # claimed, no iteration begun yet
    ITERATING = "iterating"  # a pass begun and not closed
    BETWEEN = "between"  # between passes (its last one closed)
    EXITED = "exited"  # its own ``stopped`` written; the worker lives on
    HALTED = "halted"  # the worker ended


def _exit_kind(completed: bool, error: str | None, discards: bool) -> Kind:
    if completed:
        return Kind.CLOSED
    if error is not None or discards:
        return Kind.TORN
    return Kind.EXTENDABLE


class CutCommit(NamedTuple):
    """The commit a pass's cut makes, just before the cutting event, of a pass
    cut after its save: dated ``end`` there (its ``PassSave``'s ``cut_end``)."""

    commit: CommitOf
    end: Undated


class Derived(NamedTuple):
    """What one event derives."""

    label: int | None  # the step an Emit, a Beat or a Stray stamps (None: stepless)
    node: NodeRef | None  # the node a Finish or an Exit makes, if any
    final_step: int | None  # the final_step an Exit writes
    cut: CutCommit | None  # a commit a cut makes just before this event


_NOTHING = Derived(None, None, None, None)


class Load(NamedTuple):
    """What a claim loaded, and the newest save that existed at its claim: the
    events a load fact such as "a resume loads the newest save" is checked
    against."""

    claim: int
    loaded: int | None  # the event index of the save it loaded, if any
    newest: int | None  # the event index of the newest save at its claim, if any


class Walk(NamedTuple):
    """What the walk derives, per event (index-aligned with ``history.events``)."""

    derived: tuple[Derived, ...]
    fenced: tuple[bool, ...]  # the event's worker was fenced before it
    exit_kinds: dict[int, Kind]  # claim -> its native exit node's kind
    steps: dict[NodeRef, int]  # each node's step: its iteration's, or the exit's clock
    origins: dict[int, NodeRef | None]  # claim -> the node its load names, if any
    rewinds: dict[int, NodeRef | None]  # Rewind event -> the node its target names
    loads: tuple[Load, ...]


@dataclass(frozen=True)
class Unresolved:
    """The node an in-pass save names before its pass closes or is cut."""


@dataclass
class _Save:
    ref: SaveRef
    index: int  # the event that made it
    step: int  # the saved state's step
    node: NodeRef | Unresolved | None  # the node it names (None: none holds it)


@dataclass
class _Worker:
    """One claim's worker, as the walk folds it."""

    claim: int
    process: Process
    cut_save: CutSave
    clock: int  # the step a labelled value or a beat stamps
    idle: int  # what its clock read before its first iteration
    state_step: int  # the step of the state it holds between iterations
    floor: int  # the lowest label its next iteration may take
    position: NodeRef | None  # where its native counterpart stands
    state: NodeRef | None  # the node a save of its state between passes names
    phase: Phase = Phase.LOADING
    tick_due: bool = False  # the open iteration has not beaten yet
    commits: int = 0
    exited: bool = False
    exit_kind: Kind | None = None  # its exit node's kind, once a native one exists
    released: bool = False  # fenced: a release or a takeover reached it alive
    pass_save: PassSave | None = None  # the open pass's PassSave, if any
    pass_save_index: int | None = None  # the event that is that PassSave
    awaiting_tick: bool = False  # an end dated TICKED waits for its tick
    begun: int | None = None
    finished: int | None = None
    ticked: int | None = None


@dataclass
class _State:
    workers: list[_Worker] = field(default_factory=list)
    saves: list[_Save] = field(default_factory=list)
    node_steps: dict[NodeRef, int] = field(default_factory=dict)
    origins: dict[int, NodeRef | None] = field(default_factory=dict)
    rewinds: dict[int, NodeRef | None] = field(default_factory=dict)
    loads: list[Load] = field(default_factory=list)
    stop_ids: set[str] = field(default_factory=set)
    launched: dict[str, Process] = field(default_factory=dict)
    reaped: set[str] = field(default_factory=set)
    released: set[int] = field(default_factory=set)  # claims a release named
    ended: dict[Process, ProcessEnd] = field(default_factory=dict)
    next_record: list[int] = field(
        default_factory=list
    )  # event indices at or after which a record must stand: an end dated NEXT


def walk(history: History) -> Walk:
    """Fold the history, checking that conforming writers produce it; raise
    ``NotConforming`` otherwise."""
    st, derived = _fold(history.events)
    for c, w in enumerate(st.workers):
        if w.phase is not Phase.HALTED:
            raise NotConforming(
                f"claim {c}'s worker never halts: the run is not at rest"
            )
    last_record = max(
        (i for i, ev in enumerate(history.events) if isinstance(ev, RECORDS)),
        default=-1,
    )
    for first in st.next_record:
        if first > last_record:
            raise NotConforming(
                f"an end before event {first} is dated by the next record: none follows"
            )
    return derived


def loadable(events: tuple[Event, ...]) -> dict[SaveRef, int]:
    """The saves a claim appended after ``events`` (a conforming prefix of a
    history) could load, each with its saved state's step."""
    st, _ = _fold(events)
    return {s.ref: s.step for s in st.saves}


def newest_save(events: tuple[Event, ...]) -> SaveRef | None:
    """The newest save made in ``events`` (a conforming prefix of a history),
    which a claim appended after them under ``load=newest`` loads."""
    st, _ = _fold(events)
    return st.saves[-1].ref if st.saves else None


def loads_newest(history: History) -> bool:
    """Whether every claim loaded the newest save that existed at its claim, or
    nothing where none did: mycooc's ``load=newest``, one checkpoint slot that
    every save overwrites and every claim loads."""
    return all(load.loaded == load.newest for load in walk(history).loads)


def _fold(events: tuple[Event, ...]) -> tuple[_State, Walk]:
    st = _State()
    derived: list[Derived] = []
    fenced: list[bool] = []
    for i, ev in enumerate(events):
        d, fence = _NOTHING, False
        try:
            if isinstance(ev, _WORKER_EVENTS):
                _ticks(st, ev)
            if isinstance(ev, Claim):
                d = _claim(st, i, ev)
            elif isinstance(ev, StopRequest):
                if ev.request_id in st.stop_ids:
                    raise NotConforming(f"stop id {ev.request_id!r} reused")
                st.stop_ids.add(ev.request_id)
            elif isinstance(ev, ThirdPartyStop):
                d = _third_party(st, i, ev)
            elif isinstance(ev, Launched):
                if ev.launch in st.launched:
                    raise NotConforming(f"launch {ev.launch!r} launched twice")
                st.launched[ev.launch] = ev.process
            elif isinstance(ev, Reaped):
                _reaped(st, ev)
            elif isinstance(ev, Stray):
                d = Derived(ev.step, None, None, None)
            elif isinstance(ev, UserRecord):
                if ev.topic == "value" or ev.topic.startswith(_RESERVED):
                    raise NotConforming(f"topic {ev.topic!r} is a convention's")
            else:
                w = _worker(st, ev.claim)
                fence = w.released
                d = _step(st, i, ev, w)
        except NotConforming as exc:
            raise type(exc)(f"event {i} ({ev}): {exc}") from None
        derived.append(d)
        fenced.append(fence)
    for w in st.workers:
        if w.awaiting_tick:
            raise NotConforming(
                f"claim {w.claim}'s end is dated by a tick that never comes"
            )
    return st, Walk(
        tuple(derived),
        tuple(fenced),
        {c: w.exit_kind for c, w in enumerate(st.workers) if w.exit_kind is not None},
        dict(st.node_steps),
        dict(st.origins),
        dict(st.rewinds),
        tuple(st.loads),
    )


def _ticks(st: _State, ev: WorkerEvent) -> None:
    """An end dated ``TICKED`` stands at its claim's tick, its very next event."""
    if not 0 <= ev.claim < len(st.workers):
        return
    w = st.workers[ev.claim]
    if w.awaiting_tick:
        if not isinstance(ev, Beat):
            raise NotConforming(
                "an end dated by its tick is followed at once by the tick"
            )
        w.awaiting_tick = False


def _dated(st: _State, w: _Worker, i: int, ended: Ended) -> None:
    """A pass's close at event ``i``, dated ``ended``."""
    if isinstance(ended, Ticked):
        w.awaiting_tick = True
    elif ended is Undated.NEXT:
        st.next_record.append(i + 1)
    elif ended is not Undated.OWN_LAST:
        assert_never(ended)


def _worker(st: _State, claim: int) -> _Worker:
    if not 0 <= claim < len(st.workers):
        raise NotConforming(f"claim {claim} has not claimed")
    w = st.workers[claim]
    if w.phase is Phase.HALTED:
        raise NotConforming(f"claim {claim}'s worker has halted")
    return w


class _Loaded(NamedTuple):
    """A save a claim or a rewind loads."""

    made_at: int  # the event that made it
    step: int  # the saved state's step
    node: NodeRef | None  # the node it names (None: none holds its state)


def _resolve(st: _State, ref: SaveRef, before: int) -> _Loaded:
    """The newest save ``ref`` names made before event ``before``. Its pass has
    closed or been cut, since a claim that loads it follows the fence."""
    found = [s for s in st.saves if s.ref == ref and s.index < before]
    if not found:
        raise NotConforming(f"no save {ref} exists at event {before}")
    save = found[-1]
    assert not isinstance(save.node, Unresolved), "a loaded save's pass is open"
    return _Loaded(save.index, save.step, save.node)


def _claim(st: _State, i: int, ev: Claim) -> Derived:
    if ev.claim != len(st.workers):
        raise NotConforming(
            f"claims are numbered in claim order; expected {len(st.workers)}"
        )
    if ev.process in st.ended:
        raise NotConforming(f"process {ev.process} has ended")
    cut = _gate(st, i, ev.gate)
    origin = ev.origin
    newest = st.saves[-1].index if st.saves else None
    loaded: int | None = None
    if isinstance(origin, Fresh):
        step, node, floor = 0, None, 0
    elif isinstance(origin, FromOutside):
        if origin.step < 0:
            raise NotConforming("a step is never negative")
        step, node, floor = origin.step, None, origin.step + 1
    elif isinstance(origin, FromSave):
        save = _resolve(st, origin.save, i)
        step, node, floor, loaded = save.step, save.node, save.step + 1, save.made_at
    else:
        assert_never(origin)
    st.loads.append(Load(ev.claim, loaded, newest))
    st.origins[ev.claim] = node
    clock = step if ev.clock is Clock.LOADED else 0
    st.workers.append(
        _Worker(
            ev.claim,
            ev.process,
            ev.cut_save,
            clock,
            idle=clock,
            state_step=step,
            floor=floor,
            position=node,
            state=node,
        )
    )
    return Derived(None, None, None, cut)


def _gate(st: _State, i: int, gate: Gate) -> CutCommit | None:
    """The claim gate (design-v0.2.md §4, §8; run-episodes.md §3): a claim is
    taken once the latest has ended or its worker is judged dead. FREE says it
    had ended, or its process had; TAKEOVER says neither, and the gate judged
    it dead anyway. A conforming 0.4.0 lineage admits the claim only once the
    latest has ended (§4, "The claim"), so the claim path's death report stands
    before it, and fences a live worker (§4, "A released worker fences
    itself")."""
    if not st.workers:
        if gate is not Gate.FREE:
            raise NotConforming("the run's first claim takes nothing over")
        return None
    c = len(st.workers) - 1
    prev = st.workers[c]
    free = prev.exited or c in st.released or prev.process in st.ended
    if gate is Gate.FREE and not free:
        raise NotConforming(f"claim {c} is held and its process lives: a takeover")
    if gate is Gate.TAKEOVER:
        if free:
            raise NotConforming(f"claim {c} had ended or its process had: no takeover")
        if prev.phase is not Phase.HALTED:
            return _fence(st, i, prev)
    return None


def _fence(st: _State, i: int, w: _Worker) -> CutCommit | None:
    """A death report reaches a live worker with no exit node at event ``i``:
    its open pass is cut there (``cut_save``), and it writes nothing more
    natively (§4)."""
    cut = _cut(st, i, w)
    w.released = True
    return cut


def _pass_save(st: _State, w: _Worker) -> _Save | None:
    """The save the open (or last) pass made of its completed state, if it
    landed."""
    return next((s for s in st.saves if s.index == w.pass_save_index), None)


def _cut(st: _State, i: int, w: _Worker) -> CutCommit | None:
    """The open pass ends at event ``i`` other than by its close: does the
    iteration finish? It does where the writer fact ``cut_save`` says the pass's
    save of its completed state finished it (§7, "the consumer decides it"). Its
    commit stands just before event ``i``, dated there (§3, a commit's ``t``)."""
    if w.phase is not Phase.ITERATING or w.released or w.pass_save is None:
        return None
    finishes = w.cut_save is CutSave.SAVE_START or (
        w.cut_save is CutSave.LANDED and w.pass_save.landed
    )
    save = _pass_save(st, w)
    if not finishes:
        if save is not None:
            save.node = None  # an exit that keeps the pass may name it (§6)
        return None
    node = CommitOf(w.claim, w.commits)
    w.commits += 1
    w.position, w.state_step = node, w.clock
    st.node_steps[node] = w.clock
    if save is not None:
        save.node = node
    if w.pass_save.cut_end is Undated.NEXT:
        st.next_record.append(i)
    return CutCommit(node, w.pass_save.cut_end)


def _third_party(st: _State, i: int, ev: ThirdPartyStop) -> Derived:
    if ev.names is not None and not 0 <= ev.names < len(st.workers):
        raise NotConforming(f"a stopped names claim {ev.names}, which has not claimed")
    if not isinstance(ev.intent, Release):
        return _NOTHING
    if ev.names is None:
        raise NotConforming("a release names the claim it reports dead")
    st.released.add(ev.names)
    w = st.workers[ev.names]
    # A release fences a live worker that has no exit node (§4): a death report
    # it writes is no report there once the claim has a readable exit (§3).
    if w.phase is not Phase.HALTED and w.exit_kind is None:
        return Derived(None, None, None, _fence(st, i, w))
    return _NOTHING


def _reaped(st: _State, ev: Reaped) -> None:
    if ev.launch not in st.launched:
        raise NotConforming(f"launch {ev.launch!r} reaped before it is launched")
    if ev.launch in st.reaped:
        raise NotConforming(f"launch {ev.launch!r} reaped twice: a process ends once")
    st.reaped.add(ev.launch)
    process = st.launched[ev.launch]
    if any(w.process == process for w in st.workers):
        if process not in st.ended:
            raise NotConforming(f"process {process} is reaped while it lives")
        if st.ended[process] != ev.how:
            raise NotConforming(
                f"process {process} ended {st.ended[process]}, not {ev.how}"
            )


def _save(
    st: _State, i: int, ref: SaveRef, step: int, node: NodeRef | Unresolved | None
) -> None:
    st.saves.append(_Save(ref, i, step, node))


def _step(st: _State, i: int, ev: WorkerEvent, w: _Worker) -> Derived:
    if isinstance(ev, Begin):
        if w.phase not in (Phase.LOADING, Phase.BETWEEN):
            raise NotConforming("a pass begins only after the last one closed")
        if ev.step < w.floor:
            # run-absolute steps: labels rise within a claim, and a resumed claim's
            # continue above the step it loaded
            raise NotConforming(f"label {ev.step} below the clock's floor {w.floor}")
        w.phase, w.clock, w.tick_due, w.begun = Phase.ITERATING, ev.step, True, ev.step
        w.pass_save, w.pass_save_index = None, None
        return _NOTHING
    if isinstance(ev, Emit):
        return Derived(None if ev.stepless else w.clock, None, None, None)
    if isinstance(ev, Beat):
        if w.phase not in (Phase.ITERATING, Phase.BETWEEN) or not w.tick_due:
            raise NotConforming("a beat ticks an iteration, at most once")
        w.tick_due, w.ticked = False, w.clock
        return Derived(w.clock, None, None, None)
    if isinstance(ev, Beacon):
        if w.phase is Phase.EXITED:
            raise NotConforming("a halted worker beats no more")
        return _NOTHING
    if isinstance(ev, PassSave):
        if w.phase is not Phase.ITERATING or w.pass_save is not None:
            raise NotConforming("a pass saves its completed state once, inside it")
        w.pass_save, w.pass_save_index = ev, i
        if ev.landed:
            # It names the iteration's commit once the pass closes or a cut
            # finishes it, and nothing once its worker is fenced (§4).
            _save(
                st,
                i,
                SaveOf(w.claim, w.clock),
                w.clock,
                None if w.released else Unresolved(),
            )
        return _NOTHING
    if isinstance(ev, Finish):
        if w.phase is not Phase.ITERATING:
            raise NotConforming("no pass is open")
        w.phase, w.floor, w.finished = Phase.BETWEEN, w.clock + 1, w.clock
        w.state_step = w.clock
        _dated(st, w, i, ev.ended)
        if w.released:
            # fenced: its pass was cut at the fence, which resolved its save
            w.state = None
            if ev.saved:
                _save(st, i, SaveOf(w.claim, w.clock), w.clock, None)
            return _NOTHING
        node = CommitOf(w.claim, w.commits)
        w.commits += 1
        w.position = w.state = node
        st.node_steps[node] = w.clock
        save = _pass_save(st, w)
        if save is not None:
            save.node = node  # an in-pass save of completed state names it (§6)
        if ev.saved:
            _save(st, i, SaveOf(w.claim, w.clock), w.clock, node)
        return Derived(None, node, None, None)
    if isinstance(ev, Save):
        if w.phase is Phase.EXITED:
            exit_node = (
                ExitOf(w.claim)
                if w.exit_kind is Kind.EXTENDABLE and not w.released
                else None
            )
            _save(st, i, ExitSave(w.claim), w.state_step, exit_node)
        elif w.phase is Phase.ITERATING:
            _save(st, i, SaveOf(w.claim, w.clock), w.clock, None)  # partial state
        else:
            # the state it holds, whatever its clock reads (Clock.ZERO before
            # its first iteration reads 0 over a loaded state)
            ref = SaveOf(w.claim, w.state_step)
            _save(st, i, ref, w.state_step, w.state)
        return _NOTHING
    if isinstance(ev, Rewind):
        if w.phase is not Phase.BETWEEN:
            raise NotConforming("a rewind falls between iterations")
        if ev.target is None:
            w.clock, w.floor, target = 0, 0, None
        else:
            loaded = _resolve(st, ev.target, i)
            w.clock, w.floor, target = loaded.step, loaded.step + 1, loaded.node
        w.state, w.state_step, w.tick_due = target, w.clock, False
        st.rewinds[i] = target
        if not w.released:
            w.position = target
        return _NOTHING
    if isinstance(ev, Exit):
        if w.phase not in (Phase.LOADING, Phase.ITERATING, Phase.BETWEEN):
            raise NotConforming("a worker's stopped is its halt: it writes one")
        if ev.completed and ev.error is not None:
            raise NotConforming("a completed stop carries no error (lifecycle-v0.5)")
        inside = w.phase is Phase.ITERATING
        cut = _cut(st, i, w)
        if ev.discards and (ev.error is not None or not inside or cut is not None):
            raise NotConforming(
                "only a clean exit inside an open iteration discards it"
            )
        final = _final_step(w, ev.final_step)
        if inside and ev.error is None and not ev.discards and cut is None:
            w.state_step = w.clock  # it keeps the open iteration's state
        w.phase, w.exited = Phase.EXITED, True
        if w.released:
            return Derived(None, None, final, cut)
        w.exit_kind = _exit_kind(ev.completed, ev.error, ev.discards)
        save = _pass_save(st, w)
        if inside and cut is None and save is not None:
            # cut_save says the save did not finish the pass, and the exit cut
            # it: an exit that keeps the open iteration holds the state the save
            # holds, as a save just after it would name it (§6, "Save"; A17)
            if w.exit_kind is Kind.EXTENDABLE:
                save.node = ExitOf(w.claim)
        st.node_steps[ExitOf(w.claim)] = w.clock
        return Derived(None, ExitOf(w.claim), final, cut)
    if isinstance(ev, Halt):
        cut = _cut(st, i, w)
        if ev.how is not None:
            if any(
                x is not w and x.process == w.process and x.phase is not Phase.HALTED
                for x in st.workers
            ):
                raise NotConforming(
                    f"process {w.process} ends under another live worker"
                )
            st.ended[w.process] = ev.how
        w.phase = Phase.HALTED
        return Derived(None, None, None, cut)
    assert_never(ev)


def _final_step(w: _Worker, convention: FinalStep) -> int | None:
    if convention is FinalStep.YIELDED:
        return w.begun
    if convention is FinalStep.FINISHED:
        return w.finished
    if convention is FinalStep.TICKED:
        return w.ticked
    if convention is FinalStep.COUNTER:
        return w.idle if w.finished is None else w.finished
    assert_never(convention)


def conforms(history: History) -> bool:
    try:
        walk(history)
    except NotConforming:
        return False
    return True
