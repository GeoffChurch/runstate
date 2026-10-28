"""A history's truth: the conforming 0.4.0 lineage that records the history's
own events, the target of commits-and-lineage.md §7. It says which iterations
finished, and where; what each claim loaded; which stops each worker read, so
that its drains follow the 0.3.0 watermarks; and who reported each death.

The referee is the prose: §2–§4 and §7's encoding rules E1 and E2. Each field
follows from the history so:

- **``starts``**: a claim's ``resume_from`` is the node its loaded save names,
  or null where it names none: a fresh start, a checkpoint from outside, or
  state no node holds, such as a fenced worker's later save, a pass's partial
  state, or a save after a torn or closed exit (§2, "The claim states ... where
  it starts"; §3, ``resume_from``: "state no node of the run holds").
- **``commits``**: one per finished iteration (§2, "``commit()`` writes one node
  per iteration"), in its claim's order:
  - an iteration finishes at its pass's close (``Finish``), or at a cut after
    its pass's save where the writer fact ``cut_save`` says the save finished
    it (§7, "the consumer decides it");
  - **``parent``** is the claim's position before it (§2, "A node's ``parent``
    is its claim's position before it");
  - **``values``** are the seqs of the claim's labelled values written since its
    last position change (§2, "A value belongs to the node whose ``values``
    names its seq"), in writing order. A value is never pending once its worker
    has written its own ``stopped`` or been fenced, and no node holds a register:
    a stepless value (§7, M3), or a stepped one its writer keeps outside every
    lineage (``Stamp.REGISTER``, the classifier's ``register``);
  - **``t``** is when the iteration ended (§3, lifecycle-v0.6: a commit's ``t``):
    its own tick's ``t`` where one dates it, else what the history's ``Undated``
    says, the claim's newest record before the commit or the log's next record
    after it (§7, the end-time fork). A commit a cut makes stands at the cut and
    is dated there, so its ``t`` is at or after every value it holds (§3: "a
    commit's ``t`` is at or after the ``t`` of every value it holds that its
    writer stamped").
- **``rewinds``**: one per ``Rewind`` (§4, ``rewind``). It moves the position to
  the node its target names and drops the pending values.
- **``exits``**: a worker's own ``stopped`` is its exit node (§2, "The worker's
  exit"), with ``parent`` the position at the exit:
  - **closed** if completed (§2), holding the pending values, or null where its
    loop discarded the open iteration (``Exit.discards``; §4, ``discard()``);
  - **torn** (null) if it carries an error, since every error exit discards its
    open iteration (§2), or if its loop discarded one;
  - **extendable** otherwise, holding the pending values;
  - **``honored``** follows from the native reads (§7, E2; §4, "``honored``
    names every stop pending at the exit, and every stop whose own condition
    held at a node or a heartbeat"): every stop a drain of its worker took in
    unspent. The drains read whatever lies before them, where 0.3.0 put it: each
    commit (§4, ``commit()`` step 1), each 0.3.0 tick and beacon, whose
    watermark records the read, and its exit (§4, the exit's step 1). The
    withdrawals spend stops in between, and an exit spends what it honours.
    Where it differs from the 0.3.0 exit's list, the difference is one of E2's
    stated costs (``Cost``), and the truth raises ``Residue`` for any other.
- **Fencing.** A release of a claim with no exit node, while its worker lives,
  fences that worker, and so does a takeover: it reads the death report at its
  next drain and writes nothing more (§4, "A released worker fences itself").
  A commit whose iteration finished before the report still lands (§4, "The
  race").
- **``deaths``**, each with its outcome and E1's kind of source:
  - **a third party's release** (``STOPPED``). A ``lost`` one is never written
    on a claim that has ended: "Evidence without a cause reports only while the
    claim is held" binds every party (§3, "Who writes a death report"). Through
    the helper (``Via.HELPER``), no report at all is written on a claim with an
    exit node: ``report_death`` "returns ``None`` when the claim has a readable
    exit" (§3). Written directly, a report with a cause is written "whether or
    not the claim has ended";
  - **a reap** (``REAP``), for the newest claim naming its launch when that
    claim runs in the launch's process, the same handle compared whole, and
    has no exit node: ``killed`` for a signal, ``errored`` for a nonzero code,
    ``lost`` for 0, only while held (§3, "The writers");
  - **the claim path's report** (``GATE``): a later claim's ``started`` stands
    only once the latest claim has ended (§4, "The claim"), so where nothing
    ended it, ``report_if_dead``'s ``lost`` stands just before (§3). After a
    takeover it fences the live worker.
- **``terminations``**: every reap, as ``supervisor.terminated``; and a third
  party's report of a process's end (``Report(reap)``) as one under a launch the
  migration mints (``launch`` None).
- **``withdrawals``**: E2 (§7). One for each id that a ``stopped`` honours,
  when that ``stopped`` is not its claim's exit node (a third party's, or a
  fenced worker's own) and the id is not already withdrawn, just after that
  ``stopped``, its answer. Every record stays where 0.3.0 put it: no stop
  moves.

``evidence`` holds each death report's full E1 text (§7, E1), which A19 keeps
out of the lineage: no fold parses it (§3). ``costs`` names each stop where a
native exit's ``honored`` differs from its 0.3.0 list, with the case of E2's
stated cost it falls in.

Nodes are named by their claim's 0.3.0 ``started`` seq and their index in it
(``CommitId``, from 0), or by their claim alone (``ExitId``); values by their
0.3.0 seqs. Two histories that share a log therefore have comparable truths:
equal ``Lineage``s need the same output.

``places`` says where the native writer puts each record, relative to the 0.3.0
log: ``At(seq)`` for a record that is that 0.3.0 record renamed, and
``After(seq, rank)`` for one it adds just after ``seq`` (``rank`` orders the
additions). It is for the structural checks and for diagnosis, and is not part
of the lineage.
"""

from __future__ import annotations

from collections import defaultdict
from enum import Enum, IntEnum
from typing import NamedTuple, TypeAlias, assert_never

from .history import (
    Beacon,
    Beat,
    Born,
    Claim,
    CommitOf,
    Drain,
    Emit,
    Event,
    Exit,
    Exited,
    ExitOf,
    Finish,
    Fold,
    History,
    Kind,
    Killed,
    Launched,
    NodeRef,
    Outcome,
    PassSave,
    Process,
    ProcessEnd,
    Reaped,
    Release,
    Stamp,
    Report,
    Rewind,
    StopRequest,
    ThirdPartyStop,
    Ticked,
    Undated,
    Via,
)
from .render import handle, render


class CommitId(NamedTuple):
    claim_seq: int
    nth: int


class ExitId(NamedTuple):
    claim_seq: int


NodeId: TypeAlias = CommitId | ExitId


class Source(Enum):
    """E1's kind: the 0.3.0 record a migrated death report stands for."""

    REAP = "reap"  # a launcher.terminated
    GATE = "gate"  # a later claim that passed 0.3.0's claim gate
    STOPPED = "stopped"  # a third party's stopped read as a release


class Start(NamedTuple):
    claim_seq: int
    resume_from: NodeId | None


class Commit(NamedTuple):
    node: CommitId
    parent: NodeId | None
    values: tuple[int, ...]
    t: float


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
    source: Source


class Terminated(NamedTuple):
    launch: str | None  # None: a third party's report, under a minted launch
    how: ProcessEnd


class Withdrawal(NamedTuple):
    """E2: a ``control.withdraw`` just after its answer, the ``stopped`` at
    ``by``."""

    request_id: str
    by: int


class Lineage(NamedTuple):
    starts: tuple[Start, ...]
    commits: tuple[Commit, ...]
    rewinds: tuple[RewindTo, ...]
    exits: tuple[ExitNode, ...]
    deaths: tuple[Died, ...]  # sorted: a verdict reads their set (§5)
    terminations: tuple[Terminated, ...]
    withdrawals: tuple[Withdrawal, ...]


class At(NamedTuple):
    """The 0.3.0 record at ``seq``, renamed."""

    seq: int


class After(NamedTuple):
    """A record the step adds just after the record at ``seq``; ``rank`` orders
    the additions."""

    seq: int
    rank: int


Place: TypeAlias = At | After
Record: TypeAlias = (
    Start | Commit | RewindTo | ExitNode | Died | Terminated | Withdrawal
)


class Slot(IntEnum):
    """Where among the records at one 0.3.0 seq a place falls, in order."""

    RECORD = 0  # the 0.3.0 record itself
    ADDED = 1  # records the step adds just after it


class Key(NamedTuple):
    """A place's position in the native log's order."""

    seq: int
    slot: Slot
    rank: int  # ADDED: the addition's rank


class Placed(NamedTuple):
    record: Record
    place: Place


class Evidence(NamedTuple):
    died: Died
    text: str  # E1: ``migrated:``, the source's kind, and its own evidence


class Case(Enum):
    """E2's stated cost (§7, "Its cost"): the four cases in which a native
    exit's ``honored`` differs from its 0.3.0 list, which the owner accepted
    (2026-10-25), named as §7 names them."""

    TICKS_EXIT = "ticks-exit"  # 1. a ticks-only exit honours a stop it did not
    # list: its native exit drains every stop before it
    CLEARED_AFTER_DRAIN = "cleared-after-drain"  # 2. an exit honours a stop
    # cleared between a commit's native drain, which read it, and the worker's
    # next 0.3.0 read (its next tick, in either loop order, or its exit), which
    # skips it
    ANSWERED_AFTER_CLAIM = "answered-after-claim"  # 3. an at-claim writer took
    # in a stop another party answered after its claim; the withdrawal at the
    # answer precedes its native drain, and its native exit omits the stop
    DISCHARGED_EARLIER = "discharged-earlier"  # 4. a later exit omits a stop an
    # earlier native exit discharged under case 1 only: a stop case 2 cleared is
    # named before any later claim, so no later 0.3.0 exit lists it
    INFERRED_INPUT_LIST = "inferred-input-list"  # 5. in a log born in 0.2.0, the
    # exit's list is the 0.2.0 -> 0.3.0 step's positional inference, not its
    # writer's, so the native exit may differ from it in either direction (the
    # controller's ruling, 2026-10-25; §7's text follows in Task 19's fix round).
    # Cases 1-4 describe writers' lists, so in such a log every difference is 5


class Cost(NamedTuple):
    """A stop where a readable exit's native ``honored`` differs from its 0.3.0
    list, and the case of E2's stated cost it falls in."""

    exit: ExitId
    request_id: str
    case: Case


class Truth(NamedTuple):
    lineage: Lineage
    places: tuple[Placed, ...]
    evidence: tuple[Evidence, ...]
    costs: tuple[Cost, ...]


def place_key(place: Place) -> Key:
    """The native log's order: ``At(s)`` follows every addition after ``s - 1``."""
    if isinstance(place, At):
        return Key(place.seq, Slot.RECORD, 0)
    if isinstance(place, After):
        return Key(place.seq, Slot.ADDED, place.rank)
    assert_never(place)


class Residue(Exception):
    """A native exit's ``honored`` differs from its 0.3.0 list outside E2's
    stated costs (§7): a gap in the prose, which the model reports and never
    encodes around."""


def _reap_outcome(how: ProcessEnd) -> Outcome:
    if isinstance(how, Killed):
        return Outcome.KILLED
    if isinstance(how, Exited):
        return Outcome.ERRORED if how.code else Outcome.LOST
    assert_never(how)


def _reap_text(how: ProcessEnd) -> str:
    if isinstance(how, Killed):
        return f"migrated: reap: signal {how.signal}"
    if isinstance(how, Exited):
        return f"migrated: reap: exit {how.code}"
    assert_never(how)


class _Drain(NamedTuple):
    """A read of the control log by a claim's native counterpart."""

    place: Place
    commit: CommitId | None  # the commit whose drain it is, if it is one
    ends: bool  # its exit's: the stops it takes in are then spent


class _Answer(NamedTuple):
    request_id: str
    by: int  # the answering stopped's seq
    place: After  # where a withdrawal just after it stands


class _ReadableExit(NamedTuple):
    claim: int
    claim_seq: int
    seq: int
    honored: frozenset[str]  # its 0.3.0 record's
    honors: Drain  # its writer's encoding of that list
    fold: Fold  # its writer's


def truth(history: History) -> Truth:
    rendered = render(history)
    w = rendered.walk
    events = history.events
    claim_seqs: dict[int, int] = {}
    processes: dict[int, Process] = {}
    position: dict[int, NodeId | None] = {}
    pending: dict[int, list[int]] = defaultdict(list)
    exited: set[int] = set()  # its own stopped is written (whether or not a node)
    exit_nodes: set[int] = set()
    ended: set[int] = set()
    made: dict[int, int] = defaultdict(int)  # claim -> the commits it has made
    drains: dict[int, list[_Drain]] = defaultdict(list)
    stop_seqs: dict[str, int] = {}  # each stop's 0.3.0 seq
    folds: dict[int, Fold] = {}
    answers: list[_Answer] = []
    readable: list[_ReadableExit] = []
    newest_claim: dict[str, int] = {}  # launch -> the newest claim naming it
    launches: dict[str, Process] = {}
    placed: list[Placed] = []
    evidence: list[Evidence] = []
    last = 0
    rank = 0

    def node_id(ref: NodeRef | None) -> NodeId | None:
        if ref is None:
            return None
        if isinstance(ref, CommitOf):
            return CommitId(claim_seqs[ref.claim], ref.nth)
        if isinstance(ref, ExitOf):
            return ExitId(claim_seqs[ref.claim])
        assert_never(ref)

    def after() -> After:
        nonlocal rank
        rank += 1
        return After(last, rank)

    def died(
        claim: int, outcome: Outcome, source: Source, text: str, place: Place
    ) -> None:
        record = Died(claim_seqs[claim], outcome, source)
        placed.append(Placed(record, place))
        evidence.append(Evidence(record, text))
        ended.add(claim)

    def release(
        claim: int, outcome: Outcome, via: Via, text: str, place: Place
    ) -> None:
        """A third party's death report, by §3's rules."""
        if via is Via.HELPER and claim in exit_nodes:
            return  # report_death "returns None when the claim has a readable exit"
        if outcome is Outcome.LOST and claim in ended:
            return  # "Evidence without a cause reports only while the claim is held"
        died(claim, outcome, Source.STOPPED, text, place)

    def commit(claim: int, t: float) -> None:
        cid = CommitId(claim_seqs[claim], made[claim])
        record = Commit(cid, position[claim], tuple(pending[claim]), t)
        place = after()
        placed.append(Placed(record, place))
        drains[claim].append(_Drain(place, cid, ends=False))
        position[claim] = cid
        pending[claim] = []
        made[claim] += 1

    for i, ev in enumerate(events):
        seq = rendered.seqs[i]
        fenced = w.fenced[i]
        cut = w.derived[i].cut
        if cut is not None:
            # a pass cut after its save, which the writer's cut_save says finished
            # its iteration: the commit lands before the cut (§4, "The race"),
            # dated there (§3, a commit's t)
            claim = cut.commit.claim
            commit(claim, _cut_time(history, rendered.seqs, claim, i, cut.end))
        if isinstance(ev, Claim):
            assert seq is not None
            prev = ev.claim - 1
            if prev >= 0 and prev not in ended:
                # §4, "The claim": the started stands only once the latest claim
                # has ended, so report_if_dead's lost stands just before it (§3)
                died(
                    prev,
                    Outcome.LOST,
                    Source.GATE,
                    f"migrated: gate: {handle(processes[prev])}",
                    after(),
                )
            claim_seqs[ev.claim] = seq
            processes[ev.claim] = ev.process
            folds[ev.claim] = ev.fold
            if ev.launch is not None:
                newest_claim[ev.launch] = ev.claim
            position[ev.claim] = node_id(w.origins[ev.claim])
            placed.append(Placed(Start(seq, position[ev.claim]), At(seq)))
        elif isinstance(ev, Emit):
            assert seq is not None
            if not (fenced or ev.stamp is not Stamp.METRIC or ev.claim in exited):
                pending[ev.claim].append(seq)
        elif isinstance(ev, (Beat, Beacon)):
            assert seq is not None
            if not fenced:
                drains[ev.claim].append(_Drain(At(seq), None, ends=False))
        elif isinstance(ev, Finish):
            if isinstance(w.derived[i].node, CommitOf):
                commit(ev.claim, _end_time(history, rendered.seqs, ev.claim, i))
        elif isinstance(ev, Rewind):
            if not fenced:
                target = node_id(w.rewinds[i])
                rewind = RewindTo(claim_seqs[ev.claim], made[ev.claim], target)
                placed.append(Placed(rewind, after()))
                position[ev.claim] = target
                pending[ev.claim] = []
        elif isinstance(ev, Exit):
            assert seq is not None
            exited.add(ev.claim)
            if isinstance(w.derived[i].node, ExitOf):
                kind = w.exit_kinds[ev.claim]
                discarded = ev.error is not None or ev.discards
                values = None if discarded else tuple(pending[ev.claim])
                exit_node = ExitNode(
                    ExitId(claim_seqs[ev.claim]), position[ev.claim], values, kind, ()
                )
                placed.append(Placed(exit_node, At(seq)))
                drains[ev.claim].append(_Drain(At(seq), None, ends=True))
                readable.append(
                    _ReadableExit(
                        ev.claim,
                        claim_seqs[ev.claim],
                        seq,
                        frozenset(rendered.honored[i]),
                        ev.honors,
                        folds[ev.claim],
                    )
                )
                exit_nodes.add(ev.claim)
                ended.add(ev.claim)
            else:
                # a fenced worker's own stopped is no node; the stops it honours
                # are spent by withdrawals (§7, E2)
                last = seq
                answers += [_Answer(x, seq, after()) for x in rendered.honored[i]]
        elif isinstance(ev, StopRequest):
            assert seq is not None
            stop_seqs[ev.request_id] = seq
        elif isinstance(ev, ThirdPartyStop):
            assert seq is not None
            intent = ev.intent
            if isinstance(intent, Release):
                assert ev.names is not None
                text = "migrated: stopped" + (
                    "" if ev.error is None else f": {ev.error}"
                )
                release(ev.names, intent.outcome, intent.via, text, At(seq))
            elif isinstance(intent, Report):
                if intent.reap is not None:
                    placed.append(Placed(Terminated(None, intent.reap), At(seq)))
            else:
                assert_never(intent)
            last = seq
            answers += [_Answer(x, seq, after()) for x in rendered.honored[i]]
        elif isinstance(ev, Launched):
            launches[ev.launch] = ev.process
        elif isinstance(ev, Reaped):
            assert seq is not None
            placed.append(Placed(Terminated(ev.launch, ev.how), At(seq)))
            last = seq
            # A launcher reports for the newest claim naming its launch, when that
            # claim's process is the launch's: the same handle, compared whole (§3).
            c = newest_claim.get(ev.launch)
            if c is not None and processes[c] == launches[ev.launch]:
                outcome = _reap_outcome(ev.how)
                if c not in exit_nodes and not (outcome is Outcome.LOST and c in ended):
                    died(c, outcome, Source.REAP, _reap_text(ev.how), after())
        if seq is not None:
            last = seq
    withdrawals = _withdrawals(answers)
    stops = {x: place_key(At(seq)) for x, seq in stop_seqs.items()}
    native = _native_fold(withdrawals, stops, drains)
    withdrawn = {wd.request_id: place_key(place) for wd, place in withdrawals}
    costs = _costs(readable, native, withdrawn, drains, history.born)
    honored = native.taken
    by_seq = {s: c for c, s in claim_seqs.items()}
    finished: list[Placed] = []
    for p in placed:
        record = p.record
        if isinstance(record, ExitNode):
            claim = by_seq[record.node.claim_seq]
            record = record._replace(honored=tuple(sorted(honored[claim])))
        finished.append(Placed(record, p.place))
    finished += [Placed(wd, place) for wd, place in withdrawals]
    finished.sort(key=lambda p: place_key(p.place))
    return Truth(_lineage(finished), tuple(finished), tuple(evidence), tuple(costs))


def _end_time(
    history: History, seqs: tuple[int | None, ...], claim: int, at: int
) -> float:
    """When the iteration whose pass closes at event ``at`` ended: its
    ``Finish``'s ``ended``, read against the 0.3.0 records' ``t`` (render's: a
    record's seq). The commit stands just after event ``at``."""
    ev = history.events[at]
    assert isinstance(ev, Finish)
    if isinstance(ev.ended, Ticked):
        tick = next(
            seqs[j]
            for j in range(at + 1, len(history.events))
            if isinstance(history.events[j], Beat)
            and _claim_of(history.events[j]) == claim
        )
        assert tick is not None
        return float(tick)
    return _bound(history, seqs, claim, at + 1, ev.ended)


def _cut_time(
    history: History, seqs: tuple[int | None, ...], claim: int, at: int, end: Undated
) -> float:
    """When the iteration that a cut at event ``at`` finishes ended: its commit
    stands just before event ``at``, after every value the pass wrote."""
    return _bound(history, seqs, claim, at, end)


def _bound(
    history: History,
    seqs: tuple[int | None, ...],
    claim: int,
    at: int,
    end: Undated,
) -> float:
    """The end-time fork's bound for a commit standing just before event ``at``:
    the claim's newest record before it, or the log's next record after it."""
    if end is Undated.OWN_LAST:
        own = [
            s
            for j, s in enumerate(seqs[:at])
            if s is not None and _claim_of(history.events[j]) == claim
        ]
        return float(own[-1])
    if end is Undated.NEXT:
        return float(next(s for s in seqs[at:] if s is not None))
    assert_never(end)


def _claim_of(ev: Event) -> int | None:
    """The claim whose worker wrote a record event, if any."""
    if isinstance(ev, (Claim, Emit, Beat, Beacon, Exit)):
        return ev.claim
    return None


def _withdrawals(answers: list[_Answer]) -> list[tuple[Withdrawal, Place]]:
    """E2: a withdrawal for each id an answer honours that none has withdrawn,
    just after its answer."""
    placed: list[tuple[Withdrawal, Place]] = []
    withdrawn: set[str] = set()
    for a in answers:
        if a.request_id not in withdrawn:  # "not already withdrawn"
            withdrawn.add(a.request_id)
            placed.append((Withdrawal(a.request_id, a.by), a.place))
    return placed


class _Spend(NamedTuple):
    """A withdrawal, spending a stop."""

    key: Key
    request_id: str


class _Read(NamedTuple):
    """A drain of a claim's native counterpart."""

    key: Key
    claim: int
    drain: _Drain


class _Native(NamedTuple):
    taken: dict[int, set[str]]  # claim -> the stops its drains took in unspent
    first: dict[int, dict[str, _Drain]]  # claim -> stop -> the drain that took it


def _native_fold(
    withdrawals: list[tuple[Withdrawal, Place]],
    stops: dict[str, Key],
    drains: dict[int, list[_Drain]],
) -> _Native:
    """What each claim's native drains took in (§4): every stop before a drain
    that no withdrawal and no earlier exit has spent. An exit spends what its
    worker took in."""
    timeline: list[_Spend | _Read] = [
        _Spend(place_key(place), wd.request_id) for wd, place in withdrawals
    ]
    for claim, ds in drains.items():
        timeline += [_Read(place_key(d.place), claim, d) for d in ds]
    timeline.sort(key=lambda e: e.key)
    spent: set[str] = set()
    taken: dict[int, set[str]] = defaultdict(set)
    first: dict[int, dict[str, _Drain]] = defaultdict(dict)
    for entry in timeline:
        if isinstance(entry, _Spend):
            spent.add(entry.request_id)
            continue
        for x, posted in stops.items():
            if posted < entry.key and x not in spent and x not in taken[entry.claim]:
                taken[entry.claim].add(x)
                first[entry.claim][x] = entry.drain
        if entry.drain.ends:
            spent |= taken[entry.claim]
    return _Native(taken, first)


def _costs(
    readable: list[_ReadableExit],
    native: _Native,
    withdrawn: dict[str, Key],
    drains: dict[int, list[_Drain]],
    born: Born,
) -> list[Cost]:
    """Each stop where a readable exit's native ``honored`` differs from its
    0.3.0 list, with the case of E2's stated cost it falls in (§7); a
    difference in none raises ``Residue``."""
    costs: list[Cost] = []
    discharged: dict[str, _Discharge] = {}  # stops honoured natively only
    for r in readable:
        node = ExitId(r.claim_seq)
        taken = native.taken[r.claim]
        if born is Born.V0_2_0:
            inferred = sorted(taken ^ r.honored)
            costs += [Cost(node, x, Case.INFERRED_INPUT_LIST) for x in inferred]
            continue
        for x in sorted(taken - r.honored):
            first = native.first[r.claim][x]
            case = _honoured_case(r, x, first, withdrawn, drains)
            costs.append(Cost(node, x, case))
            discharged[x] = _Discharge(r.seq, case)
        for x in sorted(r.honored - taken):
            costs.append(Cost(node, x, _omitted_case(r, x, withdrawn, discharged)))
    return costs


class _Discharge(NamedTuple):
    """A native exit that honoured a stop its 0.3.0 list omits."""

    seq: int
    case: Case


def _honoured_case(
    r: _ReadableExit,
    x: str,
    first: _Drain,
    withdrawn: dict[str, Key],
    drains: dict[int, list[_Drain]],
) -> Case:
    """Why a native exit honours a stop its 0.3.0 list omits."""
    read = place_key(first.place)
    if first.commit is not None and x in withdrawn:
        # case 2: the commit's drain read it, and it was cleared before the
        # worker's next 0.3.0 read: a tick or beacon, or an exit that drains
        reads = [
            place_key(d.place)
            for d in drains[r.claim]
            if d.commit is None and (not d.ends or r.honors is Drain.EXIT)
        ]
        later = [key for key in reads if key > read]
        if later and read < withdrawn[x] < min(later):
            return Case.CLEARED_AFTER_DRAIN
    if r.honors is Drain.TICKS:
        return Case.TICKS_EXIT
    raise Residue(
        f"the native exit of claim {r.claim} honours {x!r}, which its 0.3.0 "
        "list omits, outside E2's stated costs"
    )


def _omitted_case(
    r: _ReadableExit,
    x: str,
    withdrawn: dict[str, Key],
    discharged: dict[str, _Discharge],
) -> Case:
    """Why a native exit omits a stop its 0.3.0 list names. An earlier native
    exit that discharged it before this claim is the cause, whatever came after
    the claim."""
    if x in discharged and discharged[x].seq < r.claim_seq:
        if discharged[x].case is not Case.TICKS_EXIT:
            raise Residue(
                f"{x!r}, which a later exit lists, was discharged earlier under "
                f"{discharged[x].case.value}: E2 states case 1 alone"
            )
        return Case.DISCHARGED_EARLIER
    claimed, exited = place_key(At(r.claim_seq)), place_key(At(r.seq))
    if r.fold is Fold.AT_CLAIM and x in withdrawn and claimed < withdrawn[x] < exited:
        return Case.ANSWERED_AFTER_CLAIM
    raise Residue(
        f"the native exit of claim {r.claim} omits {x!r}, which its 0.3.0 list "
        "names, outside E2's stated costs"
    )


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
                key=lambda d: (d.claim_seq, d.outcome.value, d.source.value),
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
    lineage; at most one exit per claim; and §3's bound on a commit's ``t``, at
    or after every value it holds (render dates a value by its seq). Empty when
    it is well formed."""
    found: list[str] = []
    where: dict[NodeId, Key] = {}
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

    def names_extendable(what: str, node: NodeId | None, key: Key) -> None:
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
                if place_key(At(v)) >= key:
                    found.append(f"{r.node} commits seq {v}, which follows it")
            names_extendable(f"{r.node}'s parent", r.parent, key)
            if isinstance(r, Commit) and r.values and r.t < max(r.values):
                found.append(
                    f"{r.node}'s t {r.t} precedes seq {max(r.values)}, which it holds"
                )
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
