"""The memoizer of log format 0.4.0, staged (docs/specs/commits-and-lineage.md §5,
"The reads": ``history`` and ``ensure``; §3, "The writers").

STAGED. Task 19 of docs/plans/2026-10-28-layer2-live.md moves this module over
``runstate/memoizer.py``.

Two functions over the topic log, which join through the log, never by piping
values. ``history`` replays a schedule over a lineage's nodes, as live evaluation
evaluates it, and returns the elements of a stream at the nodes where it fires: a
one-shot read. ``ensure`` reads first, and drives a producer on a miss until the
newest claim's lineage holds a ``Target``: it returns ``Reached`` or ``Unreachable``,
never elements, since a target may count ``Commits()``, which has none, and the
caller reads anything at ``AtNode(node)``, which is permanent.

THE SEAM. A ``Producer`` is anything with ``.channel``, ``.run_id`` and
``.extend(target)``, which returns a liveness handle, never ``None``, carrying the
launch id of the drive it launched, or ``None`` for a foreign handle, whose drives
are attributed to no claim. ``launch_producer`` is the default, over a
``Launcher[D]`` whose target is ``Drivable``; ``foreign_episode`` is the handle of a
claim the producer did not launch.

A DRIVE'S CLAIM is the newest claim naming its launch (``launcher.launch_claim``).
While it is held, ``ensure`` waits, whatever the handle says: an ended handle may be
a wrapper's, and the claimant's launcher reports its death (§3), as ``ensure`` itself
does through ``report_if_dead`` on each poll. It never reports on the strength of a
handle.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Generic, NamedTuple, Protocol, TypeVar

from ..channel import Channel, Envelope
from .claims import backward, endings, named_claim
from .launcher import (
    Drivable,
    Launcher,
    launch_claim,
    relaunch_if_needed,
    report_if_dead,
)
from .observables import (
    Element,
    HeadFold,
    HeadStrategy,
    PrefixLen,
    RunResult,
    claim_verdict,
    latest_episode,
)
from .vocabulary.coordinate import Commits, Target
from .vocabulary.payloads import Outcome, Started, Topic, Value
from .vocabulary.schedule import (
    Condition,
    Coordinates,
    Subscription,
    parse_schedule,
)
from .walk import coordinates, members, verdict_parse, walk

if TYPE_CHECKING:
    from .sweep import Variant

# ----- what ensure returns, and its errors (§5) -------------------------------------


@dataclass(frozen=True)
class Reached:
    """The newest claim's lineage holds the target, at ``node``: read anything there
    at ``AtNode(node)``, which is permanent. ``None`` only where a target of 0 meets
    the empty lineage, which holds nothing to read. ``Unreachable`` names its node
    otherwise (``closed_at``), so on the union ``.node`` is a type error, and a run
    that ended short of the target cannot be read as one that reached it."""

    node: int | None


@dataclass(frozen=True)
class Unreachable:
    """The head is closed, at ``closed_at``, below the target: nothing more can be
    committed on a completed run, so no drive could reach it."""

    closed_at: int


class RunFailedError(Exception):
    """A drive's claim ended with an outcome in ``Outcome.failures()`` (§5).
    ``result`` is its verdict at raise time, stamped with the run, the retry
    decision's input, handed over rather than re-read; ``count`` is the target's
    count at the head then."""

    def __init__(
        self, run_id: str, result: RunResult, *, target: Target, count: int
    ) -> None:
        outcomes = ", ".join(sorted(result.outcomes))
        detail = "" if result.error is None else f": {result.error}"
        super().__init__(
            f"run {run_id!r} failed ({outcomes}, by {result.tier}{detail}) at "
            f"{count} of {target}"
        )
        self.run_id = run_id
        self.result = result
        self.target = target
        self.count = count


class NoProgressError(Exception):
    """A drive's claim ended, not failing, having written no commit (§5): a
    re-drive would spin. Progress is measured per drive by ``Commits()``, not by the
    target's stream: a chunk may commit no element of a sparse stream, and a claim
    that resumes from an older checkpoint legitimately lowers every count."""

    def __init__(self, run_id: str, *, target: Target, count: int) -> None:
        super().__init__(
            f"run {run_id!r}: a drive's claim ended having written no commit, at "
            f"{count} of {target}"
        )
        self.run_id = run_id
        self.target = target
        self.count = count


class RecordlessExitError(Exception):
    """A drive's handle ended with no claim naming its launch, and no other claim
    held, a second time in a row at an unchanged head and ``Commits()`` count (§5;
    plan, open question 15): the re-drive reproduced it, so another can only do so
    again. Its launch exited before claiming, as an ``sbatch`` does at submit
    time, or a worker that dies before it constructs its ``Worker``.

    A sibling of ``RunFailedError``, not a subclass: no claim ended, so there is no
    verdict to carry. ``launch_id`` is the second drive's."""

    def __init__(
        self, run_id: str, *, launch_id: str, target: Target, count: int
    ) -> None:
        super().__init__(
            f"run {run_id!r}: launch {launch_id} ended with no claim naming it, "
            f"twice at an unchanged head, at {count} of {target}"
        )
        self.run_id = run_id
        self.launch_id = launch_id
        self.target = target
        self.count = count


# ----- the producer seam (§5) --------------------------------------------------------


class LiveHandle(Protocol):
    """What ``ensure`` drives: the drive's ``launch_id``, which its claim names, or
    ``None`` for a foreign handle, whose drives name no claim; ``is_alive``, which
    writes nothing; and ``wait``, which blocks until it is not and reaps it.
    A launcher's ``LaunchHandle`` is one, and so is ``foreign_episode``'s."""

    @property
    def launch_id(self) -> str | None: ...

    def is_alive(self) -> bool: ...

    def wait(self) -> object: ...


class Producer(Protocol):
    """A run ``ensure`` can read and extend (§5): ``channel`` and ``run_id`` locate
    its log, and ``extend(target)`` drives toward ``target``, returning a liveness
    handle, never ``None``."""

    run_id: str

    @property
    def channel(self) -> Channel: ...

    def extend(self, target: Target) -> LiveHandle: ...


class _ForeignEpisode:
    """The handle of a claim the producer did not launch (specs/store.md Recipe 2):
    ``is_alive()`` is a pure read, "the newest claim has not ended", by presence
    (``claims.endings``: an exit or a death report naming it, readable or not), so
    it probes nothing, writes nothing and never raises on what the log holds (§3,
    "The writers"). It names no launch, and ``wait()`` reaps nothing."""

    launch_id: None = None

    def __init__(self, ch: Channel) -> None:
        self._ch = ch

    def is_alive(self) -> bool:
        claim = latest_episode(self._ch)
        return claim is not None and not endings(self._ch, claim, self._ch.last_seq())

    def wait(self) -> None:
        return None


def foreign_episode(ch: Channel) -> _ForeignEpisode:
    """The one public copy of the producer gate's foreign half: compose a
    producer's ``extend`` as ``relaunch_if_needed(...) or foreign_episode(ch)``."""
    return _ForeignEpisode(ch)


D = TypeVar("D", bound=Drivable)


class _LaunchProducer(Generic[D]):
    """The default producer: a ``Launcher[D]`` relaunching ``variant``'s target,
    driven to each ``extend``'s target. A producer over a ``Command``, a ray actor or
    a service is the consumer's own object of the same shape (the seam): a
    ``Command`` is not ``Drivable``, since a subprocess has no defined way to
    receive a ``Target`` (plan, open question 18)."""

    def __init__(self, launcher: Launcher[D], variant: Variant[D]) -> None:
        self._launcher = launcher
        self._variant = variant
        self.run_id = variant.run_id

    @property
    def channel(self) -> Channel:
        """A fresh handle on the run: the producer births it, so the first access
        may precede any record (``create_channel``)."""
        return self._launcher.create_channel(self.run_id)

    def extend(self, target: Target) -> LiveHandle:
        """Launch ``variant.target.driven(target)`` unless the run's newest claim is
        held (``relaunch_if_needed``, which reports a dead one first), so the worker
        receives the drive's ``Target`` as ``until``, which it passes to
        ``steps(until)``, and exits preempted there, an extendable exit a larger
        target continues from. While a claim is held, hand back its
        ``foreign_episode``."""
        driven = self._variant.target.driven(target)
        handle = relaunch_if_needed(self._launcher, self.run_id, driven)
        return handle if handle is not None else foreign_episode(self.channel)


def launch_producer(launcher: Launcher[D], variant: Variant[D]) -> _LaunchProducer[D]:
    """A producer backed by ``launcher`` relaunching ``variant`` (§5, "The producer
    seam"), for a launcher whose target is ``Drivable``: a ``ThreadLauncher`` and a
    ``ThreadTarget``, whose ``driven(until)`` passes the target as the worker's
    ``until`` keyword."""
    return _LaunchProducer(launcher, variant)


# ----- history (§5) ------------------------------------------------------------------


def history(
    ch: Channel, stream: str, schedule: Condition, *, head: HeadStrategy
) -> list[Element]:
    """Replay ``schedule`` over the lineage's nodes, root first, and return the
    elements of ``stream`` committed at the nodes where it fires (§5).

    The wire schedule is parsed here, at the read's boundary
    (``parse_schedule(schedule, pure_demand=False)``): a malformed one raises
    ``MalformedSchedule`` to the caller. It is evaluated with
    ``Subscription.evaluate`` once per node, as live evaluation evaluates it, since
    the call is the firing: a ``count`` until counts nodes that fired, not calls.
    The subscription targets ``stream``, which always holds a value
    (``valued={stream}``): an eager stream always samples. Time atoms read each
    node's ``t``, from the epoch, the earliest ``started.t``. ``every``'s baseline
    is the coordinates at the last node it fired at, as on a live lineage. It stops
    at the node where the schedule expires.

    A node where it fires contributes every element of ``stream`` it commits, in its
    order, none when it commits none: the take-the-latest-per-step collapse is
    gone, since an element's index is unique along a lineage. One-shot: it walks
    the whole lineage, and the earliest claim's ``started`` (an unreadable one
    raises ``MalformedRecordError``)."""
    parsed = parse_schedule(schedule, pure_demand=False)
    walked = walk(ch, head.resolve(ch).node)
    if not walked.nodes:
        return []
    first = ch.read(topics=[Topic.LIFECYCLE_STARTED], limit=1)[0]
    epoch = verdict_parse(Started, first).t
    sub = Subscription(parsed, registered_at=epoch, target=stream)
    valued = frozenset({stream})
    elements: dict[int, list[int]] = {}
    for at, s in members(walked, stream):
        elements.setdefault(at.env.seq, []).append(s)
    out: list[Element] = []
    baseline: Coordinates | None = None
    for at, coords in zip(walked.nodes, coordinates(walked), strict=True):
        decision = sub.evaluate(
            at=coords, baseline=baseline, now=at.node.t, valued=valued
        )
        if decision.fire:
            baseline = coords
            for s in elements.get(at.env.seq, ()):
                v = verdict_parse(Value, walked.records[s])
                out.append(Element(node=at.env.seq, seq=s, value=v.value, t=v.t))
        if decision.expired:
            break
    return out


# ----- ensure (§5) -------------------------------------------------------------------


class _At(NamedTuple):
    """Where a recordless drive left the run: the head's node and its
    ``Commits()`` count, the fixed point's coordinates (plan, open question 15)."""

    node: int | None
    commits: int


class _Drive:
    """One ``extend``: its handle, the log's last seq before it (``floor``: its
    claim lands above it), and the newest claim naming its launch, read
    incrementally above what the last poll read, so a poll costs the records since.
    ``reaped`` latches the one ``wait()`` once the handle is seen dead."""

    def __init__(self, handle: LiveHandle, floor: int) -> None:
        self.handle = handle
        self.reaped = False
        self._read = floor
        self._claim: Envelope | None = None

    def claim(self, ch: Channel, w: int) -> Envelope | None:
        """The newest claim at or below ``w`` naming the drive's launch, or
        ``None``: always ``None`` for a foreign handle, which names none."""
        launch = self.handle.launch_id
        if launch is None:
            return None
        newer = launch_claim(ch, launch, after=self._read, w=w)
        if newer is not None:
            self._claim = newer
        self._read = max(self._read, w)
        return self._claim


def ensure(
    producer: Producer,
    target: Target,
    *,
    poll_interval: float = 0.01,
    sleep: Callable[[float], None] = time.sleep,
) -> Reached | Unreachable:
    """Drive ``producer`` until the newest claim's lineage holds ``target`` (§5).

    - **Read first:** ``count(x, head) >= n`` at ``LatestClaimHead`` returns
      ``Reached`` at the head; a closed head below ``n`` returns ``Unreachable`` at
      it, without producing. There is no ``head`` parameter: under ``AtNode``
      nothing could be produced, and the produce branch follows the newest claim.
    - **Otherwise it produces:** ``producer.extend(target)``, then polls, each poll
      calling ``report_if_dead`` (§3, "The writers") and then reading the newest
      claim's head and the target's count through held folds (``HeadFold``,
      ``PrefixLen``), at O(new records). It tests ``Reached`` and ``Unreachable``
      before the failure exits, so a drive that resumes from a node already holding
      the target is ``Reached``.
    - **A drive's claim** is the newest claim naming the handle's launch. Once one
      is on the log, a target it declares a source raises ``ValueError``: its count
      is 0 forever (plan, open question 1). While it is held, ``ensure`` waits,
      whatever the handle says. Once it has ended: ``RunFailedError`` when any of
      its outcomes is in ``Outcome.failures()``, ``NoProgressError`` when it wrote
      no commit, and otherwise another drive.
    - **A drive with no claim:** while its handle is alive, or another claim is
      held, as a claim race's winner's is, it waits. Otherwise the drive is
      recordless, and a foreign handle's (no launch) drives again; the second
      recordless drive in a row at an unchanged head and ``Commits()`` count raises
      ``RecordlessExitError``, and the first drives again.

    A handle seen dead is reaped once (``wait()``), so a launcher's reap writes its
    records. ``poll_interval`` and ``sleep`` are exempt defaults (plan, "Exempt
    defaults"): they pace the waits, and a test replaces ``sleep`` with a hook,
    but neither changes what ``ensure`` returns. No timeout: a held claim is waited
    on for as long as it is held."""
    ch = producer.channel
    heads, counts = HeadFold(), PrefixLen(target.x)
    commits = PrefixLen(Commits())
    head = heads.poll(ch)
    count = counts.poll(ch, head)
    if count >= target.n:
        return Reached(head.node)
    if head.settled:
        assert head.node is not None  # a settled head is a node
        return Unreachable(head.node)
    recordless: _At | None = None  # where the last drive, if recordless, left it
    while True:
        floor = ch.last_seq()  # before the launch: its claim lands above it
        drive = _Drive(producer.extend(target), floor)
        while True:
            alive = drive.handle.is_alive()
            if not alive and not drive.reaped:
                drive.handle.wait()
                drive.reaped = True
            report_if_dead(ch)
            head = heads.poll(ch)
            count = counts.poll(ch, head)
            if count >= target.n:
                return Reached(head.node)
            if head.settled:
                assert head.node is not None  # a settled head is a node
                return Unreachable(head.node)
            claim = drive.claim(ch, head.w)
            if claim is not None:
                _refuse_a_source(claim, target)
                result = claim_verdict(ch, claim, w=head.w)
                if result is None:
                    sleep(poll_interval)  # held: wait, whatever the handle says
                    continue
                if result.outcomes & Outcome.failures():
                    stamped = replace(result, run_id=producer.run_id)
                    raise RunFailedError(
                        producer.run_id, stamped, target=target, count=count
                    )
                if not _committed(ch, claim, head.w):
                    raise NoProgressError(producer.run_id, target=target, count=count)
                recordless = None
                break  # it ended having committed: drive again
            if alive or _held(ch):
                sleep(poll_interval)
                continue
            launch = drive.handle.launch_id
            if launch is None:
                recordless = None  # a foreign drive is no recordless one
                break
            here = _At(head.node, commits.poll(ch, head))
            if recordless == here:
                raise RecordlessExitError(
                    producer.run_id, launch_id=launch, target=target, count=count
                )
            recordless = here
            break


def _refuse_a_source(claim: Envelope, target: Target) -> None:
    """``ValueError`` when the drive's claim declares ``target.x`` a source, which
    counts 0 forever (§5; plan, open question 1). An unreadable claim raises
    ``MalformedRecordError``, as the head does."""
    if isinstance(target.x, Commits):
        return
    if target.x in verdict_parse(Started, claim).sources:
        raise ValueError(
            f"the target {target.x!r} is a source of the drive's claim "
            f"{claim.seq}, which counts 0 forever: drive to an eager stream or "
            "Commits()"
        )


def _committed(ch: Channel, claim: Envelope, w: int) -> bool:
    """Did ``claim`` write a commit at or below ``w``? Its newest commit, searched
    back from ``w`` within its window (``claims.backward``), so the read costs the
    distance back to it, and the whole window only for a claim that wrote none."""
    topics: list[str] = [Topic.LIFECYCLE_COMMIT]
    return any(
        named_claim(e) == claim.seq
        for e in backward(ch, after=claim.seq, hi=w, topics=topics)
    )


def _held(ch: Channel) -> bool:
    """Is the newest claim held: no exit and no death report names it, readable or
    not (``claims.endings``, which never raises)? ``foreign_episode``'s test."""
    return foreign_episode(ch).is_alive()
