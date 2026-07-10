"""Killed-redrive: the caller pattern
(docs/backlog/ensure-redrive-recoverable-terminations.md).

``ensure`` auto-continues clean ``preempted`` stops, but **fails fast on a death**
(killed / errored) -- the retry decision is the *caller's*, not an in-``ensure``
policy. Here a worker crashes once (a recordless, SIGKILL/OOM-style death); the driver
catches the failure, decides it is resumable (the worker did NOT self-diagnose a fatal
error), and **re-calls ``ensure``**, which resumes from the checkpoint. G1
take-the-latest absorbs the resumed checkpoint overlap, so reuse stays sound -- the
result is one continuous series.

The retry budget and the per-attempt visibility live *here*, in the caller's loop,
where the budget is -- not as a give-up policy baked into the library.

Subprocess workers (``LocalLauncher``) can't take kwargs, so this brings its own tiny
producer that plumbs the step target via env -- the ``.channel`` / ``.run_id`` /
``.extend`` seam.
"""

import sys
import tempfile
from pathlib import Path

import runstate

WORKER = Path(__file__).parent / "worker.py"


class SubprocessProducer:
    """Drives a LocalLauncher subprocess worker toward ``until['step']``, plumbing the
    target via env. ``extend`` relaunches iff not already live (else hands back the live
    episode's foreign handle) -- the Recipe-2 gate, never ``None``."""

    def __init__(self, launcher, run_id, state_dir):
        self._launcher = launcher
        self.run_id = run_id
        self._state = state_dir

    @property
    def channel(self):
        return self._launcher.open_channel(self.run_id)

    def extend(self, until):
        return runstate.relaunch_if_needed(
            self._launcher, self.run_id, [sys.executable, str(WORKER)],
            env={"REDRIVE_UP_TO": str(until["step"]), "REDRIVE_STATE": str(self._state)},
        ) or runstate.foreign_episode(self.channel)


def resumable(result) -> bool:
    """The caller's retry predicate: a non-self-diagnosed death (killed / recordless
    exit) is resumable; a worker that wrote ``Stopped(error=...)`` self-diagnosed fatal,
    so don't retry it. Only the worker knows its death was fatal."""
    return (result is not None and result.error is None
            and result.outcome in (runstate.Outcome.KILLED, runstate.Outcome.ERRORED))


def main():
    rid = "redrive-demo"
    with tempfile.TemporaryDirectory() as root:
        state = Path(root) / "state"
        state.mkdir()
        with runstate.LocalLauncher(root=root) as launcher:
            # subscribe before launch so the worker reports loss every step
            launcher.open_channel(rid).send(
                {"every": {"step": 1}}, topic=runstate.Topic.CONTROL_SUBSCRIBE,
                name="loss", request_id="obs",
            )
            producer = SubprocessProducer(launcher, rid, state)

            series = None
            for attempt in range(1, 6):                 # the caller owns the retry budget
                try:
                    series = runstate.ensure(producer, "loss", until={"step": 10})
                    print(f"[driver] attempt {attempt}: ensure returned")
                    break
                except RuntimeError:
                    r = runstate.peek_terminal(producer.channel)
                    ok = resumable(r)
                    desc = ("recordless" if r is None
                            else f"outcome={r.outcome}, error={r.error!r}")
                    print(f"[driver] attempt {attempt}: ensure raised ({desc}) -> "
                          f"{'resumable, re-calling' if ok else 'fatal, giving up'}")
                    if not ok:
                        raise
            else:
                raise RuntimeError("exhausted retry budget")

            print(f"[driver] one continuous series 0..{series[-1]['step']} "
                  f"(len {len(series)}); the resumed checkpoint overlap collapsed "
                  f"via take-the-latest")


if __name__ == "__main__":
    main()
