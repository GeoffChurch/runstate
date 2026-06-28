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
     threshold is per-workload, §8).

``poll(run_id)`` is the single non-blocking verdict across all tiers; ``wait``
loops it until terminal. ``now``/``sleep`` are injectable for deterministic tests.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from typing import Optional, Union

from .channel import Channel, Envelope
from .launcher import LaunchHandle
from .observables import Outcome, RunResult, peek_terminal
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


@dataclass
class _RunState:
    run_id: str
    channel: Channel
    handle: Optional[LaunchHandle]
    last_heartbeat_at: float
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
    ):
        self._now = now
        self._sleep = sleep
        self._poll_interval = poll_interval
        self._hb_timeout = heartbeat_timeout
        self._runs: dict[str, _RunState] = {}
        self._event_cursors: dict[str, int] = {}

    def add(self, handle: LaunchHandle) -> None:
        """Track a launched run by its handle (enables the probe tier)."""
        self._track(handle.run_id, handle.channel, handle)

    def observe(self, run_id: str, channel: Channel) -> None:
        """Track a run by run_id + channel, handle-free (late-attach or
        observe-only). The probe tier is unavailable; staleness still applies."""
        self._track(run_id, channel, None)

    def _track(self, run_id: str, channel: Channel, handle: Optional[LaunchHandle]) -> None:
        self._runs[run_id] = _RunState(
            run_id=run_id,
            channel=channel,
            handle=handle,
            last_heartbeat_at=self._now(),
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
            return RunResult(outcome=Outcome.PRESUMED_DEAD, reason="probed_dead", run_id=run_id)

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
        self, run_id: str, *,
        on_event: Callable[[str, Envelope], object] | None = None,
        timeout: Optional[float] = None,
    ) -> RunResult:
        """Block until ``run_id`` is terminal (any tier), polling at
        ``poll_interval``. If ``on_event`` is given, drain new envelopes across
        all tracked runs to it as ``(run_id, Envelope)`` while waiting (the same
        stream ``iter_events`` exposes). Raises TimeoutError if ``timeout``
        elapses first — the caller's patience running out is not a death verdict
        (the run may be a healthy slow one)."""
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

    def wait_all(self, *, on_event: Callable[[str, Envelope], object] | None = None,
                 timeout: Optional[float] = None) -> dict[str, RunStatus]:
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

    def broadcast(self, name: str, schedule: Condition, *, request_id: str | None = None) -> str:
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

    def iter_events(self, timeout: Optional[float] = None) -> Iterator[tuple[str, Envelope]]:
        """Yield ``(run_id, Envelope)`` for new envelopes across all tracked runs
        as they arrive, advancing a per-run cursor independent of the verdict
        polling. Without ``timeout`` this is an endless stream (the caller breaks
        out, e.g. on a terminal envelope); with ``timeout`` it returns once the
        wall-clock deadline passes with nothing new left to drain.
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
            st.last_heartbeat_at = self._now()
            st.last_step = Heartbeat(**hb.body).step


def await_consumed(channel: Channel, seq: int, *, request_id: str | None = None,
                   timeout: float | None = None, poll_interval: float = 0.05,
                   now: Callable[[], float] = time.time,
                   sleep: Callable[[float], None] = time.sleep) -> "Nak | RunResult | None":
    """Block until the control request at ``seq`` is ANSWERED or drained.
    Answer-first (specs/service-worker.md): a ``lifecycle.nak`` bearing
    ``request_id`` that *follows* ``seq`` resolves immediately (returns the
    ``Nak``) — the watermark (the latest heartbeat's ``consumed_seq >= seq``,
    §6) is only the no-answer-yet probe for acceptance (returns ``None``). If
    a terminal record *follows* the request with no later episode, no worker
    will ever drain it: returns the terminal ``RunResult`` (refused-by-death)
    instead of blocking — while a request sent *after* a death correctly waits
    for the next episode. Raises ``TimeoutError`` if ``timeout`` elapses —
    not-yet-drained is not a refusal. With ``request_id=None``, nak detection
    is skipped. So the full codomain is the answer space: ``Nak`` (refused) |
    ``RunResult`` (the run died under the request) | ``None`` (accepted)."""
    deadline = None if timeout is None else now() + timeout

    def _answer() -> "Nak | None":
        # Positional: only a nak FOLLOWING the request answers it.
        if request_id is None:
            return None
        naks = [e for e in channel.read(after=seq, topics=[Topic.LIFECYCLE_NAK])
                if e.request_id == request_id]
        return Nak(**naks[-1].body) if naks else None

    while True:
        # Answer-first (specs/service-worker.md): a nak IS the answer; the
        # watermark is only the no-answer-yet probe. A request answered inside
        # a winning retire() drain gets its nak but never a later heartbeat —
        # watermark-first would deadlock its waiter.
        nak = _answer()
        if nak is not None:
            return nak
        hb = channel.latest(Topic.LIFECYCLE_HEARTBEAT)
        if hb is not None and Heartbeat(**hb.body).consumed_seq >= seq:
            # Re-check once: the nak and its heartbeat may both have landed
            # between this iteration's two reads.
            return _answer()
        # Refused-by-death: a terminal record FOLLOWING the request, with no
        # later episode (peek_terminal is episode-aware), means no worker will
        # ever drain it — return the terminal verdict. A terminal that
        # PRECEDES the request leaves it waiting for the next episode.
        term = peek_terminal(channel)
        if term is not None:
            last_terminal = max(
                (e.seq for e in (channel.latest(Topic.LIFECYCLE_STOPPED),
                                 channel.latest(Topic.LAUNCHER_TERMINATED))
                 if e is not None),
                default=0,
            )
            if last_terminal > seq:
                return term
        if deadline is not None and now() >= deadline:
            raise TimeoutError(f"control seq {seq} not consumed within {timeout}s")
        sleep(poll_interval)
