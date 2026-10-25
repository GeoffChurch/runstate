"""The history type: what conforming 0.3.0 writers did, one event at a time.

A history is the run as it happened: one sequence of events, in the order the
world and the log share. A worker's events carry the index of its claim (0 for
the run's first ``lifecycle.started``). Events that write a record become the
0.3.0 log (``render.py``); the others (an iteration finishing, a save landing,
a worker or its process ending) write nothing, and are exactly what a 0.3.0 log
cannot show. What a native 0.4.0 writer would have recorded follows from all of
them (``truth.py``).

Every writer choice is an event or a declared field of one:

- **the claim**: its process (``Claim.process``, which several claims may share),
  the gate's verdict (``Claim.gate``: the latest claim had ended or its process
  had, or the gate judged a live worker dead: a takeover), the resume origin (a
  fresh start, a save this log's claims made, or a checkpoint copied in from
  outside), and what its clock reads before its first iteration;
- **per iteration**: its label (``Begin.step``: a stride skips labels), which
  values it emits and where (``Emit``), whether and where it beats (``Beat``
  before or after the body, or none, with its watermark), whether it saves
  (``Finish.saved`` in the body, ``Save`` after it) with or without a record (a
  stepless ``Emit``), and whether it rewinds (``Rewind``);
- **how a claim ends**: the worker ends (``Halt``) anywhere in its program, its
  process with it or not, with or without its own ``stopped`` before (``Exit``,
  completed or not, error or not, with a ``final_step`` and an ``honored``
  convention); a third party's ``stopped`` names it (``ThirdPartyStop``, a
  release or a report); a later claim takes it over; its launch is reaped
  (``Reaped``);
- **the native counterpart's own choices** where §3 and §4 leave the 0.4.0
  writer one: whether a clean exit inside an open iteration discards it
  (``Exit.discards``); whether its driver drains at a ``heartbeat()`` at each
  0.3.0 tick (``Claim.cadence``); and whether a third party writes its death
  report through ``report_death`` or directly (``Release.via``).

What 0.3.0 pins (commits-and-lineage.md §7, "What 0.3.0 pins"), ``walk``
enforces, raising ``NotConforming``: names are the writers' own (a worker's
records name its claim); a worker's ``stopped`` is its halt (no beat, iteration
or second ``stopped`` after it); ``completed`` is the worker's alone and carries
no error; the claim gate (a claim starts once the latest has ended or its worker
is judged dead, which a takeover's verdict may judge wrongly); the step clock (a
value's and a beat's step is the worker's clock); and run-absolute steps (a
resumed claim's labels continue above the state it loaded).

Where a history is conforming 0.3.0 but a part of it has no native 0.4.0
counterpart, the walk says so instead, raising ``NoCounterpart``. A claim loads
a node that exists when it claims (0.3.0's order is claim, then load:
run-episodes.md §3; the native order of §6 chooses first, and the two read
alike wherever the node already exists). A node a fenced worker would have made
does not exist natively, so a successor that loaded such a save loaded what no
node names: a checkpoint from outside the log (``FromOutside``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import NamedTuple, TypeAlias, assert_never

# ----- references to nodes ---------------------------------------------------------


@dataclass(frozen=True)
class CommitOf:
    """The ``nth`` commit (from 0) a claim's worker made: its ``nth`` finished
    iteration, counting only those a native writer could record (a fenced
    worker's are not, ``walk``)."""

    claim: int
    nth: int


@dataclass(frozen=True)
class ExitOf:
    """A claim's exit node: its worker's own ``stopped``."""

    claim: int


NodeRef: TypeAlias = CommitOf | ExitOf

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


class Cadence(Enum):
    """Where the native counterpart drains control, besides each ``commit()``
    and its exit: its own driver's choice, which §4 leaves to it. The drains
    decide which stops its exit honours (``truth.py``)."""

    COMMITS = "commits"  # at each commit only: 0.4.0's ``steps()``, ``commit()`` alone
    TICKS = "ticks"  # also at a ``heartbeat()`` at each logged 0.3.0 tick


class Clock(Enum):
    """What the worker's clock reads before its first iteration."""

    LOADED = "loaded"  # the loaded node's step (0 for a fresh start)
    ZERO = "zero"  # 0 whatever it loaded: a counter that starts at 0 (mycooc's)


@dataclass(frozen=True)
class Fresh:
    """A fresh start: no state."""


@dataclass(frozen=True)
class FromSave:
    """The claim loaded a save this log's claims made, of ``node``, which exists
    when it claims. Its first iteration is labelled above that node's step."""

    node: NodeRef


@dataclass(frozen=True)
class FromOutside:
    """The claim loaded a checkpoint standing at ``step`` that no node of the
    log names: copied in from outside, or saved by a worker its claim had
    already been taken from (a node its native counterpart never made)."""

    step: int


Origin: TypeAlias = Fresh | FromSave | FromOutside

# ----- closed choices ----------------------------------------------------------------


class FinalStep(Enum):
    """What a worker's own ``stopped`` writes as its ``final_step``."""

    YIELDED = "yielded"  # the last iteration it began (the reference Worker's)
    FINISHED = "finished"  # the last iteration it finished (mycooc's writers)
    TICKED = "ticked"  # the last step it beat (a manual-tick worker)


class Drain(Enum):
    """Which stops a worker's own ``stopped`` lists in ``honored``: those it
    drained and no ``stopped`` had named before its claim."""

    EXIT = "exit"  # every stop up to its exit (the reference Worker's ``_die``)
    TICKS = "ticks"  # only those its beats drained, before its newest beat


class Watermark(Enum):
    """What a heartbeat writes as its ``consumed_seq``: how far its writer read
    the control order."""

    CONTROL = "control"  # the newest control record before it (the reference
    # Worker's cursor, which reads from the log's start)
    CLAIMED = "claimed"  # the newest control record since its claim, else 0 (a
    # cursor that starts at the claim)
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
    """How a third party writes its death report natively (§3)."""

    HELPER = "helper"  # through ``report_death``, which writes nothing on a claim
    # with a readable exit, nor a ``lost`` on one that has ended (the reclaim
    # tool's, the launchers')
    DIRECT = "direct"  # a ``supervisor.died`` it writes itself: "any party ...
    # may write one", and a cause reports "whether or not the claim has ended"


@dataclass(frozen=True)
class Release:
    """A third party's ``stopped`` that reports its claim dead: 0.4.0's
    ``supervisor.died`` with ``outcome``, written ``via`` the helper or
    directly. Its writer may be wrong, and a live worker it names is then
    released."""

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
    re-emitting ``launch``, past the gate's ``gate`` verdict, with the state of
    ``origin``. ``cadence`` is its native counterpart's drain cadence."""

    claim: int
    process: Process
    gate: Gate
    origin: Origin
    clock: Clock
    launch: str | None
    cadence: Cadence


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
class Finish:
    """The iteration's body is done: the worker's state now includes it. With
    ``saved``, the body's last act was a save of that state, which is durable.
    It is where the native counterpart calls ``commit()``, whose drain reads any
    death report naming the claim (§4)."""

    claim: int
    saved: bool


@dataclass(frozen=True)
class Save:
    """A save of the worker's state outside the body: after its tick, after its
    exit, or anywhere else. It names a node only where its state is one."""

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
    """The worker writes its own ``stopped``, its halt. ``discards`` is the
    native counterpart's choice: whether it calls ``w.discard()`` first (§4), as
    a worker that abandons an unfinished iteration and leaves cleanly does; it
    applies only to a clean exit inside an open iteration."""

    claim: int
    completed: bool
    error: str | None
    final_step: FinalStep
    honors: Drain
    discards: bool


@dataclass(frozen=True)
class Halt:
    """The worker ends: nothing of it follows. ``how`` is how its process ended,
    or None when the process runs on (it runs another claim's worker, or the
    worker was abandoned in it)."""

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
    """A launcher writes ``launcher.launched`` for ``process``: the claimant's
    when a claim naming the launch runs in it, else a wrapper's."""

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
    Begin | Emit | Beat | Beacon | Finish | Save | Rewind | Exit | Halt
)


@dataclass(frozen=True)
class History:
    events: tuple[Event, ...]


class NotConforming(Exception):
    """The history is not one conforming 0.3.0 writers produce: the message says
    which rule it breaks, at which event."""


class NoCounterpart(NotConforming):
    """The history is conforming 0.3.0, but a part of it has no native 0.4.0
    counterpart the model can state a truth for."""


_RESERVED = ("lifecycle.", "control.", "launcher.")

# ----- the walk ----------------------------------------------------------------------


class Phase(Enum):
    LOADING = "loading"  # claimed, no iteration begun yet
    ITERATING = "iterating"  # an iteration begun and not finished
    BETWEEN = "between"  # between iterations (its last one finished)
    EXITED = "exited"  # its own ``stopped`` written; the worker lives on
    HALTED = "halted"  # the worker ended


def _exit_kind(completed: bool, error: str | None, discards: bool) -> Kind:
    if completed:
        return Kind.CLOSED
    if error is not None or discards:
        return Kind.TORN
    return Kind.EXTENDABLE


@dataclass
class _Worker:
    """One claim's worker, as the walk folds it."""

    claim: int
    process: Process
    clock: int  # the step a labelled value or a beat stamps
    floor: int  # the lowest label its next iteration may take
    position: NodeRef | None  # where its native counterpart stands
    state: NodeRef | None  # the node a save of its state now names, if any
    phase: Phase = Phase.LOADING
    tick_due: bool = False  # the open iteration has not beaten yet
    commits: int = 0
    exited: bool = False  # it wrote its own stopped
    exit_kind: Kind | None = None  # its exit node's kind, once a native one exists
    released: bool = False  # fenced: a release or a takeover reached it alive
    begun: int | None = None
    finished: int | None = None
    ticked: int | None = None


class Derived(NamedTuple):
    """What one event derives."""

    label: int | None  # the step an Emit, a Beat or a Stray stamps (None: stepless)
    node: NodeRef | None  # the node a Finish or an Exit makes, if any
    final_step: int | None  # the final_step an Exit writes


_NOTHING = Derived(None, None, None)


class Walk(NamedTuple):
    """What the walk derives, per event (index-aligned with ``history.events``)."""

    derived: tuple[Derived, ...]
    fenced: tuple[bool, ...]  # the event's worker was fenced before it
    exit_kinds: dict[int, Kind]  # claim -> its native exit node's kind
    steps: dict[NodeRef, int]  # each node's step: its iteration's, or the exit's clock


@dataclass
class _State:
    workers: list[_Worker] = field(default_factory=list)
    saves: dict[NodeRef, int] = field(default_factory=dict)  # node -> event index
    node_steps: dict[NodeRef, int] = field(default_factory=dict)
    stop_ids: set[str] = field(default_factory=set)
    launched: dict[str, Process] = field(default_factory=dict)
    reaped: set[str] = field(default_factory=set)
    released: set[int] = field(default_factory=set)  # claims a release named
    ended: dict[Process, ProcessEnd] = field(default_factory=dict)


def walk(history: History) -> Walk:
    """Fold the history, checking that conforming writers produce it and that
    it has a native counterpart; raise ``NotConforming`` otherwise."""
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
    derived: list[Derived] = []
    fenced: list[bool] = []
    for i, ev in enumerate(events):
        d, fence = _NOTHING, False
        try:
            if isinstance(ev, Claim):
                _claim(st, ev)
            elif isinstance(ev, StopRequest):
                if ev.request_id in st.stop_ids:
                    raise NotConforming(f"stop id {ev.request_id!r} reused")
                st.stop_ids.add(ev.request_id)
            elif isinstance(ev, ThirdPartyStop):
                _third_party(st, ev)
            elif isinstance(ev, Launched):
                if ev.launch in st.launched:
                    raise NotConforming(f"launch {ev.launch!r} launched twice")
                st.launched[ev.launch] = ev.process
            elif isinstance(ev, Reaped):
                _reaped(st, ev)
            elif isinstance(ev, Stray):
                d = Derived(ev.step, None, None)
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
    return st, Walk(
        tuple(derived),
        tuple(fenced),
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


def _claim(st: _State, ev: Claim) -> None:
    if ev.claim != len(st.workers):
        raise NotConforming(
            f"claims are numbered in claim order; expected {len(st.workers)}"
        )
    if ev.process in st.ended:
        raise NotConforming(f"process {ev.process} has ended")
    _gate(st, ev.gate)
    origin = ev.origin
    if isinstance(origin, Fresh):
        loaded, position = 0, None
        floor = 0
    elif isinstance(origin, FromOutside):
        if origin.step < 0:
            raise NotConforming("a step is never negative")
        loaded, position = origin.step, None
        floor = loaded + 1
    elif isinstance(origin, FromSave):
        if origin.node not in st.saves:
            raise NoCounterpart(f"no save of {origin.node} exists at the claim")
        loaded, position = st.node_steps[origin.node], origin.node
        floor = loaded + 1
    else:
        assert_never(origin)
    clock = loaded if ev.clock is Clock.LOADED else 0
    st.workers.append(
        _Worker(ev.claim, ev.process, clock, floor, position=position, state=position)
    )


def _gate(st: _State, gate: Gate) -> None:
    """The claim gate (design-v0.2.md §4, §8; run-episodes.md §3): a claim is
    taken once the latest has ended or its worker is judged dead. FREE says it
    had ended, or its process had; TAKEOVER says neither, and the gate judged
    it dead anyway. Its native counterpart is the claim path's death report
    (§4, ``report_if_dead``), which fences a live worker."""
    if not st.workers:
        if gate is not Gate.FREE:
            raise NotConforming("the run's first claim takes nothing over")
        return
    c = len(st.workers) - 1
    prev = st.workers[c]
    free = prev.exited or c in st.released or prev.process in st.ended
    if gate is Gate.FREE and not free:
        raise NotConforming(f"claim {c} is held and its process lives: a takeover")
    if gate is Gate.TAKEOVER:
        if free:
            raise NotConforming(f"claim {c} had ended or its process had: no takeover")
        if prev.phase is not Phase.HALTED:
            prev.released = True


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
    process = st.launched[ev.launch]
    if any(w.process == process for w in st.workers):
        # the process ran a claim: the reap follows its end, and says how
        if process not in st.ended:
            raise NotConforming(f"process {process} is reaped while it lives")
        if st.ended[process] != ev.how:
            raise NotConforming(
                f"process {process} ended {st.ended[process]}, not {ev.how}"
            )


def _step(st: _State, i: int, ev: WorkerEvent, w: _Worker) -> Derived:
    if isinstance(ev, Begin):
        if w.phase not in (Phase.LOADING, Phase.BETWEEN):
            raise NotConforming("an iteration begins only after the last one finished")
        if ev.step < w.floor:
            # run-absolute steps: labels rise within a claim, and a resumed claim's
            # continue above the step it loaded
            raise NotConforming(f"label {ev.step} below the clock's floor {w.floor}")
        w.phase, w.clock, w.tick_due, w.begun = Phase.ITERATING, ev.step, True, ev.step
        w.state = None  # a save inside the body is of partial state
        return _NOTHING
    if isinstance(ev, Emit):
        return Derived(None if ev.stepless else w.clock, None, None)
    if isinstance(ev, Beat):
        if w.phase not in (Phase.ITERATING, Phase.BETWEEN) or not w.tick_due:
            raise NotConforming("a beat ticks an iteration, at most once")
        w.tick_due, w.ticked = False, w.clock
        return Derived(w.clock, None, None)
    if isinstance(ev, Beacon):
        if w.phase is Phase.EXITED:
            raise NotConforming("a halted worker beats no more")
        return _NOTHING
    if isinstance(ev, Finish):
        if w.phase is not Phase.ITERATING:
            raise NotConforming("no iteration is open")
        w.phase, w.floor, w.finished = Phase.BETWEEN, w.clock + 1, w.clock
        if w.released:
            return _NOTHING  # fenced: its native counterpart commits nothing more
        node = CommitOf(w.claim, w.commits)
        w.commits += 1
        w.position = w.state = node
        st.node_steps[node] = w.clock
        if ev.saved:
            st.saves.setdefault(node, i)
        return Derived(None, node, None)
    if isinstance(ev, Save):
        if w.phase is Phase.EXITED and (
            w.released or w.exit_kind is not Kind.EXTENDABLE
        ):
            return _NOTHING  # state no native node names
        if w.state is not None:
            # A fenced worker's save of a node it made before its fence lands:
            # natively the save runs inside the commit that made the node (§6).
            st.saves.setdefault(w.state, i)
        return _NOTHING
    if isinstance(ev, Rewind):
        if w.phase is not Phase.BETWEEN:
            raise NotConforming("a rewind falls between iterations")
        if ev.target is None:
            w.clock, w.floor = 0, 0
        else:
            if ev.target not in st.saves:
                raise NoCounterpart(f"no save of {ev.target} exists to rewind to")
            w.clock = st.node_steps[ev.target]
            w.floor = w.clock + 1
        w.state, w.tick_due = ev.target, False
        if not w.released:
            w.position = ev.target
        return _NOTHING
    if isinstance(ev, Exit):
        if w.phase not in (Phase.LOADING, Phase.ITERATING, Phase.BETWEEN):
            raise NotConforming("a worker's stopped is its halt: it writes one")
        if ev.completed and ev.error is not None:
            raise NotConforming("a completed stop carries no error (lifecycle-v0.5)")
        if ev.discards and (ev.error is not None or w.phase is not Phase.ITERATING):
            raise NotConforming(
                "only a clean exit inside an open iteration discards it"
            )
        final = _final_step(w, ev.final_step)
        w.phase, w.exited = Phase.EXITED, True
        if w.released:
            return Derived(None, None, final)
        w.exit_kind = _exit_kind(ev.completed, ev.error, ev.discards)
        st.node_steps[ExitOf(w.claim)] = w.clock
        if w.exit_kind is Kind.EXTENDABLE:
            w.state = ExitOf(w.claim)
        return Derived(None, ExitOf(w.claim), final)
    if isinstance(ev, Halt):
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
        return _NOTHING
    assert_never(ev)


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
