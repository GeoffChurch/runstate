"""The top-level public surface is the supported API; guard it against drift."""

import runstate

EXPECTED = {
    "open_channel", "attach", "Envelope",
    "Worker",
    "Launcher", "LaunchHandle", "ThreadLauncher", "LocalLauncher",
    "Watcher", "await_consumed", "RunStatus", "Running", "RunResult", "peek_terminal",
    "sweep", "Variant",
    "history", "ensure", "launch_producer", "relaunch_if_needed",
    "Value", "Started", "Heartbeat", "Stopped", "Nak", "Launched", "Terminated",
}


def test_all_public_names_resolve():
    for name in runstate.__all__:
        assert hasattr(runstate, name), f"runstate.{name} missing"


def test_public_surface_is_stable():
    assert set(runstate.__all__) - {"__version__"} == EXPECTED
