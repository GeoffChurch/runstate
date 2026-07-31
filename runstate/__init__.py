"""runstate — cooperative control protocol for long-running scientific workers.

v0.2: a per-run topic-log **substrate** with opt-in **conventions** (cooperative-
control, subscription, lifecycle, launcher) and reference **orchestration**
helpers (launchers, Watcher, sweep). See docs/design-v0.2.md.

The names re-exported here are the public surface; everything else lives under
the submodules (runstate.channel, runstate.vocabulary, ...).
"""

import os
from collections.abc import Callable

from .channel import (
    Body,
    Channel,
    Envelope,
    RunNotFound,
    attach_channel,
    create_channel,
)
from .launcher import (
    Launcher,
    LaunchHandle,
    LocalLauncher,
    ThreadLauncher,
    ensure_served,
    relaunch_if_needed,
)
from .memoizer import (
    NoProgressError,
    RecordlessExitError,
    RunFailedError,
    ensure,
    foreign_episode,
    history,
    launch_producer,
)
from .observables import (
    MalformedRecordError,
    Outcome,
    RunResult,
    last_activity,
    latest_episode,
    live_demand,
    live_episode,
    peek_terminal,
    worker_completed,
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


def current_channel(
    json_default: Callable[[object], object] | None = None,
) -> Channel:
    """Worker-side: open (or birth) the channel for the run this process was
    launched into.

    A Launcher sets ``RUNSTATE_RUN_ID`` / ``RUNSTATE_CHANNEL_ROOT`` /
    ``RUNSTATE_CHANNEL_BACKEND`` in the worker's environment; ``current_channel``
    reads them and delegates to ``create_channel`` (open-or-create, so a
    launcher-less direct run still births its own log). Mirrors how the
    orchestrator named the run, so both ends meet on the same log.
    ``json_default`` is a sender-side ``json.dumps`` hook for coercing exotic
    value payloads (e.g. numpy scalars / tensors) the worker reports.

    A launcher also sets ``RUNSTATE_LAUNCH_ID`` (the launch's correlation id);
    the ``Worker`` re-emits it on its ``lifecycle.started`` so its claim names
    the launch it answers — read by ``vocabulary/launch.py``, not here, since it
    identifies the *episode*, not the channel.
    """
    run_id = os.environ["RUNSTATE_RUN_ID"]
    root = os.environ.get("RUNSTATE_CHANNEL_ROOT")
    backend = os.environ.get("RUNSTATE_CHANNEL_BACKEND", "sqlite")
    return create_channel(run_id, root=root, backend=backend, json_default=json_default)


__all__ = [
    # substrate
    "attach_channel",
    "create_channel",
    "current_channel",
    "RunNotFound",
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
    "worker_completed",
    "last_activity",
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
    "RecordlessExitError",
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
