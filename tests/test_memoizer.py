import pytest
import runstate
from runstate.memoizer import history


def test_history_replays_schedule_over_logged_points(open_channel):
    ch = open_channel()
    for step in range(10):
        ch.send({"value": float(step), "step": step, "t": float(step)},
                topic="value", name="loss")
    got = history(open_channel(), "loss", {"every": {"step": 3}})
    assert [b["step"] for b in got] == [0, 3, 6, 9]


def test_history_returns_empty_for_empty_series(open_channel):
    # no value points for "loss" yet (run not started / wrong name) -> empty, no error
    assert history(open_channel(), "loss", {"every": {"step": 1}}) == []


def test_history_filters_by_name_and_respects_until(open_channel):
    ch = open_channel()
    for step in range(6):
        ch.send({"value": float(step), "step": step, "t": 0.0}, topic="value", name="loss")
        ch.send({"value": -1.0, "step": step, "t": 0.0}, topic="value", name="acc")
    got = history(open_channel(), "loss", {"every": {"step": 1}, "until": {"step": 4}})
    assert [b["step"] for b in got] == [0, 1, 2, 3]            # name=loss only; until step 4


def test_history_collapses_benign_re_emission_but_raises_on_divergence(open_channel):
    ch = open_channel()
    ch.send({"value": 1.0, "step": 0, "t": 0.0}, topic="value", name="loss")
    ch.send({"value": 2.0, "step": 1, "t": 0.0}, topic="value", name="loss")
    ch.send({"value": 1.0, "step": 0, "t": 9.0}, topic="value", name="loss")   # identical re-emit -> OK
    assert [b["step"] for b in history(open_channel(), "loss", {"every": {"step": 1}})] == [0, 1]
    ch.send({"value": 99.0, "step": 1, "t": 9.0}, topic="value", name="loss")  # DIVERGENT
    with pytest.raises(ValueError, match="divergent"):
        history(open_channel(), "loss", {"every": {"step": 1}})


def test_history_time_schedule_is_run_relative_to_the_run_epoch(open_channel):
    ch = open_channel()
    ch.send({"handle": "local://h/1", "hostname": None, "attached_at": 1000.0},
            topic="lifecycle.started")                          # run epoch = 1000.0
    for step in range(6):
        ch.send({"value": float(step), "step": step, "t": 1000.0 + step},
                topic="value", name="loss")                     # absolute t
    # "every 2 seconds" run-relative: t-epoch in {0,2,4} -> steps 0,2,4
    got = history(open_channel(), "loss", {"every": {"time_seconds": 2}})
    assert [b["step"] for b in got] == [0, 2, 4]
