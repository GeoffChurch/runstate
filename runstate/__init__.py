"""runstate: cooperative bidirectional control between an orchestrator and a
long-running worker, over a per-run append-only topic log
(docs/specs/commits-and-lineage.md, log format 0.4.0), and its public surface.

THE SURFACE. The substrate is unchanged. Against 0.3.0: the Worker takes
``resume_from``, ``sources`` and ``save``, with the checkpoint recipe beside it;
a launch takes its launcher's target record; the observer plane reads a lineage by
a ``head``, with ``prefix_len``, ``prefix``, ``series``, ``last_element``,
``aligned``, ``answers`` and ``streams``, and their held folds; the verdict's
``Tier`` and ``DeathReport``; ``ensure`` returns ``Reached`` or ``Unreachable``.
``progress`` and ``value_series`` are removed (§5).
"""

import os

from .channel import (
    Body,
    Channel,
    Envelope,
    Logs,
    ProcessLogs,
    Reader,
    RunNotFound,
)
from .formats import LOG_FORMAT, LogFormatError, LogFormatMismatch, LogFormatMissing
from .vocabulary.handle import handle_pid
from .vocabulary.launch import LAUNCH_ID_ENV, new_launch_id
from .checkpoint import (
    Bind,
    CheckpointChoice,
    Every,
    MostProgress,
    NoSave,
    OnHeadLineage,
    SavePolicy,
)
from .logs import (
    Copied,
    CopyLogs,
    CopyRefused,
    JsonDefault,
    MemoryLogs,
    PostgresLogs,
    Row,
    RunLive,
    SqliteLogs,
    copy_run,
    drop_run,
    from_env,
    log_digest,
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
    launch_claim,
    relaunch_if_needed,
    report_death,
    report_if_dead,
)
from .memoizer import (
    LiveHandle,
    NoProgressError,
    Producer,
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
from .vocabulary.coordinate import Commits, NodeRef, Target
from .vocabulary.payloads import (
    Bound,
    Commit,
    Died,
    Heartbeat,
    Launched,
    Nak,
    NakReason,
    Node,
    Outcome,
    Reap,
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


def current_channel(json_default: JsonDefault | None = None) -> Channel:
    """Worker-side: open (or birth) the channel for the run this process was
    launched into.

    A ``LocalLauncher`` over ``SqliteLogs`` or ``PostgresLogs`` sets
    ``RUNSTATE_RUN_ID`` and the place's ``child_env()``
    (``RUNSTATE_CHANNEL_BACKEND``, ``RUNSTATE_CHANNEL_ROOT``) in the worker's
    environment; ``current_channel`` reads them back into the place
    (``logs.from_env``) and calls its ``create`` (open-or-create, so a direct run
    that sets the three itself still births its own log). All three are required:
    there is no default place. ``json_default`` is the place's (``logs.py``): an
    exempt default, since it encodes, and changes no record a plain value would
    write.

    A launcher also sets ``RUNSTATE_LAUNCH_ID`` (the launch's correlation id);
    the ``Worker`` re-emits it on its ``lifecycle.started`` so its claim names
    the launch it answers: read by ``vocabulary/launch.py``, not here, since it
    identifies the claim, not the channel.
    """
    run_id = os.environ["RUNSTATE_RUN_ID"]
    return from_env(os.environ, json_default=json_default).create(run_id)


__all__ = [
    # the substrate, and the places a channel is opened from
    "Logs",
    "ProcessLogs",
    "MemoryLogs",
    "SqliteLogs",
    "PostgresLogs",
    "current_channel",
    "LOG_FORMAT",
    "Channel",
    "Reader",
    "Body",
    "Envelope",
    # the worker and the checkpoint recipe
    "Worker",
    "SavePolicy",
    "Bind",
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
    "new_launch_id",
    "LAUNCH_ID_ENV",
    "launch_claim",
    # the observer plane and orchestration
    "Watcher",
    "await_consumed",
    "RunStatus",
    "Running",
    "RunResult",
    "Outcome",
    "Tier",
    "DeathReport",
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
    # the memoizer
    "history",
    "ensure",
    "Reached",
    "Unreachable",
    "Producer",
    "LiveHandle",
    "launch_producer",
    "foreign_episode",
    # run copy: one run's log between backends, at its format
    "copy_run",
    "drop_run",
    "Copied",
    "log_digest",
    "Row",
    "CopyLogs",
    # the errors public calls raise
    "RunNotFound",
    "LogFormatError",
    "LogFormatMismatch",
    "LogFormatMissing",
    "MalformedRecordError",
    "MalformedSchedule",
    "ClaimAliveError",
    "ReportStarvedError",
    "WorkerStarvedError",
    "RunFailedError",
    "NoProgressError",
    "RecordlessExitError",
    "RunLive",
    "CopyRefused",
    # the convention vocabulary and its typed bodies (serialize via
    # dataclasses.asdict, parse via Cls(**body)), with their fields' enums
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
    "NakReason",
    "Withdraw",
    "Launched",
    "Terminated",
    "Reap",
    "Died",
    "Node",
]
