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
        {"reason": "completed", "error": None, "final_step": 500},
        topic="lifecycle.stopped",
    )
    r = peek_terminal(open_channel())
    assert isinstance(r, RunResult)
    assert r.outcome == "completed"
    assert r.reason == "completed"
    assert r.final_step == 500


def test_errored(open_channel):
    open_channel().send(
        {"reason": "errored", "error": "boom", "final_step": None},
        topic="lifecycle.stopped",
    )
    r = peek_terminal(open_channel())
    assert r.outcome == "errored"
    assert r.reason == "errored"
    assert r.error == "boom"


def test_commanded_is_stopped(open_channel):
    # a clean stop that isn't self-completion: normalized outcome "stopped",
    # but the verbatim worker reason is preserved
    open_channel().send(
        {"reason": "commanded", "error": None, "final_step": 7},
        topic="lifecycle.stopped",
    )
    r = peek_terminal(open_channel())
    assert r.outcome == "stopped"
    assert r.reason == "commanded"
    assert r.final_step == 7


def test_killed_from_launcher_terminated(open_channel):
    # the worker died without a clean stop; the reaper recorded the manner
    open_channel().send(
        {"reason": "killed", "signal": 9, "exit_code": None}, topic="launcher.terminated"
    )
    r = peek_terminal(open_channel())
    assert r.outcome == "killed"
    assert r.reason == "killed"


def test_clean_stop_takes_precedence_over_terminated(open_channel):
    ch = open_channel()
    ch.send({"reason": "completed", "error": None, "final_step": 9}, topic="lifecycle.stopped")
    ch.send({"reason": "exited", "exit_code": 0, "signal": None}, topic="launcher.terminated")
    assert peek_terminal(open_channel()).outcome == "completed"
