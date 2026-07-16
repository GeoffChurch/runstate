"""Bring-your-own-launcher recipe: runstate over submitit (SLURM / local).

Proves the claim the README makes -- "spawn however you want (subprocess,
submitit, ray) and talk via the protocol." The ``SubmititLauncher`` /
``SubmititHandle`` below are ~50 lines of EXAMPLE code implementing runstate's
``Launcher`` / ``LaunchHandle`` protocol over ``submitit.AutoExecutor``; nothing
here is library code. The eventual first-class adapter's open design questions
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
from dataclasses import asdict, dataclass, field
from typing import Optional

import runstate
from runstate.vocabulary.launch import new_launch_id  # mint the launch's correlation id
from runstate.vocabulary.payloads import Launched, Terminated

try:
    import submitit
except ImportError:
    print("examples/submitit needs submitit:  pip install submitit")
    sys.exit(0)


# --------------------------------------------------------------------------- #
# The worker -- runs inside the submitit job's process.                       #
# --------------------------------------------------------------------------- #
def _worker_main(total: int) -> None:
    """A normal runstate worker, identical in spirit to
    examples/minimal/worker.py -- ``attach()`` finds the run, ``Worker`` drains
    control and beacons lifecycle, the loop reports ``loss``. Only the launcher
    changed; the worker code did not."""
    import math

    with runstate.Worker(runstate.attach()) as w:
        for step in w.steps(total=total):
            w.set("loss", max(0.01, 5.0 * (0.97**step)))
        w.stopped(completed=True)  # finished the budget -> claim completion


def _entrypoint(
    run_id: str, root: str, backend: str, launch_id: str, total: int
) -> None:
    """What submitit runs in the job. It sets the ``RUNSTATE_*`` env (exactly as
    ``LocalLauncher`` does for its subprocess child) so the worker's ``attach()``
    meets the same log, and ``RUNSTATE_LAUNCH_ID`` so the ``Worker``'s
    ``lifecycle.started`` names the launch it answers
    (specs/launcher-record-identity.md)."""
    os.environ["RUNSTATE_RUN_ID"] = run_id
    os.environ["RUNSTATE_CHANNEL_ROOT"] = root
    os.environ["RUNSTATE_CHANNEL_BACKEND"] = backend
    os.environ["RUNSTATE_LAUNCH_ID"] = launch_id
    # os.environ["RUNSTATE_SQLITE_JOURNAL_MODE"] = "DELETE"  # <-- on a real cluster (NFS)
    _worker_main(total)


# --------------------------------------------------------------------------- #
# The launcher -- EXAMPLE code implementing Launcher / LaunchHandle.          #
# --------------------------------------------------------------------------- #
@dataclass
class SubmititHandle:
    """A ``LaunchHandle`` over a submitit ``Job``. Liveness reads the job object's
    own state (``is_alive``); the portable ``handle`` string is
    ``slurm://<job_id>``. ``resolve()`` abstains on that scheme
    (vocabulary/handle.py) -- so a handle-*less* observer falls to the heartbeat
    tier -- while the Watcher, holding THIS object, uses ``is_alive`` / ``wait``
    directly (the OS-handle liveness tier)."""

    run_id: str
    channel: runstate.Channel
    handle: str
    _job: "submitit.Job"
    launch_id: str
    _reaped: bool = field(default=False)

    def is_alive(self) -> bool:
        return not self._job.done()

    def wait(self, timeout: Optional[float] = None) -> Optional[int]:
        """Block until the job finishes, then reap (emit ``launcher.terminated``
        once). Returns the synthetic exit code."""
        deadline = None if timeout is None else time.time() + timeout
        while not self._job.done():
            if deadline is not None and time.time() >= deadline:
                return None
            time.sleep(0.05)
        return self._reap()

    def terminate(self) -> None:
        self._job.cancel()

    def _reap(self) -> int:
        """Record this job's death exactly once, correlated by ``launch_id``.
        submitit surfaces success/failure, not a real exit code, so map it
        synthetically (0/1) -- like ThreadLauncher's thread-death mapping; a
        viewer must not read the 1 as a process exit status."""
        rc = 1 if self._job.exception() is not None else 0
        if not self._reaped:
            self._reaped = True
            self.channel.send(
                asdict(
                    Terminated(
                        reason="exited", exit_code=rc, signal=None, t=time.time()
                    )
                ),
                topic=Terminated.TOPIC,
                request_id=self.launch_id,
            )
        return rc


class SubmititLauncher:
    """A ``Launcher`` over submitit. ``open_channel`` is uniform (as in the
    reference launchers); ``launch`` submits the worker to the executor, mints
    the launch id, and records ``launcher.launched`` (the spawn + the handle).
    The worker function is fixed here; the loop budget ``total`` is the
    launcher-specific config (as ``cmd`` is for ``LocalLauncher``)."""

    def __init__(
        self, *, root: str, folder: str, backend: str = "sqlite", cluster: str = "local"
    ):
        self._root = root
        self._backend = backend
        # cluster="slurm" on a real cluster; "local" runs a subprocess here.
        self._executor = submitit.AutoExecutor(folder=folder, cluster=cluster)

    def open_channel(self, run_id: str) -> runstate.Channel:
        return runstate.open_channel(run_id, root=self._root, backend=self._backend)

    def launch(self, run_id: str, total: int) -> SubmititHandle:
        launch_id = new_launch_id()
        job = self._executor.submit(
            _entrypoint, run_id, str(self._root), self._backend, launch_id, total
        )
        handle_str = (
            f"slurm://{job.job_id}"  # resolve() abstains -> heartbeat/handle tiers
        )
        channel = self.open_channel(run_id)
        channel.send(
            asdict(Launched(handle=handle_str, t=time.time())),
            topic=Launched.TOPIC,
            request_id=launch_id,
        )
        return SubmititHandle(run_id, channel, handle_str, job, launch_id)


# --------------------------------------------------------------------------- #
# The driver -- mirrors examples/minimal/driver.py.                           #
# --------------------------------------------------------------------------- #
def main() -> None:
    root = tempfile.mkdtemp(
        prefix="runstate-submitit-"
    )  # a SHARED-FS path on a real cluster
    folder = tempfile.mkdtemp(prefix="submitit-logs-")
    run_id = "submitit-demo"
    launcher = SubmititLauncher(root=root, folder=folder)
    print(f"[driver] run_id={run_id} root={root}")

    # Subscribe BEFORE launch so the worker picks it up on its first tick.
    ch = launcher.open_channel(run_id)
    ch.send(
        {"every": {"step": 1}},
        topic=runstate.Topic.CONTROL_SUBSCRIBE,
        name="loss",
        request_id="driver",
    )

    handle = launcher.launch(run_id, total=20)
    print(f"[driver] launched {handle.handle}")

    watcher = runstate.Watcher(poll_interval=0.05)
    watcher.add(handle)

    def on_event(rid, e):
        if e.topic == runstate.Topic.VALUE and e.request_id == "driver":
            print(f"[driver] step {e.body['step']:>2} loss {e.body['value']:.4f}")

    # Block to the terminal verdict (the worker's clean lifecycle.stopped wins
    # tiers 1-2; the slurm:// handle only matters for the inference tiers).
    result = watcher.wait(run_id, on_event=on_event)
    print(
        f"[driver] terminal: outcome={result.outcome} reason={result.reason!r} "
        f"final_step={result.final_step}"
    )

    # Reap the job to record launcher.terminated (correlated by the launch id).
    handle.wait()
    terminated = ch.read(topics=[runstate.Topic.LAUNCHER_TERMINATED])
    print(
        f"[driver] launcher.terminated: {[(t.request_id == handle.launch_id, t.body['reason']) for t in terminated]}"
    )


if __name__ == "__main__":
    main()
