"""Bring-your-own-launcher recipe: runstate over submitit (SLURM / local).

Proves the claim the README makes -- "spawn however you want (subprocess,
submitit, ray) and talk via the protocol." The ``SubmititLauncher`` /
``SubmititHandle`` below are EXAMPLE code implementing runstate's
``Launcher[Job]`` / ``LaunchHandle`` protocol over ``submitit.AutoExecutor``,
with ``Job`` its target record; nothing here is library code. It writes the
supervisor-v0.5 records a launcher owes the log: ``supervisor.launched`` at the
spawn, ``supervisor.terminated`` at the reap, and, since the job contains the
claimant, a ``supervisor.died`` through ``report_death`` when the job ended
without its worker's own exit. The eventual first-class adapter's open design questions
live in docs/backlog/submitit-launcher.md.

Runs WITHOUT a SLURM cluster via ``cluster="local"`` (submitit's subprocess
executor). The real-cluster variant is one line -- ``cluster="slurm"`` -- plus a
``root`` on a shared filesystem both the head and compute nodes see, and
``RUNSTATE_SQLITE_JOURNAL_MODE=DELETE`` in the job env (the default WAL journal
needs shared memory an NFS mount can't back -- the README's NFS caveat). Both
one-liners are marked below.

Self-contained on purpose: submitit pickles the submitted function (by value,
via cloudpickle), so the worker lives here as a top-level function rather than a
separate importable ``worker.py``.

Degrades gracefully: with submitit absent it prints a one-line install notice
and exits 0.

    uv run --no-project --with-editable . --with submitit python examples/submitit/driver.py
"""

import os
import sys
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import KW_ONLY, asdict, dataclass, field
from typing import Any, Optional

import runstate

try:
    import submitit
except ImportError:
    print("examples/submitit needs submitit:  pip install submitit")
    sys.exit(0)


# --------------------------------------------------------------------------- #
# The worker -- runs inside the submitit job's process.                       #
# --------------------------------------------------------------------------- #
def _worker_main(until: runstate.Target) -> None:
    """A normal runstate worker, identical in spirit to
    examples/minimal/worker.py -- ``current_channel()`` finds the run, ``Worker``
    drains control and writes lifecycle, the loop exposes ``loss``. Only the
    launcher changed; the worker code did not."""
    with runstate.Worker(
        runstate.current_channel(),
        resume_from=None,
        sources=["loss"],
        save=runstate.NoSave(),
    ) as w:
        for step in w.steps(until):
            w.set("loss", max(0.01, 5.0 * (0.97**step)))
        if not w.stop_pending:  # ran the whole budget, rather than being stopped
            w.stopped(completed=True, error=None)


def _entrypoint(
    fn: Callable[..., None],
    kwargs: Mapping[str, Any],
    run_id: str,
    root: str,
    backend: str,
    launch_id: str,
) -> None:
    """What submitit runs in the job. It sets the ``RUNSTATE_*`` env (exactly as
    ``LocalLauncher`` does for its subprocess child) so the worker's
    ``current_channel()`` meets the same log, and ``RUNSTATE_LAUNCH_ID`` so the
    ``Worker``'s ``lifecycle.started`` names the launch it answers
    (specs/launcher-record-identity.md)."""
    os.environ["RUNSTATE_RUN_ID"] = run_id
    os.environ["RUNSTATE_CHANNEL_ROOT"] = root
    os.environ["RUNSTATE_CHANNEL_BACKEND"] = backend
    os.environ[runstate.LAUNCH_ID_ENV] = launch_id
    # os.environ["RUNSTATE_SQLITE_JOURNAL_MODE"] = "DELETE"  # <-- on a real cluster (NFS)
    fn(**kwargs)


# --------------------------------------------------------------------------- #
# The launcher -- EXAMPLE code implementing Launcher[Job] / LaunchHandle.     #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Job:
    """What ``SubmititLauncher`` runs: ``fn(**kwargs)`` in a submitit job. Its
    target record, as ``ThreadTarget`` is ``ThreadLauncher``'s: frozen, and
    ``kwargs``, which change the worker's result, required."""

    fn: Callable[..., None]
    _: KW_ONLY
    kwargs: Mapping[str, Any]


@dataclass
class SubmititHandle:
    """A ``LaunchHandle`` over a submitit ``Job``. Liveness reads the job object's
    own state (``is_alive``); the portable ``handle`` string is
    ``slurm://<job_id>``. ``resolve()`` abstains on that scheme
    (vocabulary/handle.py) -- so a handle-*less* observer falls to the heartbeat
    tier -- while the Watcher, holding THIS object, uses ``is_alive`` / ``wait``
    directly (the OS-handle liveness tier), and reaps through ``wait``."""

    run_id: str
    channel: runstate.Channel
    handle: str
    _job: "submitit.Job[None]"
    launch_id: str
    _reaped: bool = field(default=False)

    def is_alive(self) -> bool:
        return not self._job.done()

    def wait(self, timeout: Optional[float] = None) -> Optional[int]:
        """Block until the job finishes, then reap it (its records, once).
        Returns the synthetic exit code."""
        deadline = None if timeout is None else time.time() + timeout
        while not self._job.done():
            if deadline is not None and time.time() >= deadline:
                return None
            time.sleep(0.05)
        return self._reap()

    def terminate(self) -> None:
        self._job.cancel()

    def _reap(self) -> int:
        """Record this job's end exactly once, correlated by ``launch_id``:
        ``supervisor.terminated`` always, since a launcher reports what its own
        job did; then, when a claim names the launch, a death report. The job
        contains the claimant, so its end is evidence of the claim's:
        ``report_death`` writes nothing when the claim has its own readable exit,
        and ``force=True`` since a finished job is definitive, as a reap is.

        submitit surfaces success/failure, not a real exit code, so map it
        synthetically (0/1) -- like ThreadLauncher's thread-death mapping; a
        viewer must not read the 1 as a process exit status."""
        raised = self._job.exception() is not None
        rc = 1 if raised else 0
        if self._reaped:
            return rc
        self._reaped = True
        t = time.time()
        end = runstate.Terminated(
            reason=runstate.Reap.EXITED, exit_code=rc, signal=None, t=t
        )
        self.channel.send(
            asdict(end),
            topic=runstate.Topic.SUPERVISOR_TERMINATED,
            request_id=self.launch_id,
        )
        # The claim naming this launch, in the log as read now.
        claim = runstate.launch_claim(
            self.channel, self.launch_id, after=0, w=self.channel.last_seq()
        )
        if claim is not None:
            # A raise is errored; a return with the claim still held is lost
            # (which lands only while it is held).
            outcome = runstate.Outcome.ERRORED if raised else runstate.Outcome.LOST
            evidence = (
                f"submitit: job {self._job.job_id} {'raised' if raised else 'returned'}"
            )
            runstate.report_death(
                self.channel,
                claim.seq,
                outcome=outcome,
                evidence=evidence,
                force=True,
            )
        return rc


class SubmititLauncher:
    """A ``Launcher[Job]`` over submitit. The channel locators are uniform (as in
    the reference launchers); ``launch`` mints the launch id, submits the job,
    and records ``supervisor.launched`` (the spawn + the handle)."""

    def __init__(
        self, *, root: str, folder: str, backend: str = "sqlite", cluster: str = "local"
    ):
        self._root = root
        self._backend = backend
        # cluster="slurm" on a real cluster; "local" runs a subprocess here.
        self._executor = submitit.AutoExecutor(folder=folder, cluster=cluster)

    def create_channel(self, run_id: str) -> runstate.Channel:
        return runstate.create_channel(run_id, root=self._root, backend=self._backend)

    def attach_channel(self, run_id: str) -> runstate.Channel:
        return runstate.attach_channel(run_id, root=self._root, backend=self._backend)

    def launch(self, run_id: str, target: Job) -> SubmititHandle:
        # One opaque id per launch (specs/launcher-record-identity.md): its records
        # and its claim carry it.
        launch_id = runstate.new_launch_id()
        job = self._executor.submit(
            _entrypoint,
            target.fn,
            dict(target.kwargs),
            run_id,
            str(self._root),
            self._backend,
            launch_id,
        )
        handle_str = (
            f"slurm://{job.job_id}"  # resolve() abstains -> heartbeat/handle tiers
        )
        channel = self.create_channel(run_id)
        launched = runstate.Launched(handle=handle_str, status="running", t=time.time())
        channel.send(
            asdict(launched),
            topic=runstate.Topic.SUPERVISOR_LAUNCHED,
            request_id=launch_id,
        )
        return SubmititHandle(run_id, channel, handle_str, job, launch_id)


# --------------------------------------------------------------------------- #
# The driver -- mirrors examples/minimal/driver.py.                           #
# --------------------------------------------------------------------------- #
def main() -> None:
    root = tempfile.mkdtemp(prefix="runstate-submitit-")  # SHARED FS on a cluster
    folder = tempfile.mkdtemp(prefix="submitit-logs-")
    run_id = "submitit-demo"
    # Typed as the Protocol, so mypy checks that the recipe satisfies it.
    launcher: runstate.Launcher[Job] = SubmititLauncher(root=root, folder=folder)
    print(f"[driver] run_id={run_id} root={root}")

    # Subscribe BEFORE launch so the worker picks it up on its first drain.
    ch = launcher.create_channel(run_id)
    ch.send(
        {"every": {"commits": 1}},
        topic=runstate.Topic.CONTROL_SUBSCRIBE,
        name="loss",
        request_id="driver",
    )

    until = runstate.Target(runstate.Commits(), 20)
    handle = launcher.launch(run_id, Job(_worker_main, kwargs={"until": until}))
    print(f"[driver] launched {handle.handle}")

    watcher = runstate.Watcher(heartbeat_timeout=60.0, poll_interval=0.05)
    watcher.add(handle)

    # The answers to "driver", polled at each node (see examples/minimal).
    heads, answers = runstate.HeadFold(), runstate.Answers("driver")
    printed: set[int] = set()

    def on_event(rid: str, e: runstate.Envelope) -> None:
        if e.topic not in (
            runstate.Topic.LIFECYCLE_COMMIT,
            runstate.Topic.LIFECYCLE_STOPPED,
        ):
            return
        for a in answers.poll(ch, heads.poll(ch)):
            if a.seq not in printed:
                printed.add(a.seq)
                print(f"[driver] node {a.node:>3} loss {a.value:.4f}")

    # Block to the terminal verdict (the worker's own exit; the slurm:// handle
    # only matters for the inference tiers).
    result = watcher.wait(run_id, on_event=on_event)
    outcomes = ", ".join(sorted(result.outcomes))
    print(
        f"[driver] terminal: outcomes={{{outcomes}}} tier={result.tier} node={result.node}"
    )

    # Reap the job: its supervisor.terminated, correlated by the launch id. The
    # claim has its own exit, so report_death writes nothing. (Had the Watcher
    # found the job dead first, its poll would have reaped; a reap is once.)
    handle.wait()
    terminated = ch.read(topics=[runstate.Topic.SUPERVISOR_TERMINATED])
    print(
        "[driver] supervisor.terminated: "
        f"{[(t.request_id == handle.launch_id, t.body['reason']) for t in terminated]}"
    )


if __name__ == "__main__":
    main()
