"""LocalLauncher: the subprocess reference launcher (docs/design-v0.2.md §8-9).

Spawns the worker as a child process with RUNSTATE_* in its environment; the
child calls runstate.attach() to re-derive the same run's channel (sqlite, so
the log is shared cross-process). Realizes the full handle story the in-proc
ThreadLauncher could only stub: a real child pid, resolvable and killable.
Brackets the run with launcher.launched / launcher.terminated, the latter
carrying the real manner of death (clean exit vs signal).
"""

import sys

from runstate.launcher import LocalLauncher
from runstate.liveness import peek_terminal

# A worker that re-derives its channel from the environment and records a value.
WORKER = (
    "import runstate;"
    "ch = runstate.attach();"
    "ch.send({'value': 0.5}, topic='value', name='loss')"
)

# A worker that blocks until killed.
BLOCKER = "import time, runstate; runstate.attach(); time.sleep(30)"


def test_subprocess_attaches_to_same_run_and_is_reaped(tmp_path):
    with LocalLauncher(root=tmp_path) as launcher:
        h = launcher.launch("run-1", [sys.executable, "-c", WORKER])
        assert h.wait() == 0  # block until the child exits, and reap

    ch = launcher.open_channel("run-1")
    # the child attached and wrote on the SAME run's log (cross-process)
    assert ch.latest("value", "loss").body["value"] == 0.5

    launched = ch.latest("launcher.launched")
    assert launched.body["handle"].startswith("local://")
    assert launched.body["status"] == "running"

    term = ch.latest("launcher.terminated")
    assert term.body["exit_code"] == 0
    assert term.body["reason"] == "exited"
    assert peek_terminal(ch).outcome == "completed"


def test_handle_is_alive_then_terminate_records_killed(tmp_path):
    with LocalLauncher(root=tmp_path) as launcher:
        h = launcher.launch("run-2", [sys.executable, "-c", BLOCKER])
        assert h.is_alive() is True
        h.terminate()  # SIGTERM
        h.wait()
        assert h.is_alive() is False

    ch = launcher.open_channel("run-2")
    term = ch.latest("launcher.terminated")
    assert term.body["reason"] == "killed"
    assert term.body["signal"] == 15  # SIGTERM
    assert peek_terminal(ch).outcome == "killed"


def test_reap_is_idempotent(tmp_path):
    with LocalLauncher(root=tmp_path) as launcher:
        h = launcher.launch("run-3", [sys.executable, "-c", WORKER])
        h.wait()
        h.wait()  # second reap must not emit a second launcher.terminated

    ch = launcher.open_channel("run-3")
    assert len(ch.read(topics=["launcher.terminated"])) == 1
