"""The staged public surface, ``runstate.__all__``, which Task 19 makes
``runstate.__all__`` (docs/plans/2026-10-28-layer2-live.md, Task 16): guard it
against drift. Ported from ``tests/test_public_api.py``."""

from pathlib import Path

import runstate as surface

_API_DOC = Path(__file__).resolve().parent.parent / "docs" / "api.md"

EXPECTED = {
    # substrate, unchanged
    "attach_channel",
    "create_channel",
    "current_channel",
    "LOG_FORMAT",
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
    # the convention vocabulary, its bodies and their fields' enums
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
}


def test_all_public_names_resolve():
    for name in surface.__all__:
        assert hasattr(surface, name), f"runstate.{name} missing"


def test_public_surface_is_stable():
    """The surface is exactly the plan's; ``progress`` and ``value_series`` are
    removed (§5), and the memoizer's names are on it, its seam's Protocols among
    them, while the concrete producer and handle stay private."""
    assert set(surface.__all__) == EXPECTED
    assert len(surface.__all__) == len(EXPECTED)  # no name listed twice
    for removed in ("progress", "value_series"):
        assert not hasattr(surface, removed)
    # the factories return the exported Protocols, not their private classes
    assert surface.launch_producer.__annotations__["return"] == "Producer"
    assert surface.foreign_episode.__annotations__["return"] == "LiveHandle"


def test_api_doc_covers_the_public_surface():
    """docs/api.md is the public-surface reference: every export appears there,
    backticked, so a name only a substring of another does not count."""
    text = _API_DOC.read_text()
    undocumented = [name for name in surface.__all__ if f"`{name}`" not in text]
    assert not undocumented, f"docs/api.md is missing: {undocumented}"


def test_vocab_enums_are_wire_strings():
    """``Outcome``, ``Tier`` and ``Topic`` are StrEnums whose members are their wire
    strings, so a member compares equal to the string a log holds."""
    assert surface.Outcome.COMPLETED == "completed"
    assert surface.Outcome.LOST == "lost"
    assert surface.Outcome.failures() == frozenset(
        {"errored", "killed", "lost", "presumed_dead"}
    )
    assert surface.Tier.SESSION_ENDED == "session_ended"
    assert surface.Topic.VALUE == "value"
    assert surface.Topic.SUPERVISOR_DIED == "supervisor.died"
    assert surface.Topic.CONTROL_WITHDRAW == "control.withdraw"
    assert surface.Stopped.TOPIC == "lifecycle.stopped"
    assert surface.Died.TOPIC == "supervisor.died"
