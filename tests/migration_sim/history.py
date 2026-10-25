"""The history type: what conforming 0.3.0 writers did, one event at a time.

A history is the run as it happened: one sequence of events, in the order the
world and the log share. A worker's events carry the index of its claim (0 for
the run's first ``lifecycle.started``). Events that write a record become the
0.3.0 log (``render.py``); the others (an iteration finishing, a save landing,
a process ending) write nothing, and are exactly what a 0.3.0 log cannot show.
What a native 0.4.0 writer would have recorded follows from all of them
(``truth.py``).

Every writer choice is an event or a declared field of one:

- **the resume origin** is ``Claim.origin``: a fresh start, a save this log's
  claims made (which node), or a checkpoint copied in from outside (which step);
- **per iteration**: its label (``Begin.step``: a stride skips labels), which
  values it emits and where (``Emit``), whether and where it beats (``Beat``
  before or after the body, or none), whether it saves (``Finish.saved`` in the
  body, ``Save`` after it) with or without a record (a stepless ``Emit``), and
  whether it rewinds (``Rewind``);
- **how a claim ends**: its process ends (``Halt``) anywhere in its program, with
  or without its own ``stopped`` before (``Exit``, completed or not, error or
  not, with a ``final_step`` convention); a third party's ``stopped`` names it
  (``ThirdPartyStop``, a release or a report); its launch is reaped
  (``Reaped``).

What 0.3.0 pins (commits-and-lineage.md §7, "What 0.3.0 pins"), ``walk``
enforces: names are the writers' own (a worker's records name its claim); a
worker's ``stopped`` is its halt (no beat, no iteration and no second
``stopped`` after it); ``completed`` is the worker's alone and carries no error;
the claim gate (a claim starts only once the latest one has ended or its worker
is dead); the step clock (a value's and a beat's step is the worker's clock);
and run-absolute steps (a resumed claim's labels continue above the state it
loaded).

The walk also enforces the native counterpart's limits, where the 0.4.0 model
needs a history to have one. Each is stated where it is checked and listed in
the task report: a resume loads a save made before its claim (§6, "the order at
resume"); a released worker's later saves never land (§4, "A released worker
fences itself"); and only a release opens the gate over a live worker (§4, "The
claim").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import NamedTuple, TypeAlias, assert_never

# ----- references to nodes ---------------------------------------------------------


@dataclass(frozen=True)
class CommitOf:
    """The ``nth`` commit (from 0) a claim's worker made: its ``nth``
    finished iteration, counting only those a native writer could record (a
    released worker's later iterations are fenced, ``walk``)."""

    claim: int
    nth: int


@dataclass(frozen=True)
class ExitOf:
    """A claim's exit node: its worker's own ``stopped``."""

    claim: int


NodeRef: TypeAlias = CommitOf | ExitOf

# ----- the resume origin ------------------------------------------------------------


@dataclass(frozen=True)
class Fresh:
    """A fresh start: no state, and the clock reads 0."""


@dataclass(frozen=True)
class FromSave:
    """The claim loaded a save this log's claims made, of ``node``. Its clock
    reads that node's step, and its first iteration is labelled above it."""

    node: NodeRef


@dataclass(frozen=True)
class FromOutside:
    """The claim loaded a checkpoint copied in from outside the log, standing at
    ``step``: no claim of the log saved it."""

    step: int


Origin: TypeAlias = Fresh | FromSave | FromOutside

# ----- closed choices ----------------------------------------------------------------


class FinalStep(Enum):
    """What a worker's own ``stopped`` writes as its ``final_step``."""

    YIELDED = "yielded"  # the last iteration it began (the reference Worker's)
    FINISHED = "finished"  # the last iteration it finished (mycooc's writers)
    TICKED = "ticked"  # the last step it beat (a manual-tick worker)


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


@dataclass(frozen=True)
class Release:
    """A third party's ``stopped`` that reports its claim dead: 0.4.0's
    ``supervisor.died`` with ``outcome``. Its writer may be wrong, and a live
    worker it names is then released."""

    outcome: Outcome


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
    """The claim's worker writes ``lifecycle.started``, re-emitting ``launch``,
    having chosen its state's ``origin`` before it claimed (§6)."""

    claim: int
    origin: Origin
    launch: str | None


@dataclass(frozen=True)
class Begin:
    """The worker begins an iteration and sets its clock to ``step``."""

    claim: int
    step: int


@dataclass(frozen=True)
class Emit:
    """The worker writes a ``value`` named ``name``, at its clock, or with a null
    step when ``stepless`` (a register)."""

    claim: int
    name: str
    stepless: bool


@dataclass(frozen=True)
class Beat:
    """The worker ticks: a heartbeat at its clock, at most one per iteration."""

    claim: int


@dataclass(frozen=True)
class Beacon:
    """The worker writes a stepless heartbeat: liveness only."""

    claim: int


@dataclass(frozen=True)
class Finish:
    """The iteration's body is done: the worker's state now includes it. With
    ``saved``, the body's last act was a save of that state, which is durable."""

    claim: int
    saved: bool


@dataclass(frozen=True)
class Save:
    """A save of the worker's position, outside the body: after a finished
    iteration (its tick included), or after an extendable exit."""

    claim: int


@dataclass(frozen=True)
class Rewind:
    """The worker reloads a save of ``target`` (the root when None) between
    iterations; its pending values are dropped and its clock reads the target's
    step."""

    claim: int
    target: NodeRef | None


@dataclass(frozen=True)
class Exit:
    """The worker writes its own ``stopped``, its halt."""

    claim: int
    completed: bool
    error: str | None
    final_step: FinalStep


@dataclass(frozen=True)
class Halt:
    """The worker's process ends. Nothing of the worker follows."""

    claim: int
    how: ProcessEnd


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
    """A launcher writes ``launcher.launched``. Unless ``wrapper``, the process
    it launched is its claimant's, the same handle."""

    launch: str
    wrapper: bool


@dataclass(frozen=True)
class Reaped:
    """The launcher reaps its process: ``launcher.terminated``."""

    launch: str
    how: ProcessEnd


Event: TypeAlias = (
    Claim
    | Begin
    | Emit
    | Beat
    | Beacon
    | Finish
    | Save
    | Rewind
    | Exit
    | Halt
    | StopRequest
    | ThirdPartyStop
    | Launched
    | Reaped
)

WorkerEvent: TypeAlias = (
    Begin | Emit | Beat | Beacon | Finish | Save | Rewind | Exit | Halt
)


@dataclass(frozen=True)
class History:
    events: tuple[Event, ...]


class NotConforming(Exception):
    """The history is not one a conforming 0.3.0 writer (with a native 0.4.0
    counterpart) produces: the message says which rule it breaks, at which
    event."""


# ----- the walk ----------------------------------------------------------------------


class Phase(Enum):
    LOADING = "loading"  # claimed, no iteration begun yet
    ITERATING = "iterating"  # an iteration begun and not finished
    BETWEEN = "between"  # between iterations (its last one finished)
    EXITED = "exited"  # its own ``stopped`` written; the process lives on
    HALTED = "halted"  # its process ended


def _exit_kind(completed: bool, error: str | None) -> Kind:
    if completed:
        return Kind.CLOSED
    if error is not None:
        return Kind.TORN
    return Kind.EXTENDABLE


@dataclass
class _Worker:
    """One claim's worker, as the walk folds it."""

    claim: int
    clock: int  # the step a labelled value or a beat stamps
    floor: int  # the lowest label its next iteration may take
    position: NodeRef | None  # where its state stands, as a node
    phase: Phase = Phase.LOADING
    tick_due: bool = False  # the open iteration has not beaten yet
    commits: int = 0
    exit_kind: Kind | None = None  # its exit node's kind, once a native one exists
    released: bool = False  # a release reached it while its process lived
    begun: int | None = None
    finished: int | None = None
    ticked: int | None = None
    halt: ProcessEnd | None = None


class Walk(NamedTuple):
    """What the walk derives, per event (index-aligned with ``history.events``)."""

    labels: tuple[int | None, ...]  # the step an Emit or Beat stamps (None: stepless)
    nodes: tuple[NodeRef | None, ...]  # the node a Finish or Exit makes, if any
    fenced: tuple[bool, ...]  # the event's worker was released before it
    final_steps: tuple[int | None, ...]  # the final_step an Exit writes
    exit_kinds: dict[int, Kind]  # claim -> its native exit node's kind
    steps: dict[NodeRef, int]  # each node's step: its iteration's, or the exit's clock


@dataclass
class _State:
    workers: list[_Worker] = field(default_factory=list)
    saves: dict[NodeRef, int] = field(default_factory=dict)  # node -> event index
    node_steps: dict[NodeRef, int] = field(default_factory=dict)
    stop_ids: set[str] = field(default_factory=set)
    launched: dict[str, bool] = field(default_factory=dict)  # launch -> wrapper
    claimant: dict[str, int] = field(default_factory=dict)  # launch -> its claim
    reaped: set[str] = field(default_factory=set)
    released: set[int] = field(default_factory=set)  # claims a release named


def walk(history: History) -> Walk:
    """Fold the history, checking that conforming writers produce it; raise
    ``NotConforming`` otherwise."""
    st, derived = _fold(history.events)
    for c, w in enumerate(st.workers):
        if w.phase is not Phase.HALTED:
            raise NotConforming(
                f"claim {c}'s worker never halts: the run is not at rest"
            )
    return derived


def loadable(events: tuple[Event, ...]) -> dict[NodeRef, int]:
    """The nodes a claim appended after ``events`` (a conforming prefix of a
    history) could load, each with its step: every node saved so far."""
    st, _ = _fold(events)
    return {node: st.node_steps[node] for node in st.saves}


def _fold(events: tuple[Event, ...]) -> tuple[_State, Walk]:
    st = _State()
    labels: list[int | None] = []
    nodes: list[NodeRef | None] = []
    fenced: list[bool] = []
    finals: list[int | None] = []
    for i, ev in enumerate(events):
        label: int | None = None
        node: NodeRef | None = None
        final: int | None = None
        fence = False
        try:
            if isinstance(ev, Claim):
                _claim(st, i, ev)
            elif isinstance(ev, StopRequest):
                if ev.request_id in st.stop_ids:
                    raise NotConforming(f"stop id {ev.request_id!r} reused")
                st.stop_ids.add(ev.request_id)
            elif isinstance(ev, ThirdPartyStop):
                _third_party(st, ev)
            elif isinstance(ev, Launched):
                if ev.launch in st.launched:
                    raise NotConforming(f"launch {ev.launch!r} launched twice")
                st.launched[ev.launch] = ev.wrapper
            elif isinstance(ev, Reaped):
                _reaped(st, ev)
            else:
                w = _worker(st, ev.claim)
                fence = w.released
                label, node, final = _step(st, i, ev, w)
        except NotConforming as exc:
            raise NotConforming(f"event {i} ({ev}): {exc}") from None
        labels.append(label)
        nodes.append(node)
        fenced.append(fence)
        finals.append(final)
    return st, Walk(
        tuple(labels),
        tuple(nodes),
        tuple(fenced),
        tuple(finals),
        {c: w.exit_kind for c, w in enumerate(st.workers) if w.exit_kind is not None},
        dict(st.node_steps),
    )


def _worker(st: _State, claim: int) -> _Worker:
    if not 0 <= claim < len(st.workers):
        raise NotConforming(f"claim {claim} has not claimed")
    w = st.workers[claim]
    if w.phase is Phase.HALTED:
        raise NotConforming(f"claim {claim}'s worker has halted")
    return w


def _claim(st: _State, i: int, ev: Claim) -> None:
    if ev.claim != len(st.workers):
        raise NotConforming(
            f"claims are numbered in claim order; expected {len(st.workers)}"
        )
    if st.workers:
        prev = st.workers[-1]
        c = len(st.workers) - 1
        # The claim gate (design-v0.2.md §4, §8), as the native one passes it: the
        # latest claim has ended (its own exit, or a release) or its worker is dead.
        # A third party's report ends nothing, so over a live worker it opens
        # 0.3.0's gate but not 0.4.0's: such a history has no native counterpart.
        ended = prev.phase in (Phase.EXITED, Phase.HALTED) or c in st.released
        if not ended:
            raise NotConforming(f"claim {c} is held by a live worker (the claim gate)")
    if ev.launch is not None:
        if ev.launch in st.claimant:
            raise NotConforming(f"launch {ev.launch!r} claimed twice")
        st.claimant[ev.launch] = ev.claim
    origin = ev.origin
    if isinstance(origin, Fresh):
        st.workers.append(_Worker(ev.claim, clock=0, floor=0, position=None))
    elif isinstance(origin, FromOutside):
        if origin.step < 0:
            raise NotConforming("a step is never negative")
        st.workers.append(
            _Worker(ev.claim, clock=origin.step, floor=origin.step + 1, position=None)
        )
    elif isinstance(origin, FromSave):
        # The order at resume (§6): the checkpoint is chosen before the claim, so
        # only a save already made can be loaded.
        if origin.node not in st.saves:
            raise NotConforming(f"no save of {origin.node} before the claim")
        r = st.node_steps[origin.node]
        st.workers.append(_Worker(ev.claim, clock=r, floor=r + 1, position=origin.node))
    else:
        assert_never(origin)


def _third_party(st: _State, ev: ThirdPartyStop) -> None:
    if ev.names is not None and not 0 <= ev.names < len(st.workers):
        raise NotConforming(f"a stopped names claim {ev.names}, which has not claimed")
    if isinstance(ev.intent, Release):
        if ev.names is None:
            raise NotConforming("a release names the claim it reports dead")
        w = st.workers[ev.names]
        # A release fences a live worker that has no exit node (§4): a native
        # report_death returns None once the claim has a readable exit.
        if w.phase is not Phase.HALTED and w.exit_kind is None:
            w.released = True
        st.released.add(ev.names)


def _reaped(st: _State, ev: Reaped) -> None:
    if ev.launch not in st.launched:
        raise NotConforming(f"launch {ev.launch!r} reaped before it is launched")
    if ev.launch in st.reaped:
        raise NotConforming(f"launch {ev.launch!r} reaped twice: a process ends once")
    st.reaped.add(ev.launch)
    c = st.claimant.get(ev.launch)
    if c is not None and not st.launched[ev.launch]:
        # the launch's process is its claimant: the reap follows its end, and says how
        w = st.workers[c]
        if w.halt is None:
            raise NotConforming(f"claim {c}'s process is reaped while it lives")
        if w.halt != ev.how:
            raise NotConforming(f"claim {c}'s process ended {w.halt}, not {ev.how}")


def _step(
    st: _State, i: int, ev: WorkerEvent, w: _Worker
) -> tuple[int | None, NodeRef | None, int | None]:
    """One worker event: returns its label, the node it makes, and an Exit's
    final_step."""
    if isinstance(ev, Begin):
        if w.phase not in (Phase.LOADING, Phase.BETWEEN):
            raise NotConforming("an iteration begins only after the last one finished")
        if ev.step < w.floor:
            # run-absolute steps: labels rise within a claim, and a resumed claim's
            # continue above the step it loaded
            raise NotConforming(f"label {ev.step} below the clock's floor {w.floor}")
        w.phase, w.clock, w.tick_due, w.begun = Phase.ITERATING, ev.step, True, ev.step
        return None, None, None
    if isinstance(ev, Emit):
        return (None if ev.stepless else w.clock), None, None
    if isinstance(ev, Beat):
        if w.phase not in (Phase.ITERATING, Phase.BETWEEN) or not w.tick_due:
            raise NotConforming("a beat ticks an iteration, at most once")
        w.tick_due, w.ticked = False, w.clock
        return w.clock, None, None
    if isinstance(ev, Beacon):
        if w.phase is Phase.EXITED:
            raise NotConforming("a halted worker beats no more")
        return None, None, None
    if isinstance(ev, Finish):
        if w.phase is not Phase.ITERATING:
            raise NotConforming("no iteration is open")
        w.phase, w.floor, w.finished = Phase.BETWEEN, w.clock + 1, w.clock
        if w.released:
            return None, None, None
        node = CommitOf(w.claim, w.commits)
        w.commits += 1
        w.position = node
        st.node_steps[node] = w.clock
        if ev.saved:
            st.saves.setdefault(node, i)
        return None, node, None
    if isinstance(ev, Save):
        if w.released:
            # A released worker's save never lands: its native counterpart fences
            # itself at its next drain (§4), and saves only after a node lands (§6).
            return None, None, None
        if w.phase is Phase.BETWEEN and w.position is not None:
            st.saves.setdefault(w.position, i)
        elif w.phase is Phase.EXITED and w.exit_kind is Kind.EXTENDABLE:
            st.saves.setdefault(ExitOf(w.claim), i)
        else:
            raise NotConforming(
                "a save is of a finished iteration or an extendable exit"
            )
        return None, None, None
    if isinstance(ev, Rewind):
        if w.phase is not Phase.BETWEEN:
            raise NotConforming("a rewind falls between iterations")
        if ev.target is None:
            w.clock, w.floor, w.position = 0, 0, None
        else:
            if ev.target not in st.saves:
                raise NotConforming(f"no save of {ev.target} to rewind to")
            r = st.node_steps[ev.target]
            w.clock, w.floor, w.position = r, r + 1, ev.target
        w.tick_due = False
        return None, None, None
    if isinstance(ev, Exit):
        if w.phase not in (Phase.LOADING, Phase.ITERATING, Phase.BETWEEN):
            raise NotConforming("a worker's stopped is its halt: it writes one")
        if ev.completed and ev.error is not None:
            raise NotConforming("a completed stop carries no error (lifecycle-v0.5)")
        w.phase = Phase.EXITED
        final = _final_step(w, ev.final_step)
        if w.released:
            return None, None, final
        w.exit_kind = _exit_kind(ev.completed, ev.error)
        st.node_steps[ExitOf(w.claim)] = w.clock
        return None, ExitOf(w.claim), final
    # Halt
    w.phase, w.halt = Phase.HALTED, ev.how
    return None, None, None


def _final_step(w: _Worker, convention: FinalStep) -> int | None:
    if convention is FinalStep.YIELDED:
        return w.begun
    if convention is FinalStep.FINISHED:
        return w.finished
    if convention is FinalStep.TICKED:
        return w.ticked
    assert_never(convention)


def conforms(history: History) -> bool:
    try:
        walk(history)
    except NotConforming:
        return False
    return True
