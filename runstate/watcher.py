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
from dataclasses import dataclass, field, replace
from typing import Optional, Union

from .liveness import RunResult, peek_terminal


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
    channel: object
    handle: Optional[object]
    last_heartbeat_at: float
    last_hb_seq: int = field(default=0)
    last_step: Optional[int] = field(default=None)


class Watcher:
    def __init__(
        self,
        *,
        now=time.time,
        sleep=time.sleep,
        poll_interval: float = 0.05,
        heartbeat_timeout: Optional[float] = None,
    ):
        self._now = now
        self._sleep = sleep
        self._poll_interval = poll_interval
        self._hb_timeout = heartbeat_timeout
        self._runs: dict[str, _RunState] = {}
        self._event_cursors: dict[str, int] = {}

    def add(self, handle) -> None:
        """Track a launched run by its handle (enables the probe tier)."""
        self._track(handle.run_id, handle.channel, handle)

    def observe(self, run_id: str, channel) -> None:
        """Track a run by run_id + channel, handle-free (late-attach or
        observe-only). The probe tier is unavailable; staleness still applies."""
        self._track(run_id, channel, None)

    def _track(self, run_id, channel, handle) -> None:
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

        # tier 3: probe the handle (re-check the log first, to lose to a final
        # write that raced our peek above).
        if st.handle is not None and not st.handle.is_alive():
            r = peek_terminal(st.channel)
            if r is not None:
                return replace(r, run_id=run_id)
            return RunResult(outcome="presumed_dead", reason="probed_dead", run_id=run_id)

        # tier 4: heartbeat staleness.
        beacon_age = self._now() - st.last_heartbeat_at
        if self._hb_timeout is not None and beacon_age > self._hb_timeout:
            return RunResult(
                outcome="presumed_dead", reason="heartbeat_stale", run_id=run_id
            )

        return Running(step=st.last_step, beacon_age=beacon_age)

    def wait(
        self, run_id: str, *, on_event=None, timeout: Optional[float] = None
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
            if s.done:
                return s
            if deadline is not None and self._now() >= deadline:
                raise TimeoutError(f"run {run_id!r} not terminal within {timeout}s")
            self._sleep(self._poll_interval)

    def wait_all(self, *, on_event=None, timeout: Optional[float] = None) -> dict:
        """Block until every tracked run is terminal, returning ``{run_id:
        RunStatus}`` total over the tracked set. Uncapped this is a pure
        synchronization (a slow-but-healthy run delays it, by design). With
        ``timeout`` it returns at the deadline with the still-running runs as
        their Running status — so pending is explicit (which runs, and how stale),
        not absence."""
        deadline = None if timeout is None else self._now() + timeout
        results: dict = {}
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
                break
            if deadline is not None and self._now() >= deadline:
                for run_id in pending:  # report the laggards as Running
                    results[run_id] = self.poll(run_id)
                break
            self._sleep(self._poll_interval)
        return results

    def broadcast(self, name: str, schedule: dict, *, request_id=None) -> str:
        """Fan one ``control.subscribe`` across every tracked run under a single
        shared ``request_id`` (returned). The run_id disambiguates the responses;
        this is the cross-run barrier primitive — no Experiment class. The caller
        then collects responses via iter_events / wait_all filtered on the id."""
        rid = request_id if request_id is not None else f"broadcast-{uuid.uuid4().hex}"
        for st in self._runs.values():
            st.channel.send(
                dict(schedule), topic="control.subscribe", name=name, request_id=rid
            )
        return rid

    def iter_events(self, timeout: Optional[float] = None):
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

    def _drain(self) -> list:
        """Pull all envelopes new since the last drain across every tracked run,
        advancing the per-run event cursor. Shared by iter_events and wait's
        on_event streaming."""
        out = []
        for run_id, st in list(self._runs.items()):
            cur = self._event_cursors.get(run_id, 0)
            for e in st.channel.read(after=cur):
                self._event_cursors[run_id] = e.seq
                out.append((run_id, e))
        return out

    def _note_heartbeat(self, st: _RunState) -> None:
        hb = st.channel.latest("lifecycle.heartbeat")
        if hb is not None and hb.seq > st.last_hb_seq:
            st.last_hb_seq = hb.seq
            st.last_heartbeat_at = self._now()
            st.last_step = hb.body.get("step")
