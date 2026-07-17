"""The Watcher: the stateful, inference-based failure detector (§8-9).

``peek_terminal`` is the *record-based* verdict — it answers only when a terminal
envelope already exists (a clean ``lifecycle.stopped`` or a reaped
``launcher.terminated``). The Watcher adds the two *inference-based* tiers that
need state a single log read can't have:

  3. **probe the handle** — if a tracked handle resolves dead and the log has no
     terminal record, the worker died without reporting → ``presumed_dead``;
  4. **heartbeat staleness** — if the newest ``lifecycle.heartbeat`` is older than
     ``heartbeat_timeout`` (wall-clock since it *arrived*), the worker is hung or
     crashed → ``presumed_dead``. Off unless a timeout is given (the dead-vs-busy
     threshold is per-workload, §8). The clock seeds at registration (so a
     never-beaconing startup death is caught), resets only when a poll observes a
     NEW heartbeat seq, and the boundary is strict: age == timeout is still alive.

``poll(run_id)`` is the single non-blocking verdict across all tiers; ``wait``
loops it until terminal. ``now``/``sleep`` are injectable for deterministic tests.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from typing import Optional, Protocol, Union

from .channel import Channel, EpisodeProbe, Envelope
from .launcher import LaunchHandle
from .observables import (
    Outcome,
    RunResult,
    _verdict_record,
    verdict_parse,
    peek_terminal,
)
from .vocabulary.payloads import Heartbeat, Nak, Topic
from .vocabulary.schedule import Condition


@dataclass(frozen=True)
class Running:
    """The non-terminal arm of RunStatus: a run that's still in flight, with the
    Watcher's live snapshot — ``step`` from the latest heartbeat, and
    ``beacon_age`` (seconds since that heartbeat *arrived*, the gradient toward
    presumed-dead) which is watcher-computed and not on the raw event stream."""

    step: Optional[int] = None
    beacon_age: Optional[float] = None

    @property
    def done(self) -> bool:
        return False


# A run's current status is either still-running or a terminal verdict.
RunStatus = Union[Running, RunResult]


# --- per-run liveness probe (resolved once at registration) ---
# The episode-lock signal is a backend CAPABILITY (channel/base.py), so the Watcher
# resolves it ONCE when a run is registered -- to a real probe for an EpisodeProbe
# channel, or a null probe otherwise -- and poll() just folds the verdict. This keeps
# the lock's state (which episode; when first seen) and its birth-grace logic OUT of
# the Watcher's general _RunState/poll: capability detection lives at the boundary,
# the hot path stays linear, and the substrate Channel keeps its four pure ops.


class _LockChannel(Protocol):
    """What the episode-lock probe needs of a channel: read the log AND probe the
    lock. A real ``EpisodeProbe`` backend (PostgresChannel) satisfies both."""

    def latest(self, topic: str, name: str | None = None) -> Optional[Envelope]: ...
    def episode_alive(self, started_seq: int) -> bool: ...


class _LivenessProbe(Protocol):
    """A per-run liveness contributor the Watcher folds into its cascade: a definitive
    death verdict, or None to abstain (fall through to the staleness floor)."""

    def verdict(self, now: float) -> Optional[RunResult]: ...


class _EpisodeLockProbe:
    """Liveness via the channel's episode lock. Owns the episode-tracking + birth-grace
    state so the Watcher's _RunState/poll don't carry it. The lock is a Watcher-consumed
    SIGNAL, never a claim arbiter; it can only vote DEAD (past the grace) or abstain, so
    it never vetoes the staleness floor."""

    def __init__(self, run_id: str, channel: _LockChannel, grace: float) -> None:
        self._run_id = run_id
        self._channel = channel
        self._grace = grace
        self._episode_seq: Optional[int] = None
        self._seen_at = 0.0

    def verdict(self, now: float) -> Optional[RunResult]:
        started = self._channel.latest(Topic.LIFECYCLE_STARTED)
        if started is None:
            return None  #                         no episode yet -> abstain
        if started.seq != self._episode_seq:  #    a new (or first-seen) episode
            self._episode_seq = started.seq
            self._seen_at = now  #                 the birth grace runs from first-sight
        if self._channel.episode_alive(started.seq):
            return None  #                         alive -> abstain (fall to staleness)
        if now - self._seen_at > self._grace:
            # lock released past the birth grace -> a definitive cross-host death,
            # where os.kill abstains on a foreign host.
            return RunResult(
                outcome=Outcome.PRESUMED_DEAD,
                reason="episode_lock_released",
                run_id=self._run_id,
            )
        return None  #                             within the grace (CAS->hold window)


class _NoLivenessProbe:
    """Null object: a channel without the lock capability contributes no liveness
    signal, so the Watcher falls through to its other tiers."""

    def verdict(self, now: float) -> Optional[RunResult]:
        return None


_NO_LIVENESS = _NoLivenessProbe()


@dataclass(frozen=True)
class _HeartbeatSeed:
    """The three staleness fields seeded from the beacon already on the log at
    registration (observer-clock §5). Seeded together so poll()'s first
    _note_heartbeat does not mistake the pre-existing beacon for a fresh arrival."""

    at: float
    seq: int
    step: Optional[int]


def _is_beacon_step(step: object) -> bool:
    """A conforming heartbeat step: None (stepless) or a real int (bool excluded).
    The one definition of the step-junk rule the beacon seed (``_heartbeat_seed``)
    and the witnessed-beacon upgrade (``_note_heartbeat``) both apply -- two
    inlined spellings of one rule is how they drift apart."""
    return step is None or (isinstance(step, int) and not isinstance(step, bool))


def _heartbeat_seed(hb: Optional[Envelope], now: float) -> _HeartbeatSeed:
    """Seed from the latest heartbeat, else from ``now``. A missing `t` (an
    un-migrated old beacon) or a junk-typed `t`/`step` degrades to ``now`` with no
    step — the same measurement-plane tolerance _note_heartbeat applies: a beacon we
    cannot date is treated as unwitnessed (fresh now; let the timeout catch a real
    death), never as a false verdict."""
    if hb is None:
        return _HeartbeatSeed(at=now, seq=0, step=None)
    try:
        beat = Heartbeat(**hb.body)
    except (
        TypeError
    ):  #                   missing/extra keys (e.g. pre-migration, no `t`)
        return _HeartbeatSeed(at=now, seq=hb.seq, step=None)
    at = (
        beat.t
        if isinstance(beat.t, (int, float)) and not isinstance(beat.t, bool)
        else now
    )
    step = beat.step if _is_beacon_step(beat.step) else None
    return _HeartbeatSeed(at=float(at), seq=hb.seq, step=step)


@dataclass
class _RunState:
    run_id: str
    channel: Channel
    handle: Optional[LaunchHandle]
    last_heartbeat_at: float
    liveness: _LivenessProbe
    last_hb_seq: int = field(default=0)
    last_step: Optional[int] = field(default=None)


class Watcher:
    def __init__(
        self,
        *,
        now: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        poll_interval: float = 0.05,
        heartbeat_timeout: Optional[float] = None,
        episode_grace: float = 5.0,
    ):
        self._now = now
        self._sleep = sleep
        self._poll_interval = poll_interval
        self._hb_timeout = heartbeat_timeout
        # The birth grace for the episode-lock probe: a not-held lock within this
        # window of first-sight is the CAS->hold_episode gap (inconclusive), not a
        # death. Only consulted for a backend whose channel is an EpisodeProbe.
        self._episode_grace = episode_grace
        self._runs: dict[str, _RunState] = {}
        self._event_cursors: dict[str, int] = {}

    def add(self, handle: LaunchHandle) -> None:
        """Track a launched run by its handle (enables the probe tier)."""
        self._track(handle.run_id, handle.channel, handle)

    def observe(self, run_id: str, channel: Channel) -> None:
        """Track a run by run_id + channel, handle-free (late-attach or
        observe-only). The probe tier is unavailable; staleness still applies."""
        self._track(run_id, channel, None)

    def _track(
        self, run_id: str, channel: Channel, handle: Optional[LaunchHandle]
    ) -> None:
        # Resolve the liveness capability ONCE, here at the boundary: a real probe for a
        # lock-capable channel, else the null probe. poll() never re-checks the type.
        liveness: _LivenessProbe = (
            _EpisodeLockProbe(run_id, channel, self._episode_grace)
            if isinstance(channel, EpisodeProbe)
            else _NO_LIVENESS
        )
        # Seed the staleness clock from the beacon ALREADY on the log, so a cold
        # attach to a run that went quiet long ago reads its true age at once
        # (observer-clock §5). Three fields must be seeded together, not one: poll()
        # runs _note_heartbeat FIRST, which upgrades last_heartbeat_at -> now() when
        # `hb.seq > last_hb_seq` -- so seeding last_heartbeat_at alone (leaving
        # last_hb_seq=0) lets the pre-existing beacon be mistaken for a fresh arrival
        # and clobbered, a silent no-op. Seeding last_hb_seq too makes only a GENUINELY
        # newer beacon upgrade to now() (witnessed, skew-immune). No heartbeat on the
        # log -> seed now() (the never-beaconed-startup-death catch tier 4 exists for).
        hb = channel.latest(Topic.LIFECYCLE_HEARTBEAT)
        seed = _heartbeat_seed(hb, self._now())
        self._runs[run_id] = _RunState(
            run_id=run_id,
            channel=channel,
            handle=handle,
            last_heartbeat_at=seed.at,
            last_hb_seq=seed.seq,
            last_step=seed.step,
            liveness=liveness,
        )

    def poll(self, run_id: str) -> RunStatus:
        """One non-blocking status for ``run_id`` across all tiers: a terminal
        RunResult, or the Running snapshot if it still looks alive. (Never None —
        the running case is the Running arm, carrying the gradient poll already
        computed for the staleness check, rather than discarding it.)"""
        st = self._runs[run_id]
        self._note_heartbeat(st)

        # tiers 1-2: a terminal record always wins.
        r = peek_terminal(st.channel)
        if r is not None:
            return replace(r, run_id=run_id)

        # tier 3: probe the handle. If it's dead, reap it first (wait() returns
        # at once for an already-exited handle and records launcher.terminated),
        # so we report the precise manner of death rather than a bare
        # presumed_dead that discards the exit code. presumed_dead remains only
        # for a handle whose death leaves no record at all.
        if st.handle is not None and not st.handle.is_alive():
            st.handle.wait()
            r = peek_terminal(st.channel)
            if r is not None:
                return replace(r, run_id=run_id)
            return RunResult(
                outcome=Outcome.PRESUMED_DEAD, reason="probed_dead", run_id=run_id
            )

        # tier 3b: the episode-liveness signal (resolved per run at registration; a null
        # probe for backends without the capability). poll() folds an OPAQUE verdict --
        # the lock's episode-tracking and birth-grace state live in the probe, not here.
        # It can only vote DEAD (a definitive cross-host death, where os.kill abstains)
        # or abstain (None -> fall through), so a held lock never vetoes the staleness
        # floor below. A SIBLING to the handle probe, independent of a tracked handle.
        verdict = st.liveness.verdict(self._now())
        if verdict is not None:
            return verdict

        # tier 4: heartbeat staleness. The clock runs from when we began watching
        # (last_heartbeat_at seeds at registration, then tracks the latest beacon),
        # so a worker that never beacons — crashed or hung during *startup* — is
        # caught too, not just a mid-run hang. Legit-slow startup is the caller's
        # policy: pick heartbeat_timeout >= worst-case startup, or start watching
        # only after the first beacon. NB this fires even for a handle that probed
        # *alive* (tier 3 didn't return): that's the hang case (beaconing stopped
        # though the process lives), which §8 wants caught — don't "fix" it to
        # veto on alive.
        beacon_age = self._now() - st.last_heartbeat_at
        if self._hb_timeout is not None and beacon_age > self._hb_timeout:
            return RunResult(
                outcome=Outcome.PRESUMED_DEAD, reason="heartbeat_stale", run_id=run_id
            )

        return Running(step=st.last_step, beacon_age=beacon_age)

    def wait(
        self,
        run_id: str,
        *,
        on_event: Callable[[str, Envelope], object] | None = None,
        timeout: Optional[float] = None,
    ) -> RunResult:
        """Block until ``run_id`` is terminal (any tier), polling at
        ``poll_interval``. If ``on_event`` is given, drain envelopes across all
        tracked runs to it as ``(run_id, Envelope)`` while waiting (the same
        stream ``iter_events`` exposes — cursor from 0, so the first drain
        replays logged history), with one final drain after the terminal
        verdict so no event under the verdict is lost. Raises TimeoutError if
        ``timeout`` elapses first — the caller's patience running out is not a
        death verdict (the run may be a healthy slow one). An uninterpretable
        record on the verdict plane raises ``MalformedRecordError``
        (observables) — propagated, not swallowed."""
        deadline = None if timeout is None else self._now() + timeout
        while True:
            if on_event is not None:
                for rid, e in self._drain():
                    on_event(rid, e)
            s = self.poll(run_id)
            if isinstance(s, RunResult):
                if on_event is not None:  # deliver any envelopes up to the verdict
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
        timeout: Optional[float] = None,
    ) -> dict[str, RunStatus]:
        """Block until every tracked run is terminal, returning ``{run_id:
        RunStatus}`` total over the tracked set. Uncapped this is a pure
        synchronization (a slow-but-healthy run delays it, by design). With
        ``timeout`` it returns at the deadline with the still-running runs as
        their Running status — so pending is explicit (which runs, and how stale),
        not absence.

        Caveat: a run that can reach *no* terminal tier — ``observe``-d (no
        handle) with no ``heartbeat_timeout`` — can never resolve, so an uncapped
        ``wait_all`` over such a run blocks forever. Give those a timeout, a
        handle, or a heartbeat_timeout."""
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
                if on_event is not None:  # deliver envelopes up to the last verdict
                    for rid, e in self._drain():
                        on_event(rid, e)
                break
            if deadline is not None and self._now() >= deadline:
                for run_id in pending:  # report the laggards as Running
                    results[run_id] = self.poll(run_id)
                break
            self._sleep(self._poll_interval)
        return results

    def broadcast(
        self, name: str, schedule: Condition, *, request_id: str | None = None
    ) -> str:
        """Fan one ``control.subscribe`` across every tracked run under a single
        shared ``request_id`` (returned). The run_id disambiguates the responses;
        this is the cross-run barrier primitive — no Experiment class. The caller
        then collects responses via iter_events / wait_all filtered on the id."""
        rid = request_id if request_id is not None else f"broadcast-{uuid.uuid4().hex}"
        for st in self._runs.values():
            st.channel.send(
                dict(schedule), topic=Topic.CONTROL_SUBSCRIBE, name=name, request_id=rid
            )
        return rid

    def iter_events(
        self, timeout: Optional[float] = None
    ) -> Iterator[tuple[str, Envelope]]:
        """Yield ``(run_id, Envelope)`` for envelopes across all tracked runs,
        advancing a per-run cursor independent of the verdict polling. The
        cursor starts at 0: the first drain replays each run's ENTIRE logged
        history — not "new since tracking" — then later drains yield only new
        arrivals. Without ``timeout`` this is an endless stream (the caller
        breaks out, e.g. on a terminal envelope); with ``timeout`` it returns
        once the wall-clock deadline passes with nothing new left to drain.
        """
        deadline = None if timeout is None else self._now() + timeout
        while True:
            batch = self._drain()
            for item in batch:
                yield item
            if deadline is not None and self._now() >= deadline:
                return
            if not batch:
                self._sleep(self._poll_interval)

    def _drain(self) -> list[tuple[str, Envelope]]:
        """Pull all envelopes new since the last drain across every tracked run,
        advancing the per-run event cursor. Shared by iter_events and wait's
        on_event streaming."""
        out: list[tuple[str, Envelope]] = []
        for run_id, st in list(self._runs.items()):
            cur = self._event_cursors.get(run_id, 0)
            for e in st.channel.read(after=cur):
                self._event_cursors[run_id] = e.seq
                out.append((run_id, e))
        return out

    def _note_heartbeat(self, st: _RunState) -> None:
        hb = st.channel.latest(Topic.LIFECYCLE_HEARTBEAT)
        if hb is not None and hb.seq > st.last_hb_seq:
            st.last_hb_seq = hb.seq
            try:
                step = Heartbeat(**hb.body).step
            except TypeError:
                # measurement-plane tolerance: a junk beacon isn't a
                # measurement — it earns no liveness credit and is skipped
                # (the next conforming beacon supersedes it).
                return
            if not _is_beacon_step(step):
                return  # wrong-typed step: the same junk-beacon rule
            st.last_heartbeat_at = self._now()
            st.last_step = step


def await_consumed(
    channel: Channel,
    seq: int,
    *,
    request_id: str | None = None,
    timeout: float | None = None,
    poll_interval: float = 0.05,
    now: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
) -> "Nak | RunResult | None":
    """Block until the control request at ``seq`` is ANSWERED or drained.
    Answer-first (specs/service-worker.md): a ``lifecycle.nak`` bearing
    ``request_id`` that *follows* ``seq`` resolves immediately (returns the
    ``Nak``) — the watermark (the latest heartbeat's ``consumed_seq >= seq``,
    §6) is only the no-answer-yet probe for acceptance (returns ``None``). If
    a terminal record *follows* the request with no later episode, no worker
    will ever drain it: returns the terminal ``RunResult`` (refused-by-death)
    instead of blocking — while a request sent *after* a death correctly waits
    for the next episode. Raises ``TimeoutError`` if ``timeout`` elapses —
    not-yet-drained is not a refusal — and ``MalformedRecordError`` on a nak
    body it cannot parse (the answer is on the verdict plane). With
    ``request_id=None``, nak detection is skipped. So the full codomain is the
    answer space: ``Nak`` (refused) | ``RunResult`` (the run died under the
    request) | ``None`` (accepted)."""
    deadline = None if timeout is None else now() + timeout

    def _answer() -> "Nak | None":
        # Positional: only a nak FOLLOWING the request answers it.
        if request_id is None:
            return None
        naks = [
            e
            for e in channel.read(after=seq, topics=[Topic.LIFECYCLE_NAK])
            if e.request_id == request_id
        ]
        return verdict_parse(Nak, naks[-1]) if naks else None

    while True:
        # Answer-first (specs/service-worker.md): a nak IS the answer; the
        # watermark is only the no-answer-yet probe. A request answered inside
        # a winning retire() drain gets its nak but never a later heartbeat —
        # watermark-first would deadlock its waiter.
        nak = _answer()
        if nak is not None:
            return nak
        hb = channel.latest(Topic.LIFECYCLE_HEARTBEAT)
        if hb is not None:
            try:
                consumed = Heartbeat(**hb.body).consumed_seq >= seq
            except TypeError:
                consumed = False  # a junk beacon is no watermark evidence
            if consumed:
                # Re-check once: the nak and its heartbeat may both have landed
                # between this iteration's two reads.
                return _answer()
        # Refused-by-death: the terminal record FOLLOWING the request means no
        # worker will ever drain it — return the terminal verdict. A terminal
        # that PRECEDES the request leaves it waiting for the next episode. The
        # record is the one peek_terminal speaks for (_verdict_record), not the
        # latest terminal-topic envelope: an unrelated launch's death (a
        # claim-race loser's) is not this request's refusal.
        record = _verdict_record(channel)
        if record is not None and record.seq > seq:
            term = peek_terminal(channel)
            if term is not None:
                return term
        if deadline is not None and now() >= deadline:
            raise TimeoutError(f"control seq {seq} not consumed within {timeout}s")
        sleep(poll_interval)
