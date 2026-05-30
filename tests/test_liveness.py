"""Observer-side liveness assessment (docs/design-v0.2.md §8, §9).

peek_terminal(channel) reads the log and returns a terminal RunResult if the run
has finished — a clean lifecycle.stopped, or a reaped launcher.terminated — else
None (still running / unknown). A single indexed existence lookup, no scan.
"""

from runstate.liveness import RunResult, peek_terminal


def test_none_while_running(open_channel):
    ch = open_channel()
    ch.send({"step": 0, "consumed_seq": 0}, topic="lifecycle.heartbeat")
    assert peek_terminal(open_channel()) is None


def test_completed(open_channel):
    open_channel().send(
        {"reason": "completed", "final_step": 500}, topic="lifecycle.stopped"
    )
    r = peek_terminal(open_channel())
    assert isinstance(r, RunResult)
    assert r.outcome == "completed"
    assert r.success is True
    assert r.final_step == 500


def test_errored(open_channel):
    open_channel().send(
        {"reason": "errored", "error": "boom"}, topic="lifecycle.stopped"
    )
    r = peek_terminal(open_channel())
    assert r.outcome == "errored"
    assert r.success is False
    assert r.error == "boom"


def test_killed_from_launcher_terminated(open_channel):
    # the worker died without a clean stop; the reaper recorded the manner
    open_channel().send(
        {"exit_code": 137, "signal": 9, "reason": "killed"}, topic="launcher.terminated"
    )
    r = peek_terminal(open_channel())
    assert r.outcome == "killed"
    assert r.success is False


def test_clean_stop_takes_precedence_over_terminated(open_channel):
    ch = open_channel()
    ch.send({"reason": "completed", "final_step": 9}, topic="lifecycle.stopped")
    ch.send({"exit_code": 0, "reason": "exited"}, topic="launcher.terminated")
    assert peek_terminal(open_channel()).outcome == "completed"
