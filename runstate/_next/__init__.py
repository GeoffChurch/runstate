"""The staged live layer of log format 0.4.0 (docs/specs/commits-and-lineage.md), and
its public surface.

STAGING ONLY. ``docs/plans/2026-10-28-layer2-live.md`` builds the 0.4.0 live layer
here, beside the unchanged 0.3.0 modules, in a package that mirrors the final
layout, so every commit stays green while the Worker, the reads and their tests
move over a task at a time. Its tests are ``tests/test_next_*.py``. Task 19, the
cutover, moves each module here over its 0.3.0 namesake and deletes this package;
``__all__`` below is the surface it makes ``runstate.__all__`` (plan Task 16).

Modules here import each other relatively (``.x`` in this package, ``..x`` from
``vocabulary/``) and reach unchanged package modules one level further up, never
through an absolute ``runstate.`` import, so the cutover's rewrite is mechanical.

THE SURFACE. The substrate is unchanged. Against 0.3.0: the Worker takes
``resume_from``, ``sources`` and ``save``, with the checkpoint recipe beside it;
a launch takes its launcher's target record; the observer plane reads a lineage by
a ``head``, with ``prefix_len``, ``prefix``, ``series``, ``last_element``,
``aligned``, ``answers`` and ``streams``, and their held folds; the verdict's
``Tier`` and ``DeathReport``; ``ensure`` returns ``Reached`` or ``Unreachable``.
``progress`` and ``value_series`` are removed (§5).
"""

import os
from collections.abc import Callable

from ..channel import (
    Body,
    Channel,
    Envelope,
    RunNotFound,
    attach_channel,
    create_channel,
)
from ..formats import LOG_FORMAT, LogFormatError, LogFormatMismatch, LogFormatMissing
from ..vocabulary.handle import handle_pid
from .checkpoint import (
    CheckpointChoice,
    Every,
    MostProgress,
    NoSave,
    OnHeadLineage,
    SavePolicy,
)
from .launcher import (
    ClaimAliveError,
    Command,
    Drivable,
    Launcher,
    LaunchHandle,
    LocalLauncher,
    ReportStarvedError,
    ThreadLauncher,
    ThreadTarget,
    ensure_served,
    relaunch_if_needed,
    report_death,
    report_if_dead,
)
from .memoizer import (
    NoProgressError,
    Reached,
    RecordlessExitError,
    RunFailedError,
    Unreachable,
    ensure,
    foreign_episode,
    history,
    launch_producer,
)
from .observables import (
    AlignedRow,
    Answers,
    AtNode,
    Complete,
    DeathReport,
    Element,
    Head,
    HeadFold,
    HeadStrategy,
    LastElement,
    LatestClaimHead,
    LiveDemand,
    MalformedRecordError,
    Pending,
    PendingStops,
    PrefixLen,
    RunResult,
    SettledShort,
    Streams,
    Tier,
    aligned,
    answers,
    last_activity,
    last_element,
    latest_episode,
    live_demand,
    live_episode,
    peek_terminal,
    prefix,
    prefix_len,
    series,
    streams,
    undischarged_stops,
    worker_completed,
)
from .sweep import Variant, sweep
from .vocabulary.coordinate import Commits, NodeRef, Target
from .vocabulary.payloads import (
    Bound,
    Commit,
    Died,
    Heartbeat,
    Launched,
    Nak,
    Node,
    Outcome,
    Rewind,
    Started,
    Stopped,
    Terminated,
    Topic,
    Value,
    Withdraw,
)
from .vocabulary.schedule import Condition, MalformedSchedule
from .watcher import Running, RunStatus, Watcher, await_consumed
from .worker import Worker, WorkerStarvedError


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
    value payloads (e.g. numpy scalars / tensors) the worker reports: an exempt
    default (plan, "Exempt defaults"), since it encodes, and changes no record a
    plain value would write.

    A launcher also sets ``RUNSTATE_LAUNCH_ID`` (the launch's correlation id);
    the ``Worker`` re-emits it on its ``lifecycle.started`` so its claim names
    the launch it answers: read by ``vocabulary/launch.py``, not here, since it
    identifies the claim, not the channel.
    """
    run_id = os.environ["RUNSTATE_RUN_ID"]
    root = os.environ.get("RUNSTATE_CHANNEL_ROOT")
    backend = os.environ.get("RUNSTATE_CHANNEL_BACKEND", "sqlite")
    return create_channel(run_id, root=root, backend=backend, json_default=json_default)


__all__ = [
    # substrate, unchanged
    "attach_channel",
    "create_channel",
    "current_channel",
    "RunNotFound",
    "LOG_FORMAT",
    "LogFormatError",
    "LogFormatMismatch",
    "LogFormatMissing",
    "Channel",
    "Body",
    "Envelope",
    # the worker and the checkpoint recipe
    "Worker",
    "SavePolicy",
    "Every",
    "NoSave",
    "CheckpointChoice",
    "OnHeadLineage",
    "MostProgress",
    "Commits",
    "Target",
    "NodeRef",
    # launchers and death reports
    "Launcher",
    "LaunchHandle",
    "ThreadLauncher",
    "LocalLauncher",
    "ThreadTarget",
    "Command",
    "Drivable",
    "relaunch_if_needed",
    "ensure_served",
    "report_death",
    "report_if_dead",
    "ClaimAliveError",
    # the observer plane and orchestration
    "Watcher",
    "await_consumed",
    "RunStatus",
    "Running",
    "RunResult",
    "Outcome",
    "Tier",
    "DeathReport",
    "MalformedRecordError",
    "peek_terminal",
    "worker_completed",
    "last_activity",
    "latest_episode",
    "live_demand",
    "live_episode",
    "undischarged_stops",
    "Head",
    "HeadStrategy",
    "LatestClaimHead",
    "AtNode",
    "Element",
    "Complete",
    "Pending",
    "SettledShort",
    "AlignedRow",
    "Streams",
    "prefix_len",
    "prefix",
    "series",
    "last_element",
    "aligned",
    "answers",
    "streams",
    "HeadFold",
    "PrefixLen",
    "LastElement",
    "Answers",
    "LiveDemand",
    "PendingStops",
    "handle_pid",
    # the memoizer and the sweep
    "history",
    "ensure",
    "Reached",
    "Unreachable",
    "launch_producer",
    "foreign_episode",
    "RunFailedError",
    "NoProgressError",
    "RecordlessExitError",
    "sweep",
    "Variant",
    # the errors public calls raise (ClaimAliveError, above, among them)
    "ReportStarvedError",
    "WorkerStarvedError",
    "MalformedSchedule",
    # the convention vocabulary and its typed bodies (serialize via
    # dataclasses.asdict, parse via Cls(**body))
    "Topic",
    "Condition",
    "Value",
    "Started",
    "Commit",
    "Heartbeat",
    "Rewind",
    "Stopped",
    "Bound",
    "Nak",
    "Withdraw",
    "Launched",
    "Terminated",
    "Died",
    "Node",
]
