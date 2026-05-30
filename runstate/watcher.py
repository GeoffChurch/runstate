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
from dataclasses import dataclass, field, replace
from typing import Optional

from .liveness import RunResult, peek_terminal


@dataclass
class _RunState:
    run_id: str
    channel: object
    handle: Optional[object]
    last_heartbeat_at: float
    last_hb_seq: int = field(default=0)


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

    def poll(self, run_id: str) -> Optional[RunResult]:
        """One non-blocking verdict for ``run_id`` across all tiers, or None if it
        still looks alive."""
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
        if (
            self._hb_timeout is not None
            and (self._now() - st.last_heartbeat_at) > self._hb_timeout
        ):
            return RunResult(
                outcome="presumed_dead", reason="heartbeat_stale", run_id=run_id
            )

        return None

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
            r = self.poll(run_id)
            if r is not None:
                return r
            if deadline is not None and self._now() >= deadline:
                raise TimeoutError(f"run {run_id!r} not terminal within {timeout}s")
            self._sleep(self._poll_interval)

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
