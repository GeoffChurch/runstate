import pytest
import runstate
from runstate.memoizer import history, launch_producer
from runstate.launcher import relaunch_if_needed
from runstate.vocabulary.handle import local_handle


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


def test_relaunch_if_needed_launches_when_not_live():
    launcher = runstate.ThreadLauncher()           # memory backend, in-process
    ran = []
    h = relaunch_if_needed(launcher, "r", lambda channel, **_: ran.append(1), kwargs={})
    assert h is not None
    h.wait()
    assert ran == [1]


def test_launch_producer_extend_injects_target_and_runs():
    import time as _t
    launcher = runstate.ThreadLauncher()
    seen = {}

    def worker(channel, *, up_to):
        seen["up_to"] = up_to
        with runstate.Worker(channel, now=lambda: 0.0) as w:
            for step in w.steps(total=up_to):
                w.set("loss", float(step))

    variant = runstate.Variant("exp", worker, {"kwargs": {}})
    producer = launch_producer(launcher, variant)           # default target_key="up_to"
    assert producer.run_id == "exp"
    assert producer.channel is not None                     # .channel opens without error
    producer.extend(3)                                      # launches; injects up_to=3
    for _ in range(400):                                    # wait for the episode to finish
        if launcher.open_channel("exp").latest("lifecycle.stopped") is not None:
            break
        _t.sleep(0.005)
    assert seen["up_to"] == 3                                # target injected into worker kwargs


def test_relaunch_if_needed_noops_when_a_live_episode_exists():
    launcher = runstate.ThreadLauncher()
    ch = launcher.open_channel("r")
    # fake a live episode: a started by OUR pid (resolve() -> alive), no stopped
    ch.send({"handle": local_handle(), "hostname": None, "attached_at": 0.0},
            topic="lifecycle.started")
    h = relaunch_if_needed(launcher, "r", lambda channel, **_: None, kwargs={})
    assert h is None
    assert launcher.open_channel("r").read(topics=["launcher.launched"]) == []  # no spawn
