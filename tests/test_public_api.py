"""The top-level public surface is the supported API; guard it against drift."""

from pathlib import Path

import runstate

_API_DOC = Path(__file__).resolve().parent.parent / "docs" / "api.md"

EXPECTED = {
    "attach_channel",
    "create_channel",
    "current_channel",
    "RunNotFound",
    "Channel",
    "Body",
    "Envelope",
    "Worker",
    "Launcher",
    "LaunchHandle",
    "ThreadLauncher",
    "LocalLauncher",
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
    "relaunch_if_needed",
    "ensure_served",
    "foreign_episode",
    "RunFailedError",
    "NoProgressError",
    "RecordlessExitError",
    "Topic",
    "Condition",
    "Value",
    "Started",
    "Heartbeat",
    "Stopped",
    "Nak",
    "Launched",
    "Terminated",
}


def test_all_public_names_resolve():
    for name in runstate.__all__:
        assert hasattr(runstate, name), f"runstate.{name} missing"


def test_public_surface_is_stable():
    assert set(runstate.__all__) == EXPECTED


def test_api_doc_covers_the_public_surface():
    """docs/api.md is the public-surface reference; a name added to __all__ but
    not documented there is a silent orphan. Assert every export appears
    (backticked, so a name that only shows up as a substring of another does not
    count)."""
    text = _API_DOC.read_text()
    undocumented = [name for name in runstate.__all__ if f"`{name}`" not in text]
    assert not undocumented, f"docs/api.md is missing: {undocumented}"


def test_vocab_enums_are_wire_strings():
    """Outcome/Topic are StrEnums whose members ARE their wire strings — the
    zero-migration invariant: an enum member compares equal to the bare string
    stored on every existing .db, so serialization is byte-identical."""
    assert runstate.Outcome.COMPLETED == "completed"
    assert runstate.Outcome.PRESUMED_DEAD == "presumed_dead"
    assert runstate.Outcome.failures() == frozenset(
        {"errored", "killed", "presumed_dead"}
    )
    assert runstate.Topic.VALUE == "value"
    assert runstate.Topic.CONTROL_STOP == "control.stop"
    assert (
        runstate.Stopped.TOPIC == "lifecycle.stopped"
    )  #  ClassVars now point at Topic
