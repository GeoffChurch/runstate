"""The Watcher of log format 0.4.0, staged (docs/specs/commits-and-lineage.md §5, "The
verdict", "Bounded reads", "``await_consumed``", "Sites that change"; §3, "A read never
writes"; observer-clock.md §5).

STAGED. Task 19 of docs/plans/2026-10-28-layer2-live.md moves this module over
``runstate/watcher.py``.

THE WATCHER is the stateful failure detector. ``peek_terminal`` is the verdict the
log holds; the Watcher adds the inferences a single read cannot make, each a
``presumed_dead`` naming the latest claim, with its tier:

- ``PROBED_DEAD``: a handle it holds probes dead, and its reap, which the Watcher
  runs (the one write a poll makes, §3), left no verdict;
- ``SESSION_ENDED``: the channel's session probe finds ended the session that wrote
  the held latest claim (``SessionProbe``, resolved once at registration). No grace:
  a channel takes its key before its first write, so the claim's row is stamped
  with a session that was held when it landed (channel-postgres.md);
- ``HEARTBEAT_STALE``: no witnessed liveness for longer than ``heartbeat_timeout``.

A tier can only vote dead or abstain, so none vetoes the staleness floor: a worker
whose session is held but which has stopped writing still goes stale.

WITNESSED LIVENESS (observer-clock.md §5; §3, "Liveness"). A poll witnesses the
records it reads that show the claim alive: a new ``lifecycle.started``, or a
``commit``, ``heartbeat`` or ``rewind`` naming the latest claim, each readable (a junk
record earns no credit). A witnessed arrival restarts the staleness clock at the
Watcher's own ``now``, which is skew-immune. At registration the clock is seeded
from the record's own ``t``, the newest of the latest claim's ``started``, commits,
heartbeats and rewinds, so a cold attach to a run that went quiet long ago reads its
true age at once; with no claim, from ``now``.

HELD STATE, BOUNDED POLLS (§5, "Bounded reads"). Each run holds a ``HeadFold``, a
``PrefixLen(Commits())``, a ``PendingStops`` and its witness cursor, so a poll reads
O(new records), and ``Running``'s head and count equal the pure reads after every
poll.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from typing import Protocol

from ..channel import Channel, Envelope
from ..channel.base import Session, SessionProbe
from .claims import backward, endings, named_claim, request_name, spent_by
from .launcher import LaunchHandle
from .observables import (
    Head,
    HeadFold,
    PendingStops,
    PrefixLen,
    RunResult,
    Tier,
    latest_episode,
    peek_terminal,
)
from .vocabulary.coordinate import Commits
from .vocabulary.payloads import (
    Bound,
    Commit,
    Heartbeat,
    Nak,
    Outcome,
    Rewind,
    Started,
    Stopped,
    Topic,
)
from .vocabulary.schedule import Condition, parse_schedule
from .walk import point, readable, verdict_parse

# ----- Running and RunStatus ---------------------------------------------------------


@dataclass(frozen=True)
class Running:
    """The non-terminal arm of ``RunStatus`` (§5): the newest claim's ``head`` and its
    ``count(Commits(), head)``, ``commits``, from folds held per run, and
    ``beacon_age``, the seconds since the Watcher last witnessed the run alive (or the
    seeded record's ``t``), the gradient toward ``heartbeat_stale``. A future-dated
    seed makes it negative, which reads alive: the safe direction (observer-clock §5).
    """

    head: Head
    commits: int
    beacon_age: float

    @property
    def done(self) -> bool:
        return False


RunStatus = Running | RunResult
"""A run's status: still running, or its terminal verdict."""


# ----- the session tier, resolved at registration -------------------------------------


class _SessionTier(Protocol):
    """Whether the session that wrote a held claim has ended: the session probe's
    evidence (§3), a ``SessionProbe`` channel's, or nothing (``_NoSessions``)."""

    def ended(self, claim: int) -> bool: ...


class _Sessions:
    """The tier of a ``SessionProbe`` channel. A claim's writer is fixed, so its
    stamp is read once per claim; ``session_ended`` is asked on each poll. A probe
    that cannot tell (``None``: another server, a standby) and a row with no stamp (a
    migrated one) abstain."""

    def __init__(self, ch: SessionProbe) -> None:
        self._ch = ch
        self._claim = 0
        self._session: Session | None = None

    def ended(self, claim: int) -> bool:
        if claim != self._claim:
            self._claim, self._session = claim, self._ch.writer_session(claim)
        return (
            self._session is not None and self._ch.session_ended(self._session) is True
        )


class _NoSessions:
    """The tier of a channel with no session probe: it always abstains."""

    def ended(self, claim: int) -> bool:
        return False


_NO_SESSIONS = _NoSessions()


# ----- witnessed liveness (observer-clock §5) -----------------------------------------

_WITNESS_TOPICS: list[str] = [
    Topic.LIFECYCLE_STARTED,
    Topic.LIFECYCLE_COMMIT,
    Topic.LIFECYCLE_HEARTBEAT,
    Topic.LIFECYCLE_REWIND,
]
_BODIES: dict[str, type[Commit] | type[Heartbeat] | type[Rewind]] = {
    Topic.LIFECYCLE_COMMIT: Commit,
    Topic.LIFECYCLE_HEARTBEAT: Heartbeat,
    Topic.LIFECYCLE_REWIND: Rewind,
}


class _Witness:
    """A run's witnessed liveness: the latest claim it has read, when it last
    witnessed the claim alive (``at``, the Watcher's clock, or the seed's ``t``), and a
    cursor, so each poll reads only the records since."""

    def __init__(self, ch: Channel, now: float) -> None:
        claim = latest_episode(ch)
        w = ch.last_seq()  # after the claim, so the claim is at or below it
        self._claim = None if claim is None else claim.seq
        self._cursor = w
        self.at = now if claim is None else _seed(ch, claim, w, now)

    def poll(self, ch: Channel, now: float) -> None:
        """Read the records since the cursor, and restart the clock at ``now`` if
        any witnesses the latest claim alive."""
        w = ch.last_seq()
        if w <= self._cursor:
            return
        got = ch.read(
            after=self._cursor, topics=_WITNESS_TOPICS, limit=w - self._cursor
        )
        for e in got:
            if e.seq > w:
                break
            if e.topic == Topic.LIFECYCLE_STARTED:
                self._claim = e.seq
                if readable(Started, e) is not None:
                    self.at = now
            elif named_claim(e) == self._claim:
                if readable(_BODIES[e.topic], e) is not None:
                    self.at = now
        self._cursor = w


def _seed(ch: Channel, claim: Envelope, w: int, now: float) -> float:
    """The ``t`` of the newest readable record naming ``claim`` among its commits,
    heartbeats and rewinds, searched back from ``w`` to the claim (``backward``), else
    of its ``started``; ``now`` when the claim is unreadable too, as a beacon the
    Watcher cannot date is treated as unwitnessed (observer-clock §5)."""
    topics = _WITNESS_TOPICS[1:]
    for e in backward(ch, after=claim.seq, hi=w, topics=topics):
        if named_claim(e) == claim.seq:
            body = readable(_BODIES[e.topic], e)
            if body is not None:
                return body.t
    started = readable(Started, claim)
    return now if started is None else started.t


# ----- the per-run state --------------------------------------------------------------


@dataclass
class _RunState:
    """What the Watcher holds per run: its channel and handle, the folds a poll
    reads (§5, "Bounded reads"), the session tier resolved at registration, and its
    witnessed liveness."""

    run_id: str
    channel: Channel
    handle: LaunchHandle | None
    heads: HeadFold
    commits: PrefixLen
    stops: PendingStops
    sessions: _SessionTier
    witness: _Witness


def _presumed(run_id: str, claim: int, tier: Tier) -> RunResult:
    """An inferred verdict: ``presumed_dead``, naming the claim it presumes of, with
    nothing a record would carry (no node, no reports, no error, no malformed)."""
    return RunResult(
        outcomes=frozenset({Outcome.PRESUMED_DEAD}),
        reports=(),
        node=None,
        claim_seq=claim,
        malformed=(),
        tier=tier,
        error=None,
        run_id=run_id,
    )


class Watcher:
    """The stateful failure detector (§5). ``poll(run_id)`` is one non-blocking
    status across every tier; ``wait`` loops it until terminal.

    - ``heartbeat_timeout`` is required (plan, open question 13): how long a run may
      go unwitnessed before ``heartbeat_stale``, a per-workload threshold, or
      ``None`` for no staleness tier. It changes a verdict, so it has no default.
    - ``now``, ``sleep`` and ``poll_interval`` are exempt defaults (plan, "Exempt
      defaults"): the clock and the pacing of the waits, which tests replace, and
      which change when a verdict is seen, never what it is."""

    def __init__(
        self,
        *,
        heartbeat_timeout: float | None,
        now: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        poll_interval: float = 0.05,
    ) -> None:
        self._hb_timeout = heartbeat_timeout
        self._now = now
        self._sleep = sleep
        self._poll_interval = poll_interval
        self._runs: dict[str, _RunState] = {}
        self._event_cursors: dict[str, int] = {}

    def add(self, handle: LaunchHandle) -> None:
        """Track a launched run by its handle, which enables the probe tier and the
        reap."""
        self._track(handle.run_id, handle.channel, handle)

    def observe(self, run_id: str, channel: Channel) -> None:
        """Track a run by its id and a channel, with no handle: the probe tier is
        off; the session and staleness tiers still apply."""
        self._track(run_id, channel, None)

    def _track(
        self, run_id: str, channel: Channel, handle: LaunchHandle | None
    ) -> None:
        """Resolve the session tier once, here at the boundary: the channel's probe
        if it is a ``SessionProbe``, else none; a poll never asks the type again."""
        sessions: _SessionTier = (
            _Sessions(channel) if isinstance(channel, SessionProbe) else _NO_SESSIONS
        )
        self._runs[run_id] = _RunState(
            run_id=run_id,
            channel=channel,
            handle=handle,
            heads=HeadFold(),
            commits=PrefixLen(Commits()),
            stops=PendingStops(),
            sessions=sessions,
            witness=_Witness(channel, self._now()),
        )

    def poll(self, run_id: str) -> RunStatus:
        """One non-blocking status (§5): the verdict the log holds, stamped with the
        run; else an inference about the held latest claim; else ``Running``.

        It writes nothing, but for a reap of a handle it holds (§3, "A read never
        writes"): once that handle probes dead it is reaped (``wait()``), which writes
        the launch's ``terminated`` and, where its process was the claimant, the
        ``died`` that ends the claim, so the verdict is the precise one. Only when the
        reap leaves the claim held is it ``probed_dead``. A run nothing has claimed is
        presumed nothing: an inference names its claim, and an unclaimed run's verdict
        is its launch's ``terminated``."""
        st = self._runs[run_id]
        st.witness.poll(st.channel, self._now())
        head = st.heads.poll(st.channel)
        commits = st.commits.poll(st.channel, head)
        result = peek_terminal(st.channel)
        if result is not None:
            return replace(result, run_id=run_id)
        claim = latest_episode(st.channel)
        if st.handle is not None and not st.handle.is_alive():
            st.handle.wait()  # the reap: the one write a poll makes
            result = peek_terminal(st.channel)
            if result is not None:
                return replace(result, run_id=run_id)
            claim = latest_episode(st.channel)
            if claim is not None:
                return _presumed(run_id, claim.seq, Tier.PROBED_DEAD)
        if claim is not None and st.sessions.ended(claim.seq):
            return _presumed(run_id, claim.seq, Tier.SESSION_ENDED)
        age = self._now() - st.witness.at
        if (
            claim is not None
            and self._hb_timeout is not None
            and age > self._hb_timeout
        ):
            return _presumed(run_id, claim.seq, Tier.HEARTBEAT_STALE)
        return Running(head=head, commits=commits, beacon_age=age)

    def wait(
        self,
        run_id: str,
        *,
        on_event: Callable[[str, Envelope], object] | None = None,
        timeout: float | None = None,
    ) -> RunResult:
        """Block until ``run_id`` is terminal, polling at ``poll_interval``. With
        ``on_event``, drain every tracked run's records to it as ``(run_id,
        Envelope)`` while waiting, from seq 0, with one final drain after the verdict
        so no record under it is lost. ``TimeoutError`` when ``timeout`` elapses
        first: the caller's patience is no verdict."""
        deadline = None if timeout is None else self._now() + timeout
        while True:
            if on_event is not None:
                for rid, e in self._drain():
                    on_event(rid, e)
            s = self.poll(run_id)
            if isinstance(s, RunResult):
                if on_event is not None:
                    for rid, e in self._drain():
                        on_event(rid, e)
                return s
            if deadline is not None and self._now() >= deadline:
                raise TimeoutError(f"run {run_id!r} not terminal within {timeout}s")
            self._sleep(self._poll_interval)

    def wait_all(
        self,
        *,
        on_event: Callable[[str, Envelope], object] | None = None,
        timeout: float | None = None,
    ) -> dict[str, RunStatus]:
        """Block until every tracked run is terminal: ``{run_id: RunStatus}``, total
        over the tracked set. With ``timeout`` it returns at the deadline with the
        still-running runs as their ``Running`` status. A run no tier can end (an
        ``observe``-d run on a channel with no session probe, with no
        ``heartbeat_timeout``) blocks an uncapped call forever."""
        deadline = None if timeout is None else self._now() + timeout
        results: dict[str, RunStatus] = {}
        pending = set(self._runs)
        while pending:
            if on_event is not None:
                for rid, e in self._drain():
                    on_event(rid, e)
            for run_id in list(pending):
                s = self.poll(run_id)
                if s.done:
                    results[run_id] = s
                    pending.discard(run_id)
            if not pending:
                if on_event is not None:
                    for rid, e in self._drain():
                        on_event(rid, e)
                break
            if deadline is not None and self._now() >= deadline:
                for run_id in pending:
                    results[run_id] = self.poll(run_id)
                break
            self._sleep(self._poll_interval)
        return results

    def broadcast(
        self, name: str | None, schedule: Condition, *, request_id: str | None = None
    ) -> str:
        """Send one ``control.subscribe`` to every tracked run under one shared
        ``request_id``, returned (a fresh one when ``None``): the cross-run barrier
        primitive. ``name`` is the source it samples, or ``None`` for pure demand (§5).

        The schedule is parsed first (``parse_schedule(schedule, pure_demand=name is
        None)``), so a malformed broadcast raises ``MalformedSchedule`` here and
        writes nothing, rather than a ``nak`` per run. An empty ``request_id`` is
        nameless, which every worker refuses, so it raises ``ValueError``."""
        parse_schedule(schedule, pure_demand=name is None)
        if request_id == "":
            raise ValueError("an empty request_id names nothing: pass None or an id")
        rid = request_id if request_id is not None else f"broadcast-{uuid.uuid4().hex}"
        for st in self._runs.values():
            st.channel.send(
                dict(schedule), topic=Topic.CONTROL_SUBSCRIBE, name=name, request_id=rid
            )
        return rid

    def pending_stops(self, run_id: str) -> list[Envelope]:
        """The run's pending stops, ``undischarged_stops`` held (``PendingStops``):
        each call reads only the records since the last."""
        st = self._runs[run_id]
        return st.stops.poll(st.channel)

    def iter_events(
        self, timeout: float | None = None
    ) -> Iterator[tuple[str, Envelope]]:
        """Yield ``(run_id, Envelope)`` across every tracked run, from seq 0 on each
        run's own cursor, independent of the polling. Endless without ``timeout``;
        with it, it returns once the deadline passes with nothing left to drain."""
        deadline = None if timeout is None else self._now() + timeout
        while True:
            batch = self._drain()
            yield from batch
            if deadline is not None and self._now() >= deadline:
                return
            if not batch:
                self._sleep(self._poll_interval)

    def _drain(self) -> list[tuple[str, Envelope]]:
        """Every record since the last drain, across every tracked run."""
        out: list[tuple[str, Envelope]] = []
        for run_id, st in list(self._runs.items()):
            cur = self._event_cursors.get(run_id, 0)
            for e in st.channel.read(after=cur):
                self._event_cursors[run_id] = e.seq
                out.append((run_id, e))
        return out


# ----- await_consumed (§5) ------------------------------------------------------------

_REQUESTS = (Topic.CONTROL_SUBSCRIBE, Topic.CONTROL_STOP)
_ANSWER_TOPICS: list[str] = [
    Topic.LIFECYCLE_NAK,
    Topic.LIFECYCLE_COMMIT,
    Topic.LIFECYCLE_HEARTBEAT,
    Topic.LIFECYCLE_STOPPED,
]


def await_consumed(
    ch: Channel,
    seq: int,
    *,
    timeout: float | None = None,
    poll_interval: float = 0.05,
    now: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
) -> Nak | RunResult | None:
    """Did the control request at ``seq`` land (§5)? The request is the record at
    ``seq``, and its ``request_id`` is the name its answers bear.

    - a ``Nak``, when a refusal names it;
    - ``None``, accepted, when a watermark of a claim reaches it (a commit's or a
      heartbeat's ``consumed_seq``), or an answer names it: a node's ``fired`` or
      ``expired``, on any lineage, or an exit's ``honored``;
    - the terminal ``RunResult``, when the latest claim ended by its own exit above
      the request and no watermark of it reaches the request: the exit was
      CAS-appended at a tail its writer had read, so the claim saw the request and
      ended without taking it; and when the head is closed, since nothing will ever
      take it;
    - otherwise it waits for the next claim: so does a request posted after the
      latest claim ended, and one above the last watermark of a claim death reports
      ended (a report shows that its writer read the request, not that the claim
      did).

    Each answer follows the request, so only the records above ``seq`` are read, in a
    cursor, at O(new records) per poll.

    It refuses up front, with ``ValueError``, what no worker could answer, since
    ``None`` would be a false "accepted": no record at ``seq``; a record that is not
    a ``control.subscribe`` or ``control.stop`` (a ``control.withdraw`` needs no
    worker); a request with a missing or empty id, which is nameless; an id an
    exit's ``honored``, a withdrawal or a refusal spent before ``seq``, since request
    ids are one namespace and a spent id is dead on arrival; and a subscription whose
    id is a void lease, bound to a claim that has ended. A lease voided while it
    waits, by a later claim's watermark passing a resend its claim dropped, raises
    too. ``TimeoutError`` when ``timeout`` elapses; ``MalformedRecordError`` for a
    refusal whose body does not parse. ``poll_interval``, ``now`` and ``sleep`` are
    exempt defaults: they pace the waits, and tests replace them."""
    deadline = None if timeout is None else now() + timeout
    request = point(ch, seq, w=ch.last_seq())
    if request is None:
        raise ValueError(f"no record at seq {seq}: there is no request to await")
    if request.topic not in _REQUESTS:
        raise ValueError(
            f"the record at seq {seq} is a {request.topic}, not a control request "
            "a worker answers (a subscribe or a stop)"
        )
    rid = request_name(request)
    if rid is None:
        raise ValueError(
            f"the {request.topic} at seq {seq} names no request (a missing or empty "
            "request_id): the worker refuses it nameless, and nothing can answer it"
        )
    _refuse_a_spent_id(ch, request, rid)
    leases = _Leases(ch, request, rid)
    leases.refuse_void(taken_by=None)
    heads = HeadFold()
    exits: set[int] = set()  # the claims with a readable exit above the request
    cursor = seq
    while True:
        w = ch.last_seq()
        answer: Nak | None = None
        accepted: int | None = None  # the claim whose record took the request
        got = (
            []
            if w <= cursor
            else ch.read(after=cursor, topics=_ANSWER_TOPICS, limit=w - cursor)
        )
        for e in got:
            if e.seq > w:
                break
            if e.topic == Topic.LIFECYCLE_NAK:
                if request_name(e) == rid:
                    answer = verdict_parse(Nak, e)
                    break
                continue
            took = _takes(e, rid, seq)
            exit_ = readable(Stopped, e) if e.topic == Topic.LIFECYCLE_STOPPED else None
            if exit_ is not None:
                exits.add(exit_.claim_seq)
            if took and accepted is None:
                accepted = named_claim(e)
        cursor = max(cursor, w)
        if answer is not None:
            return answer
        if accepted is not None:
            leases.refuse_void(taken_by=accepted)
            return None
        head = heads.poll(ch)
        claim = latest_episode(ch)
        if head.settled or (claim is not None and claim.seq in exits):
            result = peek_terminal(ch)
            assert result is not None  # an exit names the newest claim
            return result
        if deadline is not None and now() >= deadline:
            raise TimeoutError(
                f"the request at seq {seq} not consumed within {timeout}s"
            )
        sleep(poll_interval)


def _takes(e: Envelope, rid: str, seq: int) -> bool:
    """Does ``e``, a readable commit, heartbeat or exit above the request, show the
    request taken: a watermark reaching ``seq``, or a ``fired``, ``expired`` or
    ``honored`` naming ``rid``? An exit's seq is no watermark here: it is what the
    verdict case reads."""
    if e.topic == Topic.LIFECYCLE_HEARTBEAT:
        beat = readable(Heartbeat, e)
        return beat is not None and beat.consumed_seq >= seq
    if e.topic == Topic.LIFECYCLE_COMMIT:
        node = readable(Commit, e)
        return node is not None and (
            node.consumed_seq >= seq or rid in node.fired or rid in node.expired
        )
    exit_ = readable(Stopped, e)
    return exit_ is not None and (
        rid in exit_.honored or rid in exit_.fired or rid in exit_.expired
    )


def _refuse_a_spent_id(ch: Channel, request: Envelope, rid: str) -> None:
    """``ValueError`` if a record below the request spends ``rid`` by name
    (``claims.spent_by``: an exit's ``honored``, a withdrawal, a refusal): request
    ids are one namespace, and a spent id is dead on arrival on every control topic,
    so the worker skips the request unanswered. The log below the request is
    complete, so this is decided once. The ``request_ids`` filter also admits the
    exits, which name no request."""
    topics: list[str] = [
        Topic.LIFECYCLE_STOPPED,
        Topic.CONTROL_WITHDRAW,
        Topic.LIFECYCLE_NAK,
    ]
    for e in ch.read(topics=topics, request_ids=[rid], limit=request.seq):
        if e.seq >= request.seq:
            break
        if rid in spent_by(e):
            raise ValueError(
                f"request_id {rid!r} at seq {request.seq} is dead on arrival: the "
                f"{e.topic} at seq {e.seq} already spent it, and a spent id is "
                "never answered again: send a fresh request_id"
            )


class _Leases:
    """The claims a ``lifecycle.bound`` binds ``rid`` to, for a subscribe: a lease is
    void once its claim has ended (§2, "Per claim"), so a resend of it is dropped by
    every later claim, unanswered."""

    def __init__(self, ch: Channel, request: Envelope, rid: str) -> None:
        self._ch = ch
        self._request = request
        self._rid = rid

    def refuse_void(self, *, taken_by: int | None) -> None:
        """``ValueError`` if ``rid`` is bound to a claim that has ended, other than
        ``taken_by``, the claim whose record took the request: a claim the lease is
        bound to accepts its resend while it holds it."""
        if self._request.topic != Topic.CONTROL_SUBSCRIBE:
            return
        ch, rid = self._ch, self._rid
        w = ch.last_seq()
        topics: list[str] = [Topic.LIFECYCLE_BOUND]
        for e in ch.read(topics=topics, request_ids=[rid], limit=w):
            bound = readable(Bound, e)
            if e.seq > w or e.request_id != rid or bound is None:
                continue
            if bound.claim_seq == taken_by:
                continue
            claim = point(ch, bound.claim_seq, w=w)
            if claim is not None and endings(ch, claim, w):
                raise ValueError(
                    f"the lease {rid!r} at seq {self._request.seq} is bound to claim "
                    f"{bound.claim_seq}, which has ended: a lease is one claim's, so "
                    "no claim will serve it again: resubscribe under a fresh request_id"
                )
