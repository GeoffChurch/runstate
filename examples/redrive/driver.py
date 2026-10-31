"""Killed-redrive: the caller pattern
(docs/backlog/ensure-redrive-recoverable-terminations.md).

``ensure`` auto-continues clean ``preempted`` stops, but **fails fast on a death**
(killed / errored) -- the retry decision is the *caller's*, not an in-``ensure``
policy. Here a worker crashes once (a recordless, SIGKILL/OOM-style death); the
launcher's reap reports it (``supervisor.died``, errored), the driver catches the
failure, decides it is resumable (the worker did NOT self-diagnose a fatal error),
and **re-calls ``ensure``**, which relaunches; the worker resumes from its
checkpoint. The crashed claim's commits past that checkpoint stay on the log, on
a branch the new lineage does not include, so the result is one continuous series
with nothing to collapse.

The retry budget and the per-attempt visibility live *here*, in the caller's loop,
where the budget is -- not as a give-up policy baked into the library.

Subprocess workers (``LocalLauncher``) take no ``Target``: a ``Command`` is not
``Drivable``. So this brings its own tiny producer that plumbs the target via env,
as ``Target.atom()`` JSON -- the ``.channel`` / ``.run_id`` / ``.extend`` seam.
"""

import json
import sys
import tempfile
from pathlib import Path

import runstate

WORKER = Path(__file__).parent / "worker.py"


class SubprocessProducer:
    """Drives a LocalLauncher subprocess worker toward a ``Target``, plumbing it
    via env. ``extend`` relaunches iff the run's claim is not held (else hands back
    the held claim's foreign handle) -- the Recipe-2 gate, never ``None``."""

    def __init__(self, launcher: runstate.LocalLauncher, run_id: str, state_dir: Path):
        self._launcher = launcher
        self.run_id = run_id
        self._state = state_dir
        self._channel: runstate.Channel | None = None

    @property
    def channel(self) -> runstate.Channel:
        """One handle on the run, opened (birthing it) on first access, then held."""
        if self._channel is None:
            self._channel = self._launcher.create_channel(self.run_id)
        return self._channel

    def extend(self, target: runstate.Target) -> runstate.LiveHandle:
        env = {
            "REDRIVE_UNTIL": json.dumps(target.atom()),
            "REDRIVE_STATE": str(self._state),
        }
        cmd = runstate.Command([sys.executable, str(WORKER)], env=env)
        return runstate.relaunch_if_needed(
            self._launcher, self.run_id, cmd
        ) or runstate.foreign_episode(self.channel)


def resumable(result: runstate.RunResult) -> bool:
    """The caller's retry predicate: a non-self-diagnosed death (killed, or
    exited without writing a diagnosis) is resumable; a worker that wrote
    ``Stopped(error=...)`` self-diagnosed fatal, so don't retry it. Only the
    worker knows its death was fatal. ``outcomes`` is a set: under death
    reports it holds each report's outcome."""
    deaths = {runstate.Outcome.KILLED, runstate.Outcome.ERRORED}
    return result.error is None and bool(result.outcomes & deaths)


def main() -> None:
    rid = "redrive-demo"
    with tempfile.TemporaryDirectory() as root:
        state = Path(root) / "state"
        state.mkdir()
        with runstate.LocalLauncher(root=root) as launcher:
            producer = SubprocessProducer(launcher, rid, state)
            target = runstate.Target("loss", 10)

            for attempt in range(1, 6):  # the caller owns the retry budget
                try:
                    reached = runstate.ensure(producer, target)
                    print(f"[driver] attempt {attempt}: ensure returned {reached}")
                    break
                except runstate.RunFailedError as e:
                    r = e.result  # the verdict, observed at raise time
                    ok = resumable(r)
                    outcomes = ", ".join(sorted(r.outcomes))
                    print(
                        f"[driver] attempt {attempt}: ensure raised "
                        f"(outcomes={{{outcomes}}}, tier={r.tier}, error={r.error!r}, "
                        f"evidence={[d.evidence for d in r.reports]}) "
                        f"-> {'resumable, re-calling' if ok else 'fatal, giving up'}"
                    )
                    if not ok:
                        raise
            else:
                raise RuntimeError("exhausted retry budget")

            if not isinstance(reached, runstate.Reached) or reached.node is None:
                raise SystemExit(f"[driver] the target is out of reach: {reached}")
            ch = producer.channel
            series = runstate.series(ch, "loss", head=runstate.AtNode(reached.node))
            on_log = len(ch.read(topics=[runstate.Topic.VALUE], name="loss"))
            print(
                f"[driver] one continuous series of {len(series)} losses at node "
                f"{reached.node}; {on_log - len(series)} more on the log, off its lineage "
                "(the crashed claim's work past its checkpoint)"
            )


if __name__ == "__main__":
    main()
