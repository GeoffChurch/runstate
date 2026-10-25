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
    has written its own ``stopped`` or been fenced, and stepless values are
    registers (§7, M3);
  - **``t``** is when the iteration ended (§3, lifecycle-v0.6: a commit's ``t``):
    its own tick's ``t`` where one dates it, else what the history's ``Ended``
    says, the claim's newest record before it or the log's next record (§7, the
    end-time fork).
- **``rewinds``**: one per ``Rewind`` (§4, ``rewind``). It moves the position to
  the node its target names and drops the pending values.
- **``exits``**: a worker's own ``stopped`` is its exit node (§2, "The worker's
  exit"), with ``parent`` the position at the exit:
  - **closed** if completed (§2), holding the pending values, or null where its
    loop discarded the open iteration (``Exit.discards``; §4, ``discard()``);
  - **torn** (null) if it carries an error, since every error exit discards its
    open iteration (§2), or if its loop discarded one;
  - **extendable** otherwise, holding the pending values;
  - **``honored``** is what the native fold gives (§4, "``honored`` names every
    stop pending at the exit, and every stop whose own condition held at a node
    or a heartbeat"): every stop a drain of its worker took in unspent. Its
    drains follow the 0.3.0 history (§7, the target's third item): each commit
    (§4, ``commit()`` step 1), each 0.3.0 tick and beacon, whose watermark
    records the read, and its exit (§4, the exit's step 1). The withdrawals E2
    places spend stops in between.
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
- **``withdrawals``**: one for each id that a ``stopped`` which is not its
  claim's exit node honours, a third party's or a fenced worker's own, placed
  by E2 (§7): just after that ``stopped``, its answer, unless a readable exit's
  ``honored`` would then be false. Where a readable exit honours the stop
  though every drain of its worker that reads it follows the answer, none is
  written; where one omits it though a drain of its worker reads it before the
  answer, it stands just before the first such drain. Where two exits admit no
  place, the truth raises ``NoCounterpart``: a fork the consumer resolves.

``evidence`` holds each death report's full E1 text (§7, E1), which A19 keeps
out of the lineage: no fold parses it (§3).

Nodes are named by their claim's 0.3.0 ``started`` seq and their index in it
(``CommitId``, from 0), or by their claim alone (``ExitId``); values by their
0.3.0 seqs. Two histories that share a log therefore have comparable truths:
equal ``Lineage``s need the same output.

``places`` says where the native writer puts each record, relative to the 0.3.0
log: ``At(seq)`` for a record that is that 0.3.0 record renamed, and ``After(seq,
rank)`` for one it adds just after ``seq`` (``rank`` orders the additions). It is
for the structural checks and for diagnosis, and is not part of the lineage.
"""

from __future__ import annotations

from collections import defaultdict
from enum import Enum
from typing import NamedTuple, TypeAlias, assert_never

from .history import (
    Beacon,
    Beat,
    Claim,
    CommitOf,
    Emit,
    Ended,
    Event,
    Exit,
    Exited,
    ExitOf,
    Finish,
    History,
    Kind,
    Killed,
    Launched,
    NoCounterpart,
    NodeRef,
    Outcome,
    PassSave,
    Process,
    ProcessEnd,
    Reaped,
    Release,
    Report,
    Rewind,
    StopRequest,
    ThirdPartyStop,
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
    request_id: str
    by: int  # the seq of the stopped that answered it
    before: CommitId | int | None  # None: just after its answer; else just before
    # that commit's drain, or the 0.3.0 record (a heartbeat or an exit) at that seq


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
    rank: float


Place: TypeAlias = At | After
Record: TypeAlias = (
    Start | Commit | RewindTo | ExitNode | Died | Terminated | Withdrawal
)


class Placed(NamedTuple):
    record: Record
    place: Place


class Evidence(NamedTuple):
    died: Died
    text: str  # E1: ``migrated:``, the source's kind, and its own evidence


class Truth(NamedTuple):
    lineage: Lineage
    places: tuple[Placed, ...]
    evidence: tuple[Evidence, ...]


def place_key(place: Place) -> tuple[int, float]:
    """The native log's order: ``At(s)`` follows every addition after ``s - 1``."""
    if isinstance(place, At):
        return (place.seq, -1.0)
    return (place.seq, place.rank)


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
    key: tuple[int, float]
    before: CommitId | int  # how a withdrawal placed just before it names it


class _Answer(NamedTuple):
    request_id: str
    by: int  # the answering stopped's seq
    rank: float  # the rank a withdrawal just after it takes


class _ReadableExit(NamedTuple):
    claim: int
    seq: int
    honored: frozenset[str]  # its 0.3.0 record's


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
    stop_keys: dict[str, tuple[int, float]] = {}
    answers: list[_Answer] = []
    readable: list[_ReadableExit] = []
    newest_claim: dict[str, int] = {}  # launch -> the newest claim naming it
    launches: dict[str, Process] = {}
    placed: list[Placed] = []
    evidence: list[Evidence] = []
    last = 0
    rank = 0.0

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

    def commit(claim: int, dating: int) -> None:
        cid = CommitId(claim_seqs[claim], made[claim])
        t = _end_time(history, rendered.seqs, claim, dating)
        record = Commit(cid, position[claim], tuple(pending[claim]), t)
        place = after()
        placed.append(Placed(record, place))
        drains[claim].append(_Drain(place_key(place), cid))
        position[claim] = cid
        pending[claim] = []
        made[claim] += 1

    for i, ev in enumerate(events):
        seq = rendered.seqs[i]
        fenced = w.fenced[i]
        cut = w.derived[i].cut
        if cut is not None:
            # a pass cut after its save, which the writer's cut_save says finished
            # its iteration: the commit lands before the cut (§4, "The race")
            commit(cut.commit.claim, cut.save)
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
            if ev.launch is not None:
                newest_claim[ev.launch] = ev.claim
            position[ev.claim] = node_id(w.origins[ev.claim])
            placed.append(Placed(Start(seq, position[ev.claim]), At(seq)))
        elif isinstance(ev, Emit):
            assert seq is not None
            if not (fenced or ev.stepless or ev.claim in exited):
                pending[ev.claim].append(seq)
        elif isinstance(ev, (Beat, Beacon)):
            assert seq is not None
            if not fenced:
                drains[ev.claim].append(_Drain((seq, -1.0), seq))
        elif isinstance(ev, Finish):
            if isinstance(w.derived[i].node, CommitOf):
                commit(ev.claim, i)
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
                drains[ev.claim].append(_Drain((seq, -1.0), seq))
                readable.append(
                    _ReadableExit(ev.claim, seq, frozenset(rendered.honored[i]))
                )
                exit_nodes.add(ev.claim)
                ended.add(ev.claim)
            else:
                # a fenced worker's own stopped is no node; the stops it honours
                # are spent by withdrawals (§7, E2)
                last = seq
                answers += [_Answer(x, seq, after().rank) for x in rendered.honored[i]]
        elif isinstance(ev, StopRequest):
            assert seq is not None
            stop_keys[ev.request_id] = (seq, -1.0)
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
            answers += [_Answer(x, seq, after().rank) for x in rendered.honored[i]]
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
    withdrawals = _place_withdrawals(answers, stop_keys, readable, drains)
    honored = _native_fold(withdrawals, stop_keys, readable, drains)
    for answer in answers:
        for exit_ in readable:
            if (answer.request_id in exit_.honored) != (
                answer.request_id in honored[exit_.claim]
            ):
                raise NoCounterpart(
                    f"E2 admits no place for the withdrawal of {answer.request_id!r}: "
                    "which worker's read the lineage records is a fork"
                )
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
    return Truth(_lineage(finished), tuple(finished), tuple(evidence))


def _end_time(
    history: History, seqs: tuple[int | None, ...], claim: int, at: int
) -> float:
    """When the iteration that the event at ``at`` dates ended: its ``Ended``,
    read against the 0.3.0 records' ``t`` (render's: a record's seq)."""
    ev = history.events[at]
    assert isinstance(ev, (Finish, PassSave))
    if ev.ended is Ended.TICKED:
        tick = next(
            seqs[j]
            for j in range(at + 1, len(history.events))
            if isinstance(history.events[j], Beat)
            and _claim_of(history.events[j]) == claim
        )
        assert tick is not None
        return float(tick)
    if ev.ended is Ended.OWN_LAST:
        own = [
            s
            for j, s in enumerate(seqs[: at + 1])
            if s is not None and _claim_of(history.events[j]) == claim
        ]
        return float(own[-1])
    if ev.ended is Ended.NEXT:
        nxt = next(s for s in seqs[at + 1 :] if s is not None)
        return float(nxt)
    assert_never(ev.ended)


def _claim_of(ev: Event) -> int | None:
    """The claim whose worker wrote a record event, if any."""
    if isinstance(ev, (Claim, Emit, Beat, Beacon, Exit)):
        return ev.claim
    return None


def _place_withdrawals(
    answers: list[_Answer],
    stop_keys: dict[str, tuple[int, float]],
    readable: list[_ReadableExit],
    drains: dict[int, list[_Drain]],
) -> list[tuple[Withdrawal, Place]]:
    """E2: each withdrawal at the nearest place to its answer that keeps every
    readable exit's ``honored`` true."""
    placed: list[tuple[Withdrawal, Place]] = []
    for a in answers:
        posted = stop_keys[a.request_id]
        answer = (a.by, -1.0)
        keep_none = False
        earliest: _Drain | None = None
        for r in readable:
            own = [d for d in drains[r.claim] if d.key > posted]
            before = [d for d in own if d.key < answer]
            if a.request_id in r.honored:
                if own and not before:
                    keep_none = True  # the exit spends the stop: none is written
            elif before:
                first = min(before, key=lambda d: d.key)
                if earliest is None or first.key < earliest.key:
                    earliest = first
        if keep_none and earliest is not None:
            raise NoCounterpart(
                f"E2 admits no place for the withdrawal of {a.request_id!r}: "
                "which worker's read the lineage records is a fork"
            )
        if keep_none:
            continue
        if earliest is None:
            placed.append((Withdrawal(a.request_id, a.by, None), After(a.by, a.rank)))
            continue
        seq, rank = earliest.key
        place: Place = After(seq - 1, 1e9) if rank < 0 else After(seq, rank - 0.5)
        placed.append((Withdrawal(a.request_id, a.by, earliest.before), place))
    return placed


def _native_fold(
    withdrawals: list[tuple[Withdrawal, Place]],
    stop_keys: dict[str, tuple[int, float]],
    readable: list[_ReadableExit],
    drains: dict[int, list[_Drain]],
) -> dict[int, set[str]]:
    """The stops each native exit honours: those a drain of its worker took in
    unspent (§4), with stops spent by the withdrawals and by earlier exits."""
    exits = {(r.seq, -1.0): r.claim for r in readable}
    timeline: list[tuple[tuple[int, float], int, str, int]] = []
    for wd, place in withdrawals:
        timeline.append((place_key(place), 0, wd.request_id, -1))
    for claim, ds in drains.items():
        for d in ds:
            timeline.append((d.key, 1, "", claim))
    timeline.sort()
    spent: set[str] = set()
    taken: dict[int, set[str]] = defaultdict(set)
    for key, kind, request_id, claim in timeline:
        if kind == 0:
            spent.add(request_id)
            continue
        for x, posted in stop_keys.items():
            if posted < key and x not in spent:
                taken[claim].add(x)
        if key in exits:
            spent |= taken[claim]
    return {r.claim: taken[r.claim] for r in readable}


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
    lineage; at most one exit per claim. Empty when it is well formed."""
    found: list[str] = []
    where: dict[NodeId, tuple[int, float]] = {}
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

    def names_extendable(
        what: str, node: NodeId | None, key: tuple[int, float]
    ) -> None:
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
