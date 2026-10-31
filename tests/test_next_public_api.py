"""The staged public surface, ``runstate._next.__all__``, which Task 19 makes
``runstate.__all__`` (docs/plans/2026-10-28-layer2-live.md, Task 16): guard it
against drift. Ported from ``tests/test_public_api.py``."""

from pathlib import Path

import runstate._next as surface

_API_DOC = Path(__file__).resolve().parent.parent / "docs" / "api.md"

EXPECTED = {
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
    # the convention vocabulary and its bodies
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
}


def test_all_public_names_resolve():
    for name in surface.__all__:
        assert hasattr(surface, name), f"runstate._next.{name} missing"


def test_public_surface_is_stable():
    """The surface is exactly the plan's; ``progress`` and ``value_series`` are
    removed (§5), and the memoizer's names are on it."""
    assert set(surface.__all__) == EXPECTED
    assert len(surface.__all__) == len(EXPECTED)  # no name listed twice
    for removed in ("progress", "value_series"):
        assert not hasattr(surface, removed)


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
