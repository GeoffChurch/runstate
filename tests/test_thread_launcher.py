"""ThreadLauncher: the in-process reference launcher (docs/design-v0.2.md §8-9).

Runs the worker target on a thread in the same process, sharing the run's
channel directly (memory backend). Records the process-level lifecycle on the
log: launcher.launched (spawn-intent + handle) at start, launcher.terminated
(manner of death) when the target returns or raises.
"""

import threading

from runstate.launcher import ThreadLauncher
from runstate.observables import peek_terminal


def test_launch_runs_target_and_brackets_with_launcher_lifecycle(tmp_path):
    launcher = ThreadLauncher(root=tmp_path)
    seen = []

    def target(channel):
        seen.append(channel)
        channel.send({"value": 1}, topic="value", name="loss")

    h = launcher.launch("run-1", target)
    h.wait()

    assert len(seen) == 1
    ch = launcher.open_channel("run-1")
    topics = [e.topic for e in ch.read()]
    # launched brackets the work, terminated closes it
    assert topics[0] == "launcher.launched"
    assert topics[-1] == "launcher.terminated"
    assert "value" in topics

    launched = ch.latest("launcher.launched")
    assert launched.body["handle"].startswith("local://")
    assert launched.body["status"] == "running"

    term = ch.latest("launcher.terminated")
    assert term.body["exit_code"] == 0
    assert term.body["reason"] == "exited"


def test_handle_fields_and_liveness(tmp_path):
    launcher = ThreadLauncher(root=tmp_path)
    gate = threading.Event()

    def target(channel):
        gate.wait()

    h = launcher.launch("run-2", target)
    assert h.run_id == "run-2"
    assert h.handle.startswith("local://")
    assert h.is_alive() is True  # blocked on the gate
    gate.set()
    h.wait()
    assert h.is_alive() is False


def test_errored_target_records_nonzero_exit(tmp_path):
    launcher = ThreadLauncher(root=tmp_path)

    def target(channel):
        raise RuntimeError("boom")

    h = launcher.launch("run-3", target)
    h.wait()

    assert isinstance(h.exception, RuntimeError)
    ch = launcher.open_channel("run-3")
    term = ch.latest("launcher.terminated")
    assert term.body["exit_code"] == 1
    assert term.body["reason"] == "exited"
    # the observer reads "errored" from the nonzero exit
    assert peek_terminal(ch).outcome == "errored"


def test_target_receives_args(tmp_path):
    launcher = ThreadLauncher(root=tmp_path)
    got = []

    def target(channel, a, b, *, c):
        got.append((a, b, c))

    h = launcher.launch("run-4", target, args=(1, 2), kwargs={"c": 3})
    h.wait()
    assert got == [(1, 2, 3)]
