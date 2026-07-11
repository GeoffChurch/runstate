"""runstate — cooperative control protocol for long-running scientific workers.

v0.2: a per-run topic-log **substrate** with opt-in **conventions** (cooperative-
control, subscription, lifecycle, launcher) and reference **orchestration**
helpers (launchers, Watcher, sweep). See docs/design-v0.2.md.

The names re-exported here are the public surface; everything else lives under
the submodules (runstate.channel, runstate.schedule, ...).
"""

import os
from collections.abc import Callable

from .channel import Body, Channel, Envelope, open_channel
from .launcher import (Launcher, LaunchHandle, LocalLauncher, ThreadLauncher,
                       ensure_served, relaunch_if_needed)
from .memoizer import (NoProgressError, RunFailedError, ensure,
                       foreign_episode, history, launch_producer)
from .observables import (
    MalformedRecordError,
    Outcome,
    RunResult,
    latest_episode,
    live_demand,
    live_episode,
    peek_terminal,
    progress,
    undischarged_stops,
    value_series,
)
from .vocabulary.handle import handle_pid
from .vocabulary.schedule import Condition
from .vocabulary.payloads import (
    Heartbeat,
    Launched,
    Nak,
    Started,
    Stopped,
    Terminated,
    Topic,
    Value,
)
from .sweep import Variant, sweep
from .watcher import Running, RunStatus, Watcher, await_consumed
from .worker import Worker


def attach(run_id: str | None = None, *, root: str | os.PathLike[str] | None = None,
           backend: str | None = None,
           json_default: Callable[[object], object] | None = None) -> Channel:
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
    # substrate
    "open_channel",
    "attach",
    "Channel",
    "Body",
    "Envelope",
    # worker
    "Worker",
    # launchers
    "Launcher",
    "LaunchHandle",
    "ThreadLauncher",
    "LocalLauncher",
    # observables (the stateless observer plane) / orchestration
    "Watcher",
    "await_consumed",
    "RunStatus",
    "Running",
    "RunResult",
    "Outcome",
    "MalformedRecordError",
    "peek_terminal",
    "latest_episode",
    "live_demand",
    "live_episode",
    "progress",
    "undischarged_stops",
    "value_series",
    "handle_pid",
    "sweep",
    "Variant",
    "history",
    "ensure",
    "launch_producer",
    "foreign_episode",
    "RunFailedError",
    "NoProgressError",
    "relaunch_if_needed",
    "ensure_served",
    # convention vocabulary
    "Topic",
    "Condition",
    # convention bodies (typed; serialize via dataclasses.asdict, parse via Cls(**body))
    "Value",
    "Started",
    "Heartbeat",
    "Stopped",
    "Nak",
    "Launched",
    "Terminated",
]
