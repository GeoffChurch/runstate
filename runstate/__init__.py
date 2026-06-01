"""runstate — cooperative control protocol for long-running scientific workers.

v0.2: a per-run topic-log **substrate** with opt-in **conventions** (cooperative-
control, subscription, lifecycle, launcher) and reference **orchestration**
helpers (launchers, Watcher, sweep). See docs/design-v0.2.md.

The names re-exported here are the public surface; everything else lives under
the submodules (runstate.channel, runstate.schedule, ...).
"""

import os

from .channel import Envelope, open_channel
from .launcher import Launcher, LaunchHandle, LocalLauncher, ThreadLauncher
from .liveness import RunResult, peek_terminal
from .vocabulary.payloads import (
    Heartbeat,
    Launched,
    Nak,
    Started,
    Stopped,
    Terminated,
    Value,
)
from .sweep import Variant, sweep
from .watcher import Running, RunStatus, Watcher
from .worker import Worker

__version__ = "0.2.0.dev0"


def attach(run_id=None, *, root=None, backend=None, json_default=None):
    """Worker-side: open the channel for the run this process was launched into.

    A Launcher sets ``RUNSTATE_RUN_ID`` / ``RUNSTATE_CHANNEL_ROOT`` /
    ``RUNSTATE_CHANNEL_BACKEND`` in the worker's environment; ``attach()`` reads
    them. Explicit arguments override the environment. Mirrors how the
    orchestrator named the run, so both ends meet on the same log. ``json_default``
    is a sender-side ``json.dumps`` hook for coercing exotic value payloads
    (e.g. numpy scalars / tensors) the worker reports.
    """
    if run_id is None:
        run_id = os.environ["RUNSTATE_RUN_ID"]
    if root is None:
        root = os.environ.get("RUNSTATE_CHANNEL_ROOT")
    if backend is None:
        backend = os.environ.get("RUNSTATE_CHANNEL_BACKEND", "sqlite")
    return open_channel(run_id, root=root, backend=backend, json_default=json_default)


__all__ = [
    "__version__",
    # substrate
    "open_channel",
    "attach",
    "Envelope",
    # worker
    "Worker",
    # launchers
    "Launcher",
    "LaunchHandle",
    "ThreadLauncher",
    "LocalLauncher",
    # orchestration / liveness
    "Watcher",
    "RunStatus",
    "Running",
    "RunResult",
    "peek_terminal",
    "sweep",
    "Variant",
    # convention bodies (typed; serialize via dataclasses.asdict, parse via Cls(**body))
    "Value",
    "Started",
    "Heartbeat",
    "Stopped",
    "Nak",
    "Launched",
    "Terminated",
]
